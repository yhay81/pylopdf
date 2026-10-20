//! Preflight navigation cleanup before removing pages from the editable tree.

use std::collections::{HashMap, HashSet};

use lopdf::{Dictionary, Document, Object, ObjectId};
use pyo3::prelude::*;

use crate::document::{
    DestinationNameBudget, MAX_NAMED_DEST_ENTRIES, MAX_NAMED_DEST_NAME_BYTES,
    MAX_NAMED_DEST_TREE_DEPTH, MAX_NAMED_DEST_TREE_EDGES, MAX_NAMED_DEST_TREE_NODES,
    MAX_TOC_TREE_EDGES, MAX_TOC_TREE_NODES, PdfError, add_named_destination_text_budget, limit_err,
    named_destination_count_err, resolve_local_destination_array, toc_entry_count_err,
};

const MAX_PAGE_ANNOTATIONS: usize = 4_096;
const MAX_DEST_DEPTH: usize = 32;
const MAX_OUTLINE_DEPTH: usize = 64;
/// Copied destination values may average 16 direct objects per admitted entry.
const MAX_COPIED_DESTINATION_OBJECTS: usize = MAX_NAMED_DEST_ENTRIES * 16;

#[derive(Clone, Copy)]
enum Step {
    Key(&'static [u8]),
    Index(usize),
}

/// A stable location in a direct object, reset whenever an indirect object is met.
struct Target {
    id: ObjectId,
    path: Vec<Step>,
}

impl Target {
    fn object<'a>(&self, doc: &'a Document) -> Option<&'a Object> {
        let mut object = doc.objects.get(&self.id)?;
        for step in &self.path {
            object = match step {
                Step::Key(key) => object.as_dict().ok()?.get(key).ok()?,
                Step::Index(index) => object.as_array().ok()?.get(*index)?,
            };
        }
        Some(object)
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

    fn child(&self, step: Step) -> PyResult<Self> {
        if self.path.len() >= MAX_OUTLINE_DEPTH {
            return Err(PdfError::new_err(
                "page cleanup direct objects exceed the 64-level safety limit",
            ));
        }
        let mut path = Vec::new();
        path.try_reserve_exact(self.path.len() + 1)
            .map_err(allocation_error)?;
        path.extend_from_slice(&self.path);
        path.push(step);
        Ok(Self { id: self.id, path })
    }

    /// Follow references to the located value. A null value, an indirect null,
    /// and a reference to a missing object all read as an absent entry, as in
    /// ISO 32000-2 §7.3.7 and §7.3.10.
    fn resolve(mut self, doc: &Document) -> PyResult<Option<Self>> {
        let mut visited = [(0, 0); MAX_DEST_DEPTH];
        let mut count = 0;
        while let Some(Object::Reference(id)) = self.object(doc) {
            if visited[..count].contains(id) {
                return Ok(None);
            }
            if count == MAX_DEST_DEPTH {
                return Err(PdfError::new_err(
                    "page cleanup references exceed the 32-level safety limit",
                ));
            }
            visited[count] = *id;
            count += 1;
            self.id = *id;
            self.path.clear();
        }
        Ok(self
            .object(doc)
            .is_some_and(|object| !matches!(object, Object::Null))
            .then_some(self))
    }
}

fn validate_navigation_dictionary(dictionary: &Dictionary) -> PyResult<()> {
    if matches!(
        dictionary.get_type().ok(),
        Some(b"Page" | b"Pages" | b"Catalog")
    ) {
        return Err(PdfError::new_err(
            "navigation tree cannot alias a page or catalog dictionary",
        ));
    }
    Ok(())
}

fn allocation_error(error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!("failed to allocate page cleanup plan: {error}"))
}

fn push<T>(values: &mut Vec<T>, value: T) -> PyResult<()> {
    values.try_reserve(1).map_err(allocation_error)?;
    values.push(value);
    Ok(())
}

