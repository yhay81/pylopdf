//! Structure-tree maintenance on existing documents: adding the elements
//! that a canvas tags.
//!
//! A canvas commits as one Form XObject whose marked content numbers its
//! MCIDs from 0, so the Form gets a `/StructParents` key of its own in the
//! parent tree, and each element names its content through marked-content
//! references with `/Stm`, which content in a Form XObject needs (ISO
//! 32000-2 §14.7.5.3). The new elements join the root's single `Document`
//! element when it has one, as PDF/UA-2 asks, and the root itself otherwise.
//! Everything that reads the document is planned before it changes; the
//! commit only adds objects and stores prepared values.

use super::*;

use crate::canvas::{CanvasKid, CanvasStructure};

/// The most number-tree nodes that finding the next parent-tree key reads.
const MAX_PARENT_TREE_NODES: usize = 1 << 20;
/// The deepest number tree that it follows.
const MAX_PARENT_TREE_DEPTH: usize = 32;

fn count_error(message: impl Into<String>) -> PyErr {
    limit_err("structure_tree_count", message)
}

fn alloc_err(error: std::collections::TryReserveError) -> PyErr {
    PdfError::new_err(format!(
        "failed to allocate the structure tree edit: {error}"
    ))
}

/// Where a dictionary of the structure tree lives: an object, or directly in
/// the catalog (`/StructTreeRoot`) or in the root (`/ParentTree`).
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

/// The largest key of a number tree, walked iteratively and cycle-aware.
fn max_number_key(doc: &Document, root: &Dictionary) -> PyResult<Option<i64>> {
    let mut largest = None;
    let mut visited = HashSet::new();
    let mut stack: Vec<(&Dictionary, usize)> = Vec::new();
    stack.try_reserve(1).map_err(alloc_err)?;
    stack.push((root, 0));
    let mut nodes = 0usize;
    while let Some((node, depth)) = stack.pop() {
        nodes += 1;
        if nodes > MAX_PARENT_TREE_NODES || depth > MAX_PARENT_TREE_DEPTH {
            return Err(count_error(format!(
                "the parent tree exceeds {MAX_PARENT_TREE_NODES} nodes or {MAX_PARENT_TREE_DEPTH} levels"
            )));
        }
        if let Ok(Object::Array(pairs)) = node.get(b"Nums").map(|nums| deref_object(doc, nums)) {
            for key in pairs.iter().step_by(2) {
                if let Object::Integer(key) = deref_object(doc, key) {
                    largest = Some(largest.map_or(*key, |known: i64| known.max(*key)));
                }
            }
        }
        if let Ok(Object::Array(kids)) = node.get(b"Kids").map(|kids| deref_object(doc, kids)) {
            for kid in kids {
                if let Object::Reference(id) = kid {
                    visited.try_reserve(1).map_err(alloc_err)?;
                    if !visited.insert(*id) {
                        continue;
                    }
                }
                if let Some(child) = deref_dict_ref(doc, kid) {
                    stack.try_reserve(1).map_err(alloc_err)?;
                    stack.push((child, depth + 1));
                }
            }
        }
    }
    Ok(largest)
}

/// Whether `element` is a `Document` element, directly or through the
/// root's role map.
fn is_document_element(
    doc: &Document,
    element: &Dictionary,
    role_map: Option<&Dictionary>,
) -> bool {
    let Ok(Object::Name(role)) = element.get(b"S").map(|role| deref_object(doc, role)) else {
        return false;
    };
    if role == b"Document" {
        return true;
    }
    matches!(
        role_map.and_then(|map| map.get(role).ok()).map(|mapped| deref_object(doc, mapped)),
        Some(Object::Name(mapped)) if mapped == b"Document"
    )
}

/// The elements of one canvas, planned into the document's structure tree.
pub(super) struct StructureAppend {
    structure: CanvasStructure,
    /// The root, where it lives, and whether the catalog needs a reference.
    root: (Slot, Dictionary),
    parent_tree: (Slot, Dictionary),
    key: i64,
    /// The `Document` element that takes the new top-level elements, or
    /// `None` for the root.
    document_element: Option<(ObjectId, Dictionary)>,
}

impl StructureAppend {
    /// Plan the elements of `structure`, or `None` when it holds none.
    pub(super) fn prepare(doc: &Document, structure: CanvasStructure) -> PyResult<Option<Self>> {
        if structure.elements.is_empty() {
            return Ok(None);
        }
        if structure.elements.len() > MAX_STRUCTURE_ELEMENTS {
            return Err(count_error(format!(
                "a canvas tags more than {MAX_STRUCTURE_ELEMENTS} structure elements"
            )));
        }
        let catalog = doc.catalog().map_err(to_py_err)?;
        let root = dictionary_at(doc, catalog.get(b"StructTreeRoot").ok()).unwrap_or_else(|| {
            let mut root = Dictionary::new();
            root.set("Type", Object::Name(b"StructTreeRoot".to_vec()));
            (Slot::Direct, root)
        });
        let parent_tree = dictionary_at(doc, root.1.get(b"ParentTree").ok())
            .unwrap_or_else(|| (Slot::Direct, Dictionary::new()));
        let next = root
            .1
            .get(b"ParentTreeNextKey")
            .ok()
            .and_then(|key| deref_object(doc, key).as_i64().ok())
            .unwrap_or(0);
        let largest = max_number_key(doc, &parent_tree.1)?;
        let key = next.max(largest.map_or(0, |largest| largest.saturating_add(1)));
        if key >= i64::from(i32::MAX) {
            return Err(count_error("the parent tree has no key left"));
        }
        let role_map = root
            .1
            .get(b"RoleMap")
            .ok()
            .and_then(|map| deref_dict_ref(doc, map));
        let kids = root.1.get(b"K").ok().map(|kids| deref_object(doc, kids));
        let only = match kids {
            Some(Object::Array(items)) if items.len() == 1 => Some(&items[0]),
            Some(item @ Object::Reference(_)) => Some(item),
            _ => None,
        };
        let document_element = match only {
            Some(Object::Reference(id)) => match doc.objects.get(id) {
                Some(Object::Dictionary(element))
                    if is_document_element(doc, element, role_map) =>
                {
                    Some((*id, element.clone()))
                }
                _ => None,
            },
            _ => None,
        };
        Ok(Some(Self {
            structure,
            root,
            parent_tree,
            key,
            document_element,
        }))
    }

