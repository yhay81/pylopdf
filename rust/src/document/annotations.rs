//! Annotation reads, highlight and link creation, and the text-markup
//! appearances that the rendering snapshot synthesizes.

use super::*;

/// Bound page annotation/link interpretation and generated annotation input.
pub(crate) const MAX_PAGE_ANNOTATIONS: usize = 4_096;

pub(super) const MAX_ANNOTATION_METADATA_BYTES: usize = 1024 * 1024;

pub(super) const MAX_HIGHLIGHT_RECTS: usize = 4_096;

pub(super) const MAX_TEXT_MARKUP_SEGMENTS: usize = 65_536;

/// One page annotation that `page.annotations` lists: its position in
/// `/Annots`, its xref (0 for a direct dictionary), its subtype (`None`
/// without one), display rect, `/F` flags, the text entries `/Contents`,
/// `/Name`, `/T`, `/CreationDate`, `/M`, `/Subj`, `/NM`, and `/IT`, the `/C`
/// and `/IC` colours, and the `/CA` opacity.
pub(super) type AnnotationTuple = (
    u32,
    u32,
    Option<String>,
    (f64, f64, f64, f64),
    i64,
    [Option<String>; 8],
    Vec<f64>,
    Vec<f64>,
    Option<f64>,
);

/// The annotation text entries that `AnnotationTuple` carries, in its order.
const ANNOTATION_TEXT_KEYS: [&[u8]; 8] = [
    b"Contents",
    b"Name",
    b"T",
    b"CreationDate",
    b"M",
    b"Subj",
    b"NM",
    b"IT",
];

/// Read an annotation colour array of up to four numbers.
fn annotation_color(doc: &Document, dict: &Dictionary, key: &[u8]) -> Vec<f64> {
    let Ok(color) = dict.get(key).map(|value| deref_object(doc, value)) else {
        return Vec::new();
    };
    let Ok(items) = color.as_array() else {
        return Vec::new();
    };
    if items.len() > 4 {
        return Vec::new();
    }
    let mut components = Vec::with_capacity(items.len());
    for item in items {
        match deref_object(doc, item) {
            Object::Integer(value) => components.push(*value as f64),
            Object::Real(value) => components.push(f64::from(*value)),
            _ => return Vec::new(),
        }
    }
    components
}

/// Preallocated target for appending one page annotation without later growth.
pub(super) enum PageAnnotationAppendPlan {
    Direct,
    Indirect(ObjectId),
    Replacement(Vec<Object>),
}

pub(super) struct TextMarkupAppearancePlan {
    pub(super) annotation_id: ObjectId,
    pub(super) kind: draw::TextMarkupKind,
    pub(super) quads: Vec<[(f64, f64); 4]>,
    pub(super) segment_counts: Vec<usize>,
    pub(super) bbox: [f64; 4],
    pub(super) color: (f64, f64, f64),
    pub(super) opacity: f64,
}

pub(super) fn annotation_has_normal_appearance(doc: &Document, annotation: &Dictionary) -> bool {
    annotation
        .get(b"AP")
        .ok()
        .and_then(|appearance| deref_dict_ref(doc, appearance))
        .and_then(|appearance| appearance.get(b"N").ok())
        .is_some_and(|normal| {
            let resolved = deref_object(doc, normal);
            resolved.as_stream().is_ok() || resolved.as_dict().is_ok()
        })
}

pub(super) fn text_markup_kind(subtype: &[u8]) -> Option<draw::TextMarkupKind> {
    match subtype {
        b"Highlight" => Some(draw::TextMarkupKind::Highlight),
        b"Underline" => Some(draw::TextMarkupKind::Underline),
        b"Squiggly" => Some(draw::TextMarkupKind::Squiggly),
        b"StrikeOut" => Some(draw::TextMarkupKind::StrikeOut),
        _ => None,
    }
}

pub(super) fn text_markup_segment_count(
    kind: draw::TextMarkupKind,
    quad: [(f64, f64); 4],
) -> Option<usize> {
    if matches!(kind, draw::TextMarkupKind::Highlight) {
        return Some(4);
    }
    let [upper_left, upper_right, lower_left, lower_right] = quad;
    let (start, end) = match kind {
        draw::TextMarkupKind::Underline | draw::TextMarkupKind::Squiggly => {
            (lower_left, lower_right)
        }
        draw::TextMarkupKind::StrikeOut => (
            (
                (upper_left.0 + lower_left.0) / 2.0,
                (upper_left.1 + lower_left.1) / 2.0,
            ),
            (
                (upper_right.0 + lower_right.0) / 2.0,
                (upper_right.1 + lower_right.1) / 2.0,
            ),
        ),
        draw::TextMarkupKind::Highlight => unreachable!(),
    };
    let inline_length = (end.0 - start.0).hypot(end.1 - start.1);
    if !inline_length.is_finite() || inline_length <= f64::EPSILON {
        return None;
    }
    if matches!(
        kind,
        draw::TextMarkupKind::Underline | draw::TextMarkupKind::Squiggly
    ) {
        let top = (
            (upper_left.0 + upper_right.0) / 2.0,
            (upper_left.1 + upper_right.1) / 2.0,
        );
        let bottom = (
            (lower_left.0 + lower_right.0) / 2.0,
            (lower_left.1 + lower_right.1) / 2.0,
        );
        let cross_length = (top.0 - bottom.0).hypot(top.1 - bottom.1);
        if !cross_length.is_finite() || cross_length <= f64::EPSILON {
            return None;
        }
    }
    if matches!(kind, draw::TextMarkupKind::Squiggly) {
        let segments = (inline_length / 2.0).ceil().max(1.0);
        return Some(segments as usize);
    }
    Some(1)
}

