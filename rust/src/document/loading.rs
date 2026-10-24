//! Opening documents, authentication, and fast metadata probes.

use super::*;

/// Recover skipped objects and decide whether hayro may read the original bytes.
///
/// Encrypted input and input whose cross-reference data lopdf recovered never
/// keep their original bytes: hayro would decrypt or recover them on its own
/// and could resolve different objects. `object_recovery` decides the rest.
pub(super) fn original_bytes_match(
    doc: &mut Document,
    recovery: Option<XrefRecovery>,
    data: &[u8],
) -> PyResult<bool> {
    if recovery.is_some() || doc.is_encrypted() || doc.was_encrypted() {
        return Ok(false);
    }
    object_recovery::reconcile_loaded_objects(doc, data)
}

/// The original-byte rendering source for a load that kept its bytes.
///
/// Image compression maps hayro's object IDs to lopdf's, which no longer
/// match the original bytes once loading renumbered objects, so such a load
/// keeps none.
fn hayro_source(original: Option<&Arc<Vec<u8>>>, max_size: Option<usize>) -> HayroSource {
    original.map_or(HayroSource::Unavailable, |data| {
        HayroSource::from_shared(Arc::clone(data), max_size)
    })
}

/// Implements [`_Document::new`].
pub(super) fn new(
    max_text_size: Option<usize>,
    max_interpretation_size: Option<usize>,
    max_text_glyphs: Option<usize>,
) -> PyResult<_Document> {
    validate_interpretation_limit(max_interpretation_size)?;
    validate_text_glyph_limit(max_text_glyphs)?;
    let mut document = _Document::from_doc(
        Document::with_version("1.7"),
        max_text_size,
        max_interpretation_size,
        max_text_glyphs,
    );
    document.ensure_page_tree().map_err(to_py_err)?;
    Ok(document)
}

/// Implements [`_Document::load`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn load(
    py: Python<'_>,
    path: &str,
    password: Option<String>,
    max_decompressed_size: Option<usize>,
    max_page_content_size: Option<usize>,
    max_file_size: Option<usize>,
    max_pages: Option<usize>,
    max_objects: Option<usize>,
    max_total_decompressed_size: Option<usize>,
    max_object_depth: Option<usize>,
    max_text_size: Option<usize>,
    max_interpretation_size: Option<usize>,
    max_text_glyphs: Option<usize>,
) -> PyResult<_Document> {
    validate_password_input(password.as_deref(), "password")?;
    validate_interpretation_limit(max_interpretation_size)?;
    validate_text_glyph_limit(max_text_glyphs)?;
    let limits = DocumentLimits {
        max_file_size,
        max_pages,
        max_objects,
        max_decompressed_size,
        max_page_content_size,
        max_total_decompressed_size,
        max_object_depth,
        max_text_size,
        max_interpretation_size,
        max_text_glyphs,
    };
    let (decoder_bound, decoder_limit_code) = match (
        limits.max_decompressed_size,
        limits.max_total_decompressed_size,
    ) {
        (Some(per_stream), Some(total)) if total < per_stream => {
            (Some(total), "total_decompressed_size")
        }
        (Some(per_stream), _) => (Some(per_stream), "decompressed_size"),
        (None, Some(total)) => (Some(total), "total_decompressed_size"),
        (None, None) => (None, "decompressed_size"),
    };
    let options = LoadOptions {
        password,
        max_decompressed_size: decoder_bound,
        ..Default::default()
    };
    py.detach(|| {
        let data = read_input(path, limits.max_file_size)?;
        reject_empty_input(&data, Some(&format!("failed to load {path}")))?;
        let (mut doc, recovery) = recovery::load_document(&data, options).map_err(|error| {
            recovery_load_err(
                Some(&format!("failed to load {path}")),
                &error,
                decoder_limit_code,
            )
        })?;
        // Compare objects with their cross-reference entries before any
        // object changes number.
        let original_bytes_match = original_bytes_match(&mut doc, recovery, &data)?;
        let renumbered = normalize_object_numbers(&mut doc)?;
        let decrypted = !doc.is_encrypted();
        let page_ids = validate_structural_limits(&doc, limits, decrypted)?;
        if decrypted {
            validate_decompression_limits(
                &doc,
                page_ids.as_deref(),
                limits.max_decompressed_size,
                limits.max_page_content_size,
                limits.max_total_decompressed_size,
            )?;
        }
        let original = (original_bytes_match && !renumbered).then(|| Arc::new(data));
        Ok(_Document::from_loaded_doc(
            doc,
            hayro_source(original.as_ref(), limits.max_interpretation_size),
            original.map(|data| IncrementalBase::new(data, decoder_bound)),
            limits.max_text_size,
            limits.max_interpretation_size,
            limits.max_text_glyphs,
            recovery,
        ))
    })
}

