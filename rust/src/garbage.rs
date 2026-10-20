//! Merge identical objects and compact object numbers for `garbage=` saves.
//!
//! pymupdf's `garbage` levels are cumulative: 1 removes unreachable objects,
//! 2 also numbers the remaining objects consecutively, 3 also merges
//! identical objects that are not streams, and 4 also merges identical
//! streams. This module provides levels 2 to 4; `prune` provides level 1.

use std::collections::hash_map::DefaultHasher;
use std::collections::{HashMap, HashSet};
use std::hash::Hasher;

use lopdf::{Dictionary, Document, Object, ObjectId};
use pyo3::prelude::*;

use crate::document::PdfError;

/// Keys whose referenced objects have an identity that a reader relies on.
///
/// A page, an annotation, a form field, a structure element, an outline item,
/// an article bead, or an optional-content group is the same object wherever
/// it is named: merging two such objects that happen to be equal would merge
/// two pages, two layers, or two fields into one. Arrays named under these
/// keys are lists that pylopdf edits per owner (`/Annots`, `/K`, `/Kids`), so
/// they keep their identity as well.
const IDENTITY_KEYS: &[&[u8]] = &[
    b"AcroForm",
    b"Annots",
    b"B",
    b"Dests",
    b"Encrypt",
    b"Fields",
    b"First",
    b"IDTree",
    b"IRT",
    b"Info",
    b"K",
    b"Kids",
    b"Last",
    b"Locked",
    b"Names",
    b"Next",
    b"OC",
    b"OCGs",
    b"OCProperties",
    b"OFF",
    b"ON",
    b"Obj",
    b"Order",
    b"Outlines",
    b"P",
    b"Pages",
    b"Parent",
    b"ParentTree",
    b"Pg",
    b"Popup",
    b"Prev",
    b"Root",
    b"StructTreeRoot",
    b"Threads",
];

/// `/Type` values of dictionaries and streams that keep their identity.
const IDENTITY_TYPES: &[&[u8]] = &[
    b"Annot",
    b"Bead",
    b"Catalog",
    b"MCR",
    b"OBJR",
    b"OCG",
    b"OCMD",
    b"ObjStm",
    b"Outlines",
    b"Page",
    b"Pages",
    b"Sig",
    b"StructElem",
    b"StructTreeRoot",
    b"Thread",
    b"XRef",
];

/// Merge every set of equal objects into the one with the smallest ID.
///
/// Two objects are equal when they have the same type, the same values, the
/// same dictionary keys in the same order, and references to objects that
/// are themselves equal; streams also need equal data. Streams merge only
/// with `include_streams`. Objects that a cycle of references reaches,
/// objects that the trailer names, objects named under an identity key, and
/// dictionaries that look like pages, annotations, fields, structure
/// elements, or layers are never merged, but references to them still count
/// when their referrers are compared.
///
/// Every reference to a merged object is retargeted to the kept one and the
/// merged objects are removed. All fallible work happens before the first
/// change, so a refusal leaves the document unchanged. Returns the number of
/// removed objects.
pub(crate) fn merge_identical_objects(
    doc: &mut Document,
    include_streams: bool,
) -> PyResult<usize> {
    let remap = plan_merge(doc, include_streams)?;
    if remap.is_empty() {
        return Ok(0);
    }
    let stack = reserve_rewrite_stack(doc)?;
    rewrite_all(doc, stack, |id| match remap.get(&id) {
        Some(&kept) => Object::Reference(kept),
        None => Object::Reference(id),
    });
    for id in remap.keys() {
        doc.objects.remove(id);
    }
    Ok(remap.len())
}

/// Number every object consecutively from 1, in ascending ID order, with
/// generation 0, and return the map from old to new IDs.
///
/// A reference to a missing object becomes null, as ISO 32000-2 §7.3.10
/// reads it, because its number could otherwise name an object after
/// compaction. All fallible work happens before the first change.
pub(crate) fn compact_object_numbers(doc: &mut Document) -> PyResult<HashMap<ObjectId, ObjectId>> {
    let mut renumbered = HashMap::new();
    renumbered
        .try_reserve(doc.objects.len())
        .map_err(|error| allocation_error("compaction map", error))?;
    for (number, &id) in (1u32..).zip(doc.objects.keys()) {
        renumbered.insert(id, (number, 0));
    }
    let max_id = u32::try_from(doc.objects.len())
        .map_err(|_| PdfError::new_err("PDF object ID limit reached"))?;
    let stack = reserve_rewrite_stack(doc)?;
    rewrite_all(doc, stack, |id| {
        renumbered
            .get(&id)
            .map_or(Object::Null, |&new_id| Object::Reference(new_id))
    });
    let objects = std::mem::take(&mut doc.objects);
    for (id, object) in objects {
        let new_id = renumbered.get(&id).copied().unwrap_or(id);
        doc.objects.insert(new_id, object);
    }
    doc.max_id = max_id;
    Ok(renumbered)
}

