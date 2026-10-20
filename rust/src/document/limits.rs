//! Load-time resource limits, structural validation, and complexity facts.

use super::*;

/// Resource limits accepted by the private Rust loading boundary.
#[derive(Clone, Copy, Default)]
pub(super) struct DocumentLimits {
    pub(super) max_file_size: Option<usize>,
    pub(super) max_pages: Option<usize>,
    pub(super) max_objects: Option<usize>,
    pub(super) max_decompressed_size: Option<usize>,
    pub(super) max_page_content_size: Option<usize>,
    pub(super) max_total_decompressed_size: Option<usize>,
    pub(super) max_object_depth: Option<usize>,
    pub(super) max_text_size: Option<usize>,
    pub(super) max_interpretation_size: Option<usize>,
    pub(super) max_text_glyphs: Option<usize>,
}

/// Cheap structural facts that require neither stream decoding nor rendering.
pub(super) type ComplexityTuple = (usize, usize, usize, u64, usize);

/// Return the maximum direct array/dictionary nesting in one object.
///
/// Indirect references are leaves here. This makes cycles harmless while
/// bounding the recursive object shapes consumed by downstream libraries.
pub(super) fn direct_object_depth(object: &Object, stop_above: Option<usize>) -> PyResult<usize> {
    enum Pending<'a> {
        Visit(&'a Object, usize),
        ArrayTail(&'a [Object], usize),
    }

    let mut maximum = 1usize;
    let mut pending = Vec::new();
    pending.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate direct-object depth traversal stack: {error}"
        ))
    })?;
    pending.push(Pending::Visit(object, 1usize));
    while let Some(work) = pending.pop() {
        let (current, depth) = match work {
            Pending::Visit(current, depth) => (current, depth),
            Pending::ArrayTail(items, depth) => {
                let Some((current, remaining)) = items.split_first() else {
                    continue;
                };
                if !remaining.is_empty() {
                    pending.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow direct-object depth traversal stack: {error}"
                        ))
                    })?;
                    pending.push(Pending::ArrayTail(remaining, depth));
                }
                (current, depth)
            }
        };
        maximum = maximum.max(depth);
        if stop_above.is_some_and(|limit| maximum > limit) {
            return Ok(maximum);
        }
        let child_depth = depth.saturating_add(1);
        match current {
            Object::Array(items) if !items.is_empty() => {
                pending.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to grow direct-object depth traversal stack: {error}"
                    ))
                })?;
                pending.push(Pending::ArrayTail(items, child_depth));
            }
            Object::Dictionary(dict) => {
                pending.try_reserve(dict.len()).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to grow direct-object depth traversal stack: {error}"
                    ))
                })?;
                for (_, item) in dict.iter() {
                    pending.push(Pending::Visit(item, child_depth));
                }
            }
            Object::Stream(stream) => {
                pending.try_reserve(stream.dict.len()).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to grow direct-object depth traversal stack: {error}"
                    ))
                })?;
                for (_, item) in stream.dict.iter() {
                    pending.push(Pending::Visit(item, child_depth));
                }
            }
            _ => {}
        }
    }
    Ok(maximum)
}

/// Collect cheap structural complexity without decoding streams.
pub(super) fn document_complexity(doc: &IndexedDocument) -> PyResult<ComplexityTuple> {
    let page_count = doc.page_count()?;
    let object_count = doc.objects.len();
    let mut stream_count = 0usize;
    let mut encoded_stream_bytes = 0u64;
    let mut max_object_depth = 0usize;
    for (_, object) in doc.trailer.iter() {
        max_object_depth = max_object_depth.max(direct_object_depth(object, None)?);
    }
    for object in doc.objects.values() {
        max_object_depth = max_object_depth.max(direct_object_depth(object, None)?);
        if let Object::Stream(stream) = object {
            stream_count = stream_count.saturating_add(1);
            encoded_stream_bytes = encoded_stream_bytes.saturating_add(stream.content.len() as u64);
        }
    }
    Ok((
        page_count,
        object_count,
        stream_count,
        encoded_stream_bytes,
        max_object_depth,
    ))
}

