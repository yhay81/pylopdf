//! Info metadata, XMP reads, and the PDF/A claim.

use super::*;

/// Bound standard document Info metadata reads and writes.
pub(super) const INFO_METADATA_KEYS: [&[u8]; 9] = [
    b"Title",
    b"Author",
    b"Subject",
    b"Keywords",
    b"Creator",
    b"Producer",
    b"CreationDate",
    b"ModDate",
    b"Trapped",
];

/// The `/Trapped` values that ISO 32000-2 Table 349 writes as names.
const TRAPPED_NAMES: [&str; 3] = ["True", "False", "Unknown"];

pub(super) const MAX_INFO_METADATA_TEXT_BYTES: usize = 1024 * 1024;

/// Info strings, page count, version, encryption, and any cross-reference
/// recovery warning.
pub(super) type MetadataTuple = (
    HashMap<String, String>,
    u32,
    String,
    bool,
    Option<&'static str>,
);

/// Charge aggregate Info metadata text without overflow or partial output.
pub(super) fn add_info_metadata_budget(
    total: &mut usize,
    amount: usize,
    label: &str,
) -> PyResult<()> {
    *total = total
        .checked_add(amount)
        .ok_or_else(|| PdfError::new_err("Info metadata text exceeds the platform size limit"))?;
    if *total > MAX_INFO_METADATA_TEXT_BYTES {
        return Err(PdfError::new_err(format!(
            "Info metadata {label} exceeds the {MAX_INFO_METADATA_TEXT_BYTES}-byte safety limit"
        )));
    }
    Ok(())
}

pub(super) fn try_clone_info_metadata_text(value: &str, label: &str) -> PyResult<String> {
    let mut owned = String::new();
    owned.try_reserve_exact(value.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate Info metadata {label}: {error}"))
    })?;
    owned.push_str(value);
    Ok(owned)
}

pub(super) fn try_info_metadata_text_string(value: &str) -> PyResult<Object> {
    let encoded_len = pdf_text_string_len(value)
        .ok_or_else(|| PdfError::new_err("Info metadata text exceeds the platform size limit"))?;
    let mut encoded = Vec::new();
    encoded.try_reserve_exact(encoded_len).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate encoded Info metadata text: {error}"
        ))
    })?;
    if value.is_ascii() {
        encoded.extend_from_slice(value.as_bytes());
        Ok(Object::String(encoded, StringFormat::Literal))
    } else {
        encoded.extend_from_slice(&[0xfe, 0xff]);
        for unit in value.encode_utf16() {
            encoded.extend_from_slice(&unit.to_be_bytes());
        }
        Ok(Object::String(encoded, StringFormat::Hexadecimal))
    }
}

/// Return the trailer Info dictionary with one indirect reference resolved.
pub(super) fn info_dictionary(doc: &Document) -> Option<&Dictionary> {
    match doc.trailer.get(b"Info").ok()? {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok(),
        Object::Dictionary(dictionary) => Some(dictionary),
        _ => None,
    }
}

/// Decode only the eight public standard Info fields under aggregate budgets.
pub(super) fn collect_info_metadata(doc: &Document) -> PyResult<HashMap<String, String>> {
    let mut result = HashMap::new();
    let Some(info) = info_dictionary(doc) else {
        return Ok(result);
    };
    result
        .try_reserve(INFO_METADATA_KEYS.len())
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate returned Info metadata: {error}"
            ))
        })?;
    let mut source_bytes = 0usize;
    let mut returned_bytes = 0usize;
    for key in INFO_METADATA_KEYS {
        let Ok(value) = info.get(key) else {
            continue;
        };
        let resolved = deref_object(doc, value);
        let text = match resolved {
            Object::String(encoded, _) => {
                add_info_metadata_budget(&mut source_bytes, encoded.len(), "source text")?;
                let Ok(text) = decode_text_string(resolved) else {
                    continue;
                };
                text
            }
            // `/Trapped` is a name; other producers write it as a string.
            Object::Name(name) if key == b"Trapped" => {
                add_info_metadata_budget(&mut source_bytes, name.len(), "source text")?;
                let Ok(name) = std::str::from_utf8(name) else {
                    continue;
                };
                try_clone_info_metadata_text(name, "value")?
            }
            _ => continue,
        };
        add_info_metadata_budget(&mut returned_bytes, text.len(), "returned text")?;
        let key = std::str::from_utf8(key)
            .map_err(|error| PdfError::new_err(error.to_string()))
            .and_then(|key| try_clone_info_metadata_text(key, "key"))?;
        result.insert(key, text);
    }
    Ok(result)
}

