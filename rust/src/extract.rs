//! hayro Device によるテキスト抽出エンジン。
//!
//! hayro のインタープリタでページを解釈し、グリフごとの Unicode と位置を収集して
//! 行・語のレイアウトへ組み立てる。lopdf の extract_text と異なり、
//! content stream のコメント（lopdf#535）や定義済み CMap（90ms-RKSJ-H 等）も
//! hayro 側で解決される。不可視テキスト（OCR レイヤー、Tr 3）も抽出対象。

use hayro::hayro_interpret::font::Glyph;
use hayro::hayro_interpret::hayro_cmap::BfString;
use hayro::hayro_interpret::{
    BlendMode, ClipPath, Context, Device, GlyphDrawMode, Image, InterpreterCache,
    InterpreterSettings, Paint, PathDrawMode, SoftMask, interpret_page,
};
use hayro::hayro_syntax::Pdf;
use hayro::hayro_syntax::page::Page;
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
        // フォントサイズ: y 基底ベクトル (0,1) の像の長さ
        let [_, _, c, d, _, _] = combined.as_coeffs();
        let mut size = (c * c + d * d).sqrt();
        if !size.is_finite() || size <= 0.0 {
            size = 12.0;
        }
        // 送り幅: グリフ座標系（1000 upem）の advance を x 基底で変換した長さ。
        // 取れないフォント（Type3 等）はサイズの半分で近似する
        let advance = match glyph {
            Glyph::Outline(g) => g.advance_width().map(f64::from),
            Glyph::Type3(_) => None,
        }
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
        });
    }
}

/// 行のしきい値: ベースライン差がこの倍率 × フォントサイズ以内なら同じ行とみなす。
/// 上付き・下付き文字のずれを吸収しつつ、通常の行送り（1.0 以上）は分離できる値。
const LINE_TOLERANCE: f64 = 0.5;

/// 語のしきい値: グリフ間の隙間がこの倍率 × フォントサイズを超えたら空白を補う。
const WORD_GAP: f64 = 0.25;

/// 収集済みグリフを読み順（上→下、行内は左→右）のプレーンテキストへ組み立てる。
fn assemble_text(mut glyphs: Vec<GlyphRecord>) -> String {
    if glyphs.is_empty() {
        return String::new();
    }
    glyphs.sort_by(|a, b| a.y.total_cmp(&b.y).then(a.x.total_cmp(&b.x)));

    // ベースラインの近いグリフを行へまとめる
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

    let mut out = String::new();
    for mut line in lines {
        line.sort_by(|a, b| a.x.total_cmp(&b.x));
        let mut prev_end: Option<f64> = None;
        for glyph in &line {
            if let Some(end) = prev_end
                && glyph.x - end > glyph.size.max(1.0) * WORD_GAP
                && !out.ends_with(' ')
                && !out.ends_with('\n')
            {
                out.push(' ');
            }
            out.push_str(&glyph.text);
            prev_end = Some(glyph.x.max(prev_end.unwrap_or(f64::NEG_INFINITY)) + glyph.advance);
        }
        // 行末の余分な空白グリフは落とす
        while out.ends_with(' ') {
            out.pop();
        }
        out.push('\n');
    }
    out
}

/// 指定ページ（0 始まりの index）のテキストを抽出する。
pub(crate) fn extract_page_text(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
) -> String {
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
    assemble_text(collector.glyphs)
}
