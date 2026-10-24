//! Copies of pages within one document, with annotations and structure of
//! their own.
//!
//! A copy is a new page object that shares the original's content streams
//! and resources. Its annotations are new objects whose `/P` names the copy,
//! with `/Popup`, a popup's `/Parent`, and `/IRT` remapped among them; a
//! widget becomes another widget of its field, so both pages show and edit
//! one value. In a tagged document, the elements that own the page's marked
//! content and annotations, with their ancestors up to the root or its
//! `Document` element, are copied for the copy, which gets parent-tree keys
//! of its own. `copy_pages` runs these edits on the document and restores a
//! clone when one fails.

use super::*;

/// The deepest element chain that copying follows up to the root.
const MAX_ELEMENT_DEPTH: usize = 256;
/// Parent-tree nodes and pairs that reading the tree visits.
const MAX_TREE_NODES: usize = 1 << 20;
const MAX_TREE_DEPTH: usize = 32;

fn tree_error(message: impl Into<String>) -> PyErr {
    limit_err("structure_tree_count", message)
}

fn alloc_err(error: std::collections::TryReserveError) -> PyErr {
    PdfError::new_err(format!("failed to allocate the page copy: {error}"))
}

/// The parent tree's pairs by key, bounded and cycle-aware.
fn parent_tree_pairs(doc: &Document, tree: &Dictionary) -> PyResult<HashMap<i64, Object>> {
    let mut out = HashMap::new();
    let mut visited = HashSet::new();
    let mut stack = vec![(tree, 0usize)];
    let mut nodes = 0usize;
    while let Some((node, depth)) = stack.pop() {
        nodes += 1;
        if nodes > MAX_TREE_NODES || depth > MAX_TREE_DEPTH {
            return Err(tree_error(format!(
                "the parent tree exceeds {MAX_TREE_NODES} nodes or {MAX_TREE_DEPTH} levels"
            )));
        }
        if let Ok(Object::Array(pairs)) = node.get(b"Nums").map(|nums| deref_object(doc, nums)) {
            out.try_reserve(pairs.len() / 2).map_err(alloc_err)?;
            for pair in pairs.chunks(2) {
                if let [key, value] = pair
                    && let Object::Integer(key) = deref_object(doc, key)
                {
                    out.insert(*key, value.clone());
                }
            }
        }
        if let Ok(Object::Array(kids)) = node.get(b"Kids").map(|kids| deref_object(doc, kids)) {
            for kid in kids {
                if let Object::Reference(id) = kid
                    && !visited.insert(*id)
                {
                    continue;
                }
                if let Some(child) = deref_dict_ref(doc, kid) {
                    stack.push((child, depth + 1));
                }
            }
        }
    }
    Ok(out)
}

/// A page copy's `/StructParents`, and the `/StructParent` of each of its
/// new annotations that has one.
type CopiedKeys = (Option<i64>, Vec<(ObjectId, i64)>);

/// The structure tree that page copies extend, edited as copies are made
/// and written back by `commit`.
pub(super) struct StructureCopy {
    root_id: ObjectId,
    pairs: HashMap<i64, Object>,
    next_key: i64,
    new_pairs: Vec<(i64, Object)>,
    /// Kids to append, by parent: the root or an element.
    appends: Vec<(ObjectId, Object)>,
}

impl StructureCopy {
    /// Read a tagged document's tree, or `None` for an untagged one or one
    /// whose root is no object.
    pub(super) fn read(doc: &Document) -> PyResult<Option<Self>> {
        let Some(root_id) = doc
            .catalog()
            .ok()
            .and_then(|catalog| catalog.get(b"StructTreeRoot").ok())
            .and_then(|root| root.as_reference().ok())
        else {
            return Ok(None);
        };
        let Ok(root) = doc.get_dictionary(root_id) else {
            return Ok(None);
        };
        let pairs = match root
            .get(b"ParentTree")
            .ok()
            .and_then(|tree| deref_dict_ref(doc, tree))
        {
            Some(tree) => parent_tree_pairs(doc, tree)?,
            None => HashMap::new(),
        };
        let largest = pairs.keys().copied().max();
        let next_key = root
            .get(b"ParentTreeNextKey")
            .ok()
            .and_then(|key| deref_object(doc, key).as_i64().ok())
            .unwrap_or(0)
            .max(largest.map_or(0, |largest| largest.saturating_add(1)));
        Ok(Some(Self {
            root_id,
            pairs,
            next_key,
            new_pairs: Vec::new(),
            appends: Vec::new(),
        }))
    }

