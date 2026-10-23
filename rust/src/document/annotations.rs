//! Annotation reads, highlight and link creation, and the text-markup
//! appearances that the rendering snapshot synthesizes.

use super::*;

/// Bound page annotation/link interpretation and generated annotation input.
pub(crate) const MAX_PAGE_ANNOTATIONS: usize = 4_096;

pub(super) const MAX_ANNOTATION_METADATA_BYTES: usize = 1024 * 1024;

/// One page annotation that `page.annotations` lists: its position in
/// `/Annots`, its xref (0 for a direct dictionary), its subtype (`None`
/// without one), display rect, `/F` flags, the text entries `/Contents`,
/// `/Name`, `/T`, `/CreationDate`, `/M`, `/Subj`, `/NM`, and `/IT`, the `/C`
/// and `/IC` colours, the `/CA` opacity, the border's width, `/S` style, and
/// dashes, the `/BM` blend mode when it is one that ISO 32000-2 defines, the
/// object number of the annotation that `/IRT` names, the display rectangle
/// of the `/Popup`, and whether the popup, or else the annotation, is open.
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
    Option<(f64, String, Vec<f64>)>,
    Option<&'static str>,
    (Option<u32>, Option<(f64, f64, f64, f64)>, bool),
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
pub(super) fn annotation_color(doc: &Document, dict: &Dictionary, key: &[u8]) -> Vec<f64> {
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

/// An annotation without a normal appearance that the rendering snapshot
/// draws with the appearance engine.
pub(super) struct DrawnAppearancePlan {
    annotation_id: ObjectId,
    form: CanvasForm,
    matrix: [f64; 6],
    rect: [f64; 4],
}

/// The appearances that the rendering snapshot synthesizes.
#[derive(Default)]
pub(super) struct MissingAppearances {
    drawn: Vec<DrawnAppearancePlan>,
}

impl MissingAppearances {
    pub(super) fn is_empty(&self) -> bool {
        self.drawn.is_empty()
    }
}

/// The most annotations whose appearances one rendering snapshot draws.
const MAX_SYNTHESIZED_APPEARANCES: usize = 4_096;

/// The most path points that the synthesized appearances of one rendering
/// snapshot draw together.
const MAX_SYNTHESIZED_POINTS: usize = 262_144;

/// Collect the appearances that annotations without one need in the
/// rendering snapshot, for every type that the appearance engine draws.
/// More than 4,096 of them, more than 262,144 path points in all, or one
/// whose geometry holds more points than the engine draws, leave all of them
/// undrawn rather than drawn in part, so a document cannot amplify small
/// dictionaries into unbounded drawing.
pub(super) fn missing_appearance_plans(doc: &Document) -> PyResult<MissingAppearances> {
    let mut drawn = Vec::new();
    let mut points = 0usize;
    for (&annotation_id, object) in &doc.objects {
        let Ok(annotation) = object.as_dict() else {
            continue;
        };
        let Some(subtype) = annotation
            .get(b"Subtype")
            .ok()
            .and_then(|subtype| deref_object(doc, subtype).as_name().ok())
            .filter(|subtype| annotation_appearance::draws(subtype))
        else {
            continue;
        };
        if annotation_has_normal_appearance(doc, annotation) {
            continue;
        }
        if drawn.len() >= MAX_SYNTHESIZED_APPEARANCES
            || annotation_appearance::exceeds_points(doc, annotation, subtype)
        {
            drawn.clear();
            break;
        }
        // An annotation that the engine cannot draw, such as free text in a
        // font that cannot encode it, stays undrawn.
        let Ok(Some(annotation_appearance::Appearance {
            mut canvas,
            matrix,
            rect,
            points: drawn_points,
            ..
        })) = annotation_appearance::appearance(doc, annotation)
        else {
            continue;
        };
        points = points.saturating_add(drawn_points);
        if points > MAX_SYNTHESIZED_POINTS {
            drawn.clear();
            break;
        }
        // An appearance that draws nothing needs no rendering copy.
        let recording = canvas.take_recording()?;
        if recording.is_empty() {
            continue;
        }
        let form = prepare_vector_canvas_form(recording)?;
        drawn.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow annotation appearance plans: {error}"
            ))
        })?;
        drawn.push(DrawnAppearancePlan {
            annotation_id,
            form,
            matrix,
            rect,
        });
    }
    Ok(MissingAppearances { drawn })
}

/// Add the planned appearances to a rendering clone only.
pub(super) fn synthesize_missing_appearances_for_render(
    doc: &mut Document,
    plans: MissingAppearances,
) -> PyResult<()> {
    for plan in plans.drawn {
        let committed = commit_canvas_form(doc, plan.form, Some(plan.matrix))?;
        if let Ok(annotation) = doc
            .get_object_mut(plan.annotation_id)
            .and_then(Object::as_dict_mut)
        {
            annotation.set(
                "AP",
                dictionary! { "N" => Object::Reference(committed.form_id) },
            );
            // Text markup covers its quadrilaterals, which /Rect may not.
            annotation.set(
                "Rect",
                Object::Array(
                    plan.rect
                        .iter()
                        .map(|&value| Object::Real(value as f32))
                        .collect(),
                ),
            );
        }
    }
    Ok(())
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

/// The display points and line endings of an annotation, as
/// `read_annotation_geometry` returns them.
pub(super) type AnnotationGeometry = (
    Option<Vec<Vec<(f64, f64)>>>,
    Option<(&'static str, &'static str)>,
);

/// Implements [`_Document::read_annotation_geometry`]: the points of a line,
/// polygon, polyline, or ink annotation in display space, `None` when its
/// geometry is missing or malformed, and the line endings of a line or
/// polyline. More than `MAX_ANNOTATION_POINTS` points raise rather than
/// read in part.
pub(super) fn read_annotation_geometry(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
) -> PyResult<AnnotationGeometry> {
    py.detach(|| {
        let (crop, rotation) = this.page_display_geometry(page_number)?;
        let page_id = this.page_id(page_number)?;
        let (_, dict) = this.annotation_at(page_id, index, xref)?;
        let doc = &*this.doc;
        let Some(subtype) = dict
            .get(b"Subtype")
            .ok()
            .and_then(|value| deref_object(doc, value).as_name().ok())
            .filter(|subtype| matches!(*subtype, b"Line" | b"Polygon" | b"PolyLine" | b"Ink"))
        else {
            return Ok((None, None));
        };
        let key: &[u8] = match subtype {
            b"Line" => b"L",
            b"Ink" => b"InkList",
            _ => b"Vertices",
        };
        let numbers = dict
            .get(key)
            .ok()
            .and_then(|value| deref_object(doc, value).as_array().ok())
            .map_or(0, |items| {
                if subtype == b"Ink" {
                    items
                        .iter()
                        .map(|item| deref_object(doc, item).as_array().map_or(0, Vec::len))
                        .fold(items.len(), usize::saturating_add)
                } else {
                    items.len()
                }
            });
        if numbers / 2 > annotation_appearance::MAX_ANNOTATION_POINTS {
            return Err(limit_err(
                "annotation_point_count",
                format!(
                    "an annotation's geometry holds more than {} points",
                    annotation_appearance::MAX_ANNOTATION_POINTS
                ),
            ));
        }
        let points = annotation_appearance::point_lists(doc, dict, subtype).map(|lists| {
            lists
                .into_iter()
                .map(|points| {
                    points
                        .into_iter()
                        .map(|(x, y)| draw::pdf_to_display(crop, rotation, x, y))
                        .collect()
                })
                .collect()
        });
        let endings = matches!(subtype, b"Line" | b"PolyLine").then(|| {
            let [start, end] = annotation_appearance::line_endings(doc, dict);
            (
                annotation_appearance::LINE_ENDINGS[start],
                annotation_appearance::LINE_ENDINGS[end],
            )
        });
        Ok((points, endings))
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
        let popup = dict
            .get(b"Popup")
            .ok()
            .and_then(|popup| deref_dict_ref(&self.doc, popup));
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
            annotation_appearance::border(&self.doc, dict).map(|border| {
                (
                    border.width,
                    String::from_utf8_lossy(&border.style).into_owned(),
                    border.dashes,
                )
            }),
            dict.get(b"BM")
                .map(|value| deref_object(&self.doc, value))
                .and_then(Object::as_name)
                .ok()
                .and_then(|name| {
                    canvas::BLEND_MODES
                        .iter()
                        .find(|mode| mode.as_bytes() == name)
                        .copied()
                }),
            (
                match dict.get(b"IRT") {
                    Ok(Object::Reference(id)) => Some(id.0),
                    _ => None,
                },
                popup
                    .and_then(|popup| resolve_box(&self.doc, popup, b"Rect"))
                    .map(|rect| {
                        let display = draw::pdf_rect_to_display(crop, rotation, rect);
                        (display[0], display[1], display[2], display[3])
                    }),
                popup
                    .unwrap_or(dict)
                    .get(b"Open")
                    .map(|value| deref_object(&self.doc, value))
                    .and_then(Object::as_bool)
                    .unwrap_or(false),
            ),
        )))
    }
}