/// Collect bounded text-markup annotations that need a render-only appearance.
///
/// PDF viewers may synthesize a normal appearance from `/QuadPoints` and `/C`,
/// but hayro 0.8 requires `/AP /N`. Keep this compatibility work outside the
/// editable document and refuse aggregate geometry amplification.
pub(super) fn missing_text_markup_appearance_plans(
    doc: &Document,
) -> PyResult<Option<Vec<TextMarkupAppearancePlan>>> {
    let mut plans = Vec::new();
    let mut total_quads = 0usize;
    let mut total_segments = 0usize;
    for (&annotation_id, object) in &doc.objects {
        let Ok(annotation) = object.as_dict() else {
            continue;
        };
        let Some(kind) = annotation
            .get(b"Subtype")
            .ok()
            .and_then(|subtype| deref_object(doc, subtype).as_name().ok())
            .and_then(text_markup_kind)
        else {
            continue;
        };
        if annotation_has_normal_appearance(doc, annotation) {
            continue;
        }

        let Ok(quad_points) = annotation
            .get(b"QuadPoints")
            .map(|points| deref_object(doc, points))
            .and_then(Object::as_array)
        else {
            continue;
        };
        if quad_points.is_empty() || quad_points.len() % 8 != 0 {
            continue;
        }
        let quad_count = quad_points.len() / 8;
        let Some(new_total_quads) = total_quads.checked_add(quad_count) else {
            return Ok(None);
        };
        total_quads = new_total_quads;
        if total_quads > MAX_HIGHLIGHT_RECTS {
            return Ok(None);
        }

        let mut quads = Vec::new();
        quads.try_reserve_exact(quad_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate text-markup appearance quads: {error}"
            ))
        })?;
        let mut segment_counts = Vec::new();
        segment_counts
            .try_reserve_exact(quad_count)
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate text-markup appearance segment counts: {error}"
                ))
            })?;
        let mut valid = true;
        for chunk in quad_points.as_chunks::<8>().0 {
            let mut values = [0.0f64; 8];
            for (slot, value) in values.iter_mut().zip(chunk) {
                let Some(number) = finite_number(doc, value) else {
                    valid = false;
                    break;
                };
                *slot = number;
            }
            if !valid {
                break;
            }
            let quad = [
                (values[0], values[1]),
                (values[2], values[3]),
                (values[4], values[5]),
                (values[6], values[7]),
            ];
            let Some(segment_count) = text_markup_segment_count(kind, quad) else {
                valid = false;
                break;
            };
            let Some(new_total_segments) = total_segments.checked_add(segment_count) else {
                return Ok(None);
            };
            total_segments = new_total_segments;
            if total_segments > MAX_TEXT_MARKUP_SEGMENTS {
                return Ok(None);
            }
            quads.push(quad);
            segment_counts.push(segment_count);
        }
        if !valid {
            continue;
        }

        let Some(color) = annotation
            .get(b"C")
            .ok()
            .map(|color| deref_object(doc, color))
            .and_then(|color| color.as_array().ok())
            .filter(|color| color.len() == 3)
            .and_then(|color| {
                Some((
                    finite_number(doc, &color[0])?,
                    finite_number(doc, &color[1])?,
                    finite_number(doc, &color[2])?,
                ))
            })
            .filter(|color| {
                [color.0, color.1, color.2]
                    .into_iter()
                    .all(|component| (0.0..=1.0).contains(&component))
            })
        else {
            continue;
        };
        let opacity = if let Ok(opacity) = annotation.get(b"CA") {
            let Some(opacity) = finite_number(doc, opacity) else {
                continue;
            };
            opacity
        } else {
            1.0
        };
        if !(0.0..=1.0).contains(&opacity) {
            continue;
        }

        let bbox = draw::bounding_rect_iter(quads.iter().flatten().copied());
        if bbox.iter().any(|value| !value.is_finite()) || bbox[0] >= bbox[2] || bbox[1] >= bbox[3] {
            continue;
        }
        plans.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow text-markup appearance plans: {error}"
            ))
        })?;
        plans.push(TextMarkupAppearancePlan {
            annotation_id,
            kind,
            quads,
            segment_counts,
            bbox,
            color,
            opacity,
        });
    }
    Ok(Some(plans))
}

