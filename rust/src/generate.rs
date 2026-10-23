//! Small PDF-generation primitives used by editing operations.
//!
//! krilla owns generation and font subsetting. The resulting bytes are imported
//! into lopdf as a Form XObject, preserving the edit/render engine boundary.

use std::sync::Arc;

pub(crate) use harfrust::Font as ShapingFont;
use harfrust::{Buffer, Direction, ShapeOptions, ShaperFont};
use krilla::Document;
use krilla::color::{luma, rgb};
use krilla::geom::Point;
use krilla::num::NormalizedF32;
use krilla::page::PageSettings;
use krilla::paint::{Fill, Paint};
use krilla::surface::Surface;
use krilla::text::{Font, GlyphId, KrillaGlyph};
use lopdf::Object;
use read_fonts::TableProvider;
use read_fonts::tables::fvar::InstanceRecord;
use read_fonts::tables::os2::SelectionFlags;
use read_fonts::types::{Fixed, NameId, Tag};

use crate::draw::DeviceColor;
use crate::form;
use crate::layout::{
    LineMetrics, LinePlacement, TextBoxLayout, collect_graphemes, copy_text, layout_textbox,
    normalized_single_line,
};

/// One shaped line placed at its baseline-left point on a krilla page.
pub(crate) struct GlyphRun {
    pub(crate) origin: Point,
    pub(crate) glyphs: Vec<KrillaGlyph>,
    pub(crate) text: String,
}

/// Shape `lines` where canvas text places them: the first baseline-left
/// point at `point` in top-left-origin page coordinates, and each later
/// baseline `leading` lower.
pub(crate) fn text_runs(
    font: &ShapingFont,
    point: (f64, f64),
    lines: &[String],
    leading: f64,
) -> Result<Vec<GlyphRun>, String> {
    let x = finite_f32(point.0, "text x")?;
    let shaper = LineShaper::new(font);
    let mut runs = Vec::new();
    runs.try_reserve_exact(lines.len())
        .map_err(|error| format!("failed to allocate shaped text line collection: {error}"))?;
    for (line_number, line) in lines.iter().enumerate() {
        let glyphs = shape_line(line, &shaper)?;
        let baseline_y = finite_f32(point.1 + line_number as f64 * leading, "text y")?;
        runs.push(GlyphRun {
            origin: Point::from_xy(x, baseline_y),
            glyphs,
            text: copy_text(line)?,
        });
    }
    Ok(runs)
}

/// Wrap text in a box of `box_size` and place its lines by `placement`,
/// measuring each line with the shaped advances that drawing places.
pub(crate) fn textbox_layout(
    font: &ShapingFont,
    text: &str,
    box_size: (f64, f64),
    font_size: f64,
    placement: LinePlacement,
    justify: bool,
    max_lines: Option<usize>,
) -> Result<TextBoxLayout, String> {
    let shaper = LineShaper::new(font);
    layout_textbox(
        text,
        box_size,
        font_size,
        placement,
        justify,
        max_lines,
        |line| Ok(line_width(&shape_line(line, &shaper)?, font_size)),
    )
}

/// Lay out and shape text inside a display rectangle, with lines placed by
/// `placement`. Return the placed lines, or `None` when they do not fit,
/// together with the spare height, which is negative on overflow.
pub(crate) fn textbox_runs(
    font: &ShapingFont,
    rect: [f64; 4],
    text: &str,
    font_size: f64,
    placement: LinePlacement,
    align: u8,
    max_lines: Option<usize>,
) -> Result<(Option<Vec<GlyphRun>>, f64), String> {
    let shaper = LineShaper::new(font);
    let box_size = (rect[2] - rect[0], rect[3] - rect[1]);
    let layout = textbox_layout(
        font,
        text,
        box_size,
        font_size,
        placement,
        align == 3,
        max_lines,
    )?;
    if !layout.fits() {
        return Ok((None, layout.spare_height));
    }

    let (layout_ascent, leading, spare_height) =
        (layout.ascent, layout.leading, layout.spare_height);
    let mut runs = Vec::new();
    runs.try_reserve_exact(layout.lines.len())
        .map_err(|error| format!("failed to allocate shaped text line collection: {error}"))?;
    let box_width = box_size.0;
    for (line_number, line) in layout.lines.into_iter().enumerate() {
        let mut glyphs = shape_line(&line.text, &shaper)?;
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
            rect[1] + layout_ascent * font_size + line_number as f64 * leading,
            "text y",
        )?;
        runs.push(GlyphRun {
            origin: Point::from_xy(baseline_x, baseline_y),
            glyphs,
            text: line.text,
        });
    }
    Ok((Some(runs), spare_height))
}

