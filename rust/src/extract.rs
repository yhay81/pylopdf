//! hayro Device によるテキスト抽出エンジン。
//!
//! hayro のインタープリタでページを解釈し、グリフごとの Unicode と位置を収集して
//! 行・語・ブロックのレイアウトへ組み立てる。lopdf の extract_text と異なり、
//! content stream のコメント（lopdf#535）や定義済み CMap（90ms-RKSJ-H 等）も
//! hayro 側で解決される。不可視テキスト（OCR レイヤー、Tr 3）も抽出対象。

use hayro::hayro_interpret::font::Glyph;
use hayro::hayro_interpret::hayro_cmap::BfString;
use hayro::hayro_interpret::{
    BlendMode, ClipPath, Context, Device, GlyphDrawMode, Image, ImageData, InterpreterCache,
    InterpreterSettings, LumaData, Paint, PathDrawMode, SoftMask, interpret_page,
};
use hayro::hayro_syntax::page::Page;
use hayro::hayro_syntax::{Filter, Pdf};
use kurbo::{Affine, Point, Rect};

/// 収集した 1 グリフ分の情報。座標は「左上原点・下向き y」のページ座標。
struct GlyphRecord {
    /// グリフの Unicode 表現（合字は複数文字になる）
    text: String,
    /// ベースライン原点の x
    x: f64,
    /// ベースライン原点の y（上から下へ増加）
    y: f64,
    /// デバイス空間でのフォントサイズ（行・語の判定しきい値に使う）
    size: f64,
    /// デバイス空間での送り幅（不明なフォントでは size から推定）
    advance: f64,
    /// フォントの識別キー（スパン分割に使う。Type3 は 0）
    font_key: u128,
}

/// グリフを収集するだけの Device 実装。描画系の命令はすべて無視する。
struct TextCollector {
    glyphs: Vec<GlyphRecord>,
    page_height: f64,
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
        // フォントサイズ: y 基底ベクトルの像の長さ × 1000
        // （hayro のグリフ空間は 1000 upem 正規化のため、変換係数は実サイズの 1/1000）
        let [_, _, c, d, _, _] = combined.as_coeffs();
        let mut size = (c * c + d * d).sqrt() * 1000.0;
        if !size.is_finite() || size <= 0.0 {
            size = 12.0;
        }
        // 送り幅: グリフ座標系の advance を x 基底で変換した長さ。
        // 取れないフォント（Type3 等）はサイズの半分で近似する
        let (advance_width, font_key) = match glyph {
            Glyph::Outline(g) => (g.advance_width().map(f64::from), g.font_cache_key()),
            Glyph::Type3(_) => (None, 0),
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
        self.glyphs.push(GlyphRecord {
            text,
            x: origin.x,
            y: self.page_height - origin.y,
            size,
            advance,
            font_key,
        });
    }
}

/// 行のしきい値: ベースライン差がこの倍率 × フォントサイズ以内なら同じ行とみなす。
/// 上付き・下付き文字のずれを吸収しつつ、通常の行送り（1.0 以上）は分離できる値。
const LINE_TOLERANCE: f64 = 0.5;

/// 語のしきい値: グリフ間の隙間がこの倍率 × フォントサイズを超えたら空白を補う。
/// 通常の語間空白は 0.25em 前後、カーニングは ±0.05em 程度なので、その間に置く。
const WORD_GAP: f64 = 0.15;

/// ブロックのしきい値: 行送りがこの倍率 × フォントサイズを超えたら段落の切れ目とみなす。
const BLOCK_GAP: f64 = 1.5;

/// ベースラインからの上端・下端の近似（実フォントメトリクスは持たないため）。
const ASCENT: f64 = 0.8;
const DESCENT: f64 = 0.2;

