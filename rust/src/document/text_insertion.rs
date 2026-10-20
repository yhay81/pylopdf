//! Text insertion with Standard 14 and embedded fonts, OCR text layers,
//! and generated-PDF imports.

use super::*;

/// Bound generated invisible OCR-layer content per call.
pub(super) const MAX_OCR_LAYER_WORDS: usize = 4_096;

pub(super) const MAX_OCR_LAYER_TEXT_BYTES: usize = 1024 * 1024;

impl _Document {
    /// Draw validated subset-embedded text; see `insert_embedded_text`.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn draw_embedded_text(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        point: (f64, f64),
        lines: &[String],
        face: GenerationFace,
        fontsize: f64,
        lineheight: Option<f64>,
        color: (f64, f64, f64),
        overlay: bool,
    ) -> PyResult<usize> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
        let page_size = draw::display_size(crop, rotation);
        let (generated, drawn) = py.detach(|| {
            generate::embedded_text_page(
                page_size, point, lines, &face, fontsize, lineheight, color,
            )
            .map_err(PdfError::new_err)
        })?;
        let mut generated_doc = generated_pdf::load(&generated)
            .map_err(|error| lopdf_err(Some("failed to import generated text"), &error))?;
        face.name_subset(&mut generated_doc);
        let placement = PagePlacement {
            rect: [0.0, 0.0, page_size.0, page_size.1],
            keep_proportion: false,
        };
        let form_plan =
            py.detach(|| prepare_page_form_import(self.doc.max_id, generated_doc, 1))?;
        self.invalidate_hayro_pdf_keeping_placements();
        py.detach(|| {
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                PageForm::Import(form_plan),
                placement,
                drawing_plan,
            )
        })?;
        Ok(drawn)
    }

    /// Lay out and draw validated subset-embedded text inside a display rectangle.
    ///
    /// An overflow returns its negative spare height without mutating the PDF.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn draw_embedded_textbox(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        text: &str,
        face: GenerationFace,
        fontsize: f64,
        lineheight: Option<f64>,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_size = draw::display_size(crop, rotation);
        let rect_array = [rect.0, rect.1, rect.2, rect.3];
        let (generated, spare_height) = py.detach(|| {
            generate::embedded_textbox_page(
                page_size,
                rect_array,
                text,
                &face,
                fontsize,
                lineheight,
                align,
                color,
                max_text_size.map(|_| crate::layout::MAX_GENERATED_TEXT_LINES),
            )
            .map_err(generated_text_err)
        })?;
        let Some(generated) = generated else {
            return Ok(spare_height);
        };
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
        let mut generated_doc = generated_pdf::load(&generated)
            .map_err(|error| lopdf_err(Some("failed to import generated text"), &error))?;
        face.name_subset(&mut generated_doc);
        let placement = PagePlacement {
            rect: [0.0, 0.0, page_size.0, page_size.1],
            keep_proportion: false,
        };
        let form_plan =
            py.detach(|| prepare_page_form_import(self.doc.max_id, generated_doc, 1))?;
        self.invalidate_hayro_pdf_keeping_placements();
        py.detach(|| {
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                PageForm::Import(form_plan),
                placement,
                drawing_plan,
            )
        })?;
        Ok(spare_height)
    }
}

/// Implements [`_Document::insert_ocr_layer`].
pub(super) fn insert_ocr_layer(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    words: Vec<(f64, f64, f64, f64, String)>,
    text_rotation: u16,
) -> PyResult<()> {
    if !matches!(text_rotation, 0 | 90 | 180 | 270) {
        return Err(PdfError::new_err(
            "text_rotation must be 0, 90, 180, or 270",
        ));
    }
    let (page_id, drawing_plan, expected_font_number, to_unicode, ops) = py.detach(|| {
        if words.len() > MAX_OCR_LAYER_WORDS {
            return Err(PdfError::new_err(format!(
                "cannot insert more than {MAX_OCR_LAYER_WORDS} OCR words per call"
            )));
        }
        let text_bytes = words.iter().try_fold(0usize, |total, word| {
            total
                .checked_add(word.4.len())
                .ok_or_else(|| PdfError::new_err("OCR layer text exceeds the platform size limit"))
        })?;
        if text_bytes > MAX_OCR_LAYER_TEXT_BYTES {
            return Err(PdfError::new_err(format!(
                "OCR layer text exceeds the {MAX_OCR_LAYER_TEXT_BYTES}-byte safety limit"
            )));
        }
        let (crop, rotation) = this.page_display_geometry(page_number)?;
        let (page_id, drawing_plan) =
            this.prepare_page_drawing(page_number, true, PageResourceKind::Font)?;
        let cid_map = ocr::assign_cids(&words).map_err(PdfError::new_err)?;
        let to_unicode = ocr::build_to_unicode(&cid_map);
        let expected_font_number =
            this.doc.max_id.checked_add(4).ok_or_else(|| {
                PdfError::new_err("OCR layer objects exceed the PDF object-ID limit")
            })?;
        let ops = ocr::ocr_ops(
            crop,
            rotation,
            &words,
            &cid_map,
            drawing_plan.resource.name(),
            text_rotation,
        );
        Ok((page_id, drawing_plan, expected_font_number, to_unicode, ops))
    })?;

    // From this point onward malformed page/resource state could leave a
    // partial edit, so invalidate before the first PDF object is added.
    this.invalidate_hayro_pdf_keeping_placements();
    py.detach(|| {
        let font_id = ocr::add_ocr_font(&mut this.doc, to_unicode);
        debug_assert_eq!(font_id.0, expected_font_number);
        this.commit_drawing_resource(page_id, drawing_plan.resource, font_id)?;
        this.push_page_content(page_id, ops, drawing_plan.content)
    })
}

