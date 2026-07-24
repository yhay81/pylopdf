//! Python bindings for `lopdf::Document`.
//!
//! This is a thin type- and error-conversion layer. Python's
//! `pylopdf.Document` provides the ergonomic API.

use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};

use hayro::hayro_interpret::font::{FallbackFontQuery, FontData, FontQuery};
use hayro::hayro_interpret::hayro_cmap::CidFamily;
use hayro::hayro_interpret::{InterpreterSettings, InterpreterWarning};
use hayro::hayro_syntax::Pdf;
use hayro::vello_cpu::color::AlphaColor;
use hayro::{RenderCache, RenderSettings, render};
use lopdf::encryption::crypt_filters::{Aes256CryptFilter, CryptFilter};
use lopdf::encryption::{EncryptionState, EncryptionVersion, Permissions};
use lopdf::{
    Bookmark, Dictionary, Document, LoadOptions, Object, ObjectId, PdfMetadata, SaveOptions,
    Stream, decode_text_string, dictionary, text_string,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::draw;
use crate::ocr;

/// Bound interpreted text-page memory on long documents while retaining the
/// common search/extract/annotate working set.
const TEXT_PAGE_CACHE_CAPACITY: usize = 8;

/// One annotation returned by read_annotations: Subtype, display Rect, Contents, URI.
type AnnotationTuple = (String, (f64, f64, f64, f64), Option<String>, Option<String>);

/// One link returned by read_links: kind, display Rect, URI, one-based lopdf
/// destination page, destination display point, zoom, external file, and named
/// destination or Named action.
type LinkTuple = (
    String,
    (f64, f64, f64, f64),
    Option<String>,
    Option<u32>,
    Option<(f64, f64)>,
    Option<f64>,
    Option<String>,
    Option<String>,
);

/// Resolved link destination: page number, display point, zoom, named destination.
type ResolvedDestination = (Option<u32>, Option<(f64, f64)>, Option<f64>, Option<String>);

/// One EmbeddedFiles name-tree item: display name and FileSpec object.
///
/// FileSpec may be either an indirect reference or an inline dictionary.
type EmbeddedFileEntry = (String, Object);

/// Resolve one reference level, returning the original object on failure.
fn deref_object<'a>(doc: &'a Document, obj: &'a Object) -> &'a Object {
    match obj {
        Object::Reference(id) => doc.get_object(*id).unwrap_or(obj),
        other => other,
    }
}

/// Linearly search a `/Names` plus `/Kids` name tree for `name`.
/// `depth` limits recursion against cycles.
fn search_name_tree<'a>(
    doc: &'a Document,
    node: &'a Object,
    name: &[u8],
    depth: u8,
) -> Option<&'a Object> {
    if depth > 16 {
        return None;
    }
    let dict = deref_object(doc, node).as_dict().ok()?;
    if let Ok(names) = dict.get(b"Names")
        && let Ok(arr) = deref_object(doc, names).as_array()
    {
        for pair in arr.chunks(2) {
            if pair.len() == 2
                && let Ok(key) = deref_object(doc, &pair[0]).as_str()
                && key == name
            {
                return Some(deref_object(doc, &pair[1]));
            }
        }
    }
    if let Ok(kids) = dict.get(b"Kids")
        && let Ok(arr) = deref_object(doc, kids).as_array()
    {
        for kid in arr {
            if let Some(found) = search_name_tree(doc, kid, name, depth + 1) {
                return Some(found);
            }
        }
    }
    None
}

/// Resolve a named destination from the catalog.
/// Search both the PDF 1.2+ `/Names` → `/Dests` tree and legacy `/Dests`.
fn lookup_named_dest<'a>(doc: &'a Document, name: &[u8]) -> Option<&'a Object> {
    let catalog = doc.catalog().ok()?;
    if let Ok(names) = catalog.get(b"Names")
        && let Ok(names) = deref_object(doc, names).as_dict()
        && let Ok(dests) = names.get(b"Dests")
        && let Some(found) = search_name_tree(doc, dests, name, 0)
    {
        return Some(found);
    }
    if let Ok(dests) = catalog.get(b"Dests")
        && let Ok(dests) = deref_object(doc, dests).as_dict()
        && let Ok(found) = dests.get(name)
    {
        return Some(deref_object(doc, found));
    }
    None
}

/// Extract a display name from a string or FileSpec dictionary with `/UF`/`/F`.
fn filespec_name(doc: &Document, obj: &Object) -> Option<String> {
    match deref_object(doc, obj) {
        Object::Dictionary(d) => d
            .get(b"UF")
            .or_else(|_| d.get(b"F"))
            .ok()
            .and_then(|o| decode_text_string(deref_object(doc, o)).ok()),
        other => decode_text_string(other).ok(),
    }
}

/// One field-tree traversal node: ObjectId, prefix, inherited FT, Ff, and V.
type FieldNode = (ObjectId, String, Option<String>, i64, Option<Object>);

// Python-visible exceptions. PdfError subclasses ValueError for compatibility.
pyo3::create_exception!(
    pylopdf,
    PdfError,
    PyValueError,
    "Base pylopdf exception compatible with ValueError."
);
pyo3::create_exception!(
    pylopdf,
    PasswordError,
    PdfError,
    "A password is required or incorrect."
);

/// Page dictionary keys that may be inherited from parent nodes.
const INHERITABLE_PAGE_KEYS: [&[u8]; 4] = [b"Resources", b"MediaBox", b"CropBox", b"Rotate"];

/// Maximum PNG render pixels, approximately a 256 MB RGBA bitmap.
const MAX_RENDER_PIXELS: u64 = 64_000_000;

/// Convert a lopdf error to a Python exception with a context prefix.
///
/// Password/decryption failures become PasswordError; others become PdfError.
fn lopdf_err(prefix: Option<&str>, e: &lopdf::Error) -> PyErr {
    let message = match prefix {
        Some(p) => format!("{p}: {e}"),
        None => e.to_string(),
    };
    if matches!(
        e,
        lopdf::Error::Decryption(_) | lopdf::Error::InvalidPassword
    ) {
        PasswordError::new_err(message)
    } else {
        PdfError::new_err(message)
    }
}

/// Convert a lopdf error to a Python exception.
fn to_py_err(e: lopdf::Error) -> PyErr {
    lopdf_err(None, &e)
}

/// Safely convert f64 to PDF real representation (`lopdf::Object::Real = f32`).
fn checked_pdf_real(value: f64, name: &str) -> PyResult<f32> {
    let converted = value as f32;
    if !value.is_finite() || !converted.is_finite() {
        return Err(PdfError::new_err(format!(
            "{name} must be a finite value within PDF real-number range: {value:?}"
        )));
    }
    Ok(converted)
}

/// Convert PdfMetadata to an Info dict, page count, version, and encrypted flag.
fn pdf_metadata_to_tuple(meta: PdfMetadata) -> (BTreeMap<String, String>, u32, String, bool) {
    let mut map = BTreeMap::new();
    let pairs = [
        ("Title", meta.title),
        ("Author", meta.author),
        ("Subject", meta.subject),
        ("Keywords", meta.keywords),
        ("Creator", meta.creator),
        ("Producer", meta.producer),
        ("CreationDate", meta.creation_date),
        ("ModDate", meta.modification_date),
    ];
    for (key, value) in pairs {
        if let Some(v) = value {
            map.insert(key.to_string(), v);
        }
    }
    (map, meta.page_count, meta.version, meta.encrypted)
}

/// Save options enabling object and xref streams.
///
/// Keep ObjectStreamConfig defaults: 100 objects and compression level 6.
fn modern_save_options() -> SaveOptions {
    SaveOptions {
        use_object_streams: true,
        use_xref_streams: true,
        ..Default::default()
    }
}

/// Return a page dictionary with inherited parent-tree attributes materialized.
///
/// Merge discards the source page tree, so inherited attributes must move onto
/// the page itself.
fn resolve_inherited_page_dict(doc: &Document, page_id: ObjectId) -> lopdf::Result<Dictionary> {
    let mut dict = doc.get_object(page_id)?.as_dict()?.clone();
    for key in INHERITABLE_PAGE_KEYS {
        if dict.has(key) {
            continue;
        }
        let mut parent = dict.get(b"Parent").and_then(Object::as_reference).ok();
        let mut visited = HashSet::from([page_id]);
        while let Some(parent_id) = parent {
            if !visited.insert(parent_id) {
                return Err(lopdf::Error::ReferenceCycle(parent_id));
            }
            let parent_dict = doc.get_object(parent_id)?.as_dict()?;
            if let Ok(value) = parent_dict.get(key) {
                dict.set(key, value.clone());
                break;
            }
            parent = parent_dict
                .get(b"Parent")
                .and_then(Object::as_reference)
                .ok();
        }
    }
    Ok(dict)
}

/// Read an indirect-capable box array as normalized `[x0, y0, x1, y1]`.
fn resolve_box(doc: &Document, dict: &Dictionary, key: &[u8]) -> Option<[f64; 4]> {
    let obj = dict.get(key).ok()?;
    let obj = match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?,
        other => other,
    };
    let arr = obj.as_array().ok()?;
    if arr.len() != 4 {
        return None;
    }
    let mut v = [0f64; 4];
    for (slot, item) in v.iter_mut().zip(arr) {
        let resolved = match item {
            Object::Reference(id) => doc.get_object(*id).ok()?,
            other => other,
        };
        *slot = f64::from(resolved.as_float().ok()?);
    }
    Some([
        v[0].min(v[2]),
        v[1].min(v[3]),
        v[0].max(v[2]),
        v[1].max(v[3]),
    ])
}

/// Extract an XMP value from `key="v"` attributes or `<key>v</key>` elements.
fn xmp_value(xmp: &str, key: &str) -> Option<String> {
    let idx = xmp.find(key)?;
    let rest = xmp[idx + key.len()..].trim_start();
    if let Some(r) = rest.strip_prefix('=') {
        let r = r.trim_start();
        let r = r.strip_prefix('"').or_else(|| r.strip_prefix('\''))?;
        let end = r.find(['"', '\''])?;
        return Some(r[..end].to_owned());
    }
    if let Some(r) = rest.strip_prefix('>') {
        let end = r.find('<')?;
        return Some(r[..end].trim().to_owned());
    }
    None
}

/// Convert a hayro Pixmap to straight-alpha RGBA8 bytes.
fn rgba_bytes(pixmap: hayro::vello_cpu::Pixmap) -> Vec<u8> {
    let pixels = pixmap.take_unpremultiplied();
    let mut out = Vec::with_capacity(pixels.len() * 4);
    for px in pixels {
        out.extend_from_slice(&[px.r, px.g, px.b, px.a]);
    }
    out
}

/// Clone a dictionary while allowing an indirect reference.
fn deref_dict(doc: &Document, obj: &Object) -> Option<Dictionary> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok().cloned(),
        Object::Dictionary(d) => Some(d.clone()),
        _ => None,
    }
}

/// Read an integer while allowing an indirect reference.
fn resolve_i64(doc: &Document, obj: &Object) -> Option<i64> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_i64().ok(),
        other => other.as_i64().ok(),
    }
}

/// Validate RunLengthDecode output size without allocating the output.
fn validate_run_length_size(data: &[u8], max_output: usize) -> PyResult<()> {
    let mut pos = 0usize;
    let mut output_len = 0usize;
    while pos < data.len() {
        let length = data[pos];
        pos += 1;
        match length {
            0..=127 => {
                let count = usize::from(length) + 1;
                if pos.checked_add(count).is_none_or(|end| end > data.len()) {
                    return Err(PdfError::new_err(
                        "RunLengthDecode stream ends unexpectedly",
                    ));
                }
                pos += count;
                output_len = output_len.checked_add(count).ok_or_else(|| {
                    PdfError::new_err("RunLengthDecode decompressed size exceeds the limit")
                })?;
            }
            128 => break,
            129..=255 => {
                if pos >= data.len() {
                    return Err(PdfError::new_err(
                        "RunLengthDecode stream ends unexpectedly",
                    ));
                }
                pos += 1;
                output_len = output_len
                    .checked_add(257 - usize::from(length))
                    .ok_or_else(|| {
                        PdfError::new_err("RunLengthDecode decompressed size exceeds the limit")
                    })?;
            }
        }
        if output_len > max_output {
            return Err(PdfError::new_err(format!(
                "decompressed output exceeded the {max_output}-byte limit (possible decompression bomb)"
            )));
        }
    }
    Ok(())
}