    /// Add the elements for content that page `page_id` draws through the
    /// Form XObject `form_id`.
    pub(super) fn apply(self, doc: &mut Document, page_id: ObjectId, form_id: ObjectId) {
        let Self {
            structure,
            root: (root_slot, mut root),
            parent_tree: (tree_slot, mut tree),
            key,
            document_element,
        } = self;
        let root_id = match root_slot {
            Slot::Object(id) => id,
            Slot::Direct => doc.new_object_id(),
        };
        let base = doc.max_id + 1;
        let id_of = |index: usize| (base + index as u32, 0);
        let top_parent = document_element.as_ref().map_or(root_id, |(id, _)| *id);
        let mut top = Vec::new();
        for (index, element) in structure.elements.iter().enumerate() {
            let mut dict = Dictionary::new();
            dict.set("Type", Object::Name(b"StructElem".to_vec()));
            dict.set("S", Object::Name(element.role.as_bytes().to_vec()));
            dict.set(
                "P",
                Object::Reference(element.parent.map_or(top_parent, id_of)),
            );
            dict.set("Pg", Object::Reference(page_id));
            let kids = element
                .kids
                .iter()
                .map(|kid| match kid {
                    CanvasKid::Content(mcid) => Object::Dictionary(dictionary! {
                        "Type" => "MCR",
                        "Pg" => page_id,
                        "Stm" => form_id,
                        "MCID" => i64::from(*mcid),
                    }),
                    CanvasKid::Element(child) => Object::Reference(id_of(*child)),
                })
                .collect();
            dict.set("K", Object::Array(kids));
            for (key, text) in [
                ("Alt", &element.alt),
                ("Lang", &element.lang),
                ("T", &element.title),
                ("ActualText", &element.actual_text),
            ] {
                if let Some(text) = text {
                    dict.set(key, text_string(text));
                }
            }
            if let Some((owner, attribute, value)) = element.attribute {
                dict.set(
                    "A",
                    dictionary! {
                        "O" => Object::Name(owner.as_bytes().to_vec()),
                        attribute => Object::Name(value.as_bytes().to_vec()),
                    },
                );
            }
            if element.parent.is_none() {
                top.push(Object::Reference(id_of(index)));
            }
            doc.objects.insert(id_of(index), Object::Dictionary(dict));
        }
        doc.max_id = base + structure.elements.len() as u32 - 1;

        // The parent tree maps the Form's key to the element of each MCID.
        let owners = Object::Array(
            structure
                .mcids
                .iter()
                .map(|element| Object::Reference(id_of(*element)))
                .collect(),
        );
        let flat = tree.has(b"Nums");
        match tree.get_mut(b"Kids") {
            Ok(Object::Array(kids)) if !flat => {
                let leaf = doc.add_object(dictionary! {
                    "Nums" => Object::Array(vec![Object::Integer(key), owners]),
                    "Limits" => Object::Array(vec![Object::Integer(key), Object::Integer(key)]),
                });
                kids.push(Object::Reference(leaf));
            }
            _ => {
                let mut nums = match tree.get(b"Nums") {
                    Ok(Object::Array(nums)) => nums.clone(),
                    _ => Vec::new(),
                };
                nums.push(Object::Integer(key));
                nums.push(owners);
                tree.set("Nums", Object::Array(nums));
            }
        }
        match tree_slot {
            Slot::Object(id) => {
                doc.objects.insert(id, Object::Dictionary(tree));
            }
            Slot::Direct => root.set("ParentTree", Object::Dictionary(tree)),
        }
        root.set("ParentTreeNextKey", Object::Integer(key + 1));

        // The new top-level elements follow the existing ones.
        let append = |kids: Option<&Object>, top: Vec<Object>| -> Object {
            let mut items = match kids {
                Some(Object::Array(items)) => items.clone(),
                Some(Object::Null) | None => Vec::new(),
                Some(other) => vec![other.clone()],
            };
            items.extend(top);
            Object::Array(items)
        };
        match document_element {
            Some((id, mut element)) => {
                let kids = append(element.get(b"K").ok(), top);
                element.set("K", kids);
                doc.objects.insert(id, Object::Dictionary(element));
            }
            None => {
                let kids = append(root.get(b"K").ok(), top);
                root.set("K", kids);
            }
        }
        match root_slot {
            Slot::Object(id) => {
                doc.objects.insert(id, Object::Dictionary(root));
            }
            Slot::Direct => {
                doc.objects.insert(root_id, Object::Dictionary(root));
                if let Ok(catalog) = doc.catalog_mut() {
                    catalog.set("StructTreeRoot", Object::Reference(root_id));
                }
            }
        }
        if let Some(Object::Stream(form)) = doc.objects.get_mut(&form_id) {
            form.dict.set("StructParents", Object::Integer(key));
        }
    }
}

/// The most elements that one canvas may add.
const MAX_STRUCTURE_ELEMENTS: usize = 65_536;
