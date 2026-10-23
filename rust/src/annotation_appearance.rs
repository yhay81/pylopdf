//! Annotation appearances drawn on a canvas from the annotation dictionary.
//!
//! One function draws the normal appearance of each annotation type that
//! pylopdf creates, from the entries of the annotation dictionary alone, so
//! creation, property edits, and the appearances that the rendering snapshot
//! synthesizes for annotations without one all draw alike. The canvas covers
//! the annotation's `/Rect` in PDF user space, y down from its top edge, and
//! the returned matrix maps the composed canvas onto `/Rect`.

use lopdf::{Dictionary, Document, Object};
use pyo3::prelude::*;

use crate::canvas::{_Canvas, BLEND_MODES, StrokeArgs};

/// Bézier control-point distance of a quarter ellipse, as a fraction of its radius.
const KAPPA: f64 = 0.552_284_749_830_793_4;

/// The most dash lengths that an annotation's border pattern holds.
pub(crate) const MAX_BORDER_DASHES: usize = 32;

/// A drawn appearance: the canvas over the annotation rectangle and the
/// `/Matrix` that maps the composed canvas onto the rectangle.
pub(crate) struct Appearance {
    pub(crate) canvas: _Canvas,
    pub(crate) matrix: [f64; 6],
}

/// Whether the engine draws annotations of `subtype`.
pub(crate) fn draws(subtype: &[u8]) -> bool {
    matches!(
        subtype,
        b"Square" | b"Circle" | b"Line" | b"Polygon" | b"PolyLine" | b"Ink"
    )
}

/// The border of an annotation, from `/BS`, else `/Border`.
#[derive(Clone, PartialEq, Debug)]
pub(crate) struct Border {
    pub(crate) width: f64,
    /// The `/S` style name: `S`, `D`, `B`, `I`, or `U`.
    pub(crate) style: Vec<u8>,
    pub(crate) dashes: Vec<f64>,
}

fn resolve<'a>(doc: &'a Document, object: &'a Object) -> &'a Object {
    match object {
        Object::Reference(id) => doc.get_object(*id).unwrap_or(object),
        other => other,
    }
}

fn number(doc: &Document, object: &Object) -> Option<f64> {
    let value = match resolve(doc, object) {
        Object::Integer(value) => *value as f64,
        Object::Real(value) => f64::from(*value),
        _ => return None,
    };
    value.is_finite().then_some(value)
}

fn entry<'a>(doc: &'a Document, dict: &'a Dictionary, key: &[u8]) -> Option<&'a Object> {
    dict.get(key).ok().map(|value| resolve(doc, value))
}

/// The numbers of an array entry, or `None` when it is no array of at most
/// `max` numbers.
fn numbers(doc: &Document, dict: &Dictionary, key: &[u8], max: usize) -> Option<Vec<f64>> {
    let items = entry(doc, dict, key)?.as_array().ok()?;
    if items.len() > max {
        return None;
    }
    items.iter().map(|item| number(doc, item)).collect()
}

/// The normalized `/Rect`, or `None` without a finite one.
pub(crate) fn rect(doc: &Document, dict: &Dictionary) -> Option<[f64; 4]> {
    let values = numbers(doc, dict, b"Rect", 4)?;
    let [x0, y0, x1, y1] = values[..] else {
        return None;
    };
    Some([x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)])
}

/// An RGB colour of a `/C` or `/IC` array: gray, RGB, or CMYK converted
/// naively, as MuPDF does; `None` for an empty or unusable array.
pub(crate) fn color(doc: &Document, dict: &Dictionary, key: &[u8]) -> Option<(f64, f64, f64)> {
    let values: Vec<f64> = numbers(doc, dict, key, 4)?
        .into_iter()
        .map(|value| value.clamp(0.0, 1.0))
        .collect();
    match values[..] {
        [gray] => Some((gray, gray, gray)),
        [red, green, blue] => Some((red, green, blue)),
        [cyan, magenta, yellow, black] => Some((
            (1.0 - cyan) * (1.0 - black),
            (1.0 - magenta) * (1.0 - black),
            (1.0 - yellow) * (1.0 - black),
        )),
        _ => None,
    }
}

