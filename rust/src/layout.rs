//! Bounded paragraph layout shared by Standard 14 and embedded-font text.

use unicode_linebreak::linebreaks;
use unicode_segmentation::UnicodeSegmentation;

const FIT_TOLERANCE: f64 = 1.0e-6;
const DIRECT_BREAK_MEASURE_BYTES: usize = 64;
pub const MAX_GENERATED_TEXT_LINES: usize = 4_096;
pub const TEXT_LINE_LIMIT_ERROR: &str = "text layout exceeds the configured logical-line limit";

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
    /// The height that the lines need, which the placement defines.
    pub height: f64,
    pub spare_height: f64,
    pub ascent: f64,
    pub leading: f64,
}

impl TextBoxLayout {
    pub fn fits(&self) -> bool {
        self.spare_height >= -FIT_TOLERANCE
    }
}

/// The ascender and descender that `Font.ascender` and `Font.descender`
/// report, as fractions of the em: the ascender is positive and the
/// descender below the baseline negative. Canvas text and textboxes place
/// lines with them.
#[derive(Clone, Copy, Debug)]
pub struct LineMetrics {
    pub ascender: f64,
    pub descender: f64,
}

impl LineMetrics {
    /// Return the distance between baselines of canvas text, in em:
    /// `lineheight` when given, else the ascender minus the descender, or 1.2
    /// when that is at most 1, as pymupdf's `insert_text` spaces lines.
    /// pymupdf compares the single-precision metrics that MuPDF reports, in
    /// which 0.8 and -0.2 span 1.000000015 em, so such a face keeps its
    /// extent of 1 em.
    pub fn line_height(self, lineheight: Option<f64>) -> f64 {
        lineheight.unwrap_or_else(|| {
            let single = self.single_precision();
            if single.ascender - single.descender <= 1.0 {
                1.2
            } else {
                self.ascender - self.descender
            }
        })
    }

    /// The metrics rounded to single precision, as MuPDF reports them.
    fn single_precision(self) -> Self {
        Self {
            ascender: f64::from(self.ascender as f32),
            descender: f64::from(self.descender as f32),
        }
    }

    /// Return the distance between baselines of canvas text in points,
    /// which content operators and krilla can only place when it is finite.
    pub fn leading(self, font_size: f64, lineheight: Option<f64>) -> Result<f64, String> {
        let leading = font_size * self.line_height(lineheight);
        if leading.is_finite() {
            Ok(leading)
        } else {
            Err("lineheight times fontsize must be a finite number".to_owned())
        }
    }
}

/// How a textbox places its lines vertically and decides whether they fit:
/// the first baseline `ascent` em below the box top, later ones
/// `line_height` em apart, and room from the first line's ascent to the last
/// line's descent. The canvas and form appearances place lines this way.
#[derive(Clone, Copy)]
pub struct LinePlacement {
    pub ascent: f64,
    pub descent: f64,
    pub line_height: f64,
}

impl LinePlacement {
    /// Place lines as the canvas's `text` spaces them: the first baseline
    /// one ascender below the box top, later ones `lineheight` em apart, by
    /// default the font's line height (see [`LineMetrics::line_height`]),
    /// and room down to the last line's descender.
    pub fn lines(metrics: LineMetrics, lineheight: Option<f64>) -> Self {
        Self {
            ascent: metrics.ascender,
            descent: -metrics.descender,
            line_height: metrics.line_height(lineheight),
        }
    }

    fn leading(self, font_size: f64) -> f64 {
        self.line_height * font_size
    }

    fn required_height(self, lines: usize, font_size: f64) -> f64 {
        if lines == 0 {
            return 0.0;
        }
        (self.ascent + self.descent) * font_size + (lines - 1) as f64 * self.leading(font_size)
    }
}

/// Greedily wrap text at UAX #14 opportunities, with grapheme-safe emergency
/// breaks for an otherwise overlong word.
pub fn layout_textbox<F>(
    text: &str,
    box_size: (f64, f64),
    font_size: f64,
    placement: LinePlacement,
    justify: bool,
    max_lines: Option<usize>,
    mut measure: F,
) -> Result<TextBoxLayout, String>
where
    F: FnMut(&str) -> Result<f64, String>,
{
    let (width, height) = box_size;
    let mut lines = Vec::new();
    for paragraph in text.split('\n') {
        let remaining = max_lines.map(|limit| limit.saturating_sub(lines.len()));
        if remaining == Some(0) {
            return Err(TEXT_LINE_LIMIT_ERROR.to_owned());
        }
        let paragraph_lines = wrap_paragraph(paragraph, width, justify, remaining, &mut measure)?;
        lines
            .try_reserve(paragraph_lines.len())
            .map_err(|error| format!("failed to grow text layout line collection: {error}"))?;
        lines.extend(paragraph_lines);
    }

    let required = placement.required_height(lines.len(), font_size);
    let mut spare_height = height - required;
    if spare_height.abs() <= FIT_TOLERANCE {
        spare_height = 0.0;
    }
    Ok(TextBoxLayout {
        lines,
        height: required,
        spare_height,
        ascent: placement.ascent,
        leading: placement.leading(font_size),
    })
}

