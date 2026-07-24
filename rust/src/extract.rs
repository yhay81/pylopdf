//! Text extraction engine implemented as a hayro Device.
//!
//! Interpret pages with hayro, collect Unicode and position per glyph, then
//! assemble lines, words, and blocks. Unlike lopdf `extract_text`, hayro handles
//! content-stream comments (lopdf#535), predefined CMaps such as 90ms-RKSJ-H,
//! and invisible text such as OCR layers using `Tr 3`.

use hayro::hayro_interpret::font::Glyph;
use hayro::hayro_interpret::hayro_cmap::BfString;
use hayro::hayro_interpret::{
    BlendMode, ClipPath, Context, Device, GlyphDrawMode, Image, ImageData, InterpreterCache,
    InterpreterSettings, LumaData, Paint, PathDrawMode, SoftMask, TransformExt, interpret_page,
};
use std::collections::HashMap;
use std::sync::Arc;

use hayro::hayro_syntax::page::Page;
use hayro::hayro_syntax::{Filter, Pdf};
use kurbo::{Affine, Point, Rect};

/// Per-font display attributes propagated to spans with pymupdf-compatible flags.
#[derive(Clone, Default)]
struct FontInfo {
    /// PostScript name, or empty when unavailable.
    name: Arc<str>,
    /// pymupdf-compatible flags: italic=2, serif=4, monospace=8, bold=16.
    flags: i64,
}

/// Derive a font name and flags from OutlineGlyph, once per font.
fn font_info_of(glyph: &hayro::hayro_interpret::font::OutlineGlyph) -> FontInfo {
    let Some(data) = glyph.font_data() else {
        return FontInfo::default();
    };
    let name: Arc<str> = Arc::from(data.postscript_name.unwrap_or_default());
    let lower = name.to_ascii_lowercase();
    let mut flags = 0i64;
    if data.is_italic || lower.contains("italic") || lower.contains("oblique") {
        flags |= 2;
    }
    if data.is_serif {
        flags |= 4;
    }
    if data.is_monospace {
        flags |= 8;
    }
    if data.weight.is_some_and(|w| w >= 600) || lower.contains("bold") {
        flags |= 16;
    }
    FontInfo { name, flags }
}

/// One collected glyph in top-left-origin, downward-y display coordinates after
/// the renderer's `initial_transform`, with page rotation resolved.
#[derive(Clone)]
struct GlyphRecord {
    /// Unicode representation; ligatures may contain multiple characters.
    text: String,
    /// Baseline-origin x.
    x: f64,
    /// Baseline-origin y, increasing downward.
    y: f64,
    /// Device-space font size used by line and word thresholds.
    size: f64,
    /// Device-space advance, estimated from size for unknown fonts.
    advance: f64,
    /// Unit baseline direction in display space.
    direction: (f64, f64),
    /// Font identity key for span splitting; Type 3 uses 0.
    font_key: u128,
    /// Font display attributes: name plus pymupdf-compatible flags.
    font: FontInfo,
}

/// A Device that only collects glyphs and ignores all drawing commands.
struct TextCollector {
    glyphs: Vec<GlyphRecord>,
    /// font_key → FontInfo cache, resolving font_data only once per font.
    font_infos: HashMap<u128, FontInfo>,
}

