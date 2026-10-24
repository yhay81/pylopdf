//! Font metrics that position text the way hayro-interpret 0.8 does.
//!
//! Advances are in glyph space normalized to 1000 units per em, as hayro's
//! `Font::code_advance` reports them, and character codes are read with
//! hayro's `Font::read_code`, so that the interpreter places every glyph
//! where hayro's extraction finds it. Where hayro would read a width from an
//! embedded font program, which this module does not parse, the metrics are
//! marked approximate instead of guessed silently.

use std::collections::HashMap;

use hayro::hayro_interpret::hayro_cmap::{self, CMap, CMapName, WritingMode};
use lopdf::{Dictionary, Document, Object, Stream};
use pdf_base14_metrics::Base14Font;

use super::ContentError;

/// Glyph-space units per em, as hayro normalizes every font.
pub(super) const UNITS_PER_EM: f64 = 1000.0;

/// Decode a stream's data under the caller's bounds.
pub(crate) type Decoder<'d> = dyn Fn(&Stream) -> Result<Vec<u8>, ContentError> + 'd;

/// Metrics of the font that a `Tf` operator selected.
#[derive(Debug)]
pub(crate) struct FontMetrics {
    kind: Kind,
    /// A box in glyph space, 1000 units per em, that the glyph's ink stays
    /// inside vertically: the descriptor's `/Descent` and `/Ascent`, or its
    /// `/FontBBox`, or 200 below and 800 above the baseline. A Type 3 font
    /// gives its `/FontBBox` through `/FontMatrix` for both axes.
    extent: [f64; 4],
    /// Whether some advance had to be guessed because it lives only in an
    /// embedded font program.
    approximate: bool,
}

#[derive(Debug)]
enum Kind {
    /// Type1, MMType1, and TrueType fonts: one byte per code.
    Simple(SimpleWidths),
    /// Type3 fonts: one byte per code, widths in glyph space scaled by the
    /// first coefficient of `/FontMatrix`.
    Type3 { widths: SimpleWidths, scale: f32 },
    /// Type0 fonts with a CMap and CID metrics.
    Composite(Box<Composite>),
}

#[derive(Debug)]
struct SimpleWidths {
    /// `Some(width)` for codes that `/Widths` covers; `None` for codes
    /// outside `/FirstChar`..`/LastChar`, which take `missing`.
    table: Option<Vec<Option<f32>>>,
    missing: f32,
    /// The standard font whose AFM widths apply when `/Widths` is absent.
    standard: Option<Base14Font>,
    /// Glyph names per code for the standard-font lookup.
    names: Box<[Option<String>; 256]>,
}

#[derive(Debug)]
struct Composite {
    cmap: CMap,
    horizontal: bool,
    dw: f32,
    widths: HashMap<u32, f32>,
    dw2: (f32, f32),
    widths2: HashMap<u32, [f32; 3]>,
}

impl FontMetrics {
    /// Read the metrics of a font dictionary, or `None` when hayro would not
    /// load the font either, in which case it falls back to Helvetica.
    pub(crate) fn read(doc: &Document, font: &Dictionary, decode: &Decoder<'_>) -> Option<Self> {
        let subtype = name_of(doc, font.get(b"Subtype").ok()?)?;
        match subtype {
            b"Type0" => Composite::read(doc, font, decode).map(|composite| Self {
                extent: composite_extent(doc, font),
                kind: Kind::Composite(Box::new(composite)),
                approximate: false,
            }),
            b"Type3" => {
                let matrix = font
                    .get(b"FontMatrix")
                    .ok()
                    .and_then(|matrix| number_array(doc, matrix));
                let scale = matrix
                    .as_ref()
                    .and_then(|values| values.first().copied())
                    .unwrap_or(0.001);
                let widths = SimpleWidths::read(doc, font, None);
                Some(Self {
                    extent: type3_extent(doc, font, matrix.as_deref()),
                    kind: Kind::Type3 { widths, scale },
                    approximate: false,
                })
            }
            b"Type1" | b"MMType1" | b"TrueType" => {
                let descriptor = dict_of(doc, font.get(b"FontDescriptor").ok());
                let embedded = descriptor.is_some_and(|descriptor| {
                    [b"FontFile".as_slice(), b"FontFile2", b"FontFile3"]
                        .iter()
                        .any(|key| descriptor.has(key))
                });
                let standard = if embedded {
                    None
                } else {
                    select_standard_font(doc, font, descriptor)
                };
                let widths = SimpleWidths::read(doc, font, standard);
                // Symbol and ZapfDingbats ignore the base encoding.
                let symbolic = matches!(
                    standard,
                    Some(Base14Font::Symbol | Base14Font::ZapfDingbats)
                );
                let approximate = widths.table.is_none()
                    && (standard.is_none() || !(symbolic || encoding_is_known(doc, font)));
                Some(Self {
                    extent: descriptor_extent(doc, descriptor),
                    kind: Kind::Simple(widths),
                    approximate,
                })
            }
            _ => None,
        }
    }