/// Draw placed lines in one font and size with the current fill.
pub(crate) fn draw_runs(surface: &mut Surface<'_>, runs: &[GlyphRun], font: &Font, size: f32) {
    for run in runs {
        surface.draw_glyphs(
            run.origin,
            &run.glyphs,
            font.clone(),
            &run.text,
            size,
            false,
        );
    }
}

/// Convert a device colour with 0–1 channels to krilla's 8-bit paint.
pub(crate) fn device_paint(color: DeviceColor) -> Paint {
    match color {
        DeviceColor::Gray(gray) => luma::Color::new(channel_to_u8(gray)).into(),
        DeviceColor::Rgb(red, green, blue) => rgb::Color::new(
            channel_to_u8(red),
            channel_to_u8(green),
            channel_to_u8(blue),
        )
        .into(),
    }
}

/// The tag of the OpenType weight axis.
const WEIGHT_AXIS: Tag = Tag::new(b"wght");

/// The longest PostScript name that krilla keeps beside its six-letter subset
/// tag, the `+`, and a `-Identity-H` suffix within PDF's 127-byte name limit.
const MAX_POSTSCRIPT_NAME_LEN: usize = 127 - (6 + 1 + 1 + "Identity-H".len());

/// The user-space range of a variable face's weight axis.
#[derive(Clone, Copy)]
pub(crate) struct WeightAxis {
    pub(crate) min: f32,
    pub(crate) default: f32,
    pub(crate) max: f32,
}

/// One face and the weight that generation draws it at.
///
/// A variable face is drawn at one instance. krilla's subsetter instantiates
/// the outlines and advances at the variation coordinates that krilla is
/// given, and HarfRust must shape at the same coordinates so that every
/// advance it returns is one that krilla draws. A static face, a face drawn
/// as given, and a variable face at its default location carry no
/// coordinates, so krilla, HarfRust, and the subsetter read the font exactly
/// as they do without this type, and the output does not change.
#[derive(Clone)]
pub(crate) struct GenerationFace {
    data: Arc<Vec<u8>>,
    index: u32,
    /// The font that HarfRust shapes and that vertical metrics come from, at
    /// the selected instance. It shares `data` with krilla instead of copying it.
    shaping: ShapingFont,
    /// The value of the weight axis that a variable face was selected at.
    weight: Option<u16>,
    /// What a selected weight away from the default location adds.
    instance: Option<WeightInstance>,
}

/// The coordinate and names of an instance away from the default location.
#[derive(Clone)]
struct WeightInstance {
    weight: f32,
    /// `None` when the font has no name to derive one from.
    postscript_name: Option<String>,
    /// The subfamily name of the named instance at this location, else the
    /// axis value, as in `650wght`.
    style_name: String,
}

impl GenerationFace {
    /// Select a face, at `weight` on its weight axis when it has one.
    ///
    /// A face without a weight axis, or one opened without a weight, is drawn
    /// as given. A weight outside the axis fails, because krilla and HarfRust
    /// would silently clamp it to the nearest end and draw another weight.
    /// The font program and face index, shared rather than copied.
    pub(crate) fn program(&self) -> (Arc<Vec<u8>>, u32) {
        (Arc::clone(&self.data), self.index)
    }