/// Reject structural work above configured limits before rendering/extraction.
pub(super) fn validate_structural_limits(
    doc: &Document,
    limits: DocumentLimits,
    validate_pages: bool,
) -> PyResult<Option<Vec<ObjectId>>> {
    if let Some(limit) = limits.max_objects
        && doc.objects.len() > limit
    {
        return Err(limit_err(
            "object_count",
            format!(
                "PDF contains {} indirect objects, exceeding the configured limit of {limit}",
                doc.objects.len()
            ),
        ));
    }

    if let Some(limit) = limits.max_object_depth {
        for object in doc
            .trailer
            .iter()
            .map(|(_, object)| object)
            .chain(doc.objects.values())
        {
            let depth = direct_object_depth(object, Some(limit))?;
            if depth > limit {
                return Err(limit_err(
                    "object_depth",
                    format!(
                        "PDF direct object nesting depth is {depth}, exceeding the configured limit of {limit}"
                    ),
                ));
            }
        }
    }

    if validate_pages && (limits.max_pages.is_some() || limits.max_page_content_size.is_some()) {
        return collect_page_ids(doc, limits.max_pages, None).map(Some);
    }
    Ok(None)
}

/// Map bounded decoder failures to the applicable stable limit code.
pub(super) fn decompression_err(error: lopdf::Error, code: &'static str) -> PyErr {
    if matches!(
        error,
        lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
    ) {
        limit_err(code, LopdfErrorChain(&error).to_string())
    } else {
        to_py_err(error)
    }
}

/// Measure one decoded stream with bounded intermediate allocations.
pub(super) fn decoded_stream_size(
    doc: &Document,
    stream: &Stream,
    max_output: usize,
    limit_code: &'static str,
) -> PyResult<usize> {
    const LOPDF_FILTERS: [&[u8]; 3] = [b"FlateDecode", b"LZWDecode", b"ASCII85Decode"];
    const IMAGE_FILTERS: [&[u8]; 4] = [
        b"DCTDecode",
        b"JPXDecode",
        b"JBIG2Decode",
        b"CCITTFaxDecode",
    ];

    if !stream.dict.has(b"Filter") {
        if stream.content.len() > max_output {
            return Err(limit_err(
                limit_code,
                format!("decompressed output exceeded the {max_output}-byte limit"),
            ));
        }
        return Ok(stream.content.len());
    }
    validate_stream_filter_count(stream, "PDF stream")?;
    let raw_filters = stream.filters().map_err(to_py_err)?;
    let filters: Vec<&[u8]> = raw_filters
        .iter()
        .map(|filter| canonical_filter_name(filter))
        .collect();
    if filters.is_empty() {
        if stream.content.len() > max_output {
            return Err(limit_err(
                limit_code,
                format!("decompressed output exceeded the {max_output}-byte limit"),
            ));
        }
        return Ok(stream.content.len());
    }
    let first_unsupported = filters
        .iter()
        .position(|filter| !LOPDF_FILTERS.contains(filter));
    match first_unsupported {
        None => {
            let canonical = raw_filters
                .iter()
                .zip(&filters)
                .all(|(raw, normalized)| *raw == *normalized);
            if canonical {
                stream
                    .get_plain_content_with_limit(max_output)
                    .map(|content| content.len())
                    .map_err(|error| decompression_err(error, limit_code))
            } else {
                normalized_stream_copy(stream, &filters, "PDF stream")?
                    .get_plain_content_with_limit(max_output)
                    .map(|content| content.len())
                    .map_err(|error| decompression_err(error, limit_code))
            }
        }
        Some(index)
            if IMAGE_FILTERS.contains(&filters[index])
                && index + 1 == filters.len()
                && filters[..index]
                    .iter()
                    .all(|filter| LOPDF_FILTERS.contains(filter)) =>
        {
            // Bound and measure any compression layers before the image codec.
            let prefix_size = if index == 0 {
                0
            } else {
                normalized_stream_copy(stream, &filters[..index], "PDF stream")?
                    .get_plain_content_with_limit(max_output)
                    .map(|content| content.len())
                    .map_err(|error| decompression_err(error, limit_code))?
            };
            let width = stream
                .dict
                .get(b"Width")
                .ok()
                .and_then(|value| resolve_i64(doc, value));
            let height = stream
                .dict
                .get(b"Height")
                .ok()
                .and_then(|value| resolve_i64(doc, value));
            let (Some(width), Some(height)) = (width, height) else {
                return Err(limit_err(
                    "decompression_unverifiable",
                    "cannot resolve an image stream's Width/Height under the configured decompression policy",
                ));
            };
            let decoded_size = u64::try_from(width)
                .ok()
                .and_then(|width| {
                    u64::try_from(height)
                        .ok()
                        .and_then(|height| width.checked_mul(height))
                })
                .and_then(|pixels| pixels.checked_mul(4))
                .and_then(|bytes| usize::try_from(bytes).ok())
                .ok_or_else(|| {
                    limit_err(
                        limit_code,
                        "image decompressed size exceeds the platform limit",
                    )
                })?;
            let measured = prefix_size.max(decoded_size);
            if measured > max_output {
                return Err(limit_err(
                    limit_code,
                    format!(
                        "decompressed image output exceeded the {max_output}-byte limit (possible decompression bomb)"
                    ),
                ));
            }
            Ok(measured)
        }
        Some(index) if filters.len() == 1 && filters[index] == b"RunLengthDecode" => {
            run_length_size(&stream.content, max_output, limit_code)
        }
        Some(index) if filters.len() == 1 && filters[index] == b"ASCIIHexDecode" => {
            ascii_hex_size(&stream.content, max_output, limit_code)
        }
        Some(index) => Err(limit_err(
            "decompression_unverifiable",
            format!(
                "cannot safely verify a stream with filter {:?} under the configured decompression policy",
                String::from_utf8_lossy(filters[index])
            ),
        )),
    }
}

