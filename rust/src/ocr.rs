//! Invisible OCR text-layer primitives for searchable PDFs.
//!
//! Following ocrmypdf's approach, assign a CID to each character in a
//! non-embedded Identity-H font with a ToUnicode CMap, then write text in
//! invisible rendering mode (`Tr 3`). It appears only through extraction and
//! search, and adds almost no file size in any language.

use std::collections::BTreeMap;

use lopdf::{Dictionary, Document, Object, ObjectId, Stream, dictionary};

use crate::draw;

/// One OCR word: display-coordinate bbox plus text.
pub type OcrWord = (f64, f64, f64, f64, String);

/// Assign one-based CIDs to all characters; CID 0 is `.notdef`.
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

/// Build a CID-to-Unicode UTF-16BE ToUnicode CMap.
///
/// Split `bfchar` into specification-compliant blocks of 100 entries.
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

/// Add the Type0/CIDFontType2/FontDescriptor/ToUnicode set for the OCR layer.
///
/// No FontFile is embedded. Every CID uses DW=1000 (1 em); a horizontal text
/// matrix scale fits each word to its actual width.
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

/// Build drawing operators for invisible text (`Tr 3`).
///
/// For each word, font size equals bbox height; the baseline sits one 0.2
/// descent ratio above the bottom; horizontal scale fits the bbox width.
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
        // Scale the natural width of one em per character to the bbox width.
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