fn removed_destination<'a>(
    doc: &'a Document,
    destination: &'a Object,
    removed: &HashSet<ObjectId>,
    named: &mut Option<HashMap<&'a [u8], &'a Object>>,
    source_bytes: &mut usize,
    budget: DestinationNameBudget,
) -> PyResult<bool> {
    Ok(
        resolve_local_destination_array(doc, destination, named, source_bytes, budget)?
            .and_then(|array| array.first())
            .and_then(|first| first.as_reference().ok())
            .is_some_and(|id| removed.contains(&id)),
    )
}

fn navigation_to_removed<'a>(
    doc: &'a Document,
    dictionary: &'a Dictionary,
    removed: &HashSet<ObjectId>,
    named: &mut Option<HashMap<&'a [u8], &'a Object>>,
    source_bytes: &mut usize,
    budget: DestinationNameBudget,
) -> PyResult<(bool, bool)> {
    let direct = dictionary
        .get(b"Dest")
        .ok()
        .map(|destination| {
            removed_destination(doc, destination, removed, named, source_bytes, budget)
        })
        .transpose()?
        .unwrap_or(false);
    let action = dictionary.get(b"A").ok().and_then(|object| match object {
        Object::Reference(id) => doc.get_dictionary(*id).ok(),
        _ => object.as_dict().ok(),
    });
    let goto = match action {
        Some(action) if action.get(b"S").and_then(Object::as_name).ok() == Some(b"GoTo") => action
            .get(b"D")
            .ok()
            .map(|destination| {
                removed_destination(doc, destination, removed, named, source_bytes, budget)
            })
            .transpose()?
            .unwrap_or(false),
        _ => false,
    };
    Ok((direct, goto))
}

struct DictionaryPatch {
    target: Target,
    direct: bool,
    action: bool,
}

/// Every fallible read, traversal, and allocation precedes document mutation.
#[derive(Default)]
pub(crate) struct PageCleanupPlan {
    page_links: HashMap<ObjectId, Vec<usize>>,
    indirect_links: HashMap<ObjectId, Vec<usize>>,
    /// Indirect link annotations that the filtered arrays drop.
    removed_links: Vec<ObjectId>,
    dictionaries: Vec<DictionaryPatch>,
    replacements: Vec<(Target, Object)>,
    remove_outlines: bool,
    remove_open_action: bool,
}

impl PageCleanupPlan {
    pub(crate) fn prepare(
        doc: &Document,
        retained: &[ObjectId],
        removed: &HashSet<ObjectId>,
    ) -> PyResult<Self> {
        let mut plan = Self::default();
        if removed.is_empty() {
            return Ok(plan);
        }
        let catalog_id = doc
            .trailer
            .get(b"Root")
            .and_then(Object::as_reference)
            .map_err(|error| PdfError::new_err(error.to_string()))?;
        let catalog = Target {
            id: catalog_id,
            path: Vec::new(),
        };
        let mut named = None;
        if let Some(open_action) = doc
            .catalog()
            .ok()
            .and_then(|catalog| catalog.get(b"OpenAction").ok())
        {
            let resolved = match open_action {
                Object::Reference(id) => doc.get_object(*id).unwrap_or(open_action),
                _ => open_action,
            };
            let local = resolved.as_dict().ok().is_none_or(|action| {
                action.get(b"S").and_then(Object::as_name).ok() == Some(b"GoTo")
            });
            if local {
                plan.remove_open_action = removed_destination(
                    doc,
                    open_action,
                    removed,
                    &mut named,
                    &mut 0,
                    DestinationNameBudget::Annotation,
                )?;
            }
        }
        let mut seen_pages = HashSet::new();
        seen_pages
            .try_reserve(retained.len())
            .map_err(allocation_error)?;
        for &page_id in retained {
            if !seen_pages.insert(page_id) {
                continue;
            }
            let page = doc
                .get_dictionary(page_id)
                .map_err(|error| PdfError::new_err(error.to_string()))?;
            let Some(annots) = page.get(b"Annots").ok() else {
                continue;
            };
            let annots = match annots {
                Object::Reference(id) => doc.get_object(*id).ok(),
                _ => Some(annots),
            };
            let Some(items) = annots.and_then(|object| object.as_array().ok()) else {
                continue;
            };
            if items.len() > MAX_PAGE_ANNOTATIONS {
                return Err(PdfError::new_err(
                    "page annotations exceed the 4096-entry safety limit",
                ));
            }
            let mut excluded = Vec::new();
            let mut bytes = 0usize;
            for (index, item) in items.iter().enumerate() {
                let annotation = match item {
                    Object::Reference(id) => doc.get_dictionary(*id).ok(),
                    _ => item.as_dict().ok(),
                };
                let Some(annotation) = annotation else {
                    continue;
                };
                validate_navigation_dictionary(annotation)?;
                if annotation.get(b"Subtype").and_then(Object::as_name).ok() != Some(b"Link") {
                    continue;
                }
                let (direct, action) = navigation_to_removed(
                    doc,
                    annotation,
                    removed,
                    &mut named,
                    &mut bytes,
                    DestinationNameBudget::Annotation,
                )?;
                if direct || action {
                    push(&mut excluded, index)?;
                    if let Object::Reference(id) = item {
                        push(&mut plan.removed_links, *id)?;
                    }
                }
            }
            if !excluded.is_empty() {
                if let Ok(id) = page.get(b"Annots").and_then(Object::as_reference) {
                    if !plan.indirect_links.contains_key(&id) {
                        plan.indirect_links
                            .try_reserve(1)
                            .map_err(allocation_error)?;
                        plan.indirect_links.insert(id, excluded);
                    }
                } else {
                    plan.page_links.try_reserve(1).map_err(allocation_error)?;
                    plan.page_links.insert(page_id, excluded);
                }
            }
        }
        plan.prepare_outlines(doc, &catalog, removed, &mut named)?;
        plan.remove_outlines = retained.is_empty();
        plan.prepare_names(doc, &catalog, removed, &mut named)?;
        Ok(plan)
    }

