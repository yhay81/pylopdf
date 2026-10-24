//! Redaction of a page: removing what areas cover, verified with hayro.
//!
//! `redact_page` runs inside a copy of the document that a failure
//! restores. It interprets the page with the content engine, removes the
//! glyphs, paths, shadings, and images that the areas reach (see
//! `crate::redact`), copies every Form placement that loses something,
//! removes the annotations that overlap an area, and then lets hayro
//! interpret the result: anything that hayro still paints or extracts inside
//! an area is removed in another pass, matched by its origin or box, and a
//! page where that does not converge is refused. Fills and overlay text are
//! drawn last, through the canvas.

use hayro::kurbo::{Point, Rect};
use std::collections::HashSet;

use crate::content::Interpretation;
use crate::content::syntax::Instructions;
use crate::redact::{
    self, ContainerEdits, GraphicsMode, ImageAction, ImageMode, OperationEdit, Options, Selection,
};

use super::*;

/// Passes of removal and verification before a page is refused.
const MAX_PASSES: usize = 3;
/// Areas that one call applies.
pub(super) const MAX_REDACTION_AREAS: usize = 4096;

/// One area to redact, in display space, with its fill and overlay text.
#[derive(Clone, Debug)]
struct Area {
    rect: Rect,
    fill: Option<(f64, f64, f64)>,
    overlay: Option<Overlay>,
}

#[derive(Clone, Debug)]
struct Overlay {
    text: String,
    font: String,
    size: f64,
    color: (f64, f64, f64),
    align: u8,
}

/// Areas passed to `redact_page`: display-space rectangles with their fills.
pub(super) type AreaInput = Vec<((f64, f64, f64, f64), Option<(f64, f64, f64)>)>;

/// What a redaction removed: areas in display space, glyphs, removed image
/// placements, blanked image placements, paths, shadings, annotations, and
/// Form XObject copies.
pub(super) type RedactionTuple = (
    Vec<(f64, f64, f64, f64)>,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
);

#[derive(Default)]
pub(super) struct Counts {
    pub(super) glyphs: usize,
    images_removed: usize,
    images_blanked: usize,
    paths: usize,
    shadings: usize,
    annotations: usize,
    forms: usize,
}

fn options_from(text: bool, images: &str, graphics: &str) -> PyResult<Options> {
    let images = match images {
        "keep" => ImageMode::Keep,
        "remove" => ImageMode::Remove,
        "pixels" => ImageMode::Pixels,
        other => {
            return Err(PyValueError::new_err(format!(
                "images must be 'pixels', 'remove', or 'keep': {other:?}"
            )));
        }
    };
    let graphics = match graphics {
        "keep" => GraphicsMode::Keep,
        "covered" => GraphicsMode::Covered,
        "touched" => GraphicsMode::Touched,
        other => {
            return Err(PyValueError::new_err(format!(
                "graphics must be 'touched', 'covered', or 'keep': {other:?}"
            )));
        }
    };
    Ok(Options {
        text,
        images,
        graphics,
    })
}

fn finite_rect((x0, y0, x1, y1): (f64, f64, f64, f64)) -> PyResult<Rect> {
    if ![x0, y0, x1, y1].iter().all(|value| value.is_finite()) {
        return Err(PyValueError::new_err("a redaction area must be finite"));
    }
    Ok(Rect::new(x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)))
}

/// Implements [`_Document::redact_page`].
#[allow(clippy::too_many_arguments)]
pub(super) fn redact_page(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    areas: Option<AreaInput>,
    text: bool,
    images: &str,
    graphics: &str,
    clip: Option<(f64, f64, f64, f64)>,
) -> PyResult<RedactionTuple> {
    let mut options = options_from(text, images, graphics)?;
    let page_id = this.page_id(page_number)?;
    let clip = clip.map(finite_rect).transpose()?;
    if let Some(clip) = clip {
        let to_user = replacement::page_display(this, page_id)?.inverse();
        options.graphics = GraphicsMode::Outside(crate::content::transformed_rect(clip, to_user));
    }
    if let Some(areas) = &areas
        && areas.len() > MAX_REDACTION_AREAS
    {
        return Err(limit_err(
            "redaction_area_count",
            format!("a redaction applies at most {MAX_REDACTION_AREAS} areas"),
        ));
    }
    let no_areas = match &areas {
        Some(areas) => areas.is_empty(),
        None => !this.page_annotation_items(page_id)?.unwrap_or_default().iter().any(|item| {
            crate::redact::resolve(&this.doc, item)
                .and_then(|item| item.as_dict().ok())
                .is_some_and(|dict| matches!(dict.get(b"Subtype"), Ok(Object::Name(name)) if name == b"Redact"))
        }),
    };
    if no_areas {
        return Ok((Vec::new(), 0, 0, 0, 0, 0, 0, 0));
    }
    let original = this.doc.clone();
    let isolated = this.isolated_content_pages.clone();
    let result = redact_inner(
        this,
        py,
        page_number,
        page_id,
        areas,
        options,
        clip,
        &original,
    );
    if result.as_ref().is_ok_and(|report| !report.0.is_empty()) {
        // The original bytes still hold what the redaction removed, so the
        // document can no longer be saved as an update to them.
        this.incremental_base = None;
    }
    if result.is_err() {
        this.doc.replace(original);
        this.isolated_content_pages = isolated;
        this.invalidate_hayro_pdf();
    }
    result
}

