//! ページへの描き込み（画像挿入・別 PDF ページの重ね合わせ）の下請け。
//!
//! 方針: 既存コンテンツストリームはデコードも再エンコードもしない（lopdf の
//! content パーサ経由の往復は #535 系の癖に巻き込まれるため）。描き込みは
//! `/Contents` 配列への「新しいストリームの追加」だけで行い、既存コンテンツの
//! グラフィックス状態の漏れは、既存列を一度だけ q / Q ストリームで挟んで遮断する。

use std::io::Write;

use lopdf::{Dictionary, Document, Object, ObjectId, Stream, dictionary};

/// 挿入画像のデコード結果（PDF Image XObject の材料）。
pub struct ImageParts {
    pub width: u32,
    pub height: u32,
    /// XObject 辞書に入れる ColorSpace 名。
    pub color_space: &'static str,
    /// Filter 名（DCTDecode = JPEG パススルー / FlateDecode = 生サンプル圧縮）。
    pub filter: &'static str,
    /// フィルタ適用済みのストリームデータ。
    pub data: Vec<u8>,
    /// アルファチャンネル（存在すれば SMask 用の生グレースケール。Flate 圧縮前）。
    pub alpha: Option<Vec<u8>>,
}

/// 画像データの形式を magic bytes で判定して XObject の材料に変換する。
///
/// 対応形式は JPEG（DCTDecode パススルー）と PNG（デコードして Flate 圧縮）。
/// それ以外は None（呼び出し側でエラーメッセージにする）。
pub fn parse_image(data: &[u8]) -> Result<Option<ImageParts>, String> {
    if data.starts_with(&[0xFF, 0xD8, 0xFF]) {
        return jpeg_parts(data).map(Some);
    }
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return png_parts(data).map(Some);
    }
    Ok(None)
}

/// JPEG の SOF マーカーから寸法と色成分数を読み、バイト列ごとパススルーする。
fn jpeg_parts(data: &[u8]) -> Result<ImageParts, String> {
    let mut pos = 2usize;
    while pos + 4 <= data.len() {
        if data[pos] != 0xFF {
            pos += 1;
            continue;
        }
        let marker = data[pos + 1];
        // スタンドアロンマーカー（RST/TEM）と 0xFF フィルはスキップ
        if marker == 0xFF || (0xD0..=0xD7).contains(&marker) || marker == 0x01 {
            pos += 2;
            continue;
        }
        let length = usize::from(u16::from_be_bytes([data[pos + 2], data[pos + 3]]));
        // SOF0-15（C4=DHT, C8=JPG, CC=DAC を除く）が寸法を持つ
        if matches!(marker, 0xC0..=0xCF) && !matches!(marker, 0xC4 | 0xC8 | 0xCC) {
            if length < 8
                || pos
                    .checked_add(2 + length)
                    .is_none_or(|end| end > data.len())
            {
                return Err("JPEG の SOF セグメント長が壊れています".to_owned());
            }
            // components（pos + 9）まで読むため、最低 10 バイト必要。
            if pos.checked_add(10).is_none_or(|end| end > data.len()) {
                return Err("JPEG の SOF セグメントが壊れています".to_owned());
            }
            let height = u32::from(u16::from_be_bytes([data[pos + 5], data[pos + 6]]));
            let width = u32::from(u16::from_be_bytes([data[pos + 7], data[pos + 8]]));
            let components = data[pos + 9];
            let color_space = match components {
                1 => "DeviceGray",
                3 => "DeviceRGB",
                4 => "DeviceCMYK",
                other => return Err(format!("JPEG の色成分数 {other} には対応していません")),
            };
            if width == 0 || height == 0 {
                return Err("JPEG の寸法が 0 です".to_owned());
            }
            return Ok(ImageParts {
                width,
                height,
                color_space,
                filter: "DCTDecode",
                data: data.to_vec(),
                alpha: None,
            });
        }
        pos += 2 + length;
    }
    Err("JPEG に SOF マーカーが見つかりません".to_owned())
}