/// Convert PdfMetadata to a bounded Info dict and structural facts.
pub(super) fn pdf_metadata_to_tuple(
    meta: PdfMetadata,
) -> PyResult<(HashMap<String, String>, u32, String, bool)> {
    let PdfMetadata {
        title,
        author,
        subject,
        keywords,
        creator,
        producer,
        creation_date,
        modification_date,
        custom,
        page_count,
        version,
        encrypted,
    } = meta;
    // pylopdf exposes only the standard fields. Release upstream custom clones
    // before materializing Python-facing strings. lopdf 0.45 drops `/Trapped`
    // from both, so the fast probe cannot report it.
    drop(custom);
    let mut map = HashMap::new();
    map.try_reserve(INFO_METADATA_KEYS.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate returned Info metadata: {error}"
        ))
    })?;
    let pairs = [
        ("Title", title),
        ("Author", author),
        ("Subject", subject),
        ("Keywords", keywords),
        ("Creator", creator),
        ("Producer", producer),
        ("CreationDate", creation_date),
        ("ModDate", modification_date),
    ];
    let mut returned_bytes = 0usize;
    for (key, value) in pairs {
        if let Some(v) = value {
            add_info_metadata_budget(&mut returned_bytes, v.len(), "returned text")?;
            let key = try_clone_info_metadata_text(key, "key")?;
            map.insert(key, v);
        }
    }
    Ok((map, page_count, version, encrypted))
}

/// Extract an XMP value from `key="v"` attributes or `<key>v</key>` elements.
pub(super) fn xmp_value(xmp: &str, key: &str) -> Option<String> {
    fn tag_end(xmp: &str, open: usize) -> Option<usize> {
        let mut quote = None;
        for (offset, character) in xmp[open + 1..].char_indices() {
            match (quote, character) {
                (None, '"' | '\'') => quote = Some(character),
                (Some(expected), actual) if actual == expected => quote = None,
                (None, '>') => return Some(open + 1 + offset),
                _ => {}
            }
        }
        None
    }

    fn tag_name_end(tag: &[u8], start: usize) -> usize {
        let mut end = start;
        while end < tag.len() && !tag[end].is_ascii_whitespace() && !matches!(tag[end], b'/' | b'=')
        {
            end += 1;
        }
        end
    }

    fn attribute_value(tag: &str, mut position: usize, key: &str) -> Option<String> {
        let bytes = tag.as_bytes();
        while position < bytes.len() {
            while position < bytes.len() && bytes[position].is_ascii_whitespace() {
                position += 1;
            }
            if position >= bytes.len() || bytes[position] == b'/' {
                return None;
            }
            let name_start = position;
            position = tag_name_end(bytes, position);
            if position == name_start {
                position += 1;
                continue;
            }
            let name = &tag[name_start..position];
            while position < bytes.len() && bytes[position].is_ascii_whitespace() {
                position += 1;
            }
            if position >= bytes.len() || bytes[position] != b'=' {
                continue;
            }
            position += 1;
            while position < bytes.len() && bytes[position].is_ascii_whitespace() {
                position += 1;
            }
            let Some(&delimiter @ (b'"' | b'\'')) = bytes.get(position) else {
                continue;
            };
            position += 1;
            let value_start = position;
            let value_end = bytes[position..]
                .iter()
                .position(|byte| *byte == delimiter)
                .map(|offset| position + offset)?;
            position = value_end + 1;
            if name == key {
                return Some(tag[value_start..value_end].trim().to_owned());
            }
        }
        None
    }

    let mut cursor = 0usize;
    while let Some(relative_open) = xmp[cursor..].find('<') {
        let open = cursor + relative_open;
        let rest = &xmp[open..];
        if rest.starts_with("<!--") {
            cursor = open + rest.find("-->")? + 3;
            continue;
        }
        if rest.starts_with("<![CDATA[") {
            cursor = open + rest.find("]]>")? + 3;
            continue;
        }
        if rest.starts_with("<?") {
            cursor = open + rest.find("?>")? + 2;
            continue;
        }
        let close = tag_end(xmp, open)?;
        let tag = &xmp[open + 1..close];
        let bytes = tag.as_bytes();
        let mut name_start = 0usize;
        while name_start < bytes.len() && bytes[name_start].is_ascii_whitespace() {
            name_start += 1;
        }
        if name_start >= bytes.len() || matches!(bytes[name_start], b'!' | b'/' | b'?') {
            cursor = close + 1;
            continue;
        }
        let name_end = tag_name_end(bytes, name_start);
        let name = &tag[name_start..name_end];
        if name == key && !tag.trim_end().ends_with('/') {
            let value = &xmp[close + 1..];
            let value_end = value.find('<')?;
            return Some(value[..value_end].trim().to_owned());
        }
        if let Some(value) = attribute_value(tag, name_end, key) {
            return Some(value);
        }
        cursor = close + 1;
    }
    None
}

