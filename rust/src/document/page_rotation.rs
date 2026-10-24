//! `Page.remove_rotation`: turn a page's `/Rotate` into its content.
//!
//! The new user space is display space flipped upright: a point `p` of the
//! old user space moves to `M(p) = (dx, H - dy)`, where `(dx, dy)` is where
//! the old page displays it and `H` the display height. The page then shows
//! with `/Rotate 0` exactly what it showed before. The existing content is
//! drawn under `M`, the boxes, annotations, beads, and viewports are mapped
//! by `M`, appearances turn with the page, and every explicit destination
//! that names the page is mapped too. Everything is planned before the
//! document changes.

use super::*;

/// An affine matrix `[a b c d e f]`, as the operands of `cm`.
type Matrix = [f64; 6];

const IDENTITY: Matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];

/// The annotation flag that keeps an annotation upright on a turned page
/// (ISO 32000-2 Table 167).
const NO_ROTATE: i64 = 16;

/// The most objects that the destination scan reads.
const MAX_SCANNED_VALUES: usize = 16_777_216;

fn apply(m: &Matrix, x: f64, y: f64) -> (f64, f64) {
    (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])
}

/// `first` followed by `second`.
fn concat(first: &Matrix, second: &Matrix) -> Matrix {
    [
        first[0] * second[0] + first[1] * second[2],
        first[0] * second[1] + first[1] * second[3],
        first[2] * second[0] + first[3] * second[2],
        first[2] * second[1] + first[3] * second[3],
        first[4] * second[0] + first[5] * second[2] + second[4],
        first[4] * second[1] + first[5] * second[3] + second[5],
    ]
}

fn map_rect(m: &Matrix, [x0, y0, x1, y1]: [f64; 4]) -> [f64; 4] {
    let corners = [
        apply(m, x0, y0),
        apply(m, x1, y0),
        apply(m, x0, y1),
        apply(m, x1, y1),
    ];
    let mut out = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for (x, y) in corners {
        out[0] = out[0].min(x);
        out[1] = out[1].min(y);
        out[2] = out[2].max(x);
        out[3] = out[3].max(y);
    }
    out
}

fn number(value: f64) -> Object {
    // Right-angle maps of reals are exact up to the translation, which keeps
    // whole numbers whole.
    if value.fract() == 0.0 && value.abs() < 2_147_483_648.0 {
        Object::Integer(value as i64)
    } else {
        Object::Real(value as f32)
    }
}

fn rect_object(rect: [f64; 4]) -> Object {
    Object::Array(rect.into_iter().map(number).collect())
}

fn read_number(doc: &Document, object: &Object) -> Option<f64> {
    match deref_object(doc, object) {
        Object::Integer(value) => Some(*value as f64),
        Object::Real(value) => Some(f64::from(*value)),
        _ => None,
    }
}

fn read_rect(doc: &Document, object: &Object) -> Option<[f64; 4]> {
    let Object::Array(items) = deref_object(doc, object) else {
        return None;
    };
    let [a, b, c, d] = items.as_slice() else {
        return None;
    };
    let [x0, y0, x1, y1] = [a, b, c, d].map(|item| read_number(doc, item));
    Some([x0?.min(x1?), y0?.min(y1?), x0?.max(x1?), y0?.max(y1?)])
}

/// The matrix from the page's user space to the upright one, and the
/// rotation it removes.
fn upright_matrix(display: [f64; 4], rotation: i64) -> Matrix {
    let height = match rotation {
        90 | 270 => display[2] - display[0],
        _ => display[3] - display[1],
    };
    let (ox, oy) = draw::pdf_to_display(display, rotation, 0.0, 0.0);
    let (ax, ay) = draw::pdf_to_display(display, rotation, 1.0, 0.0);
    let (cx, cy) = draw::pdf_to_display(display, rotation, 0.0, 1.0);
    [ax - ox, oy - ay, cx - ox, oy - cy, ox, height - oy]
}