/// Prevalidate per-stream and cumulative decompression budgets at load.
pub(super) fn validate_decompression_limits(
    doc: &Document,
    page_ids: Option<&[ObjectId]>,
    max_output: Option<usize>,
    max_page_content: Option<usize>,
    max_total: Option<usize>,
) -> PyResult<()> {
    if max_output.is_none() && max_page_content.is_none() && max_total.is_none() {
        return Ok(());
    }
    let page_content_ids: HashSet<ObjectId> = if max_page_content.is_some() {
        let pages = page_ids.ok_or_else(|| {
            PdfError::new_err("internal page-content validation index is unavailable")
        })?;
        let mut content_ids = HashSet::new();
        for page_id in pages {
            let streams = draw::inspect_page_contents(doc, *page_id).map_err(PdfError::new_err)?;
            content_ids.try_reserve(streams.len()).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate page-content decompression index: {error}"
                ))
            })?;
            content_ids.extend(streams);
        }
        content_ids
    } else {
        HashSet::new()
    };
    let mut total = 0usize;
    for (object_id, object) in &doc.objects {
        let Object::Stream(stream) = object else {
            continue;
        };
        let is_page_content = page_content_ids.contains(object_id);
        if max_output.is_none() && max_total.is_none() && !is_page_content {
            continue;
        }
        let mut decode_bound = usize::MAX;
        let mut bound_code = "decompressed_size";
        if let Some(limit) = max_output {
            decode_bound = limit;
        }
        if is_page_content
            && let Some(limit) = max_page_content
            && limit < decode_bound
        {
            decode_bound = limit;
            bound_code = "page_content_size";
        }
        if let Some(limit) = max_total
            && limit < decode_bound
        {
            decode_bound = limit;
            bound_code = "total_decompressed_size";
        }
        let size = decoded_stream_size(doc, stream, decode_bound, bound_code)?;
        if let Some(limit) = max_output
            && size > limit
        {
            return Err(limit_err(
                "decompressed_size",
                format!(
                    "stream expands to {size} bytes, exceeding the configured limit of {limit}"
                ),
            ));
        }
        if is_page_content
            && let Some(limit) = max_page_content
            && size > limit
        {
            return Err(limit_err(
                "page_content_size",
                format!(
                    "page content stream expands to {size} bytes, exceeding the configured limit of {limit}"
                ),
            ));
        }
        total = total.checked_add(size).ok_or_else(|| {
            limit_err(
                "total_decompressed_size",
                "cumulative decompressed size exceeds the platform limit",
            )
        })?;
        if let Some(limit) = max_total
            && total > limit
        {
            return Err(limit_err(
                "total_decompressed_size",
                format!(
                    "streams expand to at least {total} bytes, exceeding the configured cumulative limit of {limit}"
                ),
            ));
        }
    }
    Ok(())
}
