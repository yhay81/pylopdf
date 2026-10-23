//! Image insertion and image compression.

use super::*;

/// Image input whose XObject a document may already have placed.
pub(crate) enum ImageInput {
    /// Accepted JPEG or PNG bytes.
    Encoded(Vec<u8>),
    /// A Pixmap's gray or RGB samples, with or without straight alpha.
    Pixmap {
        width: u32,
        height: u32,
        layout: Layout,
        data: Arc<Vec<u8>>,
    },
}

impl ImageInput {
    pub(crate) fn digest(&self) -> placement_reuse::ImageDigest {
        match self {
            Self::Encoded(data) => placement_reuse::encoded_image_digest(data),
            Self::Pixmap {
                width,
                height,
                layout,
                data,
            } => placement_reuse::pixmap_image_digest(*width, *height, *layout, data),
        }
    }

    pub(crate) fn parse(self) -> PyResult<draw::ImageParts> {
        match self {
            Self::Encoded(data) => draw::parse_image(data)
                .map_err(PdfError::new_err)?
                .ok_or_else(|| {
                    PdfError::new_err(
                        "unsupported image format (pass JPEG or PNG; convert other formats with Pillow or similar first)",
                    )
                }),
            Self::Pixmap {
                width,
                height,
                layout,
                data,
            } => draw::pixmap_parts(width, height, layout, &data).map_err(PdfError::new_err),
        }
    }
}

/// Read encoded image input without admitting more than one byte beyond its budget.
pub(crate) fn read_image_input(path: &str, max_size: Option<usize>) -> PyResult<Vec<u8>> {
    read_bounded_input(
        path,
        max_size,
        "image_input_size",
        "encoded image input",
        "failed to load image",
    )
}

pub(crate) fn validate_image_input(
    data: &[u8],
    max_size: Option<usize>,
    max_pixels: Option<u64>,
) -> PyResult<()> {
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    if max_pixels == Some(0) {
        return Err(PyValueError::new_err(
            "max_pixels must be a positive integer or None",
        ));
    }
    if let Some(limit) = max_size
        && data.len() > limit
    {
        return Err(limit_err(
            "image_input_size",
            format!(
                "encoded image input is {} bytes, exceeding the {limit}-byte limit",
                data.len()
            ),
        ));
    }
    if let (Some(limit), Some(pixels)) = (max_pixels, draw::png_pixel_count(data))
        && pixels > limit
    {
        return Err(limit_err(
            "image_pixel_count",
            format!("PNG image contains {pixels} pixels, exceeding the {limit}-pixel limit"),
        ));
    }
    Ok(())
}

/// Implements [`_Document::rewrite_images`].
pub(super) fn rewrite_images(
    this: &mut _Document,
    py: Python<'_>,
    options: image_compression::RewriteOptions,
) -> PyResult<image_compression::CompressionResult> {
    let settings = this.interpreter_settings();
    let usages = this.contain_renderer_panics(|this| {
        let pdf = this.hayro_view_for(PageSelection::All)?;
        py.detach(|| crate::extract::collect_image_usages(pdf, settings))
            .map_err(PdfError::new_err)
    })?;
    let mut edited = this.doc.clone();
    let result = py
        .detach(|| image_compression::rewrite_images(&mut edited, &usages, options))
        .map_err(PdfError::new_err)?;
    if result.1 > 0 {
        this.doc.replace(edited);
        this.invalidate_hayro_pdf();
    }
    Ok(result)
}