/// Map a flat array of coordinate pairs, or `None` when it is no such array.
fn map_points(doc: &Document, m: &Matrix, object: &Object) -> Option<Object> {
    let Object::Array(items) = deref_object(doc, object) else {
        return None;
    };
    if items.len() % 2 != 0 {
        return None;
    }
    let mut out = Vec::new();
    out.try_reserve_exact(items.len()).ok()?;
    for pair in items.chunks(2) {
        let (x, y) = apply(m, read_number(doc, &pair[0])?, read_number(doc, &pair[1])?);
        out.push(number(x));
        out.push(number(y));
    }
    Some(Object::Array(out))
}

/// The plan of one page's rotation removal, which `commit` stores.
struct RotationPlan {
    page_id: ObjectId,
    page: Dictionary,
    contents: draw::TransformContentPlan,
    /// Replaced indirect objects: annotations, their characteristics and
    /// appearance dictionaries, beads, and objects holding destinations.
    replaced: Vec<(ObjectId, Object)>,
    /// New appearance streams, with the IDs they were planned under.
    added: Vec<(ObjectId, Object)>,
    next_id: u32,
}

/// What maps an annotation's appearances: each original stream gets one
/// turned copy, shared by every annotation of the page that names it.
struct AppearanceCopies {
    turn: Matrix,
    copies: HashMap<ObjectId, ObjectId>,
    added: Vec<(ObjectId, Object)>,
    next_id: u32,
}

impl AppearanceCopies {
    /// The ID of the turned copy of appearance stream `id`.
    fn copy(&mut self, doc: &Document, id: ObjectId) -> PyResult<Option<ObjectId>> {
        if let Some(copy) = self.copies.get(&id) {
            return Ok(Some(*copy));
        }
        let Some(Object::Stream(stream)) = doc.objects.get(&id) else {
            return Ok(None);
        };
        let matrix = match stream.dict.get(b"Matrix").ok().and_then(|matrix| {
            match deref_object(doc, matrix) {
                Object::Array(items) if items.len() == 6 => {
                    let values: Option<Vec<f64>> =
                        items.iter().map(|item| read_number(doc, item)).collect();
                    values.map(|values| {
                        [
                            values[0], values[1], values[2], values[3], values[4], values[5],
                        ]
                    })
                }
                _ => None,
            }
        }) {
            Some(matrix) => matrix,
            None => IDENTITY,
        };
        let turned = concat(&matrix, &self.turn);
        let mut copy = stream.clone();
        copy.dict.set(
            "Matrix",
            Object::Array(turned.into_iter().map(number).collect()),
        );
        let copy_id = (self.next_id, 0);
        self.next_id = self
            .next_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        self.added.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to allocate appearance copies: {error}"))
        })?;
        self.added.push((copy_id, Object::Stream(copy)));
        self.copies.insert(id, copy_id);
        Ok(Some(copy_id))
    }

    /// Turn every appearance of `/AP`, a stream or a dictionary of states.
    fn turn_appearances(
        &mut self,
        doc: &Document,
        appearances: &Dictionary,
    ) -> PyResult<Dictionary> {
        let mut out = appearances.clone();
        for key in [&b"N"[..], b"R", b"D"] {
            let Ok(entry) = appearances.get(key) else {
                continue;
            };
            let turned = match entry {
                Object::Reference(id) => match doc.objects.get(id) {
                    Some(Object::Stream(_)) => self.copy(doc, *id)?.map(Object::Reference),
                    Some(Object::Dictionary(states)) => {
                        Some(Object::Dictionary(self.turn_states(doc, states)?))
                    }
                    _ => None,
                },
                Object::Dictionary(states) => {
                    Some(Object::Dictionary(self.turn_states(doc, states)?))
                }
                _ => None,
            };
            if let Some(turned) = turned {
                out.set(key, turned);
            }
        }
        Ok(out)
    }

    fn turn_states(&mut self, doc: &Document, states: &Dictionary) -> PyResult<Dictionary> {
        let mut out = states.clone();
        for (name, state) in states.iter() {
            if let Object::Reference(id) = state
                && let Some(copy) = self.copy(doc, *id)?
            {
                out.set(name.clone(), Object::Reference(copy));
            }
        }
        Ok(out)
    }
}