/// PNG をデコードし、8bit Gray/RGB（+ 別立てのアルファ）へ正規化して Flate 圧縮する。
fn png_parts(data: &[u8]) -> Result<ImageParts, String> {
    let mut decoder = png::Decoder::new(std::io::Cursor::new(data));
    // パレット展開・tRNS→アルファ・16bit→8bit をデコーダに任せる
    decoder.set_transformations(png::Transformations::normalize_to_color8());
    let mut reader = decoder
        .read_info()
        .map_err(|e| format!("PNG の読み取りに失敗しました: {e}"))?;
    let buf_size = reader
        .output_buffer_size()
        .ok_or_else(|| "PNG が大きすぎます".to_owned())?;
    let mut buf = vec![0u8; buf_size];
    let info = reader
        .next_frame(&mut buf)
        .map_err(|e| format!("PNG のデコードに失敗しました: {e}"))?;
    buf.truncate(info.buffer_size());
    let (width, height) = (info.width, info.height);
    if width == 0 || height == 0 {
        return Err("PNG の寸法が 0 です".to_owned());
    }

    let (color_space, samples, alpha) = match info.color_type {
        png::ColorType::Grayscale => ("DeviceGray", buf, None),
        png::ColorType::Rgb => ("DeviceRGB", buf, None),
        png::ColorType::GrayscaleAlpha => {
            let mut gray = Vec::with_capacity(buf.len() / 2);
            let mut a = Vec::with_capacity(buf.len() / 2);
            for pair in buf.chunks_exact(2) {
                gray.push(pair[0]);
                a.push(pair[1]);
            }
            ("DeviceGray", gray, Some(a))
        }
        png::ColorType::Rgba => {
            let mut rgb = Vec::with_capacity(buf.len() / 4 * 3);
            let mut a = Vec::with_capacity(buf.len() / 4);
            for px in buf.chunks_exact(4) {
                rgb.extend_from_slice(&px[..3]);
                a.push(px[3]);
            }
            ("DeviceRGB", rgb, Some(a))
        }
        // normalize_to_color8 で Indexed は展開済みのため到達しない
        png::ColorType::Indexed => return Err("パレット PNG を展開できませんでした".to_owned()),
    };

    Ok(ImageParts {
        width,
        height,
        color_space,
        filter: "FlateDecode",
        data: flate_compress(&samples)?,
        alpha,
    })
}

/// zlib（PDF の FlateDecode）で圧縮する。
pub fn flate_compress(data: &[u8]) -> Result<Vec<u8>, String> {
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    encoder
        .write_all(data)
        .and_then(|()| encoder.finish())
        .map_err(|e| format!("Flate 圧縮に失敗しました: {e}"))
}

/// 画像 XObject（と必要なら SMask）をドキュメントへ追加し、ObjectId を返す。
pub fn add_image_xobject(doc: &mut Document, parts: ImageParts) -> Result<ObjectId, String> {
    let smask_id = match &parts.alpha {
        Some(alpha) => {
            let compressed = flate_compress(alpha)?;
            let dict = dictionary! {
                "Type" => "XObject",
                "Subtype" => "Image",
                "Width" => i64::from(parts.width),
                "Height" => i64::from(parts.height),
                "ColorSpace" => "DeviceGray",
                "BitsPerComponent" => 8,
                "Filter" => "FlateDecode",
            };
            Some(doc.add_object(Stream::new(dict, compressed).with_compression(false)))
        }
        None => None,
    };
    let mut dict = dictionary! {
        "Type" => "XObject",
        "Subtype" => "Image",
        "Width" => i64::from(parts.width),
        "Height" => i64::from(parts.height),
        "ColorSpace" => parts.color_space,
        "BitsPerComponent" => 8,
        "Filter" => parts.filter,
    };
    if let Some(id) = smask_id {
        dict.set("SMask", Object::Reference(id));
    }
    Ok(doc.add_object(Stream::new(dict, parts.data).with_compression(false)))
}