    pub(crate) fn new(data: Arc<Vec<u8>>, index: u32, weight: Option<u16>) -> Result<Self, String> {
        let source: Arc<dyn AsRef<[u8]> + Send + Sync> = data.clone();
        let font = ShapingFont::new(source, index)
            .ok_or_else(|| format!("font data or collection index {index} is invalid"))?;
        let Some((weight, axis)) = weight.zip(weight_axis(&font)) else {
            return Ok(Self {
                data,
                index,
                shaping: font,
                weight: None,
                instance: None,
            });
        };
        let value = f32::from(weight);
        if !(axis.min..=axis.max).contains(&value) {
            return Err(format!(
                "weight {weight} is outside the font's weight axis, which runs from {} through {}",
                axis.min, axis.max
            ));
        }
        let shaping = font
            .instance_builder()
            .variations([(WEIGHT_AXIS, value)])
            .build();
        // read-fonts normalizes the value through `avar` exactly as krilla's
        // skrifa does, so both agree on whether it is the default location.
        let instance = (!shaping.normalized_coords().is_empty()).then(|| {
            let named = named_instance(&font, weight);
            WeightInstance {
                weight: value,
                postscript_name: instance_postscript_name(&font, named.as_ref(), weight),
                style_name: named
                    .and_then(|instance| name_text(&font, instance.subfamily_name_id))
                    .filter(|style| !style.is_empty())
                    .unwrap_or_else(|| format!("{weight}wght")),
            }
        });
        Ok(Self {
            data,
            index,
            shaping,
            weight: Some(weight),
            instance,
        })
    }

    /// The shared font bytes and collection index.
    pub(crate) fn source(&self) -> (Arc<Vec<u8>>, u32) {
        (Arc::clone(&self.data), self.index)
    }

    /// The weight-axis value a variable face was selected at.
    pub(crate) fn weight(&self) -> Option<u16> {
        self.weight
    }

    /// The PostScript name of an instance away from the default location.
    pub(crate) fn instance_postscript_name(&self) -> Option<&str> {
        self.instance
            .as_ref()
            .and_then(|instance| instance.postscript_name.as_deref())
    }

    /// The style name of an instance away from the default location.
    pub(crate) fn instance_style_name(&self) -> Option<&str> {
        self.instance
            .as_ref()
            .map(|instance| instance.style_name.as_str())
    }

    /// The font that HarfRust shapes, at this face's instance.
    ///
    /// Vertical metrics come from the same font's unvaried tables.
    pub(crate) fn shaping_font(&self) -> &ShapingFont {
        &self.shaping
    }

    /// Open the font that krilla embeds, at this face's instance.
    pub(crate) fn embedding_font(&self) -> Result<Font, String> {
        let data = Arc::clone(&self.data).into();
        match &self.instance {
            Some(instance) => Font::new_variable(
                data,
                self.index,
                &[(krilla::text::Tag::new(b"wght"), instance.weight)],
            ),
            None => Font::new(data, self.index),
        }
        .ok_or_else(|| format!("font data or collection index {} is invalid", self.index))
    }

    /// Name the subset in a generated document after the instance it embeds.
    ///
    /// krilla names a subset after name ID 6, the PostScript name of the
    /// default instance, whatever location it draws: a regular instance of
    /// Noto Sans Hebrew would be named `NotoSansHebrew-Thin`. Renaming the
    /// Type0 font, its CID font, and their descriptor keeps the subset tag and
    /// lets extraction and viewers report the instance drawn, including the
    /// bold flag that extraction infers from the name. A face at its default
    /// location keeps krilla's name.
    pub(crate) fn name_subset(&self, document: &mut lopdf::Document) {
        let Some(name) = self.instance_postscript_name() else {
            return;
        };
        for object in document.objects.values_mut() {
            let Ok(dictionary) = object.as_dict_mut() else {
                continue;
            };
            let key: &[u8] = match dictionary.get(b"Type").and_then(Object::as_name) {
                Ok(b"Font") => b"BaseFont",
                Ok(b"FontDescriptor") => b"FontName",
                _ => continue,
            };
            if let Ok(Object::Name(current)) = dictionary.get_mut(key) {
                let tag_end = current
                    .iter()
                    .position(|&byte| byte == b'+')
                    .map_or(0, |plus| plus + 1);
                current.truncate(tag_end);
                current.extend_from_slice(name.as_bytes());
            }
        }
    }
}