/// An annotation to create, as `page.annotations.add` passes a
/// specification: the PDF subtype, the display rectangle, and the entries
/// that the subtype takes. Colours are RGB from 0 to 1, the border is its
/// width, `/S` style name, and dash lengths, and the blend mode is a PDF
/// name.
#[derive(FromPyObject)]
#[pyo3(from_item_all)]
pub(super) struct AnnotationSpec {
    subtype: String,
    /// The display rectangle of a square or circle.
    #[pyo3(default)]
    rect: Option<(f64, f64, f64, f64)>,
    /// The display points of a line, polygon, or polyline, as one list, or
    /// of an ink annotation's strokes.
    #[pyo3(default)]
    points: Option<Vec<Vec<(f64, f64)>>>,
    /// The start and end `/LE` styles of a line or polyline.
    #[pyo3(default)]
    line_endings: Option<(String, String)>,
    /// The display rectangles that text markup covers.
    #[pyo3(default)]
    rects: Option<Vec<(f64, f64, f64, f64)>>,
    /// The `/Name` icon of a note.
    #[pyo3(default)]
    icon: Option<String>,
    /// Whether a note starts open, `/Open`.
    #[pyo3(default)]
    open: Option<bool>,
    /// A free text's text colour, font size, Standard 14 font, and alignment
    /// (0 to 2), as `/DA` and `/Q` record them.
    #[pyo3(default)]
    text_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    font_size: Option<f64>,
    #[pyo3(default)]
    font_name: Option<String>,
    #[pyo3(default)]
    align: Option<u8>,
    /// The OpenType face that draws a free text's text in place of a
    /// Standard 14 font.
    #[pyo3(default)]
    font: Option<Py<_Font>>,
    /// The clockwise turn of a free text's text in display space.
    #[pyo3(default)]
    rotate: Option<i64>,
    /// A free text's callout line: the display point it shows, an optional
    /// knee, and the point where it meets the box.
    #[pyo3(default)]
    callout: Option<Vec<(f64, f64)>>,
    /// The `/LE` ending at the shown point of a callout line.
    #[pyo3(default)]
    callout_ending: Option<String>,
    /// The text that a redaction leaves where it removed content.
    #[pyo3(default)]
    overlay_text: Option<String>,
    /// The file that a file attachment embeds, its name and description,
    /// and the bound on its size, 64 MiB unless the caller passes another.
    #[pyo3(default)]
    data: Option<Vec<u8>>,
    #[pyo3(default)]
    filename: Option<String>,
    #[pyo3(default)]
    description: Option<String>,
    #[pyo3(default = Some(DEFAULT_MAX_EMBEDDED_FILE_SIZE))]
    max_size: Option<usize>,
    #[pyo3(default)]
    color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    interior_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    border: Option<(f64, String, Vec<f64>)>,
    #[pyo3(default)]
    opacity: Option<f64>,
    #[pyo3(default)]
    blend_mode: Option<String>,
    #[pyo3(default)]
    contents: Option<String>,
    #[pyo3(default)]
    created: Option<String>,
}

/// The icons of a note, `/Name` (ISO 32000-2 §12.5.6.4).
const NOTE_ICONS: [&str; 7] = [
    "Comment",
    "Help",
    "Insert",
    "Key",
    "NewParagraph",
    "Note",
    "Paragraph",
];

/// The icons of a file attachment, `/Name` (ISO 32000-2 §12.5.6.15).
const FILE_ICONS: [&str; 4] = ["Graph", "PushPin", "Paperclip", "Tag"];

/// The subtypes that `add_annotation` creates.
const CREATED_SUBTYPES: [&str; 16] = [
    "Redact",
    "Text",
    "FileAttachment",
    "FreeText",
    "Stamp",
    "Caret",
    "Square",
    "Circle",
    "Line",
    "Polygon",
    "PolyLine",
    "Ink",
    "Highlight",
    "Underline",
    "StrikeOut",
    "Squiggly",
];

fn rgb_array(name: &str, (red, green, blue): (f64, f64, f64)) -> PyResult<Object> {
    if ![red, green, blue]
        .iter()
        .all(|component| component.is_finite() && (0.0..=1.0).contains(component))
    {
        return Err(PdfError::new_err(format!(
            "an annotation {name} has components from 0 to 1"
        )));
    }
    Ok(Object::Array(vec![
        Object::Real(red as f32),
        Object::Real(green as f32),
        Object::Real(blue as f32),
    ]))
}

