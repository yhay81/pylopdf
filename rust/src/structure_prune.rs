//! Plan the structure-tree cleanup for pages that deletion drops.
//!
//! A tagged PDF's structure tree names the page of its marked content through
//! `/Pg` and its annotations through OBJR `/Obj`, and the `/ParentTree` and
//! `/IDTree` of `/StructTreeRoot` name the structure elements. After
//! `delete_pages` or `select` drops a page, those references keep the page and
//! its annotations reachable, so pruning never collects them. The plan removes
//! the structure content that lay only on dropped pages, the elements left
//! without kids, their `/ParentTree` and `/IDTree` entries, and a remaining
//! element's `/Pg` that names a dropped page. Every read, traversal, and
//! allocation happens before the edit; applying the plan only shrinks arrays,
//! removes keys, and replaces existing values.

use std::collections::{HashMap, HashSet};

use lopdf::{Dictionary, Document, Object, ObjectId};
use pyo3::prelude::*;

use crate::document::{PdfError, limit_err};

/// Structure elements, number-tree nodes, and name-tree nodes in one plan.
const MAX_STRUCTURE_NODES: usize = 1 << 20;
/// Kids, number-tree and name-tree entries, and page annotation entries.
const MAX_STRUCTURE_EDGES: usize = 1 << 22;
const MAX_STRUCTURE_DEPTH: usize = 256;
const MAX_LOOKUP_TREE_DEPTH: usize = 32;
const MAX_REFERENCE_DEPTH: usize = 32;
/// Direct dictionaries and arrays nested inside one indirect object.
const MAX_DIRECT_PATH: usize = 64;

fn allocation_error(error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!(
        "failed to allocate structure tree cleanup: {error}"
    ))
}

fn count_error(message: impl Into<String>) -> PyErr {
    limit_err("structure_tree_count", message)
}

fn push<T>(values: &mut Vec<T>, value: T) -> PyResult<()> {
    values.try_reserve(1).map_err(allocation_error)?;
    values.push(value);
    Ok(())
}

fn insert<T: std::hash::Hash + Eq>(values: &mut HashSet<T>, value: T) -> PyResult<bool> {
    values.try_reserve(1).map_err(allocation_error)?;
    Ok(values.insert(value))
}

#[derive(Clone, Copy)]
enum Step {
    Key(&'static [u8]),
    Index(usize),
}

/// A value inside an indirect object, reached through direct containers.
struct Location {
    id: ObjectId,
    path: Vec<Step>,
}

impl Location {
    fn object(id: ObjectId) -> Self {
        Self {
            id,
            path: Vec::new(),
        }
    }

    fn child(&self, step: Step) -> PyResult<Self> {
        if self.path.len() >= MAX_DIRECT_PATH {
            return Err(count_error(format!(
                "structure tree direct objects exceed the {MAX_DIRECT_PATH}-level safety limit"
            )));
        }
        let mut path = Vec::new();
        path.try_reserve_exact(self.path.len() + 1)
            .map_err(allocation_error)?;
        path.extend_from_slice(&self.path);
        path.push(step);
        Ok(Self { id: self.id, path })
    }

    fn duplicate(&self) -> PyResult<Self> {
        let mut path = Vec::new();
        path.try_reserve_exact(self.path.len())
            .map_err(allocation_error)?;
        path.extend_from_slice(&self.path);
        Ok(Self { id: self.id, path })
    }

    fn object_mut<'a>(&self, doc: &'a mut Document) -> Option<&'a mut Object> {
        let mut object = doc.objects.get_mut(&self.id)?;
        for step in &self.path {
            object = match step {
                Step::Key(key) => object.as_dict_mut().ok()?.get_mut(key).ok()?,
                Step::Index(index) => object.as_array_mut().ok()?.get_mut(*index)?,
            };
        }
        Some(object)
    }
}