#[allow(clippy::too_many_arguments)]
fn redact_inner(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    page_id: ObjectId,
    areas: Option<AreaInput>,
    options: Options,
    clip: Option<Rect>,
    original: &Document,
) -> PyResult<RedactionTuple> {
    let display = replacement::page_display(this, page_id)?;
    let to_user = display.inverse();
    index_annotations(this, page_id)?;
    let (areas, applied) = match areas {
        Some(areas) => {
            let mut collected = Vec::new();
            for (rect, fill) in areas {
                collected.push(Area {
                    rect: finite_rect(rect)?,
                    fill,
                    overlay: None,
                });
            }
            (collected, Vec::new())
        }
        None => redaction_annotations(this, page_id, display)?,
    };
    if areas.is_empty() {
        return Ok((Vec::new(), 0, 0, 0, 0, 0, 0, 0));
    }
    let user_areas: Vec<Rect> = areas
        .iter()
        .map(|area| crate::content::transformed_rect(area.rect, to_user))
        .collect();
    let display_areas: Vec<Rect> = areas.iter().map(|area| area.rect).collect();

    let mut forced_glyphs: Vec<Point> = Vec::new();
    let mut forced_boxes: Vec<Rect> = Vec::new();
    for pass in 0..MAX_PASSES {
        if pass > 0 {
            this.doc.replace(original.clone());
            this.invalidate_hayro_pdf();
            index_annotations(this, page_id)?;
        }
        let counts = apply_pass(
            this,
            py,
            page_id,
            &Selector::Areas {
                areas: &user_areas,
                options,
                forced_glyphs: &forced_glyphs,
                forced_boxes: &forced_boxes,
            },
            &applied,
        )?;
        let leftovers = verify(this, py, page_number, &display_areas, options, clip)?;
        if leftovers.glyphs.is_empty() && leftovers.boxes.is_empty() {
            if let Some(clip) = clip {
                clip_content(
                    this,
                    page_id,
                    crate::content::transformed_rect(clip, to_user),
                )?;
            }
            draw_fills(this, py, page_number, &areas)?;
            // The replaced streams, forms, and images still hold what the
            // areas covered; whatever nothing reaches any more goes now,
            // not only at the next pruning save.
            prune_unreachable_objects(&mut this.doc)?;
            let areas = display_areas
                .iter()
                .map(|rect| (rect.x0, rect.y0, rect.x1, rect.y1))
                .collect();
            return Ok((
                areas,
                counts.glyphs,
                counts.images_removed,
                counts.images_blanked,
                counts.paths,
                counts.shadings,
                counts.annotations,
                counts.forms,
            ));
        }
        forced_glyphs.extend(leftovers.glyphs.iter().map(|point| to_user * *point));
        forced_boxes.extend(
            leftovers
                .boxes
                .iter()
                .map(|rect| crate::content::transformed_rect(*rect, to_user)),
        );
    }
    Err(PdfError::new_err(
        "redaction could not remove everything that the renderer still finds inside the areas",
    ))
}

/// Store the page's direct annotation dictionaries as objects, so that
/// their appearances have an object number to be attributed to.
fn index_annotations(this: &mut _Document, page_id: ObjectId) -> PyResult<()> {
    if this.page_annotation_items(page_id)?.is_some_and(|items| {
        items
            .iter()
            .any(|item| matches!(item, Object::Dictionary(_)))
    }) {
        this.invalidate_hayro_pdf_keeping_placements();
        this.indirect_page_annotations_mut(page_id)?;
    }
    Ok(())
}

/// The areas of the page's redaction annotations, with their fills and
/// overlay text, and the annotations themselves.
fn redaction_annotations(
    this: &_Document,
    page_id: ObjectId,
    display: hayro::kurbo::Affine,
) -> PyResult<(Vec<Area>, Vec<ObjectId>)> {
    let doc: &Document = &this.doc;
    let mut areas = Vec::new();
    let mut applied = Vec::new();
    for item in this.page_annotation_items(page_id)?.unwrap_or_default() {
        let Ok(id) = item.as_reference() else {
            continue;
        };
        let Ok(dict) = doc.get_dictionary(id) else {
            continue;
        };
        if !matches!(
            dict.get(b"Subtype").and_then(Object::as_name),
            Ok(b"Redact")
        ) {
            continue;
        }
        applied.push(id);
        let fill = annotation_appearance::color(doc, dict, b"IC");
        let overlay = dict
            .get(b"OverlayText")
            .ok()
            .and_then(|value| redact::resolve(doc, value))
            .and_then(|value| decode_text_string(value).ok())
            .filter(|text| !text.trim().is_empty())
            .map(|text| {
                let da = annotation_appearance::default_appearance(doc, dict);
                let align = match dict.get(b"Q") {
                    Ok(Object::Integer(value @ 0..=2)) => *value as u8,
                    _ => 0,
                };
                Overlay {
                    text,
                    font: annotation_appearance::da_font(&da.font).to_owned(),
                    size: if da.size > 0.0 { da.size } else { 11.0 },
                    color: da.color,
                    align,
                }
            });
        let quads = dict
            .get(b"QuadPoints")
            .ok()
            .and_then(|value| redact::resolve(doc, value))
            .and_then(|value| value.as_array().ok())
            .map(|values| {
                values
                    .iter()
                    .filter_map(|value| match redact::resolve(doc, value) {
                        Some(Object::Integer(value)) => Some(*value as f64),
                        Some(Object::Real(value)) => Some(f64::from(*value)),
                        _ => None,
                    })
                    .collect::<Vec<f64>>()
            })
            .filter(|values| values.len() >= 8 && values.len() % 8 == 0);
        let rects: Vec<Rect> = match quads {
            Some(values) => values
                .as_chunks::<8>()
                .0
                .iter()
                .map(|quad| {
                    let points: Vec<Point> = quad
                        .as_chunks::<2>()
                        .0
                        .iter()
                        .map(|pair| display * Point::new(pair[0], pair[1]))
                        .collect();
                    points
                        .iter()
                        .skip(1)
                        .fold(Rect::from_points(points[0], points[0]), |rect, point| {
                            rect.union_pt(*point)
                        })
                })
                .collect(),
            None => annotation_appearance::rect(doc, dict)
                .map(|[x0, y0, x1, y1]| {
                    vec![crate::content::transformed_rect(
                        Rect::new(x0, y0, x1, y1),
                        display,
                    )]
                })
                .unwrap_or_default(),
        };
        for (index, rect) in rects.into_iter().enumerate() {
            if !(rect.width() > 0.0 && rect.height() > 0.0) {
                continue;
            }
            // The overlay text goes into the first area.
            areas.push(Area {
                rect,
                fill,
                overlay: if index == 0 { overlay.clone() } else { None },
            });
        }
        if areas.len() > MAX_REDACTION_AREAS {
            return Err(limit_err(
                "redaction_area_count",
                format!("a redaction applies at most {MAX_REDACTION_AREAS} areas"),
            ));
        }
    }
    Ok((areas, applied))
}