/// Return the annotation dictionary that `spec` describes on a page, without
/// `/P` and `/AP`, after checking every entry.
fn annotation_dictionary(
    spec: &AnnotationSpec,
    (crop, rotation): ([f64; 4], i64),
) -> PyResult<Dictionary> {
    if !CREATED_SUBTYPES.contains(&spec.subtype.as_str()) {
        return Err(PdfError::new_err(format!(
            "pylopdf does not create {} annotations",
            spec.subtype
        )));
    }
    // The date is written twice, as /CreationDate and /M, and read back
    // under the same budget.
    let created = spec.created.as_deref().unwrap_or_default().as_bytes();
    validate_annotation_input(
        [
            spec.subtype.as_bytes(),
            spec.contents.as_deref().unwrap_or_default().as_bytes(),
            created,
            created,
        ],
        "subtype, contents, and dates",
    )?;
    let subtype = spec.subtype.as_str();
    let mut geometry = Dictionary::new();
    if spec.rects.is_some()
        != (annotation_appearance::is_text_markup(subtype.as_bytes()) || subtype == "Redact")
    {
        return Err(PdfError::new_err(format!(
            "rectangles place text markup, not a {subtype} annotation"
        )));
    }
    let rect = if let Some(rects) = &spec.rects {
        if rects.is_empty() || rects.len() > annotation_appearance::MAX_QUADS {
            return Err(PdfError::new_err(format!(
                "text markup covers 1 to {} rectangles",
                annotation_appearance::MAX_QUADS
            )));
        }
        let mut quad_points = Vec::new();
        quad_points
            .try_reserve_exact(rects.len() * 8)
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate QuadPoints: {error}"))
            })?;
        let mut bounds = [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ];
        let mut zigzag = 0usize;
        for &(x0, y0, x1, y1) in rects {
            if ![x0, y0, x1, y1].iter().all(|value| value.is_finite()) {
                return Err(PdfError::new_err("text markup rectangles are finite"));
            }
            if subtype == "Squiggly" {
                zigzag =
                    zigzag.saturating_add(((x1 - x0).abs() / 2.0).ceil().max(1.0) as usize + 1);
            }
            for (x, y) in draw::display_rect_quad_pdf(crop, rotation, [x0, y0, x1, y1]) {
                bounds = [
                    bounds[0].min(x),
                    bounds[1].min(y),
                    bounds[2].max(x),
                    bounds[3].max(y),
                ];
                quad_points.push(Object::Real(x as f32));
                quad_points.push(Object::Real(y as f32));
            }
        }
        if zigzag > annotation_appearance::MAX_ANNOTATION_POINTS {
            return Err(limit_err(
                "annotation_point_count",
                format!(
                    "a squiggly line of more than {} points",
                    annotation_appearance::MAX_ANNOTATION_POINTS
                ),
            ));
        }
        geometry.set("QuadPoints", Object::Array(quad_points));
        bounds
    } else if matches!(
        subtype,
        "Square" | "Circle" | "Text" | "FreeText" | "Stamp" | "Caret" | "FileAttachment"
    ) {
        let Some((x0, y0, x1, y1)) = spec.rect.filter(|_| spec.points.is_none()) else {
            return Err(PdfError::new_err(format!(
                "a {subtype} annotation takes a rectangle"
            )));
        };
        if ![x0, y0, x1, y1].iter().all(|value| value.is_finite()) {
            return Err(PdfError::new_err("an annotation rectangle is finite"));
        }
        let quad = draw::display_rect_quad_pdf(crop, rotation, [x0, y0, x1, y1]);
        draw::bounding_rect_iter(quad.iter().copied())
    } else {
        let Some(lists) = spec.points.as_ref().filter(|_| spec.rect.is_none()) else {
            return Err(PdfError::new_err(format!(
                "a {subtype} annotation takes points"
            )));
        };
        let shape_ok = match subtype {
            "Line" => lists.len() == 1 && lists[0].len() == 2,
            "Polygon" | "PolyLine" => lists.len() == 1 && lists[0].len() >= 2,
            _ => !lists.is_empty() && lists.iter().all(|points| !points.is_empty()),
        };
        if !shape_ok {
            return Err(PdfError::new_err(format!(
                "a {subtype} annotation has {}",
                match subtype {
                    "Line" => "two points",
                    "Ink" => "one or more strokes of one or more points",
                    _ => "two or more vertices",
                }
            )));
        }
        let total = lists
            .iter()
            .try_fold(0usize, |total, points| total.checked_add(points.len()));
        if total.is_none_or(|total| total > annotation_appearance::MAX_ANNOTATION_POINTS) {
            return Err(limit_err(
                "annotation_point_count",
                format!(
                    "an annotation holds at most {} points",
                    annotation_appearance::MAX_ANNOTATION_POINTS
                ),
            ));
        }
        let mut arrays = Vec::new();
        arrays.try_reserve_exact(lists.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate annotation points: {error}"))
        })?;
        let mut bounds = [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ];
        for points in lists {
            let mut array = Vec::new();
            array.try_reserve_exact(points.len() * 2).map_err(|error| {
                PdfError::new_err(format!("failed to allocate annotation points: {error}"))
            })?;
            for &(x, y) in points {
                if !(x.is_finite() && y.is_finite()) {
                    return Err(PdfError::new_err("annotation points are finite"));
                }
                let (x, y) = draw::display_to_pdf(crop, rotation, x, y);
                bounds = [
                    bounds[0].min(x),
                    bounds[1].min(y),
                    bounds[2].max(x),
                    bounds[3].max(y),
                ];
                array.push(Object::Real(x as f32));
                array.push(Object::Real(y as f32));
            }
            arrays.push(Object::Array(array));
        }
        match subtype {
            "Line" => geometry.set("L", arrays.swap_remove(0)),
            "Polygon" | "PolyLine" => geometry.set("Vertices", arrays.swap_remove(0)),
            _ => geometry.set("InkList", Object::Array(arrays)),
        }
        let width = spec.border.as_ref().map_or(1.0, |border| border.0);
        let mut pad = width / 2.0 + 1.0;
        if let Some((start, end)) = &spec.line_endings {
            if !matches!(subtype, "Line" | "PolyLine") {
                return Err(PdfError::new_err(format!(
                    "a {subtype} annotation has no line endings"
                )));
            }
            for style in [start, end] {
                if !annotation_appearance::LINE_ENDINGS.contains(&style.as_str()) {
                    return Err(PdfError::new_err(format!("unknown line ending {style:?}")));
                }
            }
            if start != "None" || end != "None" {
                pad += 2.0 * annotation_appearance::ending_size(width);
            }
            geometry.set(
                "LE",
                Object::Array(vec![
                    Object::Name(start.as_bytes().to_vec()),
                    Object::Name(end.as_bytes().to_vec()),
                ]),
            );
        }
        [
            bounds[0] - pad,
            bounds[1] - pad,
            bounds[2] + pad,
            bounds[3] + pad,
        ]
    };
    let mut dict = dictionary! {
        "Type" => "Annot",
        "Subtype" => Object::Name(spec.subtype.as_bytes().to_vec()),
        "Rect" => Object::Array(rect.iter().map(|&value| Object::Real(value as f32)).collect()),
        // Printable.
        "F" => 4,
    };
    for (key, value) in geometry {
        dict.set(key, value);
    }
    if let Some(color) = spec.color {
        dict.set("C", rgb_array("colour", color)?);
    }
    if let Some(color) = spec.interior_color {
        dict.set("IC", rgb_array("interior colour", color)?);
    }
    if let Some((width, style, dashes)) = &spec.border {
        if !(width.is_finite() && *width >= 0.0) {
            return Err(PdfError::new_err(
                "an annotation border width is finite and not negative",
            ));
        }
        if !matches!(style.as_str(), "S" | "D" | "B" | "I" | "U") {
            return Err(PdfError::new_err(format!(
                "unknown annotation border style {style:?}"
            )));
        }
        if dashes.len() > annotation_appearance::MAX_BORDER_DASHES
            || (!dashes.is_empty() && !annotation_appearance::valid_dashes(dashes))
            || (style == "D") != !dashes.is_empty()
        {
            return Err(PdfError::new_err(format!(
                "a dashed border has 1 to {} non-negative dash lengths, and only a dashed one",
                annotation_appearance::MAX_BORDER_DASHES
            )));
        }
        let mut border = dictionary! {
            "W" => Object::Real(*width as f32),
            "S" => Object::Name(style.as_bytes().to_vec()),
        };
        if !dashes.is_empty() {
            border.set(
                "D",
                Object::Array(
                    dashes
                        .iter()
                        .map(|&dash| Object::Real(dash as f32))
                        .collect(),
                ),
            );
        }
        dict.set("BS", border);
    }
    if let Some(opacity) = spec.opacity {
        if !(opacity.is_finite() && opacity > 0.0 && opacity <= 1.0) {
            return Err(PdfError::new_err(
                "an annotation opacity is greater than 0 and at most 1",
            ));
        }
        if opacity < 1.0 {
            dict.set("CA", Object::Real(opacity as f32));
        }
    }
    if let Some(mode) = &spec.blend_mode {
        if !canvas::BLEND_MODES.contains(&mode.as_str()) {
            return Err(PdfError::new_err(format!("unknown blend mode {mode:?}")));
        }
        dict.set("BM", Object::Name(mode.as_bytes().to_vec()));
    }
    if let Some(contents) = &spec.contents {
        dict.set("Contents", text_string(contents));
    }
    let text_style = spec.text_color.is_some()
        || spec.font_size.is_some()
        || spec.font_name.is_some()
        || spec.align.is_some();
    let free_text = spec.font.is_some() || spec.callout.is_some() || spec.callout_ending.is_some();
    if free_text && subtype != "FreeText"
        || text_style && !matches!(subtype, "FreeText" | "Redact")
        || spec.overlay_text.is_some() && subtype != "Redact"
    {
        return Err(PdfError::new_err(format!(
            "text style and callouts belong to free text and overlay text to redactions, not a {subtype} annotation"
        )));
    }
    if subtype == "Redact" {
        validate_annotation_input(
            [
                spec.subtype.as_bytes(),
                spec.contents.as_deref().unwrap_or_default().as_bytes(),
                spec.overlay_text.as_deref().unwrap_or_default().as_bytes(),
            ],
            "subtype, contents, and overlay text",
        )?;
        if let Some(text) = &spec.overlay_text {
            dict.set("OverlayText", text_string(text));
        }
        default_appearance_entries(spec, &mut dict)?;
    }
    if spec.rotate.is_some() && !matches!(subtype, "FreeText" | "Stamp") {
        return Err(PdfError::new_err(format!(
            "a {subtype} annotation does not turn"
        )));
    }
    let turn = spec.rotate.unwrap_or(0);
    if turn % 90 != 0 {
        return Err(PdfError::new_err(
            "an annotation turns by a multiple of 90 degrees",
        ));
    }
    // Display turns are clockwise; /Rotate turns counterclockwise in user
    // space, which the page then turns clockwise by its own rotation.
    let user_turn = (rotation - turn).rem_euclid(360);
    // A caret or file icon points up as the page is displayed.
    if matches!(subtype, "FreeText" | "Stamp" | "Caret" | "FileAttachment") && user_turn != 0 {
        dict.set("Rotate", user_turn);
    }
    if subtype == "FreeText" {
        free_text_entries(spec, (crop, rotation), &mut dict)?;
    }
    if (spec.data.is_some() || spec.filename.is_some() || spec.description.is_some())
        != (subtype == "FileAttachment")
    {
        return Err(PdfError::new_err(format!(
            "a file attachment embeds data with a file name, not a {subtype} annotation"
        )));
    }
    if subtype == "FileAttachment" {
        let (Some(data), Some(filename)) = (&spec.data, &spec.filename) else {
            return Err(PdfError::new_err(
                "a file attachment embeds data with a file name",
            ));
        };
        validate_embedded_file_input_text(filename, None, spec.description.as_deref())?;
        if spec.max_size == Some(0) {
            return Err(PdfError::new_err(
                "max_size must be a positive integer or None",
            ));
        }
        if let Some(limit) = spec.max_size
            && data.len() > limit
        {
            return Err(limit_err(
                "embedded_file_size",
                format!(
                    "attachment input is {} bytes, exceeding the {limit}-byte limit",
                    data.len()
                ),
            ));
        }
        let icon = spec.icon.as_deref().unwrap_or("PushPin");
        if !FILE_ICONS.contains(&icon) {
            return Err(PdfError::new_err(format!(
                "unknown file attachment icon {icon:?}"
            )));
        }
        dict.set("Name", Object::Name(icon.as_bytes().to_vec()));
    }
    if spec.open.is_some() && subtype != "Text"
        || spec.icon.is_some() && !matches!(subtype, "Text" | "Stamp" | "FileAttachment")
    {
        return Err(PdfError::new_err(format!(
            "an icon belongs to a note or a stamp, and an open state to a note, not a {subtype} annotation"
        )));
    }
    if subtype == "Stamp" {
        let icon = spec.icon.as_deref().unwrap_or("Approved");
        if !annotation_appearance::STAMPS
            .iter()
            .any(|(name, _, _)| *name == icon)
        {
            return Err(PdfError::new_err(format!("unknown stamp {icon:?}")));
        }
        dict.set("Name", Object::Name(icon.as_bytes().to_vec()));
    }
    if subtype == "Text" {
        let icon = spec.icon.as_deref().unwrap_or("Note");
        if !NOTE_ICONS.contains(&icon) {
            return Err(PdfError::new_err(format!("unknown note icon {icon:?}")));
        }
        dict.set("Name", Object::Name(icon.as_bytes().to_vec()));
        dict.set("Open", spec.open.unwrap_or(false));
        // A note's icon neither zooms nor turns with the page (ISO 32000-2
        // §12.5.6.4); viewers that honour the flags keep it upright.
        dict.set("F", 4 | 8 | 16);
    }
    if let Some(created) = &spec.created {
        if !created.starts_with("D:") || !created.is_ascii() || created.len() > 64 {
            return Err(PdfError::new_err("an annotation date is a PDF date string"));
        }
        dict.set("CreationDate", Object::string_literal(created.as_bytes()));
        dict.set("M", Object::string_literal(created.as_bytes()));
    }
    Ok(dict)
}