/// Add bounded text-markup appearances to a rendering clone only.
pub(super) fn synthesize_missing_text_markup_appearances_for_render(
    doc: &mut Document,
    plans: Vec<TextMarkupAppearancePlan>,
) -> PyResult<bool> {
    let Ok(object_count) = u32::try_from(plans.len()).map(|count| count.saturating_mul(2)) else {
        return Ok(false);
    };
    if doc.max_id.checked_add(object_count).is_none() {
        return Ok(false);
    }

    let mut changed = false;
    for plan in plans {
        let gs_id = doc.add_object(dictionary! {
            "Type" => "ExtGState",
            "BM" => Object::Name(plan.kind.blend_mode().as_bytes().to_vec()),
            "CA" => Object::Real(plan.opacity as f32),
            "ca" => Object::Real(plan.opacity as f32),
            "AIS" => Object::Boolean(false),
        });
        let form_dict = dictionary! {
            "Type" => "XObject",
            "Subtype" => "Form",
            "FormType" => 1,
            "BBox" => Object::Array(plan.bbox.iter().map(|&value| Object::Real(value as f32)).collect()),
            "Resources" => dictionary! {
                "ExtGState" => dictionary! { "PyloGS" => Object::Reference(gs_id) },
            },
        };
        let appearance_ops =
            draw::text_markup_ap_ops(plan.kind, &plan.quads, plan.color, &plan.segment_counts)
                .map_err(PdfError::new_err)?;
        let appearance_id =
            doc.add_object(Stream::new(form_dict, appearance_ops).with_compression(false));
        if let Ok(annotation) = doc
            .get_object_mut(plan.annotation_id)
            .and_then(Object::as_dict_mut)
        {
            annotation.set(
                "AP",
                dictionary! { "N" => Object::Reference(appearance_id) },
            );
            changed = true;
        }
    }
    Ok(changed)
}

/// Add to one annotation metadata budget without overflow or partial output.
pub(super) fn add_annotation_budget(total: &mut usize, amount: usize, label: &str) -> PyResult<()> {
    *total = total
        .checked_add(amount)
        .ok_or_else(|| PdfError::new_err("annotation metadata exceeds the platform size limit"))?;
    if *total > MAX_ANNOTATION_METADATA_BYTES {
        return Err(PdfError::new_err(format!(
            "annotation {label} exceeds the {MAX_ANNOTATION_METADATA_BYTES}-byte safety limit"
        )));
    }
    Ok(())
}

/// Materialize lossy UTF-8 only after charging the encoded annotation bytes.
pub(super) fn bounded_annotation_bytes(
    bytes: &[u8],
    encoded_bytes: &mut usize,
    returned_bytes: &mut usize,
    label: &str,
) -> PyResult<String> {
    add_annotation_budget(encoded_bytes, bytes.len(), &format!("encoded {label}"))?;
    let text = String::from_utf8_lossy(bytes).into_owned();
    add_annotation_budget(returned_bytes, text.len(), &format!("returned {label}"))?;
    Ok(text)
}

/// Decode a PDF text string under aggregate annotation metadata budgets.
pub(super) fn bounded_annotation_text(
    doc: &Document,
    object: &Object,
    encoded_bytes: &mut usize,
    returned_bytes: &mut usize,
    label: &str,
) -> PyResult<Option<String>> {
    let object = deref_object(doc, object);
    let Object::String(encoded, _) = object else {
        return Ok(None);
    };
    add_annotation_budget(encoded_bytes, encoded.len(), &format!("encoded {label}"))?;
    let Ok(text) = decode_text_string(object) else {
        return Ok(None);
    };
    add_annotation_budget(returned_bytes, text.len(), &format!("returned {label}"))?;
    Ok(Some(text))
}

/// Decode a FileSpec display name under annotation metadata budgets.
pub(super) fn bounded_annotation_filespec_name(
    doc: &Document,
    object: &Object,
    encoded_bytes: &mut usize,
    returned_bytes: &mut usize,
) -> PyResult<Option<String>> {
    let object = deref_object(doc, object);
    let name = match object {
        Object::Dictionary(filespec) => filespec.get(b"UF").or_else(|_| filespec.get(b"F")).ok(),
        _ => Some(object),
    };
    match name {
        Some(name) => {
            bounded_annotation_text(doc, name, encoded_bytes, returned_bytes, "file name")
        }
        None => Ok(None),
    }
}

/// Bound caller-supplied annotation text before encoding or PDF mutation.
pub(super) fn validate_annotation_input<'a>(
    values: impl IntoIterator<Item = &'a [u8]>,
    label: &str,
) -> PyResult<()> {
    let mut total = 0usize;
    for value in values {
        total = total.checked_add(value.len()).ok_or_else(|| {
            limit_err(
                "annotation_input_size",
                "annotation input exceeds the platform size limit",
            )
        })?;
        if total > MAX_ANNOTATION_METADATA_BYTES {
            return Err(limit_err(
                "annotation_input_size",
                format!(
                    "annotation subtype and {label} input exceed the {MAX_ANNOTATION_METADATA_BYTES}-byte UTF-8 safety limit"
                ),
            ));
        }
    }
    Ok(())
}