/// The decoded data of a container's stream: the page's joined content or
/// a Form XObject's.
fn container_data(
    doc: &Document,
    page_id: ObjectId,
    interpretation: &Interpretation,
    container: usize,
) -> PyResult<Vec<u8>> {
    match redact::form_id(interpretation, container) {
        None => replacement::page_content(doc, page_id),
        Some(id) => {
            let stream = doc
                .get_object(id)
                .and_then(Object::as_stream)
                .map_err(to_py_err)?;
            replacement::interpreted_stream(stream).map_err(replacement::content_error)
        }
    }
}

/// The resources a container resolves names against, in the unedited
/// document.
fn effective_resources(
    doc: &Document,
    page_resources: Option<&Dictionary>,
    interpretation: &Interpretation,
    container: usize,
) -> Dictionary {
    let mut current = Some(container);
    while let Some(index) = current {
        if let Some(id) = redact::form_id(interpretation, index)
            && let Ok(stream) = doc.get_object(id).and_then(Object::as_stream)
            && let Some(resources) = stream
                .dict
                .get(b"Resources")
                .ok()
                .and_then(|value| redact::resolve(doc, value))
                .and_then(|value| value.as_dict().ok())
        {
            return resources.clone();
        }
        current = interpretation.containers[index]
            .parent
            .map(|(parent, _)| parent);
    }
    page_resources.cloned().unwrap_or_default()
}

/// Add XObject entries to a copy of `resources` and remove `dropped` ones.
fn with_xobjects(
    doc: &Document,
    mut resources: Dictionary,
    added: &[(Vec<u8>, ObjectId)],
    dropped: &[Vec<u8>],
) -> Dictionary {
    let mut xobjects = resources
        .get(b"XObject")
        .ok()
        .and_then(|value| redact::resolve(doc, value))
        .and_then(|value| value.as_dict().ok())
        .cloned()
        .unwrap_or_default();
    for name in dropped {
        xobjects.remove(name);
    }
    for (name, id) in added {
        xobjects.set(name.clone(), Object::Reference(*id));
    }
    resources.set("XObject", Object::Dictionary(xobjects));
    resources
}

fn xobject_names(doc: &Document, resources: &Dictionary) -> Dictionary {
    resources
        .get(b"XObject")
        .ok()
        .and_then(|value| redact::resolve(doc, value))
        .and_then(|value| value.as_dict().ok())
        .cloned()
        .unwrap_or_default()
}

const CONSTRUCTION: [&str; 7] = ["m", "l", "c", "v", "y", "h", "re"];

/// What one pass removes: what areas reach, or the glyphs that a test picks.
pub(super) enum Selector<'a> {
    Areas {
        areas: &'a [Rect],
        options: Options,
        forced_glyphs: &'a [Point],
        forced_boxes: &'a [Rect],
    },
    Glyphs(fn(&crate::content::interpret::Glyph) -> bool),
}

