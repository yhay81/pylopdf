//! Text editing: replacing text in the font that draws it.
//!
//! The content engine gives every glyph its operation and code, and the
//! glyph's font gives its text as extraction reads it (`/ToUnicode`, a
//! predefined Unicode CMap, or a simple font's encoding), so editing needs no
//! interpretation by hayro. An occurrence is a run of glyphs, in
//! drawing order and in one stream, whose texts spell the search text. Its
//! first glyph takes the replacement's codes in the same font, and the other
//! glyphs go; one `TJ` adjustment makes up the difference between the old
//! and the new advances, so the text after it stays in place. The edit goes
//! through the redaction engine's container writing, so a Form XObject
//! placement is copied before it changes and the `/ActualText` around the
//! old glyphs goes. Text that the font cannot draw is refused, or, with a
//! fallback, removed and returned for the caller to draw in that font.

use std::collections::{HashMap, HashSet};

use hayro::kurbo::Point;

use crate::content::encode::{TextDecoder, TextEncoder};
use crate::content::interpret::Glyph;

use super::redaction::{Selector, apply_pass};
use super::*;

/// UTF-8 bytes of search and replacement text that one call accepts.
pub(super) const MAX_EDIT_INPUT_BYTES: usize = 4096;

/// A replacement that the font could not draw: display-space origin, font
/// size in points, rotation in degrees clockwise, and the text.
pub(super) type FallbackText = ((f64, f64), f64, f64, String);

/// Implements [`_Document::replace_text_in_fonts`].
pub(super) fn replace_text(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    search: &str,
    replacement: &str,
    fallback: bool,
) -> PyResult<(usize, Vec<FallbackText>)> {
    if search.is_empty() {
        return Err(PyValueError::new_err("search must be at least 1 character"));
    }
    if search.len() + replacement.len() > MAX_EDIT_INPUT_BYTES {
        return Err(limit_err(
            "replacement_input_size",
            format!(
                "search and replacement text total more than {MAX_EDIT_INPUT_BYTES} UTF-8 bytes"
            ),
        ));
    }
    let page_id = this.page_id(page_number)?;
    let display = replacement::page_display(this, page_id)?;
    let resources = match this.resolve_page_attr(page_id, b"Resources")? {
        Some(Object::Dictionary(dict)) => Some(dict),
        _ => None,
    };
    let doc: &Document = &this.doc;
    let interpretation = py.detach(|| replacement::interpret(doc, page_id, resources.as_ref()))?;
    // Each glyph's text as extraction reads it, from its font.
    let decode = replacement::interpreted_stream;
    let decoders: Vec<TextDecoder> = interpretation
        .fonts
        .iter()
        .map(|(_, id)| {
            let dict = id.and_then(|id| doc.get_dictionary(id).ok());
            TextDecoder::new(doc, dict, &decode)
        })
        .collect();
    let glyph_text: Vec<Option<String>> = interpretation
        .glyphs
        .iter()
        .map(|glyph| match glyph.font {
            Some(font) if !glyph.fallback => decoders[font].text(glyph.code, glyph.source.len),
            // hayro's Helvetica fallback shows ASCII bytes as they are.
            _ => u8::try_from(glyph.code)
                .ok()
                .filter(|code| (0x20..0x7f).contains(code))
                .map(|code| char::from(code).to_string()),
        })
        .collect();

    // Find the occurrences in drawing order, within one container.
    let glyphs = &interpretation.glyphs;
    let mut occurrences: Vec<Vec<usize>> = Vec::new();
    let mut start = 0;
    while start < glyphs.len() {
        if let Some(found) = match_at(glyphs, &glyph_text, start, search) {
            start = *found.last().unwrap_or(&start) + 1;
            if !crate::redact::in_annotation(&interpretation, glyphs[found[0]].source.container) {
                occurrences.push(found);
            }
        } else {
            start += 1;
        }
    }
    if occurrences.is_empty() {
        return Ok((0, Vec::new()));
    }

    // Encode the replacement in each occurrence's font.
    let observed = observed_codes(&interpretation, &glyph_text, &[]);
    let mut encoders: HashMap<usize, TextEncoder> = HashMap::new();
    let mut removed: HashSet<usize> = HashSet::new();
    let mut insertions: HashMap<usize, (Vec<u8>, f32)> = HashMap::new();
    let mut fallbacks: Vec<FallbackText> = Vec::new();
    for occurrence in &occurrences {
        let first = &glyphs[occurrence[0]];
        removed.extend(occurrence.iter().copied());
        if replacement.is_empty() {
            continue;
        }
        let encoded = match first.font {
            Some(font) => {
                let encoder = encoders.entry(font).or_insert_with(|| {
                    let (metrics, id) = &interpretation.fonts[font];
                    let dict = id.and_then(|id| doc.get_dictionary(id).ok());
                    TextEncoder::new(
                        doc,
                        dict,
                        metrics.as_ref().expect("a glyph's font loaded"),
                        observed.get(&font).map_or(&[][..], Vec::as_slice),
                        &decode,
                    )
                });
                encoder.encode(replacement)
            }
            None => Err(replacement.chars().next().unwrap_or(' ')),
        };
        match encoded {
            Ok(codes) => {
                let metrics = first
                    .font
                    .and_then(|font| interpretation.fonts[font].0.as_ref())
                    .expect("an encoder has a font");
                let inserted = advance_adjustment(metrics, first, &codes);
                insertions.insert(occurrence[0], (codes, inserted));
            }
            Err(missing) if !fallback => {
                return Err(PdfError::new_err(format!(
                    "the font that draws {search:?} cannot draw {missing:?}; pass a fallback_font"
                )));
            }
            Err(_) => fallbacks.push(fallback_text(first, display, replacement)),
        }
    }
    let count = occurrences.len();
    // The pass prepares every object before it changes the document.
    apply_pass(
        this,
        py,
        page_id,
        &Selector::Replace {
            glyphs: &removed,
            insertions: &insertions,
        },
        &[],
    )?;
    Ok((count, fallbacks))
}