/// The border that `/BS`, else `/Border`, describes, or `None` without
/// either.
pub(crate) fn border(doc: &Document, dict: &Dictionary) -> Option<Border> {
    if let Some(style) = entry(doc, dict, b"BS").and_then(|value| value.as_dict().ok()) {
        let width = style
            .get(b"W")
            .ok()
            .and_then(|value| number(doc, value))
            .filter(|width| *width >= 0.0)
            .unwrap_or(1.0);
        let name = entry(doc, style, b"S")
            .and_then(|value| value.as_name().ok())
            .filter(|name| matches!(*name, b"S" | b"D" | b"B" | b"I" | b"U"))
            .unwrap_or(b"S")
            .to_vec();
        let dashes = if name == b"D" {
            numbers(doc, style, b"D", MAX_BORDER_DASHES)
                .filter(|dashes| valid_dashes(dashes))
                .unwrap_or_else(|| vec![3.0])
        } else {
            Vec::new()
        };
        return Some(Border {
            width,
            style: name,
            dashes,
        });
    }
    let items = entry(doc, dict, b"Border")?.as_array().ok()?;
    if !(3..=4).contains(&items.len()) {
        return None;
    }
    let width = number(doc, &items[2]).filter(|width| *width >= 0.0)?;
    let dashes = match items.get(3) {
        Some(pattern) => {
            let pattern = resolve(doc, pattern).as_array().ok()?;
            if pattern.len() > MAX_BORDER_DASHES {
                return None;
            }
            let dashes = pattern
                .iter()
                .map(|item| number(doc, item))
                .collect::<Option<Vec<f64>>>()?;
            if !valid_dashes(&dashes) {
                return None;
            }
            dashes
        }
        None => Vec::new(),
    };
    Some(Border {
        width,
        style: if dashes.is_empty() { b"S" } else { b"D" }.to_vec(),
        dashes,
    })
}

/// Whether a dash array is one that a stroke can draw: non-negative lengths
/// that do not sum to zero.
pub(crate) fn valid_dashes(dashes: &[f64]) -> bool {
    !dashes.is_empty()
        && dashes.iter().all(|dash| dash.is_finite() && *dash >= 0.0)
        && dashes.iter().sum::<f64>() > 0.0
}

/// The `/CA` opacity, 1 when absent or out of range.
pub(crate) fn opacity(doc: &Document, dict: &Dictionary) -> f64 {
    dict.get(b"CA")
        .ok()
        .and_then(|value| number(doc, value))
        .filter(|value| (0.0..=1.0).contains(value))
        .unwrap_or(1.0)
}

/// The canvas index of the `/BM` blend mode, `None` for Normal or none.
fn blend_mode(doc: &Document, dict: &Dictionary) -> Option<u8> {
    let name = entry(doc, dict, b"BM")?.as_name().ok()?;
    let index = BLEND_MODES
        .iter()
        .position(|mode| mode.as_bytes() == name)?;
    (index != 0).then(|| u8::try_from(index).ok()).flatten()
}

/// The `/RD` insets of a square or circle: left, top, right, bottom.
fn rect_differences(doc: &Document, dict: &Dictionary, size: (f64, f64)) -> [f64; 4] {
    let Some(values) = numbers(doc, dict, b"RD", 4) else {
        return [0.0; 4];
    };
    let [left, top, right, bottom] = values[..] else {
        return [0.0; 4];
    };
    if [left, top, right, bottom].iter().any(|value| *value < 0.0)
        || left + right >= size.0
        || top + bottom >= size.1
    {
        return [0.0; 4];
    }
    [left, top, right, bottom]
}

