//! Bounded, copy-on-write preparation for simple-encoded page text replacement.

use std::collections::BTreeMap;

use lopdf::content::{Content, Operation};
use lopdf::{Dictionary, Document, Encoding, Object, StringFormat};

/// Keep resource lookup proportional to the page-content boundary.
const MAX_REPLACEMENT_FONTS: usize = 4096;
const MAX_REPLACEMENT_OPERAND_DEPTH: usize = 64;

#[derive(Debug)]
pub enum TextReplacementError {
    Pdf(lopdf::Error),
    OutputSize,
    OperandDepth,
    TooManyFonts,
}

impl From<lopdf::Error> for TextReplacementError {
    fn from(error: lopdf::Error) -> Self {
        Self::Pdf(error)
    }
}

fn checked_add(total: &mut usize, amount: usize, limit: usize) -> Result<(), TextReplacementError> {
    *total = total
        .checked_add(amount)
        .ok_or(TextReplacementError::OutputSize)?;
    if *total > limit {
        return Err(TextReplacementError::OutputSize);
    }
    Ok(())
}

fn encoded_object_upper_bound(
    object: &Object,
    total: &mut usize,
    limit: usize,
) -> Result<(), TextReplacementError> {
    let mut pending = vec![(object, 0usize)];
    while let Some((object, depth)) = pending.pop() {
        if depth > MAX_REPLACEMENT_OPERAND_DEPTH {
            return Err(TextReplacementError::OperandDepth);
        }
        match object {
            Object::Null => checked_add(total, 4, limit)?,
            Object::Boolean(_) => checked_add(total, 5, limit)?,
            Object::Integer(_) => checked_add(total, 20, limit)?,
            Object::Real(_) => checked_add(total, 64, limit)?,
            Object::Name(name) => checked_add(
                total,
                1usize
                    .checked_add(
                        name.len()
                            .checked_mul(3)
                            .ok_or(TextReplacementError::OutputSize)?,
                    )
                    .ok_or(TextReplacementError::OutputSize)?,
                limit,
            )?,
            Object::String(bytes, StringFormat::Literal) => checked_add(
                total,
                2usize
                    .checked_add(
                        bytes
                            .len()
                            .checked_mul(2)
                            .ok_or(TextReplacementError::OutputSize)?,
                    )
                    .ok_or(TextReplacementError::OutputSize)?,
                limit,
            )?,
            Object::String(bytes, StringFormat::Hexadecimal) => checked_add(
                total,
                2usize
                    .checked_add(
                        bytes
                            .len()
                            .checked_mul(2)
                            .ok_or(TextReplacementError::OutputSize)?,
                    )
                    .ok_or(TextReplacementError::OutputSize)?,
                limit,
            )?,
            Object::Array(items) => {
                checked_add(total, 2, limit)?;
                checked_add(total, items.len(), limit)?;
                pending.extend(items.iter().map(|item| (item, depth + 1)));
            }
            Object::Dictionary(dictionary) => {
                checked_add(total, 4, limit)?;
                for (key, value) in dictionary {
                    checked_add(
                        total,
                        2usize
                            .checked_add(
                                key.len()
                                    .checked_mul(3)
                                    .ok_or(TextReplacementError::OutputSize)?,
                            )
                            .ok_or(TextReplacementError::OutputSize)?,
                        limit,
                    )?;
                    pending.push((value, depth + 1));
                }
            }
            Object::Stream(stream) => {
                checked_add(total, 21, limit)?;
                checked_add(total, stream.content.len(), limit)?;
                for (key, value) in &stream.dict {
                    checked_add(
                        total,
                        2usize
                            .checked_add(
                                key.len()
                                    .checked_mul(3)
                                    .ok_or(TextReplacementError::OutputSize)?,
                            )
                            .ok_or(TextReplacementError::OutputSize)?,
                        limit,
                    )?;
                    pending.push((value, depth + 1));
                }
            }
            Object::Reference(_) => checked_add(total, 40, limit)?,
        }
    }
    Ok(())
}

fn content_encoded_upper_bound(
    content: &Content<Vec<Operation>>,
    limit: usize,
) -> Result<(), TextReplacementError> {
    let mut total = 0usize;
    for (index, operation) in content.operations.iter().enumerate() {
        if index > 0 {
            checked_add(&mut total, 1, limit)?;
        }
        for operand in &operation.operands {
            encoded_object_upper_bound(operand, &mut total, limit)?;
            checked_add(&mut total, 1, limit)?;
        }
        checked_add(&mut total, operation.operator.len(), limit)?;
    }
    Ok(())
}

fn encode_with_fallback(encoding: &Encoding<'_>, text: &str, default_char: &str) -> Vec<u8> {
    let fallback = Document::encode_text(encoding, default_char);
    let mut result = Vec::with_capacity(text.len());
    for character in text.chars() {
        let mut buffer = [0u8; 4];
        let character = character.encode_utf8(&mut buffer);
        let encoded = Document::encode_text(encoding, character);
        if encoded.is_empty() {
            result.extend_from_slice(&fallback);
        } else {
            result.extend_from_slice(&encoded);
        }
    }
    result
}