    fn take_key(&mut self) -> PyResult<i64> {
        let key = self.next_key;
        if key >= i64::from(i32::MAX) {
            return Err(tree_error("the parent tree has no key left"));
        }
        self.next_key += 1;
        Ok(key)
    }

    /// Whether `id` is where copied chains stop: the root, or an element
    /// whose role is `Document`.
    fn is_top(&self, doc: &Document, id: ObjectId) -> bool {
        id == self.root_id
            || doc.get_dictionary(id).is_ok_and(|element| {
                matches!(element.get(b"S").and_then(Object::as_name), Ok(b"Document"))
            })
    }

    /// Copy the elements of page `source` for its copy `copy`: return the
    /// copy's `/StructParents` and, by new annotation, its `/StructParent`.
    pub(super) fn copy_page(
        &mut self,
        doc: &mut Document,
        source: ObjectId,
        copy: ObjectId,
        page_key: Option<i64>,
        annotations: &[(ObjectId, ObjectId, Option<i64>)],
    ) -> PyResult<CopiedKeys> {
        // The elements that own the page's content and annotations.
        let owners: Vec<Object> = match page_key.and_then(|key| self.pairs.get(&key)) {
            Some(Object::Array(items)) => items.clone(),
            Some(Object::Reference(id)) => match doc.objects.get(id) {
                Some(Object::Array(items)) => items.clone(),
                _ => Vec::new(),
            },
            _ => Vec::new(),
        };
        let mut annotation_owners = Vec::new();
        for (_, new, key) in annotations {
            if let Some(Object::Reference(owner)) = key.and_then(|key| self.pairs.get(&key)) {
                annotation_owners.try_reserve(1).map_err(alloc_err)?;
                annotation_owners.push((*new, *owner));
            }
        }
        // Those elements and their ancestors below the top, in discovery order.
        let mut chain = Vec::new();
        let mut copies: HashMap<ObjectId, ObjectId> = HashMap::new();
        let starts = owners
            .iter()
            .filter_map(|owner| owner.as_reference().ok())
            .chain(annotation_owners.iter().map(|(_, owner)| *owner));
        for start in starts {
            let mut id = start;
            for _ in 0..MAX_ELEMENT_DEPTH {
                if copies.contains_key(&id) || self.is_top(doc, id) {
                    break;
                }
                let Ok(element) = doc.get_dictionary(id) else {
                    break;
                };
                let parent = element.get(b"P").and_then(Object::as_reference).ok();
                chain.try_reserve(1).map_err(alloc_err)?;
                chain.push(id);
                copies.insert(id, (0, 0));
                match parent {
                    Some(parent) => id = parent,
                    None => break,
                }
            }
        }
        if chain.is_empty() {
            return Ok((None, Vec::new()));
        }
        for id in &chain {
            copies.insert(*id, doc.new_object_id());
        }
        let annotation_map: HashMap<ObjectId, ObjectId> = annotations
            .iter()
            .map(|(old, new, _)| (*old, *new))
            .collect();
        for id in &chain {
            let Ok(element) = doc.get_dictionary(*id) else {
                continue;
            };
            let mut element = element.clone();
            let on_source = element.get(b"Pg").and_then(Object::as_reference).ok() == Some(source);
            let parent = element.get(b"P").and_then(Object::as_reference).ok();
            if let Some(parent) = parent {
                element.set(
                    "P",
                    Object::Reference(copies.get(&parent).copied().unwrap_or(parent)),
                );
                if !copies.contains_key(&parent) {
                    self.appends.try_reserve(1).map_err(alloc_err)?;
                    self.appends.push((parent, Object::Reference(copies[id])));
                }
            }
            if on_source {
                element.set("Pg", Object::Reference(copy));
            }
            element.remove(b"ID");
            let kids: Vec<Object> = match element.get(b"K") {
                Ok(Object::Array(kids)) => kids.clone(),
                Ok(Object::Null) | Err(_) => Vec::new(),
                Ok(kid) => vec![kid.clone()],
            };
            let mut copied = Vec::new();
            copied.try_reserve_exact(kids.len()).map_err(alloc_err)?;
            for kid in &kids {
                match kid {
                    Object::Integer(_) if on_source => copied.push(kid.clone()),
                    Object::Reference(child) if copies.contains_key(child) => {
                        copied.push(Object::Reference(copies[child]));
                    }
                    other => {
                        let Some(reference) = deref_dict_ref(doc, other) else {
                            continue;
                        };
                        let kid_page = reference.get(b"Pg").and_then(Object::as_reference).ok();
                        let here = kid_page.map_or(on_source, |page| page == source);
                        match reference.get(b"Type").and_then(Object::as_name) {
                            Ok(b"MCR") if here && !reference.has(b"Stm") => {
                                let mut reference = reference.clone();
                                reference.set("Pg", Object::Reference(copy));
                                copied.push(Object::Dictionary(reference));
                            }
                            Ok(b"OBJR") => {
                                if let Some(new) = reference
                                    .get(b"Obj")
                                    .and_then(Object::as_reference)
                                    .ok()
                                    .and_then(|old| annotation_map.get(&old))
                                {
                                    let mut reference = reference.clone();
                                    reference.set("Obj", Object::Reference(*new));
                                    reference.set("Pg", Object::Reference(copy));
                                    copied.push(Object::Dictionary(reference));
                                }
                            }
                            _ => {}
                        }
                    }
                }
            }
            element.set("K", Object::Array(copied));
            doc.objects.insert(copies[id], Object::Dictionary(element));
        }
        let page_key = if owners.is_empty() {
            None
        } else {
            let key = self.take_key()?;
            let mapped = owners
                .iter()
                .map(|owner| match owner {
                    Object::Reference(id) => {
                        Object::Reference(copies.get(id).copied().unwrap_or(*id))
                    }
                    other => other.clone(),
                })
                .collect();
            self.new_pairs.try_reserve(1).map_err(alloc_err)?;
            self.new_pairs.push((key, Object::Array(mapped)));
            Some(key)
        };
        let mut annotation_keys = Vec::new();
        for (new, owner) in annotation_owners {
            if let Some(copied) = copies.get(&owner) {
                let key = self.take_key()?;
                self.new_pairs.try_reserve(1).map_err(alloc_err)?;
                self.new_pairs.push((key, Object::Reference(*copied)));
                annotation_keys.try_reserve(1).map_err(alloc_err)?;
                annotation_keys.push((new, key));
            }
        }
        Ok((page_key, annotation_keys))
    }

