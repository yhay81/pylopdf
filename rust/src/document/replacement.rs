//! Simple-font text replacement on a page.

use super::*;

/// Bound lopdf's simple-font replacement inputs before encoding work.
pub(super) const MAX_TEXT_REPLACEMENT_INPUT_BYTES: usize = 4096;

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
