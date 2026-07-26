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
use std::io::{self, Write};
use std::sync::Arc;

use hayro::hayro_syntax::page::Page;
use hayro::hayro_syntax::{Filter, Pdf};
use kurbo::{Affine, Cap, Join, PathEl, PathSeg, Point, Rect, Shape};

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
    /// Glyph callback order before geometric clustering.
    source_order: usize,
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
    /// Cumulative UTF-8 bytes in accepted glyph Unicode.
    text_size: usize,
    /// Optional document-wide budget remaining for this interpretation.
    max_text_size: Option<usize>,
    /// Set once glyph Unicode would exceed the configured budget.
    text_limit_exceeded: bool,
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
        if self.text_limit_exceeded {
            return;
        }
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
        let Some(next_text_size) = self.text_size.checked_add(text.len()) else {
            self.text_limit_exceeded = true;
            return;
        };
        if self
            .max_text_size
            .is_some_and(|limit| next_text_size > limit)
        {
            self.text_limit_exceeded = true;
            return;
        }
        self.text_size = next_text_size;
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
        let source_order = self.glyphs.len();
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
            source_order,
        });
    }
}

/// Line threshold: baselines within this factor × font size share a line.
/// This absorbs super/subscripts while separating normal leading of 1.0 or more.
const LINE_TOLERANCE: f64 = 0.5;

/// Backward movement above this factor starts another source-order paint run.
const PAINT_RUN_RESET_TOLERANCE: f64 = 0.05;

/// Inline overlap above this factor keeps paint runs on separate logical lines.
const PAINT_LAYER_OVERLAP_TOLERANCE: f64 = 0.05;

/// Same-origin repeated glyphs below this factor still form separate runs.
const PAINT_RUN_SAME_ORIGIN_TOLERANCE: f64 = 0.0001;

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

/// Minimum aligned physical lines needed to refine one coarse grid interval.
const MIN_HYBRID_GRID_ROWS: usize = 3;

/// Require text in at least this fraction of cross-axis grid slots.
const HYBRID_GRID_OCCUPANCY_RATIO: f64 = 0.5;

/// Require adjacent inferred rows to occupy nearly the same grid slots.
const HYBRID_GRID_SLOT_SIMILARITY: f64 = 0.8;

/// Maximum leading variation relative to the largest candidate font.
const HYBRID_GRID_LEADING_VARIATION: f64 = 0.75;

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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AxisOrientation {
    Right,
    Down,
    Left,
    Up,
}

impl AxisOrientation {
    fn from_direction((x, y): (f64, f64)) -> Option<Self> {
        const NEAR_AXIS: f64 = 0.999;
        if x >= NEAR_AXIS {
            Some(Self::Right)
        } else if y >= NEAR_AXIS {
            Some(Self::Down)
        } else if x <= -NEAR_AXIS {
            Some(Self::Left)
        } else if y <= -NEAR_AXIS {
            Some(Self::Up)
        } else {
            None
        }
    }

    /// Map display coordinates into logical inline/block coordinates.
    fn logical_point(self, x: f64, y: f64) -> (f64, f64) {
        match self {
            Self::Right => (x, y),
            Self::Down => (y, -x),
            Self::Left => (-x, -y),
            Self::Up => (-y, x),
        }
    }
}

fn uniform_axis_orientation(lines: &[Vec<GlyphRecord>]) -> Option<AxisOrientation> {
    let first = AxisOrientation::from_direction(lines.first()?.first()?.direction)?;
    lines
        .iter()
        .all(|line| {
            line.first()
                .and_then(|glyph| AxisOrientation::from_direction(glyph.direction))
                == Some(first)
        })
        .then_some(first)
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
    lines
        .into_iter()
        .flat_map(split_overlapping_paint_layers)
        .collect()
}

struct PaintLayer {
    glyphs: Vec<GlyphRecord>,
    intervals: Vec<(f64, f64, f64)>,
}

fn sort_line_inline(line: &mut [GlyphRecord]) {
    line.sort_by(|left, right| {
        glyph_progress(left)
            .total_cmp(&glyph_progress(right))
            .then(left.source_order.cmp(&right.source_order))
    });
}

fn paint_run_interval(run: &[GlyphRecord]) -> (f64, f64, f64) {
    let (x0, y0, x1, y1) = glyphs_bbox(run);
    let vertical = run.first().is_some_and(has_vertical_baseline);
    let (start, end) = if vertical { (y0, y1) } else { (x0, x1) };
    let scale = run.iter().map(|glyph| glyph.size).fold(1.0, f64::max);
    (start, end, scale)
}

fn paint_intervals_overlap(
    (left_start, left_end, left_scale): (f64, f64, f64),
    (right_start, right_end, right_scale): (f64, f64, f64),
) -> bool {
    let overlap = left_end.min(right_end) - left_start.max(right_start);
    overlap > left_scale.max(right_scale) * PAINT_LAYER_OVERLAP_TOLERANCE
}