fn wrap_paragraph<F>(
    paragraph: &str,
    max_width: f64,
    justify: bool,
    max_lines: Option<usize>,
    measure: &mut F,
) -> Result<Vec<TextBoxLine>, String>
where
    F: FnMut(&str) -> Result<f64, String>,
{
    if paragraph.is_empty() {
        if max_lines == Some(0) {
            return Err(TEXT_LINE_LIMIT_ERROR.to_owned());
        }
        return single_line(String::new(), 0.0);
    }

    // Avoid measuring every UAX #14 prefix when the complete paragraph already
    // fits. Besides the common short-line case, this keeps very wide textboxes
    // linear rather than repeatedly shaping or encoding growing prefixes.
    let visible = paragraph.trim_end_matches(char::is_whitespace);
    let paragraph_width = measure(visible)?;
    if paragraph_width <= max_width + FIT_TOLERANCE {
        return single_line(copy_text(visible)?, paragraph_width);
    }

    let break_ends = collect_offsets(
        linebreaks(paragraph).map(|(end, _)| end),
        "line-break index",
    )?;
    let grapheme_ends = collect_offsets(
        paragraph
            .grapheme_indices(true)
            .map(|(offset, grapheme)| offset + grapheme.len()),
        "grapheme index",
    )?;
    let mut lines = Vec::new();
    let mut start = 0;
    while start < paragraph.len() {
        if max_lines.is_some_and(|limit| lines.len() >= limit) {
            return Err(TEXT_LINE_LIMIT_ERROR.to_owned());
        }
        let tail = &paragraph[start..];
        let (consumed, visible_end, line_width) = greedy_line_break(
            paragraph,
            start,
            max_width,
            &break_ends,
            &grapheme_ends,
            measure,
        )?;
        let soft_break = consumed < tail.len();
        let text = copy_text(&tail[..visible_end])?;
        lines
            .try_reserve(1)
            .map_err(|error| format!("failed to grow wrapped text line collection: {error}"))?;
        lines.push(TextBoxLine {
            text,
            width: line_width,
            justify: justify && soft_break,
        });
        start += consumed;
    }
    Ok(lines)
}

fn single_line(text: String, width: f64) -> Result<Vec<TextBoxLine>, String> {
    let mut lines = Vec::new();
    lines
        .try_reserve_exact(1)
        .map_err(|error| format!("failed to allocate text layout line collection: {error}"))?;
    lines.push(TextBoxLine {
        text,
        width,
        justify: false,
    });
    Ok(lines)
}

pub(crate) fn copy_text(text: &str) -> Result<String, String> {
    let mut copy = String::new();
    copy.try_reserve_exact(text.len())
        .map_err(|error| format!("failed to allocate laid-out text: {error}"))?;
    copy.push_str(text);
    Ok(copy)
}

pub(crate) fn normalized_single_line(text: &str) -> Result<String, String> {
    let mut normalized = String::new();
    normalized
        .try_reserve_exact(text.len())
        .map_err(|error| format!("failed to allocate normalized text: {error}"))?;
    for character in text.chars() {
        normalized.push(match character {
            '\r' | '\n' => ' ',
            other => other,
        });
    }
    Ok(normalized)
}

pub(crate) fn collect_graphemes(text: &str) -> Result<Vec<&str>, String> {
    collect_items(text.graphemes(true), "grapheme collection")
}

fn collect_offsets(
    offsets: impl Iterator<Item = usize>,
    context: &str,
) -> Result<Vec<usize>, String> {
    collect_items(offsets, context)
}

fn collect_items<T>(items: impl Iterator<Item = T>, context: &str) -> Result<Vec<T>, String> {
    let mut collected = Vec::new();
    for item in items {
        if collected.len() == collected.capacity() {
            collected
                .try_reserve(1)
                .map_err(|error| format!("failed to grow text {context}: {error}"))?;
        }
        collected.push(item);
    }
    Ok(collected)
}

type MeasuredLineBreak = (usize, usize, f64);

fn measure_line_break<F>(
    text: &str,
    start: usize,
    end: usize,
    measure: &mut F,
) -> Result<MeasuredLineBreak, String>
where
    F: FnMut(&str) -> Result<f64, String>,
{
    let visible = text[start..end].trim_end_matches(char::is_whitespace);
    Ok((end - start, visible.len(), measure(visible)?))
}