/// Map one annotation dictionary onto the upright page.
fn turn_annotation(
    doc: &Document,
    annotation: &Dictionary,
    m: &Matrix,
    rotation: i64,
    copies: &mut AppearanceCopies,
) -> PyResult<Dictionary> {
    let mut out = annotation.clone();
    let flags = annotation
        .get(b"F")
        .ok()
        .and_then(|flags| read_number(doc, flags))
        .unwrap_or(0.0) as i64;
    let upright = flags & NO_ROTATE != 0;
    if let Some(rect) = annotation
        .get(b"Rect")
        .ok()
        .and_then(|rect| read_rect(doc, rect))
    {
        let new = if upright {
            // The upper-left corner stays where it was and the appearance
            // stays upright, as ISO 32000-2 §12.5.3 keeps it.
            let (x, y) = apply(m, rect[0], rect[3]);
            [x, y - (rect[3] - rect[1]), x + (rect[2] - rect[0]), y]
        } else {
            map_rect(m, rect)
        };
        out.set("Rect", rect_object(new));
        if !upright
            && let Some(Object::Array(insets)) = annotation
                .get(b"RD")
                .ok()
                .map(|insets| deref_object(doc, insets))
            && let [left, top, right, bottom] = insets.as_slice()
            && let [Some(left), Some(top), Some(right), Some(bottom)] =
                [left, top, right, bottom].map(|value| read_number(doc, value))
        {
            let inner = map_rect(
                m,
                [
                    rect[0] + left,
                    rect[1] + bottom,
                    rect[2] - right,
                    rect[3] - top,
                ],
            );
            out.set(
                "RD",
                Object::Array(
                    [
                        inner[0] - new[0],
                        new[3] - inner[3],
                        new[2] - inner[2],
                        inner[1] - new[1],
                    ]
                    .into_iter()
                    .map(number)
                    .collect(),
                ),
            );
        }
    }
    for key in [&b"QuadPoints"[..], b"Vertices", b"L", b"CL"] {
        if let Some(points) = annotation
            .get(key)
            .ok()
            .and_then(|points| map_points(doc, m, points))
        {
            out.set(key, points);
        }
    }
    if let Some(Object::Array(strokes)) = annotation
        .get(b"InkList")
        .ok()
        .map(|ink| deref_object(doc, ink))
    {
        let mut mapped = Vec::new();
        mapped.try_reserve_exact(strokes.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate ink strokes: {error}"))
        })?;
        for stroke in strokes {
            mapped.push(map_points(doc, m, stroke).unwrap_or_else(|| stroke.clone()));
        }
        out.set("InkList", Object::Array(mapped));
    }
    if !upright {
        if let Some(appearances) = annotation
            .get(b"AP")
            .ok()
            .and_then(|ap| deref_dict_ref(doc, ap))
        {
            out.set(
                "AP",
                Object::Dictionary(copies.turn_appearances(doc, appearances)?),
            );
        }
        // A widget's rotation and a free text's or stamp's rotation are
        // counterclockwise on the page, which now turns with them.
        let turn = |value: Option<f64>| ((value.unwrap_or(0.0) as i64) - rotation).rem_euclid(360);
        if let Ok(value) = annotation.get(b"Rotate") {
            out.set("Rotate", Object::Integer(turn(read_number(doc, value))));
        }
        if annotation.get(b"Subtype").and_then(Object::as_name).ok() == Some(b"Widget") {
            let mut characteristics = annotation
                .get(b"MK")
                .ok()
                .and_then(|mk| deref_dict_ref(doc, mk))
                .cloned()
                .unwrap_or_default();
            let degrees = turn(
                characteristics
                    .get(b"R")
                    .ok()
                    .and_then(|r| read_number(doc, r)),
            );
            if degrees != 0 || characteristics.has(b"R") {
                characteristics.set("R", Object::Integer(degrees));
                out.set("MK", Object::Dictionary(characteristics));
            }
        }
    }
    Ok(out)
}

/// The destination fit types and how removing a quarter turn swaps them.
fn swapped_fit(fit: &[u8], rotation: i64) -> &[u8] {
    if rotation == 180 {
        return fit;
    }
    match fit {
        b"FitH" => b"FitV",
        b"FitV" => b"FitH",
        b"FitBH" => b"FitBV",
        b"FitBV" => b"FitBH",
        other => other,
    }
}

