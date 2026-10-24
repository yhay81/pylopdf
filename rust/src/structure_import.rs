//! Plan the structure tree of pages that `merge_pages` imports.
//!
//! The source document is a disposable renumbered copy. Its structure
//! content that lies only on pages not imported is removed first, with the
//! cleanup of page deletion (`StructurePagePrune`). The elements that remain
//! join the receiving tree under its single `Document` element, or its root,
//! in place of the source's own `Document` element when it had one. Their
//! parent-tree keys, and the `/StructParents` and `/StructParent` entries
//! that name them, move past the receiving tree's keys; role-map entries
//! that the receiving tree maps otherwise resolve to the source's standard
//! type; clashing class names and element IDs gain a suffix; and namespaces
//! join the receiving list. The receiving side is planned before any
//! mutation, and the commit only stores prepared values.

use std::collections::{HashMap, HashSet};

use lopdf::{Dictionary, Document, Object, ObjectId};
use pyo3::prelude::*;

use crate::document::{PdfError, limit_err};
use crate::structure_prune::StructurePagePrune;

/// Elements, kids, and tree nodes that one import reads.
const MAX_IMPORT_NODES: usize = 1 << 20;
/// Role-map links that resolving one role follows.
const MAX_ROLE_CHAIN: usize = 16;
/// Levels of a number or name tree.
const MAX_TREE_DEPTH: usize = 32;

fn count_error(message: impl Into<String>) -> PyErr {
    limit_err("structure_tree_count", message)
}

fn alloc_err(error: std::collections::TryReserveError) -> PyErr {
    PdfError::new_err(format!("failed to allocate the structure import: {error}"))
}

fn deref<'a>(doc: &'a Document, mut object: &'a Object) -> &'a Object {
    for _ in 0..MAX_ROLE_CHAIN {
        match object {
            Object::Reference(id) => match doc.objects.get(id) {
                Some(next) => object = next,
                None => return &Object::Null,
            },
            _ => return object,
        }
    }
    &Object::Null
}

fn dict<'a>(doc: &'a Document, object: &'a Object) -> Option<&'a Dictionary> {
    deref(doc, object).as_dict().ok()
}

fn catalog(doc: &Document) -> Option<&Dictionary> {
    let id = doc
        .trailer
        .get(b"Root")
        .and_then(Object::as_reference)
        .ok()?;
    doc.objects.get(&id)?.as_dict().ok()
}

fn is_element(dict: &Dictionary) -> bool {
    dict.has(b"S")
        && !matches!(
            dict.get(b"Type").and_then(Object::as_name),
            Ok(b"MCR" | b"OBJR")
        )
}

/// Flatten a number or name tree into its pairs, bounded and cycle-aware.
fn tree_pairs(doc: &Document, root: &Dictionary, key: &[u8]) -> PyResult<Vec<(Object, Object)>> {
    let mut out = Vec::new();
    let mut visited = HashSet::new();
    let mut stack = vec![(root, 0usize)];
    let mut nodes = 0usize;
    while let Some((node, depth)) = stack.pop() {
        nodes += 1;
        if nodes > MAX_IMPORT_NODES || depth > MAX_TREE_DEPTH {
            return Err(count_error(format!(
                "a structure lookup tree exceeds {MAX_IMPORT_NODES} nodes or {MAX_TREE_DEPTH} levels"
            )));
        }
        if let Ok(Object::Array(pairs)) = node.get(key).map(|pairs| deref(doc, pairs)) {
            out.try_reserve(pairs.len() / 2).map_err(alloc_err)?;
            for pair in pairs.chunks(2) {
                if let [name, value] = pair {
                    out.push((deref(doc, name).clone(), value.clone()));
                }
            }
        }
        if let Ok(Object::Array(kids)) = node.get(b"Kids").map(|kids| deref(doc, kids)) {
            for kid in kids {
                if let Object::Reference(id) = kid
                    && !visited.insert(*id)
                {
                    continue;
                }
                if let Some(child) = dict(doc, kid) {
                    stack.try_reserve(1).map_err(alloc_err)?;
                    stack.push((child, depth + 1));
                }
            }
        }
    }
    Ok(out)
}

/// Where a dictionary of the receiving tree lives.
#[derive(Clone, Copy)]
enum Slot {
    Object(ObjectId),
    Direct,
}

fn dictionary_at(doc: &Document, object: Option<&Object>) -> Option<(Slot, Dictionary)> {
    match object? {
        Object::Reference(id) => match doc.objects.get(id)? {
            Object::Dictionary(dict) => Some((Slot::Object(*id), dict.clone())),
            _ => None,
        },
        Object::Dictionary(dict) => Some((Slot::Direct, dict.clone())),
        _ => None,
    }
}

