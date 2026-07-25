//! AcroForm widget appearance primitives.

use lopdf::{Dictionary, Document, Object, Stream, dictionary};
use unicode_segmentation::UnicodeSegmentation;

use crate::draw;

/// Resolved widget dimensions, rotation, background, and border.
pub struct WidgetStyle {
    pub layout_width: f64,
    pub layout_height: f64,
    matrix: Option<[f64; 6]>,
    background: Option<PdfColor>,
    border: Option<PdfColor>,
    border_width: f64,
}

/// Auto-fit WinAnsi text using the canonical Helvetica metrics.
pub fn standard_text_ops(
    style: &WidgetStyle,
    text: &str,
    multiline: bool,
    align: u8,
    font_resource: &str,
) -> Result<Vec<u8>, String> {
    if text.is_empty() {
        return Ok(Vec::new());
    }
    let normalized = if multiline {
        text.to_owned()
    } else {
        text.chars()
            .map(|character| match character {
                '\r' | '\n' => ' ',
                other => other,
            })
            .collect()
    };
    let rect = style.content_rect();
    let box_size = (rect[2] - rect[0], rect[3] - rect[1]);
    if box_size.0 <= 0.0 || box_size.1 <= 0.0 {
        return Ok(Vec::new());
    }
    let mut font_size = box_size.1.min(12.0);
    let layout = loop {
        let candidate = draw::standard_textbox_layout(
            &normalized,
            box_size,
            "Helvetica",
            font_size,
            1.2,
            false,
        )?;
        if candidate.fits() && (multiline || candidate.lines.len() <= 1) {
            break candidate;
        }
        font_size *= 0.85;
        if font_size < 0.01 {
            return Err("form field text cannot fit inside the widget".to_owned());
        }
    };
    draw::textbox_text_ops(
        [0.0, 0.0, style.layout_width, style.layout_height],
        0,
        rect,
        &layout,
        align,
        font_resource,
        font_size,
        (0.0, 0.0, 0.0),
    )
}

/// Validate the text length for a comb field using grapheme-safe characters.
pub fn validate_comb_text(text: &str, max_len: usize) -> Result<(), String> {
    if max_len == 0 {
        return Err("comb field MaxLen must be positive".to_owned());
    }
    let count = normalized_single_line(text).graphemes(true).count();
    if count > max_len {
        return Err(format!(
            "comb field value has {count} characters, exceeding MaxLen {max_len}"
        ));
    }
    Ok(())
}

/// Auto-fit and center WinAnsi characters in an AcroForm comb field.
pub fn standard_comb_text_ops(
    style: &WidgetStyle,
    text: &str,
    max_len: usize,
    align: u8,
    font_resource: &str,
) -> Result<Vec<u8>, String> {
    validate_comb_text(text, max_len)?;
    let normalized = normalized_single_line(text);
    let graphemes = normalized.graphemes(true).collect::<Vec<_>>();
    if graphemes.is_empty() {
        return Ok(Vec::new());
    }

    let rect = style.content_rect();
    let box_size = (rect[2] - rect[0], rect[3] - rect[1]);
    if box_size.0 <= 0.0 || box_size.1 <= 0.0 {
        return Ok(Vec::new());
    }
    let cell_width = box_size.0 / max_len as f64;
    let mut font_size = box_size.1.min(12.0);
    let layouts = loop {
        let candidates = graphemes
            .iter()
            .map(|grapheme| {
                draw::standard_textbox_layout(
                    grapheme,
                    (cell_width, box_size.1),
                    "Helvetica",
                    font_size,
                    1.0,
                    false,
                )
            })
            .collect::<Result<Vec<_>, _>>()?;
        if candidates
            .iter()
            .all(|candidate| candidate.fits() && candidate.lines.len() == 1)
        {
            break candidates;
        }
        font_size *= 0.85;
        if font_size < 0.01 {
            return Err("comb field text cannot fit inside the widget".to_owned());
        }
    };

    let unused = max_len - graphemes.len();
    let start_slot = match align {
        1 => unused / 2,
        2 => unused,
        _ => 0,
    };
    let mut out = Vec::new();
    for (index, layout) in layouts.iter().enumerate() {
        let x0 = rect[0] + (start_slot + index) as f64 * cell_width;
        out.extend_from_slice(&draw::textbox_text_ops(
            [0.0, 0.0, style.layout_width, style.layout_height],
            0,
            [x0, rect[1], x0 + cell_width, rect[3]],
            layout,
            1,
            font_resource,
            font_size,
            (0.0, 0.0, 0.0),
        )?);
    }
    Ok(out)
}