    fn prepare_outlines<'a>(
        &mut self,
        doc: &'a Document,
        catalog: &Target,
        removed: &HashSet<ObjectId>,
        named: &mut Option<HashMap<&'a [u8], &'a Object>>,
    ) -> PyResult<()> {
        let Some(root) = catalog.child(Step::Key(b"Outlines"))?.resolve(doc)? else {
            return Ok(());
        };
        let Some(dictionary) = root.object(doc).and_then(|object| object.as_dict().ok()) else {
            return Err(PdfError::new_err("catalog Outlines is not a dictionary"));
        };
        validate_navigation_dictionary(dictionary)?;
        let start = if dictionary.has(b"First") {
            root.child(Step::Key(b"First"))?
        } else {
            root
        };
        let mut pending = Vec::new();
        push(&mut pending, (start, 1usize))?;
        let mut seen = HashSet::new();
        let mut nodes = 0;
        let mut edges = 0;
        let mut bytes = 0usize;
        while let Some((target, depth)) = pending.pop() {
            let Some(target) = target.resolve(doc)? else {
                continue;
            };
            if target.path.is_empty() {
                if seen.contains(&target.id) {
                    continue;
                }
                seen.try_reserve(1).map_err(allocation_error)?;
                seen.insert(target.id);
            }
            let Some(dictionary) = target.object(doc).and_then(|object| object.as_dict().ok())
            else {
                continue;
            };
            validate_navigation_dictionary(dictionary)?;
            nodes += 1;
            if nodes > MAX_TOC_TREE_NODES {
                return Err(toc_entry_count_err(format!(
                    "TOC outline tree exceeds the {MAX_TOC_TREE_NODES}-node safety limit"
                )));
            }
            for (key, child_depth) in [
                (b"Next".as_slice(), depth),
                (b"First".as_slice(), depth + 1),
            ] {
                if dictionary.has(key) {
                    if child_depth > MAX_OUTLINE_DEPTH {
                        return Err(PdfError::new_err(
                            "TOC outline tree exceeds the 64-level safety limit",
                        ));
                    }
                    edges += 1;
                    if edges > MAX_TOC_TREE_EDGES {
                        return Err(toc_entry_count_err(format!(
                            "TOC outline tree exceeds the {MAX_TOC_TREE_EDGES}-edge safety limit"
                        )));
                    }
                    push(&mut pending, (target.child(Step::Key(key))?, child_depth))?;
                }
            }
            let (direct, action) = navigation_to_removed(
                doc,
                dictionary,
                removed,
                named,
                &mut bytes,
                DestinationNameBudget::Toc,
            )?;
            if direct || action {
                push(
                    &mut self.dictionaries,
                    DictionaryPatch {
                        target,
                        direct,
                        action,
                    },
                )?;
            }
        }
        Ok(())
    }

    fn prepare_names<'a>(
        &mut self,
        doc: &'a Document,
        catalog: &Target,
        removed: &HashSet<ObjectId>,
        named: &mut Option<HashMap<&'a [u8], &'a Object>>,
    ) -> PyResult<()> {
        let mut entries = Vec::new();
        let names_container = catalog.child(Step::Key(b"Names"))?.resolve(doc)?;
        if let Some(names) = &names_container {
            let dictionary = names
                .object(doc)
                .and_then(|object| object.as_dict().ok())
                .ok_or_else(|| PdfError::new_err("catalog Names is not a dictionary"))?;
            validate_navigation_dictionary(dictionary)?;
        }
        if let Some(names) = names_container
            && let Some(root) = names.child(Step::Key(b"Dests"))?.resolve(doc)?
        {
            let dictionary = root
                .object(doc)
                .and_then(|object| object.as_dict().ok())
                .ok_or_else(|| {
                    PdfError::new_err("named-destination tree root is not a dictionary")
                })?;
            validate_navigation_dictionary(dictionary)?;
            let mut pending = Vec::new();
            push(
                &mut pending,
                (
                    root.child(Step::Key(b"Names"))?,
                    root.child(Step::Key(b"Kids"))?,
                    1usize,
                ),
            )?;
            let mut visited = HashSet::new();
            if root.path.is_empty() {
                visited.try_reserve(1).map_err(allocation_error)?;
                visited.insert(root.id);
            }
            let mut nodes = 0usize;
            let mut edges = 0usize;
            let mut total = 0usize;
            let mut bytes = 0usize;
            let mut clone_budget = CloneBudget::default();
            // Every pending node is a dictionary, matching the read-side node count.
            while let Some((names_target, kids_target, depth)) = pending.pop() {
                nodes += 1;
                if nodes > MAX_NAMED_DEST_TREE_NODES {
                    return Err(named_destination_count_err(format!(
                        "named-destination tree exceeds the {MAX_NAMED_DEST_TREE_NODES}-node safety limit"
                    )));
                }
                if let Some(names_target) = names_target.resolve(doc)?
                    && let Some(items) = names_target
                        .object(doc)
                        .and_then(|object| object.as_array().ok())
                {
                    total = total.saturating_add(items.len().div_ceil(2));
                    if total > MAX_NAMED_DEST_ENTRIES {
                        return Err(named_destination_count_err(format!(
                            "named-destination tree exceeds the {MAX_NAMED_DEST_ENTRIES}-entry safety limit"
                        )));
                    }
                    for (pair_index, pair) in items.chunks(2).enumerate() {
                        let [_, destination] = pair else { continue };
                        let Some(key_target) = names_target
                            .child(Step::Index(pair_index * 2))?
                            .resolve(doc)?
                        else {
                            continue;
                        };
                        let Some(Object::String(key_bytes, format)) = key_target.object(doc) else {
                            continue;
                        };
                        add_named_destination_text_budget(&mut bytes, key_bytes.len())?;
                        if !removed_destination(
                            doc,
                            destination,
                            removed,
                            named,
                            &mut bytes,
                            DestinationNameBudget::NamedDestination,
                        )? {
                            push(
                                &mut entries,
                                (
                                    Object::String(copy_admitted_bytes(key_bytes)?, *format),
                                    clone_budget.copy(destination, 1)?,
                                ),
                            )?;
                        }
                    }
                }
                if let Some(kids_target) = kids_target.resolve(doc)?
                    && let Some(kids) = kids_target
                        .object(doc)
                        .and_then(|object| object.as_array().ok())
                {
                    if !kids.is_empty() && depth >= MAX_NAMED_DEST_TREE_DEPTH {
                        return Err(PdfError::new_err(format!(
                            "named-destination tree exceeds the {MAX_NAMED_DEST_TREE_DEPTH}-level safety limit"
                        )));
                    }
                    edges = edges.saturating_add(kids.len());
                    if edges > MAX_NAMED_DEST_TREE_EDGES {
                        return Err(named_destination_count_err(format!(
                            "named-destination tree exceeds the {MAX_NAMED_DEST_TREE_EDGES}-edge safety limit"
                        )));
                    }
                    for index in (0..kids.len()).rev() {
                        let Some(child) = kids_target.child(Step::Index(index))?.resolve(doc)?
                        else {
                            continue;
                        };
                        let Some(dictionary) =
                            child.object(doc).and_then(|object| object.as_dict().ok())
                        else {
                            continue;
                        };
                        validate_navigation_dictionary(dictionary)?;
                        if child.path.is_empty() {
                            if visited.contains(&child.id) {
                                continue;
                            }
                            visited.try_reserve(1).map_err(allocation_error)?;
                            visited.insert(child.id);
                        }
                        push(
                            &mut pending,
                            (
                                child.child(Step::Key(b"Names"))?,
                                child.child(Step::Key(b"Kids"))?,
                                depth + 1,
                            ),
                        )?;
                    }
                }
            }
            entries.sort_unstable_by(|left, right| {
                left.0
                    .as_str()
                    .unwrap_or_default()
                    .cmp(right.0.as_str().unwrap_or_default())
            });
            let mut flat = Vec::new();
            flat.try_reserve_exact(entries.len() * 2)
                .map_err(allocation_error)?;
            for (key, value) in entries {
                flat.push(key);
                flat.push(value);
            }
            let mut dictionary = Dictionary::new();
            dictionary
                .as_hashmap_mut()
                .try_reserve(1)
                .map_err(allocation_error)?;
            dictionary.set(b"Names".to_vec(), Object::Array(flat));
            push(
                &mut self.replacements,
                (root, Object::Dictionary(dictionary)),
            )?;
        }
        if let Some(legacy) = catalog.child(Step::Key(b"Dests"))?.resolve(doc)?
            && let Some(dictionary) = legacy.object(doc).and_then(|object| object.as_dict().ok())
        {
            validate_navigation_dictionary(dictionary)?;
            if dictionary.len() > MAX_NAMED_DEST_ENTRIES {
                return Err(named_destination_count_err(format!(
                    "legacy named destinations exceed the {MAX_NAMED_DEST_ENTRIES}-entry safety limit"
                )));
            }
            let mut replacement = Dictionary::new();
            replacement
                .as_hashmap_mut()
                .try_reserve(dictionary.len())
                .map_err(allocation_error)?;
            let mut bytes = 0usize;
            let mut clone_budget = CloneBudget::default();
            for (key, value) in dictionary.iter() {
                add_named_destination_text_budget(&mut bytes, key.len())?;
                if !removed_destination(
                    doc,
                    value,
                    removed,
                    named,
                    &mut bytes,
                    DestinationNameBudget::NamedDestination,
                )? {
                    let copied_key = copy_admitted_bytes(key)?;
                    let copied_value = clone_budget.copy(value, 1)?;
                    replacement.set(copied_key, copied_value);
                }
            }
            push(
                &mut self.replacements,
                (legacy, Object::Dictionary(replacement)),
            )?;
        }
        Ok(())
    }

    /// The indirect link annotations that leave the remaining pages.
    pub(crate) fn removed_links(&self) -> &[ObjectId] {
        &self.removed_links
    }

    /// Filter inline annotation arrays on every selected original or duplicate.
    pub(crate) fn clean_page(&self, source: ObjectId, page: &mut Dictionary) {
        if let Some(indices) = self.page_links.get(&source)
            && let Ok(Object::Array(items)) = page.get_mut(b"Annots")
        {
            filter(items, indices);
        }
    }

    /// Apply prepared navigation edits after all page-tree validation succeeds.
    pub(crate) fn apply(self, doc: &mut Document) {
        for (id, indices) in self.indirect_links {
            if let Ok(Object::Array(items)) = doc.get_object_mut(id) {
                filter(items, &indices);
            }
        }
        for patch in self.dictionaries {
            if let Some(object) = patch.target.object_mut(doc)
                && let Ok(dictionary) = object.as_dict_mut()
            {
                if patch.direct {
                    dictionary.remove(b"Dest");
                }
                if patch.action {
                    dictionary.remove(b"A");
                }
            }
        }
        for (target, replacement) in self.replacements {
            if let Some(object) = target.object_mut(doc) {
                *object = replacement;
            }
        }
        if let Ok(catalog) = doc.catalog_mut() {
            if self.remove_outlines {
                catalog.remove(b"Outlines");
            }
            if self.remove_open_action {
                catalog.remove(b"OpenAction");
            }
        }
    }
}