/// Draw the normal appearance of an annotation, or return `None` for a type
/// that the engine does not draw or a dictionary without a usable `/Rect`
/// or geometry.
pub(crate) fn appearance(doc: &Document, dict: &Dictionary) -> PyResult<Option<Appearance>> {
    let Some(subtype) = entry(doc, dict, b"Subtype").and_then(|value| value.as_name().ok()) else {
        return Ok(None);
    };
    if !draws(subtype) {
        return Ok(None);
    }
    let Some(rect) = rect(doc, dict) else {
        return Ok(None);
    };
    let [x0, y0, x1, y1] = rect;
    let points = match subtype {
        b"Square" | b"Circle" => Vec::new(),
        _ => match point_lists(doc, dict, subtype) {
            Some(points) => points,
            None => return Ok(None),
        },
    };
    let size = (x1 - x0, y1 - y0);
    let mut canvas = _Canvas::new(size.0, size.1, false)?;
    let paint = Paint::of(doc, dict);
    let depth = blend_mode(doc, dict)
        .map(|mode| canvas.push_blend(mode))
        .transpose()?;
    let to_canvas = |(x, y): (f64, f64)| (x - x0, y1 - y);
    match subtype {
        b"Square" | b"Circle" => {
            let stroke = paint.stroke(0, 0);
            let fill = paint.fill();
            if stroke.is_some() || fill.is_some() {
                // The shape lies inside /RD, with the stroke inside the rectangle.
                let [left, top, right, bottom] = rect_differences(doc, dict, size);
                let inset = if stroke.is_some() {
                    paint.width / 2.0
                } else {
                    0.0
                };
                let (a0, b0) = (left + inset, top + inset);
                let (a1, b1) = (size.0 - right - inset, size.1 - bottom - inset);
                if a1 > a0 && b1 > b0 {
                    let (operators, coordinates) = if subtype == b"Circle" {
                        ellipse(a0, b0, a1, b1)
                    } else {
                        ("mlllh".to_owned(), vec![a0, b0, a1, b0, a1, b1, a0, b1])
                    };
                    canvas.add_path(&operators, coordinates, fill, stroke)?;
                }
            }
        }
        b"Polygon" => {
            let stroke = paint.stroke(0, 1);
            let fill = paint.fill();
            if let Some(vertices) = points.first()
                && vertices.len() >= 2
                && (stroke.is_some() || fill.is_some())
            {
                let (mut operators, coordinates) = polyline(vertices, to_canvas);
                operators.push('h');
                canvas.add_path(&operators, coordinates, fill, stroke)?;
            }
        }
        b"Ink" => {
            if let Some(stroke) = paint.stroke(1, 1) {
                for line in &points {
                    if line.is_empty() {
                        continue;
                    }
                    let (mut operators, mut coordinates) = polyline(line, to_canvas);
                    if line.len() == 1 {
                        // A single point draws a dot with the round cap.
                        operators.push('l');
                        coordinates.extend_from_within(..2);
                    }
                    canvas.add_path(&operators, coordinates, None, Some(stroke.clone()))?;
                }
            }
        }
        _ => {
            // Line and PolyLine: the stroked line and its two endings.
            let Some(vertices) = points.first().filter(|vertices| vertices.len() >= 2) else {
                return Ok(None);
            };
            if let Some(stroke) = paint.stroke(0, 1) {
                let (operators, coordinates) = polyline(vertices, to_canvas);
                canvas.add_path(&operators, coordinates, None, Some(stroke))?;
            }
            let endings = line_endings(doc, dict);
            let last = vertices.len() - 1;
            for (ending, tip, from) in [
                (endings[0], vertices[0], vertices[1]),
                (endings[1], vertices[last], vertices[last - 1]),
            ] {
                draw_ending(&mut canvas, &paint, ending, tip, from, to_canvas)?;
            }
        }
    }
    if let Some(depth) = depth {
        canvas.pop(depth)?;
    }
    Ok(Some(Appearance {
        canvas,
        matrix: [1.0, 0.0, 0.0, 1.0, x0, y0],
    }))
}

/// The paints of an appearance: the stroke colour, interior colour,
/// border, and opacity of the dictionary.
struct Paint {
    stroke: Option<(f64, f64, f64)>,
    interior: Option<(f64, f64, f64)>,
    width: f64,
    dashes: Vec<f64>,
    alpha: f64,
}

impl Paint {
    fn of(doc: &Document, dict: &Dictionary) -> Self {
        let border = border(doc, dict).unwrap_or(Border {
            width: 1.0,
            style: b"S".to_vec(),
            dashes: Vec::new(),
        });
        Self {
            stroke: color(doc, dict, b"C"),
            interior: color(doc, dict, b"IC"),
            width: border.width,
            dashes: border.dashes,
            alpha: opacity(doc, dict),
        }
    }

    /// The stroke with `cap` and `join`, or `None` without a colour or width.
    fn stroke(&self, cap: u8, join: u8) -> Option<StrokeArgs> {
        self.stroke
            .filter(|_| self.width > 0.0)
            .map(|(red, green, blue)| {
                (
                    (red, green, blue, self.alpha),
                    self.width,
                    cap,
                    join,
                    10.0,
                    self.dashes.clone(),
                    0.0,
                )
            })
    }

