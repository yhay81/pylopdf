//! OCR 結果の不可視テキスト層（searchable PDF 化）の下請け。
//!
//! ocrmypdf と同じ発想で、フォント実体を埋め込まない CID フォント
//! （Identity-H + ToUnicode CMap）へ文書内の文字種ごとに CID を割り当て、
//! 不可視レンダリングモード（Tr 3）でテキストを書き込む。見た目には何も
//! 描かれず、抽出・検索（ToUnicode 経由）だけに現れる。フォントを
//! 埋め込まないため、どの言語でもファイルサイズをほぼ増やさない。

use std::collections::BTreeMap;

use lopdf::{Dictionary, Document, Object, ObjectId, Stream, dictionary};

use crate::draw;

/// OCR 層の 1 語（表示座標の bbox + テキスト）。
pub type OcrWord = (f64, f64, f64, f64, String);

/// words の全文字へ CID を割り当てる（0 は notdef のため 1 始まり）。
pub fn assign_cids(words: &[OcrWord]) -> BTreeMap<char, u16> {
    let mut map = BTreeMap::new();
    let mut next: u16 = 1;
    for (_, _, _, _, text) in words {
        for ch in text.chars() {
            map.entry(ch).or_insert_with(|| {
                let cid = next;
                next = next.saturating_add(1);
                cid
            });
        }
    }
    map
}

/// CID → Unicode（UTF-16BE）の ToUnicode CMap を組み立てる。
///
/// bfchar は仕様どおり 100 エントリずつのブロックに分ける。
fn build_to_unicode(cid_map: &BTreeMap<char, u16>) -> Vec<u8> {
    let mut entries: Vec<(u16, char)> = cid_map.iter().map(|(&ch, &cid)| (cid, ch)).collect();
    entries.sort_unstable();
    let mut out = String::from(
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n\
         /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n\
         /CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n\
         1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n",
    );
    for block in entries.chunks(100) {
        out.push_str(&format!("{} beginbfchar\n", block.len()));
        for &(cid, ch) in block {
            out.push_str(&format!("<{cid:04X}> <"));
            let mut buf = [0u16; 2];
            for unit in ch.encode_utf16(&mut buf) {
                out.push_str(&format!("{unit:04X}"));
            }
            out.push_str(">\n");
        }
        out.push_str("endbfchar\n");
    }
    out.push_str("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n");
    out.into_bytes()
}

/// OCR 層用のフォント一式（Type0 / CIDFontType2 / FontDescriptor / ToUnicode）を
/// ドキュメントへ追加し、Type0 フォントの ObjectId を返す。
///
/// FontFile は持たない（非埋め込み）。全 CID の送り幅は DW=1000（1 em）で、
/// 語ごとの実幅はテキスト行列の横スケールで合わせる。
pub fn add_ocr_font(doc: &mut Document, cid_map: &BTreeMap<char, u16>) -> ObjectId {
    let to_unicode = doc.add_object(
        Stream::new(Dictionary::new(), build_to_unicode(cid_map)).with_compression(false),
    );
    let descriptor = doc.add_object(dictionary! {
        "Type" => "FontDescriptor",
        "FontName" => "PyloOCR-Gothic",
        "Flags" => 4,
        "FontBBox" => Object::Array(vec![0.into(), (-200).into(), 1000.into(), 800.into()]),
        "ItalicAngle" => 0,
        "Ascent" => 800,
        "Descent" => -200,
        "CapHeight" => 800,
        "StemV" => 80,
    });
    let cid_font = doc.add_object(dictionary! {
        "Type" => "Font",
        "Subtype" => "CIDFontType2",
        "BaseFont" => "PyloOCR-Gothic",
        "CIDSystemInfo" => dictionary! {
            "Registry" => Object::string_literal("Adobe"),
            "Ordering" => Object::string_literal("Identity"),
            "Supplement" => 0,
        },
        "FontDescriptor" => descriptor,
        "DW" => 1000,
        "CIDToGIDMap" => "Identity",
    });
    doc.add_object(dictionary! {
        "Type" => "Font",
        "Subtype" => "Type0",
        "BaseFont" => "PyloOCR-Gothic",
        "Encoding" => "Identity-H",
        "DescendantFonts" => vec![Object::Reference(cid_font)],
        "ToUnicode" => to_unicode,
    })
}

/// 不可視テキスト（Tr 3）の描画オペレータ列を組み立てる。
///
/// 語ごとに: フォントサイズ = bbox 高さ、ベースライン = bbox 下端から
/// Descent 比（0.2）だけ上、横方向はテキスト行列のスケールで bbox 幅に合わせる。
pub fn ocr_ops(
    crop: [f64; 4],
    rotation: i64,
    words: &[OcrWord],
    cid_map: &BTreeMap<char, u16>,
    font_name: &str,
) -> Vec<u8> {
    let mut out = b"q\n".to_vec();
    for (x0, y0, x1, y1, text) in words {
        let (w, h) = (x1 - x0, y1 - y0);
        #[allow(clippy::cast_precision_loss)]
        let chars = text.chars().count() as f64;
        let base_y = y1 - 0.2 * h;
        let (ox, oy) = draw::display_to_pdf(crop, rotation, *x0, base_y);
        let (rx, ry) = {
            let p = draw::display_to_pdf(crop, rotation, x0 + 1.0, base_y);
            (p.0 - ox, p.1 - oy)
        };
        let (ux, uy) = {
            let p = draw::display_to_pdf(crop, rotation, *x0, base_y - 1.0);
            (p.0 - ox, p.1 - oy)
        };
        // 1 文字 = 1 em（= フォントサイズ h）の自然幅を bbox 幅へ合わせる横スケール
        let sx = w / (chars * h);
        out.extend_from_slice(
            format!(
                "BT\n/{font_name} {} Tf\n3 Tr\n{} {} {} {} {} {} Tm\n<",
                draw::fmt(h),
                draw::fmt(sx * rx),
                draw::fmt(sx * ry),
                draw::fmt(ux),
                draw::fmt(uy),
                draw::fmt(ox),
                draw::fmt(oy),
            )
            .as_bytes(),
        );
        for ch in text.chars() {
            let cid = cid_map.get(&ch).copied().unwrap_or(0);
            out.extend_from_slice(format!("{cid:04X}").as_bytes());
        }
        out.extend_from_slice(b"> Tj\nET\n");
    }
    out.extend_from_slice(b"Q\n");
    out
}
