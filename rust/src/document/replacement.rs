//! Content reading and writing for the rewriting operations: the content
//! engine's hooks, `Page.operations`, and the page content joined as hayro
//! joins it.

use pyo3::IntoPyObjectExt;
use pyo3::types::PyBytes;

use super::*;

/// Bound one decoded stream that content interpretation reads.
const MAX_INTERPRETED_STREAM_BYTES: usize = 64 * 1024 * 1024;

/// One glyph as the content-interpretation hook reports it: display-space
/// origin; character code; advance in glyph space (1000 units per em); text
/// rendering mode; the object number of the Form XObject that holds the code,
/// or 0 for page content; the operation, operand, byte offset, and byte length
/// of the code; whether its width was approximate or its font a Helvetica
/// fallback; and the object number of the annotation whose appearance drew
/// it, or 0.
pub(super) type ContentGlyphTuple = (
    (f64, f64),
    u32,
    (f64, f64),
    u8,
    (u32, usize, usize, usize, usize),
    (bool, bool),
    u32,
    (f64, f64, f64, f64),
    Option<f32>,
);

/// Implements [`_Document::content_glyphs`].
pub(super) fn content_glyphs(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<Vec<ContentGlyphTuple>> {
    let page_id = this.page_id(page_number)?;
    let display = page_display(this, page_id)?;
    let resources = this.resolve_page_attr(page_id, b"Resources")?;
    let resources = match &resources {
        Some(Object::Dictionary(dict)) => Some(dict),
        _ => None,
    };
    let doc: &Document = &this.doc;
    let interpretation = py.detach(|| interpret(doc, page_id, resources))?;
    let mut out = Vec::new();
    out.try_reserve_exact(interpretation.glyphs.len())
        .map_err(|error| PdfError::new_err(format!("failed to allocate glyph list: {error}")))?;
    let containers = &interpretation.containers;
    for glyph in &interpretation.glyphs {
        let origin = display * glyph.origin();
        let source = glyph.source;
        out.push((
            (origin.x, origin.y),
            glyph.code,
            glyph.advance,
            glyph.render_mode,
            (
                container_number(containers, source.container),
                source.operation,
                source.operand,
                source.offset,
                source.len,
            ),
            (glyph.approximate, glyph.fallback),
            glyph.annotation.map_or(0, |(number, _)| number),
            display_bbox(display, glyph.bbox()),
            glyph.compensation,
        ));
    }
    Ok(out)
}

/// Interpret one page with the content engine.
pub(super) fn interpret(
    doc: &Document,
    page_id: ObjectId,
    resources: Option<&Dictionary>,
) -> PyResult<crate::content::Interpretation> {
    let content = page_content(doc, page_id)?;
    let page = doc.get_dictionary(page_id).map_err(to_py_err)?;
    crate::content::interpret_page(doc, page, &content, resources, &interpreted_stream)
        .map_err(content_error)
}

/// The object number of a container's stream, or 0 for page content.
fn container_number(containers: &[crate::content::ContainerInfo], index: usize) -> u32 {
    match containers[index].stream {
        crate::content::Container::Page => 0,
        crate::content::Container::Form((number, _)) => number,
    }
}

/// A rectangle in display space as `(x0, y0, x1, y1)`.
fn display_bbox(display: Affine, rect: hayro::kurbo::Rect) -> (f64, f64, f64, f64) {
    let rect = crate::content::transformed_rect(rect, display);
    (rect.x0, rect.y0, rect.x1, rect.y1)
}

/// One path as the content-items hook reports it: container object number,
/// first and painting operation, geometry and painted boxes in display
/// space, and whether it fills, strokes, and clips.
pub(super) type ContentPathTuple = (
    u32,
    usize,
    usize,
    (f64, f64, f64, f64),
    (f64, f64, f64, f64),
    (bool, bool, bool),
);
/// One image placement: container, operation, image object number or 0 for
/// an inline image, display-space box, and display-space clip box.
pub(super) type ContentImageTuple = (
    u32,
    usize,
    u32,
    (f64, f64, f64, f64),
    Option<(f64, f64, f64, f64)>,
);
/// One container: its stream's object number or 0, the index of the
/// container and operation that drew it, and the annotation's object
/// number or 0.
pub(super) type ContentContainerTuple = (u32, Option<(usize, usize)>, u32);
/// One shading: container, operation, and the display-space clip box.
pub(super) type ContentShadingTuple = (u32, usize, Option<(f64, f64, f64, f64)>);

/// Everything that the content-items hook reports for one page.
pub(super) type ContentItems = (
    Vec<ContentPathTuple>,
    Vec<ContentImageTuple>,
    Vec<ContentShadingTuple>,
    Vec<(u32, usize, Option<usize>)>,
    Vec<ContentContainerTuple>,
);

/// Implements [`_Document::content_items`].
pub(super) fn content_items(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<ContentItems> {
    let page_id = this.page_id(page_number)?;
    let display = page_display(this, page_id)?;
    let resources = this.resolve_page_attr(page_id, b"Resources")?;
    let resources = match &resources {
        Some(Object::Dictionary(dict)) => Some(dict),
        _ => None,
    };
    let doc: &Document = &this.doc;
    let interpretation = py.detach(|| interpret(doc, page_id, resources))?;
    let containers = &interpretation.containers;
    let paths = interpretation
        .paths
        .iter()
        .map(|path| {
            (
                container_number(containers, path.container),
                path.start,
                path.paint,
                display_bbox(display, path.bbox),
                display_bbox(display, path.painted),
                (path.fill, path.stroke, path.clip),
            )
        })
        .collect();
    let images = interpretation
        .images
        .iter()
        .map(|image| {
            (
                container_number(containers, image.container),
                image.operation,
                image.xobject.map_or(0, |(number, _)| number),
                display_bbox(display, image.bbox()),
                image.clip.map(|clip| display_bbox(display, clip)),
            )
        })
        .collect();
    let shadings = interpretation
        .shadings
        .iter()
        .map(|shading| {
            (
                container_number(containers, shading.container),
                shading.operation,
                shading.clip.map(|clip| display_bbox(display, clip)),
            )
        })
        .collect();
    let marked = interpretation
        .marked
        .iter()
        .map(|marked| {
            (
                container_number(containers, marked.container),
                marked.begin,
                marked.end,
            )
        })
        .collect();
    let containers = containers
        .iter()
        .enumerate()
        .map(|(index, info)| {
            (
                container_number(containers, index),
                info.parent,
                info.annotation.map_or(0, |(number, _)| number),
            )
        })
        .collect();
    Ok((paths, images, shadings, marked, containers))
}

/// The transform from default user space to the page's display space.
pub(super) fn page_display(this: &_Document, page_id: ObjectId) -> PyResult<Affine> {
    let rotation = this.resolve_page_rotation(page_id)?;
    let page_box = |key: &str| -> Option<[f64; 4]> {
        let (x0, y0, x1, y1) = this.resolve_page_box(page_id, key).ok()??;
        Some([x0, y0, x1, y1])
    };
    Ok(crate::content::display_transform(
        page_box("MediaBox"),
        page_box("CropBox"),
        rotation,
    ))
}

pub(super) fn interpreted_stream(stream: &Stream) -> Result<Vec<u8>, crate::content::ContentError> {
    decoded_stream_content(
        stream,
        Some(MAX_INTERPRETED_STREAM_BYTES),
        "content_stream_size",
        "interpreted content stream",
    )
    .map_err(|error| crate::content::ContentError::Malformed(error.to_string()))
}

pub(super) fn content_error(error: crate::content::ContentError) -> PyErr {
    match error {
        crate::content::ContentError::Limit { code, message } => limit_err(code, message),
        crate::content::ContentError::Malformed(message)
        | crate::content::ContentError::Allocation(message) => PdfError::new_err(message),
    }
}

/// Operations that `page.operations` and `Operations.parse` return by
/// default before refusing with `content_operation_count`.
pub(super) const DEFAULT_MAX_OPERATIONS: usize = 1 << 20;
/// The joined, decoded content of one page that operator-level reads and
/// writes accept.
pub(super) const MAX_PAGE_CONTENT_BYTES: usize = 64 * 1024 * 1024;

/// A page's content as hayro's `Page::page_stream` joins it: a single stream
/// as it decodes, or every stream of an array followed by one space, up to the
/// first entry that is no stream; a stream that does not decode adds nothing.
pub(super) fn page_content(doc: &Document, page_id: ObjectId) -> PyResult<Vec<u8>> {
    let page = doc.get_dictionary(page_id).map_err(to_py_err)?;
    let mut out = Vec::new();
    let Ok(mut contents) = page.get(b"Contents") else {
        return Ok(out);
    };
    for _ in 0..32 {
        match contents {
            Object::Reference(id) => match doc.objects.get(id) {
                Some(object) => contents = object,
                None => return Ok(out),
            },
            _ => break,
        }
    }
    let mut append = |stream: &Stream, separator: bool| -> PyResult<()> {
        let Some(data) = decoded_or_skipped(stream)? else {
            return Ok(());
        };
        let length = out
            .len()
            .checked_add(data.len())
            .and_then(|length| length.checked_add(usize::from(separator)))
            .filter(|&length| length <= MAX_PAGE_CONTENT_BYTES)
            .ok_or_else(|| {
                limit_err(
                    "content_stream_size",
                    format!(
                        "the page's decoded content exceeds the {MAX_PAGE_CONTENT_BYTES}-byte limit"
                    ),
                )
            })?;
        out.try_reserve_exact(length - out.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate page content: {error}"))
        })?;
        out.extend_from_slice(&data);
        if separator {
            out.push(b' ');
        }
        Ok(())
    };
    match contents {
        Object::Stream(stream) => append(stream, false)?,
        Object::Array(entries) => {
            if entries.len() > draw::MAX_PAGE_CONTENT_STREAMS {
                return Err(PdfError::new_err(format!(
                    "page Contents exceeds the {}-entry safety limit",
                    draw::MAX_PAGE_CONTENT_STREAMS
                )));
            }
            for entry in entries {
                let mut entry = entry;
                for _ in 0..32 {
                    match entry {
                        Object::Reference(id) => match doc.objects.get(id) {
                            Some(object) => entry = object,
                            None => break,
                        },
                        _ => break,
                    }
                }
                let Object::Stream(stream) = entry else {
                    break;
                };
                append(stream, true)?;
            }
        }
        _ => {}
    }
    Ok(out)
}

/// Decode one content stream, or `None` when it does not decode, as hayro
/// skips it; a limit refusal still raises.
fn decoded_or_skipped(stream: &Stream) -> PyResult<Option<Cow<'_, [u8]>>> {
    match decoded_stream_content_cow(
        stream,
        Some(MAX_PAGE_CONTENT_BYTES),
        "content_stream_size",
        "page content stream",
    ) {
        Ok(data) => Ok(Some(data)),
        Err(error) if Python::attach(|py| error.is_instance_of::<LimitError>(py)) => Err(error),
        Err(_) => Ok(None),
    }
}