    /// The metrics hayro uses for a missing or unusable font: Helvetica with
    /// its built-in encoding, since hayro builds it from an empty dictionary.
    pub(crate) fn fallback() -> Self {
        let mut names: Box<[Option<String>; 256]> = Box::new(std::array::from_fn(|_| None));
        fill_from_afm(&mut names, Base14Font::Helvetica);
        Self {
            extent: DEFAULT_EXTENT,
            kind: Kind::Simple(SimpleWidths {
                table: None,
                missing: 0.0,
                standard: Some(Base14Font::Helvetica),
                names,
            }),
            approximate: false,
        }
    }

    pub(super) fn is_approximate(&self) -> bool {
        self.approximate
    }

    pub(super) fn is_horizontal(&self) -> bool {
        match &self.kind {
            Kind::Composite(composite) => composite.horizontal,
            Kind::Simple(_) | Kind::Type3 { .. } => true,
        }
    }

    /// Read one character code at `offset`, returning it and its length.
    pub(super) fn read_code(&self, bytes: &[u8], offset: usize) -> (u32, usize) {
        match &self.kind {
            Kind::Composite(composite) => {
                let mut code = 0_u32;
                let remaining = bytes.len() - offset;
                for n in 0..4.min(remaining) {
                    code = (code << 8) | u32::from(bytes[offset + n]);
                    if composite
                        .cmap
                        .lookup_cid_code(code, u8::try_from(n + 1).unwrap_or(u8::MAX))
                        .is_some()
                    {
                        return (code, n + 1);
                    }
                }
                (0, 1)
            }
            Kind::Simple(_) | Kind::Type3 { .. } => (u32::from(bytes[offset]), 1),
        }
    }

    /// The advance of a code in units of 1/1000 em, as `(x, y)`.
    pub(crate) fn advance(&self, code: u32) -> (f64, f64) {
        match &self.kind {
            Kind::Simple(widths) => (f64::from(widths.width(code)), 0.0),
            // hayro's Type3 `glyph_width`, in the same single precision.
            Kind::Type3 { widths, scale } => (
                f64::from(widths.width(code) * scale * UNITS_PER_EM as f32),
                0.0,
            ),
            Kind::Composite(composite) => composite.advance(code),
        }
    }

    /// The glyph's horizontal width in 1/1000 em, also for vertical writing,
    /// where it is the width of the column.
    pub(super) fn width(&self, code: u32) -> f64 {
        match &self.kind {
            Kind::Composite(composite) => {
                f64::from(composite.horizontal_width(composite.cid(code)))
            }
            Kind::Simple(_) | Kind::Type3 { .. } => self.advance(code).0,
        }
    }

    /// A box in glyph space that the glyph's ink stays inside, as
    /// `[x0, y0, x1, y1]`: from the origin to the glyph's width across, and
    /// the font's extent up and down.
    pub(super) fn glyph_box(&self, code: u32) -> [f64; 4] {
        let width = self.width(code);
        let [x0, y0, x1, y1] = self.extent;
        [x0.min(0.0).min(width), y0, x1.max(width).max(0.0), y1]
    }

    /// The vector from the text origin to the glyph origin, in 1/1000 em;
    /// nonzero only for vertical writing.
    pub(super) fn displacement(&self, code: u32) -> (f64, f64) {
        match &self.kind {
            Kind::Composite(composite) => composite.displacement(code),
            Kind::Simple(_) | Kind::Type3 { .. } => (0.0, 0.0),
        }
    }
}

/// The vertical extent that a font without usable metrics gets.
const DEFAULT_EXTENT: [f64; 4] = [0.0, -200.0, 0.0, 800.0];