/// Follow a reference chain. A null value, an indirect null, a reference to a
/// missing object, and a reference cycle read as absent, following
/// ISO 32000-2 §7.3.7 and §7.3.10.
fn resolve<'a>(
    doc: &'a Document,
    mut location: Option<Location>,
    mut object: &'a Object,
) -> PyResult<Option<(Option<Location>, &'a Object)>> {
    let mut visited = [(0, 0); MAX_REFERENCE_DEPTH];
    let mut count = 0;
    while let Object::Reference(id) = object {
        if visited[..count].contains(id) {
            return Ok(None);
        }
        if count == MAX_REFERENCE_DEPTH {
            return Err(count_error(format!(
                "structure tree references exceed the {MAX_REFERENCE_DEPTH}-level safety limit"
            )));
        }
        visited[count] = *id;
        count += 1;
        let Some(next) = doc.objects.get(id) else {
            return Ok(None);
        };
        location = Some(Location::object(*id));
        object = next;
    }
    Ok((!matches!(object, Object::Null)).then_some((location, object)))
}

fn page_of(dictionary: &Dictionary) -> Option<ObjectId> {
    dictionary.get(b"Pg").and_then(Object::as_reference).ok()
}

fn integer_of(dictionary: &Dictionary, key: &[u8]) -> Option<i64> {
    dictionary.get(key).and_then(Object::as_i64).ok()
}

fn is_page_tree_or_catalog(dictionary: &Dictionary) -> bool {
    matches!(
        dictionary.get_type().ok(),
        Some(b"Page" | b"Pages" | b"Catalog")
    )
}

/// The node and edge budgets that every traversal of one plan shares.
struct Budget {
    nodes: usize,
    edges: usize,
}

impl Budget {
    fn node(&mut self) -> PyResult<()> {
        self.nodes += 1;
        if self.nodes > MAX_STRUCTURE_NODES {
            return Err(count_error(format!(
                "structure tree exceeds the {MAX_STRUCTURE_NODES}-node safety limit"
            )));
        }
        Ok(())
    }

    fn edges(&mut self, count: usize) -> PyResult<()> {
        self.edges = self.edges.saturating_add(count);
        if self.edges > MAX_STRUCTURE_EDGES {
            return Err(count_error(format!(
                "structure tree exceeds the {MAX_STRUCTURE_EDGES}-edge safety limit"
            )));
        }
        Ok(())
    }
}

/// Where each annotation of the document's pages lies.
struct Annotations {
    /// Each annotation that a remaining page lists, and the first such page.
    retained: HashMap<ObjectId, ObjectId>,
    /// Annotations that only dropped pages list.
    dropped: HashSet<ObjectId>,
}

impl Annotations {
    fn read(
        doc: &Document,
        pages: &[ObjectId],
        retained_pages: &HashSet<ObjectId>,
        removed_links: &[ObjectId],
        budget: &mut Budget,
    ) -> PyResult<Self> {
        let mut retained = HashMap::new();
        let mut dropped = HashSet::new();
        for &page_id in pages {
            let Some(page) = doc
                .objects
                .get(&page_id)
                .and_then(|object| object.as_dict().ok())
            else {
                continue;
            };
            let Ok(annots) = page.get(b"Annots") else {
                continue;
            };
            let Some((_, Object::Array(items))) = resolve(doc, None, annots)? else {
                continue;
            };
            budget.edges(items.len())?;
            let kept = retained_pages.contains(&page_id);
            for item in items {
                let Object::Reference(id) = item else {
                    continue;
                };
                if kept {
                    retained.try_reserve(1).map_err(allocation_error)?;
                    retained.entry(*id).or_insert(page_id);
                } else {
                    insert(&mut dropped, *id)?;
                }
            }
        }
        dropped.retain(|id| !retained.contains_key(id));
        // Links that navigation cleanup removes from remaining pages go too.
        for id in removed_links {
            retained.remove(id);
            insert(&mut dropped, *id)?;
        }
        Ok(Self { retained, dropped })
    }

    /// Whether an annotation that no page lists names a dropped page in `/P`.
    fn unlisted_on_dropped(
        &self,
        doc: &Document,
        id: ObjectId,
        dropped_pages: &HashSet<ObjectId>,
    ) -> bool {
        !self.retained.contains_key(&id)
            && doc
                .objects
                .get(&id)
                .and_then(|object| object.as_dict().ok())
                .and_then(|annotation| annotation.get(b"P").and_then(Object::as_reference).ok())
                .is_some_and(|page| dropped_pages.contains(&page))
    }
}