/// Implements [`_Document::page_operations`].
pub(super) fn page_operations(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
    max_operations: Option<usize>,
) -> PyResult<(Vec<Py<PyAny>>, Py<PyBytes>)> {
    let page_id = this.page_id(page_number)?;
    let doc: &Document = &this.doc;
    let data = py.detach(|| page_content(doc, page_id))?;
    operations_to_python(py, &data, max_operations)
}

/// Tokenize content and return each operation as `("o", operator,
/// operands, bytes)` or, for an inline image, `("i", dictionary entries,
/// data, bytes)`, together with the bytes after the last operation.
pub(crate) fn operations_to_python(
    py: Python<'_>,
    data: &[u8],
    max_operations: Option<usize>,
) -> PyResult<(Vec<Py<PyAny>>, Py<PyBytes>)> {
    use crate::content::syntax::Instructions;
    use hayro::hayro_syntax::object::Object as HObject;

    let parsed = Instructions::parse(data, max_operations).map_err(content_error)?;
    let mut items = Vec::new();
    items.try_reserve_exact(parsed.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate content operations: {error}"))
    })?;
    for index in 0..parsed.len() {
        let instruction = parsed.instruction(index);
        let operands = parsed.operands(index);
        let raw = PyBytes::new(py, parsed.bytes(index));
        let item = match (instruction.operator, operands.last()) {
            (b"BI", Some(HObject::Stream(image))) => (
                "i",
                dictionary_to_python(py, image.dict())?,
                PyBytes::new(py, inline_image_data(&image.raw_data()))
                    .into_any()
                    .unbind(),
                raw,
            )
                .into_py_any(py)?,
            _ => {
                let mut values = Vec::new();
                values.try_reserve_exact(operands.len()).map_err(|error| {
                    PdfError::new_err(format!("failed to allocate content operands: {error}"))
                })?;
                for operand in operands {
                    values.push(operand_to_python(py, operand)?);
                }
                ("o", PyBytes::new(py, instruction.operator), values, raw).into_py_any(py)?
            }
        };
        items.push(item);
    }
    Ok((items, PyBytes::new(py, parsed.tail()).unbind()))
}