impl Device<'_> for TextCollector {
    fn set_soft_mask(&mut self, _: Option<SoftMask<'_>>) {}
    fn set_blend_mode(&mut self, _: BlendMode) {}
    fn draw_path(&mut self, _: &kurbo::BezPath, _: Affine, _: &Paint<'_>, _: &PathDrawMode) {}
    fn push_clip_path(&mut self, _: &ClipPath) {}
    fn push_transparency_group(&mut self, _: f32, _: Option<SoftMask<'_>>, _: BlendMode) {}
    fn draw_image(&mut self, _: Image<'_, '_>, _: Affine) {}
    fn pop_clip_path(&mut self) {}
    fn pop_transparency_group(&mut self) {}

    fn draw_glyph(
        &mut self,
        glyph: &Glyph<'_>,
        transform: Affine,
        glyph_transform: Affine,
        _: &Paint<'_>,
        _: &GlyphDrawMode,
    ) {
        let Some(unicode) = glyph.as_unicode() else {
            return;
        };
        let text = match unicode {
            BfString::Char(c) => c.to_string(),
            BfString::String(s) => s,
        };
        if text.is_empty() {
            return;
        }
        let combined = transform * glyph_transform;
        let origin = combined * Point::ZERO;
        // Font size is the transformed y-basis length × 1000. Hayro normalizes
        // glyph space to 1000 upem, so the transform factor is actual size / 1000.
        let [a, b, c, d, _, _] = combined.as_coeffs();
        let mut size = (c * c + d * d).sqrt() * 1000.0;
        if !size.is_finite() || size <= 0.0 {
            size = 12.0;
        }
        let direction_length = (a * a + b * b).sqrt();
        let direction = if direction_length.is_finite() && direction_length > f64::EPSILON {
            normalize_direction((a / direction_length, b / direction_length))
        } else {
            (1.0, 0.0)
        };
        // Advance is the glyph-space advance transformed by the x basis.
        // Approximate missing fonts such as Type 3 as half the font size.
        let (advance_width, font_key, font) = match glyph {
            Glyph::Outline(g) => {
                let key = g.font_cache_key();
                let info = self
                    .font_infos
                    .entry(key)
                    .or_insert_with(|| font_info_of(g))
                    .clone();
                (g.advance_width().map(f64::from), key, info)
            }
            Glyph::Type3(_) => (None, 0, FontInfo::default()),
        };
        let advance = advance_width
            .map(|adv| {
                let moved = combined * Point::new(adv, 0.0);
                ((moved.x - origin.x).powi(2) + (moved.y - origin.y).powi(2)).sqrt()
            })
            .filter(|a| a.is_finite() && *a > 0.0)
            .unwrap_or(size * 0.5);
        if !origin.x.is_finite() || !origin.y.is_finite() {
            return;
        }
        // Context initial_transform already flips y and applies rotation, so
        // transformed coordinates are directly in top-left-origin display space.
        self.glyphs.push(GlyphRecord {
            text,
            x: origin.x,
            y: origin.y,
            size,
            advance,
            direction,
            font_key,
            font,
        });
    }
}

/// Line threshold: baselines within this factor × font size share a line.
/// This absorbs super/subscripts while separating normal leading of 1.0 or more.
const LINE_TOLERANCE: f64 = 0.5;

/// Word threshold: synthesize a space above this factor × font size.
/// Typical word gaps are about 0.25 em and kerning about ±0.05 em.
const WORD_GAP: f64 = 0.15;

/// Block threshold: leading above this factor × font size starts a paragraph.
const BLOCK_GAP: f64 = 1.5;

/// Split one baseline into separate line segments above this horizontal gap.
const LINE_SEGMENT_GAP: f64 = 2.0;

/// Minimum whitespace gutter relative to the typical font size.
const COLUMN_GUTTER: f64 = 1.5;

/// Ignore full-width headings and footers while discovering column gutters.
const MAX_COLUMN_LINE_WIDTH_RATIO: f64 = 0.75;

/// Avoid treating an isolated side note or indentation as a separate column.
const MIN_COLUMN_LINES: usize = 2;

/// Require column candidates to coexist vertically over this fraction.
const MIN_COLUMN_VERTICAL_OVERLAP: f64 = 0.25;

/// Approximate top/bottom from the baseline without real font metrics.
const ASCENT: f64 = 0.8;
const DESCENT: f64 = 0.2;

/// Snap near-axis direction components so common horizontal text stays exact.
fn normalize_direction((x, y): (f64, f64)) -> (f64, f64) {
    const AXIS_EPSILON: f64 = 1e-9;
    (
        if x.abs() < AXIS_EPSILON { 0.0 } else { x },
        if y.abs() < AXIS_EPSILON { 0.0 } else { y },
    )
}

/// Group glyphs into reading-order lines, each sorted by ascending x.
fn cluster_lines(mut glyphs: Vec<GlyphRecord>) -> Vec<Vec<GlyphRecord>> {
    glyphs.sort_by(|a, b| a.y.total_cmp(&b.y).then(a.x.total_cmp(&b.x)));
    let mut lines: Vec<Vec<GlyphRecord>> = Vec::new();
    let mut current_baseline = f64::NEG_INFINITY;
    for glyph in glyphs {
        let tolerance = glyph.size.max(1.0) * LINE_TOLERANCE;
        if (glyph.y - current_baseline).abs() <= tolerance {
            lines
                .last_mut()
                .expect("a line was created immediately before")
                .push(glyph);
        } else {
            current_baseline = glyph.y;
            lines.push(vec![glyph]);
        }
    }
    for line in &mut lines {
        line.sort_by(|a, b| a.x.total_cmp(&b.x));
    }
    lines
}

/// Split independently positioned columns or table cells sharing one baseline.
fn split_line_segments(line: &[GlyphRecord]) -> Vec<Vec<GlyphRecord>> {
    let mut segments: Vec<Vec<GlyphRecord>> = Vec::new();
    let mut previous_end: Option<f64> = None;
    let mut previous_size = 0.0_f64;
    for glyph in line {
        let threshold = previous_size.max(glyph.size).max(1.0) * LINE_SEGMENT_GAP;
        if previous_end.is_some_and(|end| glyph.x - end > threshold) {
            segments.push(Vec::new());
        }
        previous_end = Some(glyph.x + glyph.advance);
        previous_size = glyph.size;
        if segments.is_empty() {
            segments.push(Vec::new());
        }
        segments
            .last_mut()
            .expect("a segment was created immediately before")
            .push(glyph.clone());
    }
    segments
}

/// Split baseline bands only when a sustained page-level column gutter exists.
fn order_page_lines(clustered: Vec<Vec<GlyphRecord>>) -> Vec<Vec<GlyphRecord>> {
    let segments: Vec<Vec<GlyphRecord>> = clustered
        .iter()
        .flat_map(|line| split_line_segments(line))
        .collect();
    if column_boundary(&segments).is_some_and(|boundary| valid_column_split(&segments, boundary)) {
        order_columns(segments)
    } else {
        clustered
    }
}

/// Return a line's bbox without exposing the internal glyph representation.
fn line_bbox(line: &[GlyphRecord]) -> BBox {
    glyphs_bbox(line)
}

/// Median font size across lines, used to make gutter thresholds scale-aware.
fn typical_line_size(lines: &[Vec<GlyphRecord>]) -> f64 {
    let mut sizes: Vec<f64> = lines
        .iter()
        .filter_map(|line| line.iter().map(|glyph| glyph.size).reduce(f64::max))
        .filter(|size| size.is_finite() && *size > 0.0)
        .collect();
    if sizes.is_empty() {
        return 12.0;
    }
    sizes.sort_by(f64::total_cmp);
    sizes[sizes.len() / 2]
}

/// Find the strongest vertical whitespace gutter between line segments.
fn column_boundary(lines: &[Vec<GlyphRecord>]) -> Option<f64> {
    if lines.len() < MIN_COLUMN_LINES * 2 {
        return None;
    }
    let bounds: Vec<BBox> = lines.iter().map(|line| line_bbox(line)).collect();
    let region_x0 = bounds.iter().map(|(x0, _, _, _)| *x0).reduce(f64::min)?;
    let region_x1 = bounds.iter().map(|(_, _, x1, _)| *x1).reduce(f64::max)?;
    let region_width = region_x1 - region_x0;
    if !region_width.is_finite() || region_width <= 0.0 {
        return None;
    }

    let mut intervals: Vec<(f64, f64)> = bounds
        .iter()
        .filter_map(|(x0, _, x1, _)| {
            let width = x1 - x0;
            (width <= region_width * MAX_COLUMN_LINE_WIDTH_RATIO).then_some((*x0, *x1))
        })
        .collect();
    intervals.sort_by(|a, b| a.0.total_cmp(&b.0));
    let first = intervals.first().copied()?;
    let mut merged = vec![first];
    for (x0, x1) in intervals.into_iter().skip(1) {
        let last = merged
            .last_mut()
            .expect("the first interval was inserted immediately before");
        if x0 <= last.1 {
            last.1 = last.1.max(x1);
        } else {
            merged.push((x0, x1));
        }
    }

    let minimum_gap = (typical_line_size(lines) * COLUMN_GUTTER).max(12.0);
    merged
        .windows(2)
        .filter_map(|pair| {
            let gap = pair[1].0 - pair[0].1;
            (gap >= minimum_gap).then_some((gap, (pair[0].1 + pair[1].0) * 0.5))
        })
        .max_by(|a, b| a.0.total_cmp(&b.0))
        .map(|(_, boundary)| boundary)
}

/// Return the vertical extent of lines entirely on one side of a gutter.
fn side_vertical_extent(
    lines: &[Vec<GlyphRecord>],
    boundary: f64,
    left_side: bool,
) -> Option<(f64, f64, usize)> {
    let mut y0 = f64::INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    let mut count = 0;
    for line in lines {
        let (line_x0, line_y0, line_x1, line_y1) = line_bbox(line);
        let belongs = if left_side {
            line_x1 <= boundary
        } else {
            line_x0 >= boundary
        };
        if belongs {
            y0 = y0.min(line_y0);
            y1 = y1.max(line_y1);
            count += 1;
        }
    }
    (count > 0).then_some((y0, y1, count))
}

/// Validate that both sides are sustained columns rather than indentation.
fn valid_column_split(lines: &[Vec<GlyphRecord>], boundary: f64) -> bool {
    let Some((left_y0, left_y1, left_count)) = side_vertical_extent(lines, boundary, true) else {
        return false;
    };
    let Some((right_y0, right_y1, right_count)) = side_vertical_extent(lines, boundary, false)
    else {
        return false;
    };
    if left_count < MIN_COLUMN_LINES || right_count < MIN_COLUMN_LINES {
        return false;
    }
    let overlap = left_y1.min(right_y1) - left_y0.max(right_y0);
    let shorter_height = (left_y1 - left_y0).min(right_y1 - right_y0);
    overlap > 0.0 && shorter_height > 0.0 && overlap / shorter_height >= MIN_COLUMN_VERTICAL_OVERLAP
}

/// Recursively order column regions left-to-right while preserving spanning
/// headings above them and footers below them.
fn order_columns(lines: Vec<Vec<GlyphRecord>>) -> Vec<Vec<GlyphRecord>> {
    let Some(boundary) = column_boundary(&lines) else {
        return lines;
    };
    if !valid_column_split(&lines, boundary) {
        return lines;
    }

    let side_centers: Vec<f64> = lines
        .iter()
        .filter_map(|line| {
            let (x0, y0, x1, y1) = line_bbox(line);
            (x1 <= boundary || x0 >= boundary).then_some((y0 + y1) * 0.5)
        })
        .collect();
    let Some(first_center) = side_centers.iter().copied().reduce(f64::min) else {
        return lines;
    };
    let Some(last_center) = side_centers.iter().copied().reduce(f64::max) else {
        return lines;
    };
    let has_middle_spanning = lines.iter().any(|line| {
        let (x0, y0, x1, y1) = line_bbox(line);
        let center = (y0 + y1) * 0.5;
        x0 < boundary && x1 > boundary && center > first_center && center < last_center
    });
    if has_middle_spanning {
        return lines;
    }

    let mut top = Vec::new();
    let mut left = Vec::new();
    let mut right = Vec::new();
    let mut bottom = Vec::new();
    for line in lines {
        let (x0, y0, x1, y1) = line_bbox(&line);
        if x1 <= boundary {
            left.push(line);
        } else if x0 >= boundary {
            right.push(line);
        } else if (y0 + y1) * 0.5 <= first_center {
            top.push(line);
        } else {
            bottom.push(line);
        }
    }

    top.extend(order_columns(left));
    top.extend(order_columns(right));
    top.extend(bottom);
    top
}

/// Reusable interpretation of one page.
///
/// Glyph collection and line clustering are the expensive hayro operations.
/// The owned result can serve text, positioned layout, and search repeatedly
/// without retaining references into the parsed PDF.
pub(crate) struct TextPage {
    width: f64,
    height: f64,
    lines: Vec<Vec<GlyphRecord>>,
}

impl TextPage {
    pub(crate) fn new(pdf: &Pdf, page: &Page<'_>, settings: InterpreterSettings) -> Self {
        let (width, height) = page.render_dimensions();
        let lines = order_page_lines(cluster_lines(collect_glyphs(pdf, page, settings)));
        Self {
            width: f64::from(width),
            height: f64::from(height),
            lines,
        }
    }

    pub(crate) fn text(&self) -> String {
        assemble_text(&self.lines)
    }

    pub(crate) fn layout(&self) -> (f64, f64, Vec<BlockTuple>) {
        (self.width, self.height, assemble_layout(&self.lines))
    }

    pub(crate) fn search(&self, needle: &str) -> Vec<BBox> {
        search_lines(&self.lines, needle)
    }
}

/// Decide whether to insert a space from the gap between adjacent glyphs.
fn needs_gap(prev_end: Option<f64>, glyph: &GlyphRecord) -> bool {
    prev_end.is_some_and(|end| glyph.x - end > glyph.size.max(1.0) * WORD_GAP)
}

/// Assemble glyphs into top-to-bottom, left-to-right plain text.
fn assemble_text(lines: &[Vec<GlyphRecord>]) -> String {
    let mut out = String::new();
    for line in lines {
        let mut prev_end: Option<f64> = None;
        for glyph in line {
            if needs_gap(prev_end, glyph) && !out.ends_with(' ') && !out.ends_with('\n') {
                out.push(' ');
            }
            out.push_str(&glyph.text);
            prev_end = Some(glyph.x + glyph.advance);
        }
        // Drop extra whitespace glyphs at line ends.
        while out.ends_with(' ') {
            out.pop();
        }
        out.push('\n');
    }
    out
}

/// Bbox `(x0, y0, x1, y1)` with top-left origin and downward y.
type BBox = (f64, f64, f64, f64);
/// Span: `(bbox, text, size, origin, font name, flags)`.
type SpanTuple = (BBox, String, f64, (f64, f64), String, i64);
/// Word: `(bbox, text)`.
type WordTuple = (BBox, String);
/// Line: `(bbox, spans, words, baseline direction, writing mode)`.
type LineTuple = (BBox, Vec<SpanTuple>, Vec<WordTuple>, (f64, f64), u8);
/// Block: `(bbox, lines)`.
pub(crate) type BlockTuple = (BBox, Vec<LineTuple>);

/// Glyph bounding box with vertical extents approximated from baseline and size.
fn glyphs_bbox(glyphs: &[GlyphRecord]) -> BBox {
    let mut x0 = f64::INFINITY;
    let mut y0 = f64::INFINITY;
    let mut x1 = f64::NEG_INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    for g in glyphs {
        x0 = x0.min(g.x);
        x1 = x1.max(g.x + g.advance);
        y0 = y0.min(g.y - g.size * ASCENT);
        y1 = y1.max(g.y + g.size * DESCENT);
    }
    (x0, y0, x1, y1)
}

/// Split a line into contiguous spans sharing size and font.
fn split_spans(line: &[GlyphRecord]) -> Vec<SpanTuple> {
    let mut spans: Vec<SpanTuple> = Vec::new();
    let mut start = 0;
    for i in 1..=line.len() {
        let boundary = i == line.len() || {
            let (a, b) = (&line[i - 1], &line[i]);
            b.font_key != a.font_key || (b.size - a.size).abs() > 0.1
        };
        if boundary {
            let glyphs = &line[start..i];
            let mut text = String::new();
            let mut prev_end: Option<f64> = None;
            for glyph in glyphs {
                if needs_gap(prev_end, glyph) && !text.ends_with(' ') {
                    text.push(' ');
                }
                text.push_str(&glyph.text);
                prev_end = Some(glyph.x + glyph.advance);
            }
            spans.push((
                glyphs_bbox(glyphs),
                text,
                glyphs.iter().map(|g| g.size).fold(0.0, f64::max),
                (glyphs[0].x, glyphs[0].y),
                glyphs[0].font.name.to_string(),
                glyphs[0].font.flags,
            ));
            start = i;
        }
    }
    spans
}

/// Split a line into words delimited by whitespace and gaps.
fn split_words(line: &[GlyphRecord]) -> Vec<WordTuple> {
    let mut words: Vec<WordTuple> = Vec::new();
    let mut current: Vec<&GlyphRecord> = Vec::new();
    let mut prev_end: Option<f64> = None;
    let mut flush = |current: &mut Vec<&GlyphRecord>| {
        if current.is_empty() {
            return;
        }
        let text: String = current.iter().map(|g| g.text.as_str()).collect();
        let mut x0 = f64::INFINITY;
        let mut y0 = f64::INFINITY;
        let mut x1 = f64::NEG_INFINITY;
        let mut y1 = f64::NEG_INFINITY;
        for g in current.iter() {
            x0 = x0.min(g.x);
            x1 = x1.max(g.x + g.advance);
            y0 = y0.min(g.y - g.size * ASCENT);
            y1 = y1.max(g.y + g.size * DESCENT);
        }
        words.push(((x0, y0, x1, y1), text));
        current.clear();
    };
    for glyph in line {
        let is_space = glyph.text.chars().all(char::is_whitespace);
        if is_space || needs_gap(prev_end, glyph) {
            flush(&mut current);
        }
        if !is_space {
            current.push(glyph);
        }
        prev_end = Some(glyph.x + glyph.advance);
    }
    flush(&mut current);
    words
}

/// Representative baseline direction and PDF writing mode for a line.
fn line_direction(line: &[GlyphRecord]) -> ((f64, f64), u8) {
    let direction = line.first().map_or((1.0, 0.0), |glyph| glyph.direction);
    // A rotated horizontal line can have a vertical baseline direction while
    // remaining writing mode 0. Hayro does not expose the font's WMode yet, so
    // do not infer it from geometry. TextPage retains direction for the future
    // vertical-writing assembler.
    (direction, 0)
}

/// Assemble collected glyphs into blocks, lines, spans, and words.
fn assemble_layout(lines: &[Vec<GlyphRecord>]) -> Vec<BlockTuple> {
    let mut blocks: Vec<Vec<&[GlyphRecord]>> = Vec::new();
    let mut prev_baseline: Option<f64> = None;
    let mut prev_size = 0.0_f64;
    for line in lines {
        let baseline = line[0].y;
        let line_size = line.iter().map(|g| g.size).fold(0.0, f64::max);
        let new_block = match prev_baseline {
            Some(prev) => {
                let scale = prev_size.max(line_size).max(1.0);
                baseline - prev > scale * BLOCK_GAP || prev - baseline > scale * LINE_TOLERANCE
            }
            None => true,
        };
        if new_block {
            blocks.push(Vec::new());
        }
        prev_baseline = Some(baseline);
        prev_size = line_size;
        blocks
            .last_mut()
            .expect("a block was created immediately before")
            .push(line);
    }

    blocks
        .into_iter()
        .map(|block_lines| {
            let line_tuples: Vec<LineTuple> = block_lines
                .iter()
                .map(|line| {
                    let (direction, writing_mode) = line_direction(line);
                    (
                        glyphs_bbox(line),
                        split_spans(line),
                        split_words(line),
                        direction,
                        writing_mode,
                    )
                })
                .collect();
            let mut x0 = f64::INFINITY;
            let mut y0 = f64::INFINITY;
            let mut x1 = f64::NEG_INFINITY;
            let mut y1 = f64::NEG_INFINITY;
            for ((lx0, ly0, lx1, ly1), _, _, _, _) in &line_tuples {
                x0 = x0.min(*lx0);
                y0 = y0.min(*ly0);
                x1 = x1.max(*lx1);
                y1 = y1.max(*ly1);
            }
            ((x0, y0, x1, y1), line_tuples)
        })
        .collect()
}

/// Build lowercase search text and a character-to-glyph map from a line.
///
/// Insert synthetic spaces with no glyph mapping between words. When a ligature
/// or lowercasing yields multiple characters, map all of them to the same glyph.
fn line_search_index(line: &[GlyphRecord]) -> (String, Vec<Option<usize>>) {
    let mut haystack = String::new();
    let mut map: Vec<Option<usize>> = Vec::new();
    let mut prev_end: Option<f64> = None;
    for (index, glyph) in line.iter().enumerate() {
        if needs_gap(prev_end, glyph) && !haystack.ends_with(' ') {
            haystack.push(' ');
            map.push(None);
        }
        for ch in glyph.text.chars() {
            for lowered in ch.to_lowercase() {
                haystack.push(lowered);
                map.push(Some(index));
            }
        }
        prev_end = Some(glyph.x + glyph.advance);
    }
    (haystack, map)
}

/// Search page text case-insensitively and return one bbox per match.
///
/// Search is line-based and does not detect matches across lines.
fn search_lines(lines: &[Vec<GlyphRecord>], needle: &str) -> Vec<BBox> {
    let needle_lower: String = needle.chars().flat_map(char::to_lowercase).collect();
    if needle_lower.is_empty() {
        return Vec::new();
    }
    let mut hits = Vec::new();
    for line in lines {
        let (haystack, map) = line_search_index(line);
        let mut from = 0;
        while let Some(found) = haystack[from..].find(&needle_lower) {
            let start = from + found;
            let end = start + needle_lower.len();
            // Byte position → character position → glyph set.
            let char_start = haystack[..start].chars().count();
            let char_len = haystack[start..end].chars().count();
            let glyph_indices: Vec<usize> = map[char_start..char_start + char_len]
                .iter()
                .flatten()
                .copied()
                .collect();
            if let (Some(&first), Some(&last)) = (glyph_indices.first(), glyph_indices.last()) {
                let matched = &line[first..=last];
                hits.push(glyphs_bbox(matched));
            }
            from = end;
        }
    }
    hits
}

/// Extracted image: `(width, height, page bbox, "jpeg"/"png", bytes)`.
pub(crate) type ImageTuple = (u32, u32, BBox, String, Vec<u8>);

/// A Device that only collects images.
struct ImageCollector {
    images: Vec<ImageTuple>,
}

/// JPEG magic number (SOI marker).
const JPEG_MAGIC: [u8; 3] = [0xFF, 0xD8, 0xFF];

/// Return original JPEG bytes when they can be extracted unchanged.
///
/// Supports `/Filter` of `[DCTDecode]` or `[FlateDecode, DCTDecode]`.
/// Verify the decoded prefix is JPEG magic; otherwise return None so the caller
/// can fall back to decoding and PNG encoding.
fn try_jpeg_passthrough(stream: &hayro::hayro_syntax::object::Stream<'_>) -> Option<Vec<u8>> {
    use std::io::Read;
    let filters = stream.filters();
    let data = match filters.as_slice() {
        [Filter::DctDecode] => stream.raw_data().into_owned(),
        [Filter::FlateDecode, Filter::DctDecode] => {
            let mut out = Vec::new();
            flate2::read::ZlibDecoder::new(stream.raw_data().as_ref())
                .read_to_end(&mut out)
                .ok()?;
            out
        }
        _ => return None,
    };
    data.starts_with(&JPEG_MAGIC).then_some(data)
}

/// Transform an image pixel rectangle and return its display-space bounding box.
///
/// Before `draw_image`, hayro pre-concats pixel space (top origin,
/// `0..width × 0..height`) to the PDF unit square, so pass pixel corners
/// directly to the transform. `initial_transform` already flips y and rotates,
/// producing display coordinates.
fn image_bbox(transform: Affine, width: f64, height: f64) -> BBox {
    let mut x0 = f64::INFINITY;
    let mut y0 = f64::INFINITY;
    let mut x1 = f64::NEG_INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    for (px, py) in [(0.0, 0.0), (width, 0.0), (0.0, height), (width, height)] {
        let p = transform * Point::new(px, py);
        x0 = x0.min(p.x);
        x1 = x1.max(p.x);
        y0 = y0.min(p.y);
        y1 = y1.max(p.y);
    }
    (x0, y0, x1, y1)
}

/// Encode pixel data as PNG.
///
/// Rendering uses Fast/fdeflate: Balanced is tens of times slower for about 10%
/// smaller output and makes PNG the dominant render cost in benchmarks.
/// `get_images` keeps Balanced because extracted images are stored artifacts.
pub(crate) fn encode_png(
    width: u32,
    height: u32,
    color: png::ColorType,
    data: &[u8],
    compression: png::Compression,
) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut out, width, height);
        encoder.set_color(color);
        encoder.set_depth(png::BitDepth::Eight);
        encoder.set_compression(compression);
        let mut writer = encoder.write_header().ok()?;
        writer.write_image_data(data).ok()?;
        writer.finish().ok()?;
    }
    Some(out)
}