/// One prepared edit; applying it never allocates.
enum Edit {
    /// Remove these ascending indices from the array at the location.
    Filter(Location, Vec<usize>),
    /// Remove a key from the dictionary at the location.
    RemoveKey(Location, &'static [u8]),
    /// Point the existing `/Pg` of the dictionary at the location elsewhere.
    SetPage(Location, ObjectId),
}

impl Edit {
    fn depth(&self) -> usize {
        match self {
            Self::Filter(location, _)
            | Self::RemoveKey(location, _)
            | Self::SetPage(location, _) => location.path.len(),
        }
    }
}

/// What a kid of a structure element amounts to.
enum Kid<'a> {
    Kept,
    Removed,
    /// A remaining OBJR whose own `/Pg` names a dropped page, and the
    /// remaining page that lists its annotation.
    Retarget(Location, ObjectId),
    Element(Location, &'a Dictionary),
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    Walking,
    Kept,
    Removed,
}

enum Kids<'a> {
    None,
    /// A `/K` that is no array.
    Single(Option<&'a Object>),
    Array {
        location: Location,
        items: &'a [Object],
        next: usize,
    },
}

/// The `/Ref` of a walked element, which names other structure elements.
enum Reference<'a> {
    Array {
        element: Location,
        array: Location,
        items: &'a [Object],
    },
    Single {
        element: Location,
        value: &'a Object,
    },
}

/// One structure element, or the structure tree root, being walked.
struct Frame<'a> {
    location: Location,
    page: Option<ObjectId>,
    root: bool,
    kids: Kids<'a>,
    /// The index of the kid whose element is being walked.
    current: usize,
    count: usize,
    kept: bool,
    removed: Vec<usize>,
}

impl Frame<'_> {
    fn record(&mut self, index: usize, removed: bool) -> PyResult<()> {
        self.count += 1;
        if removed {
            push(&mut self.removed, index)
        } else {
            self.kept = true;
            Ok(())
        }
    }
}

struct Walk<'a> {
    doc: &'a Document,
    dropped_pages: &'a HashSet<ObjectId>,
    annotations: &'a Annotations,
    budget: Budget,
    states: HashMap<ObjectId, State>,
    walked_arrays: HashSet<ObjectId>,
    removed_elements: HashSet<ObjectId>,
    references: Vec<Reference<'a>>,
    edits: Vec<Edit>,
}

impl<'a> Walk<'a> {
    fn dropped(&self, page: Option<ObjectId>) -> bool {
        page.is_some_and(|page| self.dropped_pages.contains(&page))
    }