impl _Document {
    /// Borrow a page annotation array and reject partial reads above the cap.
    pub(super) fn page_annotation_items(&self, page_id: ObjectId) -> PyResult<Option<&[Object]>> {
        let page = self
            .doc
            .get_object(page_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?;
        let annots = match page.get(b"Annots") {
            Ok(Object::Reference(id)) => self
                .doc
                .get_object(*id)
                .and_then(Object::as_array)
                .ok()
                .map(Vec::as_slice),
            Ok(Object::Array(items)) => Some(items.as_slice()),
            _ => None,
        };
        if annots.is_some_and(|items| items.len() > MAX_PAGE_ANNOTATIONS) {
            return Err(PdfError::new_err(format!(
                "page annotations exceed the {MAX_PAGE_ANNOTATIONS}-entry safety limit"
            )));
        }
        Ok(annots)
    }

    /// Validate and preallocate one annotation append before dependent objects exist.
    pub(super) fn prepare_page_annotation_append(
        &mut self,
        page_id: ObjectId,
    ) -> PyResult<PageAnnotationAppendPlan> {
        enum Target {
            PageArray,
            IndirectArray(ObjectId),
            Missing,
        }

        let (target, existing) = {
            let page = self
                .doc
                .get_object(page_id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?;
            match page.get(b"Annots") {
                // An indirect null and a missing object start a new array, as
                // a null value does.
                Ok(Object::Reference(id)) => {
                    match non_null_object(&self.doc, *id).map_err(to_py_err)? {
                        Some(annots) => (
                            Target::IndirectArray(*id),
                            annots.as_array().map_err(to_py_err)?.len(),
                        ),
                        None => (Target::Missing, 0),
                    }
                }
                Ok(Object::Array(items)) => (Target::PageArray, items.len()),
                _ => (Target::Missing, 0),
            }
        };
        let total = existing
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("page annotations exceed the platform size limit"))?;
        if total > MAX_PAGE_ANNOTATIONS {
            return Err(PdfError::new_err(format!(
                "page annotations exceed the {MAX_PAGE_ANNOTATIONS}-entry safety limit"
            )));
        }

        match target {
            Target::IndirectArray(arr_id) => {
                // copy_page/select duplicates may share indirect Annots arrays.
                // Clone on write while shared so additions do not leak.
                let shared = self.doc.annotation_array_shared(page_id, arr_id)?;
                if shared {
                    let source = self
                        .doc
                        .get_object(arr_id)
                        .and_then(Object::as_array)
                        .map_err(to_py_err)?;
                    let mut detached = Vec::new();
                    detached.try_reserve_exact(total).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to allocate detached page annotations: {error}"
                        ))
                    })?;
                    for item in source {
                        detached.push(item.clone());
                    }
                    Ok(PageAnnotationAppendPlan::Replacement(detached))
                } else {
                    let items = self.doc.annotation_array_mut(arr_id)?;
                    items.try_reserve_exact(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow indirect page annotations: {error}"
                        ))
                    })?;
                    Ok(PageAnnotationAppendPlan::Indirect(arr_id))
                }
            }
            Target::PageArray => {
                let items = self.doc.page_annotations_mut(page_id)?;
                items.try_reserve_exact(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow page annotations: {error}"))
                })?;
                Ok(PageAnnotationAppendPlan::Direct)
            }
            Target::Missing => {
                let mut items = Vec::new();
                items.try_reserve_exact(1).map_err(|error| {
                    PdfError::new_err(format!("failed to allocate page annotations: {error}"))
                })?;
                Ok(PageAnnotationAppendPlan::Replacement(items))
            }
        }
    }

    /// Commit a preallocated annotation reference without growing a collection.
    pub(super) fn commit_page_annotation_append(
        &mut self,
        page_id: ObjectId,
        plan: PageAnnotationAppendPlan,
        annot_id: ObjectId,
    ) -> PyResult<()> {
        match plan {
            PageAnnotationAppendPlan::Direct => {
                self.doc
                    .page_annotations_mut(page_id)?
                    .push(Object::Reference(annot_id));
            }
            PageAnnotationAppendPlan::Indirect(array_id) => {
                self.doc
                    .annotation_array_mut(array_id)?
                    .push(Object::Reference(annot_id));
            }
            PageAnnotationAppendPlan::Replacement(mut items) => {
                items.push(Object::Reference(annot_id));
                self.doc.set_page_annotations(page_id, items)?;
            }
        }
        Ok(())
    }
}