/// 表示空間（左上原点・y 下向き）の点を、非回転の PDF ユーザー空間の点へ写す。
///
/// crop は対象ページの CropBox、rotation は正規化済みの表示回転（0/90/180/270）。
/// 導出はページを時計回りに rotation 度回して表示する PDF の規約に従う。
pub(crate) fn display_to_pdf(crop: [f64; 4], rotation: i64, x: f64, y: f64) -> (f64, f64) {
    let [cx0, cy0, cx1, cy1] = crop;
    match rotation {
        90 => (cx0 + y, cy0 + x),
        180 => (cx1 - x, cy0 + y),
        270 => (cx1 - y, cy1 - x),
        _ => (cx0 + x, cy1 - y),
    }
}

/// 非回転の PDF ユーザー空間の点を、表示空間（左上原点・y 下向き）の点へ写す。
///
/// display_to_pdf の逆写像。
pub(crate) fn pdf_to_display(crop: [f64; 4], rotation: i64, x: f64, y: f64) -> (f64, f64) {
    let [cx0, cy0, cx1, cy1] = crop;
    match rotation {
        90 => (y - cy0, x - cx0),
        180 => (cx1 - x, y - cy0),
        270 => (cy1 - y, cx1 - x),
        _ => (x - cx0, cy1 - y),
    }
}

/// PDF 空間の矩形を、表示空間の正規化済み矩形 (x0, y0, x1, y1) へ写す。
pub fn pdf_rect_to_display(crop: [f64; 4], rotation: i64, rect: [f64; 4]) -> [f64; 4] {
    let (ax, ay) = pdf_to_display(crop, rotation, rect[0], rect[1]);
    let (bx, by) = pdf_to_display(crop, rotation, rect[2], rect[3]);
    [ax.min(bx), ay.min(by), ax.max(bx), ay.max(by)]
}

/// 表示空間の矩形の四隅（表示上の 左上・右上・左下・右下 の順）を PDF 空間の点列で返す。
///
/// QuadPoints（Acrobat 互換のジグザグ順）と外接矩形の計算に使う。
pub fn display_rect_quad_pdf(crop: [f64; 4], rotation: i64, rect: [f64; 4]) -> [(f64, f64); 4] {
    let [x0, y0, x1, y1] = rect;
    [
        display_to_pdf(crop, rotation, x0, y0),
        display_to_pdf(crop, rotation, x1, y0),
        display_to_pdf(crop, rotation, x0, y1),
        display_to_pdf(crop, rotation, x1, y1),
    ]
}

/// PDF 空間の点列の外接矩形（正規化済み）を返す。
pub fn bounding_rect(points: &[(f64, f64)]) -> [f64; 4] {
    let mut out = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for &(x, y) in points {
        out[0] = out[0].min(x);
        out[1] = out[1].min(y);
        out[2] = out[2].max(x);
        out[3] = out[3].max(y);
    }
    out
}

/// ハイライト注釈の外観ストリーム（AP /N）の描画オペレータ列を組み立てる。
///
/// quads は display_rect_quad_pdf の返す四隅（左上・右上・左下・右下）。
/// ExtGState 名 /PyloGS（ブレンド Multiply + 透明度）は呼び出し側が Resources へ登録する。
pub fn highlight_ap_ops(quads: &[[(f64, f64); 4]], color: (f64, f64, f64)) -> Vec<u8> {
    let mut out = format!(
        "/PyloGS gs\n{} {} {} rg\n",
        fmt(color.0),
        fmt(color.1),
        fmt(color.2)
    )
    .into_bytes();
    for quad in quads {
        let [ul, ur, ll, lr] = *quad;
        out.extend_from_slice(
            format!(
                "{} {} m\n{} {} l\n{} {} l\n{} {} l\nh\nf\n",
                fmt(ul.0),
                fmt(ul.1),
                fmt(ur.0),
                fmt(ur.1),
                fmt(lr.0),
                fmt(lr.1),
                fmt(ll.0),
                fmt(ll.1),
            )
            .as_bytes(),
        );
    }
    out
}