    fn frame(
        &mut self,
        location: Location,
        dictionary: &'a Dictionary,
        root: bool,
    ) -> PyResult<Frame<'a>> {
        let mut kids = Kids::None;
        if let Ok(object) = dictionary.get(b"K") {
            match resolve(self.doc, None, object)? {
                None => {}
                Some((Some(array), Object::Array(_)))
                    if !insert(&mut self.walked_arrays, array.id)? =>
                {
                    // A malformed tree shares this array with an element
                    // already walked, which planned its edit.
                }
                Some((resolved, Object::Array(items))) => {
                    self.budget.edges(items.len())?;
                    kids = Kids::Array {
                        location: match resolved {
                            Some(array) => array,
                            None => location.child(Step::Key(b"K"))?,
                        },
                        items,
                        next: 0,
                    };
                }
                Some(_) => {
                    self.budget.edges(1)?;
                    kids = Kids::Single(Some(object));
                }
            }
        }
        if !root && let Ok(value) = dictionary.get(b"Ref") {
            let reference = match resolve(self.doc, None, value)? {
                Some((array, Object::Array(items))) => {
                    self.budget.edges(items.len())?;
                    let array = match array {
                        // A shared indirect array is planned once.
                        Some(array) => insert(&mut self.walked_arrays, array.id)?.then_some(array),
                        None => Some(location.child(Step::Key(b"Ref"))?),
                    };
                    match array {
                        Some(array) => Some(Reference::Array {
                            element: location.duplicate()?,
                            array,
                            items,
                        }),
                        None => None,
                    }
                }
                _ => Some(Reference::Single {
                    element: location.duplicate()?,
                    value,
                }),
            };
            if let Some(reference) = reference {
                push(&mut self.references, reference)?;
            }
        }
        Ok(Frame {
            location,
            page: if root { None } else { page_of(dictionary) },
            root,
            kids,
            current: 0,
            count: 0,
            kept: false,
            removed: Vec::new(),
        })
    }

    fn classify(
        &self,
        frame: &Frame<'a>,
        index: Option<usize>,
        object: &'a Object,
    ) -> PyResult<Kid<'a>> {
        let page = frame.page;
        let here = || match (&frame.kids, index) {
            (Kids::Array { location, .. }, Some(index)) => location.child(Step::Index(index)),
            _ => frame.location.child(Step::Key(b"K")),
        };
        let on_page = |page| {
            if self.dropped(page) {
                Kid::Removed
            } else {
                Kid::Kept
            }
        };
        if matches!(object, Object::Integer(_)) {
            return Ok(on_page(page));
        }
        let Some((location, Object::Dictionary(dictionary))) = resolve(self.doc, None, object)?
        else {
            return Ok(Kid::Kept);
        };
        if is_page_tree_or_catalog(dictionary) {
            return Ok(Kid::Kept);
        }
        match dictionary.get_type().ok() {
            Some(b"MCR") => Ok(on_page(page_of(dictionary).or(page))),
            Some(b"OBJR") => {
                let own = page_of(dictionary);
                if let Ok(target) = dictionary.get(b"Obj").and_then(Object::as_reference) {
                    if let Some(&listing) = self.annotations.retained.get(&target) {
                        if !self.dropped(own) {
                            return Ok(Kid::Kept);
                        }
                        let location = match location {
                            Some(location) => location,
                            None => here()?,
                        };
                        return Ok(Kid::Retarget(location, listing));
                    }
                    if self.annotations.dropped.contains(&target)
                        || self.annotations.unlisted_on_dropped(
                            self.doc,
                            target,
                            self.dropped_pages,
                        )
                    {
                        return Ok(Kid::Removed);
                    }
                }
                Ok(on_page(own.or(page)))
            }
            _ if dictionary.has(b"S") => {
                let location = match location {
                    Some(location) => location,
                    None => here()?,
                };
                Ok(Kid::Element(location, dictionary))
            }
            _ => Ok(Kid::Kept),
        }
    }

    /// Walk an element that only a lookup tree names, outside the tree.
    fn detached(&mut self, value: &'a Object) -> PyResult<()> {
        let Object::Reference(id) = value else {
            return Ok(());
        };
        if self.states.contains_key(id) {
            return Ok(());
        }
        let Some(Object::Dictionary(dictionary)) = self.doc.objects.get(id) else {
            return Ok(());
        };
        if !dictionary.has(b"S") || is_page_tree_or_catalog(dictionary) {
            return Ok(());
        }
        self.budget.node()?;
        self.states.try_reserve(1).map_err(allocation_error)?;
        self.states.insert(*id, State::Walking);
        self.walk(Location::object(*id), dictionary, false)
    }

    fn walk(&mut self, start: Location, dictionary: &'a Dictionary, root: bool) -> PyResult<()> {
        let mut stack = Vec::new();
        let frame = self.frame(start, dictionary, root)?;
        push(&mut stack, frame)?;
        while let Some(frame) = stack.last_mut() {
            let next = match &mut frame.kids {
                Kids::None => None,
                Kids::Single(object) => object.take().map(|object| (None, object)),
                Kids::Array { items, next, .. } => {
                    let item = items.get(*next).map(|object| (Some(*next), object));
                    *next += 1;
                    item
                }
            };
            let Some((index, object)) = next else {
                let Some(frame) = stack.pop() else {
                    break;
                };
                let removed = self.finish(frame)?;
                if let Some(parent) = stack.last_mut() {
                    let current = parent.current;
                    parent.record(current, removed)?;
                }
                continue;
            };
            let position = index.unwrap_or(0);
            let removed = match self.classify(frame, index, object)? {
                Kid::Kept => false,
                Kid::Removed => true,
                Kid::Retarget(location, page) => {
                    push(&mut self.edits, Edit::SetPage(location, page))?;
                    false
                }
                Kid::Element(location, dictionary) => {
                    let id = location.path.is_empty().then_some(location.id);
                    match id.and_then(|id| self.states.get(&id).copied()) {
                        Some(State::Removed) => true,
                        Some(State::Walking | State::Kept) => false,
                        None => {
                            frame.current = position;
                            if stack.len() >= MAX_STRUCTURE_DEPTH {
                                return Err(count_error(format!(
                                    "structure tree exceeds the {MAX_STRUCTURE_DEPTH}-level safety limit"
                                )));
                            }
                            self.budget.node()?;
                            if let Some(id) = id {
                                self.states.try_reserve(1).map_err(allocation_error)?;
                                self.states.insert(id, State::Walking);
                            }
                            let child = self.frame(location, dictionary, false)?;
                            push(&mut stack, child)?;
                            continue;
                        }
                    }
                }
            };
            if let Some(frame) = stack.last_mut() {
                frame.record(position, removed)?;
            }
        }
        Ok(())
    }

    /// Record a walked element's edits; returns whether it goes away.
    fn finish(&mut self, frame: Frame<'a>) -> PyResult<bool> {
        let removed = !frame.root && frame.count > 0 && !frame.kept;
        let id = frame.location.path.is_empty().then_some(frame.location.id);
        if let Some(id) = id
            && let Some(state) = self.states.get_mut(&id)
        {
            *state = if removed { State::Removed } else { State::Kept };
        }
        if removed {
            if let Some(id) = id {
                insert(&mut self.removed_elements, id)?;
            }
            return Ok(true);
        }
        let dropped_page = self.dropped(frame.page);
        if !frame.removed.is_empty() {
            match frame.kids {
                Kids::Array { location, .. } => {
                    push(&mut self.edits, Edit::Filter(location, frame.removed))?;
                }
                // Only the root keeps a lone `/K` that goes away.
                _ => push(
                    &mut self.edits,
                    Edit::RemoveKey(frame.location.duplicate()?, b"K"),
                )?,
            }
        }
        if dropped_page {
            push(&mut self.edits, Edit::RemoveKey(frame.location, b"Pg"))?;
        }
        Ok(false)
    }
}