/// Preserve source-order text runs when geometry sorting would interleave them.
///
/// A PDF may paint complete strings more than once at the same baseline. The
/// old x/y sort grouped equal-position glyphs (`A1, B1, A2, B2`) and destroyed
/// both strings. Detect backward source-order resets, then greedily place
/// non-overlapping runs on one geometry-sorted line while retaining overlapping
/// runs as separate lines. This preserves distinct overprints instead of
/// deleting them.
fn split_overlapping_paint_layers(mut line: Vec<GlyphRecord>) -> Vec<Vec<GlyphRecord>> {
    if line.len() < 2 {
        sort_line_inline(&mut line);
        return vec![line];
    }
    line.sort_by_key(|glyph| glyph.source_order);

    let mut runs: Vec<Vec<GlyphRecord>> = Vec::new();
    let mut previous_progress: Option<f64> = None;
    let mut previous_direction: Option<(f64, f64)> = None;
    let mut previous_origin: Option<(f64, f64)> = None;
    let mut previous_text: Option<String> = None;
    for glyph in line {
        let progress = glyph_progress(&glyph);
        let scale = glyph.size.max(1.0);
        let direction_changed = previous_direction.is_some_and(|direction| {
            direction.0 * glyph.direction.0 + direction.1 * glyph.direction.1 < 0.9
        });
        let moved_backward = previous_progress
            .is_some_and(|previous| progress < previous - scale * PAINT_RUN_RESET_TOLERANCE);
        let repeated_at_same_origin =
            previous_origin
                .zip(previous_text.as_deref())
                .is_some_and(|((x, y), text)| {
                    (glyph.x - x).abs() <= scale * PAINT_RUN_SAME_ORIGIN_TOLERANCE
                        && (glyph.y - y).abs() <= scale * PAINT_RUN_SAME_ORIGIN_TOLERANCE
                        && glyph.text == text
                });
        if runs.is_empty() || moved_backward || repeated_at_same_origin || direction_changed {
            runs.push(Vec::new());
        }
        previous_progress = Some(progress);
        previous_direction = Some(glyph.direction);
        previous_origin = Some((glyph.x, glyph.y));
        previous_text = Some(glyph.text.clone());
        runs.last_mut()
            .expect("a paint run was created immediately before")
            .push(glyph);
    }

    if runs.len() == 1 {
        let mut line = runs.pop().expect("one paint run exists");
        sort_line_inline(&mut line);
        return vec![line];
    }

    let mut layers: Vec<PaintLayer> = Vec::new();
    for run in runs {
        let interval = paint_run_interval(&run);
        // Merge only into the latest layer. Searching older compatible layers
        // would move this run before an intervening overprint.
        let compatible = layers.last_mut().filter(|layer| {
            layer
                .intervals
                .iter()
                .all(|existing| !paint_intervals_overlap(*existing, interval))
        });
        if let Some(layer) = compatible {
            layer.glyphs.extend(run);
            layer.intervals.push(interval);
        } else {
            layers.push(PaintLayer {
                glyphs: run,
                intervals: vec![interval],
            });
        }
    }
    layers
        .into_iter()
        .map(|mut layer| {
            sort_line_inline(&mut layer.glyphs);
            layer.glyphs
        })
        .collect()
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
    let lines = lines
        .into_iter()
        .flat_map(split_overlapping_paint_layers)
        .collect::<Vec<_>>();
    vertical_lines.extend(cluster_explicit_vertical(explicit_vertical));
    let mut all_lines = lines;
    all_lines.extend(vertical_lines);
    all_lines
}

/// Split independently positioned columns or table cells sharing one baseline.
fn split_line_segments(line: &[GlyphRecord]) -> Vec<Vec<GlyphRecord>> {
    let mut segments: Vec<Vec<GlyphRecord>> = Vec::new();
    let mut previous_end: Option<f64> = None;
    let mut previous_size = 0.0_f64;
    for glyph in line {
        let threshold = previous_size.max(glyph.size).max(1.0) * LINE_SEGMENT_GAP;
        if previous_end.is_some_and(|end| glyph_progress(glyph) - end > threshold) {
            segments.push(Vec::new());
        }
        previous_end = Some(glyph_end(glyph));
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
        if previous_end.is_some_and(|end| glyph_progress(glyph) - end > threshold) {
            count += 1;
        }
        previous_end = Some(glyph_end(glyph));
        previous_size = glyph.size;
    }
    count
}

/// Split baseline bands only when a sustained page-level column gutter exists.
fn order_page_lines(clustered: Vec<Vec<GlyphRecord>>) -> Vec<Vec<GlyphRecord>> {
    if let Some(orientation) = uniform_axis_orientation(&clustered) {
        return order_axis_page_lines(clustered, orientation);
    }
    if clustered
        .iter()
        .any(|line| line.first().is_some_and(has_vertical_baseline))
    {
        return order_vertical_page_lines(clustered);
    }
    order_axis_page_lines(clustered, AxisOrientation::Right)
}