fn filter(items: &mut Vec<Object>, excluded: &[usize]) {
    let mut index = 0;
    items.retain(|_| {
        let keep = excluded.binary_search(&index).is_err();
        index += 1;
        keep
    });
}

/// Copy bytes that a text budget has already admitted.
fn copy_admitted_bytes(value: &[u8]) -> PyResult<Vec<u8>> {
    let mut copied = Vec::new();
    copied
        .try_reserve_exact(value.len())
        .map_err(allocation_error)?;
    copied.extend_from_slice(value);
    Ok(copied)
}

fn copied_objects_err() -> PyErr {
    named_destination_count_err(format!(
        "named-destination values exceed the {MAX_COPIED_DESTINATION_OBJECTS}-object safety limit"
    ))
}

/// Bound copied destination values; their keys are admitted by the text budget.
#[derive(Default)]
struct CloneBudget {
    objects: usize,
    bytes: usize,
}

impl CloneBudget {
    fn bytes(&mut self, value: &[u8]) -> PyResult<Vec<u8>> {
        self.bytes = self.bytes.saturating_add(value.len());
        if self.bytes > MAX_NAMED_DEST_NAME_BYTES {
            return Err(limit_err(
                "named_destination_text_size",
                format!(
                    "named-destination values exceed the {MAX_NAMED_DEST_NAME_BYTES}-byte safety limit"
                ),
            ));
        }
        copy_admitted_bytes(value)
    }