/// Implements [`_Document::load_bytes`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn load_bytes(
    py: Python<'_>,
    data: &[u8],
    password: Option<String>,
    max_decompressed_size: Option<usize>,
    max_page_content_size: Option<usize>,
    max_file_size: Option<usize>,
    max_pages: Option<usize>,
    max_objects: Option<usize>,
    max_total_decompressed_size: Option<usize>,
    max_object_depth: Option<usize>,
    max_text_size: Option<usize>,
    max_interpretation_size: Option<usize>,
    max_text_glyphs: Option<usize>,
) -> PyResult<_Document> {
    validate_password_input(password.as_deref(), "password")?;
    validate_interpretation_limit(max_interpretation_size)?;
    validate_text_glyph_limit(max_text_glyphs)?;
    let limits = DocumentLimits {
        max_file_size,
        max_pages,
        max_objects,
        max_decompressed_size,
        max_page_content_size,
        max_total_decompressed_size,
        max_object_depth,
        max_text_size,
        max_interpretation_size,
        max_text_glyphs,
    };
    validate_input_size(data, limits.max_file_size)?;
    reject_empty_input(data, None)?;
    let (decoder_bound, decoder_limit_code) = match (
        limits.max_decompressed_size,
        limits.max_total_decompressed_size,
    ) {
        (Some(per_stream), Some(total)) if total < per_stream => {
            (Some(total), "total_decompressed_size")
        }
        (Some(per_stream), _) => (Some(per_stream), "decompressed_size"),
        (None, Some(total)) => (Some(total), "total_decompressed_size"),
        (None, None) => (None, "decompressed_size"),
    };
    let options = LoadOptions {
        password,
        max_decompressed_size: decoder_bound,
        ..Default::default()
    };
    py.detach(|| {
        let (mut doc, recovery) = recovery::load_document(data, options)
            .map_err(|error| recovery_load_err(None, &error, decoder_limit_code))?;
        // Compare objects with their cross-reference entries before any
        // object changes number.
        let original_bytes_match = original_bytes_match(&mut doc, recovery, data)?;
        let renumbered = normalize_object_numbers(&mut doc)?;
        let decrypted = !doc.is_encrypted();
        let page_ids = validate_structural_limits(&doc, limits, decrypted)?;
        if decrypted {
            validate_decompression_limits(
                &doc,
                page_ids.as_deref(),
                limits.max_decompressed_size,
                limits.max_page_content_size,
                limits.max_total_decompressed_size,
            )?;
        }
        let original = if original_bytes_match && !renumbered {
            let mut owned = Vec::new();
            owned.try_reserve_exact(data.len()).map_err(|error| {
                PdfError::new_err(format!("failed to allocate the original input: {error}"))
            })?;
            owned.extend_from_slice(data);
            Some(Arc::new(owned))
        } else {
            None
        };
        Ok(_Document::from_loaded_doc(
            doc,
            hayro_source(original.as_ref(), limits.max_interpretation_size),
            original.map(|data| IncrementalBase::new(data, decoder_bound)),
            limits.max_text_size,
            limits.max_interpretation_size,
            limits.max_text_glyphs,
            recovery,
        ))
    })
}

/// Implements [`_Document::load_metadata`].
pub(super) fn load_metadata(
    py: Python<'_>,
    path: &str,
    password: Option<String>,
    max_file_size: Option<usize>,
) -> PyResult<MetadataTuple> {
    validate_password_input(password.as_deref(), "password")?;
    py.detach(|| {
        let data = read_input(path, max_file_size)?;
        reject_empty_input(&data, Some(&format!("failed to load {path}")))?;
        let (meta, recovery) =
            recovery::load_metadata(&data, password.as_deref()).map_err(|error| {
                recovery_load_err(
                    Some(&format!("failed to load {path}")),
                    &error,
                    "decompressed_size",
                )
            })?;
        let (metadata, page_count, version, encrypted) = pdf_metadata_to_tuple(meta)?;
        Ok((
            metadata,
            page_count,
            version,
            encrypted,
            recovery.map(XrefRecovery::warning),
        ))
    })
}

/// Implements [`_Document::load_metadata_bytes`].
pub(super) fn load_metadata_bytes(
    py: Python<'_>,
    data: &[u8],
    password: Option<String>,
    max_file_size: Option<usize>,
) -> PyResult<MetadataTuple> {
    validate_password_input(password.as_deref(), "password")?;
    validate_input_size(data, max_file_size)?;
    reject_empty_input(data, None)?;
    py.detach(|| {
        let (meta, recovery) = recovery::load_metadata(data, password.as_deref())
            .map_err(|error| recovery_load_err(None, &error, "decompressed_size"))?;
        let (metadata, page_count, version, encrypted) = pdf_metadata_to_tuple(meta)?;
        Ok((
            metadata,
            page_count,
            version,
            encrypted,
            recovery.map(XrefRecovery::warning),
        ))
    })
}