/// Remove what one selection reaches, write every container that changed,
/// and remove the annotations that overlap the areas.
pub(super) fn apply_pass(
    this: &mut _Document,
    py: Python<'_>,
    page_id: ObjectId,
    selector: &Selector<'_>,
    applied: &[ObjectId],
) -> PyResult<Counts> {
    let page_resources = this.resolve_page_attr(page_id, b"Resources")?;
    let page_resources = match page_resources {
        Some(Object::Dictionary(dict)) => Some(dict),
        _ => None,
    };
    let doc: &Document = &this.doc;
    let interpretation =
        py.detach(|| replacement::interpret(doc, page_id, page_resources.as_ref()))?;
    let (selection, areas) = match selector {
        Selector::Areas {
            areas,
            options,
            forced_glyphs,
            forced_boxes,
        } => {
            let mut selection = redact::select(&interpretation, areas, *options, forced_glyphs);
            force_boxes(&interpretation, &mut selection, forced_boxes, *options);
            (selection, *areas)
        }
        Selector::Glyphs(test) => {
            let selection = Selection {
                glyphs: interpretation.glyphs.iter().map(test).collect(),
                paths: vec![false; interpretation.paths.len()],
                shadings: vec![false; interpretation.shadings.len()],
                images: vec![ImageAction::Keep; interpretation.images.len()],
                annotations: HashSet::new(),
            };
            (selection, &[][..])
        }
    };
    let mut counts = Counts {
        glyphs: selection.glyph_count(),
        ..Counts::default()
    };

    // Every stream's data and operations, read once whatever the number of
    // its placements.
    let doc: &Document = &this.doc;
    let containers = &interpretation.containers;
    let mut stream_data: HashMap<crate::content::Container, Vec<u8>> = HashMap::new();
    for (container, info) in containers.iter().enumerate() {
        if info.annotation.is_none() && !stream_data.contains_key(&info.stream) {
            let data = container_data(doc, page_id, &interpretation, container)?;
            stream_data.insert(info.stream, data);
        }
    }
    let mut parsed: HashMap<crate::content::Container, Instructions<'_>> = HashMap::new();
    for (key, data) in &stream_data {
        parsed.insert(
            *key,
            Instructions::parse(data, None).map_err(replacement::content_error)?,
        );
    }
    let mut operation_cache: HashMap<crate::content::Container, Vec<lopdf::content::Operation>> =
        HashMap::new();
    let mut resources_cache: HashMap<usize, Dictionary> = HashMap::new();
    let mut resources_of = |container: usize| -> Dictionary {
        resources_cache
            .entry(container)
            .or_insert_with(|| {
                effective_resources(doc, page_resources.as_ref(), &interpretation, container)
            })
            .clone()
    };

    // Operations to change, by container.
    let mut edits: HashMap<usize, ContainerEdits> = HashMap::new();
    let editable = |container: usize| !redact::in_annotation(&interpretation, container);
    let removals =
        redact::text_removals(&interpretation, &selection).map_err(replacement::content_error)?;
    // The marked-content sequences of each container, by their start.
    let mut marked_by_container: HashMap<usize, Vec<(usize, Option<usize>)>> = HashMap::new();
    for marked in &interpretation.marked {
        marked_by_container
            .entry(marked.container)
            .or_default()
            .push((marked.begin, marked.end));
    }
    let mut removed_ops: HashMap<usize, Vec<usize>> = HashMap::new();
    for &(container, operation) in removals.keys() {
        if editable(container) {
            removed_ops.entry(container).or_default().push(operation);
        }
    }
    for (container, operations_removed) in &mut removed_ops {
        operations_removed.sort_unstable();
        let key = containers[*container].stream;
        let Some(instructions) = parsed.get(&key) else {
            continue;
        };
        if let std::collections::hash_map::Entry::Vacant(entry) = operation_cache.entry(key) {
            entry.insert(
                instructions
                    .to_operations()
                    .map_err(replacement::content_error)?,
            );
        }
        let operations = &operation_cache[&key];
        let container_edits = edits.entry(*container).or_default();
        for &operation in operations_removed.iter() {
            let bytes =
                redact::rewrite_text(&operations[operation], &removals[&(*container, operation)])
                    .map_err(replacement::content_error)?;
            let edit = if bytes.is_empty() {
                OperationEdit::Remove
            } else {
                OperationEdit::Replace(bytes)
            };
            container_edits.operations.insert(operation, edit);
        }
        // Replacement text in the marked content around the glyphs: one sweep
        // over the sequences, which nest, with the removed operations in order.
        let mut sequences = marked_by_container.remove(container).unwrap_or_default();
        sequences.sort_unstable_by_key(|sequence| sequence.0);
        let mut open: Vec<(usize, Option<usize>)> = Vec::new();
        let mut next = 0;
        let mut stripped = HashSet::new();
        for &operation in operations_removed.iter() {
            while next < sequences.len() && sequences[next].0 < operation {
                open.push(sequences[next]);
                next += 1;
            }
            open.retain(|(_, end)| end.is_none_or(|end| operation < end));
            for &(begin, _) in &open {
                if stripped.insert(begin)
                    && let Some(bytes) =
                        stripped_marked_content(doc, &operations[begin], &resources_of(*container))?
                {
                    container_edits
                        .operations
                        .insert(begin, OperationEdit::Replace(bytes));
                }
            }
        }
    }
    for (path, removed) in interpretation.paths.iter().zip(&selection.paths) {
        if !removed || !editable(path.container) {
            continue;
        }
        counts.paths += 1;
        let Some(instructions) = parsed.get(&containers[path.container].stream) else {
            continue;
        };
        let container_edits = edits.entry(path.container).or_default();
        if path.clip {
            // The path still clips; it only stops painting.
            container_edits
                .operations
                .insert(path.paint, OperationEdit::Replace(b"n".to_vec()));
        } else {
            for operation in path.start..=path.paint.min(instructions.len().saturating_sub(1)) {
                let operator = instructions.instruction(operation).operator;
                if operation == path.paint
                    || CONSTRUCTION.iter().any(|op| op.as_bytes() == operator)
                {
                    container_edits
                        .operations
                        .insert(operation, OperationEdit::Remove);
                }
            }
        }
    }
    for (shading, removed) in interpretation.shadings.iter().zip(&selection.shadings) {
        if *removed && editable(shading.container) {
            counts.shadings += 1;
            edits
                .entry(shading.container)
                .or_default()
                .operations
                .insert(shading.operation, OperationEdit::Remove);
        }
    }
    let mut blanked = Vec::new();
    for (index, (image, action)) in interpretation
        .images
        .iter()
        .zip(&selection.images)
        .enumerate()
    {
        if !editable(image.container) {
            continue;
        }
        match action {
            ImageAction::Keep => {}
            ImageAction::Remove => {
                counts.images_removed += 1;
                edits
                    .entry(image.container)
                    .or_default()
                    .operations
                    .insert(image.operation, OperationEdit::Remove);
            }
            ImageAction::Blank => blanked.push(index),
        }
    }
    // Content after the point where hayro stops reading may still draw in
    // other readers; every container written again, and every container that
    // holds such content, loses it.
    for (container, info) in containers.iter().enumerate() {
        if info.annotation.is_none()
            && parsed
                .get(&info.stream)
                .is_some_and(|instructions| !redact::is_blank(instructions.tail()))
        {
            edits.entry(container).or_default();
        }
    }

    drop(resources_of);
    // Blanked images become new image objects named in their container.
    let mut additions: HashMap<usize, Vec<(Vec<u8>, ObjectId)>> = HashMap::new();
    let mut taken: HashMap<usize, HashSet<Vec<u8>>> = HashMap::new();
    let mut new_objects: Vec<(ObjectId, Object)> = Vec::new();
    let mut next_id = this.doc.max_id;
    let mut allocate =
        |object: Object, new_objects: &mut Vec<(ObjectId, Object)>| -> PyResult<ObjectId> {
            next_id = next_id
                .checked_add(1)
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
            new_objects.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to allocate redacted objects: {error}"))
            })?;
            new_objects.push(((next_id, 0), object));
            Ok((next_id, 0))
        };
    for index in blanked {
        let image = &interpretation.images[index];
        let Some(image_objects) =
            super::redaction_images::blank_image(this, py, &interpretation, index, areas, page_id)?
        else {
            // An image that cannot be decoded is removed instead.
            counts.images_removed += 1;
            edits
                .entry(image.container)
                .or_default()
                .operations
                .insert(image.operation, OperationEdit::Remove);
            continue;
        };
        counts.images_blanked += 1;
        let (image_stream, mask) = image_objects;
        let mut image_stream = image_stream;
        if let Some(mask) = mask {
            let mask_id = allocate(Object::Stream(mask), &mut new_objects)?;
            image_stream.dict.set("SMask", Object::Reference(mask_id));
        }
        let id = allocate(Object::Stream(image_stream), &mut new_objects)?;
        let resources = effective_resources(
            &this.doc,
            page_resources.as_ref(),
            &interpretation,
            image.container,
        );
        let names = xobject_names(&this.doc, &resources);
        let name = redact::fresh_name(
            &names,
            taken.entry(image.container).or_default(),
            "PylopdfRedacted",
        );
        additions
            .entry(image.container)
            .or_default()
            .push((name.clone(), id));
        let replacement = redact::encode(vec![lopdf::content::Operation::new(
            "Do",
            vec![Object::Name(name)],
        )])
        .map_err(replacement::content_error)?;
        edits
            .entry(image.container)
            .or_default()
            .operations
            .insert(image.operation, OperationEdit::Replace(replacement));
    }

    // Write every container on the path from the page to an edit.
    let edited: HashSet<usize> = edits.keys().copied().collect();
    let needed =
        redact::containers_to_copy(&interpretation, &edited).map_err(replacement::content_error)?;
    let mut page_content: Option<Vec<u8>> = None;
    let mut page_dropped: Vec<Vec<u8>> = Vec::new();
    let doc: &Document = &this.doc;
    // The containers that resolve names through each container's resources:
    // the Forms it draws, and for the page, the annotation appearances.
    let mut children: HashMap<usize, Vec<usize>> = HashMap::new();
    for (index, info) in interpretation.containers.iter().enumerate() {
        match info.parent {
            Some((parent, _)) => children.entry(parent).or_default().push(index),
            None if info.annotation.is_some() => children.entry(0).or_default().push(index),
            None => {}
        }
    }
    for container in (0..interpretation.containers.len()).rev() {
        if !needed[container] {
            continue;
        }
        let Some(instructions) = parsed.get(&interpretation.containers[container].stream) else {
            continue;
        };
        let empty = ContainerEdits::default();
        let container_edits = edits.get(&container).unwrap_or(&empty);
        let bytes = py
            .detach(|| redact::rewrite_container(instructions, container_edits, true))
            .map_err(replacement::content_error)?;
        let added = additions.remove(&container).unwrap_or_default();
        // XObjects whose drawing went or now names a copy: their resource
        // entries go too, so the originals are no longer reachable from this
        // page, unless the new content or a Form that inherits these
        // resources still names them.
        let mut dropped = Vec::new();
        // A copy carries its own resources; an uncopied Form without them
        // still resolves names through these.
        let inherited = children
            .get(&container)
            .into_iter()
            .flatten()
            .any(|&index| {
                !needed[index]
                    && redact::form_id(&interpretation, index)
                        .and_then(|id| doc.get_object(id).ok())
                        .and_then(|object| object.as_stream().ok())
                        .is_some_and(|stream| !stream.dict.has(b"Resources"))
            });
        if !inherited {
            let still_used = {
                let rewritten =
                    Instructions::parse(&bytes, None).map_err(replacement::content_error)?;
                redact::drawn_names(&rewritten)
            };
            if redact::form_id(&interpretation, container).is_some() {
                // A copy's resources are its own: every XObject it no longer
                // draws goes, including the entries it inherited.
                let resources =
                    effective_resources(doc, page_resources.as_ref(), &interpretation, container);
                for (name, _) in &xobject_names(doc, &resources) {
                    if !still_used.contains(name) {
                        dropped.push(name.clone());
                    }
                }
            } else {
                for operation in container_edits.operations.keys() {
                    if let Some(name) = redact::drawn_name(instructions, *operation)
                        && !still_used.contains(&name)
                        && !dropped.contains(&name)
                    {
                        dropped.push(name);
                    }
                }
            }
        }
        match redact::form_id(&interpretation, container) {
            None => {
                page_content = Some(bytes);
                if !added.is_empty() || !dropped.is_empty() {
                    additions.insert(container, added);
                    page_dropped = dropped;
                }
            }
            Some(form) => {
                counts.forms += 1;
                let original = doc
                    .get_object(form)
                    .and_then(Object::as_stream)
                    .map_err(to_py_err)?;
                let mut dict = original.dict.clone();
                for key in [b"Length".as_slice(), b"Filter", b"DecodeParms", b"DL"] {
                    dict.remove(key);
                }
                // The copy names its resources itself, so that its parent can
                // drop the names that only the original needed.
                let resources =
                    effective_resources(doc, page_resources.as_ref(), &interpretation, container);
                dict.set(
                    "Resources",
                    Object::Dictionary(with_xobjects(doc, resources, &added, &dropped)),
                );
                let mut stream = Stream::new(dict, bytes);
                stream
                    .compress()
                    .map_err(|error| lopdf_err(Some("redacted form compression failed"), &error))?;
                let id = allocate(Object::Stream(stream), &mut new_objects)?;
                let Some((parent, operation)) = interpretation.containers[container].parent else {
                    continue;
                };
                let resources =
                    effective_resources(doc, page_resources.as_ref(), &interpretation, parent);
                let names = xobject_names(doc, &resources);
                let name =
                    redact::fresh_name(&names, taken.entry(parent).or_default(), "PylopdfRedacted");
                additions
                    .entry(parent)
                    .or_default()
                    .push((name.clone(), id));
                let replacement = redact::encode(vec![lopdf::content::Operation::new(
                    "Do",
                    vec![Object::Name(name)],
                )])
                .map_err(replacement::content_error)?;
                edits
                    .entry(parent)
                    .or_default()
                    .operations
                    .insert(operation, OperationEdit::Replace(replacement));
            }
        }
    }

    // Commit: new objects, the page's content and resources, annotations.
    let max_id = next_id;
    for (id, object) in new_objects {
        this.doc.objects.insert(id, object);
    }
    this.doc.max_id = this.doc.max_id.max(max_id);
    if let Some(content) = page_content {
        let mut stream = Stream::new(Dictionary::new(), content);
        stream
            .compress()
            .map_err(|error| lopdf_err(Some("redacted content compression failed"), &error))?;
        let id = this.doc.add_object(stream);
        this.doc
            .set_page_object_entry(page_id, "Contents", Object::Reference(id))?;
        if let Some(added) = additions.remove(&0) {
            let resources = page_resources.clone().unwrap_or_default();
            let resources = with_xobjects(&this.doc, resources, &added, &page_dropped);
            this.doc
                .set_page_object_entry(page_id, "Resources", Object::Dictionary(resources))?;
        }
        this.isolated_content_pages.remove(&page_id);
    }
    counts.annotations = remove_annotations(this, page_id, areas, &selection, applied)?;
    this.invalidate_hayro_pdf();
    Ok(counts)
}