/// Implements [`_Document::read_annotations`].
pub(super) fn read_annotations(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<Vec<AnnotationTuple>> {
    py.detach(|| {
        let geometry = this.page_display_geometry(page_number)?;
        let page_id = this.page_id(page_number)?;
        let Some(annots) = this.page_annotation_items(page_id)? else {
            return Ok(Vec::new());
        };
        let mut out = Vec::new();
        let mut budget = (0usize, 0usize);
        for (index, item) in annots.iter().enumerate() {
            let (xref, dict) = match item {
                Object::Reference(id) => match this.doc.get_object(*id).and_then(Object::as_dict) {
                    Ok(dict) => (id.0, dict),
                    Err(_) => continue,
                },
                Object::Dictionary(dict) => (0, dict),
                _ => continue,
            };
            let index = u32::try_from(index).unwrap_or(u32::MAX);
            let Some(annotation) =
                this.annotation_item(index, xref, dict, geometry, &mut budget)?
            else {
                continue;
            };
            out.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to grow returned annotations: {error}"))
            })?;
            out.push(annotation);
        }
        Ok(out)
    })
}

/// Implements [`_Document::read_annotation`].
pub(super) fn read_annotation(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
) -> PyResult<AnnotationTuple> {
    py.detach(|| {
        let geometry = this.page_display_geometry(page_number)?;
        let page_id = this.page_id(page_number)?;
        let (index, dict) = this.annotation_at(page_id, index, xref)?;
        this.annotation_item(index, xref, dict, geometry, &mut (0, 0))?
            .ok_or_else(|| {
                StaleObjectError::new_err(format!(
                    "annotation {xref} at index {index} is no longer one that page.annotations lists"
                ))
            })
    })
}

impl _Document {
    /// Read an annotation that `page.annotations` lists, or `None` for a
    /// link, a widget, a popup, or an annotation without a rectangle. `budget` holds
    /// the encoded and returned text bytes of the call.
    fn annotation_item(
        &self,
        index: u32,
        xref: u32,
        dict: &Dictionary,
        (crop, rotation): ([f64; 4], i64),
        budget: &mut (usize, usize),
    ) -> PyResult<Option<AnnotationTuple>> {
        let subtype = dict
            .get(b"Subtype")
            .map(|value| deref_object(&self.doc, value))
            .and_then(Object::as_name)
            .ok();
        // Links and widgets have sequences of their own, and a popup
        // belongs to the annotation that names it, as in MuPDF.
        if matches!(subtype, Some(b"Link" | b"Widget" | b"Popup")) {
            return Ok(None);
        }
        let Some(rect) = resolve_box(&self.doc, dict, b"Rect") else {
            return Ok(None);
        };
        let (encoded_bytes, returned_bytes) = budget;
        let subtype = subtype
            .map(|name| {
                bounded_annotation_bytes(name, encoded_bytes, returned_bytes, "subtype text")
            })
            .transpose()?;
        let display = draw::pdf_rect_to_display(crop, rotation, rect);
        let flags = dict
            .get(b"F")
            .map(|value| deref_object(&self.doc, value))
            .and_then(Object::as_i64)
            .unwrap_or(0);
        let mut texts: [Option<String>; 8] = Default::default();
        for (slot, key) in texts.iter_mut().zip(ANNOTATION_TEXT_KEYS) {
            let Ok(object) = dict.get(key) else {
                continue;
            };
            *slot = if key == b"Name" || key == b"IT" {
                match deref_object(&self.doc, object).as_name() {
                    Ok(name) => Some(bounded_annotation_bytes(
                        name,
                        encoded_bytes,
                        returned_bytes,
                        "icon name",
                    )?),
                    Err(_) => None,
                }
            } else {
                bounded_annotation_text(
                    &self.doc,
                    object,
                    encoded_bytes,
                    returned_bytes,
                    if key == b"Contents" {
                        "Contents text"
                    } else {
                        "annotation text"
                    },
                )?
            };
        }
        let opacity = dict
            .get(b"CA")
            .map(|value| deref_object(&self.doc, value))
            .ok()
            .and_then(|value| match value {
                Object::Integer(value) => Some(*value as f64),
                Object::Real(value) => Some(f64::from(*value)),
                _ => None,
            })
            .filter(|value| value.is_finite());
        Ok(Some((
            index,
            xref,
            subtype,
            (display[0], display[1], display[2], display[3]),
            flags,
            texts,
            annotation_color(&self.doc, dict, b"C"),
            annotation_color(&self.doc, dict, b"IC"),
            opacity,
        )))
    }
}

/// The `/Matrix` of an appearance drawn on a canvas `size` over the display
/// rectangle `rect` of a page with `crop` and `rotation`: the composed canvas
/// is y up from the rectangle's bottom-left corner, and the matrix maps it to
/// PDF user space, so its BBox lands on the annotation's `/Rect` upright on
/// the displayed page.
pub(super) fn appearance_matrix(
    crop: [f64; 4],
    rotation: i64,
    rect: [f64; 4],
    size: (f64, f64),
) -> [f64; 6] {
    let bottom = rect[1] + size.1;
    let origin = draw::display_to_pdf(crop, rotation, rect[0], bottom);
    let right = draw::display_to_pdf(crop, rotation, rect[0] + 1.0, bottom);
    let up = draw::display_to_pdf(crop, rotation, rect[0], bottom - 1.0);
    [
        right.0 - origin.0,
        right.1 - origin.1,
        up.0 - origin.0,
        up.1 - origin.1,
        origin.0,
        origin.1,
    ]
}