fn replace_string(
    bytes: &mut Vec<u8>,
    encoding: &Encoding<'_>,
    search: &str,
    replacement: &str,
    default_char: &str,
    max_size: Option<usize>,
) -> Result<usize, TextReplacementError> {
    let decoded = Document::decode_text(encoding, bytes)?;
    let count = decoded.matches(search).count();
    if count == 0 {
        return Ok(0);
    }

    if let Some(limit) = max_size {
        let removed = count
            .checked_mul(search.len())
            .ok_or(TextReplacementError::OutputSize)?;
        let added = count
            .checked_mul(replacement.len())
            .ok_or(TextReplacementError::OutputSize)?;
        let output_len = decoded
            .len()
            .checked_sub(removed)
            .and_then(|value| value.checked_add(added))
            .ok_or(TextReplacementError::OutputSize)?;
        if output_len > limit {
            return Err(TextReplacementError::OutputSize);
        }
    }

    let updated = decoded.replace(search, replacement);
    *bytes = encode_with_fallback(encoding, &updated, default_char);
    Ok(count)
}

fn replace_operation(
    operation: &mut Operation,
    encoding: &Encoding<'_>,
    search: &str,
    replacement: &str,
    default_char: &str,
    max_size: Option<usize>,
) -> Result<usize, TextReplacementError> {
    let mut count = 0usize;
    for operand in &mut operation.operands {
        match operand {
            Object::String(bytes, _) => {
                count = count
                    .checked_add(replace_string(
                        bytes,
                        encoding,
                        search,
                        replacement,
                        default_char,
                        max_size,
                    )?)
                    .ok_or(TextReplacementError::OutputSize)?;
            }
            Object::Array(items) => {
                for item in items {
                    if let Object::String(bytes, _) = item {
                        count = count
                            .checked_add(replace_string(
                                bytes,
                                encoding,
                                search,
                                replacement,
                                default_char,
                                max_size,
                            )?)
                            .ok_or(TextReplacementError::OutputSize)?;
                    }
                }
            }
            _ => {}
        }
    }
    Ok(count)
}

fn page_encodings<'a>(
    doc: &'a Document,
    page: &'a Dictionary,
    max_size: Option<usize>,
) -> Result<BTreeMap<Vec<u8>, Encoding<'a>>, TextReplacementError> {
    let Some(resources) = page.get(b"Resources").ok().and_then(|object| match object {
        Object::Dictionary(dictionary) => Some(dictionary),
        Object::Reference(id) => doc.get_dictionary(*id).ok(),
        _ => None,
    }) else {
        return Ok(BTreeMap::new());
    };
    let Some(fonts) = resources.get(b"Font").ok().and_then(|object| match object {
        Object::Dictionary(dictionary) => Some(dictionary),
        Object::Reference(id) => doc.get_dictionary(*id).ok(),
        _ => None,
    }) else {
        return Ok(BTreeMap::new());
    };
    if fonts.len() > MAX_REPLACEMENT_FONTS {
        return Err(TextReplacementError::TooManyFonts);
    }
    let per_font_limit = max_size.map(|limit| limit / fonts.len().max(1));
    fonts
        .iter()
        .filter_map(|(name, object)| {
            let font = match object {
                Object::Dictionary(dictionary) => Some(dictionary),
                Object::Reference(id) => doc.get_dictionary(*id).ok(),
                _ => None,
            }?;
            let encoding = match per_font_limit {
                Some(limit) => font.get_font_encoding_with_limit(doc, limit),
                None => font.get_font_encoding(doc),
            };
            Some(
                encoding
                    .map(|encoding| (name.clone(), encoding))
                    .map_err(TextReplacementError::from),
            )
        })
        .collect()
}

/// Prepare one replacement stream without mutating the source document.
///
/// The returned bytes are ready to install as a new, page-owned `/Contents`
/// stream. `None` means that no text matched and no mutation is needed.
pub fn prepare(
    doc: &Document,
    page: &Dictionary,
    content_data: &[u8],
    search: &str,
    replacement: &str,
    default_char: &str,
    max_size: Option<usize>,
) -> Result<Option<(usize, Vec<u8>)>, TextReplacementError> {
    let encodings = page_encodings(doc, page, max_size)?;
    let mut content = Content::decode(content_data)?;
    let mut current_encoding = None;
    let mut replacement_count = 0usize;

    for operation in &mut content.operations {
        match operation.operator.as_str() {
            "Tf" => {
                let font_name = operation
                    .operands
                    .first()
                    .ok_or_else(|| lopdf::Error::Syntax("missing font operand".to_owned()))?
                    .as_name()?;
                current_encoding = encodings.get(font_name);
            }
            "Tj" | "TJ" => {
                if let Some(encoding) = current_encoding {
                    replacement_count = replacement_count
                        .checked_add(replace_operation(
                            operation,
                            encoding,
                            search,
                            replacement,
                            default_char,
                            max_size,
                        )?)
                        .ok_or(TextReplacementError::OutputSize)?;
                }
            }
            _ => {}
        }
    }

    if replacement_count == 0 {
        return Ok(None);
    }
    content_encoded_upper_bound(&content, max_size.unwrap_or(usize::MAX))?;
    let encoded = content.encode()?;
    if max_size.is_some_and(|limit| encoded.len() > limit) {
        return Err(TextReplacementError::OutputSize);
    }
    Ok(Some((replacement_count, encoded)))
}
