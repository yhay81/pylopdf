//! Keep what hayro must read to open a rendering copy within the nesting it
//! reads.
//!
//! hayro-syntax 0.8 reads no array or dictionary nested 64 levels below the
//! object that holds it (`MAX_OBJECT_NESTING_DEPTH`) and treats the whole
//! object as absent, where hayro 0.7 read it by unbounded recursion. lopdf
//! reads objects nested up to 100 levels, so a trailer, catalog, or root
//! page-tree node nested between the two keeps hayro from opening the
//! document, and such a page drops out of hayro's page list and shifts every
//! later page. A root page-tree node that hayro cannot read is worse: hayro
//! then collects pages by scanning every object, which can list the walker's
//! pages in order but without the attributes they inherit. Such documents
//! render from a flattened rendering copy, in which the entries that nest
//! these dictionaries too deeply are dropped. A page that loses one is
//! refused when an operation interprets it, and the other pages stay usable.
//! Any other object nested that deeply reads as absent, as it does in hayro.

use std::collections::TryReserveError;

use lopdf::{Dictionary, Document, Object, ObjectId};
use pyo3::PyResult;

use crate::document::PdfError;

/// The levels of arrays and dictionaries that hayro reads in one object,
/// counting the object itself: hayro-syntax's `MAX_OBJECT_NESTING_DEPTH`.
pub(crate) const MAX_READ_DEPTH: usize = 64;

/// Whether hayro could not read the trailer, the catalog, or the root
/// page-tree node, because one of them nests deeper than it reads.
pub(crate) fn roots_too_deep(doc: &Document, root: Option<ObjectId>) -> PyResult<bool> {
    if too_deep(&doc.trailer)? {
        return Ok(true);
    }
    let catalog = doc.trailer.get(b"Root").and_then(Object::as_reference).ok();
    for id in catalog.into_iter().chain(root) {
        if let Ok(dictionary) = doc.get_object(id).and_then(Object::as_dict)
            && too_deep(dictionary)?
        {
            return Ok(true);
        }
    }
    Ok(false)
}

/// Drop the entries that nest the trailer, the catalog, the root page-tree
/// node, or a page too deeply for hayro, and return the zero-based indexes
/// in `pages` of the pages that lost one.
///
/// `pages` must be the walker's pages, each with its inherited attributes
/// materialized, so that a page's own dictionary is all hayro reads of it.
pub(crate) fn fit_rendering_copy(
    doc: &mut Document,
    root: ObjectId,
    pages: &[ObjectId],
) -> PyResult<Vec<usize>> {
    drop_deep_entries(&mut doc.trailer)?;
    let catalog = doc.trailer.get(b"Root").and_then(Object::as_reference).ok();
    for id in catalog.into_iter().chain([root]) {
        if let Ok(dictionary) = doc.get_object_mut(id).and_then(Object::as_dict_mut) {
            drop_deep_entries(dictionary)?;
        }
    }
    let mut cut = Vec::new();
    for (index, &page) in pages.iter().enumerate() {
        if let Ok(dictionary) = doc.get_object_mut(page).and_then(Object::as_dict_mut)
            && drop_deep_entries(dictionary)?
        {
            cut.try_reserve(1).map_err(allocation_error)?;
            cut.push(index);
        }
    }
    Ok(cut)
}

/// Whether one of the dictionary's values nests it deeper than hayro reads.
fn too_deep(dictionary: &Dictionary) -> PyResult<bool> {
    for (_, value) in dictionary.iter() {
        if nests_too_deep(value)? {
            return Ok(true);
        }
    }
    Ok(false)
}

/// Drop each entry whose value nests the dictionary deeper than hayro reads;
/// true when one was dropped.
fn drop_deep_entries(dictionary: &mut Dictionary) -> PyResult<bool> {
    let mut deep = Vec::new();
    for (key, value) in dictionary.iter() {
        if nests_too_deep(value)? {
            deep.try_reserve(1).map_err(allocation_error)?;
            deep.push(key.clone());
        }
    }
    for key in &deep {
        dictionary.remove(key);
    }
    Ok(!deep.is_empty())
}

/// Whether a value nests the dictionary that holds it deeper than hayro
/// reads. The dictionary is the first level, so the value may hold one level
/// fewer.
fn nests_too_deep(value: &Object) -> PyResult<bool> {
    let value_limit = MAX_READ_DEPTH - 1;
    Ok(container_depth(value, value_limit)? > value_limit)
}

/// The levels of arrays and dictionaries in `object`, counting the object as
/// the first when it is one, as hayro counts them; a stream counts as its
/// dictionary, and a reference or scalar as no level.
///
/// The walk stops as soon as it finds more than `stop_above` levels.
fn container_depth(object: &Object, stop_above: usize) -> PyResult<usize> {
    enum Pending<'a> {
        Visit(&'a Object, usize),
        ArrayTail(&'a [Object], usize),
    }

    let mut deepest = 0usize;
    let mut pending = Vec::new();
    pending.try_reserve(1).map_err(allocation_error)?;
    pending.push(Pending::Visit(object, 1));
    while let Some(work) = pending.pop() {
        let (current, level) = match work {
            Pending::Visit(current, level) => (current, level),
            Pending::ArrayTail(items, level) => {
                let Some((current, remaining)) = items.split_first() else {
                    continue;
                };
                if !remaining.is_empty() {
                    pending.try_reserve(1).map_err(allocation_error)?;
                    pending.push(Pending::ArrayTail(remaining, level));
                }
                (current, level)
            }
        };
        let dictionary = match current {
            Object::Array(items) => {
                deepest = deepest.max(level);
                if !items.is_empty() {
                    pending.try_reserve(1).map_err(allocation_error)?;
                    pending.push(Pending::ArrayTail(items, level + 1));
                }
                None
            }
            Object::Dictionary(dictionary) => Some(dictionary),
            Object::Stream(stream) => Some(&stream.dict),
            _ => None,
        };
        if let Some(dictionary) = dictionary {
            deepest = deepest.max(level);
            pending
                .try_reserve(dictionary.len())
                .map_err(allocation_error)?;
            for (_, value) in dictionary.iter() {
                pending.push(Pending::Visit(value, level + 1));
            }
        }
        if deepest > stop_above {
            return Ok(deepest);
        }
    }
    Ok(deepest)
}

fn allocation_error(error: TryReserveError) -> pyo3::PyErr {
    PdfError::new_err(format!(
        "failed to allocate rendering copy depth check: {error}"
    ))
}