/// Map an explicit destination `[page /Fit ...]` that names `page_id`, or
/// leave anything else.
fn map_destination(items: &mut [Object], page_id: ObjectId, m: &Matrix, rotation: i64) -> bool {
    if items.first() != Some(&Object::Reference(page_id)) {
        return false;
    }
    let Some(Object::Name(fit)) = items.get(1).cloned() else {
        return false;
    };
    let value = |object: &Object| match object {
        Object::Integer(value) => Some(*value as f64),
        Object::Real(value) => Some(f64::from(*value)),
        _ => None,
    };
    let quarter = rotation != 180;
    // Each upright coordinate depends on one old coordinate: x on y and y on
    // x after a quarter turn, x on x and y on y after a half turn.
    let new_x = |x: Option<f64>, y: Option<f64>| {
        let source = if quarter { y } else { x };
        source.map(|source| {
            if quarter {
                m[2] * source + m[4]
            } else {
                m[0] * source + m[4]
            }
        })
    };
    let new_y = |x: Option<f64>, y: Option<f64>| {
        let source = if quarter { x } else { y };
        source.map(|source| {
            if quarter {
                m[1] * source + m[5]
            } else {
                m[3] * source + m[5]
            }
        })
    };
    let or_null = |value: Option<f64>| value.map_or(Object::Null, number);
    match fit.as_slice() {
        b"XYZ" => {
            let left = items.get(2).and_then(value);
            let top = items.get(3).and_then(value);
            if let Some(slot) = items.get_mut(2) {
                *slot = or_null(new_x(left, top));
            }
            if let Some(slot) = items.get_mut(3) {
                *slot = or_null(new_y(left, top));
            }
        }
        b"FitH" | b"FitBH" => {
            let top = items.get(2).and_then(value);
            if let Some(slot) = items.get_mut(2) {
                *slot = or_null(if quarter {
                    new_x(None, top)
                } else {
                    new_y(None, top)
                });
            }
            items[1] = Object::Name(swapped_fit(&fit, rotation).to_vec());
        }
        b"FitV" | b"FitBV" => {
            let left = items.get(2).and_then(value);
            if let Some(slot) = items.get_mut(2) {
                *slot = or_null(if quarter {
                    new_y(left, None)
                } else {
                    new_x(left, None)
                });
            }
            items[1] = Object::Name(swapped_fit(&fit, rotation).to_vec());
        }
        b"FitR" => {
            if let [_, _, left, bottom, right, top] = items
                && let [Some(x0), Some(y0), Some(x1), Some(y1)] =
                    [&*left, &*bottom, &*right, &*top].map(value)
            {
                let mapped = map_rect(m, [x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)]);
                *left = number(mapped[0]);
                *bottom = number(mapped[1]);
                *right = number(mapped[2]);
                *top = number(mapped[3]);
            }
        }
        _ => return false,
    }
    true
}

/// Whether `object` holds an explicit destination naming `page_id`.
fn holds_destination(object: &Object, page_id: ObjectId, budget: &mut usize) -> PyResult<bool> {
    let mut stack = Vec::new();
    stack
        .try_reserve(1)
        .map_err(|error| PdfError::new_err(format!("failed to scan destinations: {error}")))?;
    stack.push(object);
    while let Some(object) = stack.pop() {
        *budget = budget.checked_sub(1).ok_or_else(|| {
            limit_err(
                "destination_scan_size",
                format!("the destination scan reads more than {MAX_SCANNED_VALUES} values"),
            )
        })?;
        let children: Box<dyn Iterator<Item = &Object>> = match object {
            Object::Array(items) => {
                if items.first() == Some(&Object::Reference(page_id))
                    && matches!(items.get(1), Some(Object::Name(_)))
                {
                    return Ok(true);
                }
                Box::new(items.iter())
            }
            Object::Dictionary(dict) => Box::new(dict.iter().map(|(_, value)| value)),
            Object::Stream(stream) => Box::new(stream.dict.iter().map(|(_, value)| value)),
            _ => continue,
        };
        for child in children {
            stack.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to scan destinations: {error}"))
            })?;
            stack.push(child);
        }
    }
    Ok(false)
}