/// Mark the paths, shadings, and images whose boxes verification found
/// inside an area.
fn force_boxes(
    interpretation: &Interpretation,
    selection: &mut Selection,
    boxes: &[Rect],
    options: Options,
) {
    let same = |a: Rect, b: Rect| {
        (a.x0 - b.x0).abs() <= 0.05
            && (a.y0 - b.y0).abs() <= 0.05
            && (a.x1 - b.x1).abs() <= 0.05
            && (a.y1 - b.y1).abs() <= 0.05
    };
    for rect in boxes {
        for (path, removed) in interpretation.paths.iter().zip(selection.paths.iter_mut()) {
            if same(path.bbox, *rect) {
                *removed = true;
            }
        }
        for (shading, removed) in interpretation
            .shadings
            .iter()
            .zip(selection.shadings.iter_mut())
        {
            if shading.clip.is_some_and(|clip| same(clip, *rect)) {
                *removed = true;
            }
        }
        for (image, action) in interpretation
            .images
            .iter()
            .zip(selection.images.iter_mut())
        {
            if *action == ImageAction::Keep && same(image.bbox(), *rect) {
                *action = if options.images == ImageMode::Pixels {
                    ImageAction::Blank
                } else {
                    ImageAction::Remove
                };
            }
        }
    }
}

