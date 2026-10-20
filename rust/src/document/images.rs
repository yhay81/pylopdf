//! Image insertion and image compression.

use super::*;

/// Default public boundaries for encoded and decoded image insertion input.
pub(super) const DEFAULT_MAX_IMAGE_INPUT_SIZE: usize = 64 * 1024 * 1024;

pub(super) const DEFAULT_MAX_IMAGE_PIXELS: u64 = 64_000_000;

/// Image input whose XObject a document may already have placed.
pub(super) enum ImageInput {
    /// Accepted JPEG or PNG bytes.
    Encoded(Vec<u8>),
    /// A Pixmap's straight-alpha RGBA8 samples.
    Rgba {
        width: u32,
        height: u32,
        data: Arc<Vec<u8>>,
    },
}

impl ImageInput {
    pub(super) fn digest(&self) -> placement_reuse::ImageDigest {
        match self {
            Self::Encoded(data) => placement_reuse::encoded_image_digest(data),
            Self::Rgba {
                width,
                height,
                data,
            } => placement_reuse::rgba_image_digest(*width, *height, data),
        }
    }

    pub(super) fn parse(self) -> PyResult<draw::ImageParts> {
        match self {
            Self::Encoded(data) => draw::parse_image(data)
                .map_err(PdfError::new_err)?
                .ok_or_else(|| {
                    PdfError::new_err(
                        "unsupported image format (pass JPEG or PNG; convert other formats with Pillow or similar first)",
                    )
                }),
            Self::Rgba {
                width,
                height,
                data,
            } => draw::rgba_parts(width, height, &data).map_err(PdfError::new_err),
        }
    }
}

/// An image to place: one placed before, or new XObject data.
pub(super) enum PageImage {
    Placed(PlacedImage),
    New(draw::ImageParts),
}

/// Read encoded image input without admitting more than one byte beyond its budget.
pub(super) fn read_image_input(path: &str, max_size: Option<usize>) -> PyResult<Vec<u8>> {
    read_bounded_input(
        path,
        max_size,
        "image_input_size",
        "encoded image input",
        "failed to load image",
    )
}

pub(super) fn validate_image_input(
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

impl _Document {
    /// Draw an image into display `rect`, placing the XObject of identical
    /// earlier input again instead of adding another.
    #[allow(clippy::too_many_arguments)] // Mirrors Python's keyword-oriented drawing API.
    pub(super) fn place_image(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        input: ImageInput,
        image_rotation: i64,
        keep_proportion: bool,
        overlay: bool,
    ) -> PyResult<()> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
        let digest = py.detach(|| input.digest());
        let image = match self.placements.image(&self.doc, &digest) {
            Some(placed) => PageImage::Placed(placed),
            None => PageImage::New(py.detach(|| input.parse())?),
        };
        let (width, height) = match &image {
            PageImage::Placed(placed) => (placed.width(), placed.height()),
            PageImage::New(parts) => (parts.width, parts.height),
        };
        let matrix = draw::image_placement_matrix(
            crop,
            rotation,
            [rect.0, rect.1, rect.2, rect.3],
            width,
            height,
            keep_proportion,
            image_rotation,
        );
        let remember = matches!(image, PageImage::New(_)) && self.placements.reserve_image();
        self.invalidate_hayro_pdf_keeping_placements();
        let added = py.detach(|| -> PyResult<_> {
            let (xobj_id, added) = match image {
                PageImage::Placed(placed) => (placed.id(), None),
                PageImage::New(parts) => {
                    let id =
                        draw::add_image_xobject(&mut self.doc, parts).map_err(PdfError::new_err)?;
                    (id, PlacedImage::read(&self.doc, id))
                }
            };
            let ops = draw::draw_ops(matrix, drawing_plan.resource.name());
            self.commit_drawing_resource(page_id, drawing_plan.resource, xobj_id)?;
            self.push_page_content(page_id, ops, drawing_plan.content)?;
            Ok(added)
        })?;
        if remember && let Some(added) = added {
            self.placements.remember_image(digest, added);
        }
        Ok(())
    }
}

/// Implements [`_Document::compress_images`].
pub(super) fn compress_images(
    this: &mut _Document,
    py: Python<'_>,
    target_dpi: Option<f64>,
    quality: u8,
) -> PyResult<image_compression::CompressionResult> {
    let settings = this.interpreter_settings();
    let usages = this.contain_renderer_panics(|this| {
        let pdf = this.hayro_view_for(PageSelection::All)?;
        py.detach(|| crate::extract::collect_image_usages(pdf, settings))
            .map_err(PdfError::new_err)
    })?;
    let mut edited = this.doc.clone();
    let result = py
        .detach(|| image_compression::compress_images(&mut edited, &usages, target_dpi, quality))
        .map_err(PdfError::new_err)?;
    if result.1 > 0 {
        this.doc.replace(edited);
        this.invalidate_hayro_pdf();
    }
    Ok(result)
}

/// Implements [`_Document::insert_image`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_image(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    data: Vec<u8>,
    image_rotation: i64,
    keep_proportion: bool,
    overlay: bool,
    max_size: Option<usize>,
    max_pixels: Option<u64>,
) -> PyResult<()> {
    validate_image_input(&data, max_size, max_pixels)?;
    this.place_image(
        py,
        page_number,
        rect,
        ImageInput::Encoded(data),
        image_rotation,
        keep_proportion,
        overlay,
    )
}

/// Implements [`_Document::insert_image_file`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_image_file(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    path: &str,
    image_rotation: i64,
    keep_proportion: bool,
    overlay: bool,
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
    let data = py.detach(|| read_image_input(path, max_size))?;
    this.insert_image(
        py,
        page_number,
        rect,
        data,
        image_rotation,
        keep_proportion,
        overlay,
        max_size,
        max_pixels,
    )
}

/// Implements [`_Document::insert_pixmap`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_pixmap(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    pixmap: PyRef<'_, Pixmap>,
    image_rotation: i64,
    keep_proportion: bool,
    overlay: bool,
) -> PyResult<()> {
    let input = ImageInput::Rgba {
        width: pixmap.width,
        height: pixmap.height,
        data: Arc::clone(&pixmap.data),
    };
    drop(pixmap);
    this.place_image(
        py,
        page_number,
        rect,
        input,
        image_rotation,
        keep_proportion,
        overlay,
    )
}