    /// A solid stroke for line endings, which never dash.
    fn solid_stroke(&self) -> Option<StrokeArgs> {
        self.stroke(0, 0).map(|mut stroke| {
            stroke.5 = Vec::new();
            stroke
        })
    }

    fn fill(&self) -> Option<((f64, f64, f64, f64), bool)> {
        self.interior
            .map(|(red, green, blue)| ((red, green, blue, self.alpha), false))
    }
}

/// The path operators and canvas coordinates of a polyline.
fn polyline(
    points: &[(f64, f64)],
    to_canvas: impl Fn((f64, f64)) -> (f64, f64),
) -> (String, Vec<f64>) {
    let mut operators = String::with_capacity(points.len() + 1);
    let mut coordinates = Vec::with_capacity(points.len() * 2 + 2);
    for (index, &point) in points.iter().enumerate() {
        operators.push(if index == 0 { 'm' } else { 'l' });
        let (x, y) = to_canvas(point);
        coordinates.extend([x, y]);
    }
    (operators, coordinates)
}

/// The line-ending styles of `/LE` (ISO 32000-2 Table 179), in order.
pub(crate) const LINE_ENDINGS: [&str; 10] = [
    "None",
    "Square",
    "Circle",
    "Diamond",
    "OpenArrow",
    "ClosedArrow",
    "Butt",
    "ROpenArrow",
    "RClosedArrow",
    "Slash",
];

/// The start and end styles of `/LE`, by index into `LINE_ENDINGS`; an
/// absent or unknown style is `None`.
pub(crate) fn line_endings(doc: &Document, dict: &Dictionary) -> [usize; 2] {
    let Some(items) = entry(doc, dict, b"LE").and_then(|value| value.as_array().ok()) else {
        return [0, 0];
    };
    let style = |index: usize| {
        items
            .get(index)
            .and_then(|item| resolve(doc, item).as_name().ok())
            .and_then(|name| {
                LINE_ENDINGS
                    .iter()
                    .position(|known| known.as_bytes() == name)
            })
            .unwrap_or(0)
    };
    [style(0), style(1)]
}

/// The half size of a line ending drawn with stroke `width`.
pub(crate) fn ending_size(width: f64) -> f64 {
    3.0 * width.max(1.0)
}

/// Draw a line ending at `tip` of the segment that comes from `from`.
fn draw_ending(
    canvas: &mut _Canvas,
    paint: &Paint,
    ending: usize,
    tip: (f64, f64),
    from: (f64, f64),
    to_canvas: impl Fn((f64, f64)) -> (f64, f64),
) -> PyResult<()> {
    let (dx, dy) = (tip.0 - from.0, tip.1 - from.1);
    let length = dx.hypot(dy);
    if ending == 0 || !(length.is_finite() && length > 0.0) {
        return Ok(());
    }
    let size = ending_size(paint.width);
    // u points out of the line at the tip, n across it.
    let u = (dx / length, dy / length);
    let n = (-u.1, u.0);
    let at = |along: f64, across: f64| {
        to_canvas((
            tip.0 + u.0 * along + n.0 * across,
            tip.1 + u.1 * along + n.1 * across,
        ))
    };
    let back = size * 3f64.sqrt();
    let (closed, points): (bool, Vec<(f64, f64)>) = match LINE_ENDINGS[ending] {
        "Square" => (
            true,
            vec![
                at(size, size),
                at(size, -size),
                at(-size, -size),
                at(-size, size),
            ],
        ),
        "Diamond" => (
            true,
            vec![at(size, 0.0), at(0.0, -size), at(-size, 0.0), at(0.0, size)],
        ),
        "OpenArrow" => (false, vec![at(-back, size), at(0.0, 0.0), at(-back, -size)]),
        "ClosedArrow" => (true, vec![at(-back, size), at(0.0, 0.0), at(-back, -size)]),
        "ROpenArrow" => (false, vec![at(back, size), at(0.0, 0.0), at(back, -size)]),
        "RClosedArrow" => (true, vec![at(back, size), at(0.0, 0.0), at(back, -size)]),
        "Butt" => (false, vec![at(0.0, size), at(0.0, -size)]),
        "Slash" => {
            // 30 degrees clockwise from the perpendicular.
            let (along, across) = (size * 0.5, size * 3f64.sqrt() / 2.0);
            (false, vec![at(along, -across), at(-along, across)])
        }
        // Circle.
        _ => {
            let (cx, cy) = at(0.0, 0.0);
            let (operators, coordinates) = ellipse(cx - size, cy - size, cx + size, cy + size);
            let fill = paint.fill();
            let stroke = paint.solid_stroke();
            if fill.is_some() || stroke.is_some() {
                canvas.add_path(&operators, coordinates, fill, stroke)?;
            }
            return Ok(());
        }
    };
    let mut operators = String::with_capacity(points.len() + 1);
    let mut coordinates = Vec::with_capacity(points.len() * 2);
    for (index, (x, y)) in points.into_iter().enumerate() {
        operators.push(if index == 0 { 'm' } else { 'l' });
        coordinates.extend([x, y]);
    }
    let fill = if closed {
        operators.push('h');
        paint.fill()
    } else {
        None
    };
    let stroke = paint.solid_stroke();
    if fill.is_some() || stroke.is_some() {
        canvas.add_path(&operators, coordinates, fill, stroke)?;
    }
    Ok(())
}

