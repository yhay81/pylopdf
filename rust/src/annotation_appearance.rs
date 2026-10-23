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
    matches!(subtype, b"Square" | b"Circle")
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
/// that the engine does not draw or a dictionary without a usable `/Rect`.
pub(crate) fn appearance(doc: &Document, dict: &Dictionary) -> PyResult<Option<Appearance>> {
    let Some(subtype) = entry(doc, dict, b"Subtype").and_then(|value| value.as_name().ok()) else {
        return Ok(None);
    };
    if !draws(subtype) {
        return Ok(None);
    }
    let Some([x0, y0, x1, y1]) = rect(doc, dict) else {
        return Ok(None);
    };
    let size = (x1 - x0, y1 - y0);
    let mut canvas = _Canvas::new(size.0, size.1, false)?;
    let alpha = opacity(doc, dict);
    let stroke_color = color(doc, dict, b"C");
    let fill_color = color(doc, dict, b"IC");
    let border = border(doc, dict).unwrap_or(Border {
        width: 1.0,
        style: b"S".to_vec(),
        dashes: Vec::new(),
    });
    let stroke: Option<StrokeArgs> =
        stroke_color
            .filter(|_| border.width > 0.0)
            .map(|(red, green, blue)| {
                (
                    (red, green, blue, alpha),
                    border.width,
                    0,
                    0,
                    10.0,
                    border.dashes.clone(),
                    0.0,
                )
            });
    let fill = fill_color.map(|(red, green, blue)| ((red, green, blue, alpha), false));
    let blend = blend_mode(doc, dict);
    let depth = blend.map(|mode| canvas.push_blend(mode)).transpose()?;
    if stroke.is_some() || fill.is_some() {
        // The shape lies inside /RD, with the stroke inside the rectangle.
        let [left, top, right, bottom] = rect_differences(doc, dict, size);
        let inset = if stroke.is_some() {
            border.width / 2.0
        } else {
            0.0
        };
        let (a0, b0) = (left + inset, top + inset);
        let (a1, b1) = (size.0 - right - inset, size.1 - bottom - inset);
        if a1 > a0 && b1 > b0 {
            let (operators, coordinates) = if subtype == b"Circle" {
                ellipse(a0, b0, a1, b1)
            } else {
                ("mlllh", vec![a0, b0, a1, b0, a1, b1, a0, b1])
            };
            canvas.add_path(operators, coordinates, fill, stroke)?;
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

/// The path of the ellipse inscribed in a rectangle, as four Béziers.
fn ellipse(x0: f64, y0: f64, x1: f64, y1: f64) -> (&'static str, Vec<f64>) {
    let (cx, cy) = ((x0 + x1) / 2.0, (y0 + y1) / 2.0);
    let (rx, ry) = ((x1 - x0) / 2.0, (y1 - y0) / 2.0);
    let (kx, ky) = (rx * KAPPA, ry * KAPPA);
    (
        "mcccch",
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