/// Write the `/DA` and `/Q` of a free text or redaction: its text colour,
/// font, and size, and its alignment.
fn default_appearance_entries(spec: &AnnotationSpec, dict: &mut Dictionary) -> PyResult<()> {
    let (red, green, blue) = spec.text_color.unwrap_or((0.0, 0.0, 0.0));
    rgb_array("text colour", (red, green, blue))?;
    let size = spec.font_size.unwrap_or(11.0);
    if !(size.is_finite() && size > 0.0 && size <= 10_000.0) {
        return Err(PdfError::new_err(
            "a font size is greater than 0 and at most 10000",
        ));
    }
    let resource = match (&spec.font, spec.font_name.as_deref()) {
        (Some(_), None) => "PyloF",
        (None, Some(name)) => annotation_appearance::DA_FONTS
            .iter()
            .find(|(_, font)| *font == name)
            .map(|(resource, _)| *resource)
            .ok_or_else(|| PdfError::new_err(format!("unknown Standard 14 font {name:?}")))?,
        (None, None) => "Helv",
        (Some(_), Some(_)) => {
            return Err(PdfError::new_err(
                "a free text draws with an OpenType face or a Standard 14 font, not both",
            ));
        }
    };
    if let Some(font) = &spec.font
        && Python::attach(|py| font.borrow(py).standard_name().is_some())
    {
        return Err(PdfError::new_err(
            "name a Standard 14 font by its PostScript name, not as a face",
        ));
    }
    dict.set(
        "DA",
        Object::string_literal(format!(
            "/{resource} {} Tf {} {} {} rg",
            draw::fmt(size),
            draw::fmt(red),
            draw::fmt(green),
            draw::fmt(blue)
        )),
    );
    let align = spec.align.unwrap_or(0);
    if align > 2 {
        return Err(PdfError::new_err(
            "free text aligns left (0), centre (1), or right (2)",
        ));
    }
    dict.set("Q", i64::from(align));
    Ok(())
}