fn greedy_line_break<F>(
    text: &str,
    start: usize,
    max_width: f64,
    break_ends: &[usize],
    grapheme_ends: &[usize],
    measure: &mut F,
) -> Result<MeasuredLineBreak, String>
where
    F: FnMut(&str) -> Result<f64, String>,
{
    let mut first = break_ends.partition_point(|end| *end <= start);
    while first < break_ends.len() {
        let end = break_ends[first];
        let visible = text[start..end].trim_end_matches(char::is_whitespace);
        // Do not turn leading whitespace into an artificial blank line.
        if !visible.is_empty() || end == text.len() {
            break;
        }
        first += 1;
    }

    let first_end = break_ends[first];
    let first_visible = text[start..first_end].trim_end_matches(char::is_whitespace);
    let first_visible_end = start + first_visible.len();
    let first_candidate = if first_visible.is_empty() {
        (first_end - start, 0, measure(first_visible)?)
    } else if first_visible.len() <= DIRECT_BREAK_MEASURE_BYTES {
        let candidate = measure_line_break(text, start, first_end, measure)?;
        if candidate.2 <= max_width + FIT_TOLERANCE {
            candidate
        } else {
            return emergency_break(
                text,
                start,
                first_visible_end,
                max_width,
                grapheme_ends,
                measure,
            );
        }
    } else {
        let candidate = emergency_break(
            text,
            start,
            first_visible_end,
            max_width,
            grapheme_ends,
            measure,
        )?;
        if candidate.0 < first_visible.len() {
            return Ok(candidate);
        }
        (first_end - start, first_visible.len(), candidate.2)
    };

    // Probe increasingly distant line-break opportunities, then refine only
    // the final fitting interval. This retains greedy wrapping without
    // repeatedly measuring every growing prefix.
    let last = break_ends.len() - 1;
    let mut best_index = first;
    let mut best = first_candidate;
    let mut offset = 1_usize;
    let failed_index = loop {
        if best_index == last {
            return Ok(best);
        }
        let probe = first.saturating_add(offset).min(last);
        let candidate = measure_line_break(text, start, break_ends[probe], measure)?;
        if candidate.2 <= max_width + FIT_TOLERANCE {
            best_index = probe;
            best = candidate;
            if probe == last {
                return Ok(best);
            }
            offset = offset.saturating_mul(2);
        } else {
            break probe;
        }
    };

    let mut low = best_index + 1;
    let mut high = failed_index;
    while low < high {
        let middle = low + (high - low) / 2;
        let candidate = measure_line_break(text, start, break_ends[middle], measure)?;
        if candidate.2 <= max_width + FIT_TOLERANCE {
            best_index = middle;
            best = candidate;
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    debug_assert!(best_index < failed_index);
    Ok(best)
}

fn emergency_break<F>(
    text: &str,
    start: usize,
    max_end: usize,
    max_width: f64,
    grapheme_ends: &[usize],
    measure: &mut F,
) -> Result<(usize, usize, f64), String>
where
    F: FnMut(&str) -> Result<f64, String>,
{
    let first = grapheme_ends.partition_point(|end| *end <= start);
    let last_exclusive = grapheme_ends.partition_point(|end| *end <= max_end);
    let first_end = grapheme_ends[first];
    let first_width = measure(&text[start..first_end])?;
    if first_width > max_width + FIT_TOLERANCE {
        return Err("textbox is too narrow to fit one text grapheme".to_owned());
    }

    // Galloping keeps narrow lines local; binary refinement avoids measuring
    // every grapheme prefix when one line can hold a long unbreakable run.
    let last = last_exclusive - 1;
    let mut best_index = first;
    let mut best_width = first_width;
    let mut offset = 1_usize;
    let failed_index = loop {
        if best_index == last {
            let end = grapheme_ends[best_index];
            return Ok((end - start, end - start, best_width));
        }
        let probe = first.saturating_add(offset).min(last);
        let end = grapheme_ends[probe];
        let line_width = measure(&text[start..end])?;
        if line_width <= max_width + FIT_TOLERANCE {
            best_index = probe;
            best_width = line_width;
            if probe == last {
                return Ok((end - start, end - start, best_width));
            }
            offset = offset.saturating_mul(2);
        } else {
            break probe;
        }
    };

    let mut low = best_index + 1;
    let mut high = failed_index;
    while low < high {
        let middle = low + (high - low) / 2;
        let end = grapheme_ends[middle];
        let line_width = measure(&text[start..end])?;
        if line_width <= max_width + FIT_TOLERANCE {
            best_index = middle;
            best_width = line_width;
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    debug_assert!(best_index < failed_index);
    let end = grapheme_ends[best_index];
    Ok((end - start, end - start, best_width))
}