/// Return the range of a face's weight axis, or `None` for a static face.
pub(crate) fn weight_axis(font: &ShapingFont) -> Option<WeightAxis> {
    let fvar = font.tables().fvar().ok()?;
    let axis = fvar
        .axes()
        .ok()?
        .iter()
        .find(|axis| axis.axis_tag() == WEIGHT_AXIS)?;
    let (min, default, max) = (
        axis.min_value().to_f32(),
        axis.default_value().to_f32(),
        axis.max_value().to_f32(),
    );
    // fvar requires min <= default <= max; an axis that breaks it is unusable.
    (min <= default && default <= max).then_some(WeightAxis { min, default, max })
}

/// Read a name record, preferring English.
fn name_text(font: &ShapingFont, id: NameId) -> Option<String> {
    font.names()
        .get(id)
        .and_then(|name| name.english_or_first())
        .map(|localized| localized.name().to_string())
}

/// Return the named instance at `weight` on the weight axis with every other
/// axis at its default value.
fn named_instance(font: &ShapingFont, weight: u16) -> Option<InstanceRecord<'_>> {
    let fvar = font.tables().fvar().ok()?;
    let axes = fvar.axes().ok()?;
    let location = Fixed::from_f64(f64::from(weight));
    fvar.instances()
        .ok()?
        .iter()
        .filter_map(Result::ok)
        .find(|instance| {
            instance.coordinates.len() == axes.len()
                && axes.iter().zip(instance.coordinates).all(|(axis, value)| {
                    value.get()
                        == if axis.axis_tag() == WEIGHT_AXIS {
                            location
                        } else {
                            axis.default_value()
                        }
                })
        })
}

/// Derive the PostScript name of a face's instance at `weight`.
///
/// This follows Adobe Technical Note #5902. A named instance at that location
/// uses its own PostScript name, or joins the variations PostScript name
/// prefix (name ID 25, else the family name) and its subfamily name with a
/// hyphen, as in `NotoSansHebrew-Bold`. Any other location appends the axis
/// value, as in `NotoSansHebrew_650wght`.
fn instance_postscript_name(
    font: &ShapingFont,
    named: Option<&InstanceRecord<'_>>,
    weight: u16,
) -> Option<String> {
    let text = |id: NameId| name_text(font, id);
    if let Some(name) = named
        .and_then(|instance| instance.post_script_name_id)
        .and_then(text)
        .map(|name| postscript_characters(&name, |_| true))
        .filter(|name| !name.is_empty())
    {
        return Some(truncate_postscript_name(name));
    }
    let prefix = text(NameId::VARIATIONS_POSTSCRIPT_NAME_PREFIX)
        .map(|prefix| postscript_characters(&prefix, |_| true))
        .filter(|prefix| !prefix.is_empty())
        .or_else(|| {
            text(NameId::TYPOGRAPHIC_FAMILY_NAME)
                .or_else(|| text(NameId::FAMILY_NAME))
                .map(|family| postscript_characters(&family, |byte| byte.is_ascii_alphanumeric()))
                .filter(|family| !family.is_empty())
        })?;
    let subfamily = named
        .and_then(|instance| text(instance.subfamily_name_id))
        .map(|subfamily| postscript_characters(&subfamily, |byte| byte.is_ascii_alphanumeric()))
        .filter(|subfamily| !subfamily.is_empty());
    Some(truncate_postscript_name(match subfamily {
        Some(subfamily) => format!("{prefix}-{subfamily}"),
        None => format!("{prefix}_{weight}wght"),
    }))
}

/// Keep the characters of `text` that a PostScript name may contain and that
/// `keep` accepts: printable ASCII other than `[](){}<>/%`.
fn postscript_characters(text: &str, keep: impl Fn(u8) -> bool) -> String {
    text.bytes()
        .filter(|&byte| {
            (b'!'..=b'~').contains(&byte) && !b"[](){}<>/%".contains(&byte) && keep(byte)
        })
        .map(char::from)
        .collect()
}

fn truncate_postscript_name(mut name: String) -> String {
    // The name is ASCII, so any byte length is a character boundary.
    name.truncate(MAX_POSTSCRIPT_NAME_LEN);
    name
}