    /// Write the new parent-tree pairs and the appended kids.
    pub(super) fn commit(self, doc: &mut Document) {
        let Self {
            root_id,
            next_key,
            new_pairs,
            appends,
            ..
        } = self;
        if new_pairs.is_empty() && appends.is_empty() {
            return;
        }
        for (parent, kid) in appends {
            let Some(Object::Dictionary(parent)) = doc.objects.get_mut(&parent) else {
                continue;
            };
            match parent.get_mut(b"K") {
                Ok(Object::Array(kids)) => kids.push(kid),
                Ok(Object::Null) | Err(_) => parent.set("K", Object::Array(vec![kid])),
                Ok(other) => {
                    let existing = other.clone();
                    parent.set("K", Object::Array(vec![existing, kid]));
                }
            }
        }
        let Ok(root) = doc.get_dictionary(root_id) else {
            return;
        };
        let mut root = root.clone();
        let (tree_id, mut tree) = match root.get(b"ParentTree") {
            Ok(Object::Reference(id)) => match doc.get_dictionary(*id) {
                Ok(tree) => (Some(*id), tree.clone()),
                Err(_) => (None, Dictionary::new()),
            },
            Ok(Object::Dictionary(tree)) => (None, tree.clone()),
            _ => (None, Dictionary::new()),
        };
        let mut flat = Vec::new();
        for (key, value) in new_pairs {
            flat.push(Object::Integer(key));
            flat.push(value);
        }
        let has_nums = tree.has(b"Nums");
        if !has_nums && let Ok(Object::Array(kids)) = tree.get_mut(b"Kids") {
            let limits = Object::Array(vec![flat[0].clone(), flat[flat.len() - 2].clone()]);
            let mut leaf = Dictionary::new();
            leaf.set("Nums", Object::Array(flat));
            leaf.set("Limits", limits);
            let leaf = doc.add_object(leaf);
            kids.push(Object::Reference(leaf));
        } else if !flat.is_empty() {
            let mut nums = match tree.get(b"Nums") {
                Ok(Object::Array(nums)) => nums.clone(),
                _ => Vec::new(),
            };
            nums.extend(flat);
            tree.set("Nums", Object::Array(nums));
        }
        match tree_id {
            Some(id) => {
                doc.objects.insert(id, Object::Dictionary(tree));
            }
            None => root.set("ParentTree", Object::Dictionary(tree)),
        }
        root.set("ParentTreeNextKey", Object::Integer(next_key));
        doc.objects.insert(root_id, Object::Dictionary(root));
    }
}

