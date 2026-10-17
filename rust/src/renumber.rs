//! Renumber a document's objects in time linear in object count.

use std::collections::HashMap;
use std::ops::RangeInclusive;

use lopdf::{Document, Object, ObjectId};
use pyo3::prelude::*;

use crate::document::PdfError;

/// Number every object consecutively from `starting_id` and rewrite every
/// reference to match.
///
/// This keeps the numbering of lopdf's `Document::renumber_objects_with`:
/// objects keep their generation and take numbers in ascending ID order,
/// except that page objects trade slots so that `pages`, the page-tree order,
/// receives ascending numbers. Unlike lopdf, a reference to a missing object
/// becomes null, which is how ISO 32000-2 §7.3.10 reads it. A kept number
/// would resolve to whichever target object uses it after import, including
/// objects that the import or a later edit adds. lopdf walks the graph with a
/// `Vec` of visited IDs, twice when pages need reordering, and rescans its
/// bookmark table once per object, which is quadratic. Here a hash map assigns
/// each ID once and an explicit stack rewrites references in the trailer and
/// in every object, including unreachable objects that lopdf left stale.
///
/// `pages` must hold distinct existing objects, as the page-tree walker
/// guarantees, and is rewritten to the new IDs. The bookmark table, which
/// pylopdf fills only inside `set_toc` after clearing it, is left alone.
/// Refusal raises `PdfError` and may leave `doc` partly rewritten, so callers
/// pass a disposable copy.
pub(crate) fn renumber_objects(
    doc: &mut Document,
    starting_id: u32,
    pages: &mut [ObjectId],
) -> PyResult<()> {
    let max_id = u32::try_from(doc.objects.len())
        .ok()
        .and_then(|count| starting_id.checked_sub(1)?.checked_add(count))
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let renumbered = renumbered_ids(doc, starting_id..=max_id, pages)?;

    let mut pending = Vec::new();
    for (_, value) in doc.trailer.iter_mut() {
        rewrite_references(value, &renumbered, &mut pending)?;
    }
    let mut pending = Vec::new();
    for object in doc.objects.values_mut() {
        rewrite_references(object, &renumbered, &mut pending)?;
    }

    // `renumbered_ids` maps every existing ID, so the fallbacks never apply.
    let objects = std::mem::take(&mut doc.objects);
    for (id, object) in objects {
        let new_id = renumbered.get(&id).copied().unwrap_or(id);
        doc.objects.insert(new_id, object);
    }
    for page in pages.iter_mut() {
        *page = renumbered.get(page).copied().unwrap_or(*page);
    }
    doc.max_id = max_id;
    Ok(())
}

/// Map each existing ID to its new ID.
///
/// Ascending IDs take ascending numbers, and the k-th page slot in that order
/// goes to the k-th page in page-tree order.
fn renumbered_ids(
    doc: &Document,
    numbers: RangeInclusive<u32>,
    pages: &[ObjectId],
) -> PyResult<HashMap<ObjectId, ObjectId>> {
    let mut page_slots = Vec::new();
    page_slots
        .try_reserve_exact(pages.len())
        .map_err(|error| allocation_error("renumbered page slots", error))?;
    page_slots.extend_from_slice(pages);
    page_slots.sort_unstable();

    let mut renumbered = HashMap::new();
    renumbered
        .try_reserve(doc.objects.len())
        .map_err(|error| allocation_error("object renumbering map", error))?;
    let mut next_slot = 0;
    for (number, &id) in numbers.zip(doc.objects.keys()) {
        let moved = if page_slots.get(next_slot) == Some(&id) {
            let page = pages[next_slot];
            next_slot += 1;
            page
        } else {
            id
        };
        renumbered.insert(moved, (number, moved.1));
    }
    if next_slot != page_slots.len() {
        return Err(PdfError::new_err(
            "page tree lists a missing or repeated page object",
        ));
    }
    Ok(renumbered)
}

/// Rewrite the references in one value, with `pending` as the container stack.
fn rewrite_references<'a>(
    object: &'a mut Object,
    renumbered: &HashMap<ObjectId, ObjectId>,
    pending: &mut Vec<&'a mut Object>,
) -> PyResult<()> {
    rewrite_or_queue(object, renumbered, pending)?;
    while let Some(container) = pending.pop() {
        match container {
            Object::Array(items) => {
                for item in items {
                    rewrite_or_queue(item, renumbered, pending)?;
                }
            }
            Object::Dictionary(dict) => {
                for (_, value) in dict.iter_mut() {
                    rewrite_or_queue(value, renumbered, pending)?;
                }
            }
            Object::Stream(stream) => {
                for (_, value) in stream.dict.iter_mut() {
                    rewrite_or_queue(value, renumbered, pending)?;
                }
            }
            _ => {}
        }
    }
    Ok(())
}

/// Rewrite a reference in place or queue a container, so scalars cost no
/// stack space. `renumbered` maps every existing ID, so a reference it lacks
/// names a missing object and becomes null. Stream content is never parsed.
fn rewrite_or_queue<'a>(
    object: &'a mut Object,
    renumbered: &HashMap<ObjectId, ObjectId>,
    pending: &mut Vec<&'a mut Object>,
) -> PyResult<()> {
    match object {
        Object::Reference(id) => {
            *object = renumbered
                .get(id)
                .map_or(Object::Null, |&new_id| Object::Reference(new_id));
        }
        Object::Array(_) | Object::Dictionary(_) | Object::Stream(_) => {
            pending
                .try_reserve(1)
                .map_err(|error| allocation_error("renumbering stack", error))?;
            pending.push(object);
        }
        _ => {}
    }
    Ok(())
}

fn allocation_error(context: &str, error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!("failed to allocate {context}: {error}"))
}
