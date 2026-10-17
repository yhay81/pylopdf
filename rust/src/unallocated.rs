//! Keep references to unallocated object numbers from aliasing new objects.

use lopdf::{Document, Object};
use pyo3::prelude::*;

use crate::document::PdfError;

/// Raise `max_id` over every loaded object and replace each reference to a
/// higher number with null.
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
/// threshold is never raised to a referenced number: object numbers must keep
/// their u32 headroom, and once new objects take such numbers, lopdf's
/// cross-reference writer loops over every number up to the highest object.
///
/// References are rewritten in the trailer, arrays, dictionaries, and stream
/// dictionaries, never in stream content, with an explicit fallibly grown
/// stack. Refusal raises `PdfError` and may leave `doc` partly rewritten, so
/// callers discard it.
pub(crate) fn null_unallocated_references(doc: &mut Document) -> PyResult<()> {
    if let Some(&(highest, _)) = doc.objects.keys().next_back() {
        doc.max_id = doc.max_id.max(highest);
    }
    let max_id = doc.max_id;
    let mut pending = Vec::new();
    for (_, value) in doc.trailer.iter_mut() {
        null_references(value, max_id, &mut pending)?;
    }
    let mut pending = Vec::new();
    for object in doc.objects.values_mut() {
        null_references(object, max_id, &mut pending)?;
    }
    Ok(())
}

/// Null the unallocated references in one value, with `pending` as the
/// container stack.
fn null_references<'a>(
    object: &'a mut Object,
    max_id: u32,
    pending: &mut Vec<&'a mut Object>,
) -> PyResult<()> {
    null_or_queue(object, max_id, pending)?;
    while let Some(container) = pending.pop() {
        match container {
            Object::Array(items) => {
                for item in items {
                    null_or_queue(item, max_id, pending)?;
                }
            }
            Object::Dictionary(dict) => {
                for (_, value) in dict.iter_mut() {
                    null_or_queue(value, max_id, pending)?;
                }
            }
            Object::Stream(stream) => {
                for (_, value) in stream.dict.iter_mut() {
                    null_or_queue(value, max_id, pending)?;
                }
            }
            _ => {}
        }
    }
    Ok(())
}

/// Null an unallocated reference in place or queue a container, so scalars
/// cost no stack space. Any generation counts, because allocation reuses the
/// number with generation 0 and readers may not compare generations.
fn null_or_queue<'a>(
    object: &'a mut Object,
    max_id: u32,
    pending: &mut Vec<&'a mut Object>,
) -> PyResult<()> {
    match object {
        Object::Reference((number, _)) => {
            if *number > max_id {
                *object = Object::Null;
            }
        }
        Object::Array(_) | Object::Dictionary(_) | Object::Stream(_) => {
            pending.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate reference-nulling stack: {error}"
                ))
            })?;
            pending.push(object);
        }
        _ => {}
    }
    Ok(())
}
