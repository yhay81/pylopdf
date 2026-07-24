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
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use hayro::hayro_syntax::page::Page;
use hayro::hayro_syntax::{Filter, Pdf};
use kurbo::{Affine, PathSeg, Point, Rect};

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
    /// PDF writing mode: 0 for horizontal, 1 for vertical.
    ///
    /// Hayro currently does not expose WMode. This remains 0 for normal and
    /// rotated horizontal text, and is set to 1 only for conservative CJK
    /// vertical-layout detections during line assembly.
    writing_mode: u8,
    /// Font identity key for span splitting; Type 3 uses 0.
    font_key: u128,
    /// Font display attributes: name plus pymupdf-compatible flags.
    font: FontInfo,
}

/// One axis-aligned stroked path segment in display coordinates.
#[derive(Clone, Copy)]
struct RuleSegment {
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
    horizontal: bool,
}

/// A Device that collects glyphs and table-rule candidates.
struct TextCollector {
    glyphs: Vec<GlyphRecord>,
    rules: Vec<RuleSegment>,
    /// Skip vector-rule collection for ordinary text extraction.
    collect_rules: bool,
    /// font_key → FontInfo cache, resolving font_data only once per font.
    font_infos: HashMap<u128, FontInfo>,
}

impl Device<'_> for TextCollector {
    fn set_soft_mask(&mut self, _: Option<SoftMask<'_>>) {}
    fn set_blend_mode(&mut self, _: BlendMode) {}
    fn draw_path(
        &mut self,
        path: &kurbo::BezPath,
        transform: Affine,
        _: &Paint<'_>,
        mode: &PathDrawMode,
    ) {
        if !self.collect_rules || self.rules.len() >= MAX_TABLE_RULES {
            return;
        }
        if matches!(mode, PathDrawMode::Fill(_)) {
            if let Some(rule) = filled_rule(path, transform) {
                self.rules.push(rule);
            }
            return;
        }
        for segment in path.segments() {
            let PathSeg::Line(line) = segment else {
                continue;
            };
            let start = transform * line.p0;
            let end = transform * line.p1;
            if !start.x.is_finite()
                || !start.y.is_finite()
                || !end.x.is_finite()
                || !end.y.is_finite()
            {
                continue;
            }
            let dx = (end.x - start.x).abs();
            let dy = (end.y - start.y).abs();
            let rule = if dy <= TABLE_AXIS_TOLERANCE && dx >= MIN_TABLE_RULE_LENGTH {
                RuleSegment {
                    x0: start.x.min(end.x),
                    y0: (start.y + end.y) * 0.5,
                    x1: start.x.max(end.x),
                    y1: (start.y + end.y) * 0.5,
                    horizontal: true,
                }
            } else if dx <= TABLE_AXIS_TOLERANCE && dy >= MIN_TABLE_RULE_LENGTH {
                RuleSegment {
                    x0: (start.x + end.x) * 0.5,
                    y0: start.y.min(end.y),
                    x1: (start.x + end.x) * 0.5,
                    y1: start.y.max(end.y),
                    horizontal: false,
                }
            } else {
                continue;
            };
            self.rules.push(rule);
            if self.rules.len() >= MAX_TABLE_RULES {
                break;
            }
        }
    }
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
            writing_mode: 0,
            font_key,
            font,
        });
    }
}

/// Line threshold: baselines within this factor × font size share a line.
/// This absorbs super/subscripts while separating normal leading of 1.0 or more.
const LINE_TOLERANCE: f64 = 0.5;

/// Cross-axis tolerance when joining glyphs on one vertical baseline.
const VERTICAL_LINE_TOLERANCE: f64 = 0.35;

/// Minimum glyph count before inferring a CJK vertical writing mode.
const MIN_VERTICAL_CJK_GLYPHS: usize = 3;

/// Bound the conservative CJK geometry pass on pathological pages.
const MAX_VERTICAL_CJK_CANDIDATES: usize = 4096;

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

/// Maximum rule count considered for table detection on one page.
const MAX_TABLE_RULES: usize = 4096;

/// Maximum cells materialized from one connected grid.
const MAX_TABLE_CELLS: usize = 4096;

/// Bound merged-cell rectangle searches on damaged or adversarial grids.
const MAX_TABLE_SPAN_CANDIDATES: usize = 65_536;

/// Minimum aligned rows required by the opt-in borderless-text strategy.
const MIN_TEXT_TABLE_ROWS: usize = 3;

/// Bound the number of inferred text columns.
const MAX_TEXT_TABLE_COLUMNS: usize = 32;

/// Column edges this close relative to font size count as aligned.
const TEXT_TABLE_ALIGNMENT_TOLERANCE: f64 = 1.0;

/// Maximum gap between consecutive borderless table rows.
const TEXT_TABLE_ROW_GAP: f64 = 2.5;

/// Treat transformed path segments this close to an axis as horizontal/vertical.
const TABLE_AXIS_TOLERANCE: f64 = 0.5;

/// Ignore tiny decorations and glyph-like path segments.
const MIN_TABLE_RULE_LENGTH: f64 = 3.0;

/// Maximum short axis accepted as a filled table rule.
const MAX_FILLED_RULE_THICKNESS: f64 = 4.0;

/// Reject compact filled decorations even when one axis is slightly longer.
const MIN_FILLED_RULE_ASPECT: f64 = 4.0;