impl _Document {
    /// Add `dict`, a copy of page `source`, as a new page object with its own
    /// annotations and structure, and return its ID.
    pub(super) fn add_page_copy(
        &mut self,
        source: ObjectId,
        mut dict: Dictionary,
        structure: &mut Option<StructureCopy>,
    ) -> PyResult<ObjectId> {
        let page_id = self.doc.new_object_id();
        let entries: Vec<Object> = match dict
            .get(b"Annots")
            .map(|annots| deref_object(&self.doc, annots))
        {
            Ok(Object::Array(entries)) => entries.clone(),
            _ => Vec::new(),
        };
        if entries.len() > MAX_PAGE_ANNOTATIONS {
            return Err(PdfError::new_err(format!(
                "the page lists more than {MAX_PAGE_ANNOTATIONS} annotations"
            )));
        }
        // New numbers for the annotations other than widgets, which their
        // fields number as they take them.
        let mut numbers: HashMap<ObjectId, ObjectId> = HashMap::new();
        for entry in &entries {
            if let Object::Reference(id) = entry
                && let Ok(annotation) = self.doc.get_dictionary(*id)
                && annotation.get(b"Subtype").and_then(Object::as_name).ok() != Some(b"Widget")
                && !numbers.contains_key(id)
            {
                numbers.insert(*id, self.doc.new_object_id());
            }
        }
        let remap = |annotation: &mut Dictionary, numbers: &HashMap<ObjectId, ObjectId>| {
            annotation.set("P", Object::Reference(page_id));
            for key in [&b"Popup"[..], b"Parent", b"IRT"] {
                if let Ok(Object::Reference(old)) = annotation.get(key)
                    && let Some(new) = numbers.get(old)
                {
                    let new = *new;
                    annotation.set(key, Object::Reference(new));
                }
            }
        };
        let mut annots = Vec::new();
        annots.try_reserve_exact(entries.len()).map_err(alloc_err)?;
        let mut copied = Vec::new();
        for entry in entries {
            match &entry {
                Object::Reference(id) => {
                    let Ok(annotation) = self.doc.get_dictionary(*id) else {
                        annots.push(entry);
                        continue;
                    };
                    let mut annotation = annotation.clone();
                    let key = annotation
                        .get(b"StructParent")
                        .ok()
                        .and_then(|key| key.as_i64().ok());
                    if annotation.get(b"Subtype").and_then(Object::as_name).ok() == Some(b"Widget")
                    {
                        // A kid widget joins its field; a merged field and
                        // widget becomes a field with both widgets.
                        let merged = annotation.has(b"T") || !annotation.has(b"Parent");
                        let field = if merged {
                            *id
                        } else {
                            annotation
                                .get(b"Parent")
                                .and_then(Object::as_reference)
                                .map_err(to_py_err)?
                        };
                        for field_key in FIELD_KEYS {
                            annotation.remove(field_key);
                        }
                        if let Ok(Object::Dictionary(actions)) = annotation.get_mut(b"AA") {
                            for trigger in FIELD_TRIGGERS {
                                actions.remove(trigger.as_bytes());
                            }
                        }
                        annotation.remove(b"Parent");
                        annotation.remove(b"StructParent");
                        remap(&mut annotation, &numbers);
                        let widget = self.add_field_widget(field, annotation)?;
                        annots.push(Object::Reference(widget));
                        copied.push((*id, widget, key));
                    } else {
                        remap(&mut annotation, &numbers);
                        annotation.remove(b"StructParent");
                        let new = numbers[id];
                        self.doc.objects.insert(new, Object::Dictionary(annotation));
                        annots.push(Object::Reference(new));
                        copied.push((*id, new, key));
                    }
                }
                Object::Dictionary(annotation) => {
                    let mut annotation = annotation.clone();
                    remap(&mut annotation, &numbers);
                    annotation.remove(b"StructParent");
                    annots.push(Object::Dictionary(annotation));
                }
                _ => annots.push(entry),
            }
        }
        if !annots.is_empty() {
            dict.set("Annots", Object::Array(annots));
        }
        let page_key = dict
            .get(b"StructParents")
            .ok()
            .and_then(|key| key.as_i64().ok());
        dict.remove(b"StructParents");
        if let Some(structure) = structure {
            let (page_key, annotation_keys) =
                structure.copy_page(&mut self.doc, source, page_id, page_key, &copied)?;
            if let Some(key) = page_key {
                dict.set("StructParents", Object::Integer(key));
            }
            for (annotation, key) in annotation_keys {
                if let Ok(annotation) = self
                    .doc
                    .get_object_mut(annotation)
                    .and_then(Object::as_dict_mut)
                {
                    annotation.set("StructParent", Object::Integer(key));
                }
            }
        }
        self.doc.objects.insert(page_id, Object::Dictionary(dict));
        Ok(page_id)
    }
}
