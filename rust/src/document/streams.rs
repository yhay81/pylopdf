//! Bounded stream decoding shared by load validation and edits.

use super::*;

/// Bound sequential decoder work independently from each layer's byte budget.
pub(super) const MAX_STREAM_FILTERS: usize = 16;

/// Validate RunLengthDecode output size without allocating the output.
pub(super) fn run_length_size(
    data: &[u8],
    max_output: usize,
    limit_code: &'static str,
) -> PyResult<usize> {
    let mut pos = 0usize;
    let mut output_len = 0usize;
    while pos < data.len() {
        let length = data[pos];
        pos += 1;
        match length {
            0..=127 => {
                let count = usize::from(length) + 1;
                if pos.checked_add(count).is_none_or(|end| end > data.len()) {
                    return Err(PdfError::new_err(
                        "RunLengthDecode stream ends unexpectedly",
                    ));
                }
                pos += count;
                output_len = output_len.checked_add(count).ok_or_else(|| {
                    limit_err(
                        limit_code,
                        "RunLengthDecode decompressed size exceeds the configured limit",
                    )
                })?;
            }
            128 => break,
            129..=255 => {
                if pos >= data.len() {
                    return Err(PdfError::new_err(
                        "RunLengthDecode stream ends unexpectedly",
                    ));
                }
                pos += 1;
                output_len = output_len
                    .checked_add(257 - usize::from(length))
                    .ok_or_else(|| {
                        limit_err(
                            limit_code,
                            "RunLengthDecode decompressed size exceeds the configured limit",
                        )
                    })?;
            }
        }
        if output_len > max_output {
            return Err(limit_err(
                limit_code,
                format!(
                    "decompressed output exceeded the {max_output}-byte limit (possible decompression bomb)"
                ),
            ));
        }
    }
    Ok(output_len)
}

/// Validate ASCIIHexDecode output size without allocating the output.
pub(super) fn ascii_hex_size(
    data: &[u8],
    max_output: usize,
    limit_code: &'static str,
) -> PyResult<usize> {
    let digits = data
        .iter()
        .take_while(|&&byte| byte != b'>')
        .filter(|byte| !byte.is_ascii_whitespace())
        .count();
    let output_len = digits.div_ceil(2);
    if output_len > max_output {
        return Err(limit_err(
            limit_code,
            format!(
                "decompressed output exceeded the {max_output}-byte limit (possible decompression bomb)"
            ),
        ));
    }
    Ok(output_len)
}

/// Normalize PDF-spec filter abbreviations to canonical names.
pub(super) fn canonical_filter_name(filter: &[u8]) -> &[u8] {
    match filter {
        b"Fl" => b"FlateDecode",
        b"LZW" => b"LZWDecode",
        b"A85" => b"ASCII85Decode",
        b"AHx" => b"ASCIIHexDecode",
        b"RL" => b"RunLengthDecode",
        b"CCF" => b"CCITTFaxDecode",
        b"DCT" => b"DCTDecode",
        _ => filter,
    }
}

/// Reject a stream filter array before lopdf materializes or applies it.
pub(super) fn validate_stream_filter_count(stream: &Stream, context: &str) -> PyResult<()> {
    let count = match stream.dict.get(b"Filter") {
        Ok(Object::Name(_)) => 1,
        Ok(Object::Array(filters)) => filters.len(),
        _ => return Ok(()),
    };
    if count > MAX_STREAM_FILTERS {
        return Err(limit_err(
            "stream_filter_count",
            format!(
                "{context} uses {count} filter layers, exceeding the \
                 {MAX_STREAM_FILTERS}-layer safety limit"
            ),
        ));
    }
    Ok(())
}