/// Map every explicit destination naming `page_id` inside `dict`.
fn rewrite_destinations(
    dict: &mut Dictionary,
    page_id: ObjectId,
    m: &Matrix,
    rotation: i64,
) -> PyResult<()> {
    let mut stack: Vec<&mut Object> = Vec::new();
    for (_, value) in dict.iter_mut() {
        stack.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to rewrite destinations: {error}"))
        })?;
        stack.push(value);
    }
    rewrite_stack(stack, page_id, m, rotation)
}

fn rewrite_stack(
    mut stack: Vec<&mut Object>,
    page_id: ObjectId,
    m: &Matrix,
    rotation: i64,
) -> PyResult<()> {
    while let Some(object) = stack.pop() {
        match object {
            Object::Array(items) => {
                if map_destination(items, page_id, m, rotation) {
                    continue;
                }
                stack.try_reserve(items.len()).map_err(|error| {
                    PdfError::new_err(format!("failed to rewrite destinations: {error}"))
                })?;
                stack.extend(items.iter_mut());
            }
            Object::Dictionary(dict) => {
                stack.try_reserve(dict.len()).map_err(|error| {
                    PdfError::new_err(format!("failed to rewrite destinations: {error}"))
                })?;
                stack.extend(dict.iter_mut().map(|(_, value)| value));
            }
            Object::Stream(stream) => {
                stack.try_reserve(stream.dict.len()).map_err(|error| {
                    PdfError::new_err(format!("failed to rewrite destinations: {error}"))
                })?;
                stack.extend(stream.dict.iter_mut().map(|(_, value)| value));
            }
            _ => {}
        }
    }
    Ok(())
}