/// 配置対象の中身（cm 行列の作り方が異なる 2 種類）。
pub enum PlacedContent {
    /// 画像 XObject（単位正方形に描かれる）。アスペクト比は width/height。
    Image { width: u32, height: u32 },
    /// Form XObject（中身は取り込み元ページの座標系。BBox = 元 CropBox）。
    Form { crop: [f64; 4], rotation: i64 },
}

impl PlacedContent {
    /// 表示空間でのアスペクト比（幅 / 高さ）。
    fn display_aspect(&self) -> f64 {
        match self {
            Self::Image { width, height } => f64::from(*width) / f64::from(*height),
            Self::Form { crop, rotation } => {
                let (w, h) = (crop[2] - crop[0], crop[3] - crop[1]);
                if matches!(rotation, 90 | 270) {
                    h / w
                } else {
                    w / h
                }
            }
        }
    }
}

/// 表示空間の矩形 rect へ content を配置する cm 行列 [a b c d e f] を計算する。
///
/// rect は対象ページの表示座標（左上原点、page.rect と同じ系）。
/// keep_proportion なら content のアスペクト比を保って rect 内へ中央合わせで収める。
pub fn placement_matrix(
    target_crop: [f64; 4],
    target_rotation: i64,
    rect: [f64; 4],
    content: &PlacedContent,
    keep_proportion: bool,
) -> [f64; 6] {
    let [mut x0, mut y0, mut x1, mut y1] = rect;
    if keep_proportion {
        let (rw, rh) = (x1 - x0, y1 - y0);
        let aspect = content.display_aspect();
        let (fit_w, fit_h) = if rw / rh > aspect {
            (rh * aspect, rh)
        } else {
            (rw, rw / aspect)
        };
        x0 += (rw - fit_w) / 2.0;
        y0 += (rh - fit_h) / 2.0;
        x1 = x0 + fit_w;
        y1 = y0 + fit_h;
    }

    // 配置先の四隅（PDF 空間）: O = 表示上の左下、U = 表示右向き辺、V = 表示上向き辺
    let (ox, oy) = display_to_pdf(target_crop, target_rotation, x0, y1);
    let (ux, uy) = {
        let (px, py) = display_to_pdf(target_crop, target_rotation, x1, y1);
        (px - ox, py - oy)
    };
    let (vx, vy) = {
        let (px, py) = display_to_pdf(target_crop, target_rotation, x0, y0);
        (px - ox, py - oy)
    };

    match content {
        // 画像は単位正方形 → [U V O] がそのまま行列になる
        PlacedContent::Image { .. } => [ux, uy, vx, vy, ox, oy],
        // Form は元ページ座標系の点 Q を「元ページの表示座標 → 正規化 → 配置先」へ合成する。
        // Q の表示座標 (dx, dy) は Q の一次式なので、全体も一次式に畳める。
        PlacedContent::Form { crop, rotation } => {
            let [sx0, sy0, sx1, sy1] = *crop;
            let (sw, sh) = (sx1 - sx0, sy1 - sy0);
            let (sdw, sdh) = if matches!(rotation, 90 | 270) {
                (sh, sw)
            } else {
                (sw, sh)
            };
            // dx = ax*Qx + ay*Qy + a0, dy = bx*Qx + by*Qy + b0
            let (ax, ay, a0, bx, by, b0) = match rotation {
                90 => (0.0, 1.0, -sy0, 1.0, 0.0, -sx0),
                180 => (-1.0, 0.0, sx1, 0.0, 1.0, -sy0),
                270 => (0.0, -1.0, sy1, -1.0, 0.0, sx1),
                _ => (1.0, 0.0, -sx0, 0.0, -1.0, sy1),
            };
            // P(Q) = O + (dx/sdw)・U + (1 - dy/sdh)・V
            let a = ux * ax / sdw - vx * bx / sdh;
            let b = uy * ax / sdw - vy * bx / sdh;
            let c = ux * ay / sdw - vx * by / sdh;
            let d = uy * ay / sdw - vy * by / sdh;
            let e = ox + ux * a0 / sdw + vx * (1.0 - b0 / sdh);
            let f = oy + uy * a0 / sdw + vy * (1.0 - b0 / sdh);
            [a, b, c, d, e, f]
        }
    }
}