/// The extent of a simple or descendant font from its descriptor.
fn descriptor_extent(doc: &Document, descriptor: Option<&Dictionary>) -> [f64; 4] {
    let Some(descriptor) = descriptor else {
        return DEFAULT_EXTENT;
    };
    let value = |key: &[u8]| {
        descriptor
            .get(key)
            .ok()
            .and_then(|value| number(doc, value))
            .map(f64::from)
            .filter(|value| value.is_finite())
    };
    if let (Some(ascent), Some(descent)) = (value(b"Ascent"), value(b"Descent"))
        && ascent > descent
    {
        return [0.0, descent.min(0.0), 0.0, ascent.max(0.0)];
    }
    if let Some([_, y0, _, y1]) = descriptor
        .get(b"FontBBox")
        .ok()
        .and_then(|value| number_array(doc, value))
        .and_then(|values| <[f32; 4]>::try_from(values).ok())
        .map(|values| values.map(f64::from))
        && y1.max(y0) > y1.min(y0)
        && y0.is_finite()
        && y1.is_finite()
    {
        return [0.0, y0.min(y1).min(0.0), 0.0, y0.max(y1).max(0.0)];
    }
    DEFAULT_EXTENT
}

fn composite_extent(doc: &Document, font: &Dictionary) -> [f64; 4] {
    let descriptor = font
        .get(b"DescendantFonts")
        .ok()
        .and_then(|value| array_of(doc, value))
        .and_then(|fonts| fonts.iter().find_map(|value| dict_of(doc, Some(value))))
        .and_then(|descendant| dict_of(doc, descendant.get(b"FontDescriptor").ok()));
    descriptor_extent(doc, descriptor)
}

/// A Type 3 font's `/FontBBox` mapped by `/FontMatrix` into 1000 units per
/// em; its glyph procedures may paint anywhere inside it.
fn type3_extent(doc: &Document, font: &Dictionary, matrix: Option<&[f32]>) -> [f64; 4] {
    let bbox = font
        .get(b"FontBBox")
        .ok()
        .and_then(|value| number_array(doc, value))
        .and_then(|values| <[f32; 4]>::try_from(values).ok());
    let [a, b, c, d] = match matrix {
        Some([a, b, c, d, ..]) => [*a, *b, *c, *d].map(f64::from),
        _ => [0.001, 0.0, 0.0, 0.001],
    };
    let Some([x0, y0, x1, y1]) = bbox.map(|values| values.map(f64::from)) else {
        return DEFAULT_EXTENT;
    };
    let corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)].map(|(x, y)| {
        (
            (a * x + c * y) * UNITS_PER_EM,
            (b * x + d * y) * UNITS_PER_EM,
        )
    });
    let min_x = corners.iter().map(|p| p.0).fold(f64::INFINITY, f64::min);
    let max_x = corners
        .iter()
        .map(|p| p.0)
        .fold(f64::NEG_INFINITY, f64::max);
    let min_y = corners.iter().map(|p| p.1).fold(f64::INFINITY, f64::min);
    let max_y = corners
        .iter()
        .map(|p| p.1)
        .fold(f64::NEG_INFINITY, f64::max);
    if !(min_x.is_finite() && max_x.is_finite() && min_y.is_finite() && max_y.is_finite())
        || max_y <= min_y
    {
        return DEFAULT_EXTENT;
    }
    [
        min_x.min(0.0),
        min_y.min(0.0),
        max_x.max(0.0),
        max_y.max(0.0),
    ]
}

impl SimpleWidths {
    fn read(doc: &Document, font: &Dictionary, standard: Option<Base14Font>) -> Self {
        let descriptor = dict_of(doc, font.get(b"FontDescriptor").ok());
        let missing = descriptor
            .and_then(|descriptor| descriptor.get(b"MissingWidth").ok())
            .and_then(|value| number(doc, value))
            .unwrap_or(0.0);
        let table = (|| {
            let first = usize::try_from(integer(doc, font.get(b"FirstChar").ok()?)?).ok()?;
            let last = usize::try_from(integer(doc, font.get(b"LastChar").ok()?)?).ok()?;
            let widths = array_of(doc, font.get(b"Widths").ok()?)?;
            let count = last.checked_sub(first)?.checked_add(1)?;
            let mut table = vec![None; first.min(256)];
            for value in widths.iter().take(count) {
                if table.len() > 256 {
                    break;
                }
                // hayro's typed iteration stops at the first entry that is
                // not a number.
                let Some(width) = number(doc, value) else {
                    break;
                };
                table.push(Some(width));
            }
            Some(table)
        })();
        let names = if standard.is_some() {
            glyph_names(doc, font, standard)
        } else {
            Box::new(std::array::from_fn(|_| None))
        };
        Self {
            table,
            missing,
            standard,
            names,
        }
    }