/// The most points that one annotation's geometry holds.
pub(crate) const MAX_ANNOTATION_POINTS: usize = 65_536;

/// The point lists of a Line (`/L`), Polygon or PolyLine (`/Vertices`), or
/// Ink annotation (`/InkList`) in user space, or `None` when the geometry is
/// missing, malformed, or holds more than `MAX_ANNOTATION_POINTS` points.
pub(crate) fn point_lists(
    doc: &Document,
    dict: &Dictionary,
    subtype: &[u8],
) -> Option<Vec<Vec<(f64, f64)>>> {
    let pairs = |items: &[Object], total: &mut usize| -> Option<Vec<(f64, f64)>> {
        if !items.len().is_multiple_of(2) {
            return None;
        }
        *total = total.checked_add(items.len() / 2)?;
        if *total > MAX_ANNOTATION_POINTS {
            return None;
        }
        let mut points = Vec::new();
        points.try_reserve_exact(items.len() / 2).ok()?;
        for pair in items.as_chunks::<2>().0 {
            points.push((number(doc, &pair[0])?, number(doc, &pair[1])?));
        }
        Some(points)
    };
    let mut total = 0;
    match subtype {
        b"Line" => {
            let items = entry(doc, dict, b"L")?.as_array().ok()?;
            (items.len() == 4).then_some(())?;
            Some(vec![pairs(items, &mut total)?])
        }
        b"Polygon" | b"PolyLine" => {
            let items = entry(doc, dict, b"Vertices")?.as_array().ok()?;
            Some(vec![pairs(items, &mut total)?])
        }
        b"Ink" => {
            let strokes = entry(doc, dict, b"InkList")?.as_array().ok()?;
            if strokes.len() > MAX_ANNOTATION_POINTS {
                return None;
            }
            let mut lists = Vec::new();
            lists.try_reserve_exact(strokes.len()).ok()?;
            for stroke in strokes {
                let items = resolve(doc, stroke).as_array().ok()?;
                lists.push(pairs(items, &mut total)?);
            }
            Some(lists)
        }
        _ => None,
    }
}

/// The path of the ellipse inscribed in a rectangle, as four Béziers.
fn ellipse(x0: f64, y0: f64, x1: f64, y1: f64) -> (String, Vec<f64>) {
    let (cx, cy) = ((x0 + x1) / 2.0, (y0 + y1) / 2.0);
    let (rx, ry) = ((x1 - x0) / 2.0, (y1 - y0) / 2.0);
    let (kx, ky) = (rx * KAPPA, ry * KAPPA);
    (
        "mcccch".to_owned(),
        vec![
            x1,
            cy,
            x1,
            cy + ky,
            cx + kx,
            y1,
            cx,
            y1,
            cx - kx,
            y1,
            x0,
            cy + ky,
            x0,
            cy,
            x0,
            cy - ky,
            cx - kx,
            y0,
            cx,
            y0,
            cx + kx,
            y0,
            x1,
            cy - ky,
            x1,
            cy,
        ],
    )
}
