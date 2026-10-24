//! Content cleaning: one stream per page, every operation written from its
//! values, graphics states balanced, and only the resources the content uses.
//!
//! The operations are the ones hayro reads (`content::syntax`), so cleaning
//! changes no operation that rendering interprets; comments, the bytes after
//! the point where hayro stops reading, and `Q` operators that find no `q`
//! go, and the `Q` that the content leaves open are added at its end.
//! Resources are pruned on a page-owned copy and keep every name that a Form
//! XObject or annotation appearance without resources of its own still
//! resolves through them.

use std::collections::{HashMap, HashSet};

use hayro::hayro_syntax::object::Object as HObject;
use lopdf::content::Operation;

use crate::content::syntax::Instructions;

use super::*;

/// Resource categories that content names, with nothing else in them.
const CATEGORIES: [&[u8]; 7] = [
    b"Font",
    b"XObject",
    b"ExtGState",
    b"ColorSpace",
    b"Pattern",
    b"Shading",
    b"Properties",
];
/// Colour space names that need no resource.
const DEVICE_SPACES: [&[u8]; 7] = [
    b"DeviceGray",
    b"DeviceRGB",
    b"DeviceCMYK",
    b"Pattern",
    b"G",
    b"RGB",
    b"CMYK",
];
/// Forms followed when collecting the names that inherit resources.
const MAX_FORM_DEPTH: usize = 50;

type Names = HashMap<&'static [u8], HashSet<Vec<u8>>>;

fn name_of(object: Option<&HObject<'_>>) -> Option<Vec<u8>> {
    match object {
        Some(HObject::Name(name)) => Some(name.to_vec()),
        _ => None,
    }
}