/// Auto-fit and generate text for an AcroForm widget appearance.
///
/// Single-line fields collapse explicit line breaks and shrink until they fit
/// on one line. Multiline fields use the shared Unicode line-breaking layout.
#[allow(clippy::too_many_arguments)]
pub fn embedded_widget_text_page(
    page_size: (f64, f64),
    rect: [f64; 4],
    text: &str,
    face: &GenerationFace,
    multiline: bool,
    align: u8,
    color: (f64, f64, f64),
) -> Result<Vec<u8>, String> {
    let width = finite_f32(page_size.0, "page width")?;
    let height = finite_f32(page_size.1, "page height")?;
    let page_settings = PageSettings::from_wh(width, height)
        .ok_or_else(|| "page dimensions are outside krilla's supported range".to_owned())?;
    let shaping_font = face.shaping_font();
    let (ascent, descent) = font_vertical_metrics(shaping_font)?;
    let shaper = LineShaper::new(shaping_font);
    let normalized = if multiline {
        copy_text(text)?
    } else {
        normalized_single_line(text)?
    };
    let box_size = (rect[2] - rect[0], rect[3] - rect[1]);
    if box_size.0 <= 0.0 || box_size.1 <= 0.0 {
        return Err("widget has no usable text area".to_owned());
    }

    let mut font_size = (box_size.1 / (ascent + descent).max(0.01)).min(12.0);
    let layout = loop {
        let candidate = layout_textbox(
            &normalized,
            box_size,
            font_size,
            LinePlacement {
                ascent,
                descent,
                line_height: 1.2,
            },
            false,
            Some(crate::layout::MAX_GENERATED_TEXT_LINES),
            |line| Ok(line_width(&shape_line(line, &shaper)?, font_size)),
        )?;
        if candidate.fits() && (multiline || candidate.lines.len() <= 1) {
            break candidate;
        }
        font_size *= 0.85;
        if font_size < 0.01 {
            return Err("form field text cannot fit inside the widget".to_owned());
        }
    };

    let shaped_lines = shape_lines(layout.lines.iter().map(|line| line.text.as_str()), &shaper)?;
    let font = face.embedding_font()?;
    let size = finite_f32(font_size, "font size")?;

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
    for (line_number, (line, glyphs)) in layout.lines.iter().zip(shaped_lines).enumerate() {
        let x_offset = match align {
            1 => (box_size.0 - line.width) / 2.0,
            2 => box_size.0 - line.width,
            _ => 0.0,
        };
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
    document
        .finish()
        .map_err(|error| format!("krilla form appearance generation failed: {error}"))
}

/// Auto-fit and center shaped Unicode characters in an AcroForm comb field.
#[allow(clippy::too_many_arguments)]
pub fn embedded_widget_comb_text_page(
    page_size: (f64, f64),
    rect: [f64; 4],
    text: &str,
    max_len: usize,
    align: u8,
    face: &GenerationFace,
    color: (f64, f64, f64),
) -> Result<Vec<u8>, String> {
    form::validate_comb_text(text, max_len)?;
    let width = finite_f32(page_size.0, "page width")?;
    let height = finite_f32(page_size.1, "page height")?;
    let page_settings = PageSettings::from_wh(width, height)
        .ok_or_else(|| "page dimensions are outside krilla's supported range".to_owned())?;
    let shaping_font = face.shaping_font();
    let (ascent, descent) = font_vertical_metrics(shaping_font)?;
    let shaper = LineShaper::new(shaping_font);
    let normalized = normalized_single_line(text)?;
    let graphemes = collect_graphemes(&normalized)?;
    let box_size = (rect[2] - rect[0], rect[3] - rect[1]);
    if box_size.0 <= 0.0 || box_size.1 <= 0.0 {
        return Err("widget has no usable text area".to_owned());
    }
    let cell_width = box_size.0 / max_len as f64;

    let mut font_size = (box_size.1 / (ascent + descent).max(0.01)).min(12.0);
    let shaped = loop {
        let mut candidates = Vec::new();
        candidates
            .try_reserve_exact(graphemes.len())
            .map_err(|error| format!("failed to allocate shaped comb field collection: {error}"))?;
        for grapheme in &graphemes {
            let glyphs = shape_line(grapheme, &shaper)?;
            let glyph_width = line_width(&glyphs, font_size);
            candidates.push((glyphs, glyph_width));
        }
        if candidates
            .iter()
            .all(|(_, glyph_width)| *glyph_width <= cell_width)
        {
            break candidates;
        }
        font_size *= 0.85;
        if font_size < 0.01 {
            return Err("comb field text cannot fit inside the widget".to_owned());
        }
    };

    let font = face.embedding_font()?;
    let size = finite_f32(font_size, "font size")?;
    let unused = max_len - graphemes.len();
    let start_slot = match align {
        1 => unused / 2,
        2 => unused,
        _ => 0,
    };

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
    for (index, (grapheme, (glyphs, glyph_width))) in graphemes.iter().zip(shaped).enumerate() {
        let baseline_x = finite_f32(
            rect[0]
                + (start_slot + index) as f64 * cell_width
                + (cell_width - glyph_width).max(0.0) / 2.0,
            "text x",
        )?;
        let baseline_y = finite_f32(rect[1] + ascent * font_size, "text y")?;
        surface.draw_glyphs(
            Point::from_xy(baseline_x, baseline_y),
            &glyphs,
            font.clone(),
            grapheme,
            size,
            false,
        );
    }
    surface.finish();
    page.finish();
    document
        .finish()
        .map_err(|error| format!("krilla form appearance generation failed: {error}"))
}

/// Return the width of one line of text as generation advances it.
///
/// krilla places each glyph at the sum of the shaped advances before it, and
/// textbox and form layout measure their lines with the same sum, so this is
/// the width that drawing the line covers.
pub(crate) fn shaped_line_width(
    font: &ShapingFont,
    text: &str,
    font_size: f64,
) -> Result<f64, String> {
    let shaper = LineShaper::new(font);
    Ok(line_width(&shape_line(text, &shaper)?, font_size))
}

/// Sum the shaped advances of one line in PDF units.
pub(crate) fn line_width(glyphs: &[KrillaGlyph], font_size: f64) -> f64 {
    glyphs
        .iter()
        .map(|glyph| f64::from(glyph.x_advance) * font_size)
        .sum::<f64>()
        .abs()
}

/// A font prepared for shaping, with the em size that scales its positions.
struct LineShaper<'a> {
    font: ShaperFont<'a, 'a>,
    units_per_em: f32,
}