/// Snap rule coordinates and intersections within this distance.
const TABLE_SNAP_TOLERANCE: f64 = 1.0;

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

/// Convert a thin, axis-aligned filled polygon into its centerline.
///
/// PDF generators often paint table rules as narrow filled rectangles instead
/// of stroking paths. Curves and compact shapes are excluded so glyph outlines
/// and ordinary decorations do not become table candidates.
fn filled_rule(path: &kurbo::BezPath, transform: Affine) -> Option<RuleSegment> {
    let mut x0 = f64::INFINITY;
    let mut y0 = f64::INFINITY;
    let mut x1 = f64::NEG_INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    let mut line_count = 0;
    for segment in path.segments() {
        let PathSeg::Line(line) = segment else {
            return None;
        };
        for point in [transform * line.p0, transform * line.p1] {
            if !point.x.is_finite() || !point.y.is_finite() {
                return None;
            }
            x0 = x0.min(point.x);
            y0 = y0.min(point.y);
            x1 = x1.max(point.x);
            y1 = y1.max(point.y);
        }
        line_count += 1;
    }
    if line_count < 4 {
        return None;
    }
    let width = x1 - x0;
    let height = y1 - y0;
    if height <= MAX_FILLED_RULE_THICKNESS
        && width >= MIN_TABLE_RULE_LENGTH
        && width >= height.max(f64::EPSILON) * MIN_FILLED_RULE_ASPECT
    {
        Some(RuleSegment {
            x0,
            y0: (y0 + y1) * 0.5,
            x1,
            y1: (y0 + y1) * 0.5,
            horizontal: true,
        })
    } else if width <= MAX_FILLED_RULE_THICKNESS
        && height >= MIN_TABLE_RULE_LENGTH
        && height >= width.max(f64::EPSILON) * MIN_FILLED_RULE_ASPECT
    {
        Some(RuleSegment {
            x0: (x0 + x1) * 0.5,
            y0,
            x1: (x0 + x1) * 0.5,
            y1,
            horizontal: false,
        })
    } else {
        None
    }
}

/// Return whether a baseline direction is predominantly vertical.
fn has_vertical_baseline(glyph: &GlyphRecord) -> bool {
    glyph.direction.1.abs() > glyph.direction.0.abs()
}

/// Return whether all visible characters are CJK or full-width punctuation.
fn is_cjk_text(text: &str) -> bool {
    let mut saw_character = false;
    for ch in text.chars().filter(|ch| !ch.is_whitespace()) {
        saw_character = true;
        if !matches!(
            ch,
            '\u{2E80}'..='\u{2FFF}'
                | '\u{3000}'..='\u{30FF}'
                | '\u{31F0}'..='\u{31FF}'
                | '\u{3400}'..='\u{4DBF}'
                | '\u{4E00}'..='\u{9FFF}'
                | '\u{AC00}'..='\u{D7AF}'
                | '\u{F900}'..='\u{FAFF}'
                | '\u{FF00}'..='\u{FFEF}'
                | '\u{20000}'..='\u{3134F}'
        ) {
            return false;
        }
    }
    saw_character
}

/// Identify CJK glyphs that advance vertically even though hayro reports the
/// font's transformed horizontal basis. Requiring a local vertical neighbour
/// and no local horizontal neighbour avoids reclassifying normal CJK rows.
fn inferred_vertical_cjk_indices(glyphs: &[GlyphRecord]) -> Vec<usize> {
    let cjk_indices: Vec<usize> = glyphs
        .iter()
        .enumerate()
        .filter_map(|(index, glyph)| {
            (!has_vertical_baseline(glyph) && is_cjk_text(&glyph.text)).then_some(index)
        })
        .collect();
    if cjk_indices.len() < MIN_VERTICAL_CJK_GLYPHS
        || cjk_indices.len() > MAX_VERTICAL_CJK_CANDIDATES
    {
        return Vec::new();
    }

    let mut eligible = Vec::new();
    for &index in &cjk_indices {
        let glyph = &glyphs[index];
        let mut has_vertical_neighbour = false;
        let mut has_horizontal_neighbour = false;
        for &other_index in &cjk_indices {
            if other_index == index {
                continue;
            }
            let other = &glyphs[other_index];
            if other.font_key != glyph.font_key {
                continue;
            }
            let scale = glyph.size.max(other.size).max(1.0);
            let dx = (other.x - glyph.x).abs();
            let dy = (other.y - glyph.y).abs();
            if dx <= scale * VERTICAL_LINE_TOLERANCE && dy >= scale * 0.35 && dy <= scale * 1.35 {
                has_vertical_neighbour = true;
            }
            if dy <= scale * LINE_TOLERANCE && dx >= scale * 0.2 && dx <= scale * 1.8 {
                has_horizontal_neighbour = true;
            }
            if has_vertical_neighbour && has_horizontal_neighbour {
                break;
            }
        }
        if has_vertical_neighbour && !has_horizontal_neighbour {
            eligible.push(index);
        }
    }
    eligible
}

