//! Prepare a loaded document's object numbers for new objects.

use lopdf::{Document, Object, ObjectId};
use pyo3::prelude::*;

use crate::document::PdfError;

/// The highest object number a loaded document keeps.
///
/// ISO 32000-1 Table C.1 gives 8,388,607 as the architectural limit on
/// indirect objects in a PDF file, and MuPDF rejects higher object numbers.
/// Numbers up to it leave allocation more than four billion numbers, which
/// memory runs out long before, and bound lopdf's cross-reference writer, which
/// visits every number up to the highest object on each save.
const MAX_OBJECT_NUMBER: u32 = 8_388_607;

/// Prepare a loaded document for new objects and return whether any object
/// changed its number.
///
/// lopdf allocates new objects above `Document::max_id`. A malformed file can
/// name a higher number that no object has; ISO 32000-2 §7.3.10 reads such a
/// reference as null, but once an import or edit allocates the number, the
/// reference resolves to that unrelated new object. Every reference pylopdf
/// creates later names an allocated object or comes from a renumbered import,
/// so nulling these once at load keeps the null reading for the document's
/// lifetime. References at or below `max_id` cannot alias, because numbers
/// are only allocated above it, so a document without higher references keeps
/// its objects unchanged.
///
/// lopdf 0.45 already keeps `max_id` at least at the highest loaded object,
/// which can exceed every cross-reference entry when an object header or an
/// object-stream member disagrees with the table. That is repeated here
/// because a lower threshold would null references to live objects. The
/// threshold is never raised to a referenced number, because object numbers
/// must keep their headroom.
///
/// A `max_id` above `MAX_OBJECT_NUMBER` comes from an object or an in-use
/// cross-reference entry numbered past the limit. lopdf increments `max_id`
/// without an overflow check, so in release builds allocation would wrap to
/// object 0 and then replace objects 1, 2, and so on, often including the
/// catalog; every save would also visit up to four billion numbers. Objects
/// above the limit therefore take consecutive numbers after the highest object
/// at or below it, in ascending ID order with their generations kept, and
/// `max_id` ends at the last of them. A reference above that highest object
/// follows its object to the new number, or becomes null when it names none.
/// Numbers at or below it stay unchanged, and an over-limit cross-reference
/// entry without an object only lowers `max_id`.
///
/// References are rewritten in the trailer, arrays, dictionaries, and stream
/// dictionaries, never in stream content, with an explicit fallibly grown
/// stack. Refusal raises `PdfError` and may leave `doc` partly rewritten, so
/// callers discard it.
pub(crate) fn normalize_object_numbers(doc: &mut Document) -> PyResult<bool> {
    if let Some(&(highest, _)) = doc.objects.keys().next_back() {
        doc.max_id = doc.max_id.max(highest);
    }
    if doc.max_id <= MAX_OBJECT_NUMBER {
        let max_id = doc.max_id;
        rewrite_unallocated_references(doc, max_id, &[])?;
        return Ok(false);
    }

    let highest_kept = doc
        .objects
        .range(..=(MAX_OBJECT_NUMBER, u16::MAX))
        .next_back()
        .map_or(0, |(&(number, _), _)| number);
    let over_limit = doc.objects.split_off(&(MAX_OBJECT_NUMBER + 1, 0));
    let max_id = u32::try_from(over_limit.len())
        .ok()
        .and_then(|count| highest_kept.checked_add(count))
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    // Old IDs in ascending order, which `split_off` preserves, so references
    // find their new numbers by binary search.
    let mut moved = Vec::new();
    moved
        .try_reserve_exact(over_limit.len())
        .map_err(|error| allocation_error("object renumbering table", error))?;
    let mut renumbered = false;
    let mut number = highest_kept;
    for (old_id, object) in over_limit {
        // `max_id` bounds every number assigned here.
        number += 1;
        renumbered |= number != old_id.0;
        moved.push((old_id, number));
        doc.objects.insert((number, old_id.1), object);
    }
    doc.max_id = max_id;
    rewrite_unallocated_references(doc, highest_kept, &moved)?;
    Ok(renumbered)
}

/// Rewrite every reference numbered above `threshold`: one to a `moved` old
/// ID takes its new number and keeps its generation, and any other becomes
/// null.
fn rewrite_unallocated_references(
    doc: &mut Document,
    threshold: u32,
    moved: &[(ObjectId, u32)],
) -> PyResult<()> {
    let mut pending = Vec::new();
    for (_, value) in doc.trailer.iter_mut() {
        rewrite_references(value, threshold, moved, &mut pending)?;
    }
    let mut pending = Vec::new();
    for object in doc.objects.values_mut() {
        rewrite_references(object, threshold, moved, &mut pending)?;
    }
    Ok(())
}

/// Rewrite the unallocated references in one value, with `pending` as the
/// container stack.
fn rewrite_references<'a>(
    object: &'a mut Object,
    threshold: u32,
    moved: &[(ObjectId, u32)],
    pending: &mut Vec<&'a mut Object>,
) -> PyResult<()> {
    rewrite_or_queue(object, threshold, moved, pending)?;
    while let Some(container) = pending.pop() {
        match container {
            Object::Array(items) => {
                for item in items {
                    rewrite_or_queue(item, threshold, moved, pending)?;
                }
            }
            Object::Dictionary(dict) => {
                for (_, value) in dict.iter_mut() {
                    rewrite_or_queue(value, threshold, moved, pending)?;
                }
            }
            Object::Stream(stream) => {
                for (_, value) in stream.dict.iter_mut() {
                    rewrite_or_queue(value, threshold, moved, pending)?;
                }
            }
            _ => {}
        }
    }
    Ok(())
}

/// Rewrite an unallocated reference in place or queue a container, so scalars
/// cost no stack space. Any generation counts as unallocated, because
/// allocation reuses the number with generation 0 and readers may not compare
/// generations, but only the exact ID of a moved object follows it.
fn rewrite_or_queue<'a>(
    object: &'a mut Object,
    threshold: u32,
    moved: &[(ObjectId, u32)],
    pending: &mut Vec<&'a mut Object>,
) -> PyResult<()> {
    match object {
        Object::Reference(id) => {
            let id = *id;
            if id.0 > threshold {
                *object = match moved.binary_search_by_key(&id, |&(old_id, _)| old_id) {
                    Ok(index) => Object::Reference((moved[index].1, id.1)),
                    Err(_) => Object::Null,
                };
            }
        }
        Object::Array(_) | Object::Dictionary(_) | Object::Stream(_) => {
            pending
                .try_reserve(1)
                .map_err(|error| allocation_error("reference-rewriting stack", error))?;
            pending.push(object);
        }
        _ => {}
    }
    Ok(())
}

fn allocation_error(context: &str, error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!("failed to allocate {context}: {error}"))
}