/// cm 行列と XObject 名から描画オペレータ列を組み立てる。
pub fn draw_ops(matrix: [f64; 6], name: &str) -> Vec<u8> {
    let [a, b, c, d, e, f] = matrix;
    format!(
        "q\n{} {} {} {} {} {} cm\n/{name} Do\nQ\n",
        fmt(a),
        fmt(b),
        fmt(c),
        fmt(d),
        fmt(e),
        fmt(f)
    )
    .into_bytes()
}

/// PDF コンテンツ向けの数値表記（小数 4 桁、末尾ゼロ削除）。
pub(crate) fn fmt(v: f64) -> String {
    let s = format!("{v:.4}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    if s.is_empty() || s == "-" {
        "0".to_owned()
    } else {
        s.to_owned()
    }
}

/// ページの Resources/Font へフォントオブジェクトの参照を登録する。
///
/// 呼び出し前に継承属性の焼き込みと get_or_create_resources 済みで、
/// ページ辞書に /Resources が存在することが前提。Resources / Font どちらも
/// 間接参照の可能性があるため、可変借用の前に参照先 id を解決する 2 段構えにする。
pub fn add_page_font(
    doc: &mut Document,
    page_id: ObjectId,
    name: &str,
    font_id: ObjectId,
) -> Result<(), lopdf::Error> {
    let res_ref = doc
        .get_object(page_id)?
        .as_dict()?
        .get(b"Resources")
        .ok()
        .and_then(|r| r.as_reference().ok());
    let font_ref = {
        let resources = match res_ref {
            Some(id) => doc.get_object(id)?.as_dict()?,
            None => doc
                .get_object(page_id)?
                .as_dict()?
                .get(b"Resources")?
                .as_dict()?,
        };
        resources
            .get(b"Font")
            .ok()
            .and_then(|f| f.as_reference().ok())
    };
    if let Some(fid) = font_ref {
        let fonts = doc.get_object_mut(fid)?.as_dict_mut()?;
        fonts.set(name, Object::Reference(font_id));
        return Ok(());
    }
    let resources = match res_ref {
        Some(id) => doc.get_object_mut(id)?.as_dict_mut()?,
        None => doc
            .get_object_mut(page_id)?
            .as_dict_mut()?
            .get_mut(b"Resources")?
            .as_dict_mut()?,
    };
    if !resources.has(b"Font") {
        resources.set("Font", Dictionary::new());
    }
    let fonts = resources.get_mut(b"Font")?.as_dict_mut()?;
    fonts.set(name, Object::Reference(font_id));
    Ok(())
}

