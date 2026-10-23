//! Simple-font text replacement on a page.

use pyo3::IntoPyObjectExt;
use pyo3::types::PyBytes;

use super::*;

/// Bound lopdf's simple-font replacement inputs before encoding work.
pub(super) const MAX_TEXT_REPLACEMENT_INPUT_BYTES: usize = 4096;
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
);

/// Implements [`_Document::content_glyphs`].
pub(super) fn content_glyphs(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<Vec<ContentGlyphTuple>> {
    let page_id = this.page_id(page_number)?;
    let rotation = this.resolve_page_rotation(page_id)?;
    let page_box = |key: &str| -> Option<[f64; 4]> {
        let (x0, y0, x1, y1) = this.resolve_page_box(page_id, key).ok()??;
        Some([x0, y0, x1, y1])
    };
    let display =
        crate::content::display_transform(page_box("MediaBox"), page_box("CropBox"), rotation);
    let resources = this.resolve_page_attr(page_id, b"Resources")?;
    let resources = match &resources {
        Some(Object::Dictionary(dict)) => Some(dict),
        _ => None,
    };
    let doc: &Document = &this.doc;
    let glyphs = py.detach(|| -> PyResult<_> {
        let content = page_content(doc, page_id)?;
        let page = doc.get_dictionary(page_id).map_err(to_py_err)?;
        crate::content::interpret_page(doc, page, &content, resources, &interpreted_stream)
            .map_err(content_error)
    })?;
    let mut out = Vec::new();
    out.try_reserve_exact(glyphs.len())
        .map_err(|error| PdfError::new_err(format!("failed to allocate glyph list: {error}")))?;
    for glyph in glyphs {
        let origin = display * glyph.origin();
        let (x, y) = (origin.x, origin.y);
        let source = glyph.source;
        let container = match source.container {
            crate::content::Container::Page => 0,
            crate::content::Container::Form((number, _)) => number,
        };
        out.push((
            (x, y),
            glyph.code,
            glyph.advance,
            glyph.render_mode,
            (
                container,
                source.operation,
                source.operand,
                source.offset,
                source.len,
            ),
            (glyph.approximate, glyph.fallback),
            glyph.annotation.map_or(0, |(number, _)| number),
        ));
    }
    Ok(out)
}

fn interpreted_stream(stream: &Stream) -> Result<Vec<u8>, crate::content::ContentError> {
    decoded_stream_content(
        stream,
        Some(MAX_INTERPRETED_STREAM_BYTES),
        "content_stream_size",
        "interpreted content stream",
    )
    .map_err(|error| crate::content::ContentError::Malformed(error.to_string()))
}

fn content_error(error: crate::content::ContentError) -> PyErr {
    match error {
        crate::content::ContentError::Limit { code, message } => limit_err(code, message),
        crate::content::ContentError::Malformed(message)
        | crate::content::ContentError::Allocation(message) => PdfError::new_err(message),
    }
}

/// Implements [`_Document::replace_text_on_page`].
pub(super) fn replace_text_on_page(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    search: &str,
    replacement: &str,
    default_char: Option<String>,
    max_size: Option<usize>,
) -> PyResult<usize> {
    if search.is_empty() {
        return Err(PyValueError::new_err("search must be at least 1 character"));
    }
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    if default_char
        .as_deref()
        .is_some_and(|value| value.chars().count() != 1)
    {
        return Err(PyValueError::new_err(
            "default_char must contain exactly one character",
        ));
    }
    let input_size = search
        .len()
        .checked_add(replacement.len())
        .and_then(|size| size.checked_add(default_char.as_deref().map_or(0, str::len)))
        .ok_or_else(|| {
            limit_err(
                "replacement_input_size",
                "text replacement input size overflow",
            )
        })?;
    if input_size > MAX_TEXT_REPLACEMENT_INPUT_BYTES {
        return Err(limit_err(
            "replacement_input_size",
            format!(
                "text replacement inputs total {input_size} UTF-8 bytes, exceeding the \
                 {MAX_TEXT_REPLACEMENT_INPUT_BYTES}-byte safety limit"
            ),
        ));
    }

    let page_id = this.page_id(page_number)?;
    let streams = draw::inspect_page_contents(&this.doc, page_id).map_err(PdfError::new_err)?;
    let default_char = default_char.as_deref().unwrap_or("?");
    let replacement_count = py.detach(|| {
        for stream_id in &streams {
            if let Ok(stream) = this.doc.get_object(*stream_id).and_then(Object::as_stream) {
                validate_stream_filter_count(stream, "text replacement page content")?;
            }
        }
        // lopdf font lookup misses direct Resources inherited from a page
        // tree, so prepare the same materialized dictionary we will commit.
        let mut page = resolve_inherited_page_dict(&this.doc, page_id)?;
        let content_data = match max_size {
            Some(limit) => {
                let decode_limit = limit.saturating_sub(streams.len());
                let content = this
                    .doc
                    .get_page_content_with_limit(page_id, decode_limit)
                    .map_err(|error| match error {
                        lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded {
                            ..
                        }) => limit_err(
                            "replacement_output_size",
                            format!(
                                "page content exceeds the configured text replacement limit of \
                                 {limit} bytes"
                            ),
                        ),
                        _ => lopdf_err(Some("text replacement content decoding failed"), &error),
                    })?;
                if content.len() > limit {
                    return Err(limit_err(
                        "replacement_output_size",
                        format!(
                            "page content is {} bytes, exceeding the configured text \
                             replacement limit of {limit}",
                            content.len()
                        ),
                    ));
                }
                content
            }
            None => this.doc.get_page_content(page_id),
        };

        let prepared = text_replace::prepare(
            &this.doc,
            &page,
            &content_data,
            search,
            replacement,
            default_char,
            max_size,
        )
        .map_err(|error| match error {
            TextReplacementError::Pdf(lopdf::Error::Decompress(
                DecompressError::MemoryLimitExceeded { .. },
            ))
            | TextReplacementError::OutputSize => limit_err(
                "replacement_output_size",
                match max_size {
                    Some(limit) => format!(
                        "text replacement would exceed the configured limit of {limit} bytes"
                    ),
                    None => "text replacement output size overflow".to_owned(),
                },
            ),
            TextReplacementError::Pdf(error) => lopdf_err(Some("text replacement failed"), &error),
            TextReplacementError::OperandDepth => PdfError::new_err(
                "text replacement content exceeds the 64-level operand-depth safety limit",
            ),
            TextReplacementError::TooManyFonts => {
                PdfError::new_err("text replacement exceeds the 4096-font page safety limit")
            }
        })?;
        let Some((count, encoded)) = prepared else {
            return Ok(0);
        };

        let new_object_number = this
            .doc
            .max_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        let new_content_id = (new_object_number, 0);
        let mut stream = Stream::new(Dictionary::new(), encoded);
        stream
            .compress()
            .map_err(|error| lopdf_err(Some("text replacement compression failed"), &error))?;
        page.set("Contents", new_content_id);

        // Everything above is fallible. Commit the page-owned stream and
        // resolved page dictionary only after preparation succeeds.
        let content_id = this.doc.add_object(stream);
        debug_assert_eq!(content_id, new_content_id);
        this.doc.replace_page(page_id, page);
        Ok(count)
    })?;
    if replacement_count > 0 {
        this.isolated_content_pages.remove(&page_id);
        this.invalidate_hayro_pdf();
    }
    Ok(replacement_count)
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