/// Write the entries of a free text: `/DA`, `/Q`, `/Rotate`, and with a
/// callout `/CL`, `/LE`, `/IT`, `/RD`, and a `/Rect` that holds the line and
/// its ending as well as the box.
fn free_text_entries(
    spec: &AnnotationSpec,
    (crop, rotation): ([f64; 4], i64),
    dict: &mut Dictionary,
) -> PyResult<()> {
    default_appearance_entries(spec, dict)?;
    let Some(points) = &spec.callout else {
        if spec.callout_ending.is_some() {
            return Err(PdfError::new_err("a callout ending needs a callout line"));
        }
        return Ok(());
    };
    if !(2..=3).contains(&points.len())
        || !points.iter().all(|(x, y)| x.is_finite() && y.is_finite())
    {
        return Err(PdfError::new_err(
            "a callout line has 2 or 3 finite points: the point it shows, an optional knee, and the box",
        ));
    }
    let ending = spec.callout_ending.as_deref().unwrap_or("None");
    if !annotation_appearance::LINE_ENDINGS.contains(&ending) {
        return Err(PdfError::new_err(format!("unknown line ending {ending:?}")));
    }
    let Ok(inner) = dict.get(b"Rect").and_then(Object::as_array).map(|rect| {
        rect.iter()
            .map(|value| f64::from(value.as_float().unwrap_or(0.0)))
            .collect::<Vec<_>>()
    }) else {
        return Err(PdfError::new_err("a free text needs its rectangle"));
    };
    let width = spec.border.as_ref().map_or(0.0, |border| border.0).max(1.0);
    let pad = width / 2.0
        + 1.0
        + if ending == "None" {
            0.0
        } else {
            2.0 * annotation_appearance::ending_size(width)
        };
    let mut outer = [inner[0], inner[1], inner[2], inner[3]];
    let mut line = Vec::with_capacity(points.len() * 2);
    for &(x, y) in points {
        let (x, y) = draw::display_to_pdf(crop, rotation, x, y);
        outer = [
            outer[0].min(x - pad),
            outer[1].min(y - pad),
            outer[2].max(x + pad),
            outer[3].max(y + pad),
        ];
        line.push(Object::Real(x as f32));
        line.push(Object::Real(y as f32));
    }
    dict.set("CL", Object::Array(line));
    dict.set("IT", "FreeTextCallout");
    if ending != "None" {
        dict.set("LE", Object::Name(ending.as_bytes().to_vec()));
    }
    dict.set(
        "Rect",
        Object::Array(
            outer
                .iter()
                .map(|&value| Object::Real(value as f32))
                .collect(),
        ),
    );
    dict.set(
        "RD",
        Object::Array(
            [
                inner[0] - outer[0],
                outer[3] - inner[3],
                outer[2] - inner[2],
                inner[1] - outer[1],
            ]
            .iter()
            .map(|&value| Object::Real(value.max(0.0) as f32))
            .collect(),
        ),
    );
    Ok(())
}

/// Implements [`_Document::add_annotation`].
pub(super) fn add_annotation(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    spec: AnnotationSpec,
) -> PyResult<u32> {
    let geometry = this.page_display_geometry(page_number)?;
    let mut dict = annotation_dictionary(&spec, geometry)?;
    let page_id = this.page_id(page_number)?;
    let annotation_append = this.prepare_page_annotation_append(page_id)?;
    let annotation_appearance::Appearance {
        mut canvas,
        matrix,
        fits,
        ..
    } = annotation_appearance::appearance_with(&this.doc, &dict, spec.font.as_ref())?
        .ok_or_else(|| PdfError::new_err("the annotation has no appearance to draw"))?;
    if !fits {
        return Err(PdfError::new_err(
            "the text does not fit in the free text's rectangle; enlarge it or use a smaller size",
        ));
    }
    let appearance = this.prepare_canvas_form(py, canvas.take_recording()?)?;

    this.invalidate_hayro_pdf_keeping_placements();
    let committed = commit_canvas_form(&mut this.doc, appearance, Some(matrix))?;
    dict.set("P", page_id);
    dict.set(
        "AP",
        dictionary! { "N" => Object::Reference(committed.form_id) },
    );
    if let (Some(data), Some(filename)) = (spec.data, spec.filename) {
        let size = i64::try_from(data.len()).unwrap_or(i64::MAX);
        let mut params = dictionary! { "Size" => size };
        if let Some(created) = &spec.created {
            params.set("CreationDate", Object::string_literal(created.as_bytes()));
            params.set("ModDate", Object::string_literal(created.as_bytes()));
        }
        let stream = this.doc.add_object(Stream::new(
            dictionary! { "Type" => "EmbeddedFile", "Params" => params },
            data,
        ));
        let mut filespec = dictionary! {
            "Type" => "Filespec",
            "F" => Object::string_literal(filename.clone()),
            "UF" => text_string(&filename),
            "EF" => dictionary! { "F" => stream, "UF" => stream },
        };
        if let Some(description) = &spec.description {
            filespec.set("Desc", text_string(description));
        }
        dict.set("FS", this.doc.add_object(filespec));
    }
    this.remember_canvas_placements(committed, Some(page_number));
    let annotation_id = this.doc.add_object(dict);
    this.commit_page_annotation_append(page_id, annotation_append, annotation_id)?;
    Ok(annotation_id.0)
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

/// The FileSpec dictionary of a file attachment, or `None` for another
/// annotation or one without a usable `/FS`.
fn annotation_filespec<'a>(
    doc: &'a Document,
    annotation: &'a Dictionary,
) -> Option<&'a Dictionary> {
    if !matches!(
        annotation.get(b"Subtype").and_then(Object::as_name),
        Ok(b"FileAttachment")
    ) {
        return None;
    }
    annotation
        .get(b"FS")
        .ok()
        .and_then(|filespec| deref_dict_ref(doc, filespec))
}

/// Implements [`_Document::annotation_file_info`].
pub(super) fn annotation_file_info(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
) -> PyResult<Option<EmbeddedFileInfo>> {
    py.detach(|| {
        let page_id = this.page_id(page_number)?;
        let (_, annotation) = this.annotation_at(page_id, index, xref)?;
        annotation_filespec(&this.doc, annotation)
            .map(|filespec| filespec_info(&this.doc, filespec))
            .transpose()
    })
}

/// Implements [`_Document::annotation_file_read`].
pub(super) fn annotation_file_read(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
    max_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    py.detach(|| {
        let page_id = this.page_id(page_number)?;
        let (_, annotation) = this.annotation_at(page_id, index, xref)?;
        let filespec = annotation_filespec(&this.doc, annotation)
            .ok_or_else(|| PdfError::new_err("the annotation embeds no file"))?;
        let stream = this
            .doc
            .get_object(embedded_stream_id(&this.doc, filespec)?)
            .and_then(Object::as_stream)
            .map_err(to_py_err)?;
        decoded_stream_content(stream, max_size, "embedded_file_size", "the attached file")
    })
}