/// 表示座標 point をベースライン起点とするテキスト描画オペレータ列を組み立てる。
///
/// lines は WinAnsi（cp1252）エンコード済みのバイト列（1 要素 = 1 行）。
/// Tm に表示空間の基底ベクトルを入れるため、回転ページでも表示上で正立する。
/// 行送りは fontsize の 1.2 倍。
pub fn text_ops(
    crop: [f64; 4],
    rotation: i64,
    point: (f64, f64),
    lines: &[Vec<u8>],
    font: &str,
    size: f64,
    color: (f64, f64, f64),
) -> Vec<u8> {
    let (ox, oy) = display_to_pdf(crop, rotation, point.0, point.1);
    let (rx, ry) = {
        let p = display_to_pdf(crop, rotation, point.0 + 1.0, point.1);
        (p.0 - ox, p.1 - oy)
    };
    let (ux, uy) = {
        let p = display_to_pdf(crop, rotation, point.0, point.1 - 1.0);
        (p.0 - ox, p.1 - oy)
    };
    let mut out = format!(
        "q\nBT\n/{font} {} Tf\n{} {} {} rg\n{} {} {} {} {} {} Tm\n{} TL\n",
        fmt(size),
        fmt(color.0),
        fmt(color.1),
        fmt(color.2),
        fmt(rx),
        fmt(ry),
        fmt(ux),
        fmt(uy),
        fmt(ox),
        fmt(oy),
        fmt(size * 1.2),
    )
    .into_bytes();
    for (i, line) in lines.iter().enumerate() {
        if i > 0 {
            out.extend_from_slice(b"T*\n");
        }
        out.push(b'(');
        for &b in line {
            match b {
                b'(' | b')' | b'\\' => {
                    out.push(b'\\');
                    out.push(b);
                }
                0x20..=0x7E => out.push(b),
                _ => out.extend_from_slice(format!("\\{b:03o}").as_bytes()),
            }
        }
        out.extend_from_slice(b") Tj\n");
    }
    out.extend_from_slice(b"ET\nQ\n");
    out
}

/// 既存の /Contents 列を一度だけ q / Q ストリームで挟む（グラフィックス状態の遮断）。
///
/// 既に本関数で挟んだ形（先頭が b"q\n"、末尾が b"Q\n" の単独ストリーム）なら何もしない。
fn ensure_contents_wrapped(doc: &mut Document, page_id: ObjectId) -> Result<(), lopdf::Error> {
    let contents = doc.get_page_contents(page_id);
    if contents.is_empty() {
        return Ok(());
    }
    let stream_bytes = |doc: &Document, id: ObjectId| -> Option<Vec<u8>> {
        doc.get_object(id)
            .ok()
            .and_then(|o| o.as_stream().ok())
            .map(|s| s.content.clone())
    };
    let first = contents.first().and_then(|&id| stream_bytes(doc, id));
    let last = contents.last().and_then(|&id| stream_bytes(doc, id));
    if contents.len() >= 2 && first.as_deref() == Some(b"q\n") && last.as_deref() == Some(b"Q\n") {
        return Ok(());
    }
    let q_id =
        doc.add_object(Stream::new(Dictionary::new(), b"q\n".to_vec()).with_compression(false));
    let push_q_id =
        doc.add_object(Stream::new(Dictionary::new(), b"Q\n".to_vec()).with_compression(false));
    let mut list: Vec<Object> = vec![Object::Reference(q_id)];
    list.extend(contents.into_iter().map(Object::Reference));
    list.push(Object::Reference(push_q_id));
    let page = doc.get_object_mut(page_id).and_then(Object::as_dict_mut)?;
    page.set("Contents", list);
    Ok(())
}

/// 描画オペレータ列を新しいコンテンツストリームとしてページへ足す。
///
/// overlay なら既存コンテンツの後（上に描画）、そうでなければ先頭（下に描画）。
/// 既存コンテンツはラップするだけで中身には触れない。
pub fn push_content(
    doc: &mut Document,
    page_id: ObjectId,
    ops: Vec<u8>,
    overlay: bool,
) -> Result<(), lopdf::Error> {
    ensure_contents_wrapped(doc, page_id)?;
    let new_id = doc.add_object(Stream::new(Dictionary::new(), ops).with_compression(false));
    let mut list: Vec<Object> = doc
        .get_page_contents(page_id)
        .into_iter()
        .map(Object::Reference)
        .collect();
    if overlay {
        list.push(Object::Reference(new_id));
    } else {
        list.insert(0, Object::Reference(new_id));
    }
    let page = doc.get_object_mut(page_id).and_then(Object::as_dict_mut)?;
    page.set("Contents", list);
    Ok(())
}