fn normalized_single_line(text: &str) -> String {
    text.chars()
        .map(|character| match character {
            '\r' | '\n' => ' ',
            other => other,
        })
        .collect()
}

#[derive(Clone)]
enum PdfColor {
    Gray(f64),
    Rgb(f64, f64, f64),
    Cmyk(f64, f64, f64, f64),
}

impl WidgetStyle {
    /// Resolve widget `/Rect`, `/MK`, `/BS`, and legacy `/Border`.
    pub fn from_widget(doc: &Document, widget: &Dictionary) -> Result<Self, String> {
        let rect = resolve_array(doc, widget.get(b"Rect").map_err(|_| "widget has no Rect")?)
            .ok_or_else(|| "widget Rect is invalid".to_owned())?;
        if rect.len() != 4 {
            return Err("widget Rect must contain four numbers".to_owned());
        }
        let values = rect
            .iter()
            .map(|object| resolve_number(doc, object))
            .collect::<Option<Vec<_>>>()
            .ok_or_else(|| "widget Rect must contain four numbers".to_owned())?;
        let width = (values[2] - values[0]).abs();
        let height = (values[3] - values[1]).abs();
        if width <= 0.0 || height <= 0.0 || !width.is_finite() || !height.is_finite() {
            return Err("widget Rect must have positive finite dimensions".to_owned());
        }

        let mark = widget
            .get(b"MK")
            .ok()
            .and_then(|object| resolve_dict(doc, object));
        let rotation = mark
            .and_then(|dict| dict.get(b"R").ok())
            .and_then(|object| resolve_i64(doc, object))
            .unwrap_or(0)
            .rem_euclid(360);
        let rotation = if matches!(rotation, 90 | 180 | 270) {
            rotation
        } else {
            0
        };
        let background = mark
            .and_then(|dict| dict.get(b"BG").ok())
            .and_then(|object| parse_color(doc, object));
        let border = mark
            .and_then(|dict| dict.get(b"BC").ok())
            .and_then(|object| parse_color(doc, object))
            .or(Some(PdfColor::Gray(0.0)));
        let border_width = widget
            .get(b"BS")
            .ok()
            .and_then(|object| resolve_dict(doc, object))
            .and_then(|dict| dict.get(b"W").ok())
            .and_then(|object| resolve_number(doc, object))
            .or_else(|| {
                widget
                    .get(b"Border")
                    .ok()
                    .and_then(|object| resolve_array(doc, object))
                    .and_then(|array| array.get(2))
                    .and_then(|object| resolve_number(doc, object))
            })
            .unwrap_or(1.0)
            .clamp(0.0, width.min(height) / 2.0);

        let (layout_width, layout_height, matrix) = match rotation {
            90 => (height, width, Some([0.0, 1.0, -1.0, 0.0, width, 0.0])),
            180 => (width, height, Some([-1.0, 0.0, 0.0, -1.0, width, height])),
            270 => (height, width, Some([0.0, -1.0, 1.0, 0.0, 0.0, height])),
            _ => (width, height, None),
        };
        Ok(Self {
            layout_width,
            layout_height,
            matrix,
            background,
            border,
            border_width,
        })
    }

    /// Create a Form XObject appearance stream from local-coordinate content.
    pub fn stream(&self, resources: Option<Dictionary>, content: Vec<u8>) -> Stream {
        let mut dict = dictionary! {
            "Type" => "XObject",
            "Subtype" => "Form",
            "FormType" => 1,
            "BBox" => Object::Array(vec![
                Object::Real(0.0),
                Object::Real(0.0),
                Object::Real(self.layout_width as f32),
                Object::Real(self.layout_height as f32),
            ]),
        };
        if let Some(matrix) = self.matrix {
            dict.set(
                "Matrix",
                Object::Array(
                    matrix
                        .into_iter()
                        .map(|value| Object::Real(value as f32))
                        .collect(),
                ),
            );
        }
        if let Some(resources) = resources {
            dict.set("Resources", resources);
        }
        Stream::new(dict, content).with_compression(false)
    }