/// Decide which objects merge, mapping each merged ID to the kept ID.
fn plan_merge(doc: &Document, include_streams: bool) -> PyResult<HashMap<ObjectId, ObjectId>> {
    let ids: Vec<ObjectId> = collect_exact(
        doc.objects.keys().copied(),
        doc.objects.len(),
        "object list",
    )?;
    let mut index = HashMap::new();
    index
        .try_reserve(ids.len())
        .map_err(|error| allocation_error("object index", error))?;
    for (position, &id) in ids.iter().enumerate() {
        index.insert(id, position);
    }
    let graph = ReferenceGraph::build(doc, &ids, &index)?;
    let pinned = pinned_objects(doc)?;

    // Class numbers identify equality classes; an object that cannot merge
    // gets a class of its own.
    let mut class_of = vec_filled(ids.len(), usize::MAX, "equality classes")?;
    let mut class_members: Vec<usize> = Vec::new();
    let mut buckets: HashMap<u64, Vec<usize>> = HashMap::new();
    let mut scratch = Vec::new();

    let components = graph.components()?;
    for component in components.iter() {
        let node = component.first;
        let id = ids[node];
        let object = &doc.objects[&id];
        let mergeable = component.len == 1
            && !graph.has_self_reference(node)
            && !pinned.contains(&id)
            && is_mergeable(object, include_streams);
        if !mergeable {
            for &member in &components.members[component.start..component.start + component.len] {
                class_of[member] = push_class(&mut class_members, member)?;
            }
            continue;
        }
        let hash = hash_object(object, &index, &class_of, &mut scratch)?;
        let bucket = buckets.entry(hash).or_default();
        let mut found = None;
        for &class in bucket.iter() {
            let other = &doc.objects[&ids[class_members[class]]];
            if objects_equal(object, other, &index, &class_of, &mut scratch)? {
                found = Some(class);
                break;
            }
        }
        class_of[node] = match found {
            Some(class) => class,
            None => {
                let class = push_class(&mut class_members, node)?;
                bucket
                    .try_reserve(1)
                    .map_err(|error| allocation_error("merge bucket", error))?;
                bucket.push(class);
                class
            }
        };
    }

    // Keep the smallest ID of each class: `ids` ascends, so the first member
    // met in this scan is the smallest.
    let mut kept = vec_filled(class_members.len(), usize::MAX, "kept objects")?;
    let mut remap = HashMap::new();
    for (node, &class) in class_of.iter().enumerate() {
        if kept[class] == usize::MAX {
            kept[class] = node;
        } else {
            remap
                .try_reserve(1)
                .map_err(|error| allocation_error("merge map", error))?;
            remap.insert(ids[node], ids[kept[class]]);
        }
    }
    Ok(remap)
}

fn push_class(class_members: &mut Vec<usize>, node: usize) -> PyResult<usize> {
    class_members
        .try_reserve(1)
        .map_err(|error| allocation_error("equality classes", error))?;
    class_members.push(node);
    Ok(class_members.len() - 1)
}

/// Return whether an object may merge with an equal one by its own shape.
fn is_mergeable(object: &Object, include_streams: bool) -> bool {
    match object {
        Object::Stream(stream) => include_streams && !has_identity(&stream.dict),
        Object::Dictionary(dict) => !has_identity(dict),
        _ => true,
    }
}