/// Walk a number tree (`Nums`) or name tree (`Names`) and collect each
/// leaf array of key/value pairs with its location.
fn lookup_tree_leaves<'a>(
    doc: &'a Document,
    root: Location,
    object: &'a Object,
    key: &'static [u8],
    budget: &mut Budget,
) -> PyResult<Vec<(Location, &'a [Object])>> {
    let mut leaves = Vec::new();
    let mut pending = Vec::new();
    push(&mut pending, (Some(root), object, 1usize))?;
    let mut seen = HashSet::new();
    while let Some((location, object, depth)) = pending.pop() {
        let Some((Some(location), Object::Dictionary(node))) = resolve(doc, location, object)?
        else {
            continue;
        };
        if location.path.is_empty() && !insert(&mut seen, location.id)? {
            continue;
        }
        if is_page_tree_or_catalog(node) {
            continue;
        }
        budget.node()?;
        if let Ok(pairs) = node.get(key)
            && let Some((resolved, Object::Array(items))) = resolve(doc, None, pairs)?
        {
            let array = match resolved {
                Some(array) => insert(&mut seen, array.id)?.then_some(array),
                None => Some(location.child(Step::Key(key))?),
            };
            if let Some(array) = array {
                budget.edges(items.len())?;
                push(&mut leaves, (array, items.as_slice()))?;
            }
        }
        if let Ok(kids) = node.get(b"Kids")
            && let Some((_, Object::Array(items))) = resolve(doc, None, kids)?
        {
            if depth >= MAX_LOOKUP_TREE_DEPTH {
                return Err(count_error(format!(
                    "structure tree lookup tree exceeds the {MAX_LOOKUP_TREE_DEPTH}-level safety limit"
                )));
            }
            budget.edges(items.len())?;
            for item in items {
                if matches!(item, Object::Reference(_)) {
                    push(&mut pending, (None, item, depth + 1))?;
                }
            }
        }
    }
    Ok(leaves)
}