    fn width(&self, code: u32) -> f32 {
        let Ok(index) = usize::try_from(code) else {
            return self.missing;
        };
        if let Some(table) = &self.table {
            return table.get(index).copied().flatten().unwrap_or(self.missing);
        }
        let Some(standard) = self.standard else {
            return 0.0;
        };
        self.names
            .get(index)
            .and_then(Option::as_deref)
            .and_then(|name| standard_width(standard, name))
            .unwrap_or(0.0)
    }
}

/// A standard font's width for a glyph name, as hayro's `get_width` reads
/// it: `.notdef` is 250 units wide, and `nbspace` and `sfthyphen` take the
/// widths of `space` and `hyphen`.
fn standard_width(font: Base14Font, name: &str) -> Option<f32> {
    let name = match name {
        ".notdef" => return Some(250.0),
        "nbspace" => "space",
        "sfthyphen" => "hyphen",
        other => other,
    };
    font.glyph_width(name)
}

impl Composite {
    fn read(doc: &Document, font: &Dictionary, decode: &Decoder<'_>) -> Option<Self> {
        let cmap = read_cmap(doc, font.get(b"Encoding").ok()?, decode)?;
        let horizontal = cmap.metadata().writing_mode != Some(WritingMode::Vertical);
        let descendants = array_of(doc, font.get(b"DescendantFonts").ok()?)?;
        let descendant = descendants
            .iter()
            .find_map(|value| dict_of(doc, Some(value)))?;
        let dw = descendant
            .get(b"DW")
            .ok()
            .and_then(|value| number(doc, value))
            .unwrap_or(1000.0);
        let dw2 = descendant
            .get(b"DW2")
            .ok()
            .and_then(|value| number_array(doc, value))
            .filter(|values| values.len() >= 2)
            .map_or((880.0, -1000.0), |values| (values[0], values[1]));
        let widths = descendant
            .get(b"W")
            .ok()
            .and_then(|value| array_of(doc, value))
            .map(|array| read_cid_widths(doc, array))
            .unwrap_or_default();
        let widths2 = descendant
            .get(b"W2")
            .ok()
            .and_then(|value| array_of(doc, value))
            .map(|array| read_cid_widths2(doc, array))
            .unwrap_or_default();
        Some(Self {
            cmap,
            horizontal,
            dw,
            widths,
            dw2,
            widths2,
        })
    }

    fn cid(&self, code: u32) -> u32 {
        // hayro's `code_to_cid` tries every byte length from one up.
        (1..=4)
            .find_map(|length| self.cmap.lookup_cid_code(code, length))
            .unwrap_or(0)
    }

    fn horizontal_width(&self, cid: u32) -> f32 {
        self.widths.get(&cid).copied().unwrap_or(self.dw)
    }

    fn advance(&self, code: u32) -> (f64, f64) {
        let cid = self.cid(code);
        if self.horizontal {
            (f64::from(self.horizontal_width(cid)), 0.0)
        } else if let Some([w, _, _]) = self.widths2.get(&cid) {
            (0.0, f64::from(*w))
        } else {
            (0.0, f64::from(self.dw2.1))
        }
    }

    fn displacement(&self, code: u32) -> (f64, f64) {
        let cid = self.cid(code);
        if self.horizontal {
            (0.0, 0.0)
        } else if let Some([_, v1, v2]) = self.widths2.get(&cid) {
            (-f64::from(*v1), -f64::from(*v2))
        } else {
            (
                -f64::from(self.horizontal_width(cid)) / 2.0,
                -f64::from(self.dw2.0),
            )
        }
    }
}

fn read_cmap(doc: &Document, encoding: &Object, decode: &Decoder<'_>) -> Option<CMap> {
    let encoding = resolve(doc, encoding)?;
    match encoding {
        Object::Name(name) => named_cmap(name),
        Object::Stream(stream) => {
            let data = decode(stream).ok()?;
            CMap::parse(&data, hayro_cmap::load_embedded)
        }
        _ => None,
    }
}

fn named_cmap(name: &[u8]) -> Option<CMap> {
    match name {
        b"Identity-H" => Some(CMap::identity_h()),
        b"Identity-V" => Some(CMap::identity_v()),
        _ => {
            let data = hayro_cmap::load_embedded(CMapName::from_bytes(name))?;
            CMap::parse(data, hayro_cmap::load_embedded)
        }
    }
}

