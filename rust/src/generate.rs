//! Small PDF-generation primitives used by editing operations.
//!
//! krilla owns generation and font subsetting. The resulting bytes are imported
//! into lopdf as a Form XObject, preserving the edit/render engine boundary.

use harfrust::{Direction, FontRef, ShapeOptions, Shaper, ShaperData, UnicodeBuffer};
use krilla::Document;
use krilla::color::rgb;
use krilla::geom::Point;
use krilla::num::NormalizedF32;
use krilla::page::PageSettings;
use krilla::paint::Fill;
use krilla::text::{Font, GlyphId, KrillaGlyph};

/// Generate one transparent page containing subset-embedded OpenType text.
///
/// Coordinates use krilla's top-left-origin page surface. `point` is the first
/// line's baseline-left point; subsequent lines use 1.2× font-size leading.
pub fn embedded_text_page(
    page_size: (f64, f64),
    point: (f64, f64),
    lines: &[String],
    font_data: Vec<u8>,
    font_index: u32,
    font_size: f64,
    color: (f64, f64, f64),
) -> Result<Vec<u8>, String> {
    let width = finite_f32(page_size.0, "page width")?;
    let height = finite_f32(page_size.1, "page height")?;
    let x = finite_f32(point.0, "text x")?;
    let y = finite_f32(point.1, "text y")?;
    let size = finite_f32(font_size, "font size")?;
    let page_settings = PageSettings::from_wh(width, height)
        .ok_or_else(|| "page dimensions are outside krilla's supported range".to_owned())?;
    let shaping_font = FontRef::from_index(&font_data, font_index)
        .map_err(|_| format!("font data or collection index {font_index} is invalid"))?;
    let shaper_data = ShaperData::new(&shaping_font);
    let shaper = shaper_data.shaper(&shaping_font).build();
    let shaped_lines = lines
        .iter()
        .map(|line| shape_line(line, &shaper))
        .collect::<Result<Vec<_>, _>>()?;
    let font = Font::new(font_data.into(), font_index)
        .ok_or_else(|| format!("font data or collection index {font_index} is invalid"))?;

    let mut document = Document::new();
    let mut page = document.start_page_with(page_settings);
    let mut surface = page.surface();
    surface.set_fill(Some(Fill {
        paint: rgb::Color::new(
            channel_to_u8(color.0),
            channel_to_u8(color.1),
            channel_to_u8(color.2),
        )
        .into(),
        opacity: NormalizedF32::ONE,
        rule: Default::default(),
    }));
    for (line_number, (line, glyphs)) in lines.iter().zip(shaped_lines).enumerate() {
        let baseline_y = y + line_number as f32 * size * 1.2;
        surface.draw_glyphs(
            Point::from_xy(x, baseline_y),
            &glyphs,
            font.clone(),
            line,
            size,
            false,
        );
    }
    surface.finish();
    page.finish();
    document
        .finish()
        .map_err(|error| format!("krilla text generation failed: {error}"))
}

fn shape_line(text: &str, shaper: &Shaper<'_>) -> Result<Vec<KrillaGlyph>, String> {
    if text.is_empty() {
        return Ok(Vec::new());
    }

    let mut buffer = UnicodeBuffer::new();
    buffer.push_str(text);
    buffer.guess_segment_properties();
    let direction = buffer.direction();
    let output = shaper.shape(buffer, ShapeOptions::new());
    let positions = output.glyph_positions();
    let infos = output.glyph_infos();
    let units_per_em = shaper.units_per_em() as f32;
    let mut glyphs = Vec::with_capacity(output.len());

    for (index, (info, position)) in infos.iter().zip(positions).enumerate() {
        if info.glyph_id == 0 {
            return Err("font does not contain all glyphs needed for text".to_owned());
        }

        let start = info.cluster as usize;
        let end_index = if matches!(direction, Direction::LeftToRight | Direction::TopToBottom) {
            let mut next = index.checked_add(1);
            while let Some(next_index) = next {
                if infos
                    .get(next_index)
                    .is_some_and(|next_info| next_info.cluster == info.cluster)
                {
                    next = next_index.checked_add(1);
                } else {
                    break;
                }
            }
            next
        } else {
            let mut previous = index.checked_sub(1);
            while let Some(previous_index) = previous {
                if infos
                    .get(previous_index)
                    .is_some_and(|previous_info| previous_info.cluster == info.cluster)
                {
                    previous = previous_index.checked_sub(1);
                } else {
                    break;
                }
            }
            previous
        };
        let end = end_index
            .and_then(|other_index| infos.get(other_index))
            .map_or(text.len(), |other_info| other_info.cluster as usize);

        glyphs.push(KrillaGlyph::new(
            GlyphId::new(info.glyph_id),
            position.x_advance as f32 / units_per_em,
            position.x_offset as f32 / units_per_em,
            position.y_offset as f32 / units_per_em,
            position.y_advance as f32 / units_per_em,
            start..end,
            None,
        ));
    }

    Ok(glyphs)
}

fn finite_f32(value: f64, label: &str) -> Result<f32, String> {
    let narrowed = value as f32;
    if narrowed.is_finite() {
        Ok(narrowed)
    } else {
        Err(format!("{label} is outside krilla's finite f32 range"))
    }
}

fn channel_to_u8(value: f64) -> u8 {
    (value * 255.0).round() as u8
}