/// Validate ASCIIHexDecode output size without allocating the output.
fn validate_ascii_hex_size(data: &[u8], max_output: usize) -> PyResult<()> {
    let digits = data
        .iter()
        .take_while(|&&byte| byte != b'>')
        .filter(|byte| !byte.is_ascii_whitespace())
        .count();
    let output_len = digits.div_ceil(2);
    if output_len > max_output {
        return Err(PdfError::new_err(format!(
            "decompressed output exceeded the {max_output}-byte limit (possible decompression bomb)"
        )));
    }
    Ok(())
}

/// Normalize PDF-spec filter abbreviations to canonical names.
fn canonical_filter_name(filter: &[u8]) -> &[u8] {
    match filter {
        b"Fl" => b"FlateDecode",
        b"LZW" => b"LZWDecode",
        b"A85" => b"ASCII85Decode",
        b"AHx" => b"ASCIIHexDecode",
        b"RL" => b"RunLengthDecode",
        b"CCF" => b"CCITTFaxDecode",
        b"DCT" => b"DCTDecode",
        _ => filter,
    }
}

/// Prevalidate decompression limits at load, including hayro-lazy streams.
///
/// Decode Flate/LZW/ASCII85 with lopdf's bound; scan RunLength/ASCIIHex sizes
/// only. Bound image-decoder DCT/JPX/JBIG2/CCITT buffers as Width×Height×4.
/// Reject filter chains that cannot be bounded safely when a limit is set.
fn validate_decompression_limits(doc: &Document, max_output: usize) -> PyResult<()> {
    const LOPDF_FILTERS: [&[u8]; 3] = [b"FlateDecode", b"LZWDecode", b"ASCII85Decode"];
    const IMAGE_FILTERS: [&[u8]; 4] = [
        b"DCTDecode",
        b"JPXDecode",
        b"JBIG2Decode",
        b"CCITTFaxDecode",
    ];

    for object in doc.objects.values() {
        let Object::Stream(stream) = object else {
            continue;
        };
        if !stream.dict.has(b"Filter") {
            stream
                .get_plain_content_with_limit(max_output)
                .map_err(to_py_err)?;
            continue;
        }
        let raw_filters = stream.filters().map_err(to_py_err)?;
        let filters: Vec<&[u8]> = raw_filters
            .iter()
            .map(|filter| canonical_filter_name(filter))
            .collect();
        if filters.is_empty() {
            stream
                .get_plain_content_with_limit(max_output)
                .map_err(to_py_err)?;
            continue;
        }
        // lopdf accepts canonical filter names only; normalize on the clone.
        let mut checked_stream = stream.clone();
        let normalized_filter = if filters.len() == 1 {
            Object::Name(filters[0].to_vec())
        } else {
            Object::Array(
                filters
                    .iter()
                    .map(|filter| Object::Name(filter.to_vec()))
                    .collect(),
            )
        };
        checked_stream.dict.set("Filter", normalized_filter);

        let first_unsupported = filters
            .iter()
            .position(|filter| !LOPDF_FILTERS.contains(filter));
        match first_unsupported {
            None => {
                checked_stream
                    .get_plain_content_with_limit(max_output)
                    .map_err(to_py_err)?;
            }
            Some(index)
                if IMAGE_FILTERS.contains(&filters[index])
                    && index + 1 == filters.len()
                    && filters[..index]
                        .iter()
                        .all(|filter| LOPDF_FILTERS.contains(filter)) =>
            {
                // Decode compression layers before image filters with lopdf's bound.
                if index > 0 {
                    match checked_stream.get_plain_content_with_limit(max_output) {
                        Err(lopdf::Error::Unimplemented(_)) => {}
                        result => {
                            result.map_err(to_py_err)?;
                        }
                    }
                }
                let width = stream
                    .dict
                    .get(b"Width")
                    .ok()
                    .and_then(|value| resolve_i64(doc, value));
                let height = stream
                    .dict
                    .get(b"Height")
                    .ok()
                    .and_then(|value| resolve_i64(doc, value));
                let (Some(width), Some(height)) = (width, height) else {
                    return Err(PdfError::new_err(
                        "cannot resolve the image stream's Width/Height, so the decompression limit cannot be verified",
                    ));
                };
                let decoded_size = u64::try_from(width)
                    .ok()
                    .and_then(|width| {
                        u64::try_from(height)
                            .ok()
                            .and_then(|height| width.checked_mul(height))
                    })
                    .and_then(|pixels| pixels.checked_mul(4))
                    .ok_or_else(|| {
                        PdfError::new_err("image decompressed size exceeds the limit")
                    })?;
                if decoded_size > max_output as u64 {
                    return Err(PdfError::new_err(format!(
                        "decompressed image output exceeded the {max_output}-byte limit (possible decompression bomb)"
                    )));
                }
            }
            Some(index) if filters.len() == 1 && filters[index] == b"RunLengthDecode" => {
                validate_run_length_size(&stream.content, max_output)?;
            }
            Some(index) if filters.len() == 1 && filters[index] == b"ASCIIHexDecode" => {
                validate_ascii_hex_size(&stream.content, max_output)?;
            }
            Some(index) => {
                return Err(PdfError::new_err(format!(
                    "cannot safely verify the decompression limit for a stream with filter {:?}",
                    String::from_utf8_lossy(filters[index])
                )));
            }
        }
    }
    Ok(())
}

/// Fallback font used for non-embedded CJK fonts during rendering.
#[derive(Default, Clone)]
struct FallbackFonts {
    /// Sans/gothic family and the default when style is unknown.
    sans: Option<(Arc<Vec<u8>>, u32)>,
    /// Mincho-style serif font.
    serif: Option<(Arc<Vec<u8>>, u32)>,
}

/// Lowercase BaseFont-name patterns indicating CJK.
const CJK_NAME_HINTS: [&str; 12] = [
    "mincho", "gothic", "ryumin", "kozmin", "kozgo", "kozuka", "meiryo", "yugoth", "yumin",
    "hiragino", "ipaex", "ipam",
];

/// Lowercase BaseFont-name patterns indicating a serif/mincho family.
const SERIF_NAME_HINTS: [&str; 5] = ["mincho", "ryumin", "kozmin", "yumin", "serif"];

/// Return a configured fallback when a non-embedded font request is CJK.
///
/// Detect CJK through CIDSystemInfo (Adobe-Japan1/GB1/CNS1/Korea1) or BaseFont.
/// Adobe-Identity lacks CID-to-Unicode clues in its CMap, so use the name;
/// hayro resolves an embedded ToUnicode map when present.
fn pick_cjk_fallback(fonts: &FallbackFonts, query: &FallbackFontQuery) -> Option<(FontData, u32)> {
    let is_cjk_collection = matches!(
        query.character_collection.as_ref().map(|cc| &cc.family),
        Some(
            CidFamily::AdobeJapan1
                | CidFamily::AdobeGB1
                | CidFamily::AdobeCNS1
                | CidFamily::AdobeKorea1
        )
    );
    let name = query
        .post_script_name
        .as_deref()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let is_cjk_name = CJK_NAME_HINTS.iter().any(|hint| name.contains(hint));
    if !is_cjk_collection && !is_cjk_name {
        return None;
    }
    let prefers_serif = SERIF_NAME_HINTS.iter().any(|hint| name.contains(hint));
    let slot = if prefers_serif {
        fonts.serif.as_ref().or(fonts.sans.as_ref())
    } else {
        fonts.sans.as_ref().or(fonts.serif.as_ref())
    };
    slot.map(|(data, index)| (Arc::clone(data) as FontData, *index))
}

/// Python class holding a `lopdf::Document`.
#[pyclass(module = "pylopdf.pylopdf_core")]
pub struct _Document {
    /// Editable lopdf document.
    doc: Document,
    /// CJK fallback configuration for rendering.
    fallback_fonts: FallbackFonts,
    /// Parsed hayro snapshot of current edit state, rebuilt after invalidation.
    hayro_pdf: Option<Pdf>,
    /// Original unencrypted input, consumed by the first hayro parse.
    ///
    /// This avoids a potentially expensive lopdf serialization before the
    /// first render or extraction. Any edit discards it together with the
    /// parsed hayro view.
    hayro_source: Option<Vec<u8>>,
    /// Recently interpreted pages, keyed by one-based page number.
    text_pages: HashMap<u32, crate::extract::TextPage>,
    /// Least-recently-used to most-recently-used text-page keys.
    text_page_order: VecDeque<u32>,
    /// Hayro warnings from the latest render/extraction, written by the
    /// interpreter-settings sink and drained by `take_warnings`.
    pending_warnings: Arc<Mutex<Vec<String>>>,
}

impl _Document {
    /// Construct from lopdf with no fallback fonts configured.
    fn from_doc(doc: Document, hayro_source: Option<Vec<u8>>) -> Self {
        Self {
            doc,
            fallback_fonts: FallbackFonts::default(),
            hayro_pdf: None,
            hayro_source,
            text_pages: HashMap::new(),
            text_page_order: VecDeque::new(),
            pending_warnings: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Return the trailer Info dictionary with indirect references resolved.
    fn info_dict(&self) -> Option<&Dictionary> {
        match self.doc.trailer.get(b"Info").ok()? {
            Object::Reference(id) => self.doc.get_object(*id).ok()?.as_dict().ok(),
            Object::Dictionary(dict) => Some(dict),
            _ => None,
        }
    }

    /// Serialize current edit state to bytes for rendering.
    fn current_bytes(&mut self) -> PyResult<Vec<u8>> {
        let mut buffer = Vec::new();
        self.doc
            .save_to(&mut buffer)
            .map_err(|e| PdfError::new_err(e.to_string()))?;
        Ok(buffer)
    }

    /// Drop cached views; call at the start of every editing method.
    fn invalidate_hayro_pdf(&mut self) {
        self.hayro_pdf = None;
        self.hayro_source = None;
        self.invalidate_text_pages();
    }

    /// Drop derived text pages while retaining the parse-only hayro snapshot.
    fn invalidate_text_pages(&mut self) {
        self.text_pages.clear();
        self.text_page_order.clear();
    }

    /// Return the ObjectId of a one-based page.
    fn page_id(&self, page_number: u32) -> PyResult<ObjectId> {
        self.doc
            .get_pages()
            .get(&page_number)
            .copied()
            .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))
    }

    /// Read a page attribute while resolving inheritance and indirect references.
    fn resolve_page_attr(&self, page_id: ObjectId, key: &[u8]) -> PyResult<Option<Object>> {
        let mut current = Some(page_id);
        let mut visited = HashSet::new();
        while let Some(id) = current {
            if !visited.insert(id) {
                return Err(to_py_err(lopdf::Error::ReferenceCycle(id)));
            }
            let dict = self
                .doc
                .get_object(id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?;
            if let Ok(value) = dict.get(key) {
                let resolved = match value {
                    Object::Reference(rid) => self.doc.get_object(*rid).map_err(to_py_err)?.clone(),
                    other => other.clone(),
                };
                return Ok(Some(resolved));
            }
            current = dict.get(b"Parent").and_then(Object::as_reference).ok();
        }
        Ok(None)
    }

    /// Page display geometry: CropBox, then MediaBox, then A4, plus rotation.
    fn page_display_geometry(&self, page_number: u32) -> PyResult<([f64; 4], i64)> {
        let rotation = self.get_page_rotation(page_number)?;
        let boxed = self
            .get_page_box(page_number, "CropBox")?
            .or(self.get_page_box(page_number, "MediaBox")?)
            .unwrap_or((0.0, 0.0, 595.0, 842.0));
        let (x0, y0, x1, y1) = boxed;
        Ok(([x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)], rotation))
    }

    /// Materialize inherited attributes into the page dictionary.
    ///
    /// Required before drawing: lopdf `add_xobject` creates empty `/Resources`
    /// when absent and would shadow inherited parent resources.
    fn bake_page_attrs(&mut self, page_id: ObjectId) -> PyResult<()> {
        let dict = resolve_inherited_page_dict(&self.doc, page_id).map_err(to_py_err)?;
        self.doc.objects.insert(page_id, Object::Dictionary(dict));
        Ok(())
    }