/// Extract conservative CJK vertical chains from otherwise horizontal glyphs.
fn extract_inferred_vertical_cjk(
    glyphs: Vec<GlyphRecord>,
) -> (Vec<GlyphRecord>, Vec<Vec<GlyphRecord>>) {
    let eligible = inferred_vertical_cjk_indices(&glyphs);
    if eligible.len() < MIN_VERTICAL_CJK_GLYPHS {
        return (glyphs, Vec::new());
    }

    let mut selected = vec![false; glyphs.len()];
    for index in eligible {
        selected[index] = true;
    }
    let mut candidates = Vec::new();
    let mut remaining = Vec::new();
    for (index, glyph) in glyphs.into_iter().enumerate() {
        if selected[index] {
            candidates.push(glyph);
        } else {
            remaining.push(glyph);
        }
    }
    candidates.sort_by(|left, right| {
        left.x
            .total_cmp(&right.x)
            .then(left.font_key.cmp(&right.font_key))
            .then(left.y.total_cmp(&right.y))
    });

    let mut lines: Vec<Vec<GlyphRecord>> = Vec::new();
    for glyph in candidates {
        let matching_line = lines.iter_mut().find(|line| {
            let first = &line[0];
            first.font_key == glyph.font_key
                && (first.x - glyph.x).abs()
                    <= first.size.max(glyph.size).max(1.0) * VERTICAL_LINE_TOLERANCE
        });
        if let Some(line) = matching_line {
            line.push(glyph);
        } else {
            lines.push(vec![glyph]);
        }
    }

    let mut accepted = Vec::new();
    for mut line in lines {
        line.sort_by(|left, right| left.y.total_cmp(&right.y));
        let continuous = line.windows(2).all(|pair| {
            let scale = pair[0].size.max(pair[1].size).max(1.0);
            let gap = pair[1].y - pair[0].y;
            gap >= scale * 0.35 && gap <= scale * 1.35
        });
        if line.len() >= MIN_VERTICAL_CJK_GLYPHS && continuous {
            for glyph in &mut line {
                glyph.direction = (0.0, 1.0);
                glyph.writing_mode = 1;
            }
            accepted.push(line);
        } else {
            remaining.extend(line);
        }
    }
    (remaining, accepted)
}

/// Group glyphs whose transformed baseline itself is vertical.
fn cluster_explicit_vertical(mut glyphs: Vec<GlyphRecord>) -> Vec<Vec<GlyphRecord>> {
    glyphs.sort_by(|left, right| right.x.total_cmp(&left.x).then(left.y.total_cmp(&right.y)));
    let mut lines: Vec<Vec<GlyphRecord>> = Vec::new();
    for glyph in glyphs {
        let matching_line = lines.iter_mut().find(|line| {
            let first = &line[0];
            let scale = first.size.max(glyph.size).max(1.0);
            (first.x - glyph.x).abs() <= scale * VERTICAL_LINE_TOLERANCE
                && first.direction.0 * glyph.direction.0 + first.direction.1 * glyph.direction.1
                    > 0.9
        });
        if let Some(line) = matching_line {
            line.push(glyph);
        } else {
            lines.push(vec![glyph]);
        }
    }
    for line in &mut lines {
        let direction = line[0].direction;
        line.sort_by(|left, right| {
            let left_progress = left.x * direction.0 + left.y * direction.1;
            let right_progress = right.x * direction.0 + right.y * direction.1;
            left_progress.total_cmp(&right_progress)
        });
    }
    lines
}