/// An inline image's data without the white-space byte that separates it
/// from `EI`, which hayro's data still holds.
fn inline_image_data(data: &[u8]) -> &[u8] {
    match data.split_last() {
        Some((last, rest)) if b"\x00\t\n\x0c\r ".contains(last) => rest,
        _ => data,
    }
}

fn operand_to_python(
    py: Python<'_>,
    object: &hayro::hayro_syntax::object::Object<'_>,
) -> PyResult<Py<PyAny>> {
    use hayro::hayro_syntax::object::Object as HObject;
    match object {
        HObject::Null(_) => Ok(py.None()),
        HObject::Boolean(value) => value.into_py_any(py),
        HObject::Number(number) if crate::content::syntax::is_integer(number) => {
            number.as_i64().into_py_any(py)
        }
        HObject::Number(number) => number.as_f64().into_py_any(py),
        HObject::String(string) => ("s", PyBytes::new(py, string.as_bytes())).into_py_any(py),
        HObject::Name(name) => ("n", PyBytes::new(py, name)).into_py_any(py),
        HObject::Array(array) => {
            let mut items = Vec::new();
            for item in array.iter::<HObject<'_>>() {
                items.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to allocate a content array: {error}"))
                })?;
                items.push(operand_to_python(py, &item)?);
            }
            ("a", items).into_py_any(py)
        }
        HObject::Dict(dict) => dictionary_to_python(py, dict),
        // A stream is never an operand outside an inline image.
        HObject::Stream(stream) => dictionary_to_python(py, stream.dict()),
    }
}