/// Implements [`_Document::add_highlight_annotation`].
pub(super) fn add_highlight_annotation(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rects: Vec<(f64, f64, f64, f64)>,
    color: (f64, f64, f64),
    opacity: f64,
    content: Option<String>,
) -> PyResult<u32> {
    if rects.is_empty() || rects.len() > MAX_HIGHLIGHT_RECTS {
        return Err(PdfError::new_err(format!(
            "highlight annotations require 1 to {MAX_HIGHLIGHT_RECTS} rectangles"
        )));
    }
    validate_annotation_input(
        [
            b"Highlight".as_slice(),
            content.as_deref().unwrap_or_default().as_bytes(),
        ],
        "content",
    )?;
    let encoded_content = match content {
        Some(content) => {
            let encoded = text_string(&content);
            if encoded.as_str().is_ok_and(|bytes| {
                bytes
                    .len()
                    .checked_add(b"Highlight".len())
                    .is_none_or(|size| size > MAX_ANNOTATION_METADATA_BYTES)
            }) {
                return Err(limit_err(
                    "annotation_input_size",
                    format!(
                        "encoded annotation subtype and content exceed the {MAX_ANNOTATION_METADATA_BYTES}-byte safety limit"
                    ),
                ));
            }
            Some(encoded)
        }
        None => None,
    };
    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let page_id = this.page_id(page_number)?;
    let annotation_append = this.prepare_page_annotation_append(page_id)?;

    let mut quads = Vec::new();
    quads.try_reserve_exact(rects.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate highlight quads: {error}"))
    })?;
    for &(x0, y0, x1, y1) in &rects {
        quads.push(draw::display_rect_quad_pdf(
            crop,
            rotation,
            [x0, y0, x1, y1],
        ));
    }
    let bbox = draw::bounding_rect_iter(quads.iter().flatten().copied());
    let quad_point_count = quads
        .len()
        .checked_mul(8)
        .ok_or_else(|| PdfError::new_err("highlight QuadPoints exceed the platform limit"))?;
    let mut quad_points = Vec::new();
    quad_points
        .try_reserve_exact(quad_point_count)
        .map_err(|error| {
            PdfError::new_err(format!("failed to allocate highlight QuadPoints: {error}"))
        })?;
    for &(x, y) in quads.iter().flatten() {
        quad_points.push(Object::Real(x as f32));
        quad_points.push(Object::Real(y as f32));
    }

    // The appearance: each rectangle filled in multiply blending.
    let display = rects.iter().fold(
        [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ],
        |[x0, y0, x1, y1], &(a0, b0, a1, b1)| {
            [
                x0.min(a0.min(a1)),
                y0.min(b0.min(b1)),
                x1.max(a0.max(a1)),
                y1.max(b0.max(b1)),
            ]
        },
    );
    let mut canvas = _Canvas::new(display[2] - display[0], display[3] - display[1], false)?;
    let multiply = canvas::BLEND_MODES
        .iter()
        .position(|mode| *mode == "Multiply")
        .and_then(|mode| u8::try_from(mode).ok())
        .ok_or_else(|| PdfError::new_err("the canvas has no multiply blend mode"))?;
    let depth = canvas.push_blend(multiply)?;
    for &(x0, y0, x1, y1) in &rects {
        let (x0, x1) = (x0.min(x1) - display[0], x0.max(x1) - display[0]);
        let (y0, y1) = (y0.min(y1) - display[1], y0.max(y1) - display[1]);
        canvas.add_path(
            "mlllh",
            vec![x0, y0, x1, y0, x1, y1, x0, y1],
            Some(((color.0, color.1, color.2, opacity), false)),
            None,
        )?;
    }
    canvas.pop(depth)?;
    let appearance = canvas.take_recording()?;
    let matrix = appearance_matrix(crop, rotation, display, appearance.size);
    let appearance = this.prepare_canvas_form(py, appearance)?;

    this.invalidate_hayro_pdf_keeping_placements();
    let committed = this.commit_canvas_form(appearance, Some(matrix))?;
    let ap_id = committed.form_id;
    this.remember_canvas_placements(committed, Some(page_number));

    let mut annot = dictionary! {
        "Type" => "Annot",
        "Subtype" => "Highlight",
        "Rect" => Object::Array(bbox.iter().map(|&v| Object::Real(v as f32)).collect()),
        "QuadPoints" => Object::Array(quad_points),
        "C" => Object::Array(vec![
            Object::Real(color.0 as f32),
            Object::Real(color.1 as f32),
            Object::Real(color.2 as f32),
        ]),
        "CA" => Object::Real(opacity as f32),
        // Printable flag.
        "F" => 4,
        "P" => page_id,
        "AP" => dictionary! { "N" => Object::Reference(ap_id) },
    };
    if let Some(encoded_content) = encoded_content {
        annot.set("Contents", encoded_content);
    }
    let annot_id = this.doc.add_object(annot);
    this.commit_page_annotation_append(page_id, annotation_append, annot_id)?;
    Ok(annot_id.0)
}