impl<'a> LineShaper<'a> {
    fn new(font: &'a ShapingFont) -> Self {
        Self {
            font: ShaperFont::new(font),
            units_per_em: f32::from(font.units_per_em()),
        }
    }
}

/// Return the ascender and descender of a face, which canvas text places
/// lines with, as pymupdf's `Font` reports them.
///
/// MuPDF takes them from FreeType, which reads the OS/2 typographic metrics
/// when the face sets `USE_TYPO_METRICS` and the `hhea` metrics otherwise,
/// falling back to the typographic and then the Windows metrics when both
/// `hhea` values are zero. MuPDF then replaces an ascender that is not
/// positive or exceeds 8 em with 0.8, a descender beyond 3 em either way with
/// -0.2, and a positive descender with its negation. A variable face reports
/// the metrics of its default instance.
pub(crate) fn font_line_metrics(font: &ShapingFont) -> Result<LineMetrics, String> {
    /// The largest ascender and descender, in em, that MuPDF accepts.
    const MAX_TRUSTED_ASCENDER: i32 = 8;
    const MAX_TRUSTED_DESCENDER: i32 = 3;

    let tables = font.tables();
    let units_per_em = tables
        .head()
        .map_err(|_| "font does not contain a valid head table".to_owned())?
        .units_per_em();
    if units_per_em == 0 {
        return Err("font has an invalid units-per-em value".to_owned());
    }
    let os2 = tables.os2().ok();
    let use_typo_metrics = os2.as_ref().is_some_and(|os2| {
        os2.fs_selection()
            .contains(SelectionFlags::USE_TYPO_METRICS)
    });
    let hhea = tables.hhea().map_or((0, 0), |hhea| {
        (hhea.ascender().to_i16(), hhea.descender().to_i16())
    });
    let (ascender, descender) = match &os2 {
        Some(os2) if use_typo_metrics => (os2.s_typo_ascender(), os2.s_typo_descender()),
        Some(os2) if hhea == (0, 0) => match (os2.s_typo_ascender(), os2.s_typo_descender()) {
            // FreeType reads the unsigned Windows metrics as signed values.
            (0, 0) => (
                os2.us_win_ascent().cast_signed(),
                os2.us_win_descent().cast_signed().wrapping_neg(),
            ),
            typo => typo,
        },
        _ => hhea,
    };
    let (ascender, descender) = (i32::from(ascender), i32::from(descender));
    let units_per_em = i32::from(units_per_em);
    let ascender = if ascender <= 0 || ascender > MAX_TRUSTED_ASCENDER * units_per_em {
        0.8
    } else {
        f64::from(ascender) / f64::from(units_per_em)
    };
    let descender = if descender.abs() > MAX_TRUSTED_DESCENDER * units_per_em {
        -0.2
    } else {
        // A zero descender stays 0.0, as in MuPDF, rather than becoming -0.0.
        f64::from(-descender.abs()) / f64::from(units_per_em)
    };
    Ok(LineMetrics {
        ascender,
        descender,
    })
}