/// Implements [`_Document::insert_page_text`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_page_text(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    point: (f64, f64),
    mut lines: Vec<Vec<u8>>,
    base_font: &str,
    winansi: bool,
    fontsize: f64,
    lineheight: Option<f64>,
    color: (f64, f64, f64),
    overlay: bool,
    max_text_size: Option<usize>,
) -> PyResult<usize> {
    validate_generated_text_line_count(lines.len(), max_text_size)?;
    validate_generated_text_input(lines.iter().map(Vec::as_slice), max_text_size)?;
    let font = draw::base14_font(base_font)
        .ok_or_else(|| PdfError::new_err(format!("unsupported Standard 14 font: {base_font}")))?;
    let metrics = draw::standard_line_metrics(font);
    let leading = metrics
        .leading(fontsize, lineheight)
        .map_err(PdfError::new_err)?;
    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let (_, page_height) = draw::display_size(crop, rotation);
    lines.truncate(metrics.lines_on_page(lines.len(), page_height - point.1, fontsize, lineheight));
    let drawn = lines.len();
    let (page_id, drawing_plan) =
        this.prepare_page_drawing(page_number, overlay, PageResourceKind::Font)?;
    this.invalidate_hayro_pdf_keeping_placements();
    py.detach(|| {
        let mut font_dict = dictionary! {
            "Type" => "Font",
            "Subtype" => "Type1",
            "BaseFont" => Object::Name(base_font.as_bytes().to_vec()),
        };
        if winansi {
            font_dict.set("Encoding", Object::Name(b"WinAnsiEncoding".to_vec()));
        }
        let font_id = this.doc.add_object(font_dict);
        let ops = draw::text_ops(
            crop,
            rotation,
            point,
            &lines,
            drawing_plan.resource.name(),
            fontsize,
            leading,
            draw::DeviceColor::Rgb(color.0, color.1, color.2),
        );
        this.commit_drawing_resource(page_id, drawing_plan.resource, font_id)?;
        this.push_page_content(page_id, ops, drawing_plan.content)
    })?;
    Ok(drawn)
}

/// Implements [`_Document::insert_page_textbox`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_page_textbox(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    text: &str,
    base_font: &str,
    winansi: bool,
    fontsize: f64,
    lineheight: Option<f64>,
    align: u8,
    color: (f64, f64, f64),
    overlay: bool,
    max_text_size: Option<usize>,
) -> PyResult<f64> {
    validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
    validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
    let rect = [rect.0, rect.1, rect.2, rect.3];
    let layout = py.detach(|| {
        draw::standard_textbox_layout(
            text,
            (rect[2] - rect[0], rect[3] - rect[1]),
            base_font,
            fontsize,
            lineheight,
            align == 3,
            max_text_size.map(|_| crate::layout::MAX_GENERATED_TEXT_LINES),
        )
        .map_err(generated_text_err)
    })?;
    if !layout.fits() {
        return Ok(layout.spare_height);
    }

    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let (page_id, drawing_plan) =
        this.prepare_page_drawing(page_number, overlay, PageResourceKind::Font)?;
    this.invalidate_hayro_pdf_keeping_placements();
    py.detach(|| {
        let mut font_dict = dictionary! {
            "Type" => "Font",
            "Subtype" => "Type1",
            "BaseFont" => Object::Name(base_font.as_bytes().to_vec()),
        };
        if winansi {
            font_dict.set("Encoding", Object::Name(b"WinAnsiEncoding".to_vec()));
        }
        let font_id = this.doc.add_object(font_dict);
        let ops = draw::textbox_text_ops(
            crop,
            rotation,
            rect,
            &layout,
            align,
            drawing_plan.resource.name(),
            fontsize,
            draw::DeviceColor::Rgb(color.0, color.1, color.2),
        )
        .map_err(PdfError::new_err)?;
        this.commit_drawing_resource(page_id, drawing_plan.resource, font_id)?;
        this.push_page_content(page_id, ops, drawing_plan.content)?;
        Ok(layout.spare_height)
    })
}