impl _Document {
    fn plan_rotation_removal(
        &self,
        page_id: ObjectId,
        m: &Matrix,
        rotation: i64,
    ) -> PyResult<RotationPlan> {
        let doc = &self.doc;
        let mut page = resolve_inherited_page_dict(doc, page_id)?;
        page.set("Rotate", Object::Integer(0));
        let media = page
            .get(b"MediaBox")
            .ok()
            .and_then(|media| read_rect(doc, media))
            .unwrap_or([0.0, 0.0, draw::A4_WIDTH, draw::A4_HEIGHT]);
        page.set("MediaBox", rect_object(map_rect(m, media)));
        for key in [&b"CropBox"[..], b"BleedBox", b"TrimBox", b"ArtBox"] {
            if let Some(rect) = page.get(key).ok().and_then(|rect| read_rect(doc, rect)) {
                page.set(key, rect_object(map_rect(m, rect)));
            }
        }
        let contents = draw::prepare_transform_content(
            doc,
            page_id,
            self.isolated_content_pages.contains(&page_id),
            *m,
            |stream| {
                decoded_stream_content_cow(stream, None, "page_content_size", "page content").ok()
            },
        )
        .map_err(PdfError::new_err)?;
        let next_id = doc
            .max_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        let mut copies = AppearanceCopies {
            turn: [m[0], m[1], m[2], m[3], 0.0, 0.0],
            copies: HashMap::new(),
            added: Vec::new(),
            next_id,
        };
        let mut replaced = Vec::new();
        let mut handled = HashSet::new();
        // Annotations: indirect ones are replaced, direct ones rewritten in
        // the page's own `/Annots`.
        if let Some(Object::Array(entries)) = page
            .get(b"Annots")
            .ok()
            .map(|annots| deref_object(doc, annots))
            .cloned()
        {
            if entries.len() > MAX_PAGE_ANNOTATIONS {
                return Err(PdfError::new_err(format!(
                    "the page lists more than {MAX_PAGE_ANNOTATIONS} annotations"
                )));
            }
            let mut annots = Vec::new();
            annots.try_reserve_exact(entries.len()).map_err(|error| {
                PdfError::new_err(format!("failed to allocate annotations: {error}"))
            })?;
            for entry in entries {
                match &entry {
                    Object::Reference(id) => {
                        if let Some(Object::Dictionary(annotation)) = doc.objects.get(id)
                            && handled.insert(*id)
                        {
                            let mut turned =
                                turn_annotation(doc, annotation, m, rotation, &mut copies)?;
                            rewrite_destinations(&mut turned, page_id, m, rotation)?;
                            replaced.push((*id, Object::Dictionary(turned)));
                        }
                        annots.push(entry);
                    }
                    Object::Dictionary(annotation) => {
                        annots.push(Object::Dictionary(turn_annotation(
                            doc,
                            annotation,
                            m,
                            rotation,
                            &mut copies,
                        )?));
                    }
                    _ => annots.push(entry),
                }
            }
            page.set("Annots", Object::Array(annots));
        }
        // Article beads and viewports carry rectangles of the page.
        if let Some(Object::Array(beads)) =
            page.get(b"B").ok().map(|beads| deref_object(doc, beads))
        {
            for bead in beads {
                if let Object::Reference(id) = bead
                    && let Some(Object::Dictionary(dict)) = doc.objects.get(id)
                    && let Some(rect) = dict.get(b"R").ok().and_then(|rect| read_rect(doc, rect))
                    && handled.insert(*id)
                {
                    let mut dict = dict.clone();
                    dict.set("R", rect_object(map_rect(m, rect)));
                    replaced.push((*id, Object::Dictionary(dict)));
                }
            }
        }
        if let Some(Object::Array(viewports)) = page
            .get(b"VP")
            .ok()
            .map(|vp| deref_object(doc, vp))
            .cloned()
        {
            let mut mapped = Vec::new();
            for viewport in viewports {
                let mut dict = match deref_dict_ref(doc, &viewport) {
                    Some(dict) => dict.clone(),
                    None => {
                        mapped.push(viewport);
                        continue;
                    }
                };
                if let Some(rect) = dict.get(b"BBox").ok().and_then(|rect| read_rect(doc, rect)) {
                    dict.set("BBox", rect_object(map_rect(m, rect)));
                }
                mapped.push(Object::Dictionary(dict));
            }
            page.set("VP", Object::Array(mapped));
        }
        rewrite_destinations(&mut page, page_id, m, rotation)?;
        // Every other object that holds an explicit destination of the page:
        // outline items, links of other pages, named destinations, actions.
        let mut budget = MAX_SCANNED_VALUES;
        for (id, object) in &doc.objects {
            if *id == page_id
                || handled.contains(id)
                || !holds_destination(object, page_id, &mut budget)?
            {
                continue;
            }
            let mut object = object.clone();
            rewrite_stack(vec![&mut object], page_id, m, rotation)?;
            replaced.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to allocate destinations: {error}"))
            })?;
            replaced.push((*id, object));
        }
        let AppearanceCopies { added, next_id, .. } = copies;
        Ok(RotationPlan {
            page_id,
            page,
            contents,
            replaced,
            added,
            next_id,
        })
    }
}

/// Implements [`_Document::remove_page_rotation`]; returns the matrix from
/// the old user space to the upright one, the identity for an upright page.
pub(super) fn remove_page_rotation(this: &mut _Document, page_number: u32) -> PyResult<Matrix> {
    let page_id = this.page_id(page_number)?;
    let (display, rotation) = this.resolve_display_geometry(page_id)?;
    if rotation == 0 {
        return Ok(IDENTITY);
    }
    let m = upright_matrix(display, rotation);
    let plan = this.plan_rotation_removal(page_id, &m, rotation)?;
    this.invalidate_hayro_pdf_keeping_placements();
    let RotationPlan {
        page_id,
        mut page,
        contents,
        replaced,
        added,
        next_id,
    } = plan;
    for (id, object) in added.into_iter().chain(replaced) {
        this.doc.objects.insert(id, object);
    }
    this.doc.max_id = this.doc.max_id.max(next_id - 1);
    let contents = draw::add_transform_streams(&mut this.doc, contents);
    page.set("Contents", Object::Array(contents));
    this.doc.objects.insert(page_id, Object::Dictionary(page));
    this.isolated_content_pages.insert(page_id);
    Ok(m)
}