/// Plan the removal of the pairs of the leaves that `remove` selects.
fn plan_leaves<'a>(
    leaves: Vec<(Location, &'a [Object])>,
    edits: &mut Vec<Edit>,
    mut remove: impl FnMut(&'a Object, &'a Object) -> PyResult<bool>,
) -> PyResult<()> {
    for (array, items) in leaves {
        let mut removed = Vec::new();
        for (index, [key, value]) in items.as_chunks::<2>().0.iter().enumerate() {
            if remove(key, value)? {
                push(&mut removed, index * 2)?;
                push(&mut removed, index * 2 + 1)?;
            }
        }
        if !removed.is_empty() {
            push(edits, Edit::Filter(array, removed))?;
        }
    }
    Ok(())
}

/// Drop removed elements from the `/Ref` of the elements that remain.
fn plan_references(
    references: Vec<Reference<'_>>,
    removed_elements: &HashSet<ObjectId>,
    edits: &mut Vec<Edit>,
) -> PyResult<()> {
    let names_removed =
        |object: &Object| matches!(object, Object::Reference(id) if removed_elements.contains(id));
    for reference in references {
        match reference {
            Reference::Array {
                element,
                array,
                items,
            } => {
                if element.path.is_empty() && removed_elements.contains(&element.id) {
                    continue;
                }
                let mut removed = Vec::new();
                for (index, item) in items.iter().enumerate() {
                    if names_removed(item) {
                        push(&mut removed, index)?;
                    }
                }
                if removed.is_empty() {
                    continue;
                }
                if removed.len() == items.len() {
                    push(edits, Edit::RemoveKey(element, b"Ref"))?;
                } else {
                    push(edits, Edit::Filter(array, removed))?;
                }
            }
            Reference::Single { element, value } => {
                if !(element.path.is_empty() && removed_elements.contains(&element.id))
                    && names_removed(value)
                {
                    push(edits, Edit::RemoveKey(element, b"Ref"))?;
                }
            }
        }
    }
    Ok(())
}

/// Whether a `/ParentTree` value names only removed elements.
fn names_only_removed(
    doc: &Document,
    value: &Object,
    removed: &HashSet<ObjectId>,
) -> PyResult<bool> {
    if let Object::Reference(id) = value
        && removed.contains(id)
    {
        return Ok(true);
    }
    let Some((_, Object::Array(items))) = resolve(doc, None, value)? else {
        return Ok(false);
    };
    let mut any = false;
    for item in items {
        match item {
            Object::Reference(id) if removed.contains(id) => any = true,
            Object::Null => {}
            _ => return Ok(false),
        }
    }
    Ok(any)
}

/// The prepared structure-tree cleanup of `delete_pages` and `select`.
pub(crate) struct StructurePagePrune {
    edits: Vec<Edit>,
}