/// グリフ列を「読み順の行」へまとめる（各行は x 昇順に整列済み）。
fn cluster_lines(mut glyphs: Vec<GlyphRecord>) -> Vec<Vec<GlyphRecord>> {
    glyphs.sort_by(|a, b| a.y.total_cmp(&b.y).then(a.x.total_cmp(&b.x)));
    let mut lines: Vec<Vec<GlyphRecord>> = Vec::new();
    let mut current_baseline = f64::NEG_INFINITY;
    for glyph in glyphs {
        let tolerance = glyph.size.max(1.0) * LINE_TOLERANCE;
        if (glyph.y - current_baseline).abs() <= tolerance {
            lines
                .last_mut()
                .expect("行は直前に作られている")
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

/// 行内のグリフ間に空白を補うべきか（前グリフの右端と次グリフの左端の隙間で判定）。
fn needs_gap(prev_end: Option<f64>, glyph: &GlyphRecord) -> bool {
    prev_end.is_some_and(|end| glyph.x - end > glyph.size.max(1.0) * WORD_GAP)
}

/// 収集済みグリフを読み順（上→下、行内は左→右）のプレーンテキストへ組み立てる。
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
        // 行末の余分な空白グリフは落とす
        while out.ends_with(' ') {
            out.pop();
        }
        out.push('\n');
    }
    out
}

/// bbox（x0, y0, x1, y1。左上原点・下向き y）。
type BBox = (f64, f64, f64, f64);
/// スパン: (bbox, text, size, origin)。
type SpanTuple = (BBox, String, f64, (f64, f64));
/// 語: (bbox, text)。
type WordTuple = (BBox, String);
/// 行: (bbox, spans, words)。
type LineTuple = (BBox, Vec<SpanTuple>, Vec<WordTuple>);
/// ブロック: (bbox, lines)。
pub(crate) type BlockTuple = (BBox, Vec<LineTuple>);

/// グリフ列の外接矩形（ベースライン ± サイズ近似で縦方向を推定）。
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

/// 1 行のグリフ列をスパン（サイズ・フォントが同じ連続部分）へ分割する。
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
            ));
            start = i;
        }
    }
    spans
}

/// 1 行のグリフ列を語（空白と隙間で区切られた連続部分）へ分割する。
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

/// 収集済みグリフをブロック → 行 → スパン / 語のレイアウトへ組み立てる。
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
        blocks.last_mut().expect("直前に作られている").push(line);
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

/// 行のグリフ列から「検索用の小文字テキスト + 文字→グリフ対応表」を作る。
///
/// 語の隙間には合成空白を挿入する（対応するグリフは無いので None）。
/// 合字や小文字化で 1 グリフが複数文字になる場合は、全文字を同じグリフへ対応させる。
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

/// ページ内のテキスト検索（大文字小文字を区別しない）。ヒットごとの bbox を返す。
///
/// 行単位で検索するため、行をまたぐ一致は検出しない。
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
            // バイト位置 → 文字位置 → グリフ集合
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

/// 抽出画像: (幅, 高さ, ページ上の bbox, 形式 "jpeg"/"png", バイト列)。
pub(crate) type ImageTuple = (u32, u32, BBox, String, Vec<u8>);

/// 画像を収集するだけの Device 実装。
struct ImageCollector {
    images: Vec<ImageTuple>,
    page_height: f64,
}

/// JPEG マジックナンバー（SOI マーカー）。
const JPEG_MAGIC: [u8; 3] = [0xFF, 0xD8, 0xFF];

/// 元の JPEG バイト列をそのまま取り出せるならそれを返す。
///
/// 対応するのは /Filter が [DCTDecode] か [FlateDecode, DCTDecode] の場合。
/// 展開結果が JPEG マジックで始まることを検証し、そうでなければ None
/// （呼び出し側がデコード + PNG 化にフォールバックする）。
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