impl _Document {
    /// Validate and apply standard Info metadata updates as one atomic batch.
    pub(super) fn set_metadata_entries(&mut self, entries: Vec<(String, String)>) -> PyResult<()> {
        if entries.is_empty() {
            return Ok(());
        }
        if entries.len() > INFO_METADATA_KEYS.len() {
            return Err(PdfError::new_err(format!(
                "cannot set more than {} standard Info metadata fields",
                INFO_METADATA_KEYS.len()
            )));
        }
        let mut seen = 0u16;
        let mut source_bytes = 0usize;
        let mut encoded_bytes = 0usize;
        let mut prepared = Vec::new();
        prepared.try_reserve_exact(entries.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate prepared Info metadata entries: {error}"
            ))
        })?;
        for (key, value) in entries {
            let Some(key_index) = INFO_METADATA_KEYS
                .iter()
                .position(|candidate| *candidate == key.as_bytes())
            else {
                return Err(PdfError::new_err(format!(
                    "unsupported Info metadata key: {key:?}"
                )));
            };
            let key_bit = 1u16 << key_index;
            if seen & key_bit != 0 {
                return Err(PdfError::new_err(format!(
                    "duplicate Info metadata key: {key:?}"
                )));
            }
            seen |= key_bit;
            add_input_text_budget(
                &mut source_bytes,
                value.len(),
                MAX_INFO_METADATA_TEXT_BYTES,
                "metadata_input_size",
                "Info metadata source text",
            )?;
            let encoded = if value.is_empty() {
                None
            } else if key == "Trapped" && TRAPPED_NAMES.contains(&value.as_str()) {
                Some(Object::Name(value.clone().into_bytes()))
            } else {
                let encoded_len = pdf_text_string_len(&value).ok_or_else(|| {
                    limit_err(
                        "metadata_input_size",
                        "Info metadata encoded text exceeds the platform text-size limit",
                    )
                })?;
                add_input_text_budget(
                    &mut encoded_bytes,
                    encoded_len,
                    MAX_INFO_METADATA_TEXT_BYTES,
                    "metadata_input_size",
                    "Info metadata encoded text",
                )?;
                Some(try_info_metadata_text_string(&value)?)
            };
            prepared.push((key.into_bytes(), encoded));
        }

        // An indirect null and a missing object read as an absent `/Info`, as
        // a null value does, so a new dictionary replaces the reference.
        let existing_id = match self.doc.trailer.get(b"Info") {
            Ok(Object::Reference(id)) => {
                match non_null_object(&self.doc, *id).map_err(to_py_err)? {
                    Some(info) => {
                        info.as_dict().map_err(to_py_err)?;
                        Some(*id)
                    }
                    None => None,
                }
            }
            _ => None,
        };
        if existing_id.is_none() {
            self.doc
                .max_id
                .checked_add(1)
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        }

        self.invalidate_hayro_pdf_keeping_placements();
        let info_id = match existing_id {
            Some(id) => id,
            None => {
                let existing = match self.doc.trailer.remove(b"Info") {
                    Some(Object::Dictionary(dictionary)) => dictionary,
                    _ => Dictionary::new(),
                };
                let id = self.doc.add_object(existing);
                self.doc.trailer.set("Info", id);
                id
            }
        };
        let info = self
            .doc
            .get_object_mut(info_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        for (key, value) in prepared {
            match value {
                Some(value) => info.set(key, value),
                None => {
                    info.remove(&key);
                }
            }
        }
        Ok(())
    }

    /// Remove the trailer's `/Info` entry, as pymupdf does for an empty
    /// metadata dictionary. The dictionary stays until pruning removes it.
    pub(super) fn remove_info_dictionary(&mut self) {
        if self.doc.trailer.has(b"Info") {
            self.invalidate_hayro_pdf_keeping_placements();
            self.doc.trailer.remove(b"Info");
        }
    }

    /// The Encrypt dictionary the document was loaded with, rebuilt from the
    /// decryption state, which lopdf keeps after it removes `/Encrypt`.
    fn original_encryption(&self) -> Option<Dictionary> {
        self.doc.encryption_state.as_ref()?.encode().ok()
    }

    /// Describe the loaded document's encryption as MuPDF's metadata does,
    /// for example `Standard V5 R6 256-bit AES`.
    pub(super) fn encryption_description(&self) -> Option<String> {
        let encryption = self.original_encryption()?;
        let integer = |key: &[u8]| encryption.get(key).and_then(Object::as_i64).ok();
        let version = integer(b"V").unwrap_or(0);
        let revision = integer(b"R").unwrap_or(0);
        let method = |filter_key: &[u8]| -> &'static str {
            if version < 4 {
                return "RC4";
            }
            let filter = encryption
                .get(filter_key)
                .and_then(Object::as_name)
                .unwrap_or(b"Identity");
            if filter == b"Identity" {
                return "None";
            }
            let method = encryption
                .get(b"CF")
                .and_then(Object::as_dict)
                .and_then(|filters| filters.get(filter))
                .and_then(Object::as_dict)
                .and_then(|filter| filter.get(b"CFM"))
                .and_then(Object::as_name);
            match method {
                Ok(b"V2") => "RC4",
                Ok(b"AESV2" | b"AESV3") => "AES",
                Ok(b"None") => "None",
                _ => "Unknown",
            }
        };
        let length = match version {
            5.. => 256,
            4 => 128,
            _ => integer(b"Length").unwrap_or(40),
        };
        let (streams, strings) = (method(b"StmF"), method(b"StrF"));
        Some(if streams == strings {
            format!("Standard V{version} R{revision} {length}-bit {strings}")
        } else {
            format!(
                "Standard V{version} R{revision} {length}-bit streams: {streams} strings: {strings}"
            )
        })
    }

    /// Check a password against the encryption the document was loaded with,
    /// returning whether it matches the user and the owner password.
    pub(super) fn original_password_matches(&self, password: &str) -> (bool, bool) {
        let Some(encryption) = self.original_encryption() else {
            return (false, false);
        };
        let mut probe = Document::new();
        let encryption_id = probe.add_object(encryption);
        probe.trailer.set("Encrypt", encryption_id);
        if let Ok(file_id) = self.doc.trailer.get(b"ID") {
            probe.trailer.set("ID", file_id.clone());
        }
        (
            probe.authenticate_user_password(password).is_ok(),
            probe.authenticate_owner_password(password).is_ok(),
        )
    }
}

