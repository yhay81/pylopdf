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
use read_fonts::TableProvider;

use crate::layout::layout_textbox;

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

/// Lay out and generate subset-embedded OpenType text inside a display-space
/// rectangle. An overflow returns its negative spare height without PDF bytes.
#[allow(clippy::too_many_arguments)]
pub fn embedded_textbox_page(
    page_size: (f64, f64),
    rect: [f64; 4],
    text: &str,
    font_data: Vec<u8>,
    font_index: u32,
    font_size: f64,
    line_height: f64,
    align: u8,
    color: (f64, f64, f64),
) -> Result<(Option<Vec<u8>>, f64), String> {
    let width = finite_f32(page_size.0, "page width")?;
    let height = finite_f32(page_size.1, "page height")?;
    let size = finite_f32(font_size, "font size")?;
    let page_settings = PageSettings::from_wh(width, height)
        .ok_or_else(|| "page dimensions are outside krilla's supported range".to_owned())?;
    let shaping_font = FontRef::from_index(&font_data, font_index)
        .map_err(|_| format!("font data or collection index {font_index} is invalid"))?;
    let (ascent, descent) = font_vertical_metrics(&shaping_font)?;
    let shaper_data = ShaperData::new(&shaping_font);
    let shaper = shaper_data.shaper(&shaping_font).build();
    let box_size = (rect[2] - rect[0], rect[3] - rect[1]);
    let layout = layout_textbox(
        text,
        box_size,
        font_size,
        line_height,
        ascent,
        descent,
        align == 3,
        |line| {
            let glyphs = shape_line(line, &shaper)?;
            Ok(glyphs
                .iter()
                .map(|glyph| f64::from(glyph.x_advance) * font_size)
                .sum::<f64>()
                .abs())
        },
    )?;
    if !layout.fits() {
        return Ok((None, layout.spare_height));
    }

    let shaped_lines = layout
        .lines
        .iter()
        .map(|line| shape_line(&line.text, &shaper))
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
    let box_width = box_size.0;
    for (line_number, (line, mut glyphs)) in layout.lines.iter().zip(shaped_lines).enumerate() {
        let x_offset = match align {
            1 => (box_width - line.width) / 2.0,
            2 => box_width - line.width,
            _ => 0.0,
        };
        if line.justify {
            justify_glyphs(
                &mut glyphs,
                &line.text,
                (box_width - line.width).max(0.0) / font_size,
            );
        }
        let baseline_x = finite_f32(rect[0] + x_offset.max(0.0), "text x")?;
        let baseline_y = finite_f32(
            rect[1] + layout.ascent * font_size + line_number as f64 * layout.leading,
            "text y",
        )?;
        surface.draw_glyphs(
            Point::from_xy(baseline_x, baseline_y),
            &glyphs,
            font.clone(),
            &line.text,
            size,
            false,
        );
    }
    surface.finish();
    page.finish();
    let bytes = document
        .finish()
        .map_err(|error| format!("krilla text generation failed: {error}"))?;
    Ok((Some(bytes), layout.spare_height))
}

fn font_vertical_metrics(font: &FontRef<'_>) -> Result<(f64, f64), String> {
    let units_per_em = f64::from(
        font.head()
            .map_err(|_| "font does not contain a valid head table".to_owned())?
            .units_per_em(),
    );
    if units_per_em <= 0.0 {
        return Err("font has an invalid units-per-em value".to_owned());
    }
    let typo_metrics = font
        .os2()
        .map(|os2| (os2.s_typo_ascender(), os2.s_typo_descender()))
        .ok();
    let hhea_metrics = || {
        font.hhea()
            .map(|hhea| (hhea.ascender().to_i16(), hhea.descender().to_i16()))
            .ok()
    };
    typo_metrics
        .and_then(|metrics| normalize_vertical_metrics(metrics, units_per_em))
        .or_else(|| {
            hhea_metrics().and_then(|metrics| normalize_vertical_metrics(metrics, units_per_em))
        })
        .ok_or_else(|| "font does not contain usable vertical metrics".to_owned())
}

fn normalize_vertical_metrics(metrics: (i16, i16), units_per_em: f64) -> Option<(f64, f64)> {
    let ascent = f64::from(i32::from(metrics.0).max(0)) / units_per_em;
    let descent = f64::from((-i32::from(metrics.1)).max(0)) / units_per_em;
    (ascent + descent > 0.0).then_some((ascent, descent))
}

fn justify_glyphs(glyphs: &mut [KrillaGlyph], text: &str, extra_em: f64) {
    let whitespace_count = text.chars().filter(|character| *character == ' ').count();
    if whitespace_count == 0 {
        return;
    }
    let per_space = (extra_em / whitespace_count as f64) as f32;
    for glyph in glyphs {
        let count = text[glyph.text_range.clone()]
            .chars()
            .filter(|character| *character == ' ')
            .count();
        glyph.x_advance += per_space * count as f32;
    }
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
