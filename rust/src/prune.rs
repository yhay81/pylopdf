//! Remove objects unreachable from the trailer in time linear in object count.

use std::collections::HashSet;

use lopdf::{Document, Object, ObjectId};
use pyo3::prelude::*;

use crate::document::PdfError;

/// Remove every object that the trailer cannot reach.
///
/// This keeps `lopdf::Document::prune_objects` semantics: references are
/// followed through the trailer, arrays, dictionaries, stream dictionaries,
/// and reference-valued indirect objects, never through stream content.
/// lopdf records visited IDs in a `Vec` and scans it for every reference and
/// every object, which is quadratic in object count. Here a hash set records
/// each reached object once and an explicit stack replaces recursion.
/// Allocation refusal raises `PdfError` before any object is removed.
pub(crate) fn prune_unreachable_objects(doc: &mut Document) -> PyResult<()> {
    let reachable = trailer_reachable_object_ids(doc)?;
    if reachable.len() < doc.objects.len() {
        doc.objects.retain(|id, _| reachable.contains(id));
    }
    Ok(())
}

/// The `max_id` that a save should write: the highest object, unless
/// something names a higher number.
///
/// A cross-reference table lists every number up to `max_id`, and its
/// `/Size` is `max_id + 1`, so objects removed from the top would otherwise
/// leave free entries above the highest object, which qpdf reports. The
/// document keeps its own `max_id`, so new objects never take the number of
/// a removed one, which an incremental save's earlier revision may still
/// use, for example for an object stream that nothing references.
pub(crate) fn output_max_id(doc: &Document) -> u32 {
    let highest = doc.objects.keys().next_back().map_or(0, |id| id.0);
    if doc.max_id <= highest {
        return doc.max_id;
    }
    let mut stack: Vec<&Object> = doc.trailer.iter().map(|(_, value)| value).collect();
    for object in doc.objects.values() {
        stack.push(object);
        while let Some(object) = stack.pop() {
            match object {
                Object::Reference((number, _)) if *number > highest => return doc.max_id,
                Object::Array(items) => stack.extend(items.iter()),
                Object::Dictionary(dict) => stack.extend(dict.iter().map(|(_, value)| value)),
                Object::Stream(stream) => stack.extend(stream.dict.iter().map(|(_, value)| value)),
                _ => {}
            }
        }
    }
    highest
}

/// Collect the IDs of the existing objects that the trailer reaches, the
/// objects that [`prune_unreachable_objects`] keeps.
pub(crate) fn trailer_reachable_object_ids(doc: &Document) -> PyResult<HashSet<ObjectId>> {
    reachable_object_ids(doc, doc.trailer.iter().map(|(_, value)| value), |_| true)
}

/// Collect the IDs of existing objects reachable from `roots`.
///
/// References are followed as in [`prune_unreachable_objects`]. An object
/// that `admits` rejects is neither recorded nor traversed, as if absent.
pub(crate) fn reachable_object_ids<'a>(
    doc: &'a Document,
    roots: impl IntoIterator<Item = &'a Object>,
    admits: impl Fn(&Object) -> bool,
) -> PyResult<HashSet<ObjectId>> {
    // Only existing objects are recorded, so this reservation covers every
    // insertion during the walk.
    let mut reachable = HashSet::new();
    reachable
        .try_reserve(doc.objects.len())
        .map_err(|error| allocation_error("reachable object set", error))?;
    let mut pending = Vec::new();
    for value in roots {
        queue_reachable(doc, value, &admits, &mut reachable, &mut pending)?;
    }
    while let Some(container) = pending.pop() {
        match container {
            Object::Array(items) => {
                for item in items {
                    queue_reachable(doc, item, &admits, &mut reachable, &mut pending)?;
                }
            }
            Object::Dictionary(dict) => {
                for (_, value) in dict.iter() {
                    queue_reachable(doc, value, &admits, &mut reachable, &mut pending)?;
                }
            }
            Object::Stream(stream) => {
                for (_, value) in stream.dict.iter() {
                    queue_reachable(doc, value, &admits, &mut reachable, &mut pending)?;
                }
            }
            _ => {}
        }
    }
    Ok(reachable)
}

/// Record objects newly reached along one reference chain and queue the first
/// container met, so scalars and repeated references cost no stack space.
fn queue_reachable<'a>(
    doc: &'a Document,
    mut object: &'a Object,
    admits: &impl Fn(&Object) -> bool,
    reachable: &mut HashSet<ObjectId>,
    pending: &mut Vec<&'a Object>,
) -> PyResult<()> {
    loop {
        match object {
            Object::Reference(id) => {
                if reachable.contains(id) {
                    return Ok(());
                }
                let Some(target) = doc.objects.get(id) else {
                    return Ok(());
                };
                if !admits(target) {
                    return Ok(());
                }
                reachable.insert(*id);
                object = target;
            }
            Object::Array(_) | Object::Dictionary(_) | Object::Stream(_) => {
                pending
                    .try_reserve(1)
                    .map_err(|error| allocation_error("reachability stack", error))?;
                pending.push(object);
                return Ok(());
            }
            _ => return Ok(()),
        }
    }
}

fn allocation_error(context: &str, error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!("failed to allocate {context}: {error}"))
}