    /// Return a conservative top-left-origin text rectangle inside the border.
    pub fn content_rect(&self) -> [f64; 4] {
        let inset = (self.border_width + 1.0)
            .max(2.0)
            .min(self.layout_width.min(self.layout_height) / 3.0);
        [
            inset,
            inset,
            (self.layout_width - inset).max(inset),
            (self.layout_height - inset).max(inset),
        ]
    }

    /// Combine the widget decoration with clipped text/Form drawing operators.
    pub fn decorated_text_ops(&self, text_ops: &[u8]) -> Vec<u8> {
        let mut out = self.decoration_ops();
        if text_ops.is_empty() {
            return out;
        }
        let rect = self.content_rect();
        out.extend_from_slice(
            format!(
                "q\n{} {} {} {} re W n\n",
                fmt(rect[0]),
                fmt(rect[1]),
                fmt((rect[2] - rect[0]).max(0.0)),
                fmt((rect[3] - rect[1]).max(0.0)),
            )
            .as_bytes(),
        );
        out.extend_from_slice(text_ops);
        out.extend_from_slice(b"Q\n");
        out
    }

    /// Draw the widget background and rectangular border.
    pub fn decoration_ops(&self) -> Vec<u8> {
        let mut out = b"q\n".to_vec();
        if let Some(color) = &self.background {
            append_color(&mut out, color, false);
            out.extend_from_slice(
                format!(
                    "0 0 {} {} re f\n",
                    fmt(self.layout_width),
                    fmt(self.layout_height)
                )
                .as_bytes(),
            );
        }
        if self.border_width > 0.0
            && let Some(color) = &self.border
        {
            append_color(&mut out, color, true);
            let inset = self.border_width / 2.0;
            out.extend_from_slice(
                format!(
                    "{} w\n{} {} {} {} re S\n",
                    fmt(self.border_width),
                    fmt(inset),
                    fmt(inset),
                    fmt((self.layout_width - self.border_width).max(0.0)),
                    fmt((self.layout_height - self.border_width).max(0.0)),
                )
                .as_bytes(),
            );
        }
        out.extend_from_slice(b"Q\n");
        out
    }

    /// Build an Off/on checkbox or radio appearance.
    pub fn button_stream(&self, on: bool, radio: bool) -> Stream {
        let mut out = if radio {
            self.radio_decoration_ops()
        } else {
            self.decoration_ops()
        };
        if on {
            if radio {
                append_circle(
                    &mut out,
                    self.layout_width / 2.0,
                    self.layout_height / 2.0,
                    self.layout_width.min(self.layout_height) * 0.22,
                );
                out.extend_from_slice(b"0 g\nf\n");
            } else {
                let width = self.layout_width;
                let height = self.layout_height;
                out.extend_from_slice(
                    format!(
                        "q\n0 G\n{} w\n{} {} m\n{} {} l\n{} {} l\nS\nQ\n",
                        fmt((width.min(height) * 0.10).max(1.0)),
                        fmt(width * 0.20),
                        fmt(height * 0.52),
                        fmt(width * 0.42),
                        fmt(height * 0.27),
                        fmt(width * 0.82),
                        fmt(height * 0.76),
                    )
                    .as_bytes(),
                );
            }
        }
        self.stream(None, out)
    }

    fn radio_decoration_ops(&self) -> Vec<u8> {
        let mut out = b"q\n".to_vec();
        let radius = (self.layout_width.min(self.layout_height) - self.border_width) / 2.0;
        let center = (self.layout_width / 2.0, self.layout_height / 2.0);
        if let Some(color) = &self.background {
            append_color(&mut out, color, false);
            append_circle(&mut out, center.0, center.1, radius.max(0.0));
            out.extend_from_slice(b"f\n");
        }
        if self.border_width > 0.0
            && let Some(color) = &self.border
        {
            append_color(&mut out, color, true);
            out.extend_from_slice(format!("{} w\n", fmt(self.border_width)).as_bytes());
            append_circle(&mut out, center.0, center.1, radius.max(0.0));
            out.extend_from_slice(b"S\n");
        }
        out.extend_from_slice(b"Q\n");
        out
    }
}