/// Encode decoded RGB/gray raster data plus separate alpha as PNG.
fn encode_raster_png(image: ImageData, alpha: Option<LumaData>) -> Option<(u32, u32, Vec<u8>)> {
    let (rgb, width, height) = match image {
        ImageData::Rgb(rgb) => (rgb.data, rgb.width, rgb.height),
        ImageData::Luma(luma) => {
            let data = match &alpha {
                Some(a) if a.width == luma.width && a.height == luma.height => {
                    let interleaved: Vec<u8> = luma
                        .data
                        .iter()
                        .zip(&a.data)
                        .flat_map(|(g, a)| [*g, *a])
                        .collect();
                    return encode_png(
                        luma.width,
                        luma.height,
                        png::ColorType::GrayscaleAlpha,
                        &interleaved,
                        png::Compression::Balanced,
                    )
                    .map(|png| (luma.width, luma.height, png));
                }
                _ => luma.data,
            };
            return encode_png(
                luma.width,
                luma.height,
                png::ColorType::Grayscale,
                &data,
                png::Compression::Balanced,
            )
            .map(|png| (luma.width, luma.height, png));
        }
    };
    match alpha {
        Some(a) if a.width == width && a.height == height => {
            let interleaved: Vec<u8> = rgb
                .chunks(3)
                .zip(&a.data)
                .flat_map(|(rgb, a)| [rgb[0], rgb[1], rgb[2], *a])
                .collect();
            encode_png(
                width,
                height,
                png::ColorType::Rgba,
                &interleaved,
                png::Compression::Balanced,
            )
            .map(|png| (width, height, png))
        }
        _ => encode_png(
            width,
            height,
            png::ColorType::Rgb,
            &rgb,
            png::Compression::Balanced,
        )
        .map(|png| (width, height, png)),
    }
}