/// Group glyphs into physical text lines.
fn cluster_lines(glyphs: Vec<GlyphRecord>) -> Vec<Vec<GlyphRecord>> {
    let (explicit_vertical, horizontal): (Vec<_>, Vec<_>) =
        glyphs.into_iter().partition(has_vertical_baseline);
    let (mut horizontal, mut vertical_lines) = extract_inferred_vertical_cjk(horizontal);

    horizontal.sort_by(|a, b| a.y.total_cmp(&b.y).then(a.x.total_cmp(&b.x)));
    let mut lines: Vec<Vec<GlyphRecord>> = Vec::new();
    let mut current_baseline = f64::NEG_INFINITY;
    for glyph in horizontal {
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
    vertical_lines.extend(cluster_explicit_vertical(explicit_vertical));
    lines.extend(vertical_lines);
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

/// Count widely separated segments without cloning their glyphs.
fn line_segment_count(line: &[GlyphRecord]) -> usize {
    let mut count = usize::from(!line.is_empty());
    let mut previous_end: Option<f64> = None;
    let mut previous_size = 0.0_f64;
    for glyph in line {
        let threshold = previous_size.max(glyph.size).max(1.0) * LINE_SEGMENT_GAP;
        if previous_end.is_some_and(|end| glyph.x - end > threshold) {
            count += 1;
        }
        previous_end = Some(glyph.x + glyph.advance);
        previous_size = glyph.size;
    }
    count
}

/// Split baseline bands only when a sustained page-level column gutter exists.
fn order_page_lines(clustered: Vec<Vec<GlyphRecord>>) -> Vec<Vec<GlyphRecord>> {
    if clustered
        .iter()
        .any(|line| line.first().is_some_and(has_vertical_baseline))
    {
        return order_vertical_page_lines(clustered);
    }
    if clustered
        .iter()
        .filter(|line| line_segment_count(line) > 1)
        .take(MIN_COLUMN_LINES)
        .count()
        < MIN_COLUMN_LINES
    {
        return clustered;
    }
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

/// Order vertical columns right-to-left, preserving horizontal headers and
/// footers outside the vertical text region.
fn order_vertical_page_lines(clustered: Vec<Vec<GlyphRecord>>) -> Vec<Vec<GlyphRecord>> {
    let vertical_bounds: Vec<BBox> = clustered
        .iter()
        .filter(|line| line.first().is_some_and(has_vertical_baseline))
        .map(|line| line_bbox(line))
        .collect();
    let vertical_y0 = vertical_bounds
        .iter()
        .map(|(_, y0, _, _)| *y0)
        .reduce(f64::min)
        .unwrap_or(f64::NEG_INFINITY);
    let vertical_y1 = vertical_bounds
        .iter()
        .map(|(_, _, _, y1)| *y1)
        .reduce(f64::max)
        .unwrap_or(f64::INFINITY);

    let mut top = Vec::new();
    let mut vertical = Vec::new();
    let mut middle = Vec::new();
    let mut bottom = Vec::new();
    for line in clustered {
        if line.first().is_some_and(has_vertical_baseline) {
            vertical.push(line);
            continue;
        }
        let (_, y0, _, y1) = line_bbox(&line);
        if y1 <= vertical_y0 {
            top.push(line);
        } else if y0 >= vertical_y1 {
            bottom.push(line);
        } else {
            middle.push(line);
        }
    }
    top.sort_by(|left, right| line_bbox(left).1.total_cmp(&line_bbox(right).1));
    vertical.sort_by(|left, right| line_bbox(right).0.total_cmp(&line_bbox(left).0));
    middle.sort_by(|left, right| line_bbox(left).1.total_cmp(&line_bbox(right).1));
    bottom.sort_by(|left, right| line_bbox(left).1.total_cmp(&line_bbox(right).1));
    top.extend(vertical);
    top.extend(middle);
    top.extend(bottom);
    top
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
        let (glyphs, _) = collect_page_marks(pdf, page, settings, false);
        let physical_lines = cluster_lines(glyphs);
        let lines = order_page_lines(physical_lines);
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

/// Reusable table interpretation kept separate from normal text extraction.
pub(crate) struct TablePage {
    tables: Vec<TableTuple>,
    text_tables: Vec<TableTuple>,
}

impl TablePage {
    pub(crate) fn new(pdf: &Pdf, page: &Page<'_>, settings: InterpreterSettings) -> Self {
        let (glyphs, rules) = collect_page_marks(pdf, page, settings, true);
        let physical_lines = cluster_lines(glyphs);
        Self {
            tables: detect_grid_tables(&physical_lines, &rules),
            text_tables: detect_text_tables(&physical_lines),
        }
    }

    pub(crate) fn tables(&self, text_strategy: bool, clip: Option<BBox>) -> Vec<TableTuple> {
        let tables = if text_strategy {
            &self.text_tables
        } else {
            &self.tables
        };
        tables
            .iter()
            .filter(|table| clip.is_none_or(|clip| bbox_is_inside(table.0, clip)))
            .cloned()
            .collect()
    }
}

/// Position along the line's baseline direction.
fn glyph_progress(glyph: &GlyphRecord) -> f64 {
    if has_vertical_baseline(glyph) {
        glyph.x * glyph.direction.0 + glyph.y * glyph.direction.1
    } else {
        glyph.x
    }
}

/// Decide whether to insert a space from the gap between adjacent glyphs.
fn needs_gap(prev_end: Option<f64>, glyph: &GlyphRecord) -> bool {
    if glyph.writing_mode == 1 {
        return false;
    }
    prev_end.is_some_and(|end| glyph_progress(glyph) - end > glyph.size.max(1.0) * WORD_GAP)
}

/// End position of one glyph along its line's baseline.
fn glyph_end(glyph: &GlyphRecord) -> f64 {
    glyph_progress(glyph) + glyph.advance
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
            prev_end = Some(glyph_end(glyph));
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
/// Table: `(bbox, row count, column count, row-major cells)`.
///
/// Continuation slots covered by a merged cell are `None`; the merged cell's
/// top-left slot contains its spanning bbox and text.
/// Diagnostics are `(confidence, alignment error in em, minimum gutter in em,
/// row-gap variation in em)`. Vector-grid metrics are `None`.
type TableDiagnosticsTuple = (f64, Option<f64>, Option<f64>, Option<f64>);
pub(crate) type TableTuple = (
    BBox,
    u32,
    u32,
    Vec<Option<(BBox, String)>>,
    TableDiagnosticsTuple,
);

/// Return whether a candidate bbox is fully contained by a display-space clip.
fn bbox_is_inside((x0, y0, x1, y1): BBox, (clip_x0, clip_y0, clip_x1, clip_y1): BBox) -> bool {
    x0 >= clip_x0 - TABLE_SNAP_TOLERANCE
        && y0 >= clip_y0 - TABLE_SNAP_TOLERANCE
        && x1 <= clip_x1 + TABLE_SNAP_TOLERANCE
        && y1 <= clip_y1 + TABLE_SNAP_TOLERANCE
}

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
                prev_end = Some(glyph_end(glyph));
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
        prev_end = Some(glyph_end(glyph));
    }
    flush(&mut current);
    words
}

/// Small union-find used to split independent rule networks into tables.
struct RuleComponents {
    parent: Vec<usize>,
}

impl RuleComponents {
    fn new(len: usize) -> Self {
        Self {
            parent: (0..len).collect(),
        }
    }

    fn find(&mut self, index: usize) -> usize {
        let parent = self.parent[index];
        if parent != index {
            self.parent[index] = self.find(parent);
        }
        self.parent[index]
    }

    fn union(&mut self, left: usize, right: usize) {
        let left_root = self.find(left);
        let right_root = self.find(right);
        if left_root != right_root {
            self.parent[right_root] = left_root;
        }
    }
}

/// Return whether perpendicular rule segments meet within snap tolerance.
fn rules_intersect(horizontal: RuleSegment, vertical: RuleSegment) -> bool {
    vertical.x0 >= horizontal.x0 - TABLE_SNAP_TOLERANCE
        && vertical.x0 <= horizontal.x1 + TABLE_SNAP_TOLERANCE
        && horizontal.y0 >= vertical.y0 - TABLE_SNAP_TOLERANCE
        && horizontal.y0 <= vertical.y1 + TABLE_SNAP_TOLERANCE
}

/// Snap nearby line coordinates to one stable grid coordinate.
fn clustered_coordinates(mut values: Vec<f64>) -> Vec<f64> {
    values.sort_by(f64::total_cmp);
    let mut clusters: Vec<(f64, usize)> = Vec::new();
    for value in values {
        if let Some((sum, count)) = clusters.last_mut()
            && (value - *sum / *count as f64).abs() <= TABLE_SNAP_TOLERANCE
        {
            *sum += value;
            *count += 1;
        } else {
            clusters.push((value, 1));
        }
    }
    clusters
        .into_iter()
        .map(|(sum, count)| sum / count as f64)
        .collect()
}

/// Return whether collinear rule fragments cover an entire cell edge.
fn rule_covers(rules: &[RuleSegment], horizontal: bool, fixed: f64, start: f64, end: f64) -> bool {
    let mut intervals: Vec<(f64, f64)> = rules
        .iter()
        .filter(|rule| rule.horizontal == horizontal)
        .filter_map(|rule| {
            let rule_fixed = if horizontal { rule.y0 } else { rule.x0 };
            if (rule_fixed - fixed).abs() > TABLE_SNAP_TOLERANCE {
                return None;
            }
            let (rule_start, rule_end) = if horizontal {
                (rule.x0, rule.x1)
            } else {
                (rule.y0, rule.y1)
            };
            (rule_end >= start - TABLE_SNAP_TOLERANCE && rule_start <= end + TABLE_SNAP_TOLERANCE)
                .then_some((rule_start, rule_end))
        })
        .collect();
    intervals.sort_by(|left, right| left.0.total_cmp(&right.0));

    let mut covered = start;
    for (interval_start, interval_end) in intervals {
        if interval_start > covered + TABLE_SNAP_TOLERANCE {
            break;
        }
        covered = covered.max(interval_end);
        if covered >= end - TABLE_SNAP_TOLERANCE {
            return true;
        }
    }
    false
}

/// Return whether a candidate cell has all four outer borders.
fn cell_is_bounded(rules: &[RuleSegment], (x0, y0, x1, y1): BBox) -> bool {
    rule_covers(rules, true, y0, x0, x1)
        && rule_covers(rules, true, y1, x0, x1)
        && rule_covers(rules, false, x0, y0, y1)
        && rule_covers(rules, false, x1, y0, y1)
}

/// Reject a spanning candidate if a complete internal rule splits it.
fn cell_has_internal_split(
    rules: &[RuleSegment],
    xs: &[f64],
    ys: &[f64],
    row_start: usize,
    row_end: usize,
    column_start: usize,
    column_end: usize,
) -> bool {
    let (x0, x1) = (xs[column_start], xs[column_end]);
    let (y0, y1) = (ys[row_start], ys[row_end]);
    xs[column_start + 1..column_end]
        .iter()
        .any(|&x| rule_covers(rules, false, x, y0, y1))
        || ys[row_start + 1..row_end]
            .iter()
            .any(|&y| rule_covers(rules, true, y, x0, x1))
}

/// Tile a detected grid with the smallest bounded cells, allowing rectangular
/// row/column spans where an internal rule is absent.
fn materialize_grid_cells(
    rules: &[RuleSegment],
    xs: &[f64],
    ys: &[f64],
    word_lines: &[Vec<WordTuple>],
) -> Option<Vec<Option<(BBox, String)>>> {
    let row_count = ys.len() - 1;
    let column_count = xs.len() - 1;
    let slot_count = row_count.checked_mul(column_count)?;
    if slot_count > MAX_TABLE_CELLS {
        return None;
    }

    let mut cells = vec![None; slot_count];
    let mut covered = vec![false; slot_count];
    let mut span_candidates = 0;
    for row_start in 0..row_count {
        for column_start in 0..column_count {
            let slot = row_start * column_count + column_start;
            if covered[slot] {
                continue;
            }

            let base_bbox = (
                xs[column_start],
                ys[row_start],
                xs[column_start + 1],
                ys[row_start + 1],
            );
            let mut best =
                cell_is_bounded(rules, base_bbox).then_some((1, row_start + 1, column_start + 1));
            if best.is_none() {
                for row_end in row_start + 1..=row_count {
                    for column_end in column_start + 1..=column_count {
                        span_candidates += 1;
                        if span_candidates > MAX_TABLE_SPAN_CANDIDATES {
                            return None;
                        }
                        let overlaps = (row_start..row_end).any(|row| {
                            (column_start..column_end)
                                .any(|column| covered[row * column_count + column])
                        });
                        if overlaps {
                            continue;
                        }
                        let bbox = (xs[column_start], ys[row_start], xs[column_end], ys[row_end]);
                        if !cell_is_bounded(rules, bbox)
                            || cell_has_internal_split(
                                rules,
                                xs,
                                ys,
                                row_start,
                                row_end,
                                column_start,
                                column_end,
                            )
                        {
                            continue;
                        }
                        let area = (row_end - row_start) * (column_end - column_start);
                        if best.is_none_or(|(best_area, _, _)| area < best_area) {
                            best = Some((area, row_end, column_end));
                        }
                    }
                }
            }

            let (_, row_end, column_end) = best?;
            let bbox = (xs[column_start], ys[row_start], xs[column_end], ys[row_end]);
            cells[slot] = Some((bbox, cell_text(word_lines, bbox)));
            for row in row_start..row_end {
                for column in column_start..column_end {
                    covered[row * column_count + column] = true;
                }
            }
        }
    }
    covered.into_iter().all(|slot| slot).then_some(cells)
}

/// Extract physical-order text whose word centers fall inside a cell.
fn cell_text(lines: &[Vec<WordTuple>], (x0, y0, x1, y1): BBox) -> String {
    let mut rows = Vec::new();
    for line in lines {
        let selected: Vec<&str> = line
            .iter()
            .filter_map(|((word_x0, word_y0, word_x1, word_y1), text)| {
                let center_x = (word_x0 + word_x1) * 0.5;
                let center_y = (word_y0 + word_y1) * 0.5;
                (center_x >= x0 - TABLE_SNAP_TOLERANCE
                    && center_x <= x1 + TABLE_SNAP_TOLERANCE
                    && center_y >= y0 - TABLE_SNAP_TOLERANCE
                    && center_y <= y1 + TABLE_SNAP_TOLERANCE)
                    .then_some(text.as_str())
            })
            .collect();
        if !selected.is_empty() {
            rows.push(selected.join(" "));
        }
    }
    rows.join("\n")
}

/// Return one cell's text from an already-separated line segment.
fn text_segment_value(segment: &[GlyphRecord]) -> String {
    split_words(segment)
        .into_iter()
        .map(|(_, text)| text)
        .collect::<Vec<_>>()
        .join(" ")
}

/// Bounding box around all segments in one inferred text-table row.
fn segmented_row_bbox(row: &[Vec<GlyphRecord>]) -> BBox {
    let mut x0 = f64::INFINITY;
    let mut y0 = f64::INFINITY;
    let mut x1 = f64::NEG_INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    for segment in row {
        let (segment_x0, segment_y0, segment_x1, segment_y1) = line_bbox(segment);
        x0 = x0.min(segment_x0);
        y0 = y0.min(segment_y0);
        x1 = x1.max(segment_x1);
        y1 = y1.max(segment_y1);
    }
    (x0, y0, x1, y1)
}

/// Representative font size for one inferred text-table row.
fn segmented_row_size(row: &[Vec<GlyphRecord>]) -> f64 {
    row.iter()
        .flat_map(|segment| segment.iter())
        .map(|glyph| glyph.size)
        .fold(0.0, f64::max)
}

/// Return whether two physical rows can belong to one borderless table.
fn text_rows_compatible(
    first: &[Vec<GlyphRecord>],
    previous: &[Vec<GlyphRecord>],
    current: &[Vec<GlyphRecord>],
) -> bool {
    if first.len() != current.len() || previous.len() != current.len() {
        return false;
    }
    let scale = segmented_row_size(previous)
        .max(segmented_row_size(current))
        .max(1.0);
    let (_, _, _, previous_y1) = segmented_row_bbox(previous);
    let (_, current_y0, _, _) = segmented_row_bbox(current);
    let row_gap = current_y0 - previous_y1;
    if row_gap < -scale * LINE_TOLERANCE || row_gap > scale * TEXT_TABLE_ROW_GAP {
        return false;
    }

    first.iter().zip(current).all(|(anchor, candidate)| {
        let (anchor_x0, _, anchor_x1, _) = line_bbox(anchor);
        let (candidate_x0, _, candidate_x1, _) = line_bbox(candidate);
        (anchor_x0 - candidate_x0).abs() <= scale * TEXT_TABLE_ALIGNMENT_TOLERANCE
            || (anchor_x1 - candidate_x1).abs() <= scale * TEXT_TABLE_ALIGNMENT_TOLERANCE
    })
}

/// Summarize the geometric evidence behind one borderless-text table.
///
/// Confidence is a deterministic ranking heuristic, not a calibrated
/// probability. The component metrics stay public so callers can apply their
/// own thresholds.
fn text_table_diagnostics(rows: &[Vec<Vec<GlyphRecord>>]) -> TableDiagnosticsTuple {
    let anchor = &rows[0];
    let mut alignment_error_em = 0.0_f64;
    for row in rows.iter().skip(1) {
        let scale = segmented_row_size(anchor)
            .max(segmented_row_size(row))
            .max(1.0);
        for (anchor_segment, candidate_segment) in anchor.iter().zip(row) {
            let (anchor_x0, _, anchor_x1, _) = line_bbox(anchor_segment);
            let (candidate_x0, _, candidate_x1, _) = line_bbox(candidate_segment);
            let error = (anchor_x0 - candidate_x0)
                .abs()
                .min((anchor_x1 - candidate_x1).abs())
                / scale;
            alignment_error_em = alignment_error_em.max(error);
        }
    }

    let minimum_gutter_em = rows
        .iter()
        .flat_map(|row| {
            let scale = segmented_row_size(row).max(1.0);
            row.windows(2).map(move |pair| {
                let (_, _, left_x1, _) = line_bbox(&pair[0]);
                let (right_x0, _, _, _) = line_bbox(&pair[1]);
                (right_x0 - left_x1) / scale
            })
        })
        .reduce(f64::min)
        .unwrap_or(0.0);

    let row_bboxes: Vec<BBox> = rows.iter().map(|row| segmented_row_bbox(row)).collect();
    let normalized_row_gaps: Vec<f64> = rows
        .windows(2)
        .zip(row_bboxes.windows(2))
        .map(|(row_pair, bbox_pair)| {
            let scale = segmented_row_size(&row_pair[0])
                .max(segmented_row_size(&row_pair[1]))
                .max(1.0);
            (bbox_pair[1].1 - bbox_pair[0].3) / scale
        })
        .collect();
    let minimum_row_gap = normalized_row_gaps
        .iter()
        .copied()
        .reduce(f64::min)
        .unwrap_or(0.0);
    let maximum_row_gap = normalized_row_gaps
        .iter()
        .copied()
        .reduce(f64::max)
        .unwrap_or(0.0);
    let row_gap_variation_em = maximum_row_gap - minimum_row_gap;

    let row_depth = (rows.len().saturating_sub(MIN_TEXT_TABLE_ROWS) as f64 + 1.0) / 3.0;
    let alignment_quality =
        (1.0 - alignment_error_em / TEXT_TABLE_ALIGNMENT_TOLERANCE).clamp(0.0, 1.0);
    let spacing_quality = (1.0 - row_gap_variation_em / TEXT_TABLE_ROW_GAP).clamp(0.0, 1.0);
    let gutter_quality = (minimum_gutter_em / (LINE_SEGMENT_GAP * 2.0)).clamp(0.0, 1.0);
    let confidence = (0.65
        + 0.10 * row_depth.clamp(0.0, 1.0)
        + 0.10 * alignment_quality
        + 0.10 * spacing_quality
        + 0.05 * gutter_quality)
        .clamp(0.0, 1.0);
    (
        confidence,
        Some(alignment_error_em),
        Some(minimum_gutter_em),
        Some(row_gap_variation_em),
    )
}

/// Materialize one run of aligned borderless rows.
fn text_table_from_rows(rows: &[Vec<Vec<GlyphRecord>>]) -> Option<TableTuple> {
    if rows.len() < MIN_TEXT_TABLE_ROWS {
        return None;
    }
    let row_count_usize = rows.len();
    let column_count_usize = rows[0].len();
    if !(2..=MAX_TEXT_TABLE_COLUMNS).contains(&column_count_usize) {
        return None;
    }

    let mut x_bounds = Vec::with_capacity(column_count_usize + 1);
    x_bounds.push(
        rows.iter()
            .map(|row| line_bbox(&row[0]).0)
            .reduce(f64::min)?,
    );
    for column in 1..column_count_usize {
        let left_end = rows
            .iter()
            .map(|row| line_bbox(&row[column - 1]).2)
            .reduce(f64::max)?;
        let right_start = rows
            .iter()
            .map(|row| line_bbox(&row[column]).0)
            .reduce(f64::min)?;
        if right_start <= left_end {
            return None;
        }
        x_bounds.push((left_end + right_start) * 0.5);
    }
    x_bounds.push(
        rows.iter()
            .map(|row| line_bbox(&row[column_count_usize - 1]).2)
            .reduce(f64::max)?,
    );

    let row_bboxes: Vec<BBox> = rows.iter().map(|row| segmented_row_bbox(row)).collect();
    let mut y_bounds = Vec::with_capacity(row_count_usize + 1);
    y_bounds.push(row_bboxes[0].1);
    for pair in row_bboxes.windows(2) {
        y_bounds.push((pair[0].3 + pair[1].1) * 0.5);
    }
    y_bounds.push(row_bboxes[row_count_usize - 1].3);

    let mut cells = Vec::with_capacity(row_count_usize * column_count_usize);
    for (row_index, row) in rows.iter().enumerate() {
        for (column_index, segment) in row.iter().enumerate() {
            cells.push(Some((
                (
                    x_bounds[column_index],
                    y_bounds[row_index],
                    x_bounds[column_index + 1],
                    y_bounds[row_index + 1],
                ),
                text_segment_value(segment),
            )));
        }
    }
    let row_count = u32::try_from(row_count_usize).ok()?;
    let column_count = u32::try_from(column_count_usize).ok()?;
    let diagnostics = text_table_diagnostics(rows);
    Some((
        (
            x_bounds[0],
            y_bounds[0],
            x_bounds[column_count_usize],
            y_bounds[row_count_usize],
        ),
        row_count,
        column_count,
        cells,
        diagnostics,
    ))
}

/// Detect opt-in borderless tables from sustained aligned text segments.
fn detect_text_tables(physical_lines: &[Vec<GlyphRecord>]) -> Vec<TableTuple> {
    let mut tables = Vec::new();
    let mut run: Vec<Vec<Vec<GlyphRecord>>> = Vec::new();
    let flush = |run: &mut Vec<Vec<Vec<GlyphRecord>>>, tables: &mut Vec<TableTuple>| {
        if let Some(table) = text_table_from_rows(run) {
            tables.push(table);
        }
        run.clear();
    };

    for line in physical_lines {
        let segment_count = line_segment_count(line);
        let segments = (!line.is_empty()
            && !has_vertical_baseline(&line[0])
            && (2..=MAX_TEXT_TABLE_COLUMNS).contains(&segment_count))
        .then(|| split_line_segments(line))
        .filter(|segments| segments.len() == segment_count);
        let Some(segments) = segments else {
            flush(&mut run, &mut tables);
            continue;
        };
        if run.is_empty()
            || text_rows_compatible(
                run.first().expect("the run was checked as non-empty"),
                run.last().expect("the run was checked as non-empty"),
                &segments,
            )
        {
            run.push(segments);
        } else {
            flush(&mut run, &mut tables);
            run.push(segments);
        }
    }
    flush(&mut run, &mut tables);
    tables
}

/// Detect high-confidence rectangular tables from connected vector-rule grids.
fn detect_grid_tables(
    physical_lines: &[Vec<GlyphRecord>],
    rules: &[RuleSegment],
) -> Vec<TableTuple> {
    let word_lines: Vec<Vec<WordTuple>> = physical_lines
        .iter()
        .map(|line| split_words(line))
        .collect();
    let horizontal_indices: Vec<usize> = rules
        .iter()
        .enumerate()
        .filter_map(|(index, rule)| rule.horizontal.then_some(index))
        .collect();
    let vertical_indices: Vec<usize> = rules
        .iter()
        .enumerate()
        .filter_map(|(index, rule)| (!rule.horizontal).then_some(index))
        .collect();
    let mut components = RuleComponents::new(rules.len());
    for &horizontal_index in &horizontal_indices {
        for &vertical_index in &vertical_indices {
            if rules_intersect(rules[horizontal_index], rules[vertical_index]) {
                components.union(horizontal_index, vertical_index);
            }
        }
    }

    let mut groups: BTreeMap<usize, Vec<RuleSegment>> = BTreeMap::new();
    for (index, &rule) in rules.iter().enumerate() {
        let root = components.find(index);
        groups.entry(root).or_default().push(rule);
    }

    let mut tables = Vec::new();
    for component_rules in groups.into_values() {
        let xs = clustered_coordinates(
            component_rules
                .iter()
                .filter_map(|rule| (!rule.horizontal).then_some(rule.x0))
                .collect(),
        );
        let ys = clustered_coordinates(
            component_rules
                .iter()
                .filter_map(|rule| rule.horizontal.then_some(rule.y0))
                .collect(),
        );
        if xs.len() < 3 || ys.len() < 3 {
            continue;
        }
        let row_count_usize = ys.len() - 1;
        let column_count_usize = xs.len() - 1;
        let Some(cells) = materialize_grid_cells(&component_rules, &xs, &ys, &word_lines) else {
            continue;
        };
        let Ok(row_count) = u32::try_from(row_count_usize) else {
            continue;
        };
        let Ok(column_count) = u32::try_from(column_count_usize) else {
            continue;
        };
        tables.push((
            (
                xs[0],
                ys[0],
                *xs.last().expect("three coordinates were checked above"),
                *ys.last().expect("three coordinates were checked above"),
            ),
            row_count,
            column_count,
            cells,
            (1.0, None, None, None),
        ));
    }
    tables.sort_by(|left, right| {
        left.0
            .1
            .total_cmp(&right.0.1)
            .then(left.0.0.total_cmp(&right.0.0))
    });
    tables
}

/// Representative baseline direction and PDF writing mode for a line.
fn line_direction(line: &[GlyphRecord]) -> ((f64, f64), u8) {
    line.first().map_or(((1.0, 0.0), 0), |glyph| {
        (glyph.direction, glyph.writing_mode)
    })
}

/// Assemble collected glyphs into blocks, lines, spans, and words.
fn assemble_layout(lines: &[Vec<GlyphRecord>]) -> Vec<BlockTuple> {
    let mut blocks: Vec<Vec<&[GlyphRecord]>> = Vec::new();
    let mut prev_baseline: Option<f64> = None;
    let mut prev_size = 0.0_f64;
    let mut prev_vertical = false;
    for line in lines {
        let baseline = line[0].y;
        let line_size = line.iter().map(|g| g.size).fold(0.0, f64::max);
        let vertical = has_vertical_baseline(&line[0]);
        let new_block = match prev_baseline {
            Some(prev) => {
                let scale = prev_size.max(line_size).max(1.0);
                vertical
                    || prev_vertical != vertical
                    || baseline - prev > scale * BLOCK_GAP
                    || prev - baseline > scale * LINE_TOLERANCE
            }
            None => true,
        };
        if new_block {
            blocks.push(Vec::new());
        }
        prev_baseline = Some(baseline);
        prev_size = line_size;
        prev_vertical = vertical;
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
        prev_end = Some(glyph_end(glyph));
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

/// Interpret a page once and optionally collect vector table rules.
fn collect_page_marks(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
    collect_rules: bool,
) -> (Vec<GlyphRecord>, Vec<RuleSegment>) {
    let cache = InterpreterCache::new();
    let mut context = extraction_context(pdf, page, &cache, settings);
    let mut collector = TextCollector {
        glyphs: Vec::new(),
        rules: Vec::new(),
        collect_rules,
        font_infos: HashMap::new(),
    };
    interpret_page(page, &mut context, &mut collector);
    (collector.glyphs, collector.rules)
}