/// The resolved standard type of `role` through `role_map`.
fn resolved_role(doc: &Document, role_map: Option<&Dictionary>, role: &[u8]) -> Vec<u8> {
    let mut role = role.to_vec();
    for _ in 0..MAX_ROLE_CHAIN {
        match role_map
            .and_then(|map| map.get(&role).ok())
            .map(|mapped| deref(doc, mapped))
        {
            Some(Object::Name(mapped)) if *mapped != role => role = mapped.clone(),
            _ => break,
        }
    }
    role
}

/// A name that `taken` does not hold: `name`, else `name_N` (or `name+N`
/// for IDs) with the smallest such N.
fn unused(name: &[u8], taken: &HashSet<Vec<u8>>, separator: u8) -> Vec<u8> {
    if !taken.contains(name) {
        return name.to_vec();
    }
    (1..)
        .map(|n| {
            let mut candidate = name.to_vec();
            candidate.push(separator);
            candidate.extend_from_slice(n.to_string().as_bytes());
            candidate
        })
        .find(|candidate| !taken.contains(candidate))
        .unwrap_or_default()
}

/// The planned structure tree of imported pages.
pub(crate) struct StructureImport {
    /// Objects of the receiving document to replace or add, and the
    /// catalog's new `/StructTreeRoot` reference when it had none.
    replaced: Vec<(ObjectId, Object)>,
    catalog_root: Option<ObjectId>,
    /// How many object numbers after the import the plan used.
    pub(crate) used_ids: u32,
}