fn dictionary_to_python(
    py: Python<'_>,
    dict: &hayro::hayro_syntax::object::Dict<'_>,
) -> PyResult<Py<PyAny>> {
    use hayro::hayro_syntax::object::MaybeRef;
    let mut entries = Vec::new();
    for (key, value) in dict.entries() {
        let value = match value {
            MaybeRef::NotRef(value) => operand_to_python(py, &value)?,
            MaybeRef::Ref(_) => py.None(),
        };
        entries.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to allocate a content dictionary: {error}"))
        })?;
        entries.push((PyBytes::new(py, &key).unbind(), value));
    }
    ("d", entries).into_py_any(py)
}

/// Implements [`_Document::set_page_content`].
pub(super) fn set_page_content(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    data: &[u8],
) -> PyResult<()> {
    if data.len() > MAX_PAGE_CONTENT_BYTES {
        return Err(limit_err(
            "content_input_size",
            format!(
                "page content of {} bytes exceeds the {MAX_PAGE_CONTENT_BYTES}-byte limit",
                data.len()
            ),
        ));
    }
    let page_id = this.page_id(page_number)?;
    let stream = py.detach(|| -> PyResult<Stream> {
        let mut content = Vec::new();
        content.try_reserve_exact(data.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate page content: {error}"))
        })?;
        content.extend_from_slice(data);
        let mut stream = Stream::new(Dictionary::new(), content);
        stream
            .compress()
            .map_err(|error| lopdf_err(Some("page content compression failed"), &error))?;
        Ok(stream)
    })?;
    this.doc.get_dictionary(page_id).map_err(to_py_err)?;
    // Everything fallible is done; commit the page-owned stream.
    let content_id = this.doc.add_object(stream);
    this.doc
        .set_page_object_entry(page_id, "Contents", Object::Reference(content_id))?;
    this.isolated_content_pages.remove(&page_id);
    this.invalidate_hayro_pdf_keeping_placements();
    Ok(())
}

/// Tokenize content-stream bytes for `Operations.parse`; see
/// [`operations_to_python`].
#[pyfunction]
#[pyo3(signature = (data, max_operations=Some(DEFAULT_MAX_OPERATIONS)))]
pub(crate) fn _parse_operations(
    py: Python<'_>,
    data: &[u8],
    max_operations: Option<usize>,
) -> PyResult<(Vec<Py<PyAny>>, Py<PyBytes>)> {
    operations_to_python(py, data, max_operations)
}