/// Return whether a dictionary describes an object with an identity.
fn has_identity(dict: &Dictionary) -> bool {
    if let Ok(Object::Name(kind)) = dict.get(b"Type")
        && IDENTITY_TYPES.contains(&kind.as_slice())
    {
        return true;
    }
    // Pages, page-tree nodes, fields, outline items, annotations, and
    // structure elements name their parent or their kids; annotations
    // without `/Type` still carry `/Subtype` and `/Rect`, and fields carry
    // `/FT` or `/T`.
    [b"Parent".as_slice(), b"Kids", b"P", b"FT", b"T"]
        .iter()
        .any(|key| dict.has(key))
        || (dict.has(b"Subtype") && dict.has(b"Rect"))
}

/// Collect the objects that the trailer names or that some object names
/// under an identity key.
fn pinned_objects(doc: &Document) -> PyResult<HashSet<ObjectId>> {
    let mut pinned = HashSet::new();
    let mut pending: Vec<(&Object, bool)> = Vec::new();
    for (_, value) in doc.trailer.iter() {
        push_pending(&mut pending, value, true)?;
    }
    for object in doc.objects.values() {
        push_pending(&mut pending, object, false)?;
    }
    while let Some((object, pin)) = pending.pop() {
        match object {
            Object::Reference(id) => {
                if pin {
                    pinned
                        .try_reserve(1)
                        .map_err(|error| allocation_error("pinned objects", error))?;
                    pinned.insert(*id);
                }
            }
            Object::Array(items) => {
                for item in items {
                    push_pending(&mut pending, item, pin)?;
                }
            }
            Object::Dictionary(dict) => queue_entries(&mut pending, dict, pin)?,
            Object::Stream(stream) => queue_entries(&mut pending, &stream.dict, pin)?,
            _ => {}
        }
    }
    Ok(pinned)
}

fn queue_entries<'a>(
    pending: &mut Vec<(&'a Object, bool)>,
    dict: &'a Dictionary,
    pin: bool,
) -> PyResult<()> {
    for (key, value) in dict.iter() {
        let pin = pin || IDENTITY_KEYS.contains(&key.as_slice());
        push_pending(pending, value, pin)?;
    }
    Ok(())
}

fn push_pending<'a>(
    pending: &mut Vec<(&'a Object, bool)>,
    object: &'a Object,
    pin: bool,
) -> PyResult<()> {
    if matches!(
        object,
        Object::Reference(_) | Object::Array(_) | Object::Dictionary(_) | Object::Stream(_)
    ) {
        pending
            .try_reserve(1)
            .map_err(|error| allocation_error("pinned-object stack", error))?;
        pending.push((object, pin));
    }
    Ok(())
}

/// References between existing objects in compressed sparse row form.
struct ReferenceGraph {
    offsets: Vec<usize>,
    targets: Vec<usize>,
}

/// Strongly connected components, children before the components that
/// reference them.
struct Components {
    members: Vec<usize>,
    list: Vec<Component>,
}

#[derive(Clone, Copy)]
struct Component {
    start: usize,
    len: usize,
    first: usize,
}

impl Components {
    fn iter(&self) -> impl Iterator<Item = &Component> {
        self.list.iter()
    }
}

impl ReferenceGraph {
    fn build(doc: &Document, ids: &[ObjectId], index: &HashMap<ObjectId, usize>) -> PyResult<Self> {
        let mut offsets = Vec::new();
        offsets
            .try_reserve_exact(ids.len() + 1)
            .map_err(|error| allocation_error("reference graph", error))?;
        let mut targets = Vec::new();
        let mut stack: Vec<&Object> = Vec::new();
        offsets.push(0);
        for id in ids {
            push_container(&mut stack, &doc.objects[id])?;
            while let Some(object) = stack.pop() {
                match object {
                    Object::Reference(target) => {
                        if let Some(&node) = index.get(target) {
                            targets
                                .try_reserve(1)
                                .map_err(|error| allocation_error("reference graph", error))?;
                            targets.push(node);
                        }
                    }
                    Object::Array(items) => {
                        for item in items {
                            push_container(&mut stack, item)?;
                        }
                    }
                    Object::Dictionary(dict) => {
                        for (_, value) in dict.iter() {
                            push_container(&mut stack, value)?;
                        }
                    }
                    Object::Stream(stream) => {
                        for (_, value) in stream.dict.iter() {
                            push_container(&mut stack, value)?;
                        }
                    }
                    _ => {}
                }
            }
            offsets.push(targets.len());
        }
        Ok(Self { offsets, targets })
    }