    fn copy(&mut self, object: &Object, depth: usize) -> PyResult<Object> {
        if depth > MAX_DEST_DEPTH {
            return Err(PdfError::new_err(format!(
                "named-destination values exceed the {MAX_DEST_DEPTH}-level safety limit"
            )));
        }
        self.objects += 1;
        if self.objects > MAX_COPIED_DESTINATION_OBJECTS {
            return Err(copied_objects_err());
        }
        Ok(match object {
            Object::Name(bytes) => Object::Name(self.bytes(bytes)?),
            Object::String(bytes, format) => Object::String(self.bytes(bytes)?, *format),
            Object::Array(items) => {
                if items.len() > MAX_COPIED_DESTINATION_OBJECTS - self.objects {
                    return Err(copied_objects_err());
                }
                let mut copied = Vec::new();
                copied
                    .try_reserve_exact(items.len())
                    .map_err(allocation_error)?;
                for item in items {
                    copied.push(self.copy(item, depth + 1)?);
                }
                Object::Array(copied)
            }
            Object::Dictionary(dictionary) => {
                if dictionary.len() > MAX_COPIED_DESTINATION_OBJECTS - self.objects {
                    return Err(copied_objects_err());
                }
                let mut copied = Dictionary::new();
                copied
                    .as_hashmap_mut()
                    .try_reserve(dictionary.len())
                    .map_err(allocation_error)?;
                for (key, value) in dictionary.iter() {
                    let key = self.bytes(key)?;
                    copied.set(key, self.copy(value, depth + 1)?);
                }
                Object::Dictionary(copied)
            }
            Object::Stream(_) => {
                return Err(PdfError::new_err(
                    "a named destination cannot be a direct stream",
                ));
            }
            _ => object.clone(),
        })
    }
}