/// A `BDC` operation without the replacement text of its property list, or
/// `None` when it holds none.
fn stripped_marked_content(
    doc: &Document,
    operation: &lopdf::content::Operation,
    resources: &Dictionary,
) -> PyResult<Option<Vec<u8>>> {
    if operation.operator != "BDC" || operation.operands.len() < 2 {
        return Ok(None);
    }
    let tag = operation.operands[operation.operands.len() - 2].clone();
    let properties = match operation.operands.last() {
        Some(Object::Dictionary(dict)) => Some(dict.clone()),
        Some(Object::Name(name)) => resources
            .get(b"Properties")
            .ok()
            .and_then(|value| redact::resolve(doc, value))
            .and_then(|value| value.as_dict().ok())
            .and_then(|properties| properties.get(name).ok())
            .and_then(|value| redact::resolve(doc, value))
            .and_then(|value| value.as_dict().ok())
            .cloned(),
        _ => None,
    };
    let Some(stripped) = properties.as_ref().and_then(redact::strip_replacement_text) else {
        return Ok(None);
    };
    redact::encode(vec![lopdf::content::Operation::new(
        "BDC",
        vec![tag, Object::Dictionary(stripped)],
    )])
    .map(Some)
    .map_err(replacement::content_error)
}

/// Remove the annotations that overlap an area or whose appearances paint
/// what the areas remove, with their popups, and the applied redaction
/// annotations. Widgets leave their fields, which go with their last widget.
fn remove_annotations(
    this: &mut _Document,
    page_id: ObjectId,
    areas: &[Rect],
    selection: &Selection,
    applied: &[ObjectId],
) -> PyResult<usize> {
    let items: Vec<Object> = this
        .page_annotation_items(page_id)?
        .unwrap_or_default()
        .to_vec();
    let mut removed: Vec<ObjectId> = Vec::new();
    let mut widgets: Vec<ObjectId> = Vec::new();
    let mut count = 0;
    for item in &items {
        let Ok(id) = item.as_reference() else {
            continue;
        };
        let Ok(dict) = this.doc.get_dictionary(id) else {
            continue;
        };
        let overlapping =
            annotation_appearance::rect(&this.doc, dict).is_some_and(|[x0, y0, x1, y1]| {
                let rect = Rect::new(x0, y0, x1, y1);
                areas.iter().any(|area| redact::overlaps(rect, *area))
            });
        let applied_here = applied.contains(&id);
        if !(overlapping || applied_here || selection.annotations.contains(&id)) {
            continue;
        }
        if !applied_here {
            count += 1;
        }
        let subtype = dict.get(b"Subtype").and_then(Object::as_name).ok();
        if subtype == Some(b"Widget".as_slice()) {
            widgets.push(id);
        } else {
            removed.push(id);
            if let Ok(Object::Reference(popup)) = dict.get(b"Popup") {
                removed.push(*popup);
            }
        }
    }
    for widget in widgets {
        this.remove_widget_object(widget)?;
    }
    if !removed.is_empty() {
        let items = this.indirect_page_annotations_mut(page_id)?;
        items.retain(|item| !matches!(item, Object::Reference(id) if removed.contains(id)));
    }
    Ok(count)
}

