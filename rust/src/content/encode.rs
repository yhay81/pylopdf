//! Encoding text in a font's own codes, for text editing.
//!
//! A font draws a character when the page already shows it with a code of
//! that font, when the font's `/ToUnicode` CMap maps a code to it (producers
//! write those for the glyphs a subset holds), when a predefined UCS-2 or
//! UTF-16 CMap encodes it directly, or, for a simple font whose program is
//! not embedded, when its encoding names it, since the reader then draws a
//! complete standard font. Every code is checked against the font's own
//! code reading, so a code is never split or merged with its neighbour.

use std::collections::HashMap;

use hayro::hayro_interpret::hayro_cmap::{self, BfString, CMap};
use lopdf::{Dictionary, Document, Object};

use super::fonts::{Decoder, FontMetrics};
use crate::redact::resolve;

/// Characters to codes for one font.
pub(crate) struct TextEncoder {
    codes: HashMap<String, Vec<u8>>,
    /// The longest text that one code stands for, in characters.
    longest: usize,
}

impl TextEncoder {
    /// Build the encoder of one font; `observed` lists the texts and codes
    /// that the page already draws with it.
    pub(crate) fn new(
        doc: &Document,
        font: Option<&Dictionary>,
        metrics: &FontMetrics,
        observed: &[(String, Vec<u8>)],
        decode: &Decoder<'_>,
    ) -> Self {
        let mut encoder = Self {
            codes: HashMap::new(),
            longest: 1,
        };
        let valid = |bytes: &[u8]| metrics.read_code(bytes, 0).1 == bytes.len();
        for (text, code) in observed {
            if valid(code) {
                encoder.add(text.clone(), code.clone());
            }
        }
        let Some(font) = font else {
            return encoder;
        };
        let composite = matches!(font.get(b"Subtype"), Ok(Object::Name(name)) if name == b"Type0");
        if let Some(cmap) = font
            .get(b"ToUnicode")
            .ok()
            .and_then(|value| resolve(doc, value))
            .and_then(|value| value.as_stream().ok())
            .and_then(|stream| decode(stream).ok())
            .and_then(|data| CMap::parse(&data, hayro_cmap::load_embedded))
        {
            let last: u32 = if composite { 0xFFFF } else { 0xFF };
            for code in 0..=last {
                let bytes = if composite {
                    vec![(code >> 8) as u8, code as u8]
                } else {
                    vec![code as u8]
                };
                if !valid(&bytes) || metrics.read_code(&bytes, 0).0 != code {
                    continue;
                }
                let text = match cmap.lookup_bf_string(code) {
                    Some(BfString::Char(char)) => char.to_string(),
                    Some(BfString::String(text)) => text,
                    None => continue,
                };
                encoder.add(text, bytes);
            }
        }
        if composite {
            // UniJIS-UCS2-H and the other UCS-2 and UTF-16 CMaps take the
            // character's UTF-16 code units as its code.
            let unicode = font
                .get(b"Encoding")
                .ok()
                .and_then(|value| resolve(doc, value))
                .and_then(|value| value.as_name().ok())
                .is_some_and(|name| {
                    name.starts_with(b"Uni")
                        && (contains(name, b"UCS2") || contains(name, b"UTF16"))
                });
            if unicode {
                for code in 0x20_u32..=0xFFFF {
                    let Some(char) = char::from_u32(code) else {
                        continue;
                    };
                    let bytes = vec![(code >> 8) as u8, code as u8];
                    if valid(&bytes) {
                        encoder.codes.entry(char.to_string()).or_insert(bytes);
                    }
                }
            }
            return encoder;
        }
        let embedded = font
            .get(b"FontDescriptor")
            .ok()
            .and_then(|value| resolve(doc, value))
            .and_then(|value| value.as_dict().ok())
            .is_some_and(|descriptor| {
                [b"FontFile".as_slice(), b"FontFile2", b"FontFile3"]
                    .iter()
                    .any(|key| descriptor.has(key))
            });
        let type3 = matches!(font.get(b"Subtype"), Ok(Object::Name(name)) if name == b"Type3");
        if !embedded
            && !type3
            && let Ok(encoding) = font.get_font_encoding(doc)
        {
            for code in 0_u8..=255 {
                if let Ok(text) = encoding.bytes_to_string(&[code])
                    && !text.is_empty()
                    && text != "\u{fffd}"
                {
                    encoder.codes.entry(text).or_insert_with(|| vec![code]);
                }
            }
        }
        encoder
    }