/// Implements [`_Document::insert_embedded_text`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_embedded_text(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    point: (f64, f64),
    lines: Vec<String>,
    font_data: Vec<u8>,
    font_index: u32,
    fontsize: f64,
    lineheight: Option<f64>,
    color: (f64, f64, f64),
    overlay: bool,
    max_font_size: Option<usize>,
    max_text_size: Option<usize>,
    weight: Option<u16>,
) -> PyResult<usize> {
    validate_generated_text_line_count(lines.len(), max_text_size)?;
    validate_generated_text_input(lines.iter().map(String::as_bytes), max_text_size)?;
    validate_font_input(Some(&font_data), max_font_size)?;
    let face = generation_face(py, Arc::new(font_data), font_index, weight)?;
    this.draw_embedded_text(
        py,
        page_number,
        point,
        &lines,
        face,
        fontsize,
        lineheight,
        color,
        overlay,
    )
}

/// Implements [`_Document::insert_embedded_text_file`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_embedded_text_file(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    point: (f64, f64),
    lines: Vec<String>,
    path: &str,
    font_index: u32,
    fontsize: f64,
    lineheight: Option<f64>,
    color: (f64, f64, f64),
    overlay: bool,
    max_font_size: Option<usize>,
    max_text_size: Option<usize>,
    package_font: Option<(String, String)>,
    weight: Option<u16>,
) -> PyResult<usize> {
    validate_generated_text_line_count(lines.len(), max_text_size)?;
    validate_generated_text_input(lines.iter().map(String::as_bytes), max_text_size)?;
    let data = load_generation_font(py, path, package_font, max_font_size)?;
    let face = generation_face(py, data, font_index, weight)?;
    this.draw_embedded_text(
        py,
        page_number,
        point,
        &lines,
        face,
        fontsize,
        lineheight,
        color,
        overlay,
    )
}

/// Implements [`_Document::insert_font_text`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_font_text(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    point: (f64, f64),
    lines: Vec<String>,
    font: &Bound<'_, _Font>,
    fontsize: f64,
    lineheight: Option<f64>,
    color: (f64, f64, f64),
    overlay: bool,
    max_text_size: Option<usize>,
) -> PyResult<usize> {
    validate_generated_text_line_count(lines.len(), max_text_size)?;
    validate_generated_text_input(lines.iter().map(String::as_bytes), max_text_size)?;
    let face = font.get().face().ok_or_else(|| {
        PdfError::new_err("a Standard 14 Font draws through the Standard 14 text path")
    })?;
    this.draw_embedded_text(
        py,
        page_number,
        point,
        &lines,
        face,
        fontsize,
        lineheight,
        color,
        overlay,
    )
}

/// Implements [`_Document::insert_font_textbox`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_font_textbox(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    text: &str,
    font: &Bound<'_, _Font>,
    fontsize: f64,
    lineheight: Option<f64>,
    align: u8,
    color: (f64, f64, f64),
    overlay: bool,
    max_text_size: Option<usize>,
) -> PyResult<f64> {
    validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
    validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
    let face = font.get().face().ok_or_else(|| {
        PdfError::new_err("a Standard 14 Font draws through the Standard 14 text path")
    })?;
    this.draw_embedded_textbox(
        py,
        page_number,
        rect,
        text,
        face,
        fontsize,
        lineheight,
        align,
        color,
        overlay,
        max_text_size,
    )
}

/// Implements [`_Document::insert_embedded_textbox`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_embedded_textbox(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    text: &str,
    font_data: Vec<u8>,
    font_index: u32,
    fontsize: f64,
    lineheight: Option<f64>,
    align: u8,
    color: (f64, f64, f64),
    overlay: bool,
    max_font_size: Option<usize>,
    max_text_size: Option<usize>,
    weight: Option<u16>,
) -> PyResult<f64> {
    validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
    validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
    validate_font_input(Some(&font_data), max_font_size)?;
    let face = generation_face(py, Arc::new(font_data), font_index, weight)?;
    this.draw_embedded_textbox(
        py,
        page_number,
        rect,
        text,
        face,
        fontsize,
        lineheight,
        align,
        color,
        overlay,
        max_text_size,
    )
}

/// Implements [`_Document::insert_embedded_textbox_file`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn insert_embedded_textbox_file(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    text: &str,
    path: &str,
    font_index: u32,
    fontsize: f64,
    lineheight: Option<f64>,
    align: u8,
    color: (f64, f64, f64),
    overlay: bool,
    max_font_size: Option<usize>,
    max_text_size: Option<usize>,
    package_font: Option<(String, String)>,
    weight: Option<u16>,
) -> PyResult<f64> {
    validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
    validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
    let data = load_generation_font(py, path, package_font, max_font_size)?;
    let face = generation_face(py, data, font_index, weight)?;
    this.draw_embedded_textbox(
        py,
        page_number,
        rect,
        text,
        face,
        fontsize,
        lineheight,
        align,
        color,
        overlay,
        max_text_size,
    )
}