/// The glyphs from `start` whose texts spell `search`, if they do.
fn match_at(
    glyphs: &[Glyph],
    texts: &[Option<String>],
    start: usize,
    search: &str,
) -> Option<Vec<usize>> {
    let container = glyphs[start].source.container;
    let mut rest = search;
    let mut found = Vec::new();
    let mut index = start;
    while !rest.is_empty() {
        let glyph = glyphs.get(index)?;
        if glyph.source.container != container {
            return None;
        }
        let text = texts[index].as_deref()?;
        rest = rest.strip_prefix(text)?;
        found.push(index);
        index += 1;
    }
    Some(found)
}

/// The codes and texts that each font already draws on the page.
fn observed_codes(
    interpretation: &crate::content::Interpretation,
    texts: &[Option<String>],
    _skip: &[usize],
) -> HashMap<usize, Vec<(String, Vec<u8>)>> {
    let mut observed: HashMap<usize, Vec<(String, Vec<u8>)>> = HashMap::new();
    for (glyph, text) in interpretation.glyphs.iter().zip(texts) {
        let (Some(font), Some(text)) = (glyph.font, text) else {
            continue;
        };
        let bytes = glyph.code_bytes();
        observed
            .entry(font)
            .or_default()
            .push((text.clone(), bytes));
    }
    observed
}

/// The `TJ` adjustment that undoes the advance of `codes` drawn with the
/// text state of `glyph`.
fn advance_adjustment(
    metrics: &crate::content::fonts::FontMetrics,
    glyph: &Glyph,
    codes: &[u8],
) -> f32 {
    let [size, char_space, word_space, scaling] = glyph.text_state;
    let horizontal = metrics.is_horizontal();
    let mut total = 0.0_f32;
    let mut offset = 0;
    while offset < codes.len() {
        let (code, len) = metrics.read_code(codes, offset);
        let advance = metrics.advance(code);
        let word = if code == 32 && len == 1 {
            word_space
        } else {
            0.0
        };
        let base = |value: f64| value as f32 / 1000.0 * size + char_space + word;
        total += if horizontal {
            base(advance.0) * (scaling / 100.0)
        } else {
            base(advance.1)
        };
        offset += len;
    }
    let scale = if horizontal {
        size * scaling / 100.0
    } else {
        size
    };
    if scale == 0.0 {
        0.0
    } else {
        -total * 1000.0 / scale
    }
}

/// Where a fallback font draws the replacement of one occurrence.
fn fallback_text(glyph: &Glyph, display: hayro::kurbo::Affine, text: &str) -> FallbackText {
    let transform = display * glyph.transform;
    let origin = transform * Point::ORIGIN;
    let [a, b, ..] = transform.as_coeffs();
    let size = a.hypot(b) * 1000.0;
    let angle = b.atan2(a).to_degrees();
    ((origin.x, origin.y), size, angle, text.to_owned())
}