impl StructureImport {
    /// Plan the import of `source`'s structure for `imported` of its `pages`
    /// into `target`; `pages_dicts` are the imported page dictionaries,
    /// whose `/StructParents` move with the keys. New objects take numbers
    /// from `next_id`.
    pub(crate) fn prepare(
        target: &Document,
        source: &mut Document,
        pages: &[ObjectId],
        imported: &HashSet<ObjectId>,
        page_dicts: &mut [(ObjectId, Dictionary)],
        next_id: u32,
    ) -> PyResult<Option<Self>> {
        let Some(source_root) = catalog(source)
            .and_then(|catalog| catalog.get(b"StructTreeRoot").ok())
            .and_then(|root| dict(source, root))
            .cloned()
        else {
            return Ok(None);
        };
        let source_root_id = catalog(source)
            .and_then(|catalog| catalog.get(b"StructTreeRoot").ok())
            .and_then(|root| root.as_reference().ok());
        if let Some(prune) = StructurePagePrune::prepare(source, pages, imported, &[])? {
            prune.apply(source);
        }
        let source_root = match source_root_id.and_then(|id| source.objects.get(&id)) {
            Some(Object::Dictionary(root)) => root.clone(),
            _ => source_root,
        };
        let source_role_map = source_root
            .get(b"RoleMap")
            .ok()
            .and_then(|map| dict(source, map))
            .cloned();

        // The receiving tree.
        let target_catalog =
            catalog(target).ok_or_else(|| PdfError::new_err("the document has no catalog"))?;
        let (root_slot, mut root) =
            dictionary_at(target, target_catalog.get(b"StructTreeRoot").ok()).unwrap_or_else(
                || {
                    let mut root = Dictionary::new();
                    root.set("Type", Object::Name(b"StructTreeRoot".to_vec()));
                    (Slot::Direct, root)
                },
            );
        let mut used_ids = 0u32;
        let mut allocate = || -> PyResult<ObjectId> {
            let id = next_id
                .checked_add(used_ids)
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
            used_ids += 1;
            Ok((id, 0))
        };
        let root_id = match root_slot {
            Slot::Object(id) => id,
            Slot::Direct => allocate()?,
        };
        let target_role_map = root
            .get(b"RoleMap")
            .ok()
            .and_then(|map| dict(target, map))
            .cloned();
        let (tree_slot, mut tree) = dictionary_at(target, root.get(b"ParentTree").ok())
            .unwrap_or_else(|| (Slot::Direct, Dictionary::new()));
        let target_pairs = tree_pairs(target, &tree, b"Nums")?;
        let largest = target_pairs
            .iter()
            .filter_map(|(key, _)| key.as_i64().ok())
            .max();
        let next_key = root
            .get(b"ParentTreeNextKey")
            .ok()
            .and_then(|key| deref(target, key).as_i64().ok())
            .unwrap_or(0)
            .max(largest.map_or(0, |largest| largest.saturating_add(1)));
        let document_element = match root.get(b"K") {
            Ok(Object::Array(items)) if items.len() == 1 => items[0].as_reference().ok(),
            Ok(Object::Reference(id)) => match target.objects.get(id) {
                Some(Object::Array(items)) if items.len() == 1 => items[0].as_reference().ok(),
                Some(Object::Dictionary(_)) => Some(*id),
                _ => None,
            },
            _ => None,
        }
        .and_then(|id| match target.objects.get(&id) {
            Some(Object::Dictionary(element))
                if matches!(element.get(b"S").and_then(Object::as_name), Ok(name)
                    if resolved_role(target, target_role_map.as_ref(), name) == b"Document") =>
            {
                Some((id, element.clone()))
            }
            _ => None,
        });
        let top_parent = document_element.as_ref().map_or(root_id, |(id, _)| *id);
        // The source's top-level elements, through its single Document element.
        // Kids stay as written: a lone reference is one kid, not its object.
        let root_kids: Vec<Object> = match source_root.get(b"K") {
            Ok(Object::Array(kids)) => kids.clone(),
            Ok(Object::Reference(id)) => match source.objects.get(id) {
                Some(Object::Array(kids)) => kids.clone(),
                Some(Object::Null) | None => Vec::new(),
                Some(_) => vec![Object::Reference(*id)],
            },
            Ok(Object::Null) | Err(_) => Vec::new(),
            Ok(other) => vec![other.clone()],
        };
        let mut skipped_parents: HashSet<ObjectId> = source_root_id.into_iter().collect();
        // A receiving Document element takes the kids of the source's own;
        // otherwise the source's Document element comes along.
        let top_kids = match root_kids.as_slice() {
            [Object::Reference(id)]
                if document_element.is_some()
                    && source
                        .objects
                        .get(id)
                        .and_then(|object| object.as_dict().ok())
                        .is_some_and(|element| {
                            matches!(element.get(b"S").and_then(Object::as_name), Ok(name)
                            if resolved_role(source, source_role_map.as_ref(), name) == b"Document")
                        }) =>
            {
                skipped_parents.insert(*id);
                match source
                    .objects
                    .get(id)
                    .and_then(|object| object.as_dict().ok())
                    .and_then(|element| element.get(b"K").ok())
                {
                    Some(Object::Array(kids)) => kids.clone(),
                    Some(Object::Null) | None => Vec::new(),
                    Some(other) => vec![other.clone()],
                }
            }
            _ => root_kids,
        };
        if top_kids.is_empty() {
            return Ok(None);
        }

        // Parent-tree keys move past the receiving tree's.
        let source_tree = source_root
            .get(b"ParentTree")
            .ok()
            .and_then(|tree| dict(source, tree))
            .cloned();
        let source_pairs = match &source_tree {
            Some(tree) => tree_pairs(source, tree, b"Nums")?,
            None => Vec::new(),
        };
        let smallest = source_pairs
            .iter()
            .filter_map(|(key, _)| key.as_i64().ok())
            .min()
            .unwrap_or(0);
        let shift = next_key.saturating_sub(smallest);
        let shifted = |key: i64| key.saturating_add(shift);
        for object in source.objects.values_mut() {
            let dict = match object {
                Object::Dictionary(dict) => dict,
                Object::Stream(stream) => &mut stream.dict,
                _ => continue,
            };
            for key in [&b"StructParents"[..], b"StructParent"] {
                if let Ok(Object::Integer(value)) = dict.get(key) {
                    let value = *value;
                    dict.set(key, Object::Integer(shifted(value)));
                }
            }
        }
        for (_, page) in page_dicts.iter_mut() {
            if let Ok(Object::Integer(value)) = page.get(b"StructParents") {
                let value = *value;
                page.set("StructParents", Object::Integer(shifted(value)));
            }
        }

        // Elements: roles, classes, and IDs that the receiving tree holds otherwise.
        let mut role_map = target_role_map.clone().unwrap_or_default();
        let mut class_map = root
            .get(b"ClassMap")
            .ok()
            .and_then(|map| dict(target, map))
            .cloned()
            .unwrap_or_default();
        let source_class_map = source_root
            .get(b"ClassMap")
            .ok()
            .and_then(|map| dict(source, map))
            .cloned();
        let mut class_names: HashSet<Vec<u8>> =
            class_map.iter().map(|(name, _)| name.clone()).collect();
        let mut class_renames: HashMap<Vec<u8>, Vec<u8>> = HashMap::new();
        if let Some(source_classes) = &source_class_map {
            for (name, value) in source_classes.iter() {
                let same = class_map.get(name).ok() == Some(value);
                if same {
                    continue;
                }
                let new = unused(name, &class_names, b'_');
                class_names.insert(new.clone());
                class_map.set(new.clone(), value.clone());
                if new != *name {
                    class_renames.insert(name.clone(), new);
                }
            }
        }
        let id_tree = root
            .get(b"IDTree")
            .ok()
            .and_then(|tree| dict(target, tree))
            .cloned();
        let mut ids = match &id_tree {
            Some(tree) => tree_pairs(target, tree, b"Names")?,
            None => Vec::new(),
        };
        let mut taken_ids: HashSet<Vec<u8>> = ids
            .iter()
            .filter_map(|(name, _)| name.as_str().ok().map(<[u8]>::to_vec))
            .collect();

        let mut elements = Vec::new();
        let mut visited = HashSet::new();
        let mut stack: Vec<ObjectId> = top_kids
            .iter()
            .rev()
            .filter_map(|kid| kid.as_reference().ok())
            .collect();
        while let Some(id) = stack.pop() {
            if !visited.insert(id) {
                continue;
            }
            if visited.len() > MAX_IMPORT_NODES {
                return Err(count_error(format!(
                    "imported structure exceeds {MAX_IMPORT_NODES} elements"
                )));
            }
            let Some(Object::Dictionary(element)) = source.objects.get(&id) else {
                continue;
            };
            if !is_element(element) {
                continue;
            }
            elements.try_reserve(1).map_err(alloc_err)?;
            elements.push(id);
            if let Ok(kids) = element.get(b"K") {
                let kids = match kids {
                    Object::Array(kids) => kids.as_slice(),
                    other => std::slice::from_ref(other),
                };
                for kid in kids.iter().rev() {
                    if let Object::Reference(child) = kid {
                        stack.try_reserve(1).map_err(alloc_err)?;
                        stack.push(*child);
                    }
                }
            }
        }
        for id in &elements {
            // The role that the element takes, decided before it is edited.
            let role = match source.objects.get(id) {
                Some(Object::Dictionary(element)) => match element.get(b"S") {
                    Ok(Object::Name(role)) => Some(role.clone()),
                    _ => None,
                },
                _ => None,
            };
            let new_role = match &role {
                Some(role) => {
                    let ours = source_role_map
                        .as_ref()
                        .and_then(|map| map.get(role).ok())
                        .cloned();
                    let theirs = role_map.get(role).ok().cloned();
                    match (ours, theirs) {
                        (Some(ours), Some(theirs)) if ours != theirs => {
                            Some(resolved_role(source, source_role_map.as_ref(), role))
                        }
                        (Some(ours), None) => {
                            role_map.set(role.clone(), ours);
                            None
                        }
                        _ => None,
                    }
                }
                None => None,
            };
            let Some(Object::Dictionary(element)) = source.objects.get_mut(id) else {
                continue;
            };
            if element
                .get(b"P")
                .and_then(Object::as_reference)
                .is_ok_and(|parent| skipped_parents.contains(&parent))
            {
                element.set("P", Object::Reference(top_parent));
            }
            if let Some(new_role) = new_role {
                element.set("S", Object::Name(new_role));
            }
            if !class_renames.is_empty()
                && let Ok(classes) = element.get_mut(b"C")
            {
                let rename = |class: &mut Object| {
                    if let Object::Name(name) = class
                        && let Some(new) = class_renames.get(name)
                    {
                        *name = new.clone();
                    }
                };
                match classes {
                    Object::Array(items) => items.iter_mut().for_each(rename),
                    other => rename(other),
                }
            }
            if let Ok(Object::String(identifier, format)) = element.get(b"ID") {
                let format = *format;
                let new = unused(identifier, &taken_ids, b'+');
                taken_ids.insert(new.clone());
                element.set("ID", Object::String(new.clone(), format));
                ids.try_reserve(1).map_err(alloc_err)?;
                ids.push((Object::String(new, format), Object::Reference(*id)));
            }
        }

        // The receiving parent tree takes the shifted pairs after its own.
        let mut pairs = Vec::new();
        pairs
            .try_reserve_exact(source_pairs.len() * 2)
            .map_err(alloc_err)?;
        let mut sorted: Vec<(i64, Object)> = source_pairs
            .into_iter()
            .filter_map(|(key, value)| key.as_i64().ok().map(|key| (shifted(key), value)))
            .collect();
        sorted.sort_unstable_by_key(|(key, _)| *key);
        let new_largest = sorted.last().map(|(key, _)| *key);
        for (key, value) in sorted {
            pairs.push(Object::Integer(key));
            pairs.push(value);
        }
        let mut replaced = Vec::new();
        if !pairs.is_empty() {
            let flat = tree.has(b"Nums");
            match tree.get_mut(b"Kids") {
                Ok(Object::Array(kids)) if !flat => {
                    let leaf = allocate()?;
                    let limits =
                        Object::Array(vec![pairs[0].clone(), pairs[pairs.len() - 2].clone()]);
                    let mut node = Dictionary::new();
                    node.set("Nums", Object::Array(pairs));
                    node.set("Limits", limits);
                    replaced.push((leaf, Object::Dictionary(node)));
                    kids.push(Object::Reference(leaf));
                }
                _ => {
                    let mut nums = match tree.get(b"Nums").map(|nums| deref(target, nums)) {
                        Ok(Object::Array(nums)) => nums.clone(),
                        _ => Vec::new(),
                    };
                    nums.extend(pairs);
                    tree.set("Nums", Object::Array(nums));
                }
            }
        }
        match tree_slot {
            Slot::Object(id) => replaced.push((id, Object::Dictionary(tree))),
            Slot::Direct => root.set("ParentTree", Object::Dictionary(tree)),
        }
        root.set(
            "ParentTreeNextKey",
            Object::Integer(
                new_largest
                    .map_or(next_key, |largest| largest + 1)
                    .max(next_key),
            ),
        );
        if !role_map.is_empty() {
            root.set("RoleMap", Object::Dictionary(role_map));
        }
        if !class_map.is_empty() {
            root.set("ClassMap", Object::Dictionary(class_map));
        }
        if !ids.is_empty() {
            ids.sort_by(|(left, _), (right, _)| left.as_str().ok().cmp(&right.as_str().ok()));
            let mut names = Vec::new();
            names.try_reserve_exact(ids.len() * 2).map_err(alloc_err)?;
            for (name, value) in ids {
                names.push(name);
                names.push(value);
            }
            let mut node = Dictionary::new();
            node.set("Names", Object::Array(names));
            root.set("IDTree", Object::Dictionary(node));
        }
        if let Some(namespaces) = source_root
            .get(b"Namespaces")
            .ok()
            .map(|namespaces| deref(source, namespaces))
        {
            let mut known = match root
                .get(b"Namespaces")
                .map(|namespaces| deref(target, namespaces))
            {
                Ok(Object::Array(items)) => items.clone(),
                _ => Vec::new(),
            };
            if let Object::Array(items) = namespaces {
                for item in items {
                    if !known.contains(item) {
                        known.push(item.clone());
                    }
                }
            }
            root.set("Namespaces", Object::Array(known));
        }

        // The top-level elements follow the receiving ones.
        let top = top_kids.into_iter().map(|kid| match kid {
            Object::Dictionary(mut element) if is_element(&element) => {
                element.set("P", Object::Reference(top_parent));
                Object::Dictionary(element)
            }
            other => other,
        });
        let append = |kids: Option<&Object>| -> Vec<Object> {
            match kids {
                Some(Object::Array(items)) => items.clone(),
                Some(Object::Null) | None => Vec::new(),
                Some(other) => vec![other.clone()],
            }
        };
        match document_element {
            Some((id, mut element)) => {
                let mut kids = append(element.get(b"K").ok());
                kids.extend(top);
                element.set("K", Object::Array(kids));
                replaced.push((id, Object::Dictionary(element)));
            }
            None => {
                let mut kids = append(root.get(b"K").ok());
                kids.extend(top);
                root.set("K", Object::Array(kids));
            }
        }
        let catalog_root = matches!(root_slot, Slot::Direct).then_some(root_id);
        replaced.push((root_id, Object::Dictionary(root)));
        Ok(Some(Self {
            replaced,
            catalog_root,
            used_ids,
        }))
    }

    /// Store the planned tree; this cannot fail.
    pub(crate) fn apply(self, doc: &mut Document) {
        for (id, object) in self.replaced {
            doc.objects.insert(id, object);
        }
        if let Some(root) = self.catalog_root
            && let Some(catalog) = doc
                .trailer
                .get(b"Root")
                .and_then(Object::as_reference)
                .ok()
                .and_then(|id| doc.objects.get_mut(&id))
                .and_then(|object| object.as_dict_mut().ok())
        {
            catalog.set("StructTreeRoot", Object::Reference(root));
        }
    }
}