    fn add(&mut self, text: String, code: Vec<u8>) {
        if text.is_empty() {
            return;
        }
        self.longest = self.longest.max(text.chars().count());
        self.codes.entry(text).or_insert(code);
    }

    /// Encode `text`, taking the longest text that one code stands for at
    /// each position; the first character that no code draws is the error.
    pub(crate) fn encode(&self, text: &str) -> Result<Vec<u8>, char> {
        let chars: Vec<char> = text.chars().collect();
        let mut out = Vec::new();
        let mut index = 0;
        while index < chars.len() {
            let mut found = false;
            for length in (1..=self.longest.min(chars.len() - index)).rev() {
                let piece: String = chars[index..index + length].iter().collect();
                if let Some(code) = self.codes.get(&piece) {
                    out.extend_from_slice(code);
                    index += length;
                    found = true;
                    break;
                }
            }
            if !found {
                return Err(chars[index]);
            }
        }
        Ok(out)
    }
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window == needle)
}

/// Codes to characters for one font, as text extraction reads them: its
/// `/ToUnicode` CMap first, then a predefined UCS-2 or UTF-16 CMap, then a
/// simple font's encoding.
pub(crate) struct TextDecoder {
    to_unicode: Option<CMap>,
    unicode_codes: bool,
    simple: Option<[Option<String>; 256]>,
}

impl TextDecoder {
    pub(crate) fn new(doc: &Document, font: Option<&Dictionary>, decode: &Decoder<'_>) -> Self {
        let mut decoder = Self {
            to_unicode: None,
            unicode_codes: false,
            simple: None,
        };
        let Some(font) = font else {
            return decoder;
        };
        decoder.to_unicode = font
            .get(b"ToUnicode")
            .ok()
            .and_then(|value| resolve(doc, value))
            .and_then(|value| value.as_stream().ok())
            .and_then(|stream| decode(stream).ok())
            .and_then(|data| CMap::parse(&data, hayro_cmap::load_embedded));
        let composite = matches!(font.get(b"Subtype"), Ok(Object::Name(name)) if name == b"Type0");
        if composite {
            decoder.unicode_codes = font
                .get(b"Encoding")
                .ok()
                .and_then(|value| resolve(doc, value))
                .and_then(|value| value.as_name().ok())
                .is_some_and(|name| {
                    name.starts_with(b"Uni")
                        && (contains(name, b"UCS2") || contains(name, b"UTF16"))
                });
        } else if let Ok(encoding) = font.get_font_encoding(doc) {
            decoder.simple = Some(std::array::from_fn(|code| {
                encoding
                    .bytes_to_string(&[code as u8])
                    .ok()
                    .filter(|text| !text.is_empty() && text != "\u{fffd}")
            }));
        }
        decoder
    }

    /// The text of one code, or `None` when the font gives none.
    pub(crate) fn text(&self, code: u32, len: usize) -> Option<String> {
        if let Some(cmap) = &self.to_unicode
            && let Some(text) = cmap.lookup_bf_string(code)
        {
            return Some(match text {
                BfString::Char(char) => char.to_string(),
                BfString::String(text) => text,
            });
        }
        if self.unicode_codes && len == 2 {
            return char::from_u32(code).map(|char| char.to_string());
        }
        self.simple
            .as_ref()
            .and_then(|table| usize::try_from(code).ok().and_then(|code| table.get(code)))
            .cloned()
            .flatten()
    }
}