/// Order a uniformly axis-aligned page in its logical inline/block space.
fn order_axis_page_lines(
    mut clustered: Vec<Vec<GlyphRecord>>,
    orientation: AxisOrientation,
) -> Vec<Vec<GlyphRecord>> {
    clustered.sort_by(|left, right| {
        let left_bbox = logical_line_bbox(left, orientation);
        let right_bbox = logical_line_bbox(right, orientation);
        left_bbox
            .1
            .total_cmp(&right_bbox.1)
            .then(left_bbox.0.total_cmp(&right_bbox.0))
    });
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
    if column_boundary(&segments, orientation)
        .is_some_and(|boundary| valid_column_split(&segments, boundary, orientation))
    {
        order_columns(segments, orientation)
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

/// Return a line bbox in logical inline/block coordinates.
fn logical_line_bbox(line: &[GlyphRecord], orientation: AxisOrientation) -> BBox {
    let mut u0 = f64::INFINITY;
    let mut v0 = f64::INFINITY;
    let mut u1 = f64::NEG_INFINITY;
    let mut v1 = f64::NEG_INFINITY;
    for glyph in line {
        let (u, v) = orientation.logical_point(glyph.x, glyph.y);
        u0 = u0.min(u);
        u1 = u1.max(u + glyph.advance);
        v0 = v0.min(v - glyph.size * ASCENT);
        v1 = v1.max(v + glyph.size * DESCENT);
    }
    (u0, v0, u1, v1)
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
fn column_boundary(lines: &[Vec<GlyphRecord>], orientation: AxisOrientation) -> Option<f64> {
    if lines.len() < MIN_COLUMN_LINES * 2 {
        return None;
    }
    let bounds: Vec<BBox> = lines
        .iter()
        .map(|line| logical_line_bbox(line, orientation))
        .collect();
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
    orientation: AxisOrientation,
) -> Option<(f64, f64, usize)> {
    let mut y0 = f64::INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    let mut count = 0;
    for line in lines {
        let (line_x0, line_y0, line_x1, line_y1) = logical_line_bbox(line, orientation);
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
fn valid_column_split(
    lines: &[Vec<GlyphRecord>],
    boundary: f64,
    orientation: AxisOrientation,
) -> bool {
    let Some((left_y0, left_y1, left_count)) =
        side_vertical_extent(lines, boundary, true, orientation)
    else {
        return false;
    };
    let Some((right_y0, right_y1, right_count)) =
        side_vertical_extent(lines, boundary, false, orientation)
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
fn order_columns(
    lines: Vec<Vec<GlyphRecord>>,
    orientation: AxisOrientation,
) -> Vec<Vec<GlyphRecord>> {
    let Some(boundary) = column_boundary(&lines, orientation) else {
        return lines;
    };
    if !valid_column_split(&lines, boundary, orientation) {
        return lines;
    }

    let side_centers: Vec<f64> = lines
        .iter()
        .filter_map(|line| {
            let (x0, y0, x1, y1) = logical_line_bbox(line, orientation);
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
        let (x0, y0, x1, y1) = logical_line_bbox(line, orientation);
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
        let (x0, y0, x1, y1) = logical_line_bbox(&line, orientation);
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

    top.extend(order_columns(left, orientation));
    top.extend(order_columns(right, orientation));
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
    text_size: usize,
}

pub(crate) enum SearchError {
    TooManyHits,
}

impl TextPage {
    pub(crate) fn new(
        pdf: &Pdf,
        page: &Page<'_>,
        settings: InterpreterSettings,
        max_text_size: Option<usize>,
    ) -> Result<Self, usize> {
        let (width, height) = page.render_dimensions();
        let (glyphs, _, text_size) = collect_page_marks(pdf, page, settings, false, max_text_size)?;
        let physical_lines = cluster_lines(glyphs);
        let lines = order_page_lines(physical_lines);
        Ok(Self {
            width: f64::from(width),
            height: f64::from(height),
            lines,
            text_size,
        })
    }

    pub(crate) fn text(&self) -> String {
        assemble_text(&self.lines)
    }

    pub(crate) fn text_size(&self) -> usize {
        self.text_size
    }

    pub(crate) fn layout(&self) -> (f64, f64, Vec<BlockTuple>) {
        (self.width, self.height, assemble_layout(&self.lines))
    }

    pub(crate) fn search(
        &self,
        needle: &str,
        max_hits: Option<usize>,
    ) -> Result<Vec<BBox>, SearchError> {
        search_lines(&self.lines, needle, max_hits)
    }
}

/// Reusable table interpretation kept separate from normal text extraction.
pub(crate) struct TablePage {
    tables: Vec<TableTuple>,
    text_tables: Vec<TableTuple>,
    text_size: usize,
}

impl TablePage {
    pub(crate) fn new(
        pdf: &Pdf,
        page: &Page<'_>,
        settings: InterpreterSettings,
        max_text_size: Option<usize>,
    ) -> Result<Self, usize> {
        let (glyphs, rules, text_size) =
            collect_page_marks(pdf, page, settings, true, max_text_size)?;
        let physical_lines = cluster_lines(glyphs);
        Ok(Self {
            tables: detect_grid_tables(&physical_lines, &rules),
            text_tables: detect_text_tables(&physical_lines),
            text_size,
        })
    }

    pub(crate) fn text_size(&self) -> usize {
        self.text_size
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
    glyph.x * glyph.direction.0 + glyph.y * glyph.direction.1
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
/// One row-major table slot; merged continuations are absent.
type TableCell = Option<(BBox, String)>;
/// Materialized row-major cells plus an anchor index for every slot.
type MaterializedGrid = (Vec<TableCell>, Vec<u32>);
/// Line: `(bbox, spans, words, baseline direction, writing mode)`.
type LineTuple = (BBox, Vec<SpanTuple>, Vec<WordTuple>, (f64, f64), u8);
/// Block: `(bbox, lines)`.
pub(crate) type BlockTuple = (BBox, Vec<LineTuple>);
/// Table: `(bbox, row count, column count, row-major cells)`.
///
/// Continuation slots covered by a merged cell are `None`; the merged cell's
/// top-left slot contains its spanning bbox and text. The parallel anchor list
/// maps every slot to that top-left slot for lossless span expansion.
/// Diagnostics are `(confidence, alignment error in em, minimum gutter in em,
/// row-gap variation in em)`. Vector-grid metrics are `None`.
type TableDiagnosticsTuple = (f64, Option<f64>, Option<f64>, Option<f64>);
pub(crate) type TableTuple = (
    BBox,
    u32,
    u32,
    Vec<TableCell>,
    Vec<u32>,
    TableDiagnosticsTuple,
);

/// Return whether a candidate bbox is fully contained by a display-space clip.
fn bbox_is_inside((x0, y0, x1, y1): BBox, (clip_x0, clip_y0, clip_x1, clip_y1): BBox) -> bool {
    x0 >= clip_x0 - TABLE_SNAP_TOLERANCE
        && y0 >= clip_y0 - TABLE_SNAP_TOLERANCE
        && x1 <= clip_x1 + TABLE_SNAP_TOLERANCE
        && y1 <= clip_y1 + TABLE_SNAP_TOLERANCE
}

/// One glyph bbox with vertical extents approximated from baseline and size.
fn glyph_bbox(glyph: &GlyphRecord) -> BBox {
    if glyph.writing_mode == 1 {
        // Inferred mode-1 CJK retains upright glyph geometry; only its
        // conservative line ordering is vertical.
        return (
            glyph.x,
            glyph.y - glyph.size * ASCENT,
            glyph.x + glyph.advance,
            glyph.y + glyph.size * DESCENT,
        );
    }
    if glyph.direction.0 >= 1.0 - 1e-6 && glyph.direction.1.abs() <= 1e-6 {
        return (
            glyph.x,
            glyph.y - glyph.size * ASCENT,
            glyph.x + glyph.advance,
            glyph.y + glyph.size * DESCENT,
        );
    }
    let block = (-glyph.direction.1, glyph.direction.0);
    let mut x0 = f64::INFINITY;
    let mut y0 = f64::INFINITY;
    let mut x1 = f64::NEG_INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    for inline in [0.0, glyph.advance] {
        for cross in [-glyph.size * ASCENT, glyph.size * DESCENT] {
            let x = glyph.x + glyph.direction.0 * inline + block.0 * cross;
            let y = glyph.y + glyph.direction.1 * inline + block.1 * cross;
            x0 = x0.min(x);
            x1 = x1.max(x);
            y0 = y0.min(y);
            y1 = y1.max(y);
        }
    }
    (x0, y0, x1, y1)
}

/// Glyph bounding box with vertical extents approximated from baseline and size.
fn glyphs_bbox(glyphs: &[GlyphRecord]) -> BBox {
    let mut x0 = f64::INFINITY;
    let mut y0 = f64::INFINITY;
    let mut x1 = f64::NEG_INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    for glyph in glyphs {
        let (glyph_x0, glyph_y0, glyph_x1, glyph_y1) = glyph_bbox(glyph);
        x0 = x0.min(glyph_x0);
        y0 = y0.min(glyph_y0);
        x1 = x1.max(glyph_x1);
        y1 = y1.max(glyph_y1);
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
            let (glyph_x0, glyph_y0, glyph_x1, glyph_y1) = glyph_bbox(g);
            x0 = x0.min(glyph_x0);
            y0 = y0.min(glyph_y0);
            x1 = x1.max(glyph_x1);
            y1 = y1.max(glyph_y1);
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
) -> Option<MaterializedGrid> {
    let row_count = ys.len() - 1;
    let column_count = xs.len() - 1;
    let slot_count = row_count.checked_mul(column_count)?;
    if slot_count > MAX_TABLE_CELLS {
        return None;
    }

    let mut cells = vec![None; slot_count];
    let mut cell_anchors = vec![0; slot_count];
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
            let anchor = u32::try_from(slot).ok()?;
            for row in row_start..row_end {
                for column in column_start..column_end {
                    let covered_slot = row * column_count + column;
                    covered[covered_slot] = true;
                    cell_anchors[covered_slot] = anchor;
                }
            }
        }
    }
    covered
        .into_iter()
        .all(|slot| slot)
        .then_some((cells, cell_anchors))
}

/// Return the interval containing one coordinate within snapped grid bounds.
fn grid_interval(bounds: &[f64], coordinate: f64) -> Option<usize> {
    bounds.windows(2).position(|pair| {
        coordinate >= pair[0] - TABLE_SNAP_TOLERANCE && coordinate <= pair[1] + TABLE_SNAP_TOLERANCE
    })
}

/// Return the cross-axis grid slots occupied by one physical text line.
fn line_grid_slots(line: &[GlyphRecord], bounds: &[f64], horizontal: bool) -> Vec<bool> {
    let mut occupied = vec![false; bounds.len().saturating_sub(1)];
    for ((x0, y0, x1, y1), _) in split_words(line) {
        let coordinate = if horizontal {
            (x0 + x1) * 0.5
        } else {
            (y0 + y1) * 0.5
        };
        if let Some(index) = grid_interval(bounds, coordinate) {
            occupied[index] = true;
        }
    }
    occupied
}

/// Compare two slot signatures without rewarding jointly empty columns.
fn grid_slot_similarity(left: &[bool], right: &[bool]) -> f64 {
    let intersection = left
        .iter()
        .zip(right)
        .filter(|(left, right)| **left && **right)
        .count();
    let union = left
        .iter()
        .zip(right)
        .filter(|(left, right)| **left || **right)
        .count();
    if union == 0 {
        0.0
    } else {
        intersection as f64 / union as f64
    }
}

/// Infer missing row rules from three or more dense, evenly led text lines.
///
/// Some generators draw complete column borders but only one horizontal rule
/// every few data rows. Treating those gaps as row spans collapses several
/// records into one cell. Dense alignment across at least half the opposite
/// grid axis is strong enough to add synthetic dividers while rejecting
/// ordinary multiline headers and prose inside a wide merged cell.
fn infer_hybrid_grid_rules(
    physical_lines: &[Vec<GlyphRecord>],
    axis_bounds: &[f64],
    cross_bounds: &[f64],
    horizontal: bool,
) -> Vec<RuleSegment> {
    let cross_count = cross_bounds.len().saturating_sub(1);
    if axis_bounds.len() < 2 || cross_count < 2 {
        return Vec::new();
    }
    let minimum_occupancy =
        ((cross_count as f64 * HYBRID_GRID_OCCUPANCY_RATIO).ceil() as usize).max(2);
    let mut inferred = Vec::new();
    for interval in axis_bounds.windows(2) {
        let mut candidates: Vec<(f64, f64, Vec<bool>)> = physical_lines
            .iter()
            .filter_map(|line| {
                let glyph = line.first()?;
                let line_is_horizontal = glyph.direction.0.abs() >= glyph.direction.1.abs();
                let slots = line_grid_slots(line, cross_bounds, horizontal);
                if line_is_horizontal != horizontal
                    || slots.iter().filter(|occupied| **occupied).count() < minimum_occupancy
                {
                    return None;
                }
                let (x0, y0, x1, y1) = line_bbox(line);
                let (start, end) = if horizontal { (y0, y1) } else { (x0, x1) };
                let center = (start + end) * 0.5;
                (center >= interval[0] - TABLE_SNAP_TOLERANCE
                    && center <= interval[1] + TABLE_SNAP_TOLERANCE)
                    .then(|| {
                        let size = line.iter().map(|glyph| glyph.size).fold(0.0, f64::max);
                        (center, size, slots)
                    })
            })
            .collect();
        if candidates.len() < MIN_HYBRID_GRID_ROWS {
            continue;
        }
        candidates.sort_by(|left, right| left.0.total_cmp(&right.0));
        if !candidates
            .windows(2)
            .all(|pair| grid_slot_similarity(&pair[0].2, &pair[1].2) >= HYBRID_GRID_SLOT_SIMILARITY)
        {
            continue;
        }
        let gaps: Vec<f64> = candidates
            .windows(2)
            .map(|pair| pair[1].0 - pair[0].0)
            .collect();
        let minimum_gap = gaps.iter().copied().reduce(f64::min).unwrap_or(0.0);
        let maximum_gap = gaps.iter().copied().reduce(f64::max).unwrap_or(0.0);
        let maximum_size = candidates
            .iter()
            .map(|candidate| candidate.1)
            .reduce(f64::max)
            .unwrap_or(1.0)
            .max(1.0);
        if minimum_gap <= 0.0
            || maximum_gap > maximum_size * TEXT_TABLE_ROW_GAP
            || maximum_gap - minimum_gap > maximum_size * HYBRID_GRID_LEADING_VARIATION
        {
            continue;
        }
        for pair in candidates.windows(2) {
            let coordinate = (pair[0].0 + pair[1].0) * 0.5;
            inferred.push(if horizontal {
                RuleSegment {
                    x0: cross_bounds[0],
                    y0: coordinate,
                    x1: *cross_bounds
                        .last()
                        .expect("two cross-axis bounds were checked above"),
                    y1: coordinate,
                    horizontal: true,
                }
            } else {
                RuleSegment {
                    x0: coordinate,
                    y0: cross_bounds[0],
                    x1: coordinate,
                    y1: *cross_bounds
                        .last()
                        .expect("two cross-axis bounds were checked above"),
                    horizontal: false,
                }
            });
        }
    }
    inferred
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
    let slot_count = row_count_usize.checked_mul(column_count_usize)?;
    if slot_count > MAX_TABLE_CELLS {
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
        (0..u32::try_from(slot_count).ok()?).collect(),
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
    for mut component_rules in groups.into_values() {
        let original_xs = clustered_coordinates(
            component_rules
                .iter()
                .filter_map(|rule| (!rule.horizontal).then_some(rule.x0))
                .collect(),
        );
        let original_ys = clustered_coordinates(
            component_rules
                .iter()
                .filter_map(|rule| rule.horizontal.then_some(rule.y0))
                .collect(),
        );
        if original_xs.len() < 3 || original_ys.len() < 3 {
            continue;
        }
        let mut inferred_rules =
            infer_hybrid_grid_rules(physical_lines, &original_ys, &original_xs, true);
        inferred_rules.extend(infer_hybrid_grid_rules(
            physical_lines,
            &original_xs,
            &original_ys,
            false,
        ));
        let is_hybrid = !inferred_rules.is_empty();
        component_rules.extend(inferred_rules);
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
        let row_count_usize = ys.len() - 1;
        let column_count_usize = xs.len() - 1;
        let Some((cells, cell_anchors)) =
            materialize_grid_cells(&component_rules, &xs, &ys, &word_lines)
        else {
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
            cell_anchors,
            (if is_hybrid { 0.95 } else { 1.0 }, None, None, None),
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
fn search_lines(
    lines: &[Vec<GlyphRecord>],
    needle: &str,
    max_hits: Option<usize>,
) -> Result<Vec<BBox>, SearchError> {
    let needle_lower: String = needle.chars().flat_map(char::to_lowercase).collect();
    if needle_lower.is_empty() {
        return Ok(Vec::new());
    }
    let mut hits = Vec::new();
    for line in lines {
        let (haystack, map) = line_search_index(line);
        let mut byte_cursor = 0;
        let mut char_cursor = 0;
        for (start, _) in haystack.match_indices(&needle_lower) {
            let end = start + needle_lower.len();
            // Byte position → character position → glyph set.
            let char_start = char_cursor + haystack[byte_cursor..start].chars().count();
            let char_len = haystack[start..end].chars().count();
            let glyph_map = &map[char_start..char_start + char_len];
            let first = glyph_map.iter().find_map(|value| *value);
            let last = glyph_map.iter().rev().find_map(|value| *value);
            if let (Some(first), Some(last)) = (first, last) {
                if max_hits.is_some_and(|limit| hits.len() == limit) {
                    return Err(SearchError::TooManyHits);
                }
                let matched = &line[first..=last];
                hits.push(glyphs_bbox(matched));
            }
            byte_cursor = end;
            char_cursor = char_start + char_len;
        }
    }
    Ok(hits)
}

/// Extracted image: `(width, height, page bbox, "jpeg"/"png", bytes)`.
pub(crate) type ImageTuple = (u32, u32, BBox, String, Vec<u8>);

/// Keep one page from multiplying repeated image placements into unbounded output.
const MAX_EXTRACTED_IMAGE_PLACEMENTS: usize = 4_096;
const MAX_EXTRACTED_IMAGE_PIXELS: u64 = 64_000_000;
const MAX_EXTRACTED_IMAGE_BYTES: usize = 64 * 1024 * 1024;
const IMAGE_PLACEMENT_LIMIT_ERROR: &str =
    "image extraction exceeds the 4096-placement safety limit";
const IMAGE_PIXEL_LIMIT_ERROR: &str = "image extraction exceeds the 64000000-pixel safety limit";
const IMAGE_BYTE_LIMIT_ERROR: &str =
    "image extraction exceeds the 67108864-byte output safety limit";

/// Maximum unique indirect raster images considered by one compression call.
const MAX_IMAGE_USAGE_OBJECTS: usize = 16_384;

/// One indirect raster image and the lowest effective DPI of all placements.
///
/// The lowest DPI represents the largest, most pixel-demanding placement.
#[derive(Clone, Copy, Debug)]
pub(crate) struct ImageUsage {
    pub object_id: (u32, u16),
    pub width: u32,
    pub height: u32,
    pub min_dpi_x: Option<f64>,
    pub min_dpi_y: Option<f64>,
}

/// A Device that aggregates raster placements by indirect object ID.
struct ImageUsageCollector {
    usages: HashMap<(u32, u16), ImageUsage>,
    error: Option<&'static str>,
}

fn effective_dpi(transform: Affine) -> (Option<f64>, Option<f64>) {
    let [a, b, c, d, _, _] = transform.as_coeffs();
    let dpi = |scale: f64| {
        (scale.is_finite() && scale > 0.0)
            .then_some(72.0 / scale)
            .filter(|value| value.is_finite())
    };
    (dpi(a.hypot(b)), dpi(c.hypot(d)))
}

impl ImageUsageCollector {
    fn collect(&mut self, image: Image<'_, '_>, transform: Affine) {
        let Image::Raster(raster) = image else {
            return;
        };
        let id = raster.stream().obj_id();
        let (Ok(number), Ok(generation)) =
            (u32::try_from(id.obj_number), u16::try_from(id.gen_number))
        else {
            return;
        };
        if number == 0 {
            // Inline images do not have a mutable lopdf object.
            return;
        }
        let (dpi_x, dpi_y) = effective_dpi(transform);
        if let Some(usage) = self.usages.get_mut(&(number, generation)) {
            if usage.width != raster.width() || usage.height != raster.height() {
                usage.min_dpi_x = None;
                usage.min_dpi_y = None;
                return;
            }
            if let Some(value) = dpi_x {
                usage.min_dpi_x = Some(
                    usage
                        .min_dpi_x
                        .map_or(value, |previous| previous.min(value)),
                );
            }
            if let Some(value) = dpi_y {
                usage.min_dpi_y = Some(
                    usage
                        .min_dpi_y
                        .map_or(value, |previous| previous.min(value)),
                );
            }
            return;
        }
        if self.usages.len() >= MAX_IMAGE_USAGE_OBJECTS {
            self.error = Some("image compression exceeds the 16384-object safety limit");
            return;
        }
        self.usages.insert(
            (number, generation),
            ImageUsage {
                object_id: (number, generation),
                width: raster.width(),
                height: raster.height(),
                min_dpi_x: dpi_x,
                min_dpi_y: dpi_y,
            },
        );
    }
}

impl Device<'_> for ImageUsageCollector {
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
        if self.error.is_none() {
            self.collect(image, transform);
        }
    }
}

/// Aggregate indirect raster placements across all pages.
pub(crate) fn collect_image_usages(
    pdf: &Pdf,
    settings: InterpreterSettings,
) -> Result<Vec<ImageUsage>, &'static str> {
    let cache = InterpreterCache::new();
    let mut collector = ImageUsageCollector {
        usages: HashMap::new(),
        error: None,
    };
    for page in pdf.pages().iter() {
        let mut context = extraction_context(pdf, page, &cache, settings.clone());
        interpret_page(page, &mut context, &mut collector);
        if let Some(error) = collector.error {
            return Err(error);
        }
    }
    let mut usages = collector.usages.into_values().collect::<Vec<_>>();
    usages.sort_unstable_by_key(|usage| usage.object_id);
    Ok(usages)
}

/// A Device that only collects images.
struct ImageCollector {
    images: Vec<ImageTuple>,
    placements: usize,
    pixels: u64,
    bytes: usize,
    error: Option<&'static str>,
}

/// JPEG magic number (SOI marker).
const JPEG_MAGIC: [u8; 3] = [0xFF, 0xD8, 0xFF];

/// Return original JPEG bytes when they can be extracted unchanged.
///
/// Supports `/Filter` of `[DCTDecode]` or `[FlateDecode, DCTDecode]`.
/// Verify the decoded prefix is JPEG magic; otherwise return None so the caller
/// can fall back to decoding and PNG encoding.
fn try_jpeg_passthrough(
    stream: &hayro::hayro_syntax::object::Stream<'_>,
    max_bytes: usize,
) -> Result<Option<Vec<u8>>, &'static str> {
    use std::io::Read;
    let filters = stream.filters();
    let data = match filters.as_slice() {
        [Filter::DctDecode] => {
            let data = stream.raw_data();
            if !data.starts_with(&JPEG_MAGIC) {
                return Ok(None);
            }
            if data.len() > max_bytes {
                return Err(IMAGE_BYTE_LIMIT_ERROR);
            }
            data.into_owned()
        }
        [Filter::FlateDecode, Filter::DctDecode] => {
            let mut out = Vec::new();
            let result = flate2::read::ZlibDecoder::new(stream.raw_data().as_ref())
                .take(
                    u64::try_from(max_bytes)
                        .unwrap_or(u64::MAX)
                        .saturating_add(1),
                )
                .read_to_end(&mut out);
            if out.len() > max_bytes {
                return Err(IMAGE_BYTE_LIMIT_ERROR);
            }
            if result.is_err() {
                // Preserve the existing decode-to-PNG fallback.
                return Ok(None);
            }
            out
        }
        _ => return Ok(None),
    };
    Ok(data.starts_with(&JPEG_MAGIC).then_some(data))
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

/// Write pixel data as PNG.
///
/// Rendering uses Fast/fdeflate: Balanced is tens of times slower for about 10%
/// smaller output and makes PNG the dominant render cost in benchmarks.
/// `get_images` keeps Balanced because extracted images are stored artifacts.
pub(crate) fn write_png<W: Write>(
    output: W,
    width: u32,
    height: u32,
    color: png::ColorType,
    data: &[u8],
    compression: png::Compression,
) -> Result<(), png::EncodingError> {
    let mut encoder = png::Encoder::new(output, width, height);
    encoder.set_color(color);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_compression(compression);
    let mut writer = encoder.write_header()?;
    writer.write_image_data(data)?;
    writer.finish()
}

pub(crate) enum PngEncodeError {
    Encoding(png::EncodingError),
    OutputLimit,
}

struct BoundedPngOutput {
    bytes: Vec<u8>,
    max_size: Option<usize>,
    exceeded: bool,
}

impl BoundedPngOutput {
    fn new(max_size: Option<usize>) -> Self {
        Self {
            bytes: Vec::new(),
            max_size,
            exceeded: false,
        }
    }
}

impl Write for BoundedPngOutput {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.write_all(buffer)?;
        Ok(buffer.len())
    }

    fn write_all(&mut self, buffer: &[u8]) -> io::Result<()> {
        if self.max_size.is_some_and(|limit| {
            self.bytes
                .len()
                .checked_add(buffer.len())
                .is_none_or(|size| size > limit)
        }) {
            self.exceeded = true;
            return Err(io::Error::other("PNG output size limit exceeded"));
        }
        self.bytes.extend_from_slice(buffer);
        Ok(())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Encode pixel data as PNG without retaining output beyond `max_size`.
pub(crate) fn encode_png_bounded(
    width: u32,
    height: u32,
    color: png::ColorType,
    data: &[u8],
    compression: png::Compression,
    max_size: Option<usize>,
) -> Result<Vec<u8>, PngEncodeError> {
    let mut output = BoundedPngOutput::new(max_size);
    let result = write_png(&mut output, width, height, color, data, compression);
    if output.exceeded {
        return Err(PngEncodeError::OutputLimit);
    }
    result.map_err(PngEncodeError::Encoding)?;
    Ok(output.bytes)
}

/// Encode pixel data as PNG.
pub(crate) fn encode_png(
    width: u32,
    height: u32,
    color: png::ColorType,
    data: &[u8],
    compression: png::Compression,
) -> Option<Vec<u8>> {
    encode_png_bounded(width, height, color, data, compression, None).ok()
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

impl ImageCollector {
    fn admit(&mut self, width: u32, height: u32) -> bool {
        if self.placements >= MAX_EXTRACTED_IMAGE_PLACEMENTS {
            self.error = Some(IMAGE_PLACEMENT_LIMIT_ERROR);
            return false;
        }
        self.placements += 1;
        let pixels = u64::from(width) * u64::from(height);
        let Some(total) = self.pixels.checked_add(pixels) else {
            self.error = Some(IMAGE_PIXEL_LIMIT_ERROR);
            return false;
        };
        if total > MAX_EXTRACTED_IMAGE_PIXELS {
            self.error = Some(IMAGE_PIXEL_LIMIT_ERROR);
            return false;
        }
        self.pixels = total;
        true
    }

    fn push(&mut self, image: ImageTuple) {
        let Some(total) = self.bytes.checked_add(image.4.len()) else {
            self.error = Some(IMAGE_BYTE_LIMIT_ERROR);
            return;
        };
        if total > MAX_EXTRACTED_IMAGE_BYTES {
            self.error = Some(IMAGE_BYTE_LIMIT_ERROR);
            return;
        }
        self.bytes = total;
        self.images.push(image);
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
        if self.error.is_some() || !self.admit(image.width(), image.height()) {
            return;
        }
        let bbox = image_bbox(
            transform,
            f64::from(image.width()),
            f64::from(image.height()),
        );
        match image {
            Image::Raster(raster) => {
                // Extract images ending in DCTDecode as raw JPEG without recompression.
                match try_jpeg_passthrough(
                    raster.stream(),
                    MAX_EXTRACTED_IMAGE_BYTES.saturating_sub(self.bytes),
                ) {
                    Ok(Some(jpeg)) => {
                        self.push((
                            raster.width(),
                            raster.height(),
                            bbox,
                            "jpeg".to_owned(),
                            jpeg,
                        ));
                        return;
                    }
                    Ok(None) => {}
                    Err(error) => {
                        self.error = Some(error);
                        return;
                    }
                }
                raster.with_rgba(
                    |image_data, alpha| {
                        if let Some((width, height, data)) = encode_raster_png(image_data, alpha) {
                            self.push((width, height, bbox, "png".to_owned(), data));
                        }
                    },
                    None,
                );
            }
            Image::Stencil(stencil) => {
                stencil.with_stencil(
                    |luma, _| {
                        if let Some(data) = encode_png(
                            luma.width,
                            luma.height,
                            png::ColorType::Grayscale,
                            &luma.data,
                            png::Compression::Balanced,
                        ) {
                            self.push((luma.width, luma.height, bbox, "png".to_owned(), data));
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
) -> Result<Vec<ImageTuple>, &'static str> {
    let cache = InterpreterCache::new();
    let mut context = extraction_context(pdf, page, &cache, settings);
    let mut collector = ImageCollector {
        images: Vec::new(),
        placements: 0,
        pixels: 0,
        bytes: 0,
        error: None,
    };
    interpret_page(page, &mut context, &mut collector);
    match collector.error {
        Some(error) => Err(error),
        None => Ok(collector.images),
    }
}

/// One vector command: `"l"` with two points or `"c"` with four points.
pub(crate) type DrawingItemTuple = (String, Vec<(f64, f64)>);

type DrawingColor = (f64, f64, f64);
type DrawingStrokeTuple = (
    Option<DrawingColor>,
    Option<f64>,
    Option<f64>,
    Option<(i64, i64, i64)>,
    Option<i64>,
    Option<String>,
);
type DrawingFillTuple = (Option<DrawingColor>, Option<f64>, Option<bool>);

/// One pymupdf-style drawing path.
///
/// Fields are bbox, type, commands, close flag, stroke/fill RGB and opacity,
/// fill rule, stroke width/cap/join, and PDF dash syntax.
pub(crate) type DrawingTuple = (
    BBox,
    String,
    Vec<DrawingItemTuple>,
    bool,
    DrawingStrokeTuple,
    DrawingFillTuple,
);

/// Bound materialized output for adversarial pages.
const MAX_DRAWING_PATHS: usize = 8192;
const MAX_DRAWING_COMMANDS: usize = 131_072;

#[derive(PartialEq)]
struct DrawingGeometry {
    bbox: BBox,
    items: Vec<DrawingItemTuple>,
    close_path: bool,
}

struct DrawingRecord {
    geometry: DrawingGeometry,
    kind: &'static str,
    stroke_color: Option<(f64, f64, f64)>,
    fill_color: Option<(f64, f64, f64)>,
    stroke_opacity: Option<f64>,
    fill_opacity: Option<f64>,
    even_odd: Option<bool>,
    width: Option<f64>,
    line_cap: Option<(i64, i64, i64)>,
    line_join: Option<i64>,
    dashes: Option<String>,
}

impl DrawingRecord {
    fn into_tuple(self) -> DrawingTuple {
        (
            self.geometry.bbox,
            self.kind.to_owned(),
            self.geometry.items,
            self.geometry.close_path,
            (
                self.stroke_color,
                self.stroke_opacity,
                self.width,
                self.line_cap,
                self.line_join,
                self.dashes,
            ),
            (self.fill_color, self.fill_opacity, self.even_odd),
        )
    }
}

struct DrawingCollector {
    drawings: Vec<DrawingRecord>,
    command_count: usize,
    error: Option<&'static str>,
}

fn drawing_paint(paint: &Paint<'_>) -> (Option<(f64, f64, f64)>, Option<f64>) {
    let Paint::Color(color) = paint else {
        return (None, None);
    };
    let [red, green, blue, alpha] = color.to_rgba().components();
    (
        Some((f64::from(red), f64::from(green), f64::from(blue))),
        Some(f64::from(alpha)),
    )
}

fn drawing_scale(transform: Affine) -> f64 {
    let [a, b, c, d, _, _] = transform.as_coeffs();
    (a.hypot(b)).max(c.hypot(d))
}

fn drawing_cap(cap: Cap) -> i64 {
    match cap {
        Cap::Butt => 0,
        Cap::Round => 1,
        Cap::Square => 2,
    }
}

fn drawing_join(join: Join) -> i64 {
    match join {
        Join::Miter => 0,
        Join::Round => 1,
        Join::Bevel => 2,
    }
}

fn drawing_dashes(values: &[f32], offset: f32, scale: f64) -> String {
    let values = values
        .iter()
        .map(|value| (f64::from(*value) * scale).to_string())
        .collect::<Vec<_>>()
        .join(" ");
    format!("[{values}] {}", f64::from(offset) * scale)
}

fn drawing_geometry(
    path: &kurbo::BezPath,
    transform: Affine,
) -> Result<Option<DrawingGeometry>, &'static str> {
    let mut transformed = path.clone();
    transformed.apply_affine(transform);
    let bbox = transformed.bounding_box();
    let bbox = (bbox.x0, bbox.y0, bbox.x1, bbox.y1);
    if ![bbox.0, bbox.1, bbox.2, bbox.3]
        .into_iter()
        .all(f64::is_finite)
    {
        return Err("drawing path contains non-finite coordinates");
    }

    let mut items = Vec::new();
    let mut current = None;
    let mut subpath_start = None;
    let mut close_path = false;
    let mut push_item = |kind: &str, points: Vec<(f64, f64)>| -> Result<(), &'static str> {
        if items.len() >= MAX_DRAWING_COMMANDS {
            return Err("drawing extraction exceeds the 131072-command safety limit");
        }
        if !points
            .iter()
            .flat_map(|point| [point.0, point.1])
            .all(f64::is_finite)
        {
            return Err("drawing path contains non-finite coordinates");
        }
        items.push((kind.to_owned(), points));
        Ok(())
    };

    for element in transformed.elements() {
        match *element {
            PathEl::MoveTo(point) => {
                current = Some(point);
                subpath_start = Some(point);
            }
            PathEl::LineTo(point) => {
                if let Some(start) = current {
                    push_item("l", vec![(start.x, start.y), (point.x, point.y)])?;
                }
                current = Some(point);
            }
            PathEl::QuadTo(control, point) => {
                if let Some(start) = current {
                    let control1 = start + (control - start) * (2.0 / 3.0);
                    let control2 = point + (control - point) * (2.0 / 3.0);
                    push_item(
                        "c",
                        vec![
                            (start.x, start.y),
                            (control1.x, control1.y),
                            (control2.x, control2.y),
                            (point.x, point.y),
                        ],
                    )?;
                }
                current = Some(point);
            }
            PathEl::CurveTo(control1, control2, point) => {
                if let Some(start) = current {
                    push_item(
                        "c",
                        vec![
                            (start.x, start.y),
                            (control1.x, control1.y),
                            (control2.x, control2.y),
                            (point.x, point.y),
                        ],
                    )?;
                }
                current = Some(point);
            }
            PathEl::ClosePath => {
                if let (Some(start), Some(end)) = (subpath_start, current)
                    && start != end
                {
                    push_item("l", vec![(end.x, end.y), (start.x, start.y)])?;
                }
                current = subpath_start;
                close_path = true;
            }
        }
    }

    if items.is_empty() {
        return Ok(None);
    }
    Ok(Some(DrawingGeometry {
        bbox,
        items,
        close_path,
    }))
}

impl DrawingCollector {
    fn collect(
        &mut self,
        path: &kurbo::BezPath,
        transform: Affine,
        paint: &Paint<'_>,
        mode: &PathDrawMode,
    ) {
        if self.error.is_some() {
            return;
        }
        let geometry = match drawing_geometry(path, transform) {
            Ok(Some(geometry)) => geometry,
            Ok(None) => return,
            Err(error) => {
                self.error = Some(error);
                return;
            }
        };
        let (color, opacity) = drawing_paint(paint);
        let scale = drawing_scale(transform);
        let mut record = match mode {
            PathDrawMode::Fill(rule) => DrawingRecord {
                geometry,
                kind: "f",
                stroke_color: None,
                fill_color: color,
                stroke_opacity: None,
                fill_opacity: opacity,
                even_odd: Some(matches!(rule, hayro::hayro_interpret::FillRule::EvenOdd)),
                width: None,
                line_cap: None,
                line_join: None,
                dashes: None,
            },
            PathDrawMode::Stroke(stroke) => {
                let cap = drawing_cap(stroke.line_cap);
                DrawingRecord {
                    geometry,
                    kind: "s",
                    stroke_color: color,
                    fill_color: None,
                    stroke_opacity: opacity,
                    fill_opacity: None,
                    even_odd: None,
                    width: Some(f64::from(stroke.line_width) * scale),
                    line_cap: Some((cap, cap, cap)),
                    line_join: Some(drawing_join(stroke.line_join)),
                    dashes: Some(drawing_dashes(
                        &stroke.dash_array,
                        stroke.dash_offset,
                        scale,
                    )),
                }
            }
        };

        if record.kind == "s"
            && let Some(previous) = self.drawings.last_mut()
            && previous.kind == "f"
            && previous.geometry == record.geometry
        {
            previous.kind = "fs";
            previous.stroke_color = record.stroke_color.take();
            previous.stroke_opacity = record.stroke_opacity;
            previous.width = record.width;
            previous.line_cap = record.line_cap;
            previous.line_join = record.line_join;
            previous.dashes = record.dashes.take();
            return;
        }

        if self.drawings.len() >= MAX_DRAWING_PATHS {
            self.error = Some("drawing extraction exceeds the 8192-path safety limit");
            return;
        }
        let Some(command_count) = self.command_count.checked_add(record.geometry.items.len())
        else {
            self.error = Some("drawing extraction command count overflowed");
            return;
        };
        if command_count > MAX_DRAWING_COMMANDS {
            self.error = Some("drawing extraction exceeds the 131072-command safety limit");
            return;
        }
        self.command_count = command_count;
        self.drawings.push(record);
    }
}

impl Device<'_> for DrawingCollector {
    fn set_soft_mask(&mut self, _: Option<SoftMask<'_>>) {}
    fn set_blend_mode(&mut self, _: BlendMode) {}
    fn draw_path(
        &mut self,
        path: &kurbo::BezPath,
        transform: Affine,
        paint: &Paint<'_>,
        mode: &PathDrawMode,
    ) {
        self.collect(path, transform, paint, mode);
    }
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
    fn draw_image(&mut self, _: Image<'_, '_>, _: Affine) {}
    fn pop_clip_path(&mut self) {}
    fn pop_transparency_group(&mut self) {}
}

/// Extract interpreted vector paint operations in display coordinates.
pub(crate) fn extract_page_drawings(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
) -> Result<Vec<DrawingTuple>, &'static str> {
    let cache = InterpreterCache::new();
    let mut context = extraction_context(pdf, page, &cache, settings);
    let mut collector = DrawingCollector {
        drawings: Vec::new(),
        command_count: 0,
        error: None,
    };
    interpret_page(page, &mut context, &mut collector);
    if let Some(error) = collector.error {
        return Err(error);
    }
    Ok(collector
        .drawings
        .into_iter()
        .map(DrawingRecord::into_tuple)
        .collect())
}

/// Interpret a page once and optionally collect vector table rules.
fn collect_page_marks(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
    collect_rules: bool,
    max_text_size: Option<usize>,
) -> Result<(Vec<GlyphRecord>, Vec<RuleSegment>, usize), usize> {
    let cache = InterpreterCache::new();
    let mut context = extraction_context(pdf, page, &cache, settings);
    let mut collector = TextCollector {
        glyphs: Vec::new(),
        rules: Vec::new(),
        collect_rules,
        font_infos: HashMap::new(),
        text_size: 0,
        max_text_size,
        text_limit_exceeded: false,
    };
    interpret_page(page, &mut context, &mut collector);
    if collector.text_limit_exceeded {
        return Err(max_text_size.unwrap_or(usize::MAX));
    }
    Ok((collector.glyphs, collector.rules, collector.text_size))
}