/// Implements [`_Document::set_metadata`].
pub(super) fn set_metadata(this: &mut _Document, key: &str, value: &str) -> PyResult<()> {
    if !INFO_METADATA_KEYS.contains(&key.as_bytes()) {
        return Err(PdfError::new_err(format!(
            "unsupported Info metadata key: {key:?}"
        )));
    }
    let mut source_bytes = 0usize;
    add_input_text_budget(
        &mut source_bytes,
        value.len(),
        MAX_INFO_METADATA_TEXT_BYTES,
        "metadata_input_size",
        "Info metadata source text",
    )?;
    if !value.is_empty() {
        let encoded_len = pdf_text_string_len(value).ok_or_else(|| {
            limit_err(
                "metadata_input_size",
                "Info metadata encoded text exceeds the platform text-size limit",
            )
        })?;
        let mut encoded_bytes = 0usize;
        add_input_text_budget(
            &mut encoded_bytes,
            encoded_len,
            MAX_INFO_METADATA_TEXT_BYTES,
            "metadata_input_size",
            "Info metadata encoded text",
        )?;
    }
    let key = try_clone_info_metadata_text(key, "input key")?;
    let value = try_clone_info_metadata_text(value, "input value")?;
    let mut entries = Vec::new();
    entries.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate prepared Info metadata entries: {error}"
        ))
    })?;
    entries.push((key, value));
    this.set_metadata_entries(entries)
}