impl StructurePagePrune {
    /// Plan the cleanup; `pages` lists every page before the edit,
    /// `retained` the pages that remain, and `removed_links` the link
    /// annotations that navigation cleanup removes from them. Returns `None`
    /// when nothing changes.
    pub(crate) fn prepare(
        doc: &Document,
        pages: &[ObjectId],
        retained: &HashSet<ObjectId>,
        removed_links: &[ObjectId],
    ) -> PyResult<Option<Self>> {
        let Some(catalog) = doc
            .trailer
            .get(b"Root")
            .and_then(Object::as_reference)
            .ok()
            .and_then(|id| doc.objects.get(&id))
            .and_then(|object| object.as_dict().ok())
        else {
            return Ok(None);
        };
        let Ok(root) = catalog.get(b"StructTreeRoot") else {
            return Ok(None);
        };
        let Some((Some(root_location), Object::Dictionary(root))) = resolve(doc, None, root)?
        else {
            return Ok(None);
        };
        if is_page_tree_or_catalog(root) {
            return Ok(None);
        }
        let mut dropped_pages = HashSet::new();
        for &page in pages {
            if !retained.contains(&page) {
                insert(&mut dropped_pages, page)?;
            }
        }
        if dropped_pages.is_empty() {
            return Ok(None);
        }
        let mut budget = Budget { nodes: 0, edges: 0 };
        let annotations = Annotations::read(doc, pages, retained, removed_links, &mut budget)?;

        let mut walk = Walk {
            doc,
            dropped_pages: &dropped_pages,
            annotations: &annotations,
            budget,
            states: HashMap::new(),
            walked_arrays: HashSet::new(),
            removed_elements: HashSet::new(),
            references: Vec::new(),
            edits: Vec::new(),
        };
        walk.walk(root_location.duplicate()?, root, true)?;
        let parent_leaves = match root.get(b"ParentTree") {
            Ok(tree) => lookup_tree_leaves(
                doc,
                root_location.child(Step::Key(b"ParentTree"))?,
                tree,
                b"Nums",
                &mut walk.budget,
            )?,
            Err(_) => Vec::new(),
        };
        let id_leaves = match root.get(b"IDTree") {
            Ok(tree) => lookup_tree_leaves(
                doc,
                root_location.child(Step::Key(b"IDTree"))?,
                tree,
                b"Names",
                &mut walk.budget,
            )?,
            Err(_) => Vec::new(),
        };
        // A malformed tree can name elements that `/K` no longer reaches;
        // they keep their content's pages reachable all the same.
        for (_, items) in parent_leaves.iter().chain(&id_leaves) {
            for [_, value] in items.as_chunks::<2>().0 {
                walk.detached(value)?;
                if let Some((_, Object::Array(elements))) = resolve(doc, None, value)? {
                    walk.budget.edges(elements.len())?;
                    for element in elements {
                        walk.detached(element)?;
                    }
                }
            }
        }
        let Walk {
            removed_elements,
            references,
            mut edits,
            ..
        } = walk;
        plan_references(references, &removed_elements, &mut edits)?;

        // `/ParentTree` keys of dropped pages and of annotations that only
        // dropped pages hold, unless a remaining page or annotation uses them.
        let mut dropped_keys = HashSet::new();
        let mut retained_keys = HashSet::new();
        for &page in pages {
            let Some(key) = doc
                .objects
                .get(&page)
                .and_then(|object| object.as_dict().ok())
                .and_then(|page| integer_of(page, b"StructParents"))
            else {
                continue;
            };
            if retained.contains(&page) {
                insert(&mut retained_keys, key)?;
            } else {
                insert(&mut dropped_keys, key)?;
            }
        }
        let annotation_key = |id: &ObjectId| {
            doc.objects
                .get(id)
                .and_then(|object| object.as_dict().ok())
                .and_then(|annotation| integer_of(annotation, b"StructParent"))
        };
        for id in annotations.retained.keys() {
            if let Some(key) = annotation_key(id) {
                insert(&mut retained_keys, key)?;
            }
        }
        for id in &annotations.dropped {
            if let Some(key) = annotation_key(id) {
                insert(&mut dropped_keys, key)?;
            }
        }
        dropped_keys.retain(|key| !retained_keys.contains(key));

        plan_leaves(parent_leaves, &mut edits, |key, value| {
            if key.as_i64().is_ok_and(|key| dropped_keys.contains(&key)) {
                return Ok(true);
            }
            names_only_removed(doc, value, &removed_elements)
        })?;
        plan_leaves(id_leaves, &mut edits, |_, value| {
            Ok(matches!(value, Object::Reference(id) if removed_elements.contains(id)))
        })?;
        if edits.is_empty() {
            return Ok(None);
        }
        // Edits inside an array go first, so removing entries from the array
        // cannot shift the index that their location names.
        edits.sort_unstable_by_key(|edit| std::cmp::Reverse(edit.depth()));
        Ok(Some(Self { edits }))
    }

    /// Apply the planned edits; each shrinks an array, removes a key, or
    /// replaces an existing `/Pg`, so nothing allocates.
    pub(crate) fn apply(self, doc: &mut Document) {
        for edit in self.edits {
            match edit {
                Edit::Filter(location, removed) => {
                    if let Some(Object::Array(items)) = location.object_mut(doc) {
                        let mut index = 0;
                        let mut next = removed.iter().peekable();
                        items.retain(|_| {
                            let drop = next.next_if_eq(&&index).is_some();
                            index += 1;
                            !drop
                        });
                    }
                }
                Edit::RemoveKey(location, key) => {
                    if let Some(Object::Dictionary(dictionary)) = location.object_mut(doc) {
                        dictionary.remove(key);
                    }
                }
                Edit::SetPage(location, page) => {
                    if let Some(Object::Dictionary(dictionary)) = location.object_mut(doc)
                        && let Ok(entry) = dictionary.get_mut(b"Pg")
                    {
                        *entry = Object::Reference(page);
                    }
                }
            }
        }
    }
}