/// Charge the texts of a link's target to the annotation input budget.
fn validate_link_target(target: &TargetTuple) -> PyResult<()> {
    let (_, _, _, _, text, name) = target;
    validate_annotation_input(
        [
            b"Link".as_slice(),
            text.as_deref().unwrap_or_default().as_bytes(),
            name.as_deref().unwrap_or_default().as_bytes(),
        ],
        "target",
    )
}

/// Return the PDF `/Rect` of a display rectangle on a page.
fn link_rect(this: &_Document, page_id: ObjectId, rect: (f64, f64, f64, f64)) -> PyResult<Object> {
    let (crop, rotation) = this.resolve_display_geometry(page_id)?;
    let quad = draw::display_rect_quad_pdf(crop, rotation, [rect.0, rect.1, rect.2, rect.3]);
    let bbox = draw::bounding_rect(&quad);
    Ok(Object::Array(
        bbox.iter()
            .map(|&value| Object::Real(value as f32))
            .collect(),
    ))
}

/// Implements [`_Document::add_link`].
pub(super) fn add_link(
    this: &mut _Document,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    target: TargetTuple,
) -> PyResult<u32> {
    validate_link_target(&target)?;
    let page_id = this.page_id(page_number)?;
    let rect = link_rect(this, page_id, rect)?;
    let entry = target_entry(this, this.doc.page_ids()?, &target, &mut |_, _| Ok(()))?;
    let annotation_append = this.prepare_page_annotation_append(page_id)?;
    let mut annotation = dictionary! {
        "Type" => "Annot",
        "Subtype" => "Link",
        "Rect" => rect,
        "Border" => Object::Array(vec![0.into(), 0.into(), 0.into()]),
        "F" => 4,
        "P" => page_id,
    };
    if let Some((key, value)) = entry {
        annotation.set(key, value);
    }
    this.invalidate_hayro_pdf_keeping_placements();
    let annot_id = this.doc.add_object(annotation);
    this.commit_page_annotation_append(page_id, annotation_append, annot_id)?;
    Ok(annot_id.0)
}

impl _Document {
    /// Return the current index and dictionary of the annotation that a
    /// handle names: object `xref`, found at `index` or, after an edit moved
    /// it, at its first position in the page's `/Annots`; or, for 0, the
    /// direct dictionary at `index`.
    pub(super) fn annotation_at(
        &self,
        page_id: ObjectId,
        index: u32,
        xref: u32,
    ) -> PyResult<(u32, &Dictionary)> {
        let stale = || {
            StaleObjectError::new_err(format!(
                "annotation {xref} is no longer in the page's /Annots"
            ))
        };
        let items = self.page_annotation_items(page_id)?.ok_or_else(stale)?;
        let position = match items.get(index as usize) {
            Some(Object::Reference(id)) if id.0 == xref && xref != 0 => Some(index as usize),
            Some(Object::Dictionary(_)) if xref == 0 => Some(index as usize),
            _ if xref != 0 => items
                .iter()
                .position(|item| matches!(item, Object::Reference(id) if id.0 == xref)),
            _ => None,
        };
        let position = position.ok_or_else(stale)?;
        let dictionary = match &items[position] {
            Object::Reference(id) => self
                .doc
                .get_object(*id)
                .and_then(Object::as_dict)
                .map_err(|_| stale())?,
            Object::Dictionary(dictionary) => dictionary,
            _ => return Err(stale()),
        };
        // The page index limits /Annots to MAX_PAGE_ANNOTATIONS entries.
        Ok((u32::try_from(position).unwrap_or(u32::MAX), dictionary))
    }