/// Record the resource names that one stream's operations use.
fn collect_names(instructions: &Instructions<'_>, names: &mut Names) -> Vec<Vec<u8>> {
    let mut forms = Vec::new();
    for index in 0..instructions.len() {
        let operands = instructions.operands(index);
        let last = operands.last();
        let category: Option<(&'static [u8], Option<Vec<u8>>)> =
            match instructions.instruction(index).operator {
                b"Tf" => Some((
                    b"Font",
                    name_of(
                        operands
                            .len()
                            .checked_sub(2)
                            .and_then(|at| operands.get(at)),
                    ),
                )),
                b"Do" => {
                    let name = name_of(last);
                    if let Some(name) = &name {
                        forms.push(name.clone());
                    }
                    Some((b"XObject", name))
                }
                b"gs" => Some((b"ExtGState", name_of(last))),
                b"cs" | b"CS" => Some((b"ColorSpace", name_of(last))),
                b"scn" | b"SCN" => Some((b"Pattern", name_of(last))),
                b"sh" => Some((b"Shading", name_of(last))),
                b"BDC" | b"DP" => Some((b"Properties", name_of(last))),
                b"BI" => match last {
                    Some(HObject::Stream(image)) => {
                        let space = image
                            .dict()
                            .get::<HObject<'_>>(b"CS")
                            .or_else(|| image.dict().get::<HObject<'_>>(b"ColorSpace"));
                        Some((b"ColorSpace", name_of(space.as_ref())))
                    }
                    _ => None,
                },
                _ => None,
            };
        if let Some((category, Some(name))) = category
            && !(category == b"ColorSpace" && DEVICE_SPACES.contains(&name.as_slice()))
        {
            names.entry(category).or_default().insert(name);
        }
    }
    forms
}

/// The operations of a stream written from their values, with every `Q`
/// that finds no `q` dropped and the open ones closed at the end.
fn normalized(instructions: &Instructions<'_>) -> PyResult<Vec<u8>> {
    let operations = instructions
        .to_operations()
        .map_err(replacement::content_error)?;
    let mut out = Vec::new();
    out.try_reserve_exact(operations.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate cleaned content: {error}"))
    })?;
    let mut depth = 0_usize;
    for operation in operations {
        match operation.operator.as_str() {
            "q" => depth += 1,
            "Q" if depth == 0 => continue,
            "Q" => depth -= 1,
            _ => {}
        }
        out.push(operation);
    }
    out.try_reserve_exact(depth).map_err(|error| {
        PdfError::new_err(format!("failed to allocate cleaned content: {error}"))
    })?;
    out.extend((0..depth).map(|_| Operation::new("Q", Vec::new())));
    crate::redact::encode(out).map_err(replacement::content_error)
}

/// Collect the names that Forms drawn from `names`' streams, transitively,
/// resolve through the resources that drew them because they have none.
fn inherited_names(
    doc: &Document,
    resources: &Dictionary,
    drawn: Vec<Vec<u8>>,
    names: &mut Names,
) -> PyResult<()> {
    let mut queue: Vec<(Vec<u8>, usize)> = drawn.into_iter().map(|name| (name, 0)).collect();
    let mut seen: HashSet<ObjectId> = HashSet::new();
    let xobjects = resources
        .get(b"XObject")
        .ok()
        .and_then(|value| crate::redact::resolve(doc, value))
        .and_then(|value| value.as_dict().ok());
    while let Some((name, depth)) = queue.pop() {
        let Some(Ok(id)) = xobjects
            .and_then(|xobjects| xobjects.get(&name).ok())
            .map(Object::as_reference)
        else {
            continue;
        };
        if depth >= MAX_FORM_DEPTH || !seen.insert(id) {
            continue;
        }
        let Ok(stream) = doc.get_object(id).and_then(Object::as_stream) else {
            continue;
        };
        if stream.dict.has(b"Resources")
            || !matches!(stream.dict.get(b"Subtype"), Ok(Object::Name(subtype)) if subtype == b"Form")
        {
            continue;
        }
        let data = replacement::interpreted_stream(stream).map_err(replacement::content_error)?;
        let instructions = Instructions::parse(&data, None).map_err(replacement::content_error)?;
        for form in collect_names(&instructions, names) {
            queue.push((form, depth + 1));
        }
    }
    Ok(())
}

/// A copy of `resources` that keeps, in the categories that content names,
/// only the entries in `names`.
fn pruned(doc: &Document, resources: &Dictionary, names: &Names) -> Dictionary {
    let mut out = resources.clone();
    for category in CATEGORIES {
        let Some(entries) = resources
            .get(category)
            .ok()
            .and_then(|value| crate::redact::resolve(doc, value))
            .and_then(|value| value.as_dict().ok())
        else {
            continue;
        };
        let used = names.get(category);
        let mut kept = Dictionary::new();
        for (name, value) in entries {
            if used.is_some_and(|used| used.contains(name)) {
                kept.set(name.clone(), value.clone());
            }
        }
        if kept.is_empty() {
            out.remove(category);
        } else {
            out.set(category, Object::Dictionary(kept));
        }
    }
    out
}

/// Implements [`_Document::clean_page_contents`].
pub(super) fn clean_page_contents(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<()> {
    let page_id = this.page_id(page_number)?;
    let resources = match this.resolve_page_attr(page_id, b"Resources")? {
        Some(Object::Dictionary(dict)) => dict,
        _ => Dictionary::new(),
    };
    let doc: &Document = &this.doc;
    let (content, resources) = py.detach(|| -> PyResult<(Stream, Dictionary)> {
        let data = replacement::page_content(doc, page_id)?;
        let instructions = Instructions::parse(&data, None).map_err(replacement::content_error)?;
        let mut names = Names::new();
        let drawn = collect_names(&instructions, &mut names);
        inherited_names(doc, &resources, drawn, &mut names)?;
        // Appearances without resources of their own resolve names through
        // the page's.
        let page = doc.get_dictionary(page_id).map_err(to_py_err)?;
        for stream in appearance_streams(doc, page) {
            let Ok(stream) = doc.get_object(stream).and_then(Object::as_stream) else {
                continue;
            };
            if stream.dict.has(b"Resources") {
                continue;
            }
            let data =
                replacement::interpreted_stream(stream).map_err(replacement::content_error)?;
            let instructions =
                Instructions::parse(&data, None).map_err(replacement::content_error)?;
            let drawn = collect_names(&instructions, &mut names);
            inherited_names(doc, &resources, drawn, &mut names)?;
        }
        let bytes = normalized(&instructions)?;
        let mut stream = Stream::new(Dictionary::new(), bytes);
        stream
            .compress()
            .map_err(|error| lopdf_err(Some("cleaned content compression failed"), &error))?;
        Ok((stream, pruned(doc, &resources, &names)))
    })?;
    // Everything fallible is done; commit.
    this.invalidate_hayro_pdf();
    let id = this.doc.add_object(content);
    this.doc
        .set_page_object_entry(page_id, "Contents", Object::Reference(id))?;
    this.doc
        .set_page_object_entry(page_id, "Resources", Object::Dictionary(resources))?;
    this.isolated_content_pages.remove(&page_id);
    Ok(())
}

/// The appearance streams that a page's annotations name.
fn appearance_streams(doc: &Document, page: &Dictionary) -> Vec<ObjectId> {
    let mut streams = Vec::new();
    let Some(annotations) = page
        .get(b"Annots")
        .ok()
        .and_then(|value| crate::redact::resolve(doc, value))
        .and_then(|value| value.as_array().ok())
    else {
        return streams;
    };
    for item in annotations {
        if let Some(annotation) =
            crate::redact::resolve(doc, item).and_then(|value| value.as_dict().ok())
        {
            streams.extend(annotation_streams(doc, annotation));
        }
    }
    streams
}

/// Every stream of an annotation's `/AP`: the normal, rollover, and down
/// appearances, and each state of a state dictionary.
fn annotation_streams(doc: &Document, annotation: &Dictionary) -> Vec<ObjectId> {
    let mut streams = Vec::new();
    let Some(appearances) = annotation
        .get(b"AP")
        .ok()
        .and_then(|value| crate::redact::resolve(doc, value))
        .and_then(|value| value.as_dict().ok())
    else {
        return streams;
    };
    for (_, value) in appearances {
        match value {
            Object::Reference(id) => match doc.get_object(*id) {
                Ok(Object::Stream(_)) => streams.push(*id),
                Ok(Object::Dictionary(states)) => {
                    streams.extend(
                        states
                            .iter()
                            .filter_map(|(_, state)| state.as_reference().ok()),
                    );
                }
                _ => {}
            },
            Object::Dictionary(states) => {
                streams.extend(
                    states
                        .iter()
                        .filter_map(|(_, state)| state.as_reference().ok()),
                );
            }
            _ => {}
        }
    }
    streams
}

/// Implements [`_Document::clean_annotation_contents`]: clean every
/// appearance stream of one annotation in place, which keeps what it draws,
/// and prune the resources that each stream holds itself.
pub(super) fn clean_annotation_contents(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
) -> PyResult<()> {
    let page_id = this.page_id(page_number)?;
    let (_, annotation) = this.annotation_at(page_id, index, xref)?;
    let doc: &Document = &this.doc;
    let streams = annotation_streams(doc, annotation);
    let mut cleaned = Vec::new();
    py.detach(|| -> PyResult<()> {
        for id in streams {
            let Ok(stream) = doc.get_object(id).and_then(Object::as_stream) else {
                continue;
            };
            let data =
                replacement::interpreted_stream(stream).map_err(replacement::content_error)?;
            let instructions =
                Instructions::parse(&data, None).map_err(replacement::content_error)?;
            let mut dict = stream.dict.clone();
            for key in [b"Length".as_slice(), b"Filter", b"DecodeParms", b"DL"] {
                dict.remove(key);
            }
            if let Some(resources) = stream
                .dict
                .get(b"Resources")
                .ok()
                .and_then(|value| crate::redact::resolve(doc, value))
                .and_then(|value| value.as_dict().ok())
            {
                let mut names = Names::new();
                let drawn = collect_names(&instructions, &mut names);
                inherited_names(doc, resources, drawn, &mut names)?;
                dict.set(
                    "Resources",
                    Object::Dictionary(pruned(doc, resources, &names)),
                );
            }
            let mut stream = Stream::new(dict, normalized(&instructions)?);
            stream.compress().map_err(|error| {
                lopdf_err(Some("cleaned appearance compression failed"), &error)
            })?;
            cleaned.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to allocate cleaned appearances: {error}"))
            })?;
            cleaned.push((id, stream));
        }
        Ok(())
    })?;
    this.invalidate_hayro_pdf();
    for (id, stream) in cleaned {
        this.doc.objects.insert(id, Object::Stream(stream));
    }
    Ok(())
}