    fn edges(&self, node: usize) -> &[usize] {
        &self.targets[self.offsets[node]..self.offsets[node + 1]]
    }

    fn has_self_reference(&self, node: usize) -> bool {
        self.edges(node).contains(&node)
    }

    /// Tarjan's algorithm, run iteratively from each node in ascending order.
    fn components(&self) -> PyResult<Components> {
        let count = self.offsets.len() - 1;
        let mut order = vec_filled(count, usize::MAX, "component order")?;
        let mut low = vec_filled(count, 0usize, "component links")?;
        let mut on_stack = vec_filled(count, false, "component stack flags")?;
        let mut stack: Vec<usize> = Vec::new();
        let mut frames: Vec<(usize, usize)> = Vec::new();
        let mut members = Vec::new();
        members
            .try_reserve_exact(count)
            .map_err(|error| allocation_error("component members", error))?;
        let mut list = Vec::new();
        let mut next = 0usize;
        for root in 0..count {
            if order[root] != usize::MAX {
                continue;
            }
            frames
                .try_reserve(1)
                .map_err(|error| allocation_error("component frames", error))?;
            frames.push((root, 0));
            while let Some(&mut (node, ref mut edge)) = frames.last_mut() {
                if *edge == 0 && order[node] == usize::MAX {
                    order[node] = next;
                    low[node] = next;
                    next += 1;
                    stack
                        .try_reserve(1)
                        .map_err(|error| allocation_error("component stack", error))?;
                    stack.push(node);
                    on_stack[node] = true;
                }
                let edges = self.edges(node);
                if let Some(&target) = edges.get(*edge) {
                    *edge += 1;
                    if order[target] == usize::MAX {
                        frames
                            .try_reserve(1)
                            .map_err(|error| allocation_error("component frames", error))?;
                        frames.push((target, 0));
                    } else if on_stack[target] {
                        low[node] = low[node].min(order[target]);
                    }
                    continue;
                }
                frames.pop();
                if let Some(&(parent, _)) = frames.last() {
                    low[parent] = low[parent].min(low[node]);
                }
                if low[node] == order[node] {
                    let start = members.len();
                    loop {
                        let member = stack.pop().expect("component member on stack");
                        on_stack[member] = false;
                        members.push(member);
                        if member == node {
                            break;
                        }
                    }
                    list.try_reserve(1)
                        .map_err(|error| allocation_error("component list", error))?;
                    list.push(Component {
                        start,
                        len: members.len() - start,
                        first: node,
                    });
                }
            }
        }
        Ok(Components { members, list })
    }
}

fn push_container<'a>(stack: &mut Vec<&'a Object>, object: &'a Object) -> PyResult<()> {
    if matches!(
        object,
        Object::Reference(_) | Object::Array(_) | Object::Dictionary(_) | Object::Stream(_)
    ) {
        stack
            .try_reserve(1)
            .map_err(|error| allocation_error("object walk stack", error))?;
        stack.push(object);
    }
    Ok(())
}

/// Hash an object with references replaced by the equality class of their
/// target, or by their ID when the target is missing.
fn hash_object<'a>(
    object: &'a Object,
    index: &HashMap<ObjectId, usize>,
    class_of: &[usize],
    scratch: &mut Vec<(&'a Object, &'a Object)>,
) -> PyResult<u64> {
    let mut hasher = DefaultHasher::new();
    scratch.clear();
    reserve_one(scratch)?;
    scratch.push((object, object));
    while let Some((object, _)) = scratch.pop() {
        match object {
            Object::Null => hasher.write_u8(0),
            Object::Boolean(value) => {
                hasher.write_u8(1);
                hasher.write_u8(u8::from(*value));
            }
            Object::Integer(value) => {
                hasher.write_u8(2);
                hasher.write_i64(*value);
            }
            Object::Real(value) => {
                hasher.write_u8(3);
                hasher.write_u32(value.to_bits());
            }
            Object::Name(name) => {
                hasher.write_u8(4);
                write_bytes(&mut hasher, name);
            }
            Object::String(text, format) => {
                hasher.write_u8(5);
                hasher.write_u8(*format as u8);
                write_bytes(&mut hasher, text);
            }
            Object::Array(items) => {
                hasher.write_u8(6);
                hasher.write_usize(items.len());
                for item in items.iter().rev() {
                    reserve_one(scratch)?;
                    scratch.push((item, item));
                }
            }
            Object::Dictionary(dict) => {
                hasher.write_u8(7);
                hash_dictionary(&mut hasher, dict, scratch)?;
            }
            Object::Stream(stream) => {
                hasher.write_u8(8);
                hasher.write_u8(u8::from(stream.allows_compression));
                write_bytes(&mut hasher, &stream.content);
                hash_dictionary(&mut hasher, &stream.dict, scratch)?;
            }
            Object::Reference(id) => match index.get(id) {
                Some(&node) => {
                    hasher.write_u8(9);
                    hasher.write_usize(class_of[node]);
                }
                None => {
                    hasher.write_u8(10);
                    hasher.write_u32(id.0);
                    hasher.write_u16(id.1);
                }
            },
        }
    }
    Ok(hasher.finish())
}