fn parse_color(doc: &Document, object: &Object) -> Option<PdfColor> {
    let array = resolve_array(doc, object)?;
    let values = array
        .iter()
        .map(|item| resolve_number(doc, item).map(|value| value.clamp(0.0, 1.0)))
        .collect::<Option<Vec<_>>>()?;
    match values.as_slice() {
        [gray] => Some(PdfColor::Gray(*gray)),
        [red, green, blue] => Some(PdfColor::Rgb(*red, *green, *blue)),
        [cyan, magenta, yellow, black] => Some(PdfColor::Cmyk(*cyan, *magenta, *yellow, *black)),
        _ => None,
    }
}

fn append_color(out: &mut Vec<u8>, color: &PdfColor, stroke: bool) {
    let operator = match (color, stroke) {
        (PdfColor::Gray(gray), false) => format!("{} g\n", fmt(*gray)),
        (PdfColor::Gray(gray), true) => format!("{} G\n", fmt(*gray)),
        (PdfColor::Rgb(red, green, blue), false) => {
            format!("{} {} {} rg\n", fmt(*red), fmt(*green), fmt(*blue))
        }
        (PdfColor::Rgb(red, green, blue), true) => {
            format!("{} {} {} RG\n", fmt(*red), fmt(*green), fmt(*blue))
        }
        (PdfColor::Cmyk(cyan, magenta, yellow, black), false) => format!(
            "{} {} {} {} k\n",
            fmt(*cyan),
            fmt(*magenta),
            fmt(*yellow),
            fmt(*black)
        ),
        (PdfColor::Cmyk(cyan, magenta, yellow, black), true) => format!(
            "{} {} {} {} K\n",
            fmt(*cyan),
            fmt(*magenta),
            fmt(*yellow),
            fmt(*black)
        ),
    };
    out.extend_from_slice(operator.as_bytes());
}

fn append_circle(out: &mut Vec<u8>, cx: f64, cy: f64, radius: f64) {
    const KAPPA: f64 = 0.552_284_749_830_793_6;
    let control = radius * KAPPA;
    out.extend_from_slice(
        format!(
            "{} {} m\n{} {} {} {} {} {} c\n{} {} {} {} {} {} c\n{} {} {} {} {} {} c\n{} {} {} {} {} {} c\nh\n",
            fmt(cx + radius),
            fmt(cy),
            fmt(cx + radius),
            fmt(cy + control),
            fmt(cx + control),
            fmt(cy + radius),
            fmt(cx),
            fmt(cy + radius),
            fmt(cx - control),
            fmt(cy + radius),
            fmt(cx - radius),
            fmt(cy + control),
            fmt(cx - radius),
            fmt(cy),
            fmt(cx - radius),
            fmt(cy - control),
            fmt(cx - control),
            fmt(cy - radius),
            fmt(cx),
            fmt(cy - radius),
            fmt(cx + control),
            fmt(cy - radius),
            fmt(cx + radius),
            fmt(cy - control),
            fmt(cx + radius),
            fmt(cy),
        )
        .as_bytes(),
    );
}

fn resolve_dict<'a>(doc: &'a Document, object: &'a Object) -> Option<&'a Dictionary> {
    match object {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok(),
        Object::Dictionary(dict) => Some(dict),
        _ => None,
    }
}

fn resolve_array<'a>(doc: &'a Document, object: &'a Object) -> Option<&'a Vec<Object>> {
    match object {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_array().ok(),
        Object::Array(array) => Some(array),
        _ => None,
    }
}

fn resolve_number(doc: &Document, object: &Object) -> Option<f64> {
    match object {
        Object::Reference(id) => resolve_number(doc, doc.get_object(*id).ok()?),
        Object::Integer(value) => Some(*value as f64),
        Object::Real(value) => Some(f64::from(*value)),
        _ => None,
    }
}

fn resolve_i64(doc: &Document, object: &Object) -> Option<i64> {
    match object {
        Object::Reference(id) => resolve_i64(doc, doc.get_object(*id).ok()?),
        Object::Integer(value) => Some(*value),
        _ => None,
    }
}

fn fmt(value: f64) -> String {
    let normalized = if value.abs() < 1.0e-9 { 0.0 } else { value };
    format!("{normalized:.6}")
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_owned()
}