/// What hayro still paints or extracts inside an area: glyph origins, and
/// boxes of paths, shadings, and images, in display space.
#[derive(Default)]
struct Leftovers {
    glyphs: Vec<Point>,
    boxes: Vec<Rect>,
}

fn verify(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    areas: &[Rect],
    options: Options,
    kept: Option<Rect>,
) -> PyResult<Leftovers> {
    use crate::content_model::{ContentItem, ItemKinds};

    let settings = this.interpreter_settings();
    let content = this.contain_renderer_panics(|this| {
        let pdf = this.hayro_view_for(PageSelection::One(page_number))?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            crate::content_model::collect_page_content(
                pdf,
                page,
                settings,
                ItemKinds {
                    text_runs: true,
                    paths: true,
                    images: true,
                },
                None,
                None,
            )
            .map_err(|error| match error {
                crate::content_model::ContentError::ElementCount(limit) => limit_err(
                    "content_element_count",
                    format!("page content exceeds the {limit}-element limit"),
                ),
                crate::content_model::ContentError::Allocation(message) => {
                    PdfError::new_err(message)
                }
            })
        })
    })?;
    let rect = |(x0, y0, x1, y1): (f64, f64, f64, f64)| Rect::new(x0, y0, x1, y1);
    let touches = |bbox: Rect| areas.iter().any(|area| redact::overlaps(bbox, *area));
    let contains = |point: Point| {
        areas.iter().any(|area| {
            point.x > area.x0 && point.x < area.x1 && point.y > area.y0 && point.y < area.y1
        })
    };
    let mut leftovers = Leftovers::default();
    for item in &content.items {
        match item {
            ContentItem::Text(run) if options.text => {
                let ((_, _, matrices, _, inks), ..) = run;
                for (index, ink) in inks.iter().enumerate() {
                    let Some(matrix) = matrices.get(index * 6..index * 6 + 6) else {
                        continue;
                    };
                    let origin = Point::new(matrix[4], matrix[5]);
                    let inside = match ink {
                        Some(ink) => touches(rect(*ink)),
                        None => contains(origin),
                    };
                    if inside {
                        leftovers.glyphs.push(origin);
                    }
                }
            }
            ContentItem::Path((_, _, bbox, ..)) => {
                let bbox = rect(*bbox);
                let hit = match options.graphics {
                    GraphicsMode::Keep => false,
                    GraphicsMode::Outside(_) => {
                        kept.is_some_and(|kept| !redact::overlaps(bbox, kept))
                    }
                    GraphicsMode::Covered => areas.iter().any(|area| {
                        bbox.x0 >= area.x0
                            && bbox.x1 <= area.x1
                            && bbox.y0 >= area.y0
                            && bbox.y1 <= area.y1
                    }),
                    GraphicsMode::Touched => touches(bbox),
                };
                if hit {
                    leftovers.boxes.push(bbox);
                }
            }
            ContentItem::Image(image) if options.images == ImageMode::Remove => {
                let bbox = rect(image.7);
                if touches(bbox) {
                    leftovers.boxes.push(bbox);
                }
            }
            _ => {}
        }
    }
    if options.text {
        let layout = this.contain_renderer_panics(|this| {
            let settings = this.interpreter_settings();
            py.detach(|| {
                this.text_page(
                    (
                        page_number,
                        crate::extract::CollectOptions {
                            keep_clipped: true,
                            ..Default::default()
                        },
                    ),
                    settings,
                )?
                .raw_layout(&crate::extract::ViewOptions::default())
                .map_err(super::extraction::text_page_limit_err)
            })
        })?;
        for (_, lines) in &layout.2 {
            for (_, spans, ..) in lines {
                for (_, (_, chars), ..) in spans {
                    for (_, bbox, origin, synthetic) in chars {
                        if !synthetic && touches(rect(*bbox)) {
                            leftovers.glyphs.push(Point::new(origin.0, origin.1));
                        }
                    }
                }
            }
        }
    }
    Ok(leftovers)
}