    /// Borrow the page's own `/Annots` array for an in-place edit, first
    /// detaching an indirect array that other pages share, so that the edit
    /// cannot reach them.
    fn own_page_annotations_mut(&mut self, page_id: ObjectId) -> PyResult<&mut Vec<Object>> {
        let annots = self
            .doc
            .get_object(page_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?
            .get(b"Annots")
            .ok()
            .cloned();
        match annots {
            Some(Object::Reference(array_id)) => {
                if self.doc.annotation_array_shared(page_id, array_id)? {
                    let source = self
                        .doc
                        .get_object(array_id)
                        .and_then(Object::as_array)
                        .map_err(to_py_err)?;
                    let mut detached = Vec::new();
                    detached.try_reserve_exact(source.len()).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to allocate detached page annotations: {error}"
                        ))
                    })?;
                    detached.extend(source.iter().cloned());
                    self.doc.set_page_annotations(page_id, detached)?;
                    self.doc.page_annotations_mut(page_id)
                } else {
                    self.doc.annotation_array_mut(array_id)
                }
            }
            _ => self.doc.page_annotations_mut(page_id),
        }
    }

    /// Store every annotation that the page's `/Annots` holds as a direct
    /// dictionary as an object of its own, as MuPDF does when it loads the
    /// annotations, and borrow the page's own array. A handle finds a direct
    /// dictionary only by its position, so an edit that could shift or
    /// rewrite one goes through here first: a handle made before then finds
    /// a reference where it expects a dictionary and goes stale instead of
    /// naming another annotation.
    fn indirect_page_annotations_mut(&mut self, page_id: ObjectId) -> PyResult<&mut Vec<Object>> {
        let mut direct = Vec::new();
        for (position, item) in self
            .page_annotation_items(page_id)?
            .unwrap_or_default()
            .iter()
            .enumerate()
        {
            if let Object::Dictionary(dictionary) = item {
                direct.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to allocate direct annotations: {error}"))
                })?;
                direct.push((position, dictionary.clone()));
            }
        }
        let mut references = Vec::new();
        references
            .try_reserve_exact(direct.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate annotation references: {error}"))
            })?;
        for (position, dictionary) in direct {
            references.push((
                position,
                self.doc.add_object(Object::Dictionary(dictionary)),
            ));
        }
        let items = self.own_page_annotations_mut(page_id)?;
        for (position, id) in references {
            if let Some(item) = items.get_mut(position) {
                *item = Object::Reference(id);
            }
        }
        Ok(items)
    }
}

/// Implements [`_Document::set_link`]: edit the link and return its object
/// number, which a link stored as a direct dictionary gains here.
pub(super) fn set_link(
    this: &mut _Document,
    page_number: u32,
    index: u32,
    xref: u32,
    rect: Option<(f64, f64, f64, f64)>,
    target: Option<TargetTuple>,
) -> PyResult<u32> {
    let page_id = this.page_id(page_number)?;
    let (index, link) = this.annotation_at(page_id, index, xref)?;
    if !matches!(link.get(b"Subtype").and_then(Object::as_name), Ok(b"Link")) {
        return Err(StaleObjectError::new_err(format!(
            "annotation {xref} at index {index} is no longer a link"
        )));
    }
    let rect = rect
        .map(|rect| link_rect(this, page_id, rect))
        .transpose()?;
    let entry = match &target {
        Some(target) => {
            validate_link_target(target)?;
            Some(target_entry(
                this,
                this.doc.page_ids()?,
                target,
                &mut |_, _| Ok(()),
            )?)
        }
        None => None,
    };
    this.invalidate_hayro_pdf_keeping_placements();
    if xref == 0 {
        this.indirect_page_annotations_mut(page_id)?;
    }
    let id = match this
        .page_annotation_items(page_id)?
        .and_then(|items| items.get(index as usize))
    {
        Some(Object::Reference(id)) => *id,
        _ => return Err(PdfError::new_err("the link changed while it was edited")),
    };
    let link = this
        .doc
        .get_object_mut(id)
        .and_then(Object::as_dict_mut)
        .map_err(|_| PdfError::new_err("the link changed while it was edited"))?;
    if let Some(rect) = rect {
        link.set("Rect", rect);
    }
    if let Some(entry) = entry {
        link.remove(b"Dest");
        link.remove(b"A");
        if let Some((key, value)) = entry {
            link.set(key, value);
        }
    }
    Ok(id.0)
}

/// Implements [`_Document::remove_annotations`]: take the annotations, and
/// the popups they name when the page lists those too, out of the page's
/// `/Annots` in one edit, after finding every one of them and storing the
/// page's direct annotation dictionaries as objects, so that no later handle
/// can shift onto another annotation. The objects stay until a pruning save.
pub(super) fn remove_annotations(
    this: &mut _Document,
    page_number: u32,
    handles: Vec<(u32, u32)>,
) -> PyResult<()> {
    let page_id = this.page_id(page_number)?;
    let mut positions = Vec::new();
    positions
        .try_reserve_exact(handles.len().saturating_mul(2))
        .map_err(|error| {
            PdfError::new_err(format!("failed to allocate removed annotations: {error}"))
        })?;
    for (index, xref) in handles {
        let (index, annotation) = this.annotation_at(page_id, index, xref)?;
        positions.push(index as usize);
        if let Ok(Object::Reference(popup)) = annotation.get(b"Popup") {
            let listed = this.page_annotation_items(page_id)?.and_then(|items| {
                items
                    .iter()
                    .position(|item| matches!(item, Object::Reference(id) if id == popup))
            });
            positions.extend(listed);
        }
    }
    // Remove from the end, so that each removal keeps the earlier positions.
    positions.sort_unstable_by(|a, b| b.cmp(a));
    positions.dedup();
    if positions.is_empty() {
        return Ok(());
    }
    this.invalidate_hayro_pdf_keeping_placements();
    let items = this.indirect_page_annotations_mut(page_id)?;
    for position in positions {
        if position < items.len() {
            items.remove(position);
        }
    }
    Ok(())
}