fn hash_dictionary<'a>(
    hasher: &mut DefaultHasher,
    dict: &'a Dictionary,
    scratch: &mut Vec<(&'a Object, &'a Object)>,
) -> PyResult<()> {
    hasher.write_usize(dict.len());
    // Keys are hashed in order now; values are pushed in reverse so that
    // they are hashed in order after the keys.
    for (key, _) in dict.iter() {
        write_bytes(hasher, key);
    }
    for (_, value) in dict.iter().rev() {
        reserve_one(scratch)?;
        scratch.push((value, value));
    }
    Ok(())
}

fn write_bytes(hasher: &mut DefaultHasher, bytes: &[u8]) {
    hasher.write_usize(bytes.len());
    hasher.write(bytes);
}

/// Compare two objects as [`hash_object`] hashes them.
fn objects_equal<'a>(
    left: &'a Object,
    right: &'a Object,
    index: &HashMap<ObjectId, usize>,
    class_of: &[usize],
    scratch: &mut Vec<(&'a Object, &'a Object)>,
) -> PyResult<bool> {
    scratch.clear();
    reserve_one(scratch)?;
    scratch.push((left, right));
    while let Some(pair) = scratch.pop() {
        let equal = match pair {
            (Object::Null, Object::Null) => true,
            (Object::Boolean(a), Object::Boolean(b)) => a == b,
            (Object::Integer(a), Object::Integer(b)) => a == b,
            (Object::Real(a), Object::Real(b)) => a.to_bits() == b.to_bits(),
            (Object::Name(a), Object::Name(b)) => a == b,
            (Object::String(a, fa), Object::String(b, fb)) => a == b && fa == fb,
            (Object::Array(a), Object::Array(b)) => {
                if a.len() != b.len() {
                    return Ok(false);
                }
                for pair in a.iter().zip(b) {
                    reserve_one(scratch)?;
                    scratch.push(pair);
                }
                true
            }
            (Object::Dictionary(a), Object::Dictionary(b)) => queue_dictionaries(a, b, scratch)?,
            (Object::Stream(a), Object::Stream(b)) => {
                a.allows_compression == b.allows_compression
                    && a.content == b.content
                    && queue_dictionaries(&a.dict, &b.dict, scratch)?
            }
            (Object::Reference(a), Object::Reference(b)) => match (index.get(a), index.get(b)) {
                (Some(&x), Some(&y)) => class_of[x] == class_of[y],
                (None, None) => a == b,
                _ => false,
            },
            _ => false,
        };
        if !equal {
            return Ok(false);
        }
    }
    Ok(true)
}

fn queue_dictionaries<'a>(
    left: &'a Dictionary,
    right: &'a Dictionary,
    scratch: &mut Vec<(&'a Object, &'a Object)>,
) -> PyResult<bool> {
    if left.len() != right.len() {
        return Ok(false);
    }
    for ((left_key, left_value), (right_key, right_value)) in left.iter().zip(right.iter()) {
        if left_key != right_key {
            return Ok(false);
        }
        reserve_one(scratch)?;
        scratch.push((left_value, right_value));
    }
    Ok(true)
}

fn reserve_one<T>(stack: &mut Vec<T>) -> PyResult<()> {
    stack
        .try_reserve(1)
        .map_err(|error| allocation_error("object comparison stack", error))
}