/// Wrap the page's content in a clip to `rect`, in default user space, so
/// that what crosses its edge shows only inside.
fn clip_content(this: &mut _Document, page_id: ObjectId, rect: Rect) -> PyResult<()> {
    use crate::content::syntax::Instructions;

    let data = replacement::page_content(&this.doc, page_id)?;
    let instructions = Instructions::parse(&data, None).map_err(replacement::content_error)?;
    let (mut depth, mut unmatched) = (0_usize, 0_usize);
    for index in 0..instructions.len() {
        match instructions.instruction(index).operator {
            b"q" => depth += 1,
            b"Q" if depth == 0 => unmatched += 1,
            b"Q" => depth -= 1,
            _ => {}
        }
    }
    let mut content = Vec::new();
    let prefix = format!(
        "{}{} {} {} {} re W n\n",
        "q ".repeat(unmatched + 1),
        draw::fmt(rect.x0),
        draw::fmt(rect.y0),
        draw::fmt(rect.width()),
        draw::fmt(rect.height()),
    );
    content
        .try_reserve_exact(prefix.len() + data.len() + 2 * (depth + 2))
        .map_err(|error| {
            PdfError::new_err(format!("failed to allocate clipped content: {error}"))
        })?;
    content.extend_from_slice(prefix.as_bytes());
    content.extend_from_slice(&data);
    content.push(b'\n');
    content.extend_from_slice(" Q".repeat(depth + 1).as_bytes());
    let mut stream = Stream::new(Dictionary::new(), content);
    stream
        .compress()
        .map_err(|error| lopdf_err(Some("clipped content compression failed"), &error))?;
    let id = this.doc.add_object(stream);
    this.doc
        .set_page_object_entry(page_id, "Contents", Object::Reference(id))?;
    this.isolated_content_pages.remove(&page_id);
    this.invalidate_hayro_pdf();
    Ok(())
}

/// Draw the areas' fills and overlay text above the page content.
fn draw_fills(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    areas: &[Area],
) -> PyResult<()> {
    if areas
        .iter()
        .all(|area| area.fill.is_none() && area.overlay.is_none())
    {
        return Ok(());
    }
    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let (width, height) = draw::display_size(crop, rotation);
    let mut canvas =
        canvas::_Canvas::new(f64::from(width as f32), f64::from(height as f32), false)?;
    for area in areas {
        let Rect { x0, y0, x1, y1 } = area.rect;
        if let Some((red, green, blue)) = area.fill {
            canvas.add_path(
                "mlllh",
                vec![x0, y0, x1, y0, x1, y1, x0, y1],
                Some(((red, green, blue, 1.0), false)),
                None,
            )?;
        }
        if let Some(overlay) = &area.overlay {
            let (red, green, blue) = overlay.color;
            let font = Bound::new(py, _Font::standard(&overlay.font)?)?;
            // Text that does not fit at its size is drawn smaller, down to
            // one point, and left out below that.
            let mut size = overlay.size;
            while size >= 1.0 {
                let spare = canvas.add_textbox(
                    py,
                    (x0, y0, x1, y1),
                    &overlay.text,
                    &font,
                    size,
                    None,
                    overlay.align,
                    Some((red, green, blue, 1.0)),
                    None,
                    Some(DEFAULT_MAX_GENERATED_TEXT_SIZE),
                    0,
                )?;
                if spare >= 0.0 {
                    break;
                }
                size *= 0.8;
            }
        }
    }
    let recording = canvas.take_recording()?;
    this.place_canvas(py, page_number, recording, true)
}
