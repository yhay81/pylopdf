//! Bounded reads and validation of caller input.

use super::*;

/// Bound password KDF input before potentially expensive encryption work.
pub(super) const MAX_PASSWORD_INPUT_BYTES: usize = 127;

pub(crate) const DEFAULT_MAX_FONT_INPUT_SIZE: usize = 64 * 1024 * 1024;

pub(crate) const DEFAULT_MAX_GENERATED_TEXT_SIZE: usize = 1024 * 1024;

/// Charge one caller-text budget with a stable machine-readable limit code.
pub(super) fn add_input_text_budget(
    total: &mut usize,
    amount: usize,
    limit: usize,
    code: &'static str,
    label: &str,
) -> PyResult<()> {
    *total = total.checked_add(amount).ok_or_else(|| {
        limit_err(
            code,
            format!("{label} exceeds the platform text-size limit"),
        )
    })?;
    if *total > limit {
        return Err(limit_err(
            code,
            format!("{label} exceeds the {limit}-byte safety limit"),
        ));
    }
    Ok(())
}

/// Compute lopdf's ASCII-or-UTF-16BE text-string size without allocating it.
pub(super) fn pdf_text_string_len(value: &str) -> Option<usize> {
    if value.is_ascii() {
        return Some(value.len());
    }
    value
        .encode_utf16()
        .try_fold(2usize, |size, _| size.checked_add(2))
}

/// Read a regular file with fallible retained-buffer growth.
pub(crate) fn read_file_fallibly(
    mut file: std::fs::File,
    initial_capacity: usize,
    max_read: Option<usize>,
    allocation_label: &str,
) -> std::io::Result<Vec<u8>> {
    const READ_CHUNK_BYTES: usize = 64 * 1024;

    let mut data = Vec::new();
    data.try_reserve_exact(initial_capacity).map_err(|error| {
        std::io::Error::other(format!("failed to allocate {allocation_label}: {error}"))
    })?;
    let mut buffer = [0u8; READ_CHUNK_BYTES];
    loop {
        let read_size = max_read
            .map(|limit| limit.saturating_sub(data.len()).min(buffer.len()))
            .unwrap_or(buffer.len());
        if read_size == 0 {
            break;
        }
        let amount = match file.read(&mut buffer[..read_size]) {
            Ok(0) => break,
            Ok(amount) => amount,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        };
        data.try_reserve_exact(amount).map_err(|error| {
            std::io::Error::other(format!("failed to allocate {allocation_label}: {error}"))
        })?;
        data.extend_from_slice(&buffer[..amount]);
    }
    Ok(data)
}

/// Read a path without admitting more than one byte beyond a caller's budget.
pub(super) fn read_bounded_input(
    path: &str,
    max_size: Option<usize>,
    limit_code: &'static str,
    size_label: &str,
    error_prefix: &str,
) -> PyResult<Vec<u8>> {
    read_bounded_file(path, max_size, limit_code, size_label, error_prefix).map(|(data, _)| data)
}

/// Read a bounded path and return the metadata of the opened file.
pub(crate) fn read_bounded_file(
    path: &str,
    max_size: Option<usize>,
    limit_code: &'static str,
    size_label: &str,
    error_prefix: &str,
) -> PyResult<(Vec<u8>, std::fs::Metadata)> {
    let io_error = |error| PdfError::new_err(format!("{error_prefix} {path}: {error}"));
    let file = std::fs::File::open(path).map_err(io_error)?;
    let metadata = file.metadata().map_err(io_error)?;
    validate_file_size(metadata.len(), max_size, limit_code, size_label)?;
    let read_limit = max_size.map(|limit| limit.saturating_add(1));
    let initial_capacity = usize::try_from(metadata.len())
        .unwrap_or(0)
        .min(read_limit.unwrap_or(usize::MAX));
    let data =
        read_file_fallibly(file, initial_capacity, read_limit, size_label).map_err(io_error)?;
    if let Some(limit) = max_size
        && data.len() > limit
    {
        return Err(limit_err(
            limit_code,
            format!("{size_label} exceeds the {limit}-byte limit while being read"),
        ));
    }
    Ok((data, metadata))
}

/// Reject a file whose reported size already exceeds a caller's budget.
pub(crate) fn validate_file_size(
    size: u64,
    max_size: Option<usize>,
    limit_code: &'static str,
    size_label: &str,
) -> PyResult<()> {
    if let Some(limit) = max_size
        && size > limit as u64
    {
        return Err(limit_err(
            limit_code,
            format!("{size_label} is {size} bytes, exceeding the {limit}-byte limit"),
        ));
    }
    Ok(())
}

/// Read PDF input without admitting more than one byte beyond its file budget.
pub(super) fn read_input(path: &str, max_file_size: Option<usize>) -> PyResult<Vec<u8>> {
    if max_file_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_file_size must be a positive integer or None",
        ));
    }
    read_bounded_input(
        path,
        max_file_size,
        "file_size",
        "PDF file",
        "failed to load",
    )
}

