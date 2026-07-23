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
        let [_, _, c, d, _, _] = combined.as_coeffs();
        let mut size = (c * c + d * d).sqrt() * 1000.0;
        if !size.is_finite() || size <= 0.0 {
            size = 12.0;
        }
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

/// Approximate top/bottom from the baseline without real font metrics.
const ASCENT: f64 = 0.8;
const DESCENT: f64 = 0.2;

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

/// Decide whether to insert a space from the gap between adjacent glyphs.
fn needs_gap(prev_end: Option<f64>, glyph: &GlyphRecord) -> bool {
    prev_end.is_some_and(|end| glyph.x - end > glyph.size.max(1.0) * WORD_GAP)
}

/// Assemble glyphs into top-to-bottom, left-to-right plain text.
fn assemble_text(glyphs: Vec<GlyphRecord>) -> String {
    let mut out = String::new();
    for line in cluster_lines(glyphs) {
        let mut prev_end: Option<f64> = None;
        for glyph in &line {
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
/// Line: `(bbox, spans, words)`.
type LineTuple = (BBox, Vec<SpanTuple>, Vec<WordTuple>);
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

/// Assemble collected glyphs into blocks, lines, spans, and words.
fn assemble_layout(glyphs: Vec<GlyphRecord>) -> Vec<BlockTuple> {
    let lines = cluster_lines(glyphs);
    let mut blocks: Vec<Vec<Vec<GlyphRecord>>> = Vec::new();
    let mut prev_baseline: Option<f64> = None;
    let mut prev_size = 0.0_f64;
    for line in lines {
        let baseline = line[0].y;
        let line_size = line.iter().map(|g| g.size).fold(0.0, f64::max);
        let new_block = match prev_baseline {
            Some(prev) => baseline - prev > prev_size.max(line_size).max(1.0) * BLOCK_GAP,
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
                .map(|line| (glyphs_bbox(line), split_spans(line), split_words(line)))
                .collect();
            let mut x0 = f64::INFINITY;
            let mut y0 = f64::INFINITY;
            let mut x1 = f64::NEG_INFINITY;
            let mut y1 = f64::NEG_INFINITY;
            for ((lx0, ly0, lx1, ly1), _, _) in &line_tuples {
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
fn search_glyphs(glyphs: Vec<GlyphRecord>, needle: &str) -> Vec<BBox> {
    let needle_lower: String = needle.chars().flat_map(char::to_lowercase).collect();
    if needle_lower.is_empty() {
        return Vec::new();
    }
    let mut hits = Vec::new();
    for line in cluster_lines(glyphs) {
        let (haystack, map) = line_search_index(&line);
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

/// Extract text from the given page.
pub(crate) fn extract_page_text(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
) -> String {
    assemble_text(collect_glyphs(pdf, page, settings))
}

/// Return page display size and block/line/span/word layout.
pub(crate) fn extract_page_layout(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
) -> (f64, f64, Vec<BlockTuple>) {
    let (width, height) = page.render_dimensions();
    let blocks = assemble_layout(collect_glyphs(pdf, page, settings));
    (f64::from(width), f64::from(height), blocks)
}

/// Search a page case-insensitively and return matching rectangles.
pub(crate) fn search_page(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
    needle: &str,
) -> Vec<BBox> {
    search_glyphs(collect_glyphs(pdf, page, settings), needle)
}