/// Read `/W`: `c [w1 w2 ...]` and `c_first c_last w` entries.
fn read_cid_widths(doc: &Document, array: &[Object]) -> HashMap<u32, f32> {
    let mut widths = HashMap::new();
    let mut index = 0;
    while index < array.len() {
        let Some(first) = integer(doc, &array[index]).and_then(|v| u32::try_from(v).ok()) else {
            break;
        };
        match array.get(index + 1).and_then(|value| resolve(doc, value)) {
            Some(Object::Array(range)) => {
                let mut cid = first;
                for value in range {
                    let Some(width) = number(doc, value) else {
                        break;
                    };
                    widths.insert(cid, width);
                    let Some(next) = cid.checked_add(1) else {
                        break;
                    };
                    cid = next;
                }
                index += 2;
            }
            Some(_) => {
                let last = array
                    .get(index + 1)
                    .and_then(|value| integer(doc, value))
                    .and_then(|v| u32::try_from(v).ok());
                let width = array.get(index + 2).and_then(|value| number(doc, value));
                let (Some(last), Some(width)) = (last, width) else {
                    break;
                };
                // Bound the expansion like the CID space itself.
                for cid in first..=last.min(first.saturating_add(0xFFFF)) {
                    widths.insert(cid, width);
                }
                index += 3;
            }
            None => break,
        }
    }
    widths
}

/// Read `/W2`: `c [w1y v1x v1y ...]` and `c_first c_last w1y v1x v1y`.
fn read_cid_widths2(doc: &Document, array: &[Object]) -> HashMap<u32, [f32; 3]> {
    let mut widths = HashMap::new();
    let mut index = 0;
    while index < array.len() {
        let Some(first) = integer(doc, &array[index]).and_then(|v| u32::try_from(v).ok()) else {
            break;
        };
        match array.get(index + 1).and_then(|value| resolve(doc, value)) {
            Some(Object::Array(range)) => {
                let mut cid = first;
                let values: Vec<f32> = range.iter().map_while(|value| number(doc, value)).collect();
                for chunk in values.as_chunks::<3>().0 {
                    widths.insert(cid, *chunk);
                    let Some(next) = cid.checked_add(1) else {
                        break;
                    };
                    cid = next;
                }
                index += 2;
            }
            Some(_) => {
                let rest: Vec<f32> = array[index + 1..]
                    .iter()
                    .take(4)
                    .map_while(|value| number(doc, value))
                    .collect();
                let [last, w, v1, v2] = rest[..] else {
                    break;
                };
                let Some(last) = float_to_u32(last) else {
                    break;
                };
                for cid in first..=last.min(first.saturating_add(0xFFFF)) {
                    widths.insert(cid, [w, v1, v2]);
                }
                index += 5;
            }
            None => break,
        }
    }
    widths
}

fn float_to_u32(value: f32) -> Option<u32> {
    (value >= 0.0 && value <= u32::MAX as f32 && value.fract() == 0.0).then_some(value as u32)
}

/// Choose the standard font that hayro substitutes for a non-embedded
/// simple font, following `select_standard_font` in hayro-interpret 0.8.
fn select_standard_font(
    doc: &Document,
    font: &Dictionary,
    descriptor: Option<&Dictionary>,
) -> Option<Base14Font> {
    let base = name_of(doc, font.get(b"BaseFont").ok()?)?;
    let base = std::str::from_utf8(base).ok()?;
    let name = strip_subset_prefix(base);
    if let Some(font) = crate::draw::base14_font(name) {
        return Some(font);
    }
    let lower = name.to_ascii_lowercase();
    let weight = descriptor
        .and_then(|d| d.get(b"FontWeight").ok())
        .and_then(|value| number(doc, value));
    let angle = descriptor
        .and_then(|d| d.get(b"ItalicAngle").ok())
        .and_then(|value| number(doc, value));
    let bold =
        weight.is_some_and(|w| w >= 700.0) || lower.contains("bold") || lower.contains("demi");
    let italic =
        angle.is_some_and(|a| a != 0.0) || lower.contains("italic") || lower.contains("oblique");
    let family = if lower.contains("helvetica") || lower.contains("arial") || lower.contains("sans")
    {
        0
    } else if lower.contains("courier") || lower.contains("mono") {
        1
    } else if lower.contains("times") || lower.contains("serif") {
        2
    } else if lower.contains("zapfdingbats") || lower.contains("dingbats") {
        return Some(Base14Font::ZapfDingbats);
    } else {
        return None;
    };
    Some(match (family, bold, italic) {
        (0, false, false) => Base14Font::Helvetica,
        (0, true, false) => Base14Font::HelveticaBold,
        (0, false, true) => Base14Font::HelveticaOblique,
        (0, true, true) => Base14Font::HelveticaBoldOblique,
        (1, false, false) => Base14Font::Courier,
        (1, true, false) => Base14Font::CourierBold,
        (1, false, true) => Base14Font::CourierOblique,
        (1, true, true) => Base14Font::CourierBoldOblique,
        (_, false, false) => Base14Font::TimesRoman,
        (_, true, false) => Base14Font::TimesBold,
        (_, false, true) => Base14Font::TimesItalic,
        (_, true, true) => Base14Font::TimesBoldItalic,
    })
}