/// Reject an in-memory PDF before parsing when it exceeds the file budget.
pub(super) fn validate_input_size(data: &[u8], max_file_size: Option<usize>) -> PyResult<()> {
    if max_file_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_file_size must be a positive integer or None",
        ));
    }
    if let Some(limit) = max_file_size
        && data.len() > limit
    {
        return Err(limit_err(
            "file_size",
            format!(
                "PDF input is {} bytes, exceeding the configured limit of {limit}",
                data.len()
            ),
        ));
    }
    Ok(())
}

/// Validate the renderer/extractor snapshot budget at the private boundary.
pub(super) fn validate_interpretation_limit(
    max_interpretation_size: Option<usize>,
) -> PyResult<()> {
    if max_interpretation_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_interpretation_size must be a positive integer or None",
        ));
    }
    Ok(())
}

/// Validate the positioned-text glyph budget at the private boundary.
pub(super) fn validate_text_glyph_limit(max_text_glyphs: Option<usize>) -> PyResult<()> {
    if max_text_glyphs == Some(0) {
        return Err(PyValueError::new_err(
            "max_text_glyphs must be a positive integer or None",
        ));
    }
    Ok(())
}

/// Read OpenType font input without admitting more than one byte beyond its budget.
pub(super) fn read_font_input(path: &str, max_font_size: Option<usize>) -> PyResult<Vec<u8>> {
    read_font_file(path, max_font_size).map(|(data, _)| data)
}

/// Read bounded OpenType font input and return the metadata of the opened file.
pub(crate) fn read_font_file(
    path: &str,
    max_font_size: Option<usize>,
) -> PyResult<(Vec<u8>, std::fs::Metadata)> {
    read_bounded_file(
        path,
        max_font_size,
        "font_input_size",
        "font input",
        "failed to load font",
    )
}

/// Inspect a font path without reading it, failing as a bounded read would
/// for a missing file or a reported size above the font boundary.
pub(crate) fn font_file_metadata(
    path: &str,
    max_font_size: Option<usize>,
) -> PyResult<std::fs::Metadata> {
    let metadata = std::fs::metadata(path)
        .map_err(|error| PdfError::new_err(format!("failed to load font {path}: {error}")))?;
    validate_file_size(
        metadata.len(),
        max_font_size,
        "font_input_size",
        "font input",
    )?;
    Ok(metadata)
}

pub(crate) fn validate_font_input(
    data: Option<&[u8]>,
    max_font_size: Option<usize>,
) -> PyResult<()> {
    if max_font_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_font_size must be a positive integer or None",
        ));
    }
    if let (Some(data), Some(limit)) = (data, max_font_size)
        && data.len() > limit
    {
        return Err(limit_err(
            "font_input_size",
            format!(
                "font input is {} bytes, exceeding the {limit}-byte limit",
                data.len()
            ),
        ));
    }
    Ok(())
}

pub(crate) fn validate_generated_text_input<'a>(
    chunks: impl IntoIterator<Item = &'a [u8]>,
    max_text_size: Option<usize>,
) -> PyResult<()> {
    let Some(limit) = max_text_size else {
        return Ok(());
    };
    if limit == 0 {
        return Err(PyValueError::new_err(
            "max_text_size must be a positive integer or None",
        ));
    }
    let mut total = 0usize;
    for chunk in chunks {
        total = total.checked_add(chunk.len()).ok_or_else(|| {
            limit_err(
                "text_input_size",
                format!("text input exceeds the {limit}-byte UTF-8 limit"),
            )
        })?;
        if total > limit {
            return Err(limit_err(
                "text_input_size",
                format!("text input exceeds the {limit}-byte UTF-8 limit"),
            ));
        }
    }
    Ok(())
}

pub(crate) fn validate_generated_text_line_count(
    line_count: usize,
    max_text_size: Option<usize>,
) -> PyResult<()> {
    if max_text_size.is_some() && line_count > crate::layout::MAX_GENERATED_TEXT_LINES {
        return Err(limit_err(
            "text_line_count",
            format!(
                "text input exceeds the {}-line safety limit",
                crate::layout::MAX_GENERATED_TEXT_LINES
            ),
        ));
    }
    Ok(())
}

pub(crate) fn generated_text_err(error: String) -> PyErr {
    if error == crate::layout::TEXT_LINE_LIMIT_ERROR {
        limit_err(
            "text_line_count",
            format!(
                "text layout exceeds the {}-line safety limit",
                crate::layout::MAX_GENERATED_TEXT_LINES
            ),
        )
    } else {
        PdfError::new_err(error)
    }
}

pub(super) fn validate_password_input(password: Option<&str>, label: &str) -> PyResult<()> {
    if let Some(password) = password
        && password.len() > MAX_PASSWORD_INPUT_BYTES
    {
        return Err(limit_err(
            "password_input_size",
            format!("{label} exceeds the {MAX_PASSWORD_INPUT_BYTES}-byte UTF-8 safety limit"),
        ));
    }
    Ok(())
}
