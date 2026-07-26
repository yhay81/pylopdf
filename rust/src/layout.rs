//! Bounded paragraph layout shared by Standard 14 and embedded-font text.

use unicode_linebreak::linebreaks;
use unicode_segmentation::UnicodeSegmentation;

const FIT_TOLERANCE: f64 = 1.0e-6;

/// One laid-out line in display coordinates.
pub struct TextBoxLine {
    pub text: String,
    pub width: f64,
    /// Expand whitespace only for a soft-wrapped, non-final paragraph line.
    pub justify: bool,
}

/// Textbox layout computed before any PDF objects are mutated.
pub struct TextBoxLayout {
    pub lines: Vec<TextBoxLine>,
    pub spare_height: f64,
    pub ascent: f64,
    pub leading: f64,
}

impl TextBoxLayout {
    pub fn fits(&self) -> bool {
        self.spare_height >= -FIT_TOLERANCE
    }
}

/// Greedily wrap text at UAX #14 opportunities, with grapheme-safe emergency
/// breaks for an otherwise overlong word.
#[allow(clippy::too_many_arguments)]
pub fn layout_textbox<F>(
    text: &str,
    box_size: (f64, f64),
    font_size: f64,
    line_height: f64,
    ascent: f64,
    descent: f64,
    justify: bool,
    mut measure: F,
) -> Result<TextBoxLayout, String>
where
    F: FnMut(&str) -> Result<f64, String>,
{
    let (width, height) = box_size;
    let mut lines = Vec::new();
    for paragraph in text.split('\n') {
        lines.extend(wrap_paragraph(paragraph, width, justify, &mut measure)?);
    }

    let leading = line_height * font_size;
    let glyph_height = (ascent + descent) * font_size;
    let required_height = if lines.is_empty() {
        0.0
    } else {
        glyph_height + (lines.len() - 1) as f64 * leading
    };
    let mut spare_height = height - required_height;
    if spare_height.abs() <= FIT_TOLERANCE {
        spare_height = 0.0;
    }
    Ok(TextBoxLayout {
        lines,
        spare_height,
        ascent,
        leading,
    })
}

fn wrap_paragraph<F>(
    paragraph: &str,
    max_width: f64,
    justify: bool,
    measure: &mut F,
) -> Result<Vec<TextBoxLine>, String>
where
    F: FnMut(&str) -> Result<f64, String>,
{
    if paragraph.is_empty() {
        return Ok(vec![TextBoxLine {
            text: String::new(),
            width: 0.0,
            justify: false,
        }]);
    }

    // Avoid measuring every UAX #14 prefix when the complete paragraph already
    // fits. Besides the common short-line case, this keeps very wide textboxes
    // linear rather than repeatedly shaping or encoding growing prefixes.
    let visible = paragraph.trim_end_matches(char::is_whitespace);
    let paragraph_width = measure(visible)?;
    if paragraph_width <= max_width + FIT_TOLERANCE {
        return Ok(vec![TextBoxLine {
            text: visible.to_owned(),
            width: paragraph_width,
            justify: false,
        }]);
    }

    let mut lines = Vec::new();
    let mut start = 0;
    while start < paragraph.len() {
        let tail = &paragraph[start..];
        let mut best: Option<(usize, usize, f64)> = None;
        for (end, _) in linebreaks(tail) {
            let visible = tail[..end].trim_end_matches(char::is_whitespace);
            // Do not turn leading whitespace into an artificial blank line.
            if visible.is_empty() && end < tail.len() {
                continue;
            }
            let line_width = measure(visible)?;
            if line_width <= max_width + FIT_TOLERANCE {
                best = Some((end, visible.len(), line_width));
            } else {
                break;
            }
        }

        let (consumed, visible_end, line_width) = match best {
            Some(value) => value,
            None => emergency_break(tail, max_width, measure)?,
        };
        let soft_break = consumed < tail.len();
        lines.push(TextBoxLine {
            text: tail[..visible_end].to_owned(),
            width: line_width,
            justify: justify && soft_break,
        });
        start += consumed;
    }
    Ok(lines)
}

fn emergency_break<F>(
    text: &str,
    max_width: f64,
    measure: &mut F,
) -> Result<(usize, usize, f64), String>
where
    F: FnMut(&str) -> Result<f64, String>,
{
    let mut best = None;
    for (start, grapheme) in text.grapheme_indices(true) {
        let end = start + grapheme.len();
        let line_width = measure(&text[..end])?;
        if line_width <= max_width + FIT_TOLERANCE {
            best = Some((end, end, line_width));
        } else {
            break;
        }
    }
    best.ok_or_else(|| "textbox is too narrow to fit one text grapheme".to_owned())
}