/// 画像のピクセル矩形を transform で写した外接矩形を、左上原点の bbox で返す。
///
/// hayro は draw_image の直前に「ピクセル空間（上原点、0..幅 × 0..高さ）→
/// PDF の単位正方形」の変換を pre-concat しているため、transform には
/// ピクセル座標の四隅をそのまま渡す。
fn image_bbox(transform: Affine, width: f64, height: f64, page_height: f64) -> BBox {
    let mut x0 = f64::INFINITY;
    let mut y0 = f64::INFINITY;
    let mut x1 = f64::NEG_INFINITY;
    let mut y1 = f64::NEG_INFINITY;
    for (px, py) in [(0.0, 0.0), (width, 0.0), (0.0, height), (width, height)] {
        let p = transform * Point::new(px, py);
        x0 = x0.min(p.x);
        x1 = x1.max(p.x);
        y0 = y0.min(page_height - p.y);
        y1 = y1.max(page_height - p.y);
    }
    (x0, y0, x1, y1)
}

/// ピクセルデータを PNG にエンコードする。
fn encode_png(width: u32, height: u32, color: png::ColorType, data: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut out, width, height);
        encoder.set_color(color);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().ok()?;
        writer.write_image_data(data).ok()?;
        writer.finish().ok()?;
    }
    Some(out)
}

/// デコード済みラスタ画像（RGB/グレー + 別チャンネルのアルファ）を PNG 化する。
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
                    )
                    .map(|png| (luma.width, luma.height, png));
                }
                _ => luma.data,
            };
            return encode_png(luma.width, luma.height, png::ColorType::Grayscale, &data)
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
            encode_png(width, height, png::ColorType::Rgba, &interleaved)
                .map(|png| (width, height, png))
        }
        _ => encode_png(width, height, png::ColorType::Rgb, &rgb).map(|png| (width, height, png)),
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
            self.page_height,
        );
        match image {
            Image::Raster(raster) => {
                // DCTDecode で終わる画像は生の JPEG をそのまま取り出す（再圧縮しない）
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

/// 指定ページ上に描画される画像を抽出する。
pub(crate) fn extract_page_images(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
) -> Vec<ImageTuple> {
    let cache = InterpreterCache::new();
    let mut context = Context::new(
        Affine::IDENTITY,
        Rect::new(0.0, 0.0, 1.0, 1.0),
        &cache,
        pdf.xref(),
        settings,
    );
    let (_, page_height) = page.render_dimensions();
    let mut collector = ImageCollector {
        images: Vec::new(),
        page_height: f64::from(page_height),
    };
    interpret_page(page, &mut context, &mut collector);
    collector.images
}

/// ページを解釈してグリフ列を収集する。
fn collect_glyphs(pdf: &Pdf, page: &Page<'_>, settings: InterpreterSettings) -> Vec<GlyphRecord> {
    let cache = InterpreterCache::new();
    let mut context = Context::new(
        Affine::IDENTITY,
        Rect::new(0.0, 0.0, 1.0, 1.0),
        &cache,
        pdf.xref(),
        settings,
    );
    let (_, page_height) = page.render_dimensions();
    let mut collector = TextCollector {
        glyphs: Vec::new(),
        page_height: f64::from(page_height),
    };
    interpret_page(page, &mut context, &mut collector);
    collector.glyphs
}

/// 指定ページのテキストを抽出する。
pub(crate) fn extract_page_text(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
) -> String {
    assemble_text(collect_glyphs(pdf, page, settings))
}

/// 指定ページのレイアウト（ブロック → 行 → スパン / 語）と表示サイズを返す。
pub(crate) fn extract_page_layout(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
) -> (f64, f64, Vec<BlockTuple>) {
    let (width, height) = page.render_dimensions();
    let blocks = assemble_layout(collect_glyphs(pdf, page, settings));
    (f64::from(width), f64::from(height), blocks)
}

/// 指定ページ内を検索し、ヒットした矩形を返す（大文字小文字を区別しない）。
pub(crate) fn search_page(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
    needle: &str,
) -> Vec<BBox> {
    search_glyphs(collect_glyphs(pdf, page, settings), needle)
}