/// The catalog's `/PageMode` names (ISO 32000-2 Table 29).
const PAGE_MODES: [&str; 6] = [
    "UseNone",
    "UseOutlines",
    "UseThumbs",
    "FullScreen",
    "UseOC",
    "UseAttachments",
];

/// The catalog's `/PageLayout` names (ISO 32000-2 Table 29).
const PAGE_LAYOUTS: [&str; 6] = [
    "SinglePage",
    "OneColumn",
    "TwoColumnLeft",
    "TwoColumnRight",
    "TwoPageLeft",
    "TwoPageRight",
];

/// The catalog's XMP metadata stream, or `None` when `/Metadata` names
/// anything else.
fn xmp_stream(doc: &Document) -> Option<&Stream> {
    doc.catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Metadata").ok())
        .map(|metadata| deref_object(doc, metadata))
        .and_then(|metadata| metadata.as_stream().ok())
}

/// Implements [`_Document::xml_metadata`].
pub(super) fn xml_metadata(
    this: &_Document,
    py: Python<'_>,
    max_size: Option<usize>,
) -> PyResult<Option<Vec<u8>>> {
    py.detach(|| {
        xmp_stream(&this.doc)
            .map(|stream| {
                decoded_stream_content(stream, max_size, "xmp_metadata_size", "XMP metadata")
            })
            .transpose()
    })
}

/// Implements [`_Document::set_xml_metadata`].
///
/// As in MuPDF, a `/Metadata` reference to an existing stream is rewritten
/// in place, and otherwise a new `/Type /Metadata /Subtype /XML` stream
/// replaces the entry. The data stays unfiltered, also when saving with
/// `deflate=True`, so that tools which scan files for XMP packets find it.
pub(super) fn set_xml_metadata(
    this: &mut _Document,
    data: Vec<u8>,
    max_size: Option<usize>,
) -> PyResult<()> {
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    if let Some(limit) = max_size
        && data.len() > limit
    {
        return Err(limit_err(
            "xmp_metadata_input_size",
            format!(
                "XMP metadata input is {} bytes, exceeding the {limit}-byte limit",
                data.len()
            ),
        ));
    }
    let catalog = this.doc.catalog().map_err(to_py_err)?;
    let existing = match catalog.get(b"Metadata") {
        Ok(Object::Reference(id)) if matches!(this.doc.get_object(*id), Ok(Object::Stream(_))) => {
            Some(*id)
        }
        _ => None,
    };
    if let Some(id) = existing {
        // The stream may be any object, so forget remembered placements.
        this.invalidate_hayro_pdf();
        let stream = this
            .doc
            .get_object_mut(id)
            .and_then(Object::as_stream_mut)
            .map_err(to_py_err)?;
        for key in [b"Filter".as_slice(), b"DecodeParms", b"DL"] {
            stream.dict.remove(key);
        }
        stream.set_content(data);
        stream.allows_compression = false;
        return Ok(());
    }
    this.doc
        .max_id
        .checked_add(1)
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let mut dictionary = Dictionary::new();
    dictionary.set("Type", Object::Name(b"Metadata".to_vec()));
    dictionary.set("Subtype", Object::Name(b"XML".to_vec()));
    let mut stream = Stream::new(dictionary, data);
    stream.allows_compression = false;
    this.invalidate_hayro_pdf_keeping_placements();
    let id = this.doc.add_object(stream);
    this.doc
        .catalog_mut()
        .map_err(to_py_err)?
        .set("Metadata", Object::Reference(id));
    Ok(())
}