fn strip_subset_prefix(name: &str) -> &str {
    match name.split_once('+') {
        Some((prefix, rest))
            if prefix.len() == 6 && prefix.bytes().all(|b| b.is_ascii_uppercase()) =>
        {
            rest
        }
        _ => name,
    }
}

/// `MacRomanEncoding` from ISO 32000-2 Annex D: code and glyph name.
const MAC_ROMAN: &[(u8, &str)] = &[
    (32, "space"),
    (33, "exclam"),
    (34, "quotedbl"),
    (35, "numbersign"),
    (36, "dollar"),
    (37, "percent"),
    (38, "ampersand"),
    (39, "quotesingle"),
    (40, "parenleft"),
    (41, "parenright"),
    (42, "asterisk"),
    (43, "plus"),
    (44, "comma"),
    (45, "hyphen"),
    (46, "period"),
    (47, "slash"),
    (48, "zero"),
    (49, "one"),
    (50, "two"),
    (51, "three"),
    (52, "four"),
    (53, "five"),
    (54, "six"),
    (55, "seven"),
    (56, "eight"),
    (57, "nine"),
    (58, "colon"),
    (59, "semicolon"),
    (60, "less"),
    (61, "equal"),
    (62, "greater"),
    (63, "question"),
    (64, "at"),
    (65, "A"),
    (66, "B"),
    (67, "C"),
    (68, "D"),
    (69, "E"),
    (70, "F"),
    (71, "G"),
    (72, "H"),
    (73, "I"),
    (74, "J"),
    (75, "K"),
    (76, "L"),
    (77, "M"),
    (78, "N"),
    (79, "O"),
    (80, "P"),
    (81, "Q"),
    (82, "R"),
    (83, "S"),
    (84, "T"),
    (85, "U"),
    (86, "V"),
    (87, "W"),
    (88, "X"),
    (89, "Y"),
    (90, "Z"),
    (91, "bracketleft"),
    (92, "backslash"),
    (93, "bracketright"),
    (94, "asciicircum"),
    (95, "underscore"),
    (96, "grave"),
    (97, "a"),
    (98, "b"),
    (99, "c"),
    (100, "d"),
    (101, "e"),
    (102, "f"),
    (103, "g"),
    (104, "h"),
    (105, "i"),
    (106, "j"),
    (107, "k"),
    (108, "l"),
    (109, "m"),
    (110, "n"),
    (111, "o"),
    (112, "p"),
    (113, "q"),
    (114, "r"),
    (115, "s"),
    (116, "t"),
    (117, "u"),
    (118, "v"),
    (119, "w"),
    (120, "x"),
    (121, "y"),
    (122, "z"),
    (123, "braceleft"),
    (124, "bar"),
    (125, "braceright"),
    (126, "asciitilde"),
    (128, "Adieresis"),
    (129, "Aring"),
    (130, "Ccedilla"),
    (131, "Eacute"),
    (132, "Ntilde"),
    (133, "Odieresis"),
    (134, "Udieresis"),
    (135, "aacute"),
    (136, "agrave"),
    (137, "acircumflex"),
    (138, "adieresis"),
    (139, "atilde"),
    (140, "aring"),
    (141, "ccedilla"),
    (142, "eacute"),
    (143, "egrave"),
    (144, "ecircumflex"),
    (145, "edieresis"),
    (146, "iacute"),
    (147, "igrave"),
    (148, "icircumflex"),
    (149, "idieresis"),
    (150, "ntilde"),
    (151, "oacute"),
    (152, "ograve"),
    (153, "ocircumflex"),
    (154, "odieresis"),
    (155, "otilde"),
    (156, "uacute"),
    (157, "ugrave"),
    (158, "ucircumflex"),
    (159, "udieresis"),
    (160, "dagger"),
    (161, "degree"),
    (162, "cent"),
    (163, "sterling"),
    (164, "section"),
    (165, "bullet"),
    (166, "paragraph"),
    (167, "germandbls"),
    (168, "registered"),
    (169, "copyright"),
    (170, "trademark"),
    (171, "acute"),
    (172, "dieresis"),
    (174, "AE"),
    (175, "Oslash"),
    (177, "plusminus"),
    (180, "yen"),
    (181, "mu"),
    (187, "ordfeminine"),
    (188, "ordmasculine"),
    (190, "ae"),
    (191, "oslash"),
    (192, "questiondown"),
    (193, "exclamdown"),
    (194, "logicalnot"),
    (196, "florin"),
    (199, "guillemotleft"),
    (200, "guillemotright"),
    (201, "ellipsis"),
    (202, "space"),
    (203, "Agrave"),
    (204, "Atilde"),
    (205, "Otilde"),
    (206, "OE"),
    (207, "oe"),
    (208, "endash"),
    (209, "emdash"),
    (210, "quotedblleft"),
    (211, "quotedblright"),
    (212, "quoteleft"),
    (213, "quoteright"),
    (214, "divide"),
    (216, "ydieresis"),
    (217, "Ydieresis"),
    (218, "fraction"),
    (219, "currency"),
    (220, "guilsinglleft"),
    (221, "guilsinglright"),
    (222, "fi"),
    (223, "fl"),
    (224, "daggerdbl"),
    (225, "periodcentered"),
    (226, "quotesinglbase"),
    (227, "quotedblbase"),
    (228, "perthousand"),
    (229, "Acircumflex"),
    (230, "Ecircumflex"),
    (231, "Aacute"),
    (232, "Edieresis"),
    (233, "Egrave"),
    (234, "Iacute"),
    (235, "Icircumflex"),
    (236, "Idieresis"),
    (237, "Igrave"),
    (238, "Oacute"),
    (239, "Ocircumflex"),
    (241, "Ograve"),
    (242, "Uacute"),
    (243, "Ucircumflex"),
    (244, "Ugrave"),
    (245, "dotlessi"),
    (246, "circumflex"),
    (247, "tilde"),
    (248, "macron"),
    (249, "breve"),
    (250, "dotaccent"),
    (251, "ring"),
    (252, "cedilla"),
    (253, "hungarumlaut"),
    (254, "ogonek"),
    (255, "caron"),
];

