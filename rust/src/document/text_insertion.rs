//! The invisible text layer that `Page.apply_ocr` adds; the canvas draws all
//! other text.

use super::*;

/// Bound generated invisible OCR-layer content per call.
pub(super) const MAX_OCR_LAYER_WORDS: usize = 4_096;

pub(super) const MAX_OCR_LAYER_TEXT_BYTES: usize = 1024 * 1024;

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