impl Device<'_> for ImageCollector {
    fn set_soft_mask(&mut self, _: Option<SoftMask<'_>>) {}
    fn set_blend_mode(&mut self, _: BlendMode) {}
    fn draw_path(&mut self, _: &kurbo::BezPath, _: Affine, _: &Paint<'_>, _: &PathDrawMode) {}
    fn push_clip_path(&mut self, _: &ClipPath) {}
    fn push_transparency_group(&mut self, _: f32, _: Option<SoftMask<'_>>, _: BlendMode) {}
    fn draw_glyph(
        &mut self,
        _: &Glyph<'_>,
        _: Affine,
        _: Affine,
        _: &Paint<'_>,
        _: &GlyphDrawMode,
    ) {
    }
    fn pop_clip_path(&mut self) {}
    fn pop_transparency_group(&mut self) {}

    fn draw_image(&mut self, image: Image<'_, '_>, transform: Affine) {
        let bbox = image_bbox(
            transform,
            f64::from(image.width()),
            f64::from(image.height()),
        );
        match image {
            Image::Raster(raster) => {
                // Extract images ending in DCTDecode as raw JPEG without recompression.
                if let Some(jpeg) = try_jpeg_passthrough(raster.stream()) {
                    self.images.push((
                        raster.width(),
                        raster.height(),
                        bbox,
                        "jpeg".to_owned(),
                        jpeg,
                    ));
                    return;
                }
                let images = &mut self.images;
                raster.with_rgba(
                    |image_data, alpha| {
                        if let Some((width, height, data)) = encode_raster_png(image_data, alpha) {
                            images.push((width, height, bbox, "png".to_owned(), data));
                        }
                    },
                    None,
                );
            }
            Image::Stencil(stencil) => {
                let images = &mut self.images;
                stencil.with_stencil(
                    |luma, _| {
                        if let Some(data) = encode_png(
                            luma.width,
                            luma.height,
                            png::ColorType::Grayscale,
                            &luma.data,
                            png::Compression::Balanced,
                        ) {
                            images.push((luma.width, luma.height, bbox, "png".to_owned(), data));
                        }
                    },
                    None,
                );
            }
        }
    }
}

/// Build the extraction Context.
///
/// Use the renderer's `initial_transform(true)` so Device coordinates are in
/// top-left-origin display space with rotation resolved.
fn extraction_context<'a>(
    pdf: &'a Pdf,
    page: &Page<'a>,
    cache: &'a InterpreterCache<'a>,
    settings: InterpreterSettings,
) -> Context<'a> {
    let (width, height) = page.render_dimensions();
    Context::new(
        page.initial_transform(true).to_kurbo(),
        Rect::new(0.0, 0.0, f64::from(width), f64::from(height)),
        cache,
        pdf.xref(),
        settings,
    )
}

/// Extract images drawn on the given page.
pub(crate) fn extract_page_images(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
) -> Vec<ImageTuple> {
    let cache = InterpreterCache::new();
    let mut context = extraction_context(pdf, page, &cache, settings);
    let mut collector = ImageCollector { images: Vec::new() };
    interpret_page(page, &mut context, &mut collector);
    collector.images
}

/// Interpret a page and collect glyphs.
fn collect_glyphs(pdf: &Pdf, page: &Page<'_>, settings: InterpreterSettings) -> Vec<GlyphRecord> {
    let cache = InterpreterCache::new();
    let mut context = extraction_context(pdf, page, &cache, settings);
    let mut collector = TextCollector {
        glyphs: Vec::new(),
        font_infos: HashMap::new(),
    };
    interpret_page(page, &mut context, &mut collector);
    collector.glyphs
}