/// The Mac OS Roman glyphs that `MacRomanEncoding` lacks, which hayro uses
/// for those codes.
const MAC_OS_ROMAN_EXTRA: &[(u8, &str)] = &[
    (173, "notequal"),
    (176, "infinity"),
    (178, "lessequal"),
    (179, "greaterequal"),
    (182, "partialdiff"),
    (183, "summation"),
    (184, "product"),
    (185, "pi"),
    (186, "integral"),
    (189, "Omega"),
    (195, "radical"),
    (197, "approxequal"),
    (198, "Delta"),
    (215, "lozenge"),
    (240, "apple"),
];

fn fill_from_afm(names: &mut [Option<String>; 256], font: Base14Font) {
    for metric in font.metrics().character_metrics.iter() {
        if let Some(slot) = usize::try_from(metric.code)
            .ok()
            .and_then(|code| names.get_mut(code))
        {
            *slot = Some(metric.name.to_string());
        }
    }
}

/// Whether hayro's base encoding for a simple font is one this module
/// names: no `/Encoding`, a dictionary without `/BaseEncoding`, or
/// WinAnsi, MacRoman, or Standard.
fn encoding_is_known(doc: &Document, font: &Dictionary) -> bool {
    let base = match font
        .get(b"Encoding")
        .ok()
        .and_then(|value| resolve(doc, value))
    {
        Some(Object::Name(name)) => Some(name.as_slice()),
        Some(Object::Dictionary(dict)) => dict
            .get(b"BaseEncoding")
            .ok()
            .and_then(|value| name_of(doc, value)),
        _ => None,
    };
    matches!(
        base,
        None | Some(b"WinAnsiEncoding" | b"MacRomanEncoding" | b"StandardEncoding")
    )
}