/// Build one normalized stream copy without infallibly cloning its payload.
pub(super) fn normalized_stream_copy(
    stream: &Stream,
    filters: &[&[u8]],
    context: &str,
) -> PyResult<Stream> {
    let mut content = Vec::new();
    content
        .try_reserve_exact(stream.content.len())
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate encoded {context} for filter normalization: {error}"
            ))
        })?;
    content.extend_from_slice(&stream.content);
    let normalized_filter = if filters.len() == 1 {
        Object::Name(filters[0].to_vec())
    } else {
        Object::Array(
            filters
                .iter()
                .map(|filter| Object::Name(filter.to_vec()))
                .collect(),
        )
    };
    let mut checked_stream = Stream {
        dict: stream.dict.clone(),
        content,
        allows_compression: stream.allows_compression,
        start_position: stream.start_position,
    };
    checked_stream.dict.set("Filter", normalized_filter);
    Ok(checked_stream)
}

/// Decode a general-purpose stream with an optional bound on every filter layer.
///
/// Unlike lopdf's lenient `get_plain_content`, reported decoder failures and
/// unsupported filters are errors rather than a reason to return encoded bytes
/// as if they were plain.
pub(super) fn decoded_stream_content_cow<'a>(
    stream: &'a Stream,
    max_size: Option<usize>,
    limit_code: &'static str,
    context: &str,
) -> PyResult<Cow<'a, [u8]>> {
    let reject_raw = |length: usize, limit: usize| {
        limit_err(
            limit_code,
            format!("{context} is {length} bytes, exceeding the {limit}-byte decoded-size limit"),
        )
    };
    if !stream.dict.has(b"Filter") {
        if let Some(limit) = max_size
            && stream.content.len() > limit
        {
            return Err(reject_raw(stream.content.len(), limit));
        }
        return Ok(Cow::Borrowed(&stream.content));
    }
    validate_stream_filter_count(stream, context)?;

    let raw_filters = stream.filters().map_err(|error| {
        PdfError::new_err(format!(
            "{context} has an invalid Filter: {}",
            LopdfErrorChain(&error)
        ))
    })?;
    if raw_filters.is_empty() {
        if let Some(limit) = max_size
            && stream.content.len() > limit
        {
            return Err(reject_raw(stream.content.len(), limit));
        }
        return Ok(Cow::Borrowed(&stream.content));
    }
    let normalized_filters: Vec<&[u8]> = raw_filters
        .iter()
        .map(|filter| canonical_filter_name(filter))
        .collect();
    let decode_error = |error: lopdf::Error| {
        if matches!(
            error,
            lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
        ) && let Some(limit) = max_size
        {
            limit_err(
                limit_code,
                format!("{context} exceeds the {limit}-byte decoded-size limit"),
            )
        } else {
            PdfError::new_err(format!(
                "failed to decode {context}: {}",
                LopdfErrorChain(&error)
            ))
        }
    };
    let decode = |candidate: &Stream| match max_size {
        Some(limit) => candidate
            .decompressed_content_with_limit(limit)
            .map_err(decode_error),
        None => candidate.decompressed_content().map_err(decode_error),
    };
    if raw_filters
        .iter()
        .zip(&normalized_filters)
        .all(|(raw, normalized)| *raw == *normalized)
    {
        return decode(stream).map(Cow::Owned);
    }

    let checked_stream = normalized_stream_copy(stream, &normalized_filters, context)?;
    decode(&checked_stream).map(Cow::Owned)
}

/// Materialize a strictly decoded stream for Python-owned return data.
pub(super) fn decoded_stream_content(
    stream: &Stream,
    max_size: Option<usize>,
    limit_code: &'static str,
    context: &str,
) -> PyResult<Vec<u8>> {
    match decoded_stream_content_cow(stream, max_size, limit_code, context)? {
        Cow::Owned(data) => Ok(data),
        Cow::Borrowed(data) => {
            let mut owned = Vec::new();
            owned.try_reserve_exact(data.len()).map_err(|error| {
                PdfError::new_err(format!("failed to allocate {context}: {error}"))
            })?;
            owned.extend_from_slice(data);
            Ok(owned)
        }
    }
}