    /// Flatten AcroForm fields into `(full name, ObjectId, FT, Ff, V)`.
    ///
    /// FT/Ff/V inherit from parents; full names join `/T` components with dots.
    /// A leaf has resolved FT and no child carrying `/T`.
    fn collect_form_fields(&self) -> Vec<(String, ObjectId, String, i64, Option<Object>)> {
        let Some(acroform) = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"AcroForm").ok().cloned())
            .and_then(|o| deref_dict(&self.doc, &o))
        else {
            return Vec::new();
        };
        let Ok(fields) = acroform.get(b"Fields").and_then(Object::as_array) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        // (ObjectId, qualified name, inherited FT, inherited Ff, inherited V)
        let mut stack: Vec<FieldNode> = fields
            .iter()
            .filter_map(|f| f.as_reference().ok())
            .map(|id| (id, String::new(), None, 0, None))
            .collect();
        let mut visited = HashSet::new();
        while let Some((id, prefix, inh_ft, inh_ff, inh_v)) = stack.pop() {
            if !visited.insert(id) || visited.len() > 4096 {
                continue;
            }
            let Ok(dict) = self.doc.get_object(id).and_then(Object::as_dict) else {
                continue;
            };
            let name = match dict.get(b"T").ok().and_then(|t| decode_text_string(t).ok()) {
                Some(t) if prefix.is_empty() => t,
                Some(t) => format!("{prefix}.{t}"),
                None => prefix.clone(),
            };
            let ft = dict
                .get(b"FT")
                .and_then(Object::as_name)
                .ok()
                .map(|n| String::from_utf8_lossy(n).into_owned())
                .or(inh_ft);
            let ff = dict
                .get(b"Ff")
                .ok()
                .and_then(|o| resolve_i64(&self.doc, o))
                .unwrap_or(inh_ff);
            let v = dict.get(b"V").ok().cloned().or(inh_v);
            // Children with /T are fields; children without it are widgets.
            let child_fields: Vec<ObjectId> = dict
                .get(b"Kids")
                .and_then(Object::as_array)
                .map(|kids| {
                    kids.iter()
                        .filter_map(|k| k.as_reference().ok())
                        .filter(|kid_id| {
                            self.doc
                                .get_object(*kid_id)
                                .and_then(Object::as_dict)
                                .is_ok_and(|d| d.has(b"T"))
                        })
                        .collect()
                })
                .unwrap_or_default();
            if child_fields.is_empty() {
                if let Some(ft) = ft {
                    out.push((name, id, ft, ff, v));
                }
            } else {
                for kid in child_fields {
                    stack.push((kid, name.clone(), ft.clone(), ff, v.clone()));
                }
            }
        }
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out
    }

    /// Return widget annotation ObjectIds, or the field itself when Kids is absent.
    fn field_widgets(&self, field_id: ObjectId) -> Vec<ObjectId> {
        let Ok(dict) = self.doc.get_object(field_id).and_then(Object::as_dict) else {
            return vec![field_id];
        };
        let widgets: Vec<ObjectId> = dict
            .get(b"Kids")
            .and_then(Object::as_array)
            .map(|kids| {
                kids.iter()
                    .filter_map(|k| k.as_reference().ok())
                    .filter(|kid_id| {
                        self.doc
                            .get_object(*kid_id)
                            .and_then(Object::as_dict)
                            .is_ok_and(|d| !d.has(b"T"))
                    })
                    .collect()
            })
            .unwrap_or_default();
        if widgets.is_empty() {
            vec![field_id]
        } else {
            widgets
        }
    }

    /// Set NeedAppearances=true in AcroForm, including indirect dictionaries.
    fn set_need_appearances(&mut self) -> PyResult<()> {
        let acroform_ref = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"AcroForm").ok())
            .and_then(|a| a.as_reference().ok());
        match acroform_ref {
            Some(id) => {
                let acroform = self
                    .doc
                    .get_object_mut(id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                acroform.set("NeedAppearances", true);
            }
            None => {
                let catalog = self.doc.catalog_mut().map_err(to_py_err)?;
                let acroform = catalog
                    .get_mut(b"AcroForm")
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                acroform.set("NeedAppearances", true);
            }
        }
        Ok(())
    }

    /// Add an annotation reference to page `/Annots`, including indirect arrays.
    fn push_page_annotation(&mut self, page_id: ObjectId, annot_id: ObjectId) -> PyResult<()> {
        let array_ref = {
            let page = self
                .doc
                .get_object(page_id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?;
            page.get(b"Annots").ok().and_then(|a| a.as_reference().ok())
        };
        match array_ref {
            Some(arr_id) => {
                // copy_page/select duplicates may share indirect Annots arrays.
                // Clone on write while shared so additions do not leak.
                let shared = self.doc.get_pages().into_values().any(|other_page_id| {
                    other_page_id != page_id
                        && self
                            .doc
                            .get_object(other_page_id)
                            .and_then(Object::as_dict)
                            .ok()
                            .and_then(|page| page.get(b"Annots").ok())
                            .and_then(|annots| annots.as_reference().ok())
                            == Some(arr_id)
                });
                if shared {
                    let mut arr = self
                        .doc
                        .get_object(arr_id)
                        .and_then(Object::as_array)
                        .map_err(to_py_err)?
                        .clone();
                    arr.push(Object::Reference(annot_id));
                    let page = self
                        .doc
                        .get_object_mut(page_id)
                        .and_then(Object::as_dict_mut)
                        .map_err(to_py_err)?;
                    page.set("Annots", arr);
                } else {
                    let arr = self
                        .doc
                        .get_object_mut(arr_id)
                        .and_then(Object::as_array_mut)
                        .map_err(to_py_err)?;
                    arr.push(Object::Reference(annot_id));
                }
            }
            None => {
                let page = self
                    .doc
                    .get_object_mut(page_id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                let mut arr = match page.get(b"Annots").and_then(Object::as_array) {
                    Ok(existing) => existing.clone(),
                    Err(_) => Vec::new(),
                };
                arr.push(Object::Reference(annot_id));
                page.set("Annots", arr);
            }
        }
        Ok(())
    }

    /// Return the hayro view, preferring original bytes before normalization.
    ///
    /// Editing methods invalidate the cache, preserving the invariant that
    /// rendered state always reflects edits. Consecutive renders rebuild once.
    fn hayro_view(&mut self) -> PyResult<&Pdf> {
        if self.hayro_pdf.is_none() {
            let expected_pages = self.doc.get_pages().len();
            let source_pdf = self
                .hayro_source
                .take()
                .and_then(|data| Pdf::new(data).ok())
                .filter(|pdf| pdf.pages().len() == expected_pages);
            let pdf = match source_pdf {
                Some(pdf) => pdf,
                None => {
                    let data = self.current_bytes()?;
                    Pdf::new(data).map_err(|e| {
                        PdfError::new_err(format!("failed to parse PDF for rendering: {e:?}"))
                    })?
                }
            };
            self.hayro_pdf = Some(pdf);
        }
        Ok(self
            .hayro_pdf
            .as_ref()
            .expect("constructed immediately before"))
    }

    /// Return a cached, owned interpretation of a one-based page.
    fn text_page(
        &mut self,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<&crate::extract::TextPage> {
        if self.text_pages.contains_key(&page_number) {
            self.text_page_order.retain(|number| *number != page_number);
            self.text_page_order.push_back(page_number);
            return Ok(self
                .text_pages
                .get(&page_number)
                .expect("cache key was checked immediately before"));
        }

        let text_page = {
            let pdf = self.hayro_view()?;
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            crate::extract::TextPage::new(pdf, page, settings)
        };

        if self.text_pages.len() >= TEXT_PAGE_CACHE_CAPACITY
            && let Some(evicted) = self.text_page_order.pop_front()
        {
            self.text_pages.remove(&evicted);
        }
        self.text_pages.insert(page_number, text_page);
        self.text_page_order.push_back(page_number);
        Ok(self
            .text_pages
            .get(&page_number)
            .expect("text page was inserted immediately before"))
    }

    /// Build InterpreterSettings with fallbacks and the warning sink.
    fn interpreter_settings(&self) -> InterpreterSettings {
        let mut settings = InterpreterSettings::default();
        if self.fallback_fonts.sans.is_some() || self.fallback_fonts.serif.is_some() {
            let fonts = self.fallback_fonts.clone();
            let default_resolver = settings.font_resolver.clone();
            settings.font_resolver = Arc::new(move |query| {
                if let FontQuery::Fallback(fallback) = query
                    && let Some(picked) = pick_cjk_fallback(&fonts, fallback)
                {
                    return Some(picked);
                }
                default_resolver(query)
            });
        }
        // Collect hayro warnings in pending_warnings, deduplicating messages.
        let sink = Arc::clone(&self.pending_warnings);
        settings.warning_sink = Arc::new(move |warning| {
            let message = match warning {
                InterpreterWarning::UnsupportedFont => {
                    "encountered an unsupported font format; some glyphs could not be processed"
                }
                InterpreterWarning::ImageDecodeFailure => "failed to decode an image",
            };
            if let Ok(mut pending) = sink.lock()
                && !pending.iter().any(|m| m == message)
            {
                pending.push(message.to_owned());
            }
        });
        settings
    }

    /// Validate and render a page to hayro Pixmap; shared by PNG and Pixmap APIs.
    fn render_pixmap_impl(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: f32,
        background: Option<(u8, u8, u8, u8)>,
    ) -> PyResult<hayro::vello_cpu::Pixmap> {
        if !scale.is_finite() || scale <= 0.0 {
            return Err(PdfError::new_err("scale must be a finite, positive value"));
        }
        let interpreter_settings = self.interpreter_settings();
        py.detach(|| {
            let pdf = self.hayro_view()?;
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| {
                    PdfError::new_err(format!("page {page_number} does not exist"))
                })?;
            let (page_width, page_height) = page.render_dimensions();
            let pixel_width = (f64::from(page_width) * f64::from(scale)).floor();
            let pixel_height = (f64::from(page_height) * f64::from(scale)).floor();
            if !pixel_width.is_finite()
                || !pixel_height.is_finite()
                || pixel_width < 1.0
                || pixel_height < 1.0
            {
                return Err(PdfError::new_err(
                    "scale is too small, or the PDF page size is invalid",
                ));
            }
            if pixel_width > f64::from(u16::MAX) || pixel_height > f64::from(u16::MAX) {
                return Err(PdfError::new_err(format!(
                    "render size {pixel_width:.0}x{pixel_height:.0} exceeds the 65535-pixel limit per side"
                )));
            }
            let total_pixels = (pixel_width as u64) * (pixel_height as u64);
            if total_pixels > MAX_RENDER_PIXELS {
                return Err(PdfError::new_err(format!(
                    "render size {pixel_width:.0}x{pixel_height:.0} ({total_pixels} pixels) exceeds the {MAX_RENDER_PIXELS}-pixel limit"
                )));
            }
            let mut render_settings = RenderSettings {
                x_scale: scale,
                y_scale: scale,
                ..Default::default()
            };
            if let Some((r, g, b, a)) = background {
                render_settings.bg_color = AlphaColor::from_rgba8(r, g, b, a);
            }
            let cache = RenderCache::new();
            Ok(render(page, &cache, &interpreter_settings, &render_settings))
        })
    }

    /// Return root Pages ObjectId, creating a minimal tree for empty documents.
    fn ensure_page_tree(&mut self) -> lopdf::Result<ObjectId> {
        let existing = self
            .doc
            .catalog()
            .and_then(|catalog| catalog.get(b"Pages"))
            .and_then(Object::as_reference);
        if let Ok(pages_id) = existing {
            return Ok(pages_id);
        }
        let pages_id = self.doc.add_object(dictionary! {
            "Type" => "Pages",
            "Kids" => Vec::<Object>::new(),
            "Count" => 0,
        });
        let catalog_id = self.doc.add_object(dictionary! {
            "Type" => "Catalog",
            "Pages" => pages_id,
        });
        self.doc.trailer.set("Root", catalog_id);
        Ok(pages_id)
    }

    /// Import selected one-based pages from `other` in order into self's object
    /// space and return their ObjectIds; the caller connects root Kids.
    ///
    /// Materialize inherited page attributes and repoint Parent to root Pages.
    fn transplant_pages(
        &mut self,
        other: &Self,
        page_numbers: &[u32],
        pages_id: ObjectId,
    ) -> PyResult<Vec<ObjectId>> {
        let starting_id = self
            .doc
            .max_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        let mut other_doc = other.doc.clone();
        other_doc.renumber_objects_with(starting_id);
        let new_max_id = other_doc.max_id;

        let other_pages = other_doc.get_pages();
        let mut ordered_ids = Vec::with_capacity(page_numbers.len());
        for number in page_numbers {
            let id = *other_pages
                .get(number)
                .ok_or_else(|| PdfError::new_err(format!("page {number} does not exist")))?;
            ordered_ids.push(id);
        }

        // The source page tree is discarded; materialize inheritance per page.
        let mut resolved_pages = Vec::with_capacity(ordered_ids.len());
        for &page_id in &ordered_ids {
            let mut dict = resolve_inherited_page_dict(&other_doc, page_id).map_err(to_py_err)?;
            dict.set("Parent", pages_id);
            resolved_pages.push((page_id, dict));
        }

        // Import objects outside the Catalog/Pages/Page tree.
        for (id, object) in other_doc.objects {
            match object.type_name().unwrap_or(b"") {
                b"Catalog" | b"Pages" | b"Page" => {}
                _ => {
                    self.doc.objects.insert(id, object);
                }
            }
        }
        for (id, dict) in resolved_pages {
            self.doc.objects.insert(id, Object::Dictionary(dict));
        }

        self.doc.max_id = new_max_id;
        Ok(ordered_ids)
    }

    /// Append `new_ids` to root Pages Kids/Count without flattening.
    fn append_pages(&mut self, pages_id: ObjectId, new_ids: Vec<ObjectId>) -> PyResult<()> {
        // Input Count may be damaged; recalculate from reachable pages.
        // new_ids are not in Kids yet, so get_pages returns existing pages only.
        let total_count = self
            .doc
            .get_pages()
            .len()
            .checked_add(new_ids.len())
            .ok_or_else(|| PdfError::new_err("page count limit reached"))?;
        let count = i64::try_from(total_count).map_err(|e| PdfError::new_err(e.to_string()))?;
        let pages_dict = self
            .doc
            .get_object_mut(pages_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        let mut kids = match pages_dict.get(b"Kids").and_then(Object::as_array) {
            Ok(kids) => kids.clone(),
            Err(_) => Vec::new(),
        };
        kids.extend(new_ids.into_iter().map(Object::Reference));
        pages_dict.set("Kids", kids);
        pages_dict.set("Count", count);
        Ok(())
    }

    /// Return current pages with `new_ids` inserted at zero-based `position`.
    ///
    /// `new_ids` must not yet be reachable from root Kids or included by get_pages.
    fn spliced_page_order(&self, new_ids: Vec<ObjectId>, position: Option<usize>) -> Vec<ObjectId> {
        let mut order: Vec<ObjectId> = self.doc.get_pages().into_values().collect();
        let pos = position.unwrap_or(order.len()).min(order.len());
        order.splice(pos..pos, new_ids);
        order
    }

    /// Create an AES-256 PDF 2.0 V5/R6 encrypted clone; leave self plaintext.
    ///
    /// `file_encryption_key` is 32 random bytes generated by Python `os.urandom`.
    fn encrypted_clone(
        &self,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
    ) -> PyResult<Document> {
        if file_encryption_key.len() != 32 {
            return Err(PdfError::new_err(format!(
                "file_encryption_key must be 32 bytes ({} bytes given)",
                file_encryption_key.len()
            )));
        }
        let crypt_filter: Arc<dyn CryptFilter> = Arc::new(Aes256CryptFilter);
        let version = EncryptionVersion::V5 {
            encrypt_metadata: true,
            crypt_filters: BTreeMap::from([(b"StdCF".to_vec(), crypt_filter)]),
            file_encryption_key,
            stream_filter: b"StdCF".to_vec(),
            string_filter: b"StdCF".to_vec(),
            owner_password,
            user_password,
            permissions: Permissions::from_bits_truncate(permissions),
        };
        let state = EncryptionState::try_from(version).map_err(to_py_err)?;
        let mut cloned = self.doc.clone();
        cloned.encrypt(&state).map_err(to_py_err)?;
        Ok(cloned)
    }

    /// Replace root Pages Kids/Count with the given order, flattening the tree.
    ///
    /// Materialize inheritance on every page and point Parent to root.
    /// The caller prunes obsolete intermediate nodes.
    fn rebuild_page_tree(&mut self, pages_id: ObjectId, ordered: Vec<ObjectId>) -> PyResult<()> {
        for &page_id in &ordered {
            let mut dict = resolve_inherited_page_dict(&self.doc, page_id).map_err(to_py_err)?;
            dict.set("Parent", pages_id);
            self.doc.objects.insert(page_id, Object::Dictionary(dict));
        }
        let kids: Vec<Object> = ordered.iter().map(|&id| Object::Reference(id)).collect();
        let count = i64::try_from(kids.len()).map_err(|e| PdfError::new_err(e.to_string()))?;
        let pages_dict = self
            .doc
            .get_object_mut(pages_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        pages_dict.set("Kids", kids);
        pages_dict.set("Count", count);
        Ok(())
    }

    /// Resolve an array/name/string/or `/D` dictionary to a one-based lopdf page,
    /// destination display point, zoom, and named destination.
    ///
    /// Convert `/XYZ` left/top, `/FitH` top, or `/FitV` left into the destination
    /// page's rotated top-left-origin display space. Point-less `/Fit` or `/FitR`
    /// destinations return None.
    fn resolve_dest(
        &self,
        dest: &Object,
        page_map: &BTreeMap<ObjectId, u32>,
    ) -> ResolvedDestination {
        let doc = &self.doc;
        let mut nameddest = None;
        let mut resolved = deref_object(doc, dest);
        if let Object::Name(name) | Object::String(name, _) = resolved {
            nameddest = Some(String::from_utf8_lossy(name).into_owned());
            match lookup_named_dest(doc, name) {
                Some(found) => resolved = found,
                None => return (None, None, None, nameddest),
            }
        }
        // A named destination value may be a dictionary containing `/D`.
        if let Object::Dictionary(d) = resolved {
            match d.get(b"D") {
                Ok(inner) => resolved = deref_object(doc, inner),
                Err(_) => return (None, None, None, nameddest),
            }
        }
        let Object::Array(arr) = resolved else {
            return (None, None, None, nameddest);
        };
        // Element 0 should be a page reference, but some producers write a
        // zero-based integer. Keep references unresolved for reverse lookup.
        let page = match arr.first() {
            Some(Object::Reference(id)) => page_map.get(id).copied(),
            Some(Object::Integer(i)) if *i >= 0 => Some(*i as u32 + 1),
            _ => None,
        };
        let mut to = None;
        let mut zoom = None;
        if let Some(page_number) = page
            && let Ok((crop, rotation)) = self.page_display_geometry(page_number)
        {
            let num = |index: usize| {
                arr.get(index).and_then(|o| match deref_object(doc, o) {
                    Object::Integer(v) => Some(*v as f64),
                    Object::Real(v) => Some(f64::from(*v)),
                    _ => None,
                })
            };
            match arr.get(1).and_then(|o| deref_object(doc, o).as_name().ok()) {
                Some(b"XYZ") => {
                    // left/top may be Null; default to the crop's left/top edge.
                    let left = num(2).unwrap_or(crop[0]);
                    let top = num(3).unwrap_or(crop[3]);
                    zoom = num(4).filter(|z| *z != 0.0);
                    to = Some(draw::pdf_to_display(crop, rotation, left, top));
                }
                Some(b"FitH") | Some(b"FitBH") => {
                    let top = num(2).unwrap_or(crop[3]);
                    to = Some(draw::pdf_to_display(crop, rotation, crop[0], top));
                }
                Some(b"FitV") | Some(b"FitBV") => {
                    let left = num(2).unwrap_or(crop[0]);
                    to = Some(draw::pdf_to_display(crop, rotation, left, crop[3]));
                }
                _ => {}
            }
        }
        (page, to, zoom, nameddest)
    }
}

/// Collect `(name, FileSpec)` attachments from the EmbeddedFiles name tree.
///
/// Recurse through `/Kids` with depth/cycle guards. Preserve inline dictionaries
/// so a read operation does not mutate the document.
fn collect_embedded_files(doc: &Document) -> Vec<EmbeddedFileEntry> {
    fn node_dict(doc: &Document, obj: &Object) -> Option<Dictionary> {
        match obj {
            Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok().cloned(),
            Object::Dictionary(d) => Some(d.clone()),
            _ => None,
        }
    }
    let Some(root) = doc
        .catalog()
        .ok()
        .and_then(|c| c.get(b"Names").ok().cloned())
        .and_then(|names| node_dict(doc, &names))
        .and_then(|names| names.get(b"EmbeddedFiles").ok().cloned())
        .and_then(|ef| node_dict(doc, &ef))
    else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut stack = vec![(root, 0usize)];
    while let Some((node, depth)) = stack.pop() {
        if depth > 32 {
            continue;
        }
        if let Ok(pairs) = node.get(b"Names").and_then(Object::as_array) {
            for pair in pairs.chunks(2) {
                let [key, value] = pair else { continue };
                let Ok(name) = decode_text_string(key) else {
                    continue;
                };
                let filespec = match value {
                    Object::Reference(id) => Object::Reference(*id),
                    Object::Dictionary(d) => Object::Dictionary(d.clone()),
                    _ => continue,
                };
                out.push((name, filespec));
            }
        }
        if let Ok(kids) = node.get(b"Kids").and_then(Object::as_array) {
            for kid in kids.clone() {
                if let Some(dict) = node_dict(doc, &kid) {
                    stack.push((dict, depth + 1));
                }
            }
        }
    }
    out
}

/// Rewrite EmbeddedFiles as one flat node while preserving other name trees.
fn write_embedded_files(doc: &mut Document, mut entries: Vec<EmbeddedFileEntry>) -> PyResult<()> {
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    let mut flat = Vec::with_capacity(entries.len() * 2);
    for (name, filespec) in entries {
        flat.push(text_string(&name));
        flat.push(filespec);
    }
    let tree = Object::Dictionary(dictionary! { "Names" => Object::Array(flat) });
    // Mutate an indirect `/Names` target or the inline Catalog value in place.
    let names_ref = doc
        .catalog()
        .ok()
        .and_then(|c| c.get(b"Names").ok())
        .and_then(|n| n.as_reference().ok());
    match names_ref {
        Some(id) => {
            let names = doc
                .get_object_mut(id)
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            names.set("EmbeddedFiles", tree);
        }
        None => {
            let catalog = doc.catalog_mut().map_err(to_py_err)?;
            if !catalog.has(b"Names") {
                catalog.set("Names", Dictionary::new());
            }
            let names = catalog
                .get_mut(b"Names")
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            names.set("EmbeddedFiles", tree);
        }
    }
    Ok(())
}

#[pymethods]
impl _Document {
    /// Create an empty PDF document.
    #[new]
    fn new() -> PyResult<Self> {
        let mut document = Self::from_doc(Document::with_version("1.7"), None);
        document.ensure_page_tree().map_err(to_py_err)?;
        Ok(document)
    }

    /// Load from a file path.
    ///
    /// `password` decrypts encrypted PDFs. `max_decompressed_size` limits bytes
    /// per stream against decompression bombs; None is unlimited.
    #[staticmethod]
    #[pyo3(signature = (path, password=None, max_decompressed_size=None))]
    fn load(
        py: Python<'_>,
        path: &str,
        password: Option<String>,
        max_decompressed_size: Option<usize>,
    ) -> PyResult<Self> {
        let options = LoadOptions {
            password,
            max_decompressed_size,
            ..Default::default()
        };
        py.detach(|| {
            let data = std::fs::read(path)
                .map_err(|e| PdfError::new_err(format!("failed to load {path}: {e}")))?;
            let doc = Document::load_mem_with_options(&data, options)
                .map_err(|e| lopdf_err(Some(&format!("failed to load {path}")), &e))?;
            if !doc.is_encrypted()
                && let Some(limit) = max_decompressed_size
            {
                validate_decompression_limits(&doc, limit)?;
            }
            let hayro_source = (!doc.was_encrypted()).then_some(data);
            Ok(Self::from_doc(doc, hayro_source))
        })
    }

    /// Load from bytes with the same arguments as `load`.
    #[staticmethod]
    #[pyo3(signature = (data, password=None, max_decompressed_size=None))]
    fn load_bytes(
        py: Python<'_>,
        data: &[u8],
        password: Option<String>,
        max_decompressed_size: Option<usize>,
    ) -> PyResult<Self> {
        let options = LoadOptions {
            password,
            max_decompressed_size,
            ..Default::default()
        };
        py.detach(|| {
            let doc = Document::load_mem_with_options(data, options).map_err(to_py_err)?;
            if !doc.is_encrypted()
                && let Some(limit) = max_decompressed_size
            {
                validate_decompression_limits(&doc, limit)?;
            }
            let hayro_source = (!doc.was_encrypted()).then(|| data.to_vec());
            Ok(Self::from_doc(doc, hayro_source))
        })
    }

    /// Read metadata quickly without loading the complete document.
    ///
    /// Return `(Info string dict, page count, version, encrypted flag)`.
    #[staticmethod]
    #[pyo3(signature = (path, password=None))]
    fn load_metadata(
        py: Python<'_>,
        path: &str,
        password: Option<String>,
    ) -> PyResult<(BTreeMap<String, String>, u32, String, bool)> {
        py.detach(|| {
            let meta = match &password {
                Some(pw) => Document::load_metadata_with_password(path, pw),
                None => Document::load_metadata(path),
            }
            .map_err(|e| lopdf_err(Some(&format!("failed to load {path}")), &e))?;
            Ok(pdf_metadata_to_tuple(meta))
        })
    }

    /// Read metadata from bytes, returning the same shape as `load_metadata`.
    #[staticmethod]
    #[pyo3(signature = (data, password=None))]
    fn load_metadata_bytes(
        py: Python<'_>,
        data: &[u8],
        password: Option<String>,
    ) -> PyResult<(BTreeMap<String, String>, u32, String, bool)> {
        py.detach(|| {
            let meta = match &password {
                Some(pw) => Document::load_metadata_mem_with_password(data, pw),
                None => Document::load_metadata_mem(data),
            }
            .map_err(to_py_err)?;
            Ok(pdf_metadata_to_tuple(meta))
        })
    }

    /// Configure a CJK fallback font for rendering.
    ///
    /// `kind` is `sans` (default) or `serif`. `data` contains TTF/OTF/TTC bytes;
    /// `index` selects a TTC face.
    fn set_fallback_font(&mut self, kind: &str, data: Vec<u8>, index: u32) -> PyResult<()> {
        let slot = match kind {
            "sans" => &mut self.fallback_fonts.sans,
            "serif" => &mut self.fallback_fonts.serif,
            _ => {
                return Err(PdfError::new_err(format!(
                    "kind must be 'sans' or 'serif': {kind:?}"
                )));
            }
        };
        *slot = Some((Arc::new(data), index));
        self.invalidate_text_pages();
        Ok(())
    }

    /// Clear all CJK fallback-font configuration.
    fn clear_fallback_fonts(&mut self) {
        self.fallback_fonts = FallbackFonts::default();
        self.invalidate_text_pages();
    }

    /// Return whether the document remains encrypted.
    fn is_encrypted(&self) -> bool {
        self.doc.is_encrypted()
    }

    /// Return whether the document was encrypted at load; remains true after decryption.
    fn was_encrypted(&self) -> bool {
        self.doc.was_encrypted()
    }

    /// Check a user password without decrypting.
    fn authenticate_user_password(&self, password: &str) -> bool {
        self.doc.authenticate_user_password(password).is_ok()
    }

    /// Check an owner password without decrypting.
    fn authenticate_owner_password(&self, password: &str) -> bool {
        self.doc.authenticate_owner_password(password).is_ok()
    }

    /// Save to a file path.
    fn save(&mut self, py: Python<'_>, path: &str) -> PyResult<()> {
        py.detach(|| {
            self.doc
                .save(path)
                .map(|_| ())
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))
        })
    }

    /// Serialize to bytes.
    fn save_bytes(&mut self, py: Python<'_>) -> PyResult<Vec<u8>> {
        py.detach(|| {
            let mut buffer = Vec::new();
            self.doc
                .save_to(&mut buffer)
                .map_err(|e| PdfError::new_err(e.to_string()))?;
            Ok(buffer)
        })
    }

    /// Save with PDF 1.5+ object and xref streams.
    ///
    /// lopdf raises the version and changes xref type, mutating document state,
    /// so invalidate the rendering cache.
    fn save_with_object_streams(&mut self, py: Python<'_>, path: &str) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let file = std::fs::File::create(path)
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))?;
            let mut writer = std::io::BufWriter::new(file);
            self.doc
                .save_with_options(&mut writer, modern_save_options())
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))?;
            writer
                .into_inner()
                .map(|_| ())
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))
        })
    }

    /// Serialize with PDF 1.5+ object and xref streams.
    fn save_bytes_with_object_streams(&mut self, py: Python<'_>) -> PyResult<Vec<u8>> {
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let mut buffer = Vec::new();
            self.doc
                .save_with_options(&mut buffer, modern_save_options())
                .map_err(|e| PdfError::new_err(e.to_string()))?;
            Ok(buffer)
        })
    }

    /// Return the page count.
    fn page_count(&self) -> usize {
        self.doc.get_pages().len()
    }

    /// Return the PDF version string, such as `"1.7"`.
    fn version(&self) -> String {
        self.doc.version.clone()
    }

    /// Return Info dictionary strings as `{key: value}`.
    fn get_metadata(&self) -> BTreeMap<String, String> {
        let mut result = BTreeMap::new();
        let Some(info) = self.info_dict() else {
            return result;
        };
        for (key, value) in info.iter() {
            let resolved = match value {
                Object::Reference(id) => match self.doc.get_object(*id) {
                    Ok(object) => object,
                    Err(_) => continue,
                },
                other => other,
            };
            if let Ok(text) = decode_text_string(resolved) {
                result.insert(String::from_utf8_lossy(key).into_owned(), text);
            }
        }
        result
    }

    /// Set an Info entry, deleting it when the value is empty.
    fn set_metadata(&mut self, key: &str, value: &str) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let info_id = if let Ok(Object::Reference(id)) = self.doc.trailer.get(b"Info") {
            *id
        } else {
            // Move an inline dictionary to an indirect object, or create one.
            let existing = match self.doc.trailer.get(b"Info") {
                Ok(Object::Dictionary(dict)) => dict.clone(),
                _ => Dictionary::new(),
            };
            let id = self.doc.add_object(existing);
            self.doc.trailer.set("Info", id);
            id
        };
        let info = self
            .doc
            .get_object_mut(info_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        if value.is_empty() {
            info.remove(key.as_bytes());
        } else {
            info.set(key, text_string(value));
        }
        Ok(())
    }

    /// Delete a one-based page.
    fn delete_pages(&mut self, page_numbers: Vec<u32>) {
        self.invalidate_hayro_pdf();
        self.doc.delete_pages(&page_numbers);
    }

    /// Extract text from a one-based page.
    ///
    /// Collect glyph Unicode/positions through the hayro interpreter and
    /// assemble reading-order text. CJK fallbacks also apply to extraction.
    fn extract_text(&mut self, py: Python<'_>, page_numbers: Vec<u32>) -> PyResult<String> {
        let settings = self.interpreter_settings();
        py.detach(|| {
            let mut out = String::new();
            for number in &page_numbers {
                out.push_str(&self.text_page(*number, settings.clone())?.text());
            }
            Ok(out)
        })
    }

    /// Return layout for a one-based page.
    ///
    /// Return `(width, height, blocks)`, where block=`(bbox, lines)`,
    /// line=`(bbox, spans, words, direction, writing mode)`,
    /// span=`(bbox, text, size, origin, font, flags)`, and word=`(bbox, text)`.
    #[allow(clippy::type_complexity)]
    fn extract_layout(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<(f64, f64, Vec<crate::extract::BlockTuple>)> {
        let settings = self.interpreter_settings();
        py.detach(|| Ok(self.text_page(page_number, settings)?.layout()))
    }

    /// Detect high-confidence vector-bordered tables on a one-based page.
    fn find_tables(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<Vec<crate::extract::TableTuple>> {
        let settings = self.interpreter_settings();
        py.detach(|| Ok(self.text_page(page_number, settings)?.tables()))
    }

    /// Extract images drawn on a one-based page.
    ///
    /// Return `(width, height, bbox, "jpeg"/"png", bytes)` items.
    fn extract_images(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<Vec<crate::extract::ImageTuple>> {
        let settings = self.interpreter_settings();
        let pdf = self.hayro_view()?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            Ok(crate::extract::extract_page_images(pdf, page, settings))
        })
    }

    /// Search a one-based page case-insensitively.
    fn search_page(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        needle: &str,
    ) -> PyResult<Vec<(f64, f64, f64, f64)>> {
        let settings = self.interpreter_settings();
        py.detach(|| Ok(self.text_page(page_number, settings)?.search(needle)))
    }

    /// Append every page from another document.
    fn merge(&mut self, py: Python<'_>, other: &Self) -> PyResult<()> {
        let count = u32::try_from(other.doc.get_pages().len())
            .map_err(|e| PdfError::new_err(e.to_string()))?;
        let all: Vec<u32> = (1..=count).collect();
        self.merge_pages(py, other, all, None)
    }

    /// Import specified one-based pages from another document in order.
    ///
    /// `position` is a zero-based insertion point; None appends. Flatten the
    /// page tree under root while inserting.
    fn merge_pages(
        &mut self,
        py: Python<'_>,
        other: &Self,
        page_numbers: Vec<u32>,
        position: Option<usize>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        py.detach(|| {
            // Reserve Pages/Catalog IDs in an empty target to avoid source collisions.
            let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
            let new_ids = self.transplant_pages(other, &page_numbers, pages_id)?;
            match position {
                None => self.append_pages(pages_id, new_ids)?,
                Some(_) => {
                    let order = self.spliced_page_order(new_ids, position);
                    self.rebuild_page_tree(pages_id, order)?;
                }
            }
            // transplant_pages initially moves all non-page objects. Prune
            // attachments/metadata unreachable from selected pages or hidden
            // source data remains even for a full-range append.
            self.doc.prune_objects();
            Ok(())
        })
    }

    /// Insert a blank page at zero-based `position`; None appends.
    fn new_page(&mut self, position: Option<usize>, width: f32, height: f32) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        if !width.is_finite() || !height.is_finite() || width <= 0.0 || height <= 0.0 {
            return Err(PdfError::new_err(format!(
                "width / height must be positive finite values within PDF real-number range: ({width:?}, {height:?})"
            )));
        }
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
        let page_id = self.doc.add_object(dictionary! {
            "Type" => "Page",
            "Parent" => pages_id,
            "MediaBox" => Object::Array(vec![
                Object::Real(0.0),
                Object::Real(0.0),
                Object::Real(width),
                Object::Real(height),
            ]),
        });
        match position {
            None => self.append_pages(pages_id, vec![page_id]),
            Some(_) => {
                let order = self.spliced_page_order(vec![page_id], position);
                self.rebuild_page_tree(pages_id, order)?;
                self.doc.prune_objects();
                Ok(())
            }
        }
    }

    /// Copy a one-based page to zero-based `position`; None appends.
    ///
    /// The page dictionary is an independent copy with inheritance materialized;
    /// Contents and Resources remain shared with the source page.
    fn copy_page(&mut self, page_number: u32, position: Option<usize>) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
        let source_id = self.page_id(page_number)?;
        let mut dict = resolve_inherited_page_dict(&self.doc, source_id).map_err(to_py_err)?;
        dict.set("Parent", pages_id);
        let new_id = self.doc.add_object(Object::Dictionary(dict));
        match position {
            None => self.append_pages(pages_id, vec![new_id]),
            Some(_) => {
                let order = self.spliced_page_order(vec![new_id], position);
                self.rebuild_page_tree(pages_id, order)?;
                self.doc.prune_objects();
                Ok(())
            }
        }
    }

    /// Keep specified one-based pages in order, also supporting reordering.
    ///
    /// PDF page-tree Parent must be unique, so duplicate selections require copies.
    fn select(&mut self, page_numbers: Vec<u32>) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let pages = self.doc.get_pages();
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;

        // For repeated pages, create a copy with inheritance materialized because
        // PDF page-tree Parent references must be unique.
        let mut seen = HashSet::new();
        let mut ordered = Vec::with_capacity(page_numbers.len());
        for number in &page_numbers {
            let page_id = *pages
                .get(number)
                .ok_or_else(|| PdfError::new_err(format!("page {number} does not exist")))?;
            let use_id = if seen.insert(page_id) {
                page_id
            } else {
                let dict = resolve_inherited_page_dict(&self.doc, page_id).map_err(to_py_err)?;
                self.doc.add_object(Object::Dictionary(dict))
            };
            ordered.push(use_id);
        }
        self.rebuild_page_tree(pages_id, ordered)?;

        // Remove pages and intermediate nodes that became unreachable.
        self.doc.prune_objects();
        Ok(())
    }

    /// Render a one-based page to PNG.
    ///
    /// `background` is fill RGBA in 0–255; None preserves transparency.
    fn render_page_png(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: f32,
        background: Option<(u8, u8, u8, u8)>,
    ) -> PyResult<Vec<u8>> {
        let pixmap = self.render_pixmap_impl(py, page_number, scale, background)?;
        // PNG encoding can cost more than rasterization, so release the GIL and
        // use Fast/fdeflate. Balanced is tens of times slower for about 10%
        // smaller output and made PNG the dominant render cost in benchmarks.
        py.detach(|| {
            let width = u32::from(pixmap.width());
            let height = u32::from(pixmap.height());
            let data = rgba_bytes(pixmap);
            crate::extract::encode_png(
                width,
                height,
                png::ColorType::Rgba,
                &data,
                png::Compression::Fast,
            )
            .ok_or_else(|| PdfError::new_err("failed to encode PNG"))
        })
    }

    /// Render a one-based page and return a straight-alpha RGBA8 Pixmap.
    fn render_page_pixmap(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: f32,
        background: Option<(u8, u8, u8, u8)>,
    ) -> PyResult<crate::pixmap::Pixmap> {
        let pixmap = self.render_pixmap_impl(py, page_number, scale, background)?;
        let width = u32::from(pixmap.width());
        let height = u32::from(pixmap.height());
        // Release the GIL: unpremultiplication and byte conversion are costly.
        let data = py.detach(|| rgba_bytes(pixmap));
        Ok(crate::pixmap::Pixmap {
            width,
            height,
            data,
        })
    }

    /// Drain hayro warning messages accumulated by the latest operation.
    fn take_warnings(&mut self) -> Vec<String> {
        self.pending_warnings
            .lock()
            .map(|mut pending| std::mem::take(&mut *pending))
            .unwrap_or_default()
    }

    /// Render a one-based page to an SVG string.
    fn render_page_svg(&mut self, py: Python<'_>, page_number: u32) -> PyResult<String> {
        let interpreter_settings = self.interpreter_settings();
        py.detach(|| {
            let pdf = self.hayro_view()?;
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            let cache = hayro_svg::RenderCache::new();
            let settings = hayro_svg::SvgRenderSettings::default();
            Ok(hayro_svg::convert(
                page,
                &cache,
                &interpreter_settings,
                &settings,
            ))
        })
    }

    /// Return the TOC as flat `(level, title, one-based page number)` entries.
    ///
    /// Return empty when absent and skip entries that do not resolve to a page.
    fn get_toc(&self) -> PyResult<Vec<(u32, String, u32)>> {
        match self.doc.get_toc() {
            Ok(toc) => Ok(toc
                .toc
                .into_iter()
                .map(|t| (t.level as u32, t.title, t.page as u32))
                .collect()),
            // Missing catalog Outlines raises DictKey; treat it as an empty TOC.
            Err(lopdf::Error::NoOutline) => Ok(Vec::new()),
            Err(lopdf::Error::DictKey(ref key)) if key == "Outlines" => Ok(Vec::new()),
            Err(e) => Err(to_py_err(e)),
        }
    }

    /// Replace the TOC from `(level, title, one-based page)` entries; empty deletes.
    ///
    /// Python validates one-based levels and maximum +1 depth. lopdf writes
    /// non-ASCII titles as UTF-16BE with a BOM.
    fn set_toc(&mut self, entries: Vec<(u32, String, u32)>) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        // Discard existing outlines and construction state.
        self.doc.bookmarks.clear();
        self.doc.bookmark_table.clear();
        self.doc.max_bookmark_id = 0;
        if let Ok(catalog) = self.doc.catalog_mut() {
            catalog.remove(b"Outlines");
        }
        if entries.is_empty() {
            self.doc.prune_objects();
            return Ok(());
        }
        let pages = self.doc.get_pages();
        // parents[level - 1] = latest bookmark ID at that level.
        let mut parents: Vec<u32> = Vec::new();
        for (level, title, page) in entries {
            let page_id = *pages
                .get(&page)
                .ok_or_else(|| PdfError::new_err(format!("page {page} does not exist")))?;
            let level = level as usize;
            let parent = if level >= 2 {
                parents.get(level - 2).copied()
            } else {
                None
            };
            let id = self
                .doc
                .add_bookmark(Bookmark::new(title, [0.0, 0.0, 0.0], 0, page_id), parent);
            parents.truncate(level - 1);
            parents.push(id);
        }
        if let Some(outline_id) = self.doc.build_outline() {
            self.doc
                .catalog_mut()
                .map_err(to_py_err)?
                .set("Outlines", Object::Reference(outline_id));
        }
        // Prune old outline objects.
        self.doc.prune_objects();
        Ok(())
    }

    /// Save an AES-256 encrypted clone to a file while this document stays plaintext.
    fn save_encrypted(
        &self,
        py: Python<'_>,
        path: &str,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
    ) -> PyResult<()> {
        py.detach(|| {
            let mut cloned = self.encrypted_clone(
                user_password,
                owner_password,
                permissions,
                file_encryption_key,
            )?;
            cloned
                .save(path)
                .map(|_| ())
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))
        })
    }

    /// Serialize an AES-256 encrypted clone while this document stays plaintext.
    fn save_bytes_encrypted(
        &self,
        py: Python<'_>,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
    ) -> PyResult<Vec<u8>> {
        py.detach(|| {
            let mut cloned = self.encrypted_clone(
                user_password,
                owner_password,
                permissions,
                file_encryption_key,
            )?;
            let mut buffer = Vec::new();
            cloned
                .save_to(&mut buffer)
                .map_err(|e| PdfError::new_err(e.to_string()))?;
            Ok(buffer)
        })
    }

    /// Return inherited, normalized 0..360 rotation for a one-based page.
    fn get_page_rotation(&self, page_number: u32) -> PyResult<i64> {
        let page_id = self.page_id(page_number)?;
        match self.resolve_page_attr(page_id, b"Rotate")? {
            Some(obj) => Ok(obj.as_i64().map_err(to_py_err)?.rem_euclid(360)),
            None => Ok(0),
        }
    }

    /// Set rotation for a one-based page; Python validates the value.
    fn set_page_rotation(&mut self, page_number: u32, rotation: i64) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let page_id = self.page_id(page_number)?;
        let dict = self
            .doc
            .get_object_mut(page_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        dict.set("Rotate", rotation);
        Ok(())
    }

    /// Return a resolved page box such as MediaBox/CropBox, or None when absent.
    fn get_page_box(&self, page_number: u32, key: &str) -> PyResult<Option<(f64, f64, f64, f64)>> {
        let page_id = self.page_id(page_number)?;
        let Some(obj) = self.resolve_page_attr(page_id, key.as_bytes())? else {
            return Ok(None);
        };
        let arr = obj.as_array().map_err(to_py_err)?;
        if arr.len() != 4 {
            return Err(PdfError::new_err(format!(
                "{key} must be a 4-element array ({} elements given)",
                arr.len()
            )));
        }
        let mut values = [0f64; 4];
        for (slot, item) in values.iter_mut().zip(arr) {
            let resolved = match item {
                Object::Reference(id) => self.doc.get_object(*id).map_err(to_py_err)?,
                other => other,
            };
            *slot = f64::from(resolved.as_float().map_err(to_py_err)?);
        }
        Ok(Some((values[0], values[1], values[2], values[3])))
    }

    /// Set a box on a one-based page; Python validates the rectangle.
    fn set_page_box(
        &mut self,
        page_number: u32,
        key: &str,
        x0: f64,
        y0: f64,
        x1: f64,
        y1: f64,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let x0 = checked_pdf_real(x0, "x0")?;
        let y0 = checked_pdf_real(y0, "y0")?;
        let x1 = checked_pdf_real(x1, "x1")?;
        let y1 = checked_pdf_real(y1, "y1")?;
        let page_id = self.page_id(page_number)?;
        let dict = self
            .doc
            .get_object_mut(page_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        dict.set(
            key,
            Object::Array(vec![
                Object::Real(x0),
                Object::Real(y0),
                Object::Real(x1),
                Object::Real(y1),
            ]),
        );
        Ok(())
    }

    /// Compress streams.
    fn compress(&mut self, py: Python<'_>) {
        self.invalidate_hayro_pdf();
        py.detach(|| self.doc.compress());
    }

    /// Decompress streams.
    fn decompress(&mut self, py: Python<'_>) {
        self.invalidate_hayro_pdf();
        py.detach(|| self.doc.decompress());
    }

    /// Remove unreferenced objects.
    fn prune_objects(&mut self) {
        self.invalidate_hayro_pdf();
        self.doc.prune_objects();
    }

    /// Draw JPEG/PNG bytes into display `rect` on a one-based page.
    ///
    /// `rect` uses top-left-origin page display space, including rotation.
    /// Drawing only adds a content stream and never rewrites existing content.
    fn insert_image(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        data: Vec<u8>,
        keep_proportion: bool,
        overlay: bool,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            let parts = draw::parse_image(&data).map_err(PdfError::new_err)?.ok_or_else(|| {
                PdfError::new_err(
                    "unsupported image format (pass JPEG or PNG; convert other formats with Pillow or similar first)",
                )
            })?;
            let content = draw::PlacedContent::Image {
                width: parts.width,
                height: parts.height,
            };
            let matrix = draw::placement_matrix(
                crop,
                rotation,
                [rect.0, rect.1, rect.2, rect.3],
                &content,
                keep_proportion,
            );
            let xobj_id = draw::add_image_xobject(&mut self.doc, parts).map_err(PdfError::new_err)?;
            self.bake_page_attrs(page_id)?;
            let name = format!("PyloIm{}", xobj_id.0);
            self.doc
                .add_xobject(page_id, name.as_bytes(), xobj_id)
                .map_err(to_py_err)?;
            draw::push_content(&mut self.doc, page_id, draw::draw_ops(matrix, &name), overlay).map_err(to_py_err)
        })
    }

    /// Import a one-based page from `other` as a Form XObject into display `rect`.
    ///
    /// Renumber source objects as in merge and wrap page content in a Form
    /// XObject to preserve vectors.
    // This boundary mirrors the Python signature, so the argument count is intentional.
    #[allow(clippy::too_many_arguments)]
    fn show_pdf_page(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        other: &Self,
        src_page_number: u32,
        keep_proportion: bool,
        overlay: bool,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            let starting_id = self
                .doc
                .max_id
                .checked_add(1)
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
            let mut other_doc = other.doc.clone();
            other_doc.renumber_objects_with(starting_id);
            let src_id = *other_doc.get_pages().get(&src_page_number).ok_or_else(|| {
                PdfError::new_err(format!("source page {src_page_number} does not exist"))
            })?;
            let src_dict = resolve_inherited_page_dict(&other_doc, src_id).map_err(to_py_err)?;

            // Source display geometry: CropBox, MediaBox, A4; rotation 0..360.
            let src_crop = resolve_box(&other_doc, &src_dict, b"CropBox")
                .or_else(|| resolve_box(&other_doc, &src_dict, b"MediaBox"))
                .unwrap_or([0.0, 0.0, 595.0, 842.0]);
            let src_rotation = src_dict
                .get(b"Rotate")
                .ok()
                .and_then(|o| resolve_i64(&other_doc, o))
                .unwrap_or(0)
                .rem_euclid(360);

            // Wrap in q/Q to contain imbalance without re-encoding source content.
            let mut form_content = b"q\n".to_vec();
            form_content.extend_from_slice(&other_doc.get_page_content(src_id));
            form_content.extend_from_slice(b"\nQ\n");

            let mut form_dict = dictionary! {
                "Type" => "XObject",
                "Subtype" => "Form",
                "FormType" => 1,
                "BBox" => Object::Array(src_crop.iter().map(|&v| Object::Real(v as f32)).collect()),
            };
            if let Ok(res) = src_dict.get(b"Resources") {
                form_dict.set("Resources", res.clone());
            }
            if let Ok(group) = src_dict.get(b"Group") {
                form_dict.set("Group", group.clone());
            }

            // Import non-page-tree objects referenced by Resources.
            let new_max_id = other_doc.max_id;
            for (id, object) in other_doc.objects {
                match object.type_name().unwrap_or(b"") {
                    b"Catalog" | b"Pages" | b"Page" => {}
                    _ => {
                        self.doc.objects.insert(id, object);
                    }
                }
            }
            self.doc.max_id = new_max_id;

            let form_id = self
                .doc
                .add_object(Stream::new(form_dict, form_content).with_compression(false));
            let content = draw::PlacedContent::Form {
                crop: src_crop,
                rotation: src_rotation,
            };
            let matrix = draw::placement_matrix(
                crop,
                rotation,
                [rect.0, rect.1, rect.2, rect.3],
                &content,
                keep_proportion,
            );
            self.bake_page_attrs(page_id)?;
            let name = format!("PyloFm{}", form_id.0);
            self.doc
                .add_xobject(page_id, name.as_bytes(), form_id)
                .map_err(to_py_err)?;
            draw::push_content(
                &mut self.doc,
                page_id,
                draw::draw_ops(matrix, &name),
                overlay,
            )
            .map_err(to_py_err)?;
            // All source non-page objects were moved initially; prune assets and
            // attachments unreachable from the Form XObject.
            self.doc.prune_objects();
            Ok(())
        })
    }

    /// Read annotations from a one-based page.
    ///
    /// Each item is `(Subtype, display Rect, Contents, URI)`. Rect uses rotated
    /// top-left-origin display space; Contents/URI are optional.
    fn read_annotations(&self, page_number: u32) -> PyResult<Vec<AnnotationTuple>> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        let page = self
            .doc
            .get_object(page_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?;
        let annots = match page.get(b"Annots") {
            Ok(Object::Reference(id)) => {
                match self.doc.get_object(*id).and_then(Object::as_array) {
                    Ok(arr) => arr.clone(),
                    Err(_) => return Ok(Vec::new()),
                }
            }
            Ok(Object::Array(arr)) => arr.clone(),
            _ => return Ok(Vec::new()),
        };
        let mut out = Vec::new();
        for item in annots {
            let dict = match &item {
                Object::Reference(id) => match self.doc.get_object(*id).and_then(Object::as_dict) {
                    Ok(d) => d,
                    Err(_) => continue,
                },
                Object::Dictionary(d) => d,
                _ => continue,
            };
            let subtype = match dict.get(b"Subtype").and_then(Object::as_name) {
                Ok(name) => String::from_utf8_lossy(name).into_owned(),
                Err(_) => continue,
            };
            let Some(rect) = resolve_box(&self.doc, dict, b"Rect") else {
                continue;
            };
            let display = draw::pdf_rect_to_display(crop, rotation, rect);
            let contents = dict
                .get(b"Contents")
                .ok()
                .map(|o| match o {
                    Object::Reference(id) => self.doc.get_object(*id).unwrap_or(o),
                    other => other,
                })
                .and_then(|o| decode_text_string(o).ok());
            let uri = dict
                .get(b"A")
                .ok()
                .and_then(|a| match a {
                    Object::Reference(id) => {
                        self.doc.get_object(*id).and_then(Object::as_dict).ok()
                    }
                    Object::Dictionary(d) => Some(d),
                    _ => None,
                })
                .filter(|action| matches!(action.get(b"S").and_then(Object::as_name), Ok(b"URI")))
                .and_then(|action| action.get(b"URI").ok())
                .and_then(|o| o.as_str().ok())
                .map(|s| String::from_utf8_lossy(s).into_owned());
            out.push((
                subtype,
                (display[0], display[1], display[2], display[3]),
                contents,
                uri,
            ));
        }
        Ok(out)
    }

    /// Read link annotations from a one-based page and resolve destinations.
    ///
    /// Support `/A` actions (URI, GoTo, GoToR, Launch, Named) and direct `/Dest`.
    /// Resolve GoTo names from `/Names` trees and legacy `/Dests`.
    fn read_links(&self, page_number: u32) -> PyResult<Vec<LinkTuple>> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        let page = self
            .doc
            .get_object(page_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?;
        let annots = match page.get(b"Annots") {
            Ok(Object::Reference(id)) => {
                match self.doc.get_object(*id).and_then(Object::as_array) {
                    Ok(arr) => arr.clone(),
                    Err(_) => return Ok(Vec::new()),
                }
            }
            Ok(Object::Array(arr)) => arr.clone(),
            _ => return Ok(Vec::new()),
        };
        // Build ObjectId → lopdf page-number lookup for destination resolution.
        let page_map: BTreeMap<ObjectId, u32> = self
            .doc
            .get_pages()
            .into_iter()
            .map(|(number, id)| (id, number))
            .collect();
        let mut out = Vec::new();
        for item in annots {
            let dict = match &item {
                Object::Reference(id) => match self.doc.get_object(*id).and_then(Object::as_dict) {
                    Ok(d) => d,
                    Err(_) => continue,
                },
                Object::Dictionary(d) => d,
                _ => continue,
            };
            if !matches!(dict.get(b"Subtype").and_then(Object::as_name), Ok(b"Link")) {
                continue;
            }
            let Some(rect) = resolve_box(&self.doc, dict, b"Rect") else {
                continue;
            };
            let display = draw::pdf_rect_to_display(crop, rotation, rect);
            let rect_tuple = (display[0], display[1], display[2], display[3]);

            let action = dict.get(b"A").ok().and_then(|a| match a {
                Object::Reference(id) => self.doc.get_object(*id).and_then(Object::as_dict).ok(),
                Object::Dictionary(d) => Some(d),
                _ => None,
            });
            if let Some(action) = action {
                match action.get(b"S").and_then(Object::as_name) {
                    Ok(b"URI") => {
                        let uri = action
                            .get(b"URI")
                            .ok()
                            .and_then(|o| deref_object(&self.doc, o).as_str().ok())
                            .map(|s| String::from_utf8_lossy(s).into_owned());
                        out.push((
                            "uri".to_string(),
                            rect_tuple,
                            uri,
                            None,
                            None,
                            None,
                            None,
                            None,
                        ));
                    }
                    Ok(b"GoTo") => {
                        if let Ok(dest) = action.get(b"D") {
                            let (page, to, zoom, name) = self.resolve_dest(dest, &page_map);
                            out.push((
                                "goto".to_string(),
                                rect_tuple,
                                None,
                                page,
                                to,
                                zoom,
                                None,
                                name,
                            ));
                        }
                    }
                    Ok(b"GoToR") => {
                        let file = action
                            .get(b"F")
                            .ok()
                            .and_then(|o| filespec_name(&self.doc, o));
                        // External-document destinations retain names without page resolution.
                        let name =
                            action
                                .get(b"D")
                                .ok()
                                .and_then(|d| match deref_object(&self.doc, d) {
                                    Object::Name(n) | Object::String(n, _) => {
                                        Some(String::from_utf8_lossy(n).into_owned())
                                    }
                                    _ => None,
                                });
                        out.push((
                            "gotor".to_string(),
                            rect_tuple,
                            None,
                            None,
                            None,
                            None,
                            file,
                            name,
                        ));
                    }
                    Ok(b"Launch") => {
                        let file = action
                            .get(b"F")
                            .ok()
                            .and_then(|o| filespec_name(&self.doc, o));
                        out.push((
                            "launch".to_string(),
                            rect_tuple,
                            None,
                            None,
                            None,
                            None,
                            file,
                            None,
                        ));
                    }
                    Ok(b"Named") => {
                        let name = action
                            .get(b"N")
                            .ok()
                            .and_then(|o| deref_object(&self.doc, o).as_name().ok())
                            .map(|n| String::from_utf8_lossy(n).into_owned());
                        out.push((
                            "named".to_string(),
                            rect_tuple,
                            None,
                            None,
                            None,
                            None,
                            None,
                            name,
                        ));
                    }
                    _ => {}
                }
            } else if let Ok(dest) = dict.get(b"Dest") {
                let (page, to, zoom, name) = self.resolve_dest(dest, &page_map);
                out.push((
                    "goto".to_string(),
                    rect_tuple,
                    None,
                    page,
                    to,
                    zoom,
                    None,
                    name,
                ));
            }
        }
        Ok(out)
    }

    /// Add a highlight annotation to a one-based page.
    ///
    /// `rects` use display coordinates. Generate Acrobat-order QuadPoints and
    /// an `AP /N` appearance with Multiply blending for hayro and other viewers.
    fn add_highlight_annotation(
        &mut self,
        page_number: u32,
        rects: Vec<(f64, f64, f64, f64)>,
        color: (f64, f64, f64),
        opacity: f64,
        content: Option<String>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;

        let quads: Vec<[(f64, f64); 4]> = rects
            .iter()
            .map(|&(x0, y0, x1, y1)| draw::display_rect_quad_pdf(crop, rotation, [x0, y0, x1, y1]))
            .collect();
        let all_points: Vec<(f64, f64)> = quads.iter().flatten().copied().collect();
        let bbox = draw::bounding_rect(&all_points);

        // Appearance BBox equals annotation Rect and draws in page space.
        let gs_id = self.doc.add_object(dictionary! {
            "Type" => "ExtGState",
            "BM" => Object::Name(b"Multiply".to_vec()),
            "CA" => Object::Real(opacity as f32),
            "ca" => Object::Real(opacity as f32),
        });
        let form_dict = dictionary! {
            "Type" => "XObject",
            "Subtype" => "Form",
            "FormType" => 1,
            "BBox" => Object::Array(bbox.iter().map(|&v| Object::Real(v as f32)).collect()),
            "Group" => dictionary! { "Type" => "Group", "S" => "Transparency" },
            "Resources" => dictionary! {
                "ExtGState" => dictionary! { "PyloGS" => Object::Reference(gs_id) },
            },
        };
        let ap_id = self.doc.add_object(
            Stream::new(form_dict, draw::highlight_ap_ops(&quads, color)).with_compression(false),
        );

        let quad_points: Vec<Object> = quads
            .iter()
            .flatten()
            .flat_map(|&(x, y)| [Object::Real(x as f32), Object::Real(y as f32)])
            .collect();
        let mut annot = dictionary! {
            "Type" => "Annot",
            "Subtype" => "Highlight",
            "Rect" => Object::Array(bbox.iter().map(|&v| Object::Real(v as f32)).collect()),
            "QuadPoints" => Object::Array(quad_points),
            "C" => Object::Array(vec![
                Object::Real(color.0 as f32),
                Object::Real(color.1 as f32),
                Object::Real(color.2 as f32),
            ]),
            "CA" => Object::Real(opacity as f32),
            // Printable flag.
            "F" => 4,
            "P" => page_id,
            "AP" => dictionary! { "N" => Object::Reference(ap_id) },
        };
        if let Some(text) = content {
            annot.set("Contents", text_string(&text));
        }
        let annot_id = self.doc.add_object(annot);
        self.push_page_annotation(page_id, annot_id)
    }

    /// Add a URI link annotation to display `rect` on a one-based page.
    fn add_link_annotation(
        &mut self,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        uri: String,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        let quad = draw::display_rect_quad_pdf(crop, rotation, [rect.0, rect.1, rect.2, rect.3]);
        let bbox = draw::bounding_rect(&quad);
        let annot_id = self.doc.add_object(dictionary! {
            "Type" => "Annot",
            "Subtype" => "Link",
            "Rect" => Object::Array(bbox.iter().map(|&v| Object::Real(v as f32)).collect()),
            "Border" => Object::Array(vec![0.into(), 0.into(), 0.into()]),
            "F" => 4,
            "P" => page_id,
            "A" => dictionary! {
                "Type" => "Action",
                "S" => "URI",
                "URI" => Object::string_literal(uri),
            },
        });
        self.push_page_annotation(page_id, annot_id)
    }

    /// Read the XMP PDF/A claim from `pdfaid:part` and conformance.
    ///
    /// This reads a self-declaration rather than validating compliance; use
    /// veraPDF for validation. PDF/A-4 without conformance returns an empty string.
    fn pdfa_claim(&self) -> Option<(i64, String)> {
        let catalog = self.doc.catalog().ok()?;
        let meta_ref = catalog.get(b"Metadata").ok()?.as_reference().ok()?;
        let stream = self.doc.get_object(meta_ref).ok()?.as_stream().ok()?;
        let data = stream
            .decompressed_content()
            .unwrap_or_else(|_| stream.content.clone());
        let xmp = String::from_utf8_lossy(&data);
        let part: i64 = xmp_value(&xmp, "pdfaid:part")?.parse().ok()?;
        let conformance = xmp_value(&xmp, "pdfaid:conformance").unwrap_or_default();
        Some((part, conformance))
    }

    /// Read page-label definitions from the PageLabels number tree.
    ///
    /// Each item is `(start index, style, prefix, first number)`. Recurse through
    /// Kids and return entries sorted by start page.
    fn get_page_labels(&self) -> Vec<(i64, Option<String>, Option<String>, i64)> {
        let Some(root) = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"PageLabels").ok().cloned())
            .and_then(|o| deref_dict(&self.doc, &o))
        else {
            return Vec::new();
        };
        let mut out = Vec::new();
        let mut stack = vec![(root, 0usize)];
        while let Some((node, depth)) = stack.pop() {
            if depth > 32 {
                continue;
            }
            if let Ok(nums) = node.get(b"Nums").and_then(Object::as_array) {
                for pair in nums.chunks(2) {
                    let [key, value] = pair else { continue };
                    let Some(start) = resolve_i64(&self.doc, key) else {
                        continue;
                    };
                    let Some(label) = deref_dict(&self.doc, value) else {
                        continue;
                    };
                    let style = label
                        .get(b"S")
                        .and_then(Object::as_name)
                        .ok()
                        .map(|n| String::from_utf8_lossy(n).into_owned());
                    let prefix = label
                        .get(b"P")
                        .ok()
                        .and_then(|o| decode_text_string(o).ok());
                    let st = label
                        .get(b"St")
                        .ok()
                        .and_then(|o| resolve_i64(&self.doc, o))
                        .unwrap_or(1);
                    out.push((start, style, prefix, st));
                }
            }
            if let Ok(kids) = node.get(b"Kids").and_then(Object::as_array) {
                for kid in kids.clone() {
                    if let Some(dict) = deref_dict(&self.doc, &kid) {
                        stack.push((dict, depth + 1));
                    }
                }
            }
        }
        out.sort_by_key(|(start, _, _, _)| *start);
        out
    }

    /// Write labels as a flat number tree; empty removes it, Python validates.
    fn set_page_labels(
        &mut self,
        labels: Vec<(i64, Option<String>, Option<String>, i64)>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let mut nums = Vec::with_capacity(labels.len() * 2);
        for (start, style, prefix, st) in labels {
            let mut label = Dictionary::new();
            if let Some(s) = style {
                label.set("S", Object::Name(s.into_bytes()));
            }
            if let Some(p) = prefix {
                label.set("P", text_string(&p));
            }
            if st != 1 {
                label.set("St", st);
            }
            nums.push(Object::Integer(start));
            nums.push(Object::Dictionary(label));
        }
        let catalog = self.doc.catalog_mut().map_err(to_py_err)?;
        if nums.is_empty() {
            catalog.remove(b"PageLabels");
        } else {
            catalog.set("PageLabels", dictionary! { "Nums" => Object::Array(nums) });
        }
        Ok(())
    }

    /// Return AcroForm fields as `(full name, kind, value)`.
    ///
    /// Kind is text/checkbox/radio/button/combobox/listbox/signature. Value is
    /// stringified `/V`, including state names such as Yes/Off, or None.
    fn get_form_fields(&self) -> Vec<(String, String, Option<String>)> {
        self.collect_form_fields()
            .into_iter()
            .map(|(name, _, ft, ff, v)| {
                let kind = match ft.as_str() {
                    "Tx" => "text",
                    "Sig" => "signature",
                    "Ch" => {
                        if ff & (1 << 17) != 0 {
                            "combobox"
                        } else {
                            "listbox"
                        }
                    }
                    "Btn" => {
                        if ff & (1 << 16) != 0 {
                            "button"
                        } else if ff & (1 << 15) != 0 {
                            "radio"
                        } else {
                            "checkbox"
                        }
                    }
                    _ => "unknown",
                };
                let value = v.and_then(|obj| match &obj {
                    Object::Name(n) => Some(String::from_utf8_lossy(n).into_owned()),
                    Object::String(..) => decode_text_string(&obj).ok(),
                    Object::Reference(id) => self
                        .doc
                        .get_object(*id)
                        .ok()
                        .and_then(|o| decode_text_string(o).ok()),
                    Object::Array(items) => Some(
                        items
                            .iter()
                            .filter_map(|i| decode_text_string(i).ok())
                            .collect::<Vec<_>>()
                            .join(", "),
                    ),
                    _ => None,
                });
                (name, kind.to_owned(), value)
            })
            .collect()
    }

    /// Return checkbox/radio state names from widget `AP /N` keys.
    fn form_button_states(&self, name: &str) -> PyResult<Vec<String>> {
        let (field_id, ft) = self
            .collect_form_fields()
            .into_iter()
            .find(|(n, ..)| n == name)
            .map(|(_, id, ft, ..)| (id, ft))
            .ok_or_else(|| PdfError::new_err(format!("form field not found: {name:?}")))?;
        if ft != "Btn" {
            return Ok(Vec::new());
        }
        let mut states = Vec::new();
        for widget_id in self.field_widgets(field_id) {
            let Ok(widget) = self.doc.get_object(widget_id).and_then(Object::as_dict) else {
                continue;
            };
            let Some(ap) = widget
                .get(b"AP")
                .ok()
                .and_then(|o| deref_dict(&self.doc, o))
            else {
                continue;
            };
            let Some(normal) = ap.get(b"N").ok().and_then(|o| deref_dict(&self.doc, o)) else {
                continue;
            };
            for (key, _) in normal.iter() {
                let state = String::from_utf8_lossy(key).into_owned();
                if !states.contains(&state) {
                    states.push(state);
                }
            }
        }
        Ok(states)
    }

    /// Set a form-field value and enable NeedAppearances.
    ///
    /// Write Tx/Ch as text and Btn as a Name state. Update each Btn widget `/AS`,
    /// falling back to Off when the requested state is absent from AP.
    fn set_form_field(&mut self, name: &str, value: &str) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (field_id, ft) = self
            .collect_form_fields()
            .into_iter()
            .find(|(n, ..)| n == name)
            .map(|(_, id, ft, ..)| (id, ft))
            .ok_or_else(|| PdfError::new_err(format!("form field not found: {name:?}")))?;
        match ft.as_str() {
            "Tx" | "Ch" => {
                let dict = self
                    .doc
                    .get_object_mut(field_id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                dict.set("V", text_string(value));
            }
            "Btn" => {
                {
                    let dict = self
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                        .map_err(to_py_err)?;
                    dict.set("V", Object::Name(value.as_bytes().to_vec()));
                }
                // Match each widget `/AS` to V; use Off when absent from AP.
                for widget_id in self.field_widgets(field_id) {
                    let has_state = self
                        .doc
                        .get_object(widget_id)
                        .and_then(Object::as_dict)
                        .ok()
                        .and_then(|w| w.get(b"AP").ok().and_then(|o| deref_dict(&self.doc, o)))
                        .and_then(|ap| ap.get(b"N").ok().and_then(|o| deref_dict(&self.doc, o)))
                        .is_some_and(|normal| normal.has(value.as_bytes()));
                    let state = if has_state { value } else { "Off" };
                    if let Ok(widget) = self
                        .doc
                        .get_object_mut(widget_id)
                        .and_then(Object::as_dict_mut)
                    {
                        widget.set("AS", Object::Name(state.as_bytes().to_vec()));
                    }
                }
            }
            "Sig" => {
                return Err(PdfError::new_err(
                    "filling signature fields is not supported (see the pyHanko integration for digital signatures)",
                ));
            }
            other => {
                return Err(PdfError::new_err(format!(
                    "unsupported field type: {other:?}"
                )));
            }
        }
        self.set_need_appearances()
    }

    /// Return sorted attachment names independent of name-tree order.
    fn embfile_names(&self) -> Vec<String> {
        let mut names: Vec<String> = collect_embedded_files(&self.doc)
            .into_iter()
            .map(|(n, _)| n)
            .collect();
        names.sort();
        names
    }

    /// Return attachment contents.
    fn embfile_get(&self, name: &str) -> PyResult<Vec<u8>> {
        let entries = collect_embedded_files(&self.doc);
        let (_, filespec_obj) = entries
            .into_iter()
            .find(|(n, _)| n == name)
            .ok_or_else(|| PdfError::new_err(format!("attachment not found: {name:?}")))?;
        let filespec = match &filespec_obj {
            Object::Reference(id) => self
                .doc
                .get_object(*id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?,
            Object::Dictionary(dict) => dict,
            _ => return Err(PdfError::new_err("attachment's FileSpec is corrupt")),
        };
        let ef = match filespec.get(b"EF").map_err(to_py_err)? {
            Object::Reference(id) => self
                .doc
                .get_object(*id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?,
            Object::Dictionary(d) => d,
            _ => return Err(PdfError::new_err("attachment's EF dictionary is corrupt")),
        };
        let stream_ref = ef
            .get(b"F")
            .or_else(|_| ef.get(b"UF"))
            .and_then(Object::as_reference)
            .map_err(to_py_err)?;
        let stream = self
            .doc
            .get_object(stream_ref)
            .and_then(Object::as_stream)
            .map_err(to_py_err)?;
        Ok(stream
            .decompressed_content()
            .unwrap_or_else(|_| stream.content.clone()))
    }

    /// Add an attachment, rejecting duplicate names.
    fn embfile_add(
        &mut self,
        py: Python<'_>,
        name: String,
        data: Vec<u8>,
        filename: Option<String>,
        desc: Option<String>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let entries = collect_embedded_files(&self.doc);
            if entries.iter().any(|(n, _)| *n == name) {
                return Err(PdfError::new_err(format!(
                    "an attachment with this name already exists: {name:?} (call embfile_del first)"
                )));
            }
            let size = i64::try_from(data.len()).map_err(|e| PdfError::new_err(e.to_string()))?;
            // Keep compression allowed so save(deflate=True) can compress it.
            let ef_id = self.doc.add_object(Stream::new(
                dictionary! {
                    "Type" => "EmbeddedFile",
                    "Params" => dictionary! { "Size" => size },
                },
                data,
            ));
            let fname = filename.unwrap_or_else(|| name.clone());
            let mut filespec = dictionary! {
                "Type" => "Filespec",
                "F" => Object::string_literal(fname.clone()),
                "UF" => text_string(&fname),
                "EF" => dictionary! { "F" => ef_id, "UF" => ef_id },
            };
            if let Some(text) = desc {
                filespec.set("Desc", text_string(&text));
            }
            let filespec_id = self.doc.add_object(filespec);
            let mut entries = entries;
            entries.push((name, Object::Reference(filespec_id)));
            write_embedded_files(&mut self.doc, entries)
        })
    }

    /// Delete an attachment, raising an error when absent.
    fn embfile_del(&mut self, name: &str) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let entries = collect_embedded_files(&self.doc);
        let before = entries.len();
        let remaining: Vec<EmbeddedFileEntry> =
            entries.into_iter().filter(|(n, _)| n != name).collect();
        if remaining.len() == before {
            return Err(PdfError::new_err(format!("attachment not found: {name:?}")));
        }
        write_embedded_files(&mut self.doc, remaining)
    }

    /// Insert display-coordinate OCR words as an invisible text layer.
    ///
    /// Store only Unicode and position using a non-embedded Identity-H CID font,
    /// ToUnicode, and invisible `Tr 3`; it appears only in extraction and search.
    fn insert_ocr_layer(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        words: Vec<(f64, f64, f64, f64, String)>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            let cid_map = ocr::assign_cids(&words);
            if cid_map.len() >= usize::from(u16::MAX) {
                return Err(PdfError::new_err(
                    "too many distinct characters for the OCR layer (max 65,534 per call)",
                ));
            }
            let font_id = ocr::add_ocr_font(&mut self.doc, &cid_map);
            self.bake_page_attrs(page_id)?;
            self.doc
                .get_or_create_resources(page_id)
                .map_err(to_py_err)?;
            let name = format!("PyloF{}", font_id.0);
            draw::add_page_font(&mut self.doc, page_id, &name, font_id).map_err(to_py_err)?;
            let ops = ocr::ocr_ops(crop, rotation, &words, &cid_map, &name);
            draw::push_content(&mut self.doc, page_id, ops, true).map_err(to_py_err)
        })
    }

    /// Draw text from display-coordinate baseline `point` on a one-based page.
    ///
    /// `lines` contains WinAnsi bytes, one per line; Python validates and
    /// converts cp1252. `base_font` is a Standard 14 name and is not embedded.
    // This boundary mirrors the Python signature, so the argument count is intentional.
    #[allow(clippy::too_many_arguments)]
    fn insert_page_text(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        point: (f64, f64),
        lines: Vec<Vec<u8>>,
        base_font: &str,
        winansi: bool,
        fontsize: f64,
        color: (f64, f64, f64),
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            let mut font_dict = dictionary! {
                "Type" => "Font",
                "Subtype" => "Type1",
                "BaseFont" => Object::Name(base_font.as_bytes().to_vec()),
            };
            if winansi {
                font_dict.set("Encoding", Object::Name(b"WinAnsiEncoding".to_vec()));
            }
            let font_id = self.doc.add_object(font_dict);
            self.bake_page_attrs(page_id)?;
            self.doc
                .get_or_create_resources(page_id)
                .map_err(to_py_err)?;
            let name = format!("PyloF{}", font_id.0);
            draw::add_page_font(&mut self.doc, page_id, &name, font_id).map_err(to_py_err)?;
            let ops = draw::text_ops(crop, rotation, point, &lines, &name, fontsize, color);
            draw::push_content(&mut self.doc, page_id, ops, true).map_err(to_py_err)
        })
    }

    /// Replace text on a one-based page and return the replacement count.
    ///
    /// Thinly expose lopdf `replace_partial_text`. It supports simply encoded
    /// fonts only and round-trips content through lopdf's parser. Python's
    /// docstring documents the limitations.
    fn replace_text_on_page(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        search: &str,
        replacement: &str,
        default_char: Option<String>,
    ) -> PyResult<usize> {
        self.invalidate_hayro_pdf();
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            // lopdf get_page_fonts ignores inheritance, so materialize it first.
            self.bake_page_attrs(page_id)?;
            self.doc
                .replace_partial_text(page_number, search, replacement, default_char.as_deref())
                .map_err(|e| lopdf_err(Some("text replacement failed"), &e))
        })
    }
}