/// Reserve a stack large enough to rewrite any one object without growing.
///
/// Rewriting pushes each container of one object at most once, so the
/// largest container count of the trailer or of any object bounds it.
fn reserve_rewrite_stack<'a>(doc: &Document) -> PyResult<Vec<&'a mut Object>> {
    let mut largest = count_containers(doc.trailer.iter().map(|(_, value)| value))?;
    for object in doc.objects.values() {
        largest = largest.max(count_containers(std::iter::once(object))?);
    }
    let mut stack = Vec::new();
    stack
        .try_reserve_exact(largest)
        .map_err(|error| allocation_error("reference rewrite stack", error))?;
    Ok(stack)
}

fn count_containers<'a>(roots: impl Iterator<Item = &'a Object>) -> PyResult<usize> {
    let mut count = 0usize;
    let mut stack: Vec<&Object> = Vec::new();
    for root in roots {
        push_container(&mut stack, root)?;
        while let Some(object) = stack.pop() {
            match object {
                Object::Array(items) => {
                    count += 1;
                    for item in items {
                        push_container(&mut stack, item)?;
                    }
                }
                Object::Dictionary(dict) => {
                    count += 1;
                    for (_, value) in dict.iter() {
                        push_container(&mut stack, value)?;
                    }
                }
                Object::Stream(stream) => {
                    count += 1;
                    for (_, value) in stream.dict.iter() {
                        push_container(&mut stack, value)?;
                    }
                }
                _ => {}
            }
        }
    }
    Ok(count)
}

/// Replace every reference in the trailer and in every object with
/// `replace(id)`, using a stack that [`reserve_rewrite_stack`] sized.
fn rewrite_all(doc: &mut Document, stack: Vec<&mut Object>, replace: impl Fn(ObjectId) -> Object) {
    let mut stack = recycle(stack);
    for (_, value) in doc.trailer.iter_mut() {
        rewrite_one(value, &replace, &mut stack);
    }
    for object in doc.objects.values_mut() {
        let mut next = recycle(stack);
        rewrite_one(object, &replace, &mut next);
        stack = recycle(next);
    }
}

/// Reuse a stack's allocation for references of another lifetime.
///
/// Collecting an emptied vector through `map` into one of a type with the
/// same size and alignment reuses its allocation.
fn recycle<'b>(mut stack: Vec<&mut Object>) -> Vec<&'b mut Object> {
    stack.clear();
    stack.into_iter().map(|_| unreachable!()).collect()
}

fn rewrite_one<'a>(
    object: &'a mut Object,
    replace: &impl Fn(ObjectId) -> Object,
    stack: &mut Vec<&'a mut Object>,
) {
    rewrite_or_queue(object, replace, stack);
    while let Some(container) = stack.pop() {
        match container {
            Object::Array(items) => {
                for item in items {
                    rewrite_or_queue(item, replace, stack);
                }
            }
            Object::Dictionary(dict) => {
                for (_, value) in dict.iter_mut() {
                    rewrite_or_queue(value, replace, stack);
                }
            }
            Object::Stream(stream) => {
                for (_, value) in stream.dict.iter_mut() {
                    rewrite_or_queue(value, replace, stack);
                }
            }
            _ => {}
        }
    }
}

fn rewrite_or_queue<'a>(
    object: &'a mut Object,
    replace: &impl Fn(ObjectId) -> Object,
    stack: &mut Vec<&'a mut Object>,
) {
    match object {
        Object::Reference(id) => *object = replace(*id),
        Object::Array(_) | Object::Dictionary(_) | Object::Stream(_) => {
            // `reserve_rewrite_stack` sized the stack for this push.
            stack.push(object);
        }
        _ => {}
    }
}

fn collect_exact<T>(items: impl Iterator<Item = T>, len: usize, context: &str) -> PyResult<Vec<T>> {
    let mut collected = Vec::new();
    collected
        .try_reserve_exact(len)
        .map_err(|error| allocation_error(context, error))?;
    collected.extend(items);
    Ok(collected)
}

fn vec_filled<T: Clone>(len: usize, value: T, context: &str) -> PyResult<Vec<T>> {
    let mut filled = Vec::new();
    filled
        .try_reserve_exact(len)
        .map_err(|error| allocation_error(context, error))?;
    filled.resize(len, value);
    Ok(filled)
}

fn allocation_error(context: &str, error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!("failed to allocate {context}: {error}"))
}