/// Implements [`_Document::remove_xml_metadata`]. The stream stays until
/// pruning removes it, as in MuPDF.
pub(super) fn remove_xml_metadata(this: &mut _Document) -> PyResult<()> {
    if this.doc.catalog().map_err(to_py_err)?.has(b"Metadata") {
        this.invalidate_hayro_pdf_keeping_placements();
        this.doc
            .catalog_mut()
            .map_err(to_py_err)?
            .remove(b"Metadata");
    }
    Ok(())
}

/// Implements [`_Document::set_catalog_name`] for `/PageMode` and
/// `/PageLayout`.
pub(super) fn set_catalog_name(this: &mut _Document, key: &str, value: &str) -> PyResult<()> {
    let allowed: &[&str] = match key {
        "PageMode" => &PAGE_MODES,
        "PageLayout" => &PAGE_LAYOUTS,
        _ => {
            return Err(PdfError::new_err(format!(
                "unsupported catalog name entry: {key:?}"
            )));
        }
    };
    if !allowed.contains(&value) {
        return Err(PdfError::new_err(format!("bad {key} value: {value:?}")));
    }
    this.doc.catalog().map_err(to_py_err)?;
    this.invalidate_hayro_pdf_keeping_placements();
    this.doc
        .catalog_mut()
        .map_err(to_py_err)?
        .set(key, Object::Name(value.as_bytes().to_vec()));
    Ok(())
}

/// Implements [`_Document::set_mark_info`]: replace `/MarkInfo` with a
/// direct dictionary of all three entries, as pymupdf writes it.
pub(super) fn set_mark_info(
    this: &mut _Document,
    marked: bool,
    user_properties: bool,
    suspects: bool,
) -> PyResult<()> {
    this.doc.catalog().map_err(to_py_err)?;
    let mut mark_info = Dictionary::new();
    mark_info.set("Marked", marked);
    mark_info.set("UserProperties", user_properties);
    mark_info.set("Suspects", suspects);
    this.invalidate_hayro_pdf_keeping_placements();
    this.doc
        .catalog_mut()
        .map_err(to_py_err)?
        .set("MarkInfo", mark_info);
    Ok(())
}

/// Implements [`_Document::has_annotation_kind`]: whether any page's
/// `/Annots` lists a dictionary whose `/Subtype` is `/Link` (`links`), or is
/// neither `/Link` nor `/Widget`, a missing subtype included (otherwise).
pub(super) fn has_annotation_kind(this: &_Document, py: Python<'_>, links: bool) -> PyResult<bool> {
    py.detach(|| {
        let doc = &this.doc;
        fn present<'a>(doc: &'a Document, object: &'a Object) -> Option<&'a Object> {
            non_null_value(doc, object).ok().flatten()
        }
        for &page_id in doc.page_ids()? {
            let Some(Object::Array(entries)) = doc
                .get_object(page_id)
                .and_then(Object::as_dict)
                .and_then(|page| page.get(b"Annots"))
                .ok()
                .and_then(|annots| present(doc, annots))
            else {
                continue;
            };
            for entry in entries {
                let Some(Object::Dictionary(annotation)) = present(doc, entry) else {
                    continue;
                };
                let subtype = annotation
                    .get(b"Subtype")
                    .ok()
                    .and_then(|subtype| present(doc, subtype))
                    .and_then(|subtype| subtype.as_name().ok());
                let found = if links {
                    subtype == Some(b"Link")
                } else {
                    !matches!(subtype, Some(b"Link" | b"Widget"))
                };
                if found {
                    return Ok(true);
                }
            }
        }
        Ok(false)
    })
}

/// Implements [`_Document::pdfa_claim`].
pub(super) fn pdfa_claim(
    this: &_Document,
    py: Python<'_>,
    max_size: Option<usize>,
) -> PyResult<Option<(i64, String)>> {
    py.detach(|| {
        let Some(stream) = xmp_stream(&this.doc) else {
            return Ok(None);
        };
        let data = decoded_stream_content(stream, max_size, "xmp_metadata_size", "XMP metadata")?;
        let xmp = String::from_utf8_lossy(&data);
        let Some(part) = xmp_value(&xmp, "pdfaid:part").and_then(|value| value.parse::<i64>().ok())
        else {
            return Ok(None);
        };
        let conformance = xmp_value(&xmp, "pdfaid:conformance").unwrap_or_default();
        Ok(Some((part, conformance)))
    })
}