/// Implements [`_Document::annotation_file_write`]: replace the attached
/// file's data with a new stream that keeps the old stream's entries other
/// than its filters and length, as `embfile_upd` does.
pub(super) fn annotation_file_write(
    this: &mut _Document,
    page_number: u32,
    index: u32,
    xref: u32,
    data: Vec<u8>,
    max_size: Option<usize>,
) -> PyResult<u32> {
    if max_size == Some(0) {
        return Err(PdfError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    if let Some(limit) = max_size
        && data.len() > limit
    {
        return Err(limit_err(
            "embedded_file_size",
            format!(
                "attachment input is {} bytes, exceeding the {limit}-byte limit",
                data.len()
            ),
        ));
    }
    let page_id = this.page_id(page_number)?;
    let (_, annotation) = this.annotation_at(page_id, index, xref)?;
    let filespec = annotation_filespec(&this.doc, annotation)
        .ok_or_else(|| PdfError::new_err("the annotation embeds no file"))?;
    let filespec_id = match annotation.get(b"FS") {
        Ok(Object::Reference(id)) => Some(*id),
        _ => None,
    };
    let mut stream_dict = embedded_stream_id(&this.doc, filespec)
        .ok()
        .and_then(|id| this.doc.get_object(id).ok())
        .and_then(|object| object.as_stream().ok())
        .map(|stream| stream.dict.clone())
        .unwrap_or_else(|| dictionary! { "Type" => "EmbeddedFile" });
    for key in [
        &b"Filter"[..],
        b"DecodeParms",
        b"DL",
        b"Length",
        b"F",
        b"FFilter",
        b"FDecodeParms",
    ] {
        stream_dict.remove(key);
    }
    let mut params = match stream_dict.get(b"Params") {
        Ok(Object::Dictionary(params)) => params.clone(),
        Ok(Object::Reference(id)) => this
            .doc
            .get_object(*id)
            .and_then(Object::as_dict)
            .cloned()
            .unwrap_or_default(),
        _ => Dictionary::new(),
    };
    params.remove(b"CheckSum");
    params.set("Size", i64::try_from(data.len()).unwrap_or(i64::MAX));
    stream_dict.set("Params", params);
    let (_, id) = edited_annotation(this, page_id, index, xref)?;
    let stream = this.doc.add_object(Stream::new(stream_dict, data));
    let ef = dictionary! { "F" => stream, "UF" => stream };
    let target = filespec_id.unwrap_or(id);
    if let Ok(object) = this
        .doc
        .get_object_mut(target)
        .and_then(Object::as_dict_mut)
    {
        if filespec_id.is_some() {
            object.set("EF", ef);
        } else if let Ok(Object::Dictionary(filespec)) = object.get_mut(b"FS") {
            filespec.set("EF", ef);
        }
    }
    Ok(id.0)
}

/// An edit of an annotation's properties, as the setters of `Annotation`
/// pass it: each entry present replaces that property, and `clear` names
/// the properties to remove. Geometry is in display space.
#[derive(FromPyObject)]
#[pyo3(from_item_all)]
pub(super) struct AnnotationEdit {
    #[pyo3(default)]
    rect: Option<(f64, f64, f64, f64)>,
    #[pyo3(default)]
    contents: Option<String>,
    #[pyo3(default)]
    author: Option<String>,
    #[pyo3(default)]
    subject: Option<String>,
    #[pyo3(default)]
    name: Option<String>,
    #[pyo3(default)]
    language: Option<String>,
    #[pyo3(default)]
    icon: Option<String>,
    #[pyo3(default)]
    color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    interior_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    opacity: Option<f64>,
    #[pyo3(default)]
    flags: Option<i64>,
    #[pyo3(default)]
    border: Option<(f64, String, Vec<f64>)>,
    #[pyo3(default)]
    blend_mode: Option<String>,
    #[pyo3(default)]
    rotate: Option<i64>,
    #[pyo3(default)]
    points: Option<Vec<Vec<(f64, f64)>>>,
    #[pyo3(default)]
    line_endings: Option<(String, String)>,
    #[pyo3(default)]
    font: Option<Py<_Font>>,
    #[pyo3(default)]
    modified: Option<String>,
    /// Always given, so that the edit is not all defaults.
    clear: Vec<String>,
}

/// The types whose `/Rect` an edit may move; the others follow their points.
const RECT_SUBTYPES: [&[u8]; 7] = [
    b"Square",
    b"Circle",
    b"Text",
    b"FreeText",
    b"Stamp",
    b"Caret",
    b"FileAttachment",
];

/// Set `/Rect` of a line, polygon, polyline, or ink annotation to the bounds
/// of its points, widened for its stroke and its line endings.
fn fit_point_rect(doc: &Document, dict: &mut Dictionary, subtype: &[u8]) {
    let Some(lists) = annotation_appearance::point_lists(doc, dict, subtype) else {
        return;
    };
    let mut bounds = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for &(x, y) in lists.iter().flatten() {
        bounds = [
            bounds[0].min(x),
            bounds[1].min(y),
            bounds[2].max(x),
            bounds[3].max(y),
        ];
    }
    if !bounds.iter().all(|value| value.is_finite()) {
        return;
    }
    let width = annotation_appearance::border(doc, dict).map_or(1.0, |border| border.width);
    let mut pad = width / 2.0 + 1.0;
    if annotation_appearance::line_endings(doc, dict) != [0, 0] {
        pad += 2.0 * annotation_appearance::ending_size(width);
    }
    dict.set(
        "Rect",
        Object::Array(
            [
                bounds[0] - pad,
                bounds[1] - pad,
                bounds[2] + pad,
                bounds[3] + pad,
            ]
            .iter()
            .map(|&value| Object::Real(value as f32))
            .collect(),
        ),
    );
}

/// Implements [`_Document::edit_annotation`]: change the annotation's
/// properties and draw its appearance again when the engine draws its type,
/// after checking every entry; a refusal changes nothing.
pub(super) fn edit_annotation(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
    edit: AnnotationEdit,
) -> PyResult<u32> {
    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let page_id = this.page_id(page_number)?;
    let (_, annotation) = this.annotation_at(page_id, index, xref)?;
    let mut dict = annotation.clone();
    let subtype = dict
        .get(b"Subtype")
        .map(|value| deref_object(&this.doc, value))
        .and_then(Object::as_name)
        .map(<[u8]>::to_vec)
        .unwrap_or_default();
    let texts = [
        &edit.contents,
        &edit.author,
        &edit.subject,
        &edit.name,
        &edit.language,
    ];
    validate_annotation_input(
        texts
            .iter()
            .map(|text| text.as_deref().unwrap_or_default().as_bytes()),
        "texts",
    )?;
    for (value, key) in texts
        .into_iter()
        .zip([&b"Contents"[..], b"T", b"Subj", b"NM", b"Lang"])
    {
        if let Some(text) = value {
            dict.set(key, text_string(text));
        }
    }
    for property in &edit.clear {
        let key: &[u8] = match property.as_str() {
            "contents" => b"Contents",
            "author" => b"T",
            "subject" => b"Subj",
            "name" => b"NM",
            "language" => b"Lang",
            "color" => b"C",
            "interior_color" => b"IC",
            "blend_mode" => b"BM",
            "border" => {
                dict.remove(b"Border");
                b"BS"
            }
            other => {
                return Err(PdfError::new_err(format!(
                    "an annotation property that cannot be removed: {other:?}"
                )));
            }
        };
        dict.remove(key);
    }
    if let Some(icon) = &edit.icon {
        let known = match subtype.as_slice() {
            b"Text" => NOTE_ICONS.contains(&icon.as_str()),
            b"Stamp" => annotation_appearance::STAMPS
                .iter()
                .any(|(name, _, _)| name == icon),
            b"FileAttachment" => FILE_ICONS.contains(&icon.as_str()),
            _ => {
                return Err(PdfError::new_err(
                    "only a note, a stamp, or a file attachment has an icon",
                ));
            }
        };
        if !known {
            return Err(PdfError::new_err(format!("unknown icon {icon:?}")));
        }
        dict.set("Name", Object::Name(icon.as_bytes().to_vec()));
    }
    if let Some(color) = edit.color {
        dict.set("C", rgb_array("colour", color)?);
    }
    if let Some(color) = edit.interior_color {
        dict.set("IC", rgb_array("interior colour", color)?);
    }
    if let Some(opacity) = edit.opacity {
        if !(opacity.is_finite() && opacity > 0.0 && opacity <= 1.0) {
            return Err(PdfError::new_err(
                "an annotation opacity is greater than 0 and at most 1",
            ));
        }
        if opacity < 1.0 {
            dict.set("CA", Object::Real(opacity as f32));
        } else {
            dict.remove(b"CA");
        }
    }
    if let Some(flags) = edit.flags {
        if !(0..=1023).contains(&flags) {
            return Err(PdfError::new_err("annotation flags are bits 1 to 10"));
        }
        dict.set("F", flags);
    }
    if let Some((width, style, dashes)) = &edit.border {
        if !(width.is_finite() && *width >= 0.0)
            || !matches!(style.as_str(), "S" | "D" | "B" | "I" | "U")
            || dashes.len() > annotation_appearance::MAX_BORDER_DASHES
            || (!dashes.is_empty() && !annotation_appearance::valid_dashes(dashes))
            || (style == "D") == dashes.is_empty()
        {
            return Err(PdfError::new_err(
                "the border is not one that pylopdf writes",
            ));
        }
        let mut border = dictionary! {
            "W" => Object::Real(*width as f32),
            "S" => Object::Name(style.as_bytes().to_vec()),
        };
        if !dashes.is_empty() {
            border.set(
                "D",
                Object::Array(
                    dashes
                        .iter()
                        .map(|&dash| Object::Real(dash as f32))
                        .collect(),
                ),
            );
        }
        dict.remove(b"Border");
        dict.set("BS", border);
    }
    if let Some(mode) = &edit.blend_mode {
        if !canvas::BLEND_MODES.contains(&mode.as_str()) {
            return Err(PdfError::new_err(format!("unknown blend mode {mode:?}")));
        }
        dict.set("BM", Object::Name(mode.as_bytes().to_vec()));
    }
    if let Some(turn) = edit.rotate {
        if !matches!(
            subtype.as_slice(),
            b"FreeText" | b"Stamp" | b"Caret" | b"FileAttachment"
        ) || turn % 90 != 0
        {
            return Err(PdfError::new_err(
                "free text, stamps, carets, and file attachments turn by multiples of 90 degrees",
            ));
        }
        let user_turn = (rotation - turn).rem_euclid(360);
        if user_turn == 0 {
            dict.remove(b"Rotate");
        } else {
            dict.set("Rotate", user_turn);
        }
    }
    if let Some((x0, y0, x1, y1)) = edit.rect {
        if !RECT_SUBTYPES.contains(&subtype.as_slice()) || dict.has(b"CL") {
            return Err(PdfError::new_err(
                "this annotation follows its points or callout; edit those instead of its rectangle",
            ));
        }
        if ![x0, y0, x1, y1].iter().all(|value| value.is_finite()) {
            return Err(PdfError::new_err("an annotation rectangle is finite"));
        }
        let quad = draw::display_rect_quad_pdf(crop, rotation, [x0, y0, x1, y1]);
        let rect = draw::bounding_rect_iter(quad.iter().copied());
        dict.set(
            "Rect",
            Object::Array(
                rect.iter()
                    .map(|&value| Object::Real(value as f32))
                    .collect(),
            ),
        );
    }
    let point_type = matches!(
        subtype.as_slice(),
        b"Line" | b"Polygon" | b"PolyLine" | b"Ink"
    );
    if let Some(lists) = &edit.points {
        let shape_ok = match subtype.as_slice() {
            b"Line" => lists.len() == 1 && lists[0].len() == 2,
            b"Polygon" | b"PolyLine" => lists.len() == 1 && lists[0].len() >= 2,
            b"Ink" => !lists.is_empty() && lists.iter().all(|points| !points.is_empty()),
            _ => {
                return Err(PdfError::new_err(
                    "only lines, polygons, polylines, and ink have points",
                ));
            }
        };
        if !shape_ok {
            return Err(PdfError::new_err(
                "a line has two points, a polygon or polyline two or more, and ink strokes one or more",
            ));
        }
        if lists.iter().map(Vec::len).sum::<usize>() > annotation_appearance::MAX_ANNOTATION_POINTS
        {
            return Err(limit_err(
                "annotation_point_count",
                format!(
                    "an annotation holds at most {} points",
                    annotation_appearance::MAX_ANNOTATION_POINTS
                ),
            ));
        }
        let mut arrays = Vec::new();
        for points in lists {
            let mut array = Vec::new();
            array.try_reserve_exact(points.len() * 2).map_err(|error| {
                PdfError::new_err(format!("failed to allocate annotation points: {error}"))
            })?;
            for &(x, y) in points {
                if !(x.is_finite() && y.is_finite()) {
                    return Err(PdfError::new_err("annotation points are finite"));
                }
                let (x, y) = draw::display_to_pdf(crop, rotation, x, y);
                array.push(Object::Real(x as f32));
                array.push(Object::Real(y as f32));
            }
            arrays.push(Object::Array(array));
        }
        match subtype.as_slice() {
            b"Line" => dict.set("L", arrays.swap_remove(0)),
            b"Polygon" | b"PolyLine" => dict.set("Vertices", arrays.swap_remove(0)),
            _ => dict.set("InkList", Object::Array(arrays)),
        }
    }
    if let Some((start, end)) = &edit.line_endings {
        if !matches!(subtype.as_slice(), b"Line" | b"PolyLine") {
            return Err(PdfError::new_err(
                "only lines and polylines have line endings",
            ));
        }
        for style in [start, end] {
            if !annotation_appearance::LINE_ENDINGS.contains(&style.as_str()) {
                return Err(PdfError::new_err(format!("unknown line ending {style:?}")));
            }
        }
        dict.set(
            "LE",
            Object::Array(vec![
                Object::Name(start.as_bytes().to_vec()),
                Object::Name(end.as_bytes().to_vec()),
            ]),
        );
    }
    if point_type {
        fit_point_rect(&this.doc, &mut dict, &subtype);
    }
    if let Some(modified) = &edit.modified {
        if !modified.starts_with("D:") || !modified.is_ascii() || modified.len() > 64 {
            return Err(PdfError::new_err("an annotation date is a PDF date string"));
        }
        dict.set("M", Object::string_literal(modified.as_bytes()));
    }
    // Draw the appearance again for the types the engine draws.
    let appearance = if annotation_appearance::draws(&subtype) {
        match annotation_appearance::appearance_with(&this.doc, &dict, edit.font.as_ref())? {
            Some(annotation_appearance::Appearance {
                mut canvas,
                matrix,
                fits,
                ..
            }) => {
                if !fits {
                    return Err(PdfError::new_err(
                        "the text does not fit in the annotation's rectangle",
                    ));
                }
                Some((
                    this.prepare_canvas_form(py, canvas.take_recording()?)?,
                    matrix,
                ))
            }
            None => None,
        }
    } else {
        None
    };

    let (_, id) = edited_annotation(this, page_id, index, xref)?;
    if let Some((form, matrix)) = appearance {
        let committed = commit_canvas_form(&mut this.doc, form, Some(matrix))?;
        dict.set(
            "AP",
            dictionary! { "N" => Object::Reference(committed.form_id) },
        );
        this.remember_canvas_placements(committed, Some(page_number));
    }
    if let Ok(object) = this.doc.get_object_mut(id) {
        *object = Object::Dictionary(dict);
    }
    Ok(id.0)
}

/// Find an annotation for an edit and return its object number, after
/// storing the page's direct annotation dictionaries as objects when it is
/// one, as `set_link` does. The caller has checked its input, and the caches
/// are invalidated here.
pub(super) fn edited_annotation(
    this: &mut _Document,
    page_id: ObjectId,
    index: u32,
    xref: u32,
) -> PyResult<(u32, ObjectId)> {
    let (index, _) = this.annotation_at(page_id, index, xref)?;
    this.invalidate_hayro_pdf_keeping_placements();
    if xref == 0 {
        this.indirect_page_annotations_mut(page_id)?;
    }
    match this
        .page_annotation_items(page_id)?
        .and_then(|items| items.get(index as usize))
    {
        Some(Object::Reference(id)) => Ok((index, *id)),
        _ => Err(PdfError::new_err(
            "the annotation changed while it was edited",
        )),
    }
}

/// The object number of the popup that an annotation names, when it is a
/// dictionary object.
fn popup_id(doc: &Document, annotation: &Dictionary) -> Option<ObjectId> {
    match annotation.get(b"Popup") {
        Ok(Object::Reference(id)) if doc.get_dictionary(*id).is_ok() => Some(*id),
        _ => None,
    }
}

/// Implements [`_Document::set_annotation_popup`]: move the annotation's
/// popup to a display rectangle, adding one that the page lists after it,
/// or remove the popup with `None`.
pub(super) fn set_annotation_popup(
    this: &mut _Document,
    page_number: u32,
    index: u32,
    xref: u32,
    rect: Option<(f64, f64, f64, f64)>,
) -> PyResult<u32> {
    let page_id = this.page_id(page_number)?;
    let (_, annotation) = this.annotation_at(page_id, index, xref)?;
    if matches!(
        annotation.get(b"Subtype").and_then(Object::as_name),
        Ok(b"Popup" | b"Link" | b"Widget")
    ) {
        return Err(PdfError::new_err("only a markup annotation has a popup"));
    }
    let existing = popup_id(&this.doc, annotation);
    let rect = rect
        .map(|rect| link_rect(this, page_id, rect))
        .transpose()?;
    let append = match (&rect, existing) {
        (Some(_), None) => Some(this.prepare_page_annotation_append(page_id)?),
        _ => None,
    };
    let (_, id) = edited_annotation(this, page_id, index, xref)?;
    match (rect, existing) {
        (Some(rect), Some(popup)) => {
            if let Ok(popup) = this.doc.get_object_mut(popup).and_then(Object::as_dict_mut) {
                popup.set("Rect", rect);
            }
        }
        (Some(rect), None) => {
            let popup = this.doc.add_object(dictionary! {
                "Type" => "Annot",
                "Subtype" => "Popup",
                "Rect" => rect,
                "Parent" => id,
                "P" => page_id,
                "Open" => false,
            });
            if let Ok(annotation) = this.doc.get_object_mut(id).and_then(Object::as_dict_mut) {
                annotation.set("Popup", popup);
            }
            if let Some(append) = append {
                this.commit_page_annotation_append(page_id, append, popup)?;
            }
        }
        (None, popup) => {
            if let Ok(annotation) = this.doc.get_object_mut(id).and_then(Object::as_dict_mut) {
                annotation.remove(b"Popup");
            }
            if let Some(popup) = popup {
                let items = this.indirect_page_annotations_mut(page_id)?;
                items.retain(|item| !matches!(item, Object::Reference(listed) if *listed == popup));
            }
        }
    }
    Ok(id.0)
}

/// Implements [`_Document::set_annotation_open`]: open or close the
/// annotation's popup, and a note's own `/Open`.
pub(super) fn set_annotation_open(
    this: &mut _Document,
    page_number: u32,
    index: u32,
    xref: u32,
    open: bool,
) -> PyResult<u32> {
    let page_id = this.page_id(page_number)?;
    let (_, annotation) = this.annotation_at(page_id, index, xref)?;
    let popup = popup_id(&this.doc, annotation);
    let is_note = matches!(
        annotation.get(b"Subtype").and_then(Object::as_name),
        Ok(b"Text")
    );
    if popup.is_none() && !is_note {
        return Err(PdfError::new_err(
            "the annotation has no popup to open; set popup_rect first",
        ));
    }
    let (_, id) = edited_annotation(this, page_id, index, xref)?;
    if let Some(popup) = popup
        && let Ok(popup) = this.doc.get_object_mut(popup).and_then(Object::as_dict_mut)
    {
        popup.set("Open", open);
    }
    if is_note && let Ok(annotation) = this.doc.get_object_mut(id).and_then(Object::as_dict_mut) {
        annotation.set("Open", open);
    }
    Ok(id.0)
}

/// Implements [`_Document::set_annotation_reply`]: make the annotation a
/// reply to another annotation that the page lists, `/IRT`, or no reply.
pub(super) fn set_annotation_reply(
    this: &mut _Document,
    page_number: u32,
    index: u32,
    xref: u32,
    target: Option<u32>,
) -> PyResult<u32> {
    let page_id = this.page_id(page_number)?;
    this.annotation_at(page_id, index, xref)?;
    let target = match target {
        Some(target) => {
            let listed = this.page_annotation_items(page_id)?.is_some_and(|items| {
                items
                    .iter()
                    .any(|item| matches!(item, Object::Reference(id) if id.0 == target))
            });
            if !listed || target == xref {
                return Err(PdfError::new_err(
                    "an annotation replies to another annotation of its page",
                ));
            }
            let generation = this
                .doc
                .objects
                .keys()
                .filter(|id| id.0 == target)
                .map(|id| id.1)
                .max()
                .unwrap_or(0);
            Some((target, generation))
        }
        None => None,
    };
    let (_, id) = edited_annotation(this, page_id, index, xref)?;
    if let Ok(annotation) = this.doc.get_object_mut(id).and_then(Object::as_dict_mut) {
        match target {
            Some(target) => annotation.set("IRT", Object::Reference(target)),
            None => {
                annotation.remove(b"IRT");
                annotation.remove(b"RT");
            }
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