/// Glyph names per code from `/Encoding`, its `/Differences`, and the
/// standard font's built-in encoding.
fn glyph_names(
    doc: &Document,
    font: &Dictionary,
    standard: Option<Base14Font>,
) -> Box<[Option<String>; 256]> {
    let mut names: Box<[Option<String>; 256]> = Box::new(std::array::from_fn(|_| None));
    let encoding = font
        .get(b"Encoding")
        .ok()
        .and_then(|value| resolve(doc, value));
    let (mut base, differences) = match encoding {
        Some(Object::Name(name)) => (Some(name.as_slice()), None),
        Some(Object::Dictionary(dict)) => (
            dict.get(b"BaseEncoding")
                .ok()
                .and_then(|value| name_of(doc, value)),
            dict.get(b"Differences")
                .ok()
                .and_then(|value| array_of(doc, value)),
        ),
        _ => (None, None),
    };
    // hayro ignores the base encoding of a non-embedded font drawn as Symbol
    // or ZapfDingbats (pdf.js issue 16464), keeping only `/Differences`.
    if matches!(
        standard,
        Some(Base14Font::Symbol | Base14Font::ZapfDingbats)
    ) {
        base = None;
    }
    match base {
        // hayro's `Encoding::map_code` names code 0 `.notdef` in every table
        // encoding, and names every unmapped WinAnsi code above 40 `bullet`.
        Some(b"WinAnsiEncoding") => {
            for (code, slot) in names.iter_mut().enumerate() {
                let code = u8::try_from(code).unwrap_or(u8::MAX);
                *slot = pdf_base14_metrics::winansi_glyph_name(code)
                    .or((code > 40).then_some("bullet"))
                    .map(str::to_owned);
            }
            names[0] = Some(".notdef".to_owned());
        }
        // The PDF table first, then the rest of Mac OS Roman, as hayro does.
        Some(b"MacRomanEncoding") => {
            for (code, name) in MAC_OS_ROMAN_EXTRA.iter().chain(MAC_ROMAN) {
                names[usize::from(*code)] = Some((*name).to_owned());
            }
            names[0] = Some(".notdef".to_owned());
        }
        // StandardEncoding, which the `C` fields of a Latin AFM record.
        Some(b"StandardEncoding") => {
            fill_from_afm(&mut names, Base14Font::Helvetica);
            names[0] = Some(".notdef".to_owned());
        }
        // The font's built-in encoding: StandardEncoding for the text fonts
        // and their own codes for Symbol and ZapfDingbats.
        None => {
            if let Some(standard) = standard {
                fill_from_afm(&mut names, standard);
            }
        }
        // MacExpertEncoding and unknown names: widths stay unresolved, and
        // `encoding_is_known` marks the metrics approximate.
        Some(_) => {}
    }
    if let Some(differences) = differences {
        let mut code: Option<usize> = None;
        for value in differences {
            match resolve(doc, value) {
                Some(Object::Integer(next)) => code = usize::try_from(*next).ok(),
                Some(Object::Name(name)) => {
                    if let Some(current) = code {
                        if let Some(slot) = names.get_mut(current) {
                            *slot = std::str::from_utf8(name).ok().map(str::to_owned);
                        }
                        code = current.checked_add(1);
                    }
                }
                _ => {}
            }
        }
    }
    names
}

fn resolve<'a>(doc: &'a Document, value: &'a Object) -> Option<&'a Object> {
    let mut current = value;
    for _ in 0..32 {
        match current {
            Object::Reference(id) => current = doc.get_object(*id).ok()?,
            other => return Some(other),
        }
    }
    None
}

fn name_of<'a>(doc: &'a Document, value: &'a Object) -> Option<&'a [u8]> {
    match resolve(doc, value)? {
        Object::Name(name) => Some(name),
        _ => None,
    }
}

pub(super) fn dict_of<'a>(doc: &'a Document, value: Option<&'a Object>) -> Option<&'a Dictionary> {
    match resolve(doc, value?)? {
        Object::Dictionary(dict) => Some(dict),
        Object::Stream(stream) => Some(&stream.dict),
        _ => None,
    }
}

fn array_of<'a>(doc: &'a Document, value: &'a Object) -> Option<&'a [Object]> {
    match resolve(doc, value)? {
        Object::Array(array) => Some(array),
        _ => None,
    }
}

pub(super) fn number(doc: &Document, value: &Object) -> Option<f32> {
    match resolve(doc, value)? {
        Object::Integer(value) => Some(*value as f32),
        Object::Real(value) => Some(*value),
        _ => None,
    }
}

fn integer(doc: &Document, value: &Object) -> Option<i64> {
    match resolve(doc, value)? {
        Object::Integer(value) => Some(*value),
        Object::Real(value) if value.fract() == 0.0 => Some(*value as i64),
        _ => None,
    }
}

fn number_array(doc: &Document, value: &Object) -> Option<Vec<f32>> {
    let array = array_of(doc, value)?;
    array.iter().map(|value| number(doc, value)).collect()
}