/// Return the ascent and descent, as positive fractions of the em, that form
/// appearances place lines with.
pub(crate) fn font_vertical_metrics(font: &ShapingFont) -> Result<(f64, f64), String> {
    let font = font.tables();
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

fn shape_lines<'a>(
    lines: impl ExactSizeIterator<Item = &'a str>,
    shaper: &LineShaper<'_>,
) -> Result<Vec<Vec<KrillaGlyph>>, String> {
    let mut shaped = Vec::new();
    shaped
        .try_reserve_exact(lines.len())
        .map_err(|error| format!("failed to allocate shaped text line collection: {error}"))?;
    for line in lines {
        shaped.push(shape_line(line, shaper)?);
    }
    Ok(shaped)
}

fn shape_line(text: &str, shaper: &LineShaper<'_>) -> Result<Vec<KrillaGlyph>, String> {
    if text.is_empty() {
        return Ok(Vec::new());
    }

    let mut buffer = Buffer::new();
    buffer.push_str(text);
    buffer.guess_segment_properties();
    let direction = buffer.direction();
    harfrust::shape(&shaper.font, &mut buffer, ShapeOptions::new())
        .map_err(|error| format!("text shaping failed: {error}"))?;
    if !buffer.allocation_successful() {
        return Err("failed to allocate shaped glyph buffer".to_owned());
    }
    let positions = buffer.glyph_positions();
    let infos = buffer.glyph_infos();
    let units_per_em = shaper.units_per_em;
    let mut glyphs = Vec::new();
    glyphs
        .try_reserve_exact(buffer.len())
        .map_err(|error| format!("failed to allocate shaped glyph collection: {error}"))?;

    for (index, (info, position)) in infos.iter().zip(positions).enumerate() {
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
        if info.glyph_id == 0 {
            let cluster = text
                .get(start..end)
                .or_else(|| text.get(start..))
                .unwrap_or_default();
            return Err(missing_glyph_error(cluster));
        }

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

/// Characters of a cluster that a missing-glyph error names.
const MISSING_GLYPH_REPORTED_CHARS: usize = 8;

/// Name the code points of a cluster that the font has no glyph for.
///
/// A cluster can be arbitrarily long, so the message names at most
/// `MISSING_GLYPH_REPORTED_CHARS` of its characters.
fn missing_glyph_error(cluster: &str) -> String {
    let mut message = String::from("font does not contain all glyphs needed for text");
    let mut characters = cluster.chars();
    for (position, character) in characters
        .by_ref()
        .take(MISSING_GLYPH_REPORTED_CHARS)
        .enumerate()
    {
        message.push_str(if position == 0 {
            ": no glyph for "
        } else {
            " "
        });
        message.push_str(&format!("U+{:04X} ({character:?})", u32::from(character)));
    }
    if characters.next().is_some() {
        message.push_str(" ...");
    }
    message
}

pub(crate) fn finite_f32(value: f64, label: &str) -> Result<f32, String> {
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
