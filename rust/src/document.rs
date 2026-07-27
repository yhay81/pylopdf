//! Python bindings for `lopdf::Document`.
//!
//! This is a thin type- and error-conversion layer. Python's
//! `pylopdf.Document` provides the ergonomic API.

use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::io::{self, Read, Write};
use std::sync::atomic::{AtomicUsize, Ordering};
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
    Bookmark, DecompressError, Dictionary, Document, LoadOptions, Object, ObjectId, PdfMetadata,
    SaveOptions, Stream, StringFormat, decode_text_string, dictionary, text_string,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
#[cfg(not(target_os = "emscripten"))]
use rayon::prelude::*;

use crate::draw;
use crate::form;
use crate::generate;
use crate::image_compression;
use crate::ocr;
use crate::pixmap::Pixmap;
use crate::text_replace::{self, TextReplacementError};

/// Bound interpreted text-page memory on long documents while retaining the
/// common search/extract/annotate working set.
const TEXT_PAGE_CACHE_CAPACITY: usize = 8;

/// Keep table interpretations bounded independently from ordinary text pages.
const TABLE_PAGE_CACHE_CAPACITY: usize = 8;

/// Bound attachment name-tree traversal and returned metadata.
const DEFAULT_MAX_EMBEDDED_FILE_SIZE: usize = 64 * 1024 * 1024;
const MAX_EMBEDDED_FILE_ENTRIES: usize = 4_096;
const MAX_EMBEDDED_FILE_TREE_NODES: usize = 4_096;
const MAX_EMBEDDED_FILE_NAME_BYTES: usize = 1024 * 1024;
const MAX_EMBEDDED_FILE_TREE_DEPTH: usize = 32;
const MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES: usize = 1024 * 1024;
const MAX_EMBEDDED_FILE_DIRECT_OBJECTS: usize = 4_096;
const MAX_EMBEDDED_FILE_DIRECT_BYTES: usize = 1024 * 1024;
const MAX_EMBEDDED_FILE_DIRECT_DEPTH: usize = 32;

/// Bound page-label number-tree traversal and returned metadata.
const MAX_PAGE_LABEL_ENTRIES: usize = 4_096;
const MAX_PAGE_LABEL_TREE_NODES: usize = 4_096;
const MAX_PAGE_LABEL_TEXT_BYTES: usize = 1024 * 1024;
const MAX_PAGE_LABEL_TREE_DEPTH: usize = 32;

/// Bound AcroForm field-tree traversal and returned metadata.
const MAX_FORM_FIELD_ENTRIES: usize = 4_096;
const MAX_FORM_FIELD_TREE_NODES: usize = 4_096;
const MAX_FORM_FIELD_TREE_EDGES: usize = 8_192;
const MAX_FORM_FIELD_TREE_DEPTH: usize = 64;
const MAX_FORM_FIELD_WIDGETS: usize = 4_096;
const MAX_FORM_FIELD_NAME_BYTES: usize = 1024 * 1024;
const MAX_FORM_FIELD_VALUE_BYTES: usize = 1024 * 1024;
const MAX_FORM_FIELD_VALUE_ITEMS: usize = 4_096;
const MAX_FORM_BUTTON_STATE_ENTRIES: usize = 8_192;
const MAX_FORM_BUTTON_STATE_NAMES: usize = 4_096;
const MAX_FORM_BUTTON_STATE_NAME_BYTES: usize = 1024 * 1024;

/// Bound page annotation/link interpretation and generated annotation input.
const MAX_PAGE_ANNOTATIONS: usize = 4_096;
const MAX_ANNOTATION_METADATA_BYTES: usize = 1024 * 1024;
const MAX_HIGHLIGHT_RECTS: usize = 4_096;
const MAX_TEXT_MARKUP_SEGMENTS: usize = 65_536;

/// Bound named-destination name-tree lookup used by link resolution.
const MAX_NAMED_DEST_ENTRIES: usize = 4_096;
const MAX_NAMED_DEST_TREE_NODES: usize = 4_096;
const MAX_NAMED_DEST_TREE_EDGES: usize = 8_192;
const MAX_NAMED_DEST_TREE_DEPTH: usize = 32;
const MAX_NAMED_DEST_NAME_BYTES: usize = 1024 * 1024;

/// Bound outline traversal and returned TOC metadata.
const MAX_TOC_ENTRIES: usize = 4_096;
const MAX_TOC_TREE_NODES: usize = 4_096;
const MAX_TOC_TREE_EDGES: usize = 8_192;
const MAX_TOC_TREE_DEPTH: usize = 64;
const MAX_TOC_DEST_DEPTH: usize = 32;
const MAX_TOC_TEXT_BYTES: usize = 1024 * 1024;

/// Bound generated invisible OCR-layer content per call.
const MAX_OCR_LAYER_WORDS: usize = 4_096;
const MAX_OCR_LAYER_TEXT_BYTES: usize = 1024 * 1024;

/// Bound lopdf's simple-font replacement inputs before encoding work.
const MAX_TEXT_REPLACEMENT_INPUT_BYTES: usize = 4096;

/// Bound one page-structure mutation batch before cloning or importing graphs.
const MAX_STRUCTURAL_PAGE_BATCH: usize = 4_096;

/// Bound the private multi-page plain-text extraction input.
const MAX_TEXT_EXTRACTION_PAGES: usize = 4_096;

/// Bound search input and returned geometry independently from page text.
const MAX_SEARCH_INPUT_BYTES: usize = 4_096;
const DEFAULT_MAX_SEARCH_HITS: usize = 4_096;

/// Bound password KDF input before potentially expensive encryption work.
const MAX_PASSWORD_INPUT_BYTES: usize = 127;

/// Default public boundaries for encoded and decoded image insertion input.
const DEFAULT_MAX_IMAGE_INPUT_SIZE: usize = 64 * 1024 * 1024;
const DEFAULT_MAX_IMAGE_PIXELS: u64 = 64_000_000;
const DEFAULT_MAX_FONT_INPUT_SIZE: usize = 64 * 1024 * 1024;
const DEFAULT_MAX_GENERATED_TEXT_SIZE: usize = 1024 * 1024;

/// Bound standard document Info metadata reads and writes.
const INFO_METADATA_KEYS: [&[u8]; 8] = [
    b"Title",
    b"Author",
    b"Subject",
    b"Keywords",
    b"Creator",
    b"Producer",
    b"CreationDate",
    b"ModDate",
];
const MAX_INFO_METADATA_TEXT_BYTES: usize = 1024 * 1024;

const XREF_REPAIR_WARNING: &str = "recovered a PDF with an incorrect startxref offset; saving will rewrite its cross-reference data";

const TEXT_FIELD_MULTILINE: i64 = 1 << 12;
const TEXT_FIELD_PASSWORD: i64 = 1 << 13;
const TEXT_FIELD_FILE_SELECT: i64 = 1 << 20;
const TEXT_FIELD_COMB: i64 = 1 << 24;

#[derive(Clone, Copy)]
enum WidgetTextLayout {
    SingleLine,
    Multiline,
    Comb(usize),
}

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

/// Info strings, page count, version, encryption, and startxref-repair status.
type MetadataTuple = (BTreeMap<String, String>, u32, String, bool, bool);

/// Target geometry and drawing order for one placed PDF page.
#[derive(Clone, Copy)]
struct PagePlacement {
    rect: [f64; 4],
    keep_proportion: bool,
    overlay: bool,
}

/// One EmbeddedFiles name-tree item: display name and FileSpec object.
///
/// FileSpec may be either an indirect reference or an inline dictionary.
type EmbeddedFileEntry = (String, Object);

/// Preflighted location of the Catalog's `/Names` dictionary.
#[derive(Clone, Copy)]
enum EmbeddedFilesWriteTarget {
    Missing,
    Inline,
    Indirect(ObjectId),
}

/// One PageLabels number-tree item: start, style, prefix, and first number.
type PageLabelEntry = (i64, Option<String>, Option<String>, i64);

/// One flattened TOC item: one-based level, title, and page number.
type TocEntry = (u32, String, u32);

/// One flattened AcroForm field: name, object, type, flags, and normalized value.
type FormFieldEntry = (String, ObjectId, String, i64, Option<Arc<str>>);

/// One widget and the raw state names from its normal appearance dictionary.
type WidgetStateNames = (ObjectId, Vec<Vec<u8>>);

/// Resolve one reference level, returning the original object on failure.
fn deref_object<'a>(doc: &'a Document, obj: &'a Object) -> &'a Object {
    match obj {
        Object::Reference(id) => doc.get_object(*id).unwrap_or(obj),
        other => other,
    }
}

/// Visit a named-destination tree with complete node-local safety budgets.
fn visit_named_destinations<'a>(
    doc: &'a Document,
    root: &'a Object,
    mut visitor: impl FnMut(&'a [u8], &'a Object) -> PyResult<()>,
) -> PyResult<()> {
    let mut stack = vec![(root, 1usize)];
    let mut visited = HashSet::new();
    let mut nodes = 0usize;
    let mut edges = 0usize;
    let mut entries = 0usize;
    let mut name_bytes = 0usize;
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object
            && !visited.insert(*id)
        {
            continue;
        }
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes = nodes.saturating_add(1);
        if nodes > MAX_NAMED_DEST_TREE_NODES {
            return Err(PdfError::new_err(format!(
                "named-destination tree exceeds the {MAX_NAMED_DEST_TREE_NODES}-node safety limit"
            )));
        }
        if let Ok(names_object) = node.get(b"Names")
            && let Ok(names) = deref_object(doc, names_object).as_array()
        {
            entries = entries
                .checked_add(names.len().div_ceil(2))
                .ok_or_else(|| {
                    PdfError::new_err("named-destination tree exceeds the platform size limit")
                })?;
            if entries > MAX_NAMED_DEST_ENTRIES {
                return Err(PdfError::new_err(format!(
                    "named-destination tree exceeds the {MAX_NAMED_DEST_ENTRIES}-entry safety limit"
                )));
            }
            for pair in names.chunks(2) {
                let [key, value] = pair else { continue };
                let Ok(key) = deref_object(doc, key).as_str() else {
                    continue;
                };
                name_bytes = name_bytes.saturating_add(key.len());
                if name_bytes > MAX_NAMED_DEST_NAME_BYTES {
                    return Err(PdfError::new_err(format!(
                        "named-destination keys exceed the {MAX_NAMED_DEST_NAME_BYTES}-byte safety limit"
                    )));
                }
                visitor(key, deref_object(doc, value))?;
            }
        }
        if let Ok(kids_object) = node.get(b"Kids")
            && let Ok(kids) = deref_object(doc, kids_object).as_array()
        {
            if !kids.is_empty() && depth >= MAX_NAMED_DEST_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "named-destination tree exceeds the {MAX_NAMED_DEST_TREE_DEPTH}-level safety limit"
                )));
            }
            edges = edges.checked_add(kids.len()).ok_or_else(|| {
                PdfError::new_err("named-destination tree exceeds the platform size limit")
            })?;
            if edges > MAX_NAMED_DEST_TREE_EDGES {
                return Err(PdfError::new_err(format!(
                    "named-destination tree exceeds the {MAX_NAMED_DEST_TREE_EDGES}-edge safety limit"
                )));
            }
            for kid in kids.iter().rev() {
                stack.push((kid, depth + 1));
            }
        }
    }
    Ok(())
}

/// Build one borrowed index so TOC resolution scans a name tree at most once.
fn named_destination_index(doc: &Document) -> PyResult<HashMap<&[u8], &Object>> {
    let mut index = HashMap::new();
    let Some(root) = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Names").ok())
        .map(|names| deref_object(doc, names))
        .and_then(|names| names.as_dict().ok())
        .and_then(|names| names.get(b"Dests").ok())
    else {
        return Ok(index);
    };
    visit_named_destinations(doc, root, |key, value| {
        index.entry(key).or_insert(value);
        Ok(())
    })?;
    Ok(index)
}

/// Resolve one legacy catalog `/Dests` dictionary entry without scanning it.
fn lookup_legacy_named_dest<'a>(doc: &'a Document, name: &[u8]) -> Option<&'a Object> {
    doc.catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Dests").ok())
        .map(|dests| deref_object(doc, dests))
        .and_then(|dests| dests.as_dict().ok())
        .and_then(|dests| dests.get(name).ok())
        .map(|value| deref_object(doc, value))
}

/// Charge encoded or returned TOC text without overflow or partial output.
fn add_toc_text_budget(total: &mut usize, amount: usize, label: &str) -> PyResult<()> {
    *total = total
        .checked_add(amount)
        .ok_or_else(|| PdfError::new_err("TOC text exceeds the platform size limit"))?;
    if *total > MAX_TOC_TEXT_BYTES {
        return Err(PdfError::new_err(format!(
            "TOC {label} exceeds the {MAX_TOC_TEXT_BYTES}-byte safety limit"
        )));
    }
    Ok(())
}

/// Decode one outline title while bounding both source and returned text.
fn bounded_toc_title(
    doc: &Document,
    object: &Object,
    source_bytes: &mut usize,
    returned_bytes: &mut usize,
) -> PyResult<Option<String>> {
    let object = deref_object(doc, object);
    let Object::String(encoded, _) = object else {
        return Ok(None);
    };
    add_toc_text_budget(source_bytes, encoded.len(), "source text")?;
    let Ok(title) = decode_text_string(object) else {
        return Ok(None);
    };
    add_toc_text_budget(returned_bytes, title.len(), "returned text")?;
    Ok(Some(title))
}

/// Find a destination object on an outline node, mirroring lopdf precedence.
fn outline_destination<'a>(doc: &'a Document, node: &'a Dictionary) -> Option<&'a Object> {
    match node
        .get(b"A")
        .ok()
        .map(|action| deref_object(doc, action))
        .and_then(|action| action.as_dict().ok())
    {
        Some(action) => match action.get(b"S").and_then(Object::as_name) {
            Ok(b"GoTo" | b"GoToR") => action.get(b"D").ok(),
            _ => None,
        },
        None => node.get(b"Dest").ok(),
    }
}

/// Resolve one outline destination to a one-based page under bounded indirection.
fn resolve_toc_page<'a>(
    doc: &'a Document,
    destination: &'a Object,
    page_map: &BTreeMap<ObjectId, u32>,
    named_destinations: &mut Option<HashMap<&'a [u8], &'a Object>>,
    source_bytes: &mut usize,
) -> PyResult<Option<u32>> {
    let mut current = destination;
    let mut visited = HashSet::new();
    for _ in 0..MAX_TOC_DEST_DEPTH {
        match current {
            Object::Reference(id) => {
                if !visited.insert(*id) {
                    return Ok(None);
                }
                let Ok(object) = doc.get_object(*id) else {
                    return Ok(None);
                };
                current = object;
            }
            Object::Name(name) | Object::String(name, _) => {
                add_toc_text_budget(source_bytes, name.len(), "source text")?;
                if named_destinations.is_none() {
                    *named_destinations = Some(named_destination_index(doc)?);
                }
                let found = named_destinations
                    .as_ref()
                    .and_then(|index| index.get(name.as_slice()).copied())
                    .or_else(|| lookup_legacy_named_dest(doc, name));
                let Some(found) = found else {
                    return Ok(None);
                };
                current = found;
            }
            Object::Dictionary(dictionary) => {
                let Ok(inner) = dictionary.get(b"D") else {
                    return Ok(None);
                };
                current = inner;
            }
            Object::Array(array) => {
                return Ok(match array.first() {
                    Some(Object::Reference(id)) => page_map.get(id).copied(),
                    _ => None,
                });
            }
            _ => return Ok(None),
        }
    }
    Err(PdfError::new_err(format!(
        "TOC destination exceeds the {MAX_TOC_DEST_DEPTH}-level safety limit"
    )))
}

/// Flatten an outline tree with explicit cycle, size, depth, and text budgets.
fn collect_toc(doc: &Document) -> PyResult<Vec<TocEntry>> {
    let Some(outlines_object) = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Outlines").ok())
    else {
        return Ok(Vec::new());
    };
    let Ok(outlines) = deref_object(doc, outlines_object).as_dict() else {
        return Err(PdfError::new_err("catalog Outlines is not a dictionary"));
    };
    let (start, mut edges) = match outlines.get(b"First") {
        Ok(first) => (first, 1usize),
        Err(_) => (outlines_object, 0usize),
    };
    let page_map: BTreeMap<ObjectId, u32> = doc
        .get_pages()
        .into_iter()
        .map(|(number, id)| (id, number))
        .collect();
    let mut named_destinations = None;
    let mut stack = vec![(start, 1usize)];
    let mut visited = HashSet::new();
    let mut nodes = 0usize;
    let mut source_bytes = 0usize;
    let mut returned_bytes = 0usize;
    let mut out = Vec::new();
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object
            && !visited.insert(*id)
        {
            continue;
        }
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes = nodes
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("TOC outline tree exceeds the platform size limit"))?;
        if nodes > MAX_TOC_TREE_NODES {
            return Err(PdfError::new_err(format!(
                "TOC outline tree exceeds the {MAX_TOC_TREE_NODES}-node safety limit"
            )));
        }

        if let (Ok(title_object), Some(destination)) =
            (node.get(b"Title"), outline_destination(doc, node))
            && let Some(title) =
                bounded_toc_title(doc, title_object, &mut source_bytes, &mut returned_bytes)?
            && let Some(page) = resolve_toc_page(
                doc,
                destination,
                &page_map,
                &mut named_destinations,
                &mut source_bytes,
            )?
        {
            out.push((
                u32::try_from(depth).map_err(|error| PdfError::new_err(error.to_string()))?,
                title,
                page,
            ));
            if out.len() > MAX_TOC_ENTRIES {
                return Err(PdfError::new_err(format!(
                    "TOC exceeds the {MAX_TOC_ENTRIES}-entry safety limit"
                )));
            }
        }

        if let Ok(next) = node.get(b"Next") {
            edges = edges.checked_add(1).ok_or_else(|| {
                PdfError::new_err("TOC outline tree exceeds the platform size limit")
            })?;
            if edges > MAX_TOC_TREE_EDGES {
                return Err(PdfError::new_err(format!(
                    "TOC outline tree exceeds the {MAX_TOC_TREE_EDGES}-edge safety limit"
                )));
            }
            stack.push((next, depth));
        }
        if let Ok(first) = node.get(b"First") {
            if depth >= MAX_TOC_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "TOC outline tree exceeds the {MAX_TOC_TREE_DEPTH}-level safety limit"
                )));
            }
            edges = edges.checked_add(1).ok_or_else(|| {
                PdfError::new_err("TOC outline tree exceeds the platform size limit")
            })?;
            if edges > MAX_TOC_TREE_EDGES {
                return Err(PdfError::new_err(format!(
                    "TOC outline tree exceeds the {MAX_TOC_TREE_EDGES}-edge safety limit"
                )));
            }
            stack.push((first, depth + 1));
        }
    }
    Ok(out)
}

/// One field-tree traversal node: object, prefix, inherited FT/Ff/value, depth.
type FieldNode = (
    ObjectId,
    String,
    Option<String>,
    i64,
    Option<Arc<str>>,
    usize,
);

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
pyo3::create_exception!(
    pylopdf,
    LimitError,
    PdfError,
    "A configured resource limit was exceeded."
);

/// Resource limits accepted by the private Rust loading boundary.
#[derive(Clone, Copy, Default)]
struct DocumentLimits {
    max_file_size: Option<usize>,
    max_pages: Option<usize>,
    max_objects: Option<usize>,
    max_decompressed_size: Option<usize>,
    max_page_content_size: Option<usize>,
    max_total_decompressed_size: Option<usize>,
    max_object_depth: Option<usize>,
    max_text_size: Option<usize>,
    max_interpretation_size: Option<usize>,
    max_text_glyphs: Option<usize>,
}

/// Cheap structural facts that require neither stream decoding nor rendering.
type ComplexityTuple = (usize, usize, usize, u64, usize);

/// Original unencrypted input retained for the first render or extraction.
enum HayroSource {
    Unavailable,
    Bytes(Vec<u8>),
    TooLarge { actual: usize, limit: usize },
}

impl HayroSource {
    fn from_owned(data: Vec<u8>, max_size: Option<usize>) -> Self {
        match max_size {
            Some(limit) if data.len() > limit => Self::TooLarge {
                actual: data.len(),
                limit,
            },
            _ => Self::Bytes(data),
        }
    }

    fn from_optional_owned(data: Option<Vec<u8>>, max_size: Option<usize>) -> Self {
        data.map_or(Self::Unavailable, |data| Self::from_owned(data, max_size))
    }

    fn from_borrowed(data: &[u8], max_size: Option<usize>) -> PyResult<Self> {
        if let Some(limit) = max_size
            && data.len() > limit
        {
            return Ok(Self::TooLarge {
                actual: data.len(),
                limit,
            });
        }
        let mut owned = Vec::new();
        owned.try_reserve_exact(data.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate rendering and extraction source: {error}"
            ))
        })?;
        owned.extend_from_slice(data);
        Ok(Self::Bytes(owned))
    }

    fn take_bytes(&mut self) -> Option<Vec<u8>> {
        match std::mem::replace(self, Self::Unavailable) {
            Self::Bytes(data) => Some(data),
            Self::Unavailable | Self::TooLarge { .. } => None,
        }
    }
}

/// Page dictionary keys that may be inherited from parent nodes.
const INHERITABLE_PAGE_KEYS: [&[u8]; 4] = [b"Resources", b"MediaBox", b"CropBox", b"Rotate"];

/// Maximum PNG render pixels, approximately a 256 MB RGBA bitmap.
const MAX_RENDER_PIXELS: u64 = 64_000_000;
const MAX_RENDER_BATCH_PAGES: usize = 4_096;

/// Bound concurrent pixmap plus straight-alpha conversion buffers to ~512 MB.
#[cfg(not(target_os = "emscripten"))]
const MAX_PARALLEL_RENDER_BYTES: u64 = 512_000_000;
#[cfg(not(target_os = "emscripten"))]
const ESTIMATED_RENDER_BYTES_PER_PIXEL: u64 = 8;

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
    } else if matches!(
        e,
        lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
    ) {
        limit_err("decompressed_size", message)
    } else {
        PdfError::new_err(message)
    }
}

/// Convert a lopdf error to a Python exception.
fn to_py_err(e: lopdf::Error) -> PyErr {
    lopdf_err(None, &e)
}

/// Convert a load error while retaining which configured bound protected eager
/// object/xref stream decoding.
fn load_err(prefix: Option<&str>, error: &lopdf::Error, limit_code: &'static str) -> PyErr {
    if matches!(
        error,
        lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
    ) {
        let message = match prefix {
            Some(prefix) => format!("{prefix}: {error}"),
            None => error.to_string(),
        };
        limit_err(limit_code, message)
    } else {
        lopdf_err(prefix, error)
    }
}

fn is_pdf_whitespace(byte: u8) -> bool {
    matches!(byte, 0 | b'\t' | b'\n' | 0x0c | b'\r' | b' ')
}

fn line_end(data: &[u8], start: usize) -> usize {
    data[start..]
        .iter()
        .position(|byte| matches!(byte, b'\r' | b'\n'))
        .map_or(data.len(), |offset| start + offset)
}

fn decimal_field(field: &[u8]) -> Option<usize> {
    (!field.is_empty() && field.iter().all(u8::is_ascii_digit))
        .then(|| {
            field.iter().try_fold(0usize, |value, digit| {
                value
                    .checked_mul(10)?
                    .checked_add(usize::from(*digit - b'0'))
            })
        })
        .flatten()
}

/// Recognize only a classic xref table header plus its first entry.
///
/// This deliberately does not scan object headers or repair xref streams. The
/// retrying lopdf parse remains the authority for the complete table/trailer.
fn looks_like_classic_xref(data: &[u8], offset: usize) -> bool {
    if data.get(offset..offset.saturating_add(4)) != Some(b"xref")
        || (offset > 0 && !matches!(data[offset - 1], b'\r' | b'\n'))
    {
        return false;
    }
    let mut cursor = offset + 4;
    while matches!(data.get(cursor), Some(b' ' | b'\t')) {
        cursor += 1;
    }
    if !matches!(data.get(cursor), Some(b'\r' | b'\n')) {
        return false;
    }
    while matches!(data.get(cursor), Some(byte) if is_pdf_whitespace(*byte)) {
        cursor += 1;
    }
    let header_end = line_end(data, cursor);
    let mut header = data[cursor..header_end]
        .split(|byte| matches!(byte, b' ' | b'\t'))
        .filter(|field| !field.is_empty());
    let Some(start_object) = header.next().and_then(decimal_field) else {
        return false;
    };
    let Some(entry_count) = header.next().and_then(decimal_field) else {
        return false;
    };
    if header.next().is_some() || start_object > u32::MAX as usize || entry_count == 0 {
        return false;
    }
    cursor = header_end;
    while matches!(data.get(cursor), Some(byte) if is_pdf_whitespace(*byte)) {
        cursor += 1;
    }
    let entry_end = line_end(data, cursor);
    let mut entry = data[cursor..entry_end]
        .split(|byte| matches!(byte, b' ' | b'\t'))
        .filter(|field| !field.is_empty());
    let valid_offset = entry
        .next()
        .is_some_and(|field| field.len() == 10 && field.iter().all(u8::is_ascii_digit));
    let valid_generation = entry
        .next()
        .is_some_and(|field| field.len() == 5 && field.iter().all(u8::is_ascii_digit));
    let valid_kind = entry
        .next()
        .is_some_and(|field| matches!(field, b"n" | b"f"));
    valid_offset && valid_generation && valid_kind && entry.next().is_none()
}

fn last_subslice(data: &[u8], needle: &[u8], end: usize) -> Option<usize> {
    data.get(..end)?
        .windows(needle.len())
        .rposition(|window| window == needle)
}

/// Patch one incorrect final `startxref` value when a classic table is intact.
///
/// The scan is linear, considers only the last structurally plausible classic
/// table before the final startxref, and never guesses object offsets. Callers
/// accept the patch only when a complete bounded lopdf retry succeeds.
fn repair_classic_startxref(data: &[u8]) -> lopdf::Result<Option<Vec<u8>>> {
    let Some(pdf_start) = data.windows(5).position(|window| window == b"%PDF-") else {
        return Ok(None);
    };
    let Some(eof) = last_subslice(data, b"%%EOF", data.len()) else {
        return Ok(None);
    };
    let Some(startxref) = last_subslice(data, b"startxref", eof) else {
        return Ok(None);
    };
    if startxref <= pdf_start {
        return Ok(None);
    }
    let mut number_start = startxref + b"startxref".len();
    while matches!(data.get(number_start), Some(byte) if is_pdf_whitespace(*byte)) {
        number_start += 1;
    }
    let mut number_end = number_start;
    while matches!(data.get(number_end), Some(byte) if byte.is_ascii_digit()) {
        number_end += 1;
    }
    let Some(number) = data.get(number_start..number_end) else {
        return Ok(None);
    };
    let Some(current) = decimal_field(number) else {
        return Ok(None);
    };

    let mut xref = None;
    for (relative, window) in data[pdf_start..startxref].windows(4).enumerate() {
        if window == b"xref" {
            let candidate = pdf_start + relative;
            let final_revision = !data[candidate..startxref]
                .windows(b"%%EOF".len())
                .any(|window| window == b"%%EOF")
                && !data[candidate..startxref]
                    .windows(b"endobj".len())
                    .any(|window| window == b"endobj")
                && data[candidate..startxref]
                    .windows(b"trailer".len())
                    .any(|window| window == b"trailer");
            if final_revision && looks_like_classic_xref(data, candidate) {
                xref = Some(candidate);
            }
        }
    }
    let Some(xref) = xref else {
        return Ok(None);
    };
    let Some(relative_xref) = xref.checked_sub(pdf_start) else {
        return Ok(None);
    };
    if current == relative_xref {
        return Ok(None);
    }
    let replacement = relative_xref.to_string();
    let repaired_len = data
        .len()
        .checked_sub(number_end - number_start)
        .and_then(|length| length.checked_add(replacement.len()))
        .ok_or_else(|| {
            lopdf::Error::IO(std::io::Error::other("repaired PDF input size overflowed"))
        })?;
    let mut repaired = Vec::new();
    repaired.try_reserve_exact(repaired_len).map_err(|error| {
        lopdf::Error::IO(std::io::Error::other(format!(
            "failed to allocate repaired PDF input: {error}"
        )))
    })?;
    repaired.extend_from_slice(&data[..number_start]);
    repaired.extend_from_slice(replacement.as_bytes());
    repaired.extend_from_slice(&data[number_end..]);
    Ok(Some(repaired))
}

fn xref_recovery_candidate(error: &lopdf::Error) -> bool {
    matches!(
        error,
        lopdf::Error::Parse(_) | lopdf::Error::Xref(_) | lopdf::Error::InvalidOffset(_)
    )
}

fn load_document_with_recovery(
    data: &[u8],
    options: LoadOptions,
) -> lopdf::Result<(Document, Option<Vec<u8>>)> {
    let original_error = match Document::load_mem_with_options(data, options.clone()) {
        Ok(document) => return Ok((document, None)),
        Err(error) => error,
    };
    if options.strict || !xref_recovery_candidate(&original_error) {
        return Err(original_error);
    }
    let Some(repaired) = repair_classic_startxref(data)? else {
        return Err(original_error);
    };
    match Document::load_mem_with_options(&repaired, options) {
        Ok(document) => Ok((document, Some(repaired))),
        Err(_) => Err(original_error),
    }
}

fn load_metadata_with_recovery(
    data: &[u8],
    password: Option<&str>,
) -> lopdf::Result<(PdfMetadata, bool)> {
    let load = |input: &[u8]| match password {
        Some(password) => Document::load_metadata_mem_with_password(input, password),
        None => Document::load_metadata_mem(input),
    };
    let original_error = match load(data) {
        Ok(metadata) => return Ok((metadata, false)),
        Err(error) => error,
    };
    if !xref_recovery_candidate(&original_error) {
        return Err(original_error);
    }
    let Some(repaired) = repair_classic_startxref(data)? else {
        return Err(original_error);
    };
    match load(&repaired) {
        Ok(metadata) => Ok((metadata, true)),
        Err(_) => Err(original_error),
    }
}

/// Construct a machine-readable resource-limit exception.
///
/// The first exception argument is a stable code; Python supplies a friendly
/// `str(error)` and `error.code` view over the two arguments.
fn limit_err(code: &'static str, message: impl Into<String>) -> PyErr {
    LimitError::new_err((code, message.into()))
}

/// Charge one caller-text budget with a stable machine-readable limit code.
fn add_input_text_budget(
    total: &mut usize,
    amount: usize,
    limit: usize,
    code: &'static str,
    label: &str,
) -> PyResult<()> {
    *total = total.checked_add(amount).ok_or_else(|| {
        limit_err(
            code,
            format!("{label} exceeds the platform text-size limit"),
        )
    })?;
    if *total > limit {
        return Err(limit_err(
            code,
            format!("{label} exceeds the {limit}-byte safety limit"),
        ));
    }
    Ok(())
}

/// Compute lopdf's ASCII-or-UTF-16BE text-string size without allocating it.
fn pdf_text_string_len(value: &str) -> Option<usize> {
    if value.is_ascii() {
        return Some(value.len());
    }
    value
        .encode_utf16()
        .try_fold(2usize, |size, _| size.checked_add(2))
}

/// Map one text collector refusal to its stable public resource code.
fn text_page_limit_err(error: crate::extract::TextPageLimit) -> PyErr {
    match error {
        crate::extract::TextPageLimit::TextSize(limit) => limit_err(
            "text_size",
            format!(
                "page text exceeds the remaining configured Unicode payload budget of {limit} bytes"
            ),
        ),
        crate::extract::TextPageLimit::GlyphCount(limit) => limit_err(
            "text_glyph_count",
            format!(
                "page text exceeds the remaining configured positioned-glyph budget of {limit}"
            ),
        ),
        crate::extract::TextPageLimit::Allocation(message) => PdfError::new_err(message),
    }
}

/// Read a regular file with fallible retained-buffer growth.
pub(crate) fn read_file_fallibly(
    mut file: std::fs::File,
    initial_capacity: usize,
    max_read: Option<usize>,
    allocation_label: &str,
) -> std::io::Result<Vec<u8>> {
    const READ_CHUNK_BYTES: usize = 64 * 1024;

    let mut data = Vec::new();
    data.try_reserve_exact(initial_capacity).map_err(|error| {
        std::io::Error::other(format!("failed to allocate {allocation_label}: {error}"))
    })?;
    let mut buffer = [0u8; READ_CHUNK_BYTES];
    loop {
        let read_size = max_read
            .map(|limit| limit.saturating_sub(data.len()).min(buffer.len()))
            .unwrap_or(buffer.len());
        if read_size == 0 {
            break;
        }
        let amount = match file.read(&mut buffer[..read_size]) {
            Ok(0) => break,
            Ok(amount) => amount,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        };
        data.try_reserve_exact(amount).map_err(|error| {
            std::io::Error::other(format!("failed to allocate {allocation_label}: {error}"))
        })?;
        data.extend_from_slice(&buffer[..amount]);
    }
    Ok(data)
}

/// Read a path without admitting more than one byte beyond a caller's budget.
fn read_bounded_input(
    path: &str,
    max_size: Option<usize>,
    limit_code: &'static str,
    size_label: &str,
    error_prefix: &str,
) -> PyResult<Vec<u8>> {
    let io_error = |error| PdfError::new_err(format!("{error_prefix} {path}: {error}"));
    let file = std::fs::File::open(path).map_err(&io_error)?;
    let metadata_size = file.metadata().map_err(&io_error)?.len();
    if let Some(limit) = max_size
        && metadata_size > limit as u64
    {
        return Err(limit_err(
            limit_code,
            format!("{size_label} is {metadata_size} bytes, exceeding the {limit}-byte limit"),
        ));
    }
    let read_limit = max_size.map(|limit| limit.saturating_add(1));
    let initial_capacity = usize::try_from(metadata_size)
        .unwrap_or(0)
        .min(read_limit.unwrap_or(usize::MAX));
    let data =
        read_file_fallibly(file, initial_capacity, read_limit, size_label).map_err(io_error)?;
    if let Some(limit) = max_size
        && data.len() > limit
    {
        return Err(limit_err(
            limit_code,
            format!("{size_label} exceeds the {limit}-byte limit while being read"),
        ));
    }
    Ok(data)
}

/// Read PDF input without admitting more than one byte beyond its file budget.
fn read_input(path: &str, max_file_size: Option<usize>) -> PyResult<Vec<u8>> {
    if max_file_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_file_size must be a positive integer or None",
        ));
    }
    read_bounded_input(
        path,
        max_file_size,
        "file_size",
        "PDF file",
        "failed to load",
    )
}

/// Reject an in-memory PDF before parsing when it exceeds the file budget.
fn validate_input_size(data: &[u8], max_file_size: Option<usize>) -> PyResult<()> {
    if max_file_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_file_size must be a positive integer or None",
        ));
    }
    if let Some(limit) = max_file_size
        && data.len() > limit
    {
        return Err(limit_err(
            "file_size",
            format!(
                "PDF input is {} bytes, exceeding the configured limit of {limit}",
                data.len()
            ),
        ));
    }
    Ok(())
}

/// Validate the renderer/extractor snapshot budget at the private boundary.
fn validate_interpretation_limit(max_interpretation_size: Option<usize>) -> PyResult<()> {
    if max_interpretation_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_interpretation_size must be a positive integer or None",
        ));
    }
    Ok(())
}

/// Validate the positioned-text glyph budget at the private boundary.
fn validate_text_glyph_limit(max_text_glyphs: Option<usize>) -> PyResult<()> {
    if max_text_glyphs == Some(0) {
        return Err(PyValueError::new_err(
            "max_text_glyphs must be a positive integer or None",
        ));
    }
    Ok(())
}

/// Read encoded image input without admitting more than one byte beyond its budget.
fn read_image_input(path: &str, max_size: Option<usize>) -> PyResult<Vec<u8>> {
    read_bounded_input(
        path,
        max_size,
        "image_input_size",
        "encoded image input",
        "failed to load image",
    )
}

/// Read OpenType font input without admitting more than one byte beyond its budget.
fn read_font_input(path: &str, max_font_size: Option<usize>) -> PyResult<Vec<u8>> {
    read_bounded_input(
        path,
        max_font_size,
        "font_input_size",
        "font input",
        "failed to load font",
    )
}

fn validate_font_input(data: Option<&[u8]>, max_font_size: Option<usize>) -> PyResult<()> {
    if max_font_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_font_size must be a positive integer or None",
        ));
    }
    if let (Some(data), Some(limit)) = (data, max_font_size)
        && data.len() > limit
    {
        return Err(limit_err(
            "font_input_size",
            format!(
                "font input is {} bytes, exceeding the {limit}-byte limit",
                data.len()
            ),
        ));
    }
    Ok(())
}

fn validate_generated_text_input<'a>(
    chunks: impl IntoIterator<Item = &'a [u8]>,
    max_text_size: Option<usize>,
) -> PyResult<()> {
    let Some(limit) = max_text_size else {
        return Ok(());
    };
    if limit == 0 {
        return Err(PyValueError::new_err(
            "max_text_size must be a positive integer or None",
        ));
    }
    let mut total = 0usize;
    for chunk in chunks {
        total = total.checked_add(chunk.len()).ok_or_else(|| {
            limit_err(
                "text_input_size",
                format!("text input exceeds the {limit}-byte UTF-8 limit"),
            )
        })?;
        if total > limit {
            return Err(limit_err(
                "text_input_size",
                format!("text input exceeds the {limit}-byte UTF-8 limit"),
            ));
        }
    }
    Ok(())
}

fn validate_generated_text_line_count(
    line_count: usize,
    max_text_size: Option<usize>,
) -> PyResult<()> {
    if max_text_size.is_some() && line_count > crate::layout::MAX_GENERATED_TEXT_LINES {
        return Err(limit_err(
            "text_line_count",
            format!(
                "text input exceeds the {}-line safety limit",
                crate::layout::MAX_GENERATED_TEXT_LINES
            ),
        ));
    }
    Ok(())
}

fn generated_text_err(error: String) -> PyErr {
    if error == crate::layout::TEXT_LINE_LIMIT_ERROR {
        limit_err(
            "text_line_count",
            format!(
                "text layout exceeds the {}-line safety limit",
                crate::layout::MAX_GENERATED_TEXT_LINES
            ),
        )
    } else {
        PdfError::new_err(error)
    }
}

fn validate_form_field_input(name: &str, value: Option<&str>) -> PyResult<()> {
    for (label, text, limit) in [
        ("form field name", Some(name), MAX_FORM_FIELD_NAME_BYTES),
        ("form field value", value, MAX_FORM_FIELD_VALUE_BYTES),
    ] {
        if text.is_some_and(|text| text.len() > limit) {
            return Err(limit_err(
                "form_field_input_size",
                format!("{label} exceeds the {limit}-byte UTF-8 safety limit"),
            ));
        }
    }
    Ok(())
}

fn validate_embedded_file_lookup_name(name: &str) -> PyResult<()> {
    if name.len() > MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES {
        return Err(limit_err(
            "embedded_file_input_size",
            format!(
                "attachment lookup name exceeds the {MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES}-byte UTF-8 safety limit"
            ),
        ));
    }
    Ok(())
}

fn validate_embedded_file_input_text(
    name: &str,
    filename: Option<&str>,
    desc: Option<&str>,
) -> PyResult<()> {
    let fname = filename.unwrap_or(name);
    let input_text_bytes = name
        .len()
        .checked_add(fname.len())
        .and_then(|total| desc.map_or(Some(total), |text| total.checked_add(text.len())))
        .ok_or_else(|| {
            limit_err(
                "embedded_file_input_size",
                "attachment input text exceeds the platform size limit",
            )
        })?;
    if input_text_bytes > MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES {
        return Err(limit_err(
            "embedded_file_input_size",
            format!(
                "attachment name, filename, and description exceed the \
                 {MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES}-byte UTF-8 safety limit"
            ),
        ));
    }
    Ok(())
}

fn validate_password_input(password: Option<&str>, label: &str) -> PyResult<()> {
    if let Some(password) = password
        && password.len() > MAX_PASSWORD_INPUT_BYTES
    {
        return Err(limit_err(
            "password_input_size",
            format!("{label} exceeds the {MAX_PASSWORD_INPUT_BYTES}-byte UTF-8 safety limit"),
        ));
    }
    Ok(())
}

fn validate_image_input(
    data: &[u8],
    max_size: Option<usize>,
    max_pixels: Option<u64>,
) -> PyResult<()> {
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    if max_pixels == Some(0) {
        return Err(PyValueError::new_err(
            "max_pixels must be a positive integer or None",
        ));
    }
    if let Some(limit) = max_size
        && data.len() > limit
    {
        return Err(limit_err(
            "image_input_size",
            format!(
                "encoded image input is {} bytes, exceeding the {limit}-byte limit",
                data.len()
            ),
        ));
    }
    if let (Some(limit), Some(pixels)) = (max_pixels, draw::png_pixel_count(data))
        && pixels > limit
    {
        return Err(limit_err(
            "image_pixel_count",
            format!("PNG image contains {pixels} pixels, exceeding the {limit}-pixel limit"),
        ));
    }
    Ok(())
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

/// Charge aggregate Info metadata text without overflow or partial output.
fn add_info_metadata_budget(total: &mut usize, amount: usize, label: &str) -> PyResult<()> {
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

/// Return the trailer Info dictionary with one indirect reference resolved.
fn info_dictionary(doc: &Document) -> Option<&Dictionary> {
    match doc.trailer.get(b"Info").ok()? {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok(),
        Object::Dictionary(dictionary) => Some(dictionary),
        _ => None,
    }
}

/// Decode only the eight public standard Info fields under aggregate budgets.
fn collect_info_metadata(doc: &Document) -> PyResult<BTreeMap<String, String>> {
    let mut result = BTreeMap::new();
    let Some(info) = info_dictionary(doc) else {
        return Ok(result);
    };
    let mut source_bytes = 0usize;
    let mut returned_bytes = 0usize;
    for key in INFO_METADATA_KEYS {
        let Ok(value) = info.get(key) else {
            continue;
        };
        let resolved = deref_object(doc, value);
        let Object::String(encoded, _) = resolved else {
            continue;
        };
        add_info_metadata_budget(&mut source_bytes, encoded.len(), "source text")?;
        let Ok(text) = decode_text_string(resolved) else {
            continue;
        };
        add_info_metadata_budget(&mut returned_bytes, text.len(), "returned text")?;
        result.insert(String::from_utf8_lossy(key).into_owned(), text);
    }
    Ok(result)
}

/// Convert PdfMetadata to a bounded Info dict and structural facts.
fn pdf_metadata_to_tuple(
    meta: PdfMetadata,
) -> PyResult<(BTreeMap<String, String>, u32, String, bool)> {
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
    // before materializing Python-facing strings.
    drop(custom);
    let mut map = BTreeMap::new();
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
            map.insert(key.to_string(), v);
        }
    }
    Ok((map, page_count, version, encrypted))
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

/// A serialization sink that refuses the write crossing one byte budget.
struct BoundedPdfOutput {
    bytes: Vec<u8>,
    max_size: Option<usize>,
    exceeded: bool,
}

impl BoundedPdfOutput {
    fn new(max_size: Option<usize>) -> Self {
        Self {
            bytes: Vec::new(),
            max_size,
            exceeded: false,
        }
    }
}

impl Write for BoundedPdfOutput {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        self.write_all(buffer)?;
        Ok(buffer.len())
    }

    fn write_all(&mut self, buffer: &[u8]) -> std::io::Result<()> {
        let new_len = self
            .bytes
            .len()
            .checked_add(buffer.len())
            .ok_or_else(|| std::io::Error::other("serialized PDF output size overflowed"))?;
        if self.max_size.is_some_and(|limit| new_len > limit) {
            self.exceeded = true;
            return Err(std::io::Error::other(
                "serialized PDF output size limit exceeded",
            ));
        }
        self.bytes.try_reserve(buffer.len()).map_err(|error| {
            std::io::Error::other(format!("failed to allocate serialized PDF output: {error}"))
        })?;
        self.bytes.extend_from_slice(buffer);
        Ok(())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// Serialize through a writer that never retains bytes beyond `max_size`.
fn serialize_pdf_with_limit(
    document: &mut Document,
    options: Option<SaveOptions>,
    max_size: Option<usize>,
    limit_code: &'static str,
    size_label: &str,
) -> PyResult<Vec<u8>> {
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    let mut output = BoundedPdfOutput::new(max_size);
    let result = match options {
        Some(options) => document.save_with_options(&mut output, options).map(|_| ()),
        None => document.save_to(&mut output).map(|_| ()),
    };
    if output.exceeded {
        let limit = max_size.expect("an output can exceed only a configured limit");
        return Err(limit_err(
            limit_code,
            format!("{size_label} exceeds the configured {limit}-byte limit"),
        ));
    }
    result.map_err(|error| PdfError::new_err(error.to_string()))?;
    Ok(output.bytes)
}

/// Serialize public PDF output through its stable output-size boundary.
fn serialize_pdf(
    document: &mut Document,
    options: Option<SaveOptions>,
    max_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    serialize_pdf_with_limit(
        document,
        options,
        max_size,
        "pdf_output_size",
        "serialized PDF output",
    )
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

/// Import a page as an unplaced Form XObject and return its display metadata.
///
/// This is shared by page placement and krilla-generated widget appearances.
fn import_page_as_form(
    target: &mut Document,
    mut source: Document,
    page_number: u32,
) -> PyResult<(ObjectId, [f64; 4], i64)> {
    let starting_id = target
        .max_id
        .checked_add(1)
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    source.renumber_objects_with(starting_id);
    let source_id = *source
        .get_pages()
        .get(&page_number)
        .ok_or_else(|| PdfError::new_err(format!("source page {page_number} does not exist")))?;
    let source_dict = resolve_inherited_page_dict(&source, source_id).map_err(to_py_err)?;
    let source_crop = resolve_box(&source, &source_dict, b"CropBox")
        .or_else(|| resolve_box(&source, &source_dict, b"MediaBox"))
        .unwrap_or([0.0, 0.0, 595.0, 842.0]);
    let source_rotation = source_dict
        .get(b"Rotate")
        .ok()
        .and_then(|object| resolve_i64(&source, object))
        .unwrap_or(0)
        .rem_euclid(360);

    let mut form_content = b"q\n".to_vec();
    form_content.extend_from_slice(&source.get_page_content(source_id));
    form_content.extend_from_slice(b"\nQ\n");
    let mut form_dict = dictionary! {
        "Type" => "XObject",
        "Subtype" => "Form",
        "FormType" => 1,
        "BBox" => Object::Array(
            source_crop
                .iter()
                .map(|&value| Object::Real(value as f32))
                .collect(),
        ),
    };
    if let Ok(resources) = source_dict.get(b"Resources") {
        form_dict.set("Resources", resources.clone());
    }
    if let Ok(group) = source_dict.get(b"Group") {
        form_dict.set("Group", group.clone());
    }

    let new_max_id = source.max_id;
    for (id, object) in source.objects {
        match object.type_name().unwrap_or(b"") {
            b"Catalog" | b"Pages" | b"Page" => {}
            _ => {
                target.objects.insert(id, object);
            }
        }
    }
    target.max_id = new_max_id;
    let form_id = target.add_object(Stream::new(form_dict, form_content).with_compression(false));
    Ok((form_id, source_crop, source_rotation))
}

/// Extract an XMP value from `key="v"` attributes or `<key>v</key>` elements.
fn xmp_value(xmp: &str, key: &str) -> Option<String> {
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

/// Convert a hayro Pixmap to straight-alpha RGBA8 bytes.
fn rgba_bytes(pixmap: hayro::vello_cpu::Pixmap) -> Result<Vec<u8>, String> {
    let pixels = pixmap.take_unpremultiplied();
    let output_len = pixels
        .len()
        .checked_mul(4)
        .ok_or_else(|| "rendered RGBA buffer size overflowed".to_owned())?;
    let mut out = Vec::new();
    out.try_reserve_exact(output_len)
        .map_err(|error| format!("failed to allocate rendered RGBA buffer: {error}"))?;
    for px in pixels {
        out.extend_from_slice(&[px.r, px.g, px.b, px.a]);
    }
    Ok(out)
}

/// Convert only a display-coordinate crop of a hayro Pixmap to RGBA8 bytes.
fn cropped_rgba_bytes(
    pixmap: hayro::vello_cpu::Pixmap,
    width: u32,
    height: u32,
    scale: f32,
    clip: (f64, f64, f64, f64),
) -> Result<(u32, u32, Vec<u8>), String> {
    if ![clip.0, clip.1, clip.2, clip.3]
        .into_iter()
        .all(f64::is_finite)
        || clip.0 >= clip.2
        || clip.1 >= clip.3
    {
        return Err("clip must be a finite rectangle with x0 < x1 and y0 < y1".to_owned());
    }
    let scale = f64::from(scale);
    let pixel_x0 = (clip.0 * scale).floor().clamp(0.0, f64::from(width)) as u32;
    let pixel_y0 = (clip.1 * scale).floor().clamp(0.0, f64::from(height)) as u32;
    let pixel_x1 = (clip.2 * scale).ceil().clamp(0.0, f64::from(width)) as u32;
    let pixel_y1 = (clip.3 * scale).ceil().clamp(0.0, f64::from(height)) as u32;
    if pixel_x0 >= pixel_x1 || pixel_y0 >= pixel_y1 {
        return Err("clip does not intersect the rendered page".to_owned());
    }

    let cropped_width = pixel_x1 - pixel_x0;
    let cropped_height = pixel_y1 - pixel_y0;
    let source_width =
        usize::try_from(width).map_err(|_| "rendered page width is too large".to_owned())?;
    let source_x0 =
        usize::try_from(pixel_x0).map_err(|_| "cropped page offset is too large".to_owned())?;
    let source_x1 =
        usize::try_from(pixel_x1).map_err(|_| "cropped page offset is too large".to_owned())?;
    let capacity = usize::try_from(cropped_width)
        .ok()
        .and_then(|width| {
            usize::try_from(cropped_height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| "cropped page is too large".to_owned())?;
    let pixels = pixmap.take_unpremultiplied();
    let mut cropped = Vec::new();
    cropped
        .try_reserve_exact(capacity)
        .map_err(|error| format!("failed to allocate cropped RGBA buffer: {error}"))?;
    for y in pixel_y0..pixel_y1 {
        let row_start = usize::try_from(y)
            .ok()
            .and_then(|value| value.checked_mul(source_width))
            .and_then(|value| value.checked_add(source_x0))
            .ok_or_else(|| "cropped page offset is too large".to_owned())?;
        let row_end = row_start
            .checked_add(source_x1 - source_x0)
            .ok_or_else(|| "cropped page offset is too large".to_owned())?;
        for px in pixels
            .get(row_start..row_end)
            .ok_or_else(|| "cropped page exceeds the rendered image".to_owned())?
        {
            cropped.extend_from_slice(&[px.r, px.g, px.b, px.a]);
        }
    }
    Ok((cropped_width, cropped_height, cropped))
}

/// Validate one page and return its raster pixel count.
fn render_pixel_count(pdf: &Pdf, page_number: u32, scale: f32) -> Result<u64, String> {
    if !scale.is_finite() || scale <= 0.0 {
        return Err("scale must be a finite, positive value".to_owned());
    }
    let pages = pdf.pages();
    let page = page_number
        .checked_sub(1)
        .and_then(|index| pages.get(index as usize))
        .ok_or_else(|| format!("page {page_number} does not exist"))?;
    let (page_width, page_height) = page.render_dimensions();
    let pixel_width = (f64::from(page_width) * f64::from(scale)).floor();
    let pixel_height = (f64::from(page_height) * f64::from(scale)).floor();
    if !pixel_width.is_finite()
        || !pixel_height.is_finite()
        || pixel_width < 1.0
        || pixel_height < 1.0
    {
        return Err("scale is too small, or the PDF page size is invalid".to_owned());
    }
    if pixel_width > f64::from(u16::MAX) || pixel_height > f64::from(u16::MAX) {
        return Err(format!(
            "render size {pixel_width:.0}x{pixel_height:.0} exceeds the 65535-pixel limit per side"
        ));
    }
    let total_pixels = (pixel_width as u64) * (pixel_height as u64);
    if total_pixels > MAX_RENDER_PIXELS {
        return Err(format!(
            "render size {pixel_width:.0}x{pixel_height:.0} ({total_pixels} pixels) exceeds the {MAX_RENDER_PIXELS}-pixel limit"
        ));
    }
    Ok(total_pixels)
}

/// Render one page from an immutable hayro snapshot and caller-owned cache.
fn render_pdf_page<'a>(
    pdf: &'a Pdf,
    cache: &RenderCache<'a>,
    interpreter_settings: &InterpreterSettings,
    page_number: u32,
    scale: f32,
    background: Option<(u8, u8, u8, u8)>,
) -> Result<hayro::vello_cpu::Pixmap, String> {
    render_pixel_count(pdf, page_number, scale)?;
    let pages = pdf.pages();
    let page = page_number
        .checked_sub(1)
        .and_then(|index| pages.get(index as usize))
        .ok_or_else(|| format!("page {page_number} does not exist"))?;
    let mut render_settings = RenderSettings {
        x_scale: scale,
        y_scale: scale,
        ..Default::default()
    };
    if let Some((r, g, b, a)) = background {
        render_settings.bg_color = AlphaColor::from_rgba8(r, g, b, a);
    }
    Ok(render(page, cache, interpreter_settings, &render_settings))
}

/// Convert a rendered pixmap into the bounded fast PNG representation used publicly.
fn rendered_png(pixmap: hayro::vello_cpu::Pixmap, max_size: Option<usize>) -> PyResult<Vec<u8>> {
    let width = u32::from(pixmap.width());
    let height = u32::from(pixmap.height());
    let data = rgba_bytes(pixmap).map_err(PdfError::new_err)?;
    match crate::extract::encode_png_bounded(
        width,
        height,
        png::ColorType::Rgba,
        &data,
        png::Compression::Fast,
        max_size,
    ) {
        Ok(png) => Ok(png),
        Err(crate::extract::PngEncodeError::OutputLimit) => {
            let limit = max_size.expect("PNG output can exceed only a configured limit");
            Err(limit_err(
                "render_output_size",
                format!("rendered PNG exceeds the {limit}-byte encoded-output limit"),
            ))
        }
        Err(crate::extract::PngEncodeError::Encoding(error)) => {
            Err(PdfError::new_err(format!("failed to encode PNG: {error}")))
        }
    }
}

struct BatchPngOutput<'a> {
    bytes: Vec<u8>,
    max_size: usize,
    output_bytes: &'a AtomicUsize,
    exceeded: bool,
    committed: bool,
}

impl<'a> BatchPngOutput<'a> {
    fn new(max_size: usize, output_bytes: &'a AtomicUsize) -> Self {
        Self {
            bytes: Vec::new(),
            max_size,
            output_bytes,
            exceeded: false,
            committed: false,
        }
    }

    fn finish(mut self) -> Vec<u8> {
        self.committed = true;
        std::mem::take(&mut self.bytes)
    }
}

impl Write for BatchPngOutput<'_> {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.write_all(buffer)?;
        Ok(buffer.len())
    }

    fn write_all(&mut self, buffer: &[u8]) -> io::Result<()> {
        if self
            .output_bytes
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |total| {
                total
                    .checked_add(buffer.len())
                    .filter(|&new_total| new_total <= self.max_size)
            })
            .is_err()
        {
            self.exceeded = true;
            return Err(io::Error::other("PNG batch output size limit exceeded"));
        }
        if let Err(error) = self.bytes.try_reserve(buffer.len()) {
            self.output_bytes.fetch_sub(buffer.len(), Ordering::Relaxed);
            return Err(io::Error::other(format!(
                "failed to allocate rendered PNG: {error}"
            )));
        }
        self.bytes.extend_from_slice(buffer);
        Ok(())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl Drop for BatchPngOutput<'_> {
    fn drop(&mut self) {
        if !self.committed {
            self.output_bytes
                .fetch_sub(self.bytes.len(), Ordering::Relaxed);
        }
    }
}

/// Encode one batch page while charging each retained PNG chunk atomically.
fn rendered_batch_png(
    pixmap: hayro::vello_cpu::Pixmap,
    max_size: usize,
    output_bytes: &AtomicUsize,
) -> PyResult<Vec<u8>> {
    let width = u32::from(pixmap.width());
    let height = u32::from(pixmap.height());
    let data = rgba_bytes(pixmap).map_err(PdfError::new_err)?;
    let mut output = BatchPngOutput::new(max_size, output_bytes);
    let result = crate::extract::write_png(
        &mut output,
        width,
        height,
        png::ColorType::Rgba,
        &data,
        png::Compression::Fast,
    );
    if output.exceeded {
        return Err(limit_err(
            "render_output_size",
            format!("rendered PNG batch exceeds the {max_size}-byte encoded-output limit"),
        ));
    }
    result.map_err(|error| PdfError::new_err(format!("failed to encode PNG: {error}")))?;
    Ok(output.finish())
}

struct BatchRenderer<'pdf, 'shared> {
    pdf: &'pdf Pdf,
    interpreter_settings: &'shared InterpreterSettings,
    scale: f32,
    background: Option<(u8, u8, u8, u8)>,
    max_output_size: Option<usize>,
    output_bytes: &'shared AtomicUsize,
}

impl<'pdf> BatchRenderer<'pdf, '_> {
    /// Render and encode one page through the shared output budget.
    fn render(&self, cache: &RenderCache<'pdf>, page_number: u32) -> PyResult<Vec<u8>> {
        let pixmap = render_pdf_page(
            self.pdf,
            cache,
            self.interpreter_settings,
            page_number,
            self.scale,
            self.background,
        )
        .map_err(PdfError::new_err)?;
        match self.max_output_size {
            Some(limit) => rendered_batch_png(pixmap, limit, self.output_bytes),
            None => rendered_png(pixmap, None),
        }
    }
}

/// Clone a dictionary while allowing an indirect reference.
fn deref_dict(doc: &Document, obj: &Object) -> Option<Dictionary> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok().cloned(),
        Object::Dictionary(d) => Some(d.clone()),
        _ => None,
    }
}

/// Preserve control characters that PDFDocEncoding intentionally leaves unmapped.
fn form_text_string(text: &str) -> Object {
    if !text.chars().any(char::is_control) {
        return text_string(text);
    }
    let mut encoded = vec![0xfe, 0xff];
    for unit in text.encode_utf16() {
        encoded.extend_from_slice(&unit.to_be_bytes());
    }
    Object::String(encoded, StringFormat::Hexadecimal)
}

/// Resolve annotation state dictionaries to their selected stream for hayro.
///
/// PDF permits `/AP /N` to be a state-name dictionary selected by `/AS`.
/// hayro 0.7 only consumes a direct normal stream, so rendering uses a clone
/// with the selected entry substituted while the editable PDF stays canonical.
fn normalize_state_appearances_for_render(doc: &mut Document) -> bool {
    let object_ids: Vec<ObjectId> = doc.objects.keys().copied().collect();
    let mut updates = Vec::new();
    for object_id in object_ids {
        let Some((mut appearance, selected)) = doc
            .get_object(object_id)
            .ok()
            .and_then(|object| object.as_dict().ok())
            .and_then(|annotation| {
                let state = annotation.get(b"AS").and_then(Object::as_name).ok()?;
                let appearance = deref_dict(doc, annotation.get(b"AP").ok()?).unwrap_or_default();
                let normal = deref_dict(doc, appearance.get(b"N").ok()?)?;
                let selected = normal.get(state).ok()?.clone();
                let resolved = match &selected {
                    Object::Reference(id) => doc.get_object(*id).ok()?,
                    other => other,
                };
                resolved.as_stream().ok()?;
                Some((appearance, selected))
            })
        else {
            continue;
        };
        appearance.set("N", selected);
        updates.push((object_id, appearance));
    }
    for (object_id, appearance) in &updates {
        if let Ok(annotation) = doc.get_object_mut(*object_id).and_then(Object::as_dict_mut) {
            annotation.set("AP", Object::Dictionary(appearance.clone()));
        }
    }
    !updates.is_empty()
}

fn has_state_appearances(doc: &Document) -> bool {
    doc.objects.values().any(|object| {
        object
            .as_dict()
            .ok()
            .and_then(|annotation| {
                let state = annotation.get(b"AS").and_then(Object::as_name).ok()?;
                let appearance = deref_dict(doc, annotation.get(b"AP").ok()?)?;
                let normal = deref_dict(doc, appearance.get(b"N").ok()?)?;
                let selected = normal.get(state).ok()?;
                let resolved = match selected {
                    Object::Reference(id) => doc.get_object(*id).ok()?,
                    other => other,
                };
                resolved.as_stream().is_ok().then_some(())
            })
            .is_some()
    })
}

struct TextMarkupAppearancePlan {
    annotation_id: ObjectId,
    kind: draw::TextMarkupKind,
    quads: Vec<[(f64, f64); 4]>,
    segment_counts: Vec<usize>,
    color: (f64, f64, f64),
    opacity: f64,
}

fn finite_number(doc: &Document, object: &Object) -> Option<f64> {
    let value = f64::from(deref_object(doc, object).as_float().ok()?);
    value.is_finite().then_some(value)
}

fn annotation_has_normal_appearance(doc: &Document, annotation: &Dictionary) -> bool {
    annotation
        .get(b"AP")
        .ok()
        .and_then(|appearance| deref_dict(doc, appearance))
        .and_then(|appearance| appearance.get(b"N").ok().cloned())
        .is_some_and(|normal| {
            let resolved = deref_object(doc, &normal);
            resolved.as_stream().is_ok() || resolved.as_dict().is_ok()
        })
}

fn text_markup_kind(subtype: &[u8]) -> Option<draw::TextMarkupKind> {
    match subtype {
        b"Highlight" => Some(draw::TextMarkupKind::Highlight),
        b"Underline" => Some(draw::TextMarkupKind::Underline),
        b"Squiggly" => Some(draw::TextMarkupKind::Squiggly),
        b"StrikeOut" => Some(draw::TextMarkupKind::StrikeOut),
        _ => None,
    }
}

fn text_markup_segment_count(kind: draw::TextMarkupKind, quad: [(f64, f64); 4]) -> Option<usize> {
    if matches!(kind, draw::TextMarkupKind::Highlight) {
        return Some(4);
    }
    let [upper_left, upper_right, lower_left, lower_right] = quad;
    let (start, end) = match kind {
        draw::TextMarkupKind::Underline | draw::TextMarkupKind::Squiggly => {
            (lower_left, lower_right)
        }
        draw::TextMarkupKind::StrikeOut => (
            (
                (upper_left.0 + lower_left.0) / 2.0,
                (upper_left.1 + lower_left.1) / 2.0,
            ),
            (
                (upper_right.0 + lower_right.0) / 2.0,
                (upper_right.1 + lower_right.1) / 2.0,
            ),
        ),
        draw::TextMarkupKind::Highlight => unreachable!(),
    };
    let inline_length = (end.0 - start.0).hypot(end.1 - start.1);
    if !inline_length.is_finite() || inline_length <= f64::EPSILON {
        return None;
    }
    if matches!(
        kind,
        draw::TextMarkupKind::Underline | draw::TextMarkupKind::Squiggly
    ) {
        let top = (
            (upper_left.0 + upper_right.0) / 2.0,
            (upper_left.1 + upper_right.1) / 2.0,
        );
        let bottom = (
            (lower_left.0 + lower_right.0) / 2.0,
            (lower_left.1 + lower_right.1) / 2.0,
        );
        let cross_length = (top.0 - bottom.0).hypot(top.1 - bottom.1);
        if !cross_length.is_finite() || cross_length <= f64::EPSILON {
            return None;
        }
    }
    if matches!(kind, draw::TextMarkupKind::Squiggly) {
        let segments = (inline_length / 2.0).ceil().max(1.0);
        return Some(segments as usize);
    }
    Some(1)
}

/// Collect bounded text-markup annotations that need a render-only appearance.
///
/// PDF viewers may synthesize a normal appearance from `/QuadPoints` and `/C`,
/// but hayro 0.7 requires `/AP /N`. Keep this compatibility work outside the
/// editable document and refuse aggregate geometry amplification.
fn missing_text_markup_appearance_plans(doc: &Document) -> Option<Vec<TextMarkupAppearancePlan>> {
    let mut plans = Vec::new();
    let mut total_quads = 0usize;
    let mut total_segments = 0usize;
    for (&annotation_id, object) in &doc.objects {
        let Ok(annotation) = object.as_dict() else {
            continue;
        };
        let Some(kind) = annotation
            .get(b"Subtype")
            .ok()
            .and_then(|subtype| deref_object(doc, subtype).as_name().ok())
            .and_then(text_markup_kind)
        else {
            continue;
        };
        if annotation_has_normal_appearance(doc, annotation) {
            continue;
        }

        let Ok(quad_points) = annotation
            .get(b"QuadPoints")
            .map(|points| deref_object(doc, points))
            .and_then(Object::as_array)
        else {
            continue;
        };
        if quad_points.is_empty() || quad_points.len() % 8 != 0 {
            continue;
        }
        let quad_count = quad_points.len() / 8;
        total_quads = total_quads.checked_add(quad_count)?;
        if total_quads > MAX_HIGHLIGHT_RECTS {
            return None;
        }

        let mut quads = Vec::with_capacity(quad_count);
        let mut segment_counts = Vec::with_capacity(quad_count);
        let mut valid = true;
        for chunk in quad_points.chunks_exact(8) {
            let mut values = [0.0f64; 8];
            for (slot, value) in values.iter_mut().zip(chunk) {
                let Some(number) = finite_number(doc, value) else {
                    valid = false;
                    break;
                };
                *slot = number;
            }
            if !valid {
                break;
            }
            let quad = [
                (values[0], values[1]),
                (values[2], values[3]),
                (values[4], values[5]),
                (values[6], values[7]),
            ];
            let Some(segment_count) = text_markup_segment_count(kind, quad) else {
                valid = false;
                break;
            };
            total_segments = total_segments.checked_add(segment_count)?;
            if total_segments > MAX_TEXT_MARKUP_SEGMENTS {
                return None;
            }
            quads.push(quad);
            segment_counts.push(segment_count);
        }
        if !valid {
            continue;
        }

        let Some(color) = annotation
            .get(b"C")
            .ok()
            .map(|color| deref_object(doc, color))
            .and_then(|color| color.as_array().ok())
            .filter(|color| color.len() == 3)
            .and_then(|color| {
                Some((
                    finite_number(doc, &color[0])?,
                    finite_number(doc, &color[1])?,
                    finite_number(doc, &color[2])?,
                ))
            })
            .filter(|color| {
                [color.0, color.1, color.2]
                    .into_iter()
                    .all(|component| (0.0..=1.0).contains(&component))
            })
        else {
            continue;
        };
        let opacity = if let Ok(opacity) = annotation.get(b"CA") {
            let Some(opacity) = finite_number(doc, opacity) else {
                continue;
            };
            opacity
        } else {
            1.0
        };
        if !(0.0..=1.0).contains(&opacity) {
            continue;
        }

        let points: Vec<(f64, f64)> = quads.iter().flatten().copied().collect();
        let bbox = draw::bounding_rect(&points);
        if bbox.iter().any(|value| !value.is_finite()) || bbox[0] >= bbox[2] || bbox[1] >= bbox[3] {
            continue;
        }
        plans.push(TextMarkupAppearancePlan {
            annotation_id,
            kind,
            quads,
            segment_counts,
            color,
            opacity,
        });
    }
    Some(plans)
}

fn has_missing_text_markup_appearances(doc: &Document) -> bool {
    missing_text_markup_appearance_plans(doc).is_some_and(|plans| !plans.is_empty())
}

/// Add bounded text-markup appearances to a rendering clone only.
fn synthesize_missing_text_markup_appearances_for_render(doc: &mut Document) -> bool {
    let Some(plans) = missing_text_markup_appearance_plans(doc) else {
        return false;
    };
    let Ok(object_count) = u32::try_from(plans.len()).map(|count| count.saturating_mul(2)) else {
        return false;
    };
    if doc.max_id.checked_add(object_count).is_none() {
        return false;
    }

    let mut changed = false;
    for plan in plans {
        let points: Vec<(f64, f64)> = plan.quads.iter().flatten().copied().collect();
        let bbox = draw::bounding_rect(&points);
        let gs_id = doc.add_object(dictionary! {
            "Type" => "ExtGState",
            "BM" => Object::Name(plan.kind.blend_mode().as_bytes().to_vec()),
            "CA" => Object::Real(plan.opacity as f32),
            "ca" => Object::Real(plan.opacity as f32),
            "AIS" => Object::Boolean(false),
        });
        let form_dict = dictionary! {
            "Type" => "XObject",
            "Subtype" => "Form",
            "FormType" => 1,
            "BBox" => Object::Array(bbox.iter().map(|&value| Object::Real(value as f32)).collect()),
            "Resources" => dictionary! {
                "ExtGState" => dictionary! { "PyloGS" => Object::Reference(gs_id) },
            },
        };
        let appearance_ops =
            draw::text_markup_ap_ops(plan.kind, &plan.quads, plan.color, &plan.segment_counts);
        let appearance_id =
            doc.add_object(Stream::new(form_dict, appearance_ops).with_compression(false));
        if let Ok(annotation) = doc
            .get_object_mut(plan.annotation_id)
            .and_then(Object::as_dict_mut)
        {
            annotation.set(
                "AP",
                dictionary! { "N" => Object::Reference(appearance_id) },
            );
            changed = true;
        }
    }
    changed
}

/// Read an integer while allowing an indirect reference.
fn resolve_i64(doc: &Document, obj: &Object) -> Option<i64> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_i64().ok(),
        other => other.as_i64().ok(),
    }
}

/// Validate RunLengthDecode output size without allocating the output.
fn run_length_size(data: &[u8], max_output: usize, limit_code: &'static str) -> PyResult<usize> {
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
                    limit_err(
                        limit_code,
                        "RunLengthDecode decompressed size exceeds the configured limit",
                    )
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
                        limit_err(
                            limit_code,
                            "RunLengthDecode decompressed size exceeds the configured limit",
                        )
                    })?;
            }
        }
        if output_len > max_output {
            return Err(limit_err(
                limit_code,
                format!(
                    "decompressed output exceeded the {max_output}-byte limit (possible decompression bomb)"
                ),
            ));
        }
    }
    Ok(output_len)
}

/// Validate ASCIIHexDecode output size without allocating the output.
fn ascii_hex_size(data: &[u8], max_output: usize, limit_code: &'static str) -> PyResult<usize> {
    let digits = data
        .iter()
        .take_while(|&&byte| byte != b'>')
        .filter(|byte| !byte.is_ascii_whitespace())
        .count();
    let output_len = digits.div_ceil(2);
    if output_len > max_output {
        return Err(limit_err(
            limit_code,
            format!(
                "decompressed output exceeded the {max_output}-byte limit (possible decompression bomb)"
            ),
        ));
    }
    Ok(output_len)
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

/// Decode a general-purpose stream with an optional bound on every filter layer.
///
/// Unlike lopdf's lenient `get_plain_content`, malformed or unsupported filters
/// are errors rather than a reason to return encoded bytes as if they were plain.
fn decoded_stream_content(
    stream: &Stream,
    max_size: Option<usize>,
    limit_code: &'static str,
    context: &str,
) -> PyResult<Vec<u8>> {
    let reject_raw = |length: usize, limit: usize| {
        limit_err(
            limit_code,
            format!("{context} is {length} bytes, exceeding the {limit}-byte decoded-size limit"),
        )
    };
    if !stream.dict.has(b"Filter") {
        if let Some(limit) = max_size
            && stream.content.len() > limit
        {
            return Err(reject_raw(stream.content.len(), limit));
        }
        return Ok(stream.content.clone());
    }

    let raw_filters = stream
        .filters()
        .map_err(|error| PdfError::new_err(format!("{context} has an invalid Filter: {error}")))?;
    if raw_filters.is_empty() {
        if let Some(limit) = max_size
            && stream.content.len() > limit
        {
            return Err(reject_raw(stream.content.len(), limit));
        }
        return Ok(stream.content.clone());
    }
    let normalized_filters: Vec<&[u8]> = raw_filters
        .iter()
        .map(|filter| canonical_filter_name(filter))
        .collect();
    let mut checked_stream = stream.clone();
    checked_stream.dict.set(
        "Filter",
        if normalized_filters.len() == 1 {
            Object::Name(normalized_filters[0].to_vec())
        } else {
            Object::Array(
                normalized_filters
                    .iter()
                    .map(|filter| Object::Name(filter.to_vec()))
                    .collect(),
            )
        },
    );
    let decode_error = |error: lopdf::Error| {
        if matches!(
            error,
            lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
        ) && let Some(limit) = max_size
        {
            limit_err(
                limit_code,
                format!("{context} exceeds the {limit}-byte decoded-size limit"),
            )
        } else {
            PdfError::new_err(format!("failed to decode {context}: {error}"))
        }
    };
    match max_size {
        Some(limit) => checked_stream
            .decompressed_content_with_limit(limit)
            .map_err(decode_error),
        None => checked_stream.decompressed_content().map_err(decode_error),
    }
}

/// Return the maximum direct array/dictionary nesting in one object.
///
/// Indirect references are leaves here. This makes cycles harmless while
/// bounding the recursive object shapes consumed by downstream libraries.
fn direct_object_depth(object: &Object, stop_above: Option<usize>) -> usize {
    enum Pending<'a> {
        Visit(&'a Object, usize),
        ArrayTail(&'a [Object], usize),
    }

    let mut maximum = 1usize;
    let mut pending = vec![Pending::Visit(object, 1usize)];
    while let Some(work) = pending.pop() {
        let (current, depth) = match work {
            Pending::Visit(current, depth) => (current, depth),
            Pending::ArrayTail(items, depth) => {
                let Some((current, remaining)) = items.split_first() else {
                    continue;
                };
                if !remaining.is_empty() {
                    pending.push(Pending::ArrayTail(remaining, depth));
                }
                (current, depth)
            }
        };
        maximum = maximum.max(depth);
        if stop_above.is_some_and(|limit| maximum > limit) {
            return maximum;
        }
        let child_depth = depth.saturating_add(1);
        match current {
            Object::Array(items) if !items.is_empty() => {
                pending.push(Pending::ArrayTail(items, child_depth));
            }
            Object::Dictionary(dict) => {
                pending.extend(
                    dict.iter()
                        .map(|(_, item)| Pending::Visit(item, child_depth)),
                );
            }
            Object::Stream(stream) => {
                pending.extend(
                    stream
                        .dict
                        .iter()
                        .map(|(_, item)| Pending::Visit(item, child_depth)),
                );
            }
            _ => {}
        }
    }
    maximum
}

/// Collect cheap structural complexity without decoding streams.
fn document_complexity(doc: &Document) -> ComplexityTuple {
    let page_count = doc.get_pages().len();
    let object_count = doc.objects.len();
    let mut stream_count = 0usize;
    let mut encoded_stream_bytes = 0u64;
    let mut max_object_depth = doc
        .trailer
        .iter()
        .map(|(_, object)| direct_object_depth(object, None))
        .max()
        .unwrap_or_default();
    for object in doc.objects.values() {
        max_object_depth = max_object_depth.max(direct_object_depth(object, None));
        if let Object::Stream(stream) = object {
            stream_count = stream_count.saturating_add(1);
            encoded_stream_bytes = encoded_stream_bytes.saturating_add(stream.content.len() as u64);
        }
    }
    (
        page_count,
        object_count,
        stream_count,
        encoded_stream_bytes,
        max_object_depth,
    )
}

/// Reject structural work above configured limits before rendering/extraction.
fn validate_structural_limits(
    doc: &Document,
    limits: DocumentLimits,
    validate_pages: bool,
) -> PyResult<()> {
    if let Some(limit) = limits.max_objects
        && doc.objects.len() > limit
    {
        return Err(limit_err(
            "object_count",
            format!(
                "PDF contains {} indirect objects, exceeding the configured limit of {limit}",
                doc.objects.len()
            ),
        ));
    }

    if let Some(limit) = limits.max_object_depth {
        let depth = doc
            .trailer
            .iter()
            .map(|(_, object)| direct_object_depth(object, Some(limit)))
            .chain(
                doc.objects
                    .values()
                    .map(|object| direct_object_depth(object, Some(limit))),
            )
            .max()
            .unwrap_or_default();
        if depth > limit {
            return Err(limit_err(
                "object_depth",
                format!(
                    "PDF direct object nesting depth is {depth}, exceeding the configured limit of {limit}"
                ),
            ));
        }
    }

    if validate_pages && limits.max_pages.is_some() {
        let pages = doc.get_pages().len();
        if let Some(limit) = limits.max_pages
            && pages > limit
        {
            return Err(limit_err(
                "page_count",
                format!("PDF contains {pages} pages, exceeding the configured limit of {limit}"),
            ));
        }
    }
    Ok(())
}

/// Map bounded decoder failures to the applicable stable limit code.
fn decompression_err(error: lopdf::Error, code: &'static str) -> PyErr {
    if matches!(
        error,
        lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
    ) {
        limit_err(code, error.to_string())
    } else {
        to_py_err(error)
    }
}

/// Measure one decoded stream with bounded intermediate allocations.
fn decoded_stream_size(
    doc: &Document,
    stream: &Stream,
    max_output: usize,
    limit_code: &'static str,
) -> PyResult<usize> {
    const LOPDF_FILTERS: [&[u8]; 3] = [b"FlateDecode", b"LZWDecode", b"ASCII85Decode"];
    const IMAGE_FILTERS: [&[u8]; 4] = [
        b"DCTDecode",
        b"JPXDecode",
        b"JBIG2Decode",
        b"CCITTFaxDecode",
    ];

    if !stream.dict.has(b"Filter") {
        if stream.content.len() > max_output {
            return Err(limit_err(
                limit_code,
                format!("decompressed output exceeded the {max_output}-byte limit"),
            ));
        }
        return Ok(stream.content.len());
    }
    let raw_filters = stream.filters().map_err(to_py_err)?;
    let filters: Vec<&[u8]> = raw_filters
        .iter()
        .map(|filter| canonical_filter_name(filter))
        .collect();
    if filters.is_empty() {
        if stream.content.len() > max_output {
            return Err(limit_err(
                limit_code,
                format!("decompressed output exceeded the {max_output}-byte limit"),
            ));
        }
        return Ok(stream.content.len());
    }
    // lopdf accepts canonical filter names only; normalize on the clone.
    let mut checked_stream = stream.clone();
    let normalized_filter = |selected: &[&[u8]]| {
        if selected.len() == 1 {
            Object::Name(selected[0].to_vec())
        } else {
            Object::Array(
                selected
                    .iter()
                    .map(|filter| Object::Name(filter.to_vec()))
                    .collect(),
            )
        }
    };
    checked_stream
        .dict
        .set("Filter", normalized_filter(&filters));

    let first_unsupported = filters
        .iter()
        .position(|filter| !LOPDF_FILTERS.contains(filter));
    match first_unsupported {
        None => checked_stream
            .get_plain_content_with_limit(max_output)
            .map(|content| content.len())
            .map_err(|error| decompression_err(error, limit_code)),
        Some(index)
            if IMAGE_FILTERS.contains(&filters[index])
                && index + 1 == filters.len()
                && filters[..index]
                    .iter()
                    .all(|filter| LOPDF_FILTERS.contains(filter)) =>
        {
            // Bound and measure any compression layers before the image codec.
            let prefix_size = if index == 0 {
                0
            } else {
                checked_stream
                    .dict
                    .set("Filter", normalized_filter(&filters[..index]));
                checked_stream
                    .get_plain_content_with_limit(max_output)
                    .map(|content| content.len())
                    .map_err(|error| decompression_err(error, limit_code))?
            };
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
                return Err(limit_err(
                    "decompression_unverifiable",
                    "cannot resolve an image stream's Width/Height under the configured decompression policy",
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
                .and_then(|bytes| usize::try_from(bytes).ok())
                .ok_or_else(|| {
                    limit_err(
                        limit_code,
                        "image decompressed size exceeds the platform limit",
                    )
                })?;
            let measured = prefix_size.max(decoded_size);
            if measured > max_output {
                return Err(limit_err(
                    limit_code,
                    format!(
                        "decompressed image output exceeded the {max_output}-byte limit (possible decompression bomb)"
                    ),
                ));
            }
            Ok(measured)
        }
        Some(index) if filters.len() == 1 && filters[index] == b"RunLengthDecode" => {
            run_length_size(&stream.content, max_output, limit_code)
        }
        Some(index) if filters.len() == 1 && filters[index] == b"ASCIIHexDecode" => {
            ascii_hex_size(&stream.content, max_output, limit_code)
        }
        Some(index) => Err(limit_err(
            "decompression_unverifiable",
            format!(
                "cannot safely verify a stream with filter {:?} under the configured decompression policy",
                String::from_utf8_lossy(filters[index])
            ),
        )),
    }
}

/// Prevalidate per-stream and cumulative decompression budgets at load.
fn validate_decompression_limits(
    doc: &Document,
    max_output: Option<usize>,
    max_page_content: Option<usize>,
    max_total: Option<usize>,
) -> PyResult<()> {
    if max_output.is_none() && max_page_content.is_none() && max_total.is_none() {
        return Ok(());
    }
    let page_content_ids: HashSet<ObjectId> = if max_page_content.is_some() {
        doc.get_pages()
            .values()
            .flat_map(|page_id| doc.get_page_contents(*page_id))
            .collect()
    } else {
        HashSet::new()
    };
    let mut total = 0usize;
    for (object_id, object) in &doc.objects {
        let Object::Stream(stream) = object else {
            continue;
        };
        let is_page_content = page_content_ids.contains(object_id);
        if max_output.is_none() && max_total.is_none() && !is_page_content {
            continue;
        }
        let mut decode_bound = usize::MAX;
        let mut bound_code = "decompressed_size";
        if let Some(limit) = max_output {
            decode_bound = limit;
        }
        if is_page_content
            && let Some(limit) = max_page_content
            && limit < decode_bound
        {
            decode_bound = limit;
            bound_code = "page_content_size";
        }
        if let Some(limit) = max_total
            && limit < decode_bound
        {
            decode_bound = limit;
            bound_code = "total_decompressed_size";
        }
        let size = decoded_stream_size(doc, stream, decode_bound, bound_code)?;
        if let Some(limit) = max_output
            && size > limit
        {
            return Err(limit_err(
                "decompressed_size",
                format!(
                    "stream expands to {size} bytes, exceeding the configured limit of {limit}"
                ),
            ));
        }
        if is_page_content
            && let Some(limit) = max_page_content
            && size > limit
        {
            return Err(limit_err(
                "page_content_size",
                format!(
                    "page content stream expands to {size} bytes, exceeding the configured limit of {limit}"
                ),
            ));
        }
        total = total.checked_add(size).ok_or_else(|| {
            limit_err(
                "total_decompressed_size",
                "cumulative decompressed size exceeds the platform limit",
            )
        })?;
        if let Some(limit) = max_total
            && total > limit
        {
            return Err(limit_err(
                "total_decompressed_size",
                format!(
                    "streams expand to at least {total} bytes, exceeding the configured cumulative limit of {limit}"
                ),
            ));
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

/// Add to one AcroForm text budget without overflow or partial output.
fn add_form_budget(total: &mut usize, amount: usize, limit: usize, label: &str) -> PyResult<()> {
    *total = total
        .checked_add(amount)
        .ok_or_else(|| PdfError::new_err("AcroForm metadata exceeds the platform size limit"))?;
    if *total > limit {
        return Err(PdfError::new_err(format!(
            "AcroForm {label} text exceeds the {limit}-byte safety limit"
        )));
    }
    Ok(())
}

/// Decode one field-name component while bounding encoded and decoded text.
fn bounded_form_text(
    doc: &Document,
    object: &Object,
    encoded_bytes: &mut usize,
    decoded_bytes: &mut usize,
    limit: usize,
    label: &str,
) -> PyResult<Option<String>> {
    let object = deref_object(doc, object);
    let Object::String(encoded, _) = object else {
        return Ok(None);
    };
    add_form_budget(
        encoded_bytes,
        encoded.len(),
        limit,
        &format!("encoded {label}"),
    )?;
    let Ok(decoded) = decode_text_string(object) else {
        return Ok(None);
    };
    add_form_budget(
        decoded_bytes,
        decoded.len(),
        limit,
        &format!("decoded {label}"),
    )?;
    Ok(Some(decoded))
}

/// Normalize one field value while bounding arrays and all produced text.
fn bounded_form_value(
    doc: &Document,
    object: &Object,
    encoded_bytes: &mut usize,
    decoded_bytes: &mut usize,
    value_items: &mut usize,
) -> PyResult<Option<String>> {
    match deref_object(doc, object) {
        Object::Name(name) => {
            add_form_budget(
                encoded_bytes,
                name.len(),
                MAX_FORM_FIELD_VALUE_BYTES,
                "encoded field-value",
            )?;
            let decoded = String::from_utf8_lossy(name).into_owned();
            add_form_budget(
                decoded_bytes,
                decoded.len(),
                MAX_FORM_FIELD_VALUE_BYTES,
                "decoded field-value",
            )?;
            Ok(Some(decoded))
        }
        object @ Object::String(encoded, _) => {
            add_form_budget(
                encoded_bytes,
                encoded.len(),
                MAX_FORM_FIELD_VALUE_BYTES,
                "encoded field-value",
            )?;
            let Ok(decoded) = decode_text_string(object) else {
                return Ok(None);
            };
            add_form_budget(
                decoded_bytes,
                decoded.len(),
                MAX_FORM_FIELD_VALUE_BYTES,
                "decoded field-value",
            )?;
            Ok(Some(decoded))
        }
        Object::Array(items) => {
            *value_items = value_items.checked_add(items.len()).ok_or_else(|| {
                PdfError::new_err("AcroForm field values exceed the platform size limit")
            })?;
            if *value_items > MAX_FORM_FIELD_VALUE_ITEMS {
                return Err(PdfError::new_err(format!(
                    "AcroForm field values exceed the {MAX_FORM_FIELD_VALUE_ITEMS}-item safety limit"
                )));
            }
            let mut values = Vec::new();
            for item in items {
                let item = deref_object(doc, item);
                let Object::String(encoded, _) = item else {
                    continue;
                };
                add_form_budget(
                    encoded_bytes,
                    encoded.len(),
                    MAX_FORM_FIELD_VALUE_BYTES,
                    "encoded field-value",
                )?;
                let Ok(decoded) = decode_text_string(item) else {
                    continue;
                };
                add_form_budget(
                    decoded_bytes,
                    decoded.len(),
                    MAX_FORM_FIELD_VALUE_BYTES,
                    "decoded field-value",
                )?;
                values.push(decoded);
            }
            if values.len() > 1 {
                add_form_budget(
                    decoded_bytes,
                    (values.len() - 1) * 2,
                    MAX_FORM_FIELD_VALUE_BYTES,
                    "decoded field-value",
                )?;
            }
            Ok(Some(values.join(", ")))
        }
        _ => Ok(None),
    }
}

/// Add to one annotation metadata budget without overflow or partial output.
fn add_annotation_budget(total: &mut usize, amount: usize, label: &str) -> PyResult<()> {
    *total = total
        .checked_add(amount)
        .ok_or_else(|| PdfError::new_err("annotation metadata exceeds the platform size limit"))?;
    if *total > MAX_ANNOTATION_METADATA_BYTES {
        return Err(PdfError::new_err(format!(
            "annotation {label} exceeds the {MAX_ANNOTATION_METADATA_BYTES}-byte safety limit"
        )));
    }
    Ok(())
}

/// Materialize lossy UTF-8 only after charging the encoded annotation bytes.
fn bounded_annotation_bytes(
    bytes: &[u8],
    encoded_bytes: &mut usize,
    returned_bytes: &mut usize,
    label: &str,
) -> PyResult<String> {
    add_annotation_budget(encoded_bytes, bytes.len(), &format!("encoded {label}"))?;
    let text = String::from_utf8_lossy(bytes).into_owned();
    add_annotation_budget(returned_bytes, text.len(), &format!("returned {label}"))?;
    Ok(text)
}

/// Decode a PDF text string under aggregate annotation metadata budgets.
fn bounded_annotation_text(
    doc: &Document,
    object: &Object,
    encoded_bytes: &mut usize,
    returned_bytes: &mut usize,
    label: &str,
) -> PyResult<Option<String>> {
    let object = deref_object(doc, object);
    let Object::String(encoded, _) = object else {
        return Ok(None);
    };
    add_annotation_budget(encoded_bytes, encoded.len(), &format!("encoded {label}"))?;
    let Ok(text) = decode_text_string(object) else {
        return Ok(None);
    };
    add_annotation_budget(returned_bytes, text.len(), &format!("returned {label}"))?;
    Ok(Some(text))
}

/// Decode a FileSpec display name under annotation metadata budgets.
fn bounded_annotation_filespec_name(
    doc: &Document,
    object: &Object,
    encoded_bytes: &mut usize,
    returned_bytes: &mut usize,
) -> PyResult<Option<String>> {
    let object = deref_object(doc, object);
    let name = match object {
        Object::Dictionary(filespec) => filespec.get(b"UF").or_else(|_| filespec.get(b"F")).ok(),
        _ => Some(object),
    };
    match name {
        Some(name) => {
            bounded_annotation_text(doc, name, encoded_bytes, returned_bytes, "file name")
        }
        None => Ok(None),
    }
}

/// Bound caller-supplied annotation text before encoding or PDF mutation.
fn validate_annotation_input<'a>(
    values: impl IntoIterator<Item = &'a [u8]>,
    label: &str,
) -> PyResult<()> {
    let mut total = 0usize;
    for value in values {
        total = total.checked_add(value.len()).ok_or_else(|| {
            limit_err(
                "annotation_input_size",
                "annotation input exceeds the platform size limit",
            )
        })?;
        if total > MAX_ANNOTATION_METADATA_BYTES {
            return Err(limit_err(
                "annotation_input_size",
                format!(
                    "annotation subtype and {label} input exceed the {MAX_ANNOTATION_METADATA_BYTES}-byte UTF-8 safety limit"
                ),
            ));
        }
    }
    Ok(())
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
    hayro_source: HayroSource,
    /// Recently interpreted pages, keyed by one-based page number.
    text_pages: HashMap<u32, crate::extract::TextPage>,
    /// Least-recently-used to most-recently-used text-page keys.
    text_page_order: VecDeque<u32>,
    /// Recently interpreted table pages, keyed by one-based page number.
    table_pages: HashMap<u32, crate::extract::TablePage>,
    /// Least-recently-used to most-recently-used table-page keys.
    table_page_order: VecDeque<u32>,
    /// Configured cumulative Unicode glyph payload across interpreted pages.
    max_text_size: Option<usize>,
    /// Serialized PDF bytes admitted to the renderer/extractor snapshot.
    max_interpretation_size: Option<usize>,
    /// Configured cumulative positioned glyph count across interpreted pages.
    max_text_glyphs: Option<usize>,
    /// UTF-8 glyph payload already admitted for each interpreted page.
    interpreted_text_sizes: HashMap<u32, usize>,
    /// Positioned glyph records already admitted for each interpreted page.
    interpreted_glyph_counts: HashMap<u32, usize>,
    /// Pages whose existing contents were isolated during this document lifetime.
    isolated_content_pages: HashSet<ObjectId>,
    /// Whether loading repaired an incorrect final classic startxref offset.
    is_repaired: bool,
    /// Hayro warnings from the latest render/extraction, written by the
    /// interpreter-settings sink and drained by `take_warnings`.
    pending_warnings: Arc<Mutex<Vec<String>>>,
}

impl _Document {
    /// Construct from lopdf with no fallback fonts configured.
    fn from_doc(
        doc: Document,
        hayro_source: Option<Vec<u8>>,
        max_text_size: Option<usize>,
        max_interpretation_size: Option<usize>,
        max_text_glyphs: Option<usize>,
    ) -> Self {
        Self::from_loaded_doc(
            doc,
            HayroSource::from_optional_owned(hayro_source, max_interpretation_size),
            max_text_size,
            max_interpretation_size,
            max_text_glyphs,
            false,
        )
    }

    /// Construct from a loaded document and retain visible recovery state.
    fn from_loaded_doc(
        doc: Document,
        hayro_source: HayroSource,
        max_text_size: Option<usize>,
        max_interpretation_size: Option<usize>,
        max_text_glyphs: Option<usize>,
        is_repaired: bool,
    ) -> Self {
        let pending = if is_repaired {
            vec![XREF_REPAIR_WARNING.to_owned()]
        } else {
            Vec::new()
        };
        Self {
            doc,
            fallback_fonts: FallbackFonts::default(),
            hayro_pdf: None,
            hayro_source,
            text_pages: HashMap::new(),
            text_page_order: VecDeque::new(),
            table_pages: HashMap::new(),
            table_page_order: VecDeque::new(),
            max_text_size,
            max_interpretation_size,
            max_text_glyphs,
            interpreted_text_sizes: HashMap::new(),
            interpreted_glyph_counts: HashMap::new(),
            isolated_content_pages: HashSet::new(),
            is_repaired,
            pending_warnings: Arc::new(Mutex::new(pending)),
        }
    }

    /// Validate and apply standard Info metadata updates as one atomic batch.
    fn set_metadata_entries(&mut self, entries: Vec<(String, String)>) -> PyResult<()> {
        if entries.is_empty() {
            return Ok(());
        }
        if entries.len() > INFO_METADATA_KEYS.len() {
            return Err(PdfError::new_err(format!(
                "cannot set more than {} standard Info metadata fields",
                INFO_METADATA_KEYS.len()
            )));
        }
        let mut seen = HashSet::new();
        let mut source_bytes = 0usize;
        let mut encoded_bytes = 0usize;
        let mut prepared = Vec::with_capacity(entries.len());
        for (key, value) in entries {
            if !INFO_METADATA_KEYS.contains(&key.as_bytes()) {
                return Err(PdfError::new_err(format!(
                    "unsupported Info metadata key: {key:?}"
                )));
            }
            if !seen.insert(key.clone()) {
                return Err(PdfError::new_err(format!(
                    "duplicate Info metadata key: {key:?}"
                )));
            }
            add_input_text_budget(
                &mut source_bytes,
                value.len(),
                MAX_INFO_METADATA_TEXT_BYTES,
                "metadata_input_size",
                "Info metadata source text",
            )?;
            let encoded = if value.is_empty() {
                None
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
                Some(text_string(&value))
            };
            prepared.push((key.into_bytes(), encoded));
        }

        let existing_id = match self.doc.trailer.get(b"Info") {
            Ok(Object::Reference(id)) => {
                self.doc
                    .get_object(*id)
                    .and_then(Object::as_dict)
                    .map_err(to_py_err)?;
                Some(*id)
            }
            _ => None,
        };
        if existing_id.is_none() {
            self.doc
                .max_id
                .checked_add(1)
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        }

        self.invalidate_hayro_pdf();
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

    /// Serialize current edit state to bytes for rendering.
    fn current_bytes(&mut self) -> PyResult<Vec<u8>> {
        serialize_pdf_with_limit(
            &mut self.doc,
            None,
            self.max_interpretation_size,
            "interpretation_size",
            "serialized rendering and extraction snapshot",
        )
    }

    /// Reject a retained source before the renderer or extractor parses it.
    fn validate_interpretation_source(&self, data: &[u8]) -> PyResult<()> {
        validate_interpretation_limit(self.max_interpretation_size)?;
        if let Some(limit) = self.max_interpretation_size
            && data.len() > limit
        {
            return Err(limit_err(
                "interpretation_size",
                format!(
                    "rendering and extraction source is {} bytes, exceeding the configured limit of {limit}",
                    data.len()
                ),
            ));
        }
        Ok(())
    }

    /// Drop cached views; call at the start of every editing method.
    fn invalidate_hayro_pdf(&mut self) {
        self.hayro_pdf = None;
        self.hayro_source = HayroSource::Unavailable;
        self.invalidate_interpreted_pages();
    }

    /// Drop derived text/table pages while retaining the hayro snapshot.
    fn invalidate_interpreted_pages(&mut self) {
        self.text_pages.clear();
        self.text_page_order.clear();
        self.table_pages.clear();
        self.table_page_order.clear();
        self.interpreted_text_sizes.clear();
        self.interpreted_glyph_counts.clear();
    }

    /// Return the glyph payload still available to one page interpretation.
    fn text_budget(&self, page_number: u32) -> PyResult<Option<usize>> {
        let Some(limit) = self.max_text_size else {
            return Ok(None);
        };
        if let Some(admitted) = self.interpreted_text_sizes.get(&page_number) {
            return Ok(Some(*admitted));
        }
        let used = self
            .interpreted_text_sizes
            .values()
            .try_fold(0usize, |total, value| total.checked_add(*value))
            .ok_or_else(|| {
                limit_err(
                    "text_size",
                    "interpreted text size exceeds the platform limit",
                )
            })?;
        let remaining = limit.saturating_sub(used);
        Ok(Some(remaining))
    }

    /// Return the positioned glyph records still available to one page.
    fn glyph_budget(&self, page_number: u32) -> PyResult<Option<usize>> {
        let Some(limit) = self.max_text_glyphs else {
            return Ok(None);
        };
        if let Some(admitted) = self.interpreted_glyph_counts.get(&page_number) {
            return Ok(Some(*admitted));
        }
        let used = self
            .interpreted_glyph_counts
            .values()
            .try_fold(0usize, |total, value| total.checked_add(*value))
            .ok_or_else(|| {
                limit_err(
                    "text_glyph_count",
                    "interpreted text glyph count exceeds the platform limit",
                )
            })?;
        Ok(Some(limit.saturating_sub(used)))
    }

    /// Record one page's text resources without charging cache re-interpretation.
    fn admit_text_usage(
        &mut self,
        page_number: u32,
        text_size: usize,
        glyph_count: usize,
    ) -> PyResult<()> {
        if self.interpreted_text_sizes.contains_key(&page_number) {
            debug_assert!(self.interpreted_glyph_counts.contains_key(&page_number));
            return Ok(());
        }
        debug_assert!(!self.interpreted_glyph_counts.contains_key(&page_number));
        if let Some(limit) = self.max_text_size {
            let used = self
                .interpreted_text_sizes
                .values()
                .try_fold(text_size, |total, value| total.checked_add(*value))
                .ok_or_else(|| {
                    limit_err(
                        "text_size",
                        "interpreted text size exceeds the platform limit",
                    )
                })?;
            if used > limit {
                return Err(limit_err(
                    "text_size",
                    format!(
                        "interpreted text reached {used} bytes, exceeding the configured cumulative limit of {limit}"
                    ),
                ));
            }
        }
        if let Some(limit) = self.max_text_glyphs {
            let used = self
                .interpreted_glyph_counts
                .values()
                .try_fold(glyph_count, |total, value| total.checked_add(*value))
                .ok_or_else(|| {
                    limit_err(
                        "text_glyph_count",
                        "interpreted text glyph count exceeds the platform limit",
                    )
                })?;
            if used > limit {
                return Err(limit_err(
                    "text_glyph_count",
                    format!(
                        "interpreted text reached {used} glyphs, exceeding the configured cumulative limit of {limit}"
                    ),
                ));
            }
        }
        self.interpreted_text_sizes.insert(page_number, text_size);
        self.interpreted_glyph_counts
            .insert(page_number, glyph_count);
        Ok(())
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

    /// Validate one drawing insertion before caches or PDF objects are changed.
    fn preflight_page_content(&self, page_number: u32) -> PyResult<ObjectId> {
        let page_id = self.page_id(page_number)?;
        draw::preflight_push_content(
            &self.doc,
            page_id,
            self.isolated_content_pages.contains(&page_id),
        )
        .map_err(PdfError::new_err)?;
        Ok(page_id)
    }

    /// Append one prepared stream and retain verified in-memory isolation state.
    fn push_page_content(
        &mut self,
        page_id: ObjectId,
        ops: Vec<u8>,
        overlay: bool,
    ) -> PyResult<()> {
        let is_isolated = draw::push_content(
            &mut self.doc,
            page_id,
            ops,
            overlay,
            self.isolated_content_pages.contains(&page_id),
        )
        .map_err(to_py_err)?;
        if is_isolated {
            self.isolated_content_pages.insert(page_id);
        }
        Ok(())
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

    /// Import and place a page from an owned source snapshot.
    fn place_pdf_page(
        &mut self,
        page_id: ObjectId,
        target_geometry: ([f64; 4], i64),
        source: Document,
        src_page_number: u32,
        placement: PagePlacement,
    ) -> PyResult<()> {
        draw::preflight_push_content(
            &self.doc,
            page_id,
            self.isolated_content_pages.contains(&page_id),
        )
        .map_err(PdfError::new_err)?;
        let (form_id, src_crop, src_rotation) =
            import_page_as_form(&mut self.doc, source, src_page_number)?;
        let content = draw::PlacedContent::Form {
            crop: src_crop,
            rotation: src_rotation,
        };
        let matrix = draw::placement_matrix(
            target_geometry.0,
            target_geometry.1,
            placement.rect,
            &content,
            placement.keep_proportion,
        );
        self.bake_page_attrs(page_id)?;
        let name = format!("PyloFm{}", form_id.0);
        self.doc
            .add_xobject(page_id, name.as_bytes(), form_id)
            .map_err(to_py_err)?;
        self.push_page_content(page_id, draw::draw_ops(matrix, &name), placement.overlay)?;
        // All source non-page objects were moved initially; prune assets and
        // attachments unreachable from the Form XObject.
        self.doc.prune_objects();
        Ok(())
    }

    /// Flatten AcroForm fields into `(full name, ObjectId, FT, Ff, V)`.
    ///
    /// FT/Ff/V inherit from parents; full names join `/T` components with dots.
    /// A leaf has resolved FT and no child carrying `/T`. Traversal and returned
    /// text are bounded so malformed trees cannot produce partial results.
    fn collect_form_fields(&self) -> PyResult<Vec<FormFieldEntry>> {
        let Some(acroform_object) = self
            .doc
            .catalog()
            .ok()
            .and_then(|catalog| catalog.get(b"AcroForm").ok())
        else {
            return Ok(Vec::new());
        };
        let Ok(acroform) = deref_object(&self.doc, acroform_object).as_dict() else {
            return Ok(Vec::new());
        };
        let Ok(fields_object) = acroform.get(b"Fields") else {
            return Ok(Vec::new());
        };
        let Ok(fields) = deref_object(&self.doc, fields_object).as_array() else {
            return Ok(Vec::new());
        };
        if fields.len() > MAX_FORM_FIELD_TREE_EDGES {
            return Err(PdfError::new_err(format!(
                "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_EDGES}-edge safety limit"
            )));
        }

        let mut out = Vec::new();
        let mut stack: Vec<FieldNode> = fields
            .iter()
            .filter_map(|field| field.as_reference().ok())
            .map(|id| (id, String::new(), None, 0, None, 1))
            .collect();
        let mut visited = HashSet::new();
        let mut edges = fields.len();
        let mut widget_refs = 0usize;
        let mut encoded_name_bytes = 0usize;
        let mut decoded_name_bytes = 0usize;
        let mut materialized_name_bytes = 0usize;
        let mut encoded_value_bytes = 0usize;
        let mut decoded_value_bytes = 0usize;
        let mut returned_value_bytes = 0usize;
        let mut value_items = 0usize;
        while let Some((id, prefix, inh_ft, inh_ff, inh_v, depth)) = stack.pop() {
            if !visited.insert(id) {
                continue;
            }
            if visited.len() > MAX_FORM_FIELD_TREE_NODES {
                return Err(PdfError::new_err(format!(
                    "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_NODES}-node safety limit"
                )));
            }
            let Ok(dict) = self.doc.get_object(id).and_then(Object::as_dict) else {
                continue;
            };
            let component = match dict.get(b"T") {
                Ok(object) => bounded_form_text(
                    &self.doc,
                    object,
                    &mut encoded_name_bytes,
                    &mut decoded_name_bytes,
                    MAX_FORM_FIELD_NAME_BYTES,
                    "field-name",
                )?,
                Err(_) => None,
            };
            let name = match component {
                Some(component) if prefix.is_empty() => component,
                Some(component) => format!("{prefix}.{component}"),
                None => prefix.clone(),
            };
            add_form_budget(
                &mut materialized_name_bytes,
                name.len(),
                MAX_FORM_FIELD_NAME_BYTES,
                "materialized field-name",
            )?;
            let ft = match dict.get(b"FT") {
                Ok(object) => {
                    let object = deref_object(&self.doc, object);
                    match object.as_name() {
                        Ok(name) => {
                            add_form_budget(
                                &mut encoded_name_bytes,
                                name.len(),
                                MAX_FORM_FIELD_NAME_BYTES,
                                "encoded field-name/type",
                            )?;
                            let decoded = String::from_utf8_lossy(name).into_owned();
                            add_form_budget(
                                &mut decoded_name_bytes,
                                decoded.len(),
                                MAX_FORM_FIELD_NAME_BYTES,
                                "decoded field-name/type",
                            )?;
                            Some(decoded)
                        }
                        Err(_) => inh_ft,
                    }
                }
                Err(_) => inh_ft,
            };
            let ff = dict
                .get(b"Ff")
                .ok()
                .and_then(|object| resolve_i64(&self.doc, object))
                .unwrap_or(inh_ff);
            let value = match dict.get(b"V") {
                Ok(object) => bounded_form_value(
                    &self.doc,
                    object,
                    &mut encoded_value_bytes,
                    &mut decoded_value_bytes,
                    &mut value_items,
                )?
                .map(Arc::from),
                Err(_) => inh_v,
            };

            let mut has_child_fields = false;
            if let Ok(kids_object) = dict.get(b"Kids")
                && let Ok(kids) = deref_object(&self.doc, kids_object).as_array()
            {
                edges = edges.checked_add(kids.len()).ok_or_else(|| {
                    PdfError::new_err("AcroForm field tree exceeds the platform size limit")
                })?;
                if edges > MAX_FORM_FIELD_TREE_EDGES {
                    return Err(PdfError::new_err(format!(
                        "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_EDGES}-edge safety limit"
                    )));
                }
                for kid in kids {
                    let Ok(kid_id) = kid.as_reference() else {
                        continue;
                    };
                    let is_field = self
                        .doc
                        .get_object(kid_id)
                        .and_then(Object::as_dict)
                        .is_ok_and(|child| child.has(b"T"));
                    if !is_field {
                        widget_refs = widget_refs.saturating_add(1);
                        if widget_refs > MAX_FORM_FIELD_WIDGETS {
                            return Err(PdfError::new_err(format!(
                                "AcroForm field tree exceeds the {MAX_FORM_FIELD_WIDGETS}-widget safety limit"
                            )));
                        }
                        continue;
                    }
                    if depth >= MAX_FORM_FIELD_TREE_DEPTH {
                        return Err(PdfError::new_err(format!(
                            "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_DEPTH}-level safety limit"
                        )));
                    }
                    has_child_fields = true;
                    stack.push((
                        kid_id,
                        name.clone(),
                        ft.clone(),
                        ff,
                        value.clone(),
                        depth + 1,
                    ));
                }
            }
            if !has_child_fields && let Some(ft) = ft {
                if out.len() >= MAX_FORM_FIELD_ENTRIES {
                    return Err(PdfError::new_err(format!(
                        "AcroForm field tree exceeds the {MAX_FORM_FIELD_ENTRIES}-entry safety limit"
                    )));
                }
                if let Some(value) = &value {
                    add_form_budget(
                        &mut returned_value_bytes,
                        value.len(),
                        MAX_FORM_FIELD_VALUE_BYTES,
                        "returned field-value",
                    )?;
                }
                out.push((name, id, ft, ff, value));
            }
        }
        out.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(out)
    }

    /// Return widget annotation ObjectIds, or the field itself when Kids is absent.
    fn field_widgets(&self, field_id: ObjectId) -> Vec<ObjectId> {
        let Ok(dict) = self.doc.get_object(field_id).and_then(Object::as_dict) else {
            return vec![field_id];
        };
        let widgets: Vec<ObjectId> = dict
            .get(b"Kids")
            .ok()
            .map(|object| deref_object(&self.doc, object))
            .and_then(|object| object.as_array().ok())
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

    fn visible_field_widgets(&self, field_id: ObjectId) -> Vec<ObjectId> {
        self.field_widgets(field_id)
            .into_iter()
            .filter(|widget_id| {
                self.doc
                    .get_object(*widget_id)
                    .and_then(Object::as_dict)
                    .is_ok_and(|widget| {
                        widget
                            .get(b"Subtype")
                            .and_then(Object::as_name)
                            .is_ok_and(|name| name == b"Widget")
                            || widget.has(b"Rect")
                    })
            })
            .collect()
    }

    /// Borrow and bound every normal-appearance state name for one button field.
    fn button_appearance_states(
        &self,
        field_id: ObjectId,
    ) -> PyResult<(Vec<WidgetStateNames>, usize, usize)> {
        let widgets = self.visible_field_widgets(field_id);
        if widgets.len() > MAX_FORM_FIELD_WIDGETS {
            return Err(PdfError::new_err(format!(
                "AcroForm field exceeds the {MAX_FORM_FIELD_WIDGETS}-widget safety limit"
            )));
        }
        let mut result = Vec::with_capacity(widgets.len());
        let mut entries = 0usize;
        let mut encoded_name_bytes = 0usize;
        for widget_id in widgets {
            let normal = self
                .doc
                .get_object(widget_id)
                .and_then(Object::as_dict)
                .ok()
                .and_then(|widget| widget.get(b"AP").ok())
                .and_then(|object| deref_object(&self.doc, object).as_dict().ok())
                .and_then(|appearance| appearance.get(b"N").ok())
                .and_then(|object| deref_object(&self.doc, object).as_dict().ok());
            let mut states = Vec::new();
            if let Some(normal) = normal {
                entries = entries.checked_add(normal.len()).ok_or_else(|| {
                    PdfError::new_err("AcroForm button states exceed the platform size limit")
                })?;
                if entries > MAX_FORM_BUTTON_STATE_ENTRIES {
                    return Err(PdfError::new_err(format!(
                        "AcroForm button states exceed the {MAX_FORM_BUTTON_STATE_ENTRIES}-entry safety limit"
                    )));
                }
                states.reserve(normal.len());
                for (state, _) in normal {
                    add_form_budget(
                        &mut encoded_name_bytes,
                        state.len(),
                        MAX_FORM_BUTTON_STATE_NAME_BYTES,
                        "encoded button-state name",
                    )?;
                    states.push(state.clone());
                }
            }
            result.push((widget_id, states));
        }
        Ok((result, entries, encoded_name_bytes))
    }

    /// Set AcroForm NeedAppearances, including indirect dictionaries.
    fn set_need_appearances(&mut self, value: bool) -> PyResult<()> {
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
                acroform.set("NeedAppearances", value);
            }
            None => {
                let catalog = self.doc.catalog_mut().map_err(to_py_err)?;
                let acroform = catalog
                    .get_mut(b"AcroForm")
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                acroform.set("NeedAppearances", value);
            }
        }
        Ok(())
    }

    /// Read an inheritable field attribute from a field/widget parent chain.
    fn resolve_field_attr(&self, field_id: ObjectId, key: &[u8]) -> Option<Object> {
        let mut current = Some(field_id);
        let mut visited = HashSet::new();
        while let Some(id) = current {
            if !visited.insert(id) || visited.len() > 64 {
                return None;
            }
            let dict = self.doc.get_object(id).ok()?.as_dict().ok()?;
            if let Ok(value) = dict.get(key) {
                return Some(value.clone());
            }
            current = dict.get(b"Parent").and_then(Object::as_reference).ok();
        }
        None
    }

    /// Resolve a valid text-field comb size or explain malformed flag data.
    fn field_comb_max_len(&self, field_id: ObjectId, flags: i64) -> Result<Option<usize>, String> {
        if flags & TEXT_FIELD_COMB == 0 {
            return Ok(None);
        }
        if flags & (TEXT_FIELD_MULTILINE | TEXT_FIELD_PASSWORD | TEXT_FIELD_FILE_SELECT) != 0 {
            return Err(
                "comb fields cannot also be multiline, password, or file-select fields".to_owned(),
            );
        }
        let max_len = self
            .resolve_field_attr(field_id, b"MaxLen")
            .as_ref()
            .and_then(|object| resolve_i64(&self.doc, object))
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| *value > 0)
            .ok_or_else(|| "comb field requires a positive MaxLen".to_owned())?;
        Ok(Some(max_len))
    }

    /// Clone a widget AP dictionary, update/remove `/N`, and write it locally.
    fn set_widget_normal_appearance(
        &mut self,
        widget_id: ObjectId,
        normal: Option<Object>,
    ) -> PyResult<()> {
        let mut appearance = self
            .doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .ok()
            .and_then(|widget| widget.get(b"AP").ok())
            .and_then(|object| deref_dict(&self.doc, object))
            .unwrap_or_default();
        if let Some(normal) = normal {
            appearance.set("N", normal);
        } else {
            appearance.remove(b"N");
        }
        let widget = self
            .doc
            .get_object_mut(widget_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        if appearance.is_empty() {
            widget.remove(b"AP");
        } else {
            widget.set("AP", Object::Dictionary(appearance));
        }
        Ok(())
    }

    /// Return the normal-appearance state dictionary for a button widget.
    fn button_normal_appearance(&self, widget_id: ObjectId) -> Dictionary {
        self.doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .ok()
            .and_then(|widget| widget.get(b"AP").ok())
            .and_then(|object| deref_dict(&self.doc, object))
            .and_then(|appearance| {
                appearance
                    .get(b"N")
                    .ok()
                    .and_then(|object| deref_dict(&self.doc, object))
            })
            .unwrap_or_default()
    }

    /// Treat a non-empty stream as an authored appearance worth preserving.
    fn appearance_has_content(&self, appearance: &Object) -> bool {
        let resolved = match appearance {
            Object::Reference(id) => self.doc.get_object(*id).ok(),
            other => Some(other),
        };
        resolved
            .and_then(|object| object.as_stream().ok())
            .is_some_and(|stream| !stream.content.is_empty())
    }

    fn widget_has_normal_stream(&self, widget_id: ObjectId) -> bool {
        self.doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .ok()
            .and_then(|widget| widget.get(b"AP").ok())
            .and_then(|appearance| deref_dict(&self.doc, appearance))
            .and_then(|appearance| appearance.get(b"N").ok().cloned())
            .is_some_and(|normal| self.appearance_has_content(&normal))
    }

    /// Ensure a widget has usable Off and selected-state vector appearances.
    fn synthesize_button_appearance(
        &mut self,
        widget_id: ObjectId,
        state: &str,
        radio: bool,
    ) -> PyResult<()> {
        let widget = self
            .doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?
            .clone();
        let style =
            form::WidgetStyle::from_widget(&self.doc, &widget).map_err(PdfError::new_err)?;
        let mut normal = self.button_normal_appearance(widget_id);
        for (name, on) in [("Off", false), (state, state != "Off")] {
            let keep_existing = normal
                .get(name.as_bytes())
                .ok()
                .is_some_and(|appearance| self.appearance_has_content(appearance));
            if !keep_existing {
                let appearance_id = self.doc.add_object(style.button_stream(on, radio));
                normal.set(name, Object::Reference(appearance_id));
            }
        }
        self.set_widget_normal_appearance(widget_id, Some(Object::Dictionary(normal)))
    }

    fn sync_button_widget_appearances(
        &mut self,
        field_id: ObjectId,
        flags: i64,
        value: &str,
    ) -> PyResult<()> {
        let (widget_states, mut planned_entries, mut planned_name_bytes) =
            self.button_appearance_states(field_id)?;
        let requested_is_known = widget_states
            .iter()
            .any(|(_, states)| states.iter().any(|state| state == value.as_bytes()));
        for (index, (_, states)) in widget_states.iter().enumerate() {
            let selected = value != "Off"
                && (states.iter().any(|state| state == value.as_bytes())
                    || (!requested_is_known && index == 0));
            let selected_state = if selected { value } else { "Off" };
            for (position, planned_state) in ["Off", selected_state].into_iter().enumerate() {
                if position == 1 && selected_state == "Off" {
                    // The duplicate name in this pair never creates a second key.
                    continue;
                }
                if states.iter().any(|state| state == planned_state.as_bytes()) {
                    continue;
                }
                planned_entries = planned_entries.checked_add(1).ok_or_else(|| {
                    PdfError::new_err("AcroForm button states exceed the platform size limit")
                })?;
                if planned_entries > MAX_FORM_BUTTON_STATE_ENTRIES {
                    return Err(PdfError::new_err(format!(
                        "AcroForm button state update exceeds the {MAX_FORM_BUTTON_STATE_ENTRIES}-entry safety limit"
                    )));
                }
                add_form_budget(
                    &mut planned_name_bytes,
                    planned_state.len(),
                    MAX_FORM_BUTTON_STATE_NAME_BYTES,
                    "encoded button-state name",
                )?;
            }
        }
        let radio = flags & (1 << 15) != 0;
        for (index, (widget_id, states)) in widget_states.into_iter().enumerate() {
            let selected = value != "Off"
                && (states.iter().any(|state| state == value.as_bytes())
                    || (!requested_is_known && index == 0));
            let state = if selected { value } else { "Off" };
            self.synthesize_button_appearance(widget_id, state, radio)?;
            let widget = self
                .doc
                .get_object_mut(widget_id)
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            widget.set("AS", Object::Name(state.as_bytes().to_vec()));
        }
        Ok(())
    }

    /// Generate a text/choice widget appearance, preserving AP down/rollover data.
    fn synthesize_text_appearance(
        &mut self,
        widget_id: ObjectId,
        value: &str,
        layout: WidgetTextLayout,
        align: u8,
        font_data: Option<&Vec<u8>>,
        font_index: u32,
    ) -> PyResult<bool> {
        let widget = self
            .doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?
            .clone();
        let style =
            form::WidgetStyle::from_widget(&self.doc, &widget).map_err(PdfError::new_err)?;
        if let WidgetTextLayout::Comb(max_len) = layout {
            form::validate_comb_text(value, max_len).map_err(PdfError::new_err)?;
        }

        if font_data.is_none() && !draw::is_winansi(value) {
            // Preserve the value and NeedAppearances compatibility behavior when
            // no embeddable font is available, but never leave a stale value AP.
            self.set_widget_normal_appearance(widget_id, None)?;
            return Ok(false);
        }

        let (resources, text_ops) = match font_data {
            Some(font_data) if !value.is_empty() => {
                let generated = match layout {
                    WidgetTextLayout::Comb(max_len) => generate::embedded_widget_comb_text_page(
                        (style.layout_width, style.layout_height),
                        style.content_rect(),
                        value,
                        max_len,
                        align,
                        font_data.clone(),
                        font_index,
                        (0.0, 0.0, 0.0),
                    ),
                    _ => generate::embedded_widget_text_page(
                        (style.layout_width, style.layout_height),
                        style.content_rect(),
                        value,
                        font_data.clone(),
                        font_index,
                        matches!(layout, WidgetTextLayout::Multiline),
                        align,
                        (0.0, 0.0, 0.0),
                    ),
                }
                .map_err(generated_text_err)?;
                let generated_doc = Document::load_mem(&generated).map_err(|error| {
                    lopdf_err(Some("failed to import generated form appearance"), &error)
                })?;
                let (form_id, crop, rotation) =
                    import_page_as_form(&mut self.doc, generated_doc, 1)?;
                let name = format!("PyloTx{}", form_id.0);
                let content = draw::PlacedContent::Form { crop, rotation };
                let matrix = draw::placement_matrix(
                    [0.0, 0.0, style.layout_width, style.layout_height],
                    0,
                    [0.0, 0.0, style.layout_width, style.layout_height],
                    &content,
                    false,
                );
                let resources = dictionary! {
                    "XObject" => dictionary! {
                        name.as_bytes() => Object::Reference(form_id),
                    },
                };
                (Some(resources), draw::draw_ops(matrix, &name))
            }
            Some(_) => (None, Vec::new()),
            None => {
                let text_ops = match layout {
                    WidgetTextLayout::Comb(max_len) => {
                        form::standard_comb_text_ops(&style, value, max_len, align, "Helv")
                    }
                    _ => form::standard_text_ops(
                        &style,
                        value,
                        matches!(layout, WidgetTextLayout::Multiline),
                        align,
                        "Helv",
                    ),
                }
                .map_err(generated_text_err)?;
                if text_ops.is_empty() {
                    (None, text_ops)
                } else {
                    let font_id = self.doc.add_object(dictionary! {
                        "Type" => "Font",
                        "Subtype" => "Type1",
                        "BaseFont" => "Helvetica",
                        "Encoding" => "WinAnsiEncoding",
                    });
                    let resources = dictionary! {
                        "Font" => dictionary! {
                            "Helv" => Object::Reference(font_id),
                        },
                    };
                    (Some(resources), text_ops)
                }
            }
        };
        let content = style.decorated_text_ops(&text_ops);
        let appearance_id = self.doc.add_object(style.stream(resources, content));
        self.set_widget_normal_appearance(widget_id, Some(Object::Reference(appearance_id)))?;
        Ok(true)
    }

    /// Fill missing appearances on untouched fields when their current values
    /// can be represented without guessing a font.
    fn synthesize_missing_form_appearances(&mut self, target_name: &str) -> PyResult<()> {
        for (name, field_id, field_type, flags, value) in self.collect_form_fields()? {
            if name == target_name {
                continue;
            }
            match field_type.as_str() {
                "Tx" | "Ch" => {
                    let value = value.as_deref().map(str::to_owned).unwrap_or_default();
                    if !draw::is_winansi(&value) {
                        continue;
                    }
                    let layout = if field_type == "Tx" {
                        match self.field_comb_max_len(field_id, flags) {
                            Ok(Some(max_len)) => WidgetTextLayout::Comb(max_len),
                            Ok(None) if flags & TEXT_FIELD_MULTILINE != 0 => {
                                WidgetTextLayout::Multiline
                            }
                            Ok(None) => WidgetTextLayout::SingleLine,
                            Err(_) => continue,
                        }
                    } else {
                        WidgetTextLayout::SingleLine
                    };
                    let align = self
                        .resolve_field_attr(field_id, b"Q")
                        .as_ref()
                        .and_then(|object| resolve_i64(&self.doc, object))
                        .and_then(|value| u8::try_from(value).ok())
                        .filter(|value| *value <= 2)
                        .unwrap_or(0);
                    for widget_id in self.visible_field_widgets(field_id) {
                        if !self.widget_has_normal_stream(widget_id)
                            && self
                                .synthesize_text_appearance(
                                    widget_id, &value, layout, align, None, 0,
                                )
                                .is_err()
                        {
                            continue;
                        }
                    }
                }
                "Btn" if flags & (1 << 16) == 0 => {
                    let state = value
                        .as_deref()
                        .map(str::to_owned)
                        .unwrap_or_else(|| "Off".to_owned());
                    if self
                        .sync_button_widget_appearances(field_id, flags, &state)
                        .is_err()
                    {
                        continue;
                    }
                }
                _ => {}
            }
        }
        Ok(())
    }

    fn form_appearances_complete(&self) -> PyResult<bool> {
        Ok(self
            .collect_form_fields()?
            .into_iter()
            .all(|(_, field_id, field_type, flags, _)| {
                let widgets = self.visible_field_widgets(field_id);
                match field_type.as_str() {
                    "Tx" | "Ch" => widgets
                        .into_iter()
                        .all(|widget_id| self.widget_has_normal_stream(widget_id)),
                    "Btn" if flags & (1 << 16) == 0 => widgets.into_iter().all(|widget_id| {
                        let Ok(widget) = self.doc.get_object(widget_id).and_then(Object::as_dict)
                        else {
                            return false;
                        };
                        let Some(state) = widget.get(b"AS").and_then(Object::as_name).ok() else {
                            return false;
                        };
                        self.button_normal_appearance(widget_id)
                            .get(state)
                            .ok()
                            .is_some_and(|appearance| self.appearance_has_content(appearance))
                    }),
                    _ => true,
                }
            }))
    }

    /// Mutating implementation kept behind an atomic clone in the Python method.
    fn set_form_field_inner(
        &mut self,
        name: &str,
        value: &str,
        font_data: Option<Vec<u8>>,
        font_index: u32,
    ) -> PyResult<()> {
        let (field_id, ft, ff) = self
            .collect_form_fields()?
            .into_iter()
            .find(|(field_name, ..)| field_name == name)
            .map(|(_, id, ft, ff, _)| (id, ft, ff))
            .ok_or_else(|| PdfError::new_err(format!("form field not found: {name:?}")))?;
        match ft.as_str() {
            "Tx" | "Ch" => {
                {
                    let field = self
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                        .map_err(to_py_err)?;
                    field.set("V", form_text_string(value));
                }
                let layout = if ft == "Tx" {
                    match self
                        .field_comb_max_len(field_id, ff)
                        .map_err(PdfError::new_err)?
                    {
                        Some(max_len) => WidgetTextLayout::Comb(max_len),
                        None if ff & TEXT_FIELD_MULTILINE != 0 => WidgetTextLayout::Multiline,
                        None => WidgetTextLayout::SingleLine,
                    }
                } else {
                    WidgetTextLayout::SingleLine
                };
                let align = self
                    .resolve_field_attr(field_id, b"Q")
                    .as_ref()
                    .and_then(|object| resolve_i64(&self.doc, object))
                    .and_then(|value| u8::try_from(value).ok())
                    .filter(|value| *value <= 2)
                    .unwrap_or(0);
                for widget_id in self.visible_field_widgets(field_id) {
                    self.synthesize_text_appearance(
                        widget_id,
                        value,
                        layout,
                        align,
                        font_data.as_ref(),
                        font_index,
                    )?;
                }
            }
            "Btn" if ff & (1 << 16) != 0 => {
                return Err(PdfError::new_err(
                    "pushbutton fields do not have fillable values",
                ));
            }
            "Btn" => {
                {
                    let field = self
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                        .map_err(to_py_err)?;
                    field.set("V", Object::Name(value.as_bytes().to_vec()));
                }
                self.sync_button_widget_appearances(field_id, ff, value)?;
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
        self.synthesize_missing_form_appearances(name)?;
        self.set_need_appearances(!self.form_appearances_complete()?)
    }

    /// Borrow a page annotation array and reject partial reads above the cap.
    fn page_annotation_items(&self, page_id: ObjectId) -> PyResult<Option<&[Object]>> {
        let page = self
            .doc
            .get_object(page_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?;
        let annots = match page.get(b"Annots") {
            Ok(Object::Reference(id)) => self
                .doc
                .get_object(*id)
                .and_then(Object::as_array)
                .ok()
                .map(Vec::as_slice),
            Ok(Object::Array(items)) => Some(items.as_slice()),
            _ => None,
        };
        if annots.is_some_and(|items| items.len() > MAX_PAGE_ANNOTATIONS) {
            return Err(PdfError::new_err(format!(
                "page annotations exceed the {MAX_PAGE_ANNOTATIONS}-entry safety limit"
            )));
        }
        Ok(annots)
    }

    /// Preflight an annotation append before adding any dependent objects.
    fn ensure_page_annotation_capacity(
        &self,
        page_id: ObjectId,
        additional: usize,
    ) -> PyResult<()> {
        let page = self
            .doc
            .get_object(page_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?;
        let existing = match page.get(b"Annots") {
            Ok(Object::Reference(id)) => self
                .doc
                .get_object(*id)
                .and_then(Object::as_array)
                .map_err(to_py_err)?
                .len(),
            Ok(Object::Array(items)) => items.len(),
            _ => 0,
        };
        let total = existing
            .checked_add(additional)
            .ok_or_else(|| PdfError::new_err("page annotations exceed the platform size limit"))?;
        if total > MAX_PAGE_ANNOTATIONS {
            return Err(PdfError::new_err(format!(
                "page annotations exceed the {MAX_PAGE_ANNOTATIONS}-entry safety limit"
            )));
        }
        Ok(())
    }

    /// Add an annotation reference to page `/Annots`, including indirect arrays.
    fn push_page_annotation(&mut self, page_id: ObjectId, annot_id: ObjectId) -> PyResult<()> {
        self.ensure_page_annotation_capacity(page_id, 1)?;
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
            let prepare_appearances =
                has_state_appearances(&self.doc) || has_missing_text_markup_appearances(&self.doc);
            if !prepare_appearances {
                match &self.hayro_source {
                    HayroSource::TooLarge { actual, limit } => {
                        return Err(limit_err(
                            "interpretation_size",
                            format!(
                                "rendering and extraction source is {actual} bytes, exceeding the configured limit of {limit}"
                            ),
                        ));
                    }
                    HayroSource::Bytes(data) => {
                        self.validate_interpretation_source(data)?;
                    }
                    HayroSource::Unavailable => {}
                }
            }
            let source_pdf = (!prepare_appearances)
                .then(|| self.hayro_source.take_bytes())
                .flatten()
                .and_then(|data| Pdf::new(data).ok())
                .filter(|pdf| pdf.pages().len() == expected_pages);
            let pdf = match source_pdf {
                Some(pdf) => pdf,
                None => {
                    let mut render_doc = if prepare_appearances {
                        let mut doc = if self.max_interpretation_size.is_some() {
                            let data = self.current_bytes()?;
                            Document::load_mem(&data).map_err(|error| {
                                PdfError::new_err(format!(
                                    "failed to prepare bounded PDF for rendering: {error}"
                                ))
                            })?
                        } else {
                            self.doc.clone()
                        };
                        normalize_state_appearances_for_render(&mut doc);
                        synthesize_missing_text_markup_appearances_for_render(&mut doc);
                        Some(doc)
                    } else {
                        None
                    };
                    let data = match render_doc.as_mut() {
                        Some(doc) => serialize_pdf_with_limit(
                            doc,
                            None,
                            self.max_interpretation_size,
                            "interpretation_size",
                            "serialized rendering and extraction snapshot",
                        )?,
                        None => self.current_bytes()?,
                    };
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

        let text_budget = self.text_budget(page_number)?;
        let glyph_budget = self.glyph_budget(page_number)?;
        let text_page = {
            let pdf = self.hayro_view()?;
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            crate::extract::TextPage::new(pdf, page, settings, text_budget, glyph_budget)
                .map_err(text_page_limit_err)?
        };
        self.admit_text_usage(page_number, text_page.text_size(), text_page.glyph_count())?;

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

    /// Return a cached, owned table interpretation of a one-based page.
    fn table_page(
        &mut self,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<&crate::extract::TablePage> {
        if self.table_pages.contains_key(&page_number) {
            self.table_page_order
                .retain(|number| *number != page_number);
            self.table_page_order.push_back(page_number);
            return Ok(self
                .table_pages
                .get(&page_number)
                .expect("cache key was checked immediately before"));
        }

        let text_budget = self.text_budget(page_number)?;
        let glyph_budget = self.glyph_budget(page_number)?;
        let table_page = {
            let pdf = self.hayro_view()?;
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            crate::extract::TablePage::new(pdf, page, settings, text_budget, glyph_budget)
                .map_err(text_page_limit_err)?
        };
        self.admit_text_usage(
            page_number,
            table_page.text_size(),
            table_page.glyph_count(),
        )?;

        if self.table_pages.len() >= TABLE_PAGE_CACHE_CAPACITY
            && let Some(evicted) = self.table_page_order.pop_front()
        {
            self.table_pages.remove(&evicted);
        }
        self.table_pages.insert(page_number, table_page);
        self.table_page_order.push_back(page_number);
        Ok(self
            .table_pages
            .get(&page_number)
            .expect("table page was inserted immediately before"))
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
        let interpreter_settings = self.interpreter_settings();
        py.detach(|| {
            let pdf = self.hayro_view()?;
            let cache = RenderCache::new();
            render_pdf_page(
                pdf,
                &cache,
                &interpreter_settings,
                page_number,
                scale,
                background,
            )
            .map_err(PdfError::new_err)
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
        validate_password_input(Some(user_password), "user password")?;
        validate_password_input(Some(owner_password), "owner password")?;
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
    fn resolve_dest<'a>(
        &'a self,
        dest: &'a Object,
        page_map: &BTreeMap<ObjectId, u32>,
        named_destinations: &mut Option<HashMap<&'a [u8], &'a Object>>,
        encoded_bytes: &mut usize,
        returned_bytes: &mut usize,
    ) -> PyResult<ResolvedDestination> {
        let doc = &self.doc;
        let mut nameddest = None;
        let mut resolved = deref_object(doc, dest);
        if let Object::Name(name) | Object::String(name, _) = resolved {
            nameddest = Some(bounded_annotation_bytes(
                name,
                encoded_bytes,
                returned_bytes,
                "named destination",
            )?);
            if named_destinations.is_none() {
                *named_destinations = Some(named_destination_index(doc)?);
            }
            let found = named_destinations
                .as_ref()
                .and_then(|index| index.get(name.as_slice()).copied())
                .or_else(|| lookup_legacy_named_dest(doc, name));
            let Some(found) = found else {
                return Ok((None, None, None, nameddest));
            };
            resolved = found;
        }
        // A named destination value may be a dictionary containing `/D`.
        if let Object::Dictionary(d) = resolved {
            match d.get(b"D") {
                Ok(inner) => resolved = deref_object(doc, inner),
                Err(_) => return Ok((None, None, None, nameddest)),
            }
        }
        let Object::Array(arr) = resolved else {
            return Ok((None, None, None, nameddest));
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
        Ok((page, to, zoom, nameddest))
    }
}

/// Collect page-label definitions from a bounded, cycle-aware number-tree walk.
fn collect_page_labels(doc: &Document) -> PyResult<Vec<PageLabelEntry>> {
    let Some(root) = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"PageLabels").ok())
    else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    let mut visited = HashSet::new();
    let mut stack = vec![(root, 1usize)];
    let mut nodes = 0usize;
    let mut edges = 0usize;
    let mut pairs_seen = 0usize;
    let mut encoded_text_bytes = 0usize;
    let mut decoded_text_bytes = 0usize;
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object
            && !visited.insert(*id)
        {
            continue;
        }
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes = nodes.saturating_add(1);
        if nodes > MAX_PAGE_LABEL_TREE_NODES {
            return Err(PdfError::new_err(format!(
                "page-label number tree exceeds the {MAX_PAGE_LABEL_TREE_NODES}-node safety limit"
            )));
        }
        if let Ok(nums) = node.get(b"Nums").and_then(Object::as_array) {
            pairs_seen = pairs_seen
                .checked_add(nums.len().div_ceil(2))
                .ok_or_else(|| {
                    PdfError::new_err("page-label number tree exceeds the platform size limit")
                })?;
            if pairs_seen > MAX_PAGE_LABEL_ENTRIES {
                return Err(PdfError::new_err(format!(
                    "page-label number tree exceeds the {MAX_PAGE_LABEL_ENTRIES}-entry safety limit"
                )));
            }
            for pair in nums.chunks(2) {
                let [key, value] = pair else { continue };
                let Some(start) = resolve_i64(doc, key) else {
                    continue;
                };
                let Ok(label) = deref_object(doc, value).as_dict() else {
                    continue;
                };
                let style = label
                    .get(b"S")
                    .and_then(|object| deref_object(doc, object).as_name())
                    .ok()
                    .map(|name| {
                        encoded_text_bytes = encoded_text_bytes.saturating_add(name.len());
                        let text = String::from_utf8_lossy(name).into_owned();
                        decoded_text_bytes = decoded_text_bytes.saturating_add(text.len());
                        text
                    });
                let prefix = label
                    .get(b"P")
                    .ok()
                    .map(|object| deref_object(doc, object))
                    .and_then(|object| {
                        if let Object::String(encoded, _) = object {
                            encoded_text_bytes = encoded_text_bytes.saturating_add(encoded.len());
                        }
                        decode_text_string(object).ok()
                    })
                    .inspect(|text| {
                        decoded_text_bytes = decoded_text_bytes.saturating_add(text.len());
                    });
                if encoded_text_bytes > MAX_PAGE_LABEL_TEXT_BYTES
                    || decoded_text_bytes > MAX_PAGE_LABEL_TEXT_BYTES
                {
                    return Err(PdfError::new_err(format!(
                        "page-label text exceeds the {MAX_PAGE_LABEL_TEXT_BYTES}-byte safety limit"
                    )));
                }
                let first = label
                    .get(b"St")
                    .ok()
                    .and_then(|object| resolve_i64(doc, object))
                    .unwrap_or(1);
                out.push((start, style, prefix, first));
            }
        }
        if let Ok(kids) = node.get(b"Kids").and_then(Object::as_array) {
            if !kids.is_empty() && depth >= MAX_PAGE_LABEL_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "page-label number tree exceeds the {MAX_PAGE_LABEL_TREE_DEPTH}-level safety limit"
                )));
            }
            edges = edges.checked_add(kids.len()).ok_or_else(|| {
                PdfError::new_err("page-label number tree exceeds the platform size limit")
            })?;
            if edges > MAX_PAGE_LABEL_TREE_NODES {
                return Err(PdfError::new_err(format!(
                    "page-label number tree exceeds the {MAX_PAGE_LABEL_TREE_NODES}-edge safety limit"
                )));
            }
            for kid in kids {
                stack.push((kid, depth + 1));
            }
        }
    }
    out.sort_by_key(|(start, _, _, _)| *start);
    Ok(out)
}

/// Visit valid `(name, FileSpec)` items in the EmbeddedFiles name tree.
///
/// Recurse through `/Kids` with depth, cycle, node, entry, and decoded-name
/// guards. Nodes and FileSpecs remain borrowed so read operations do not clone
/// adversarial direct object shapes. Returning `Some` stops the walk early.
fn visit_embedded_files<'a, T>(
    doc: &'a Document,
    mut visitor: impl FnMut(String, &'a Object) -> PyResult<Option<T>>,
) -> PyResult<Option<T>> {
    let Some(root) = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Names").ok())
        .map(|names| deref_object(doc, names))
        .and_then(|names| names.as_dict().ok())
        .and_then(|names| names.get(b"EmbeddedFiles").ok())
    else {
        return Ok(None);
    };
    let mut visited = HashSet::new();
    let mut stack = vec![(root, 1usize)];
    let mut nodes = 0usize;
    let mut edges = 0usize;
    let mut pairs_seen = 0usize;
    let mut encoded_name_bytes = 0usize;
    let mut name_bytes = 0usize;
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object
            && !visited.insert(*id)
        {
            continue;
        }
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes = nodes.saturating_add(1);
        if nodes > MAX_EMBEDDED_FILE_TREE_NODES {
            return Err(PdfError::new_err(format!(
                "attachment name tree exceeds the {MAX_EMBEDDED_FILE_TREE_NODES}-node safety limit"
            )));
        }
        if let Ok(pairs) = node.get(b"Names").and_then(Object::as_array) {
            pairs_seen = pairs_seen
                .checked_add(pairs.len().div_ceil(2))
                .ok_or_else(|| {
                    PdfError::new_err("attachment name tree exceeds the platform size limit")
                })?;
            if pairs_seen > MAX_EMBEDDED_FILE_ENTRIES {
                return Err(PdfError::new_err(format!(
                    "attachment name tree exceeds the {MAX_EMBEDDED_FILE_ENTRIES}-entry safety limit"
                )));
            }
            for pair in pairs.chunks(2) {
                let [key, value] = pair else { continue };
                let Object::String(encoded_name, _) = key else {
                    continue;
                };
                encoded_name_bytes = encoded_name_bytes
                    .checked_add(encoded_name.len())
                    .ok_or_else(|| {
                        PdfError::new_err("attachment names exceed the platform size limit")
                    })?;
                if encoded_name_bytes > MAX_EMBEDDED_FILE_NAME_BYTES {
                    return Err(PdfError::new_err(format!(
                        "encoded attachment names exceed the {MAX_EMBEDDED_FILE_NAME_BYTES}-byte safety limit"
                    )));
                }
                let Ok(name) = decode_text_string(key) else {
                    continue;
                };
                name_bytes = name_bytes.checked_add(name.len()).ok_or_else(|| {
                    PdfError::new_err("attachment names exceed the platform size limit")
                })?;
                if name_bytes > MAX_EMBEDDED_FILE_NAME_BYTES {
                    return Err(PdfError::new_err(format!(
                        "attachment names exceed the {MAX_EMBEDDED_FILE_NAME_BYTES}-byte safety limit"
                    )));
                }
                if !matches!(value, Object::Reference(_) | Object::Dictionary(_)) {
                    continue;
                }
                if let Some(result) = visitor(name, value)? {
                    return Ok(Some(result));
                }
            }
        }
        if let Ok(kids) = node.get(b"Kids").and_then(Object::as_array) {
            if !kids.is_empty() && depth >= MAX_EMBEDDED_FILE_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "attachment name tree exceeds the {MAX_EMBEDDED_FILE_TREE_DEPTH}-level safety limit"
                )));
            }
            edges = edges.checked_add(kids.len()).ok_or_else(|| {
                PdfError::new_err("attachment name tree exceeds the platform size limit")
            })?;
            if edges > MAX_EMBEDDED_FILE_TREE_NODES {
                return Err(PdfError::new_err(format!(
                    "attachment name tree exceeds the {MAX_EMBEDDED_FILE_TREE_NODES}-edge safety limit"
                )));
            }
            for kid in kids {
                stack.push((kid, depth + 1));
            }
        }
    }
    Ok(None)
}

fn embedded_file_shape_error(detail: &str, limit: usize) -> PyErr {
    PdfError::new_err(format!(
        "inline attachment FileSpec exceeds the {limit}-{detail} safety limit"
    ))
}

/// Bound the direct object shape copied when a name-tree FileSpec is inline.
///
/// Indirect references remain leaves. This preserves ordinary inline
/// FileSpecs without allowing custom keys to amplify one add/delete operation
/// through an arbitrarily deep or wide clone.
fn validate_inline_embedded_filespec(object: &Object) -> PyResult<()> {
    let mut pending = vec![(object, 1usize)];
    let mut objects = 0usize;
    let mut bytes = 0usize;
    while let Some((current, depth)) = pending.pop() {
        objects = objects.checked_add(1).ok_or_else(|| {
            PdfError::new_err("inline attachment FileSpec exceeds the platform size limit")
        })?;
        if objects > MAX_EMBEDDED_FILE_DIRECT_OBJECTS {
            return Err(embedded_file_shape_error(
                "object",
                MAX_EMBEDDED_FILE_DIRECT_OBJECTS,
            ));
        }
        if depth > MAX_EMBEDDED_FILE_DIRECT_DEPTH {
            return Err(embedded_file_shape_error(
                "level",
                MAX_EMBEDDED_FILE_DIRECT_DEPTH,
            ));
        }

        let mut add_bytes = |amount: usize| -> PyResult<()> {
            bytes = bytes.checked_add(amount).ok_or_else(|| {
                PdfError::new_err("inline attachment FileSpec exceeds the platform size limit")
            })?;
            if bytes > MAX_EMBEDDED_FILE_DIRECT_BYTES {
                return Err(embedded_file_shape_error(
                    "byte",
                    MAX_EMBEDDED_FILE_DIRECT_BYTES,
                ));
            }
            Ok(())
        };
        let child_count = match current {
            Object::Name(value) | Object::String(value, _) => {
                add_bytes(value.len())?;
                0
            }
            Object::Array(items) => items.len(),
            Object::Dictionary(dict) => {
                for (key, _) in dict.iter() {
                    add_bytes(key.len())?;
                }
                dict.len()
            }
            Object::Stream(stream) => {
                add_bytes(stream.content.len())?;
                for (key, _) in stream.dict.iter() {
                    add_bytes(key.len())?;
                }
                stream.dict.len()
            }
            _ => 0,
        };
        if child_count != 0 && depth >= MAX_EMBEDDED_FILE_DIRECT_DEPTH {
            return Err(embedded_file_shape_error(
                "level",
                MAX_EMBEDDED_FILE_DIRECT_DEPTH,
            ));
        }
        let scheduled = objects
            .checked_add(pending.len())
            .and_then(|total| total.checked_add(child_count))
            .ok_or_else(|| {
                PdfError::new_err("inline attachment FileSpec exceeds the platform size limit")
            })?;
        if scheduled > MAX_EMBEDDED_FILE_DIRECT_OBJECTS {
            return Err(embedded_file_shape_error(
                "object",
                MAX_EMBEDDED_FILE_DIRECT_OBJECTS,
            ));
        }
        let child_depth = depth + 1;
        match current {
            Object::Array(items) => {
                pending.extend(items.iter().map(|child| (child, child_depth)));
            }
            Object::Dictionary(dict) => {
                pending.extend(dict.iter().map(|(_, child)| (child, child_depth)));
            }
            Object::Stream(stream) => {
                pending.extend(stream.dict.iter().map(|(_, child)| (child, child_depth)));
            }
            _ => {}
        }
    }
    Ok(())
}

/// Collect owned entries for attachment-tree rewrite operations.
fn collect_embedded_files(doc: &Document) -> PyResult<Vec<EmbeddedFileEntry>> {
    let mut out = Vec::new();
    visit_embedded_files(doc, |name, value| {
        if matches!(value, Object::Dictionary(_)) {
            validate_inline_embedded_filespec(value)?;
        }
        out.push((name, value.clone()));
        Ok(None::<()>)
    })?;
    Ok(out)
}

/// Validate the name-dictionary target before attachment objects are added.
fn embedded_files_write_target(doc: &Document) -> PyResult<EmbeddedFilesWriteTarget> {
    let catalog = doc.catalog().map_err(to_py_err)?;
    if !catalog.has(b"Names") {
        return Ok(EmbeddedFilesWriteTarget::Missing);
    }
    match catalog.get(b"Names").map_err(to_py_err)? {
        Object::Dictionary(_) => Ok(EmbeddedFilesWriteTarget::Inline),
        Object::Reference(id) => {
            doc.get_object(*id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?;
            Ok(EmbeddedFilesWriteTarget::Indirect(*id))
        }
        _ => Err(PdfError::new_err(
            "Catalog Names entry must be a dictionary or indirect dictionary",
        )),
    }
}

/// Rewrite EmbeddedFiles as one flat node while preserving other name trees.
fn write_embedded_files(
    doc: &mut Document,
    mut entries: Vec<EmbeddedFileEntry>,
    target: EmbeddedFilesWriteTarget,
) -> PyResult<()> {
    entries.sort_by(|a, b| a.0.cmp(&b.0));
    let mut flat = Vec::with_capacity(entries.len() * 2);
    for (name, filespec) in entries {
        flat.push(text_string(&name));
        flat.push(filespec);
    }
    let tree = Object::Dictionary(dictionary! { "Names" => Object::Array(flat) });
    match target {
        EmbeddedFilesWriteTarget::Indirect(id) => {
            let names = doc
                .get_object_mut(id)
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            names.set("EmbeddedFiles", tree);
        }
        EmbeddedFilesWriteTarget::Inline => {
            let catalog = doc.catalog_mut().map_err(to_py_err)?;
            let names = catalog
                .get_mut(b"Names")
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            names.set("EmbeddedFiles", tree);
        }
        EmbeddedFilesWriteTarget::Missing => {
            doc.catalog_mut().map_err(to_py_err)?.set(
                "Names",
                dictionary! {
                    "EmbeddedFiles" => tree,
                },
            );
        }
    }
    Ok(())
}

#[pymethods]
impl _Document {
    /// Create an empty PDF document.
    #[new]
    #[pyo3(signature = (
        max_text_size=None,
        max_interpretation_size=None,
        max_text_glyphs=None
    ))]
    fn new(
        max_text_size: Option<usize>,
        max_interpretation_size: Option<usize>,
        max_text_glyphs: Option<usize>,
    ) -> PyResult<Self> {
        validate_interpretation_limit(max_interpretation_size)?;
        validate_text_glyph_limit(max_text_glyphs)?;
        let mut document = Self::from_doc(
            Document::with_version("1.7"),
            None,
            max_text_size,
            max_interpretation_size,
            max_text_glyphs,
        );
        document.ensure_page_tree().map_err(to_py_err)?;
        Ok(document)
    }

    /// Load from a file path.
    ///
    /// `password` decrypts encrypted PDFs. Optional limits are validated before
    /// returning a document or interpreting its text.
    #[staticmethod]
    #[pyo3(signature = (
        path,
        password=None,
        max_decompressed_size=None,
        max_page_content_size=None,
        max_file_size=None,
        max_pages=None,
        max_objects=None,
        max_total_decompressed_size=None,
        max_object_depth=None,
        max_text_size=None,
        max_interpretation_size=None,
        max_text_glyphs=None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn load(
        py: Python<'_>,
        path: &str,
        password: Option<String>,
        max_decompressed_size: Option<usize>,
        max_page_content_size: Option<usize>,
        max_file_size: Option<usize>,
        max_pages: Option<usize>,
        max_objects: Option<usize>,
        max_total_decompressed_size: Option<usize>,
        max_object_depth: Option<usize>,
        max_text_size: Option<usize>,
        max_interpretation_size: Option<usize>,
        max_text_glyphs: Option<usize>,
    ) -> PyResult<Self> {
        validate_password_input(password.as_deref(), "password")?;
        validate_interpretation_limit(max_interpretation_size)?;
        validate_text_glyph_limit(max_text_glyphs)?;
        let limits = DocumentLimits {
            max_file_size,
            max_pages,
            max_objects,
            max_decompressed_size,
            max_page_content_size,
            max_total_decompressed_size,
            max_object_depth,
            max_text_size,
            max_interpretation_size,
            max_text_glyphs,
        };
        let (decoder_bound, decoder_limit_code) = match (
            limits.max_decompressed_size,
            limits.max_total_decompressed_size,
        ) {
            (Some(per_stream), Some(total)) if total < per_stream => {
                (Some(total), "total_decompressed_size")
            }
            (Some(per_stream), _) => (Some(per_stream), "decompressed_size"),
            (None, Some(total)) => (Some(total), "total_decompressed_size"),
            (None, None) => (None, "decompressed_size"),
        };
        let options = LoadOptions {
            password,
            max_decompressed_size: decoder_bound,
            ..Default::default()
        };
        py.detach(|| {
            let data = read_input(path, limits.max_file_size)?;
            let (doc, repaired) = load_document_with_recovery(&data, options).map_err(|error| {
                load_err(
                    Some(&format!("failed to load {path}")),
                    &error,
                    decoder_limit_code,
                )
            })?;
            let decrypted = !doc.is_encrypted();
            validate_structural_limits(&doc, limits, decrypted)?;
            if decrypted {
                validate_decompression_limits(
                    &doc,
                    limits.max_decompressed_size,
                    limits.max_page_content_size,
                    limits.max_total_decompressed_size,
                )?;
            }
            let is_repaired = repaired.is_some();
            let source = repaired.unwrap_or(data);
            let hayro_source = if doc.was_encrypted() {
                HayroSource::Unavailable
            } else {
                HayroSource::from_owned(source, limits.max_interpretation_size)
            };
            Ok(Self::from_loaded_doc(
                doc,
                hayro_source,
                limits.max_text_size,
                limits.max_interpretation_size,
                limits.max_text_glyphs,
                is_repaired,
            ))
        })
    }

    /// Load from bytes with the same arguments as `load`.
    #[staticmethod]
    #[pyo3(signature = (
        data,
        password=None,
        max_decompressed_size=None,
        max_page_content_size=None,
        max_file_size=None,
        max_pages=None,
        max_objects=None,
        max_total_decompressed_size=None,
        max_object_depth=None,
        max_text_size=None,
        max_interpretation_size=None,
        max_text_glyphs=None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn load_bytes(
        py: Python<'_>,
        data: &[u8],
        password: Option<String>,
        max_decompressed_size: Option<usize>,
        max_page_content_size: Option<usize>,
        max_file_size: Option<usize>,
        max_pages: Option<usize>,
        max_objects: Option<usize>,
        max_total_decompressed_size: Option<usize>,
        max_object_depth: Option<usize>,
        max_text_size: Option<usize>,
        max_interpretation_size: Option<usize>,
        max_text_glyphs: Option<usize>,
    ) -> PyResult<Self> {
        validate_password_input(password.as_deref(), "password")?;
        validate_interpretation_limit(max_interpretation_size)?;
        validate_text_glyph_limit(max_text_glyphs)?;
        let limits = DocumentLimits {
            max_file_size,
            max_pages,
            max_objects,
            max_decompressed_size,
            max_page_content_size,
            max_total_decompressed_size,
            max_object_depth,
            max_text_size,
            max_interpretation_size,
            max_text_glyphs,
        };
        validate_input_size(data, limits.max_file_size)?;
        let (decoder_bound, decoder_limit_code) = match (
            limits.max_decompressed_size,
            limits.max_total_decompressed_size,
        ) {
            (Some(per_stream), Some(total)) if total < per_stream => {
                (Some(total), "total_decompressed_size")
            }
            (Some(per_stream), _) => (Some(per_stream), "decompressed_size"),
            (None, Some(total)) => (Some(total), "total_decompressed_size"),
            (None, None) => (None, "decompressed_size"),
        };
        let options = LoadOptions {
            password,
            max_decompressed_size: decoder_bound,
            ..Default::default()
        };
        py.detach(|| {
            let (doc, repaired) = load_document_with_recovery(data, options)
                .map_err(|error| load_err(None, &error, decoder_limit_code))?;
            let decrypted = !doc.is_encrypted();
            validate_structural_limits(&doc, limits, decrypted)?;
            if decrypted {
                validate_decompression_limits(
                    &doc,
                    limits.max_decompressed_size,
                    limits.max_page_content_size,
                    limits.max_total_decompressed_size,
                )?;
            }
            let is_repaired = repaired.is_some();
            let hayro_source = if doc.was_encrypted() {
                HayroSource::Unavailable
            } else {
                match repaired {
                    Some(repaired) => {
                        HayroSource::from_owned(repaired, limits.max_interpretation_size)
                    }
                    None => HayroSource::from_borrowed(data, limits.max_interpretation_size)?,
                }
            };
            Ok(Self::from_loaded_doc(
                doc,
                hayro_source,
                limits.max_text_size,
                limits.max_interpretation_size,
                limits.max_text_glyphs,
                is_repaired,
            ))
        })
    }

    /// Return `(pages, objects, streams, encoded stream bytes, direct depth)`.
    fn complexity(&self) -> ComplexityTuple {
        document_complexity(&self.doc)
    }

    /// Read metadata quickly without loading the complete document.
    ///
    /// Return `(Info string dict, page count, version, encrypted, repaired)`.
    #[staticmethod]
    #[pyo3(signature = (path, password=None, max_file_size=None))]
    fn load_metadata(
        py: Python<'_>,
        path: &str,
        password: Option<String>,
        max_file_size: Option<usize>,
    ) -> PyResult<MetadataTuple> {
        validate_password_input(password.as_deref(), "password")?;
        py.detach(|| {
            let data = read_input(path, max_file_size)?;
            let (meta, repaired) = load_metadata_with_recovery(&data, password.as_deref())
                .map_err(|error| lopdf_err(Some(&format!("failed to load {path}")), &error))?;
            let (metadata, page_count, version, encrypted) = pdf_metadata_to_tuple(meta)?;
            Ok((metadata, page_count, version, encrypted, repaired))
        })
    }

    /// Read metadata from bytes, returning the same shape as `load_metadata`.
    #[staticmethod]
    #[pyo3(signature = (data, password=None, max_file_size=None))]
    fn load_metadata_bytes(
        py: Python<'_>,
        data: &[u8],
        password: Option<String>,
        max_file_size: Option<usize>,
    ) -> PyResult<MetadataTuple> {
        validate_password_input(password.as_deref(), "password")?;
        validate_input_size(data, max_file_size)?;
        py.detach(|| {
            let (meta, repaired) =
                load_metadata_with_recovery(data, password.as_deref()).map_err(to_py_err)?;
            let (metadata, page_count, version, encrypted) = pdf_metadata_to_tuple(meta)?;
            Ok((metadata, page_count, version, encrypted, repaired))
        })
    }

    /// Return whether opening repaired an incorrect final classic startxref.
    fn is_repaired(&self) -> bool {
        self.is_repaired
    }

    /// Configure a CJK fallback font for rendering.
    ///
    /// `kind` is `sans` (default) or `serif`. `data` contains TTF/OTF/TTC bytes;
    /// `index` selects a TTC face.
    #[pyo3(signature = (
        kind,
        data,
        index,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE)
    ))]
    fn set_fallback_font(
        &mut self,
        kind: &str,
        data: Vec<u8>,
        index: u32,
        max_font_size: Option<usize>,
    ) -> PyResult<()> {
        validate_font_input(Some(&data), max_font_size)?;
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
        self.invalidate_interpreted_pages();
        Ok(())
    }

    /// Read and configure bounded CJK fallback font input from a path.
    #[pyo3(signature = (
        kind,
        path,
        index,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE)
    ))]
    fn set_fallback_font_file(
        &mut self,
        py: Python<'_>,
        kind: &str,
        path: &str,
        index: u32,
        max_font_size: Option<usize>,
    ) -> PyResult<()> {
        validate_font_input(None, max_font_size)?;
        let data = py.detach(|| read_font_input(path, max_font_size))?;
        self.set_fallback_font(kind, data, index, max_font_size)
    }

    /// Atomically read and configure both bundled CJK fallback font paths.
    #[pyo3(signature = (
        sans_path,
        serif_path,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE)
    ))]
    fn set_fallback_font_files(
        &mut self,
        py: Python<'_>,
        sans_path: &str,
        serif_path: &str,
        max_font_size: Option<usize>,
    ) -> PyResult<()> {
        validate_font_input(None, max_font_size)?;
        let (sans, serif) = py.detach(|| {
            let sans = read_font_input(sans_path, max_font_size)?;
            let serif = read_font_input(serif_path, max_font_size)?;
            Ok::<_, PyErr>((sans, serif))
        })?;
        self.fallback_fonts.sans = Some((Arc::new(sans), 0));
        self.fallback_fonts.serif = Some((Arc::new(serif), 0));
        self.invalidate_interpreted_pages();
        Ok(())
    }

    /// Clear all CJK fallback-font configuration.
    fn clear_fallback_fonts(&mut self) {
        self.fallback_fonts = FallbackFonts::default();
        self.invalidate_interpreted_pages();
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
    fn authenticate_user_password(&self, password: &str) -> PyResult<bool> {
        validate_password_input(Some(password), "password")?;
        Ok(self.doc.authenticate_user_password(password).is_ok())
    }

    /// Check an owner password without decrypting.
    fn authenticate_owner_password(&self, password: &str) -> PyResult<bool> {
        validate_password_input(Some(password), "password")?;
        Ok(self.doc.authenticate_owner_password(password).is_ok())
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
    #[pyo3(signature = (max_size=None))]
    fn save_bytes(&mut self, py: Python<'_>, max_size: Option<usize>) -> PyResult<Vec<u8>> {
        py.detach(|| serialize_pdf(&mut self.doc, None, max_size))
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
    #[pyo3(signature = (max_size=None))]
    fn save_bytes_with_object_streams(
        &mut self,
        py: Python<'_>,
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        self.invalidate_hayro_pdf();
        py.detach(|| serialize_pdf(&mut self.doc, Some(modern_save_options()), max_size))
    }

    /// Return the page count.
    fn page_count(&self) -> usize {
        self.doc.get_pages().len()
    }

    /// Return the PDF version string, such as `"1.7"`.
    fn version(&self) -> String {
        self.doc.version.clone()
    }

    /// Return the eight standard Info strings under aggregate text budgets.
    fn get_metadata(&self, py: Python<'_>) -> PyResult<BTreeMap<String, String>> {
        py.detach(|| collect_info_metadata(&self.doc))
    }

    /// Set an Info entry, deleting it when the value is empty.
    fn set_metadata(&mut self, key: &str, value: &str) -> PyResult<()> {
        self.set_metadata_entries(vec![(key.to_owned(), value.to_owned())])
    }

    /// Atomically update multiple standard Info entries.
    fn set_metadata_batch(&mut self, entries: Vec<(String, String)>) -> PyResult<()> {
        self.set_metadata_entries(entries)
    }

    /// Delete a one-based page.
    fn delete_pages(&mut self, page_numbers: Vec<u32>) -> PyResult<()> {
        if page_numbers.len() > MAX_STRUCTURAL_PAGE_BATCH {
            return Err(PdfError::new_err(format!(
                "cannot delete more than {MAX_STRUCTURAL_PAGE_BATCH} page entries per call"
            )));
        }
        if page_numbers.is_empty() {
            return Ok(());
        }
        self.invalidate_hayro_pdf();
        self.doc.delete_pages(&page_numbers);
        Ok(())
    }

    /// Extract text from a one-based page.
    ///
    /// Collect glyph Unicode/positions through the hayro interpreter and
    /// assemble reading-order text. CJK fallbacks also apply to extraction.
    fn extract_text(&mut self, py: Python<'_>, page_numbers: Vec<u32>) -> PyResult<String> {
        if page_numbers.len() > MAX_TEXT_EXTRACTION_PAGES {
            return Err(PdfError::new_err(format!(
                "cannot extract text from more than {MAX_TEXT_EXTRACTION_PAGES} page entries per call"
            )));
        }
        // Every collected glyph contributes at least one UTF-8 byte. Plain
        // assembly adds at most one inferred gap or line ending per glyph, so
        // twice the configured payload budget is a complete output bound.
        let max_output_size = self
            .max_text_size
            .map(|limit| {
                limit.checked_mul(2).ok_or_else(|| {
                    limit_err(
                        "text_size",
                        "configured text budget exceeds the platform limit",
                    )
                })
            })
            .transpose()?;
        let settings = self.interpreter_settings();
        py.detach(|| {
            let mut out = String::new();
            for number in &page_numbers {
                let remaining = max_output_size.map(|limit| limit.saturating_sub(out.len()));
                let page_text = self
                    .text_page(*number, settings.clone())?
                    .text(remaining)
                    .map_err(|error| match error {
                        crate::extract::TextPageLimit::TextSize(_) => {
                            if let Some(limit) = max_output_size {
                                limit_err(
                                    "text_size",
                                    format!(
                                        "plain text output exceeds the {limit}-byte limit derived from max_text_size"
                                    ),
                                )
                            } else {
                                limit_err(
                                    "text_size",
                                    "plain text output exceeds the platform limit",
                                )
                            }
                        }
                        other => text_page_limit_err(other),
                    })?;
                out.try_reserve(page_text.len()).map_err(|error| {
                    PdfError::new_err(format!("failed to grow multi-page text output: {error}"))
                })?;
                out.push_str(&page_text);
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
        py.detach(|| {
            self.text_page(page_number, settings)?
                .layout()
                .map_err(text_page_limit_err)
        })
    }

    /// Detect high-confidence tables on a one-based page.
    #[pyo3(signature = (page_number, strategy, clip=None))]
    fn find_tables(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        strategy: &str,
        clip: Option<(f64, f64, f64, f64)>,
    ) -> PyResult<Vec<crate::extract::TableTuple>> {
        let text_strategy = match strategy {
            "lines" => false,
            "text" => true,
            _ => {
                return Err(PyValueError::new_err("strategy must be 'lines' or 'text'"));
            }
        };
        let settings = self.interpreter_settings();
        py.detach(|| {
            Ok(self
                .table_page(page_number, settings)?
                .tables(text_strategy, clip))
        })
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
            crate::extract::extract_page_images(pdf, page, settings).map_err(PdfError::new_err)
        })
    }

    /// Extract vector paint operations on a one-based page.
    fn extract_drawings(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<Vec<crate::extract::DrawingTuple>> {
        let settings = self.interpreter_settings();
        let pdf = self.hayro_view()?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            crate::extract::extract_page_drawings(pdf, page, settings).map_err(PdfError::new_err)
        })
    }

    /// Downsample and JPEG-recompress safe DCT or Flate XObjects atomically.
    fn compress_images(
        &mut self,
        py: Python<'_>,
        target_dpi: Option<f64>,
        quality: u8,
    ) -> PyResult<image_compression::CompressionResult> {
        let settings = self.interpreter_settings();
        let pdf = self.hayro_view()?;
        let usages = py
            .detach(|| crate::extract::collect_image_usages(pdf, settings))
            .map_err(PdfError::new_err)?;
        let mut edited = self.doc.clone();
        let result = py
            .detach(|| {
                image_compression::compress_images(&mut edited, &usages, target_dpi, quality)
            })
            .map_err(PdfError::new_err)?;
        if result.1 > 0 {
            self.doc = edited;
            self.invalidate_hayro_pdf();
        }
        Ok(result)
    }

    /// Search a one-based page case-insensitively.
    #[pyo3(signature = (
        page_number,
        needle,
        max_hits=Some(DEFAULT_MAX_SEARCH_HITS)
    ))]
    fn search_page(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        needle: &str,
        max_hits: Option<usize>,
    ) -> PyResult<Vec<(f64, f64, f64, f64)>> {
        if needle.is_empty() {
            return Err(PyValueError::new_err("needle must be at least 1 character"));
        }
        if needle.len() > MAX_SEARCH_INPUT_BYTES {
            return Err(limit_err(
                "search_input_size",
                format!(
                    "search needle exceeds the {MAX_SEARCH_INPUT_BYTES}-byte UTF-8 safety limit"
                ),
            ));
        }
        if max_hits == Some(0) {
            return Err(PyValueError::new_err(
                "max_hits must be a positive integer or None",
            ));
        }
        let settings = self.interpreter_settings();
        py.detach(|| {
            self.text_page(page_number, settings)?
                .search(needle, max_hits)
                .map_err(|crate::extract::SearchError::TooManyHits| {
                    let limit = max_hits.expect("bounded search hit count");
                    limit_err(
                        "search_hit_count",
                        format!("search results exceed the {limit}-hit safety limit"),
                    )
                })
        })
    }

    /// Append every page from another document.
    fn merge(&mut self, py: Python<'_>, other: &Self) -> PyResult<()> {
        let page_count = other.doc.get_pages().len();
        if page_count > MAX_STRUCTURAL_PAGE_BATCH {
            return Err(PdfError::new_err(format!(
                "cannot merge more than {MAX_STRUCTURAL_PAGE_BATCH} page entries per call"
            )));
        }
        let count = u32::try_from(page_count).map_err(|e| PdfError::new_err(e.to_string()))?;
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
        if page_numbers.len() > MAX_STRUCTURAL_PAGE_BATCH {
            return Err(PdfError::new_err(format!(
                "cannot merge more than {MAX_STRUCTURAL_PAGE_BATCH} page entries per call"
            )));
        }
        if page_numbers.is_empty() {
            return Ok(());
        }
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
        if page_numbers.len() > MAX_STRUCTURAL_PAGE_BATCH {
            return Err(PdfError::new_err(format!(
                "cannot select more than {MAX_STRUCTURAL_PAGE_BATCH} page entries per call"
            )));
        }
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
        max_output_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        if max_output_size == Some(0) {
            return Err(PyValueError::new_err(
                "max_output_size must be a positive integer or None",
            ));
        }
        let pixmap = self.render_pixmap_impl(py, page_number, scale, background)?;
        // PNG encoding can cost more than rasterization, so release the GIL and
        // use Fast/fdeflate. Balanced is tens of times slower for about 10%
        // smaller output and made PNG the dominant render cost in benchmarks.
        py.detach(|| rendered_png(pixmap, max_output_size))
    }

    /// Render one-based pages to PNG in input order on a bounded worker pool.
    fn render_pages_png(
        &mut self,
        py: Python<'_>,
        page_numbers: Vec<u32>,
        scale: f32,
        background: Option<(u8, u8, u8, u8)>,
        workers: usize,
        max_output_size: Option<usize>,
    ) -> PyResult<Vec<Vec<u8>>> {
        if workers == 0 || workers > 64 {
            return Err(PyValueError::new_err("workers must be between 1 and 64"));
        }
        if page_numbers.is_empty() {
            return Ok(Vec::new());
        }
        if page_numbers.len() > MAX_RENDER_BATCH_PAGES {
            return Err(PdfError::new_err(format!(
                "cannot render more than {MAX_RENDER_BATCH_PAGES} pages per batch"
            )));
        }
        let interpreter_settings = self.interpreter_settings();
        py.detach(|| {
            let pdf = self.hayro_view()?;
            #[cfg(not(target_os = "emscripten"))]
            let max_pixels = page_numbers
                .iter()
                .map(|&page_number| render_pixel_count(pdf, page_number, scale))
                .collect::<Result<Vec<_>, _>>()
                .map_err(PdfError::new_err)?
                .into_iter()
                .max()
                .expect("empty page lists return before rendering");
            let output_bytes = AtomicUsize::new(0);
            let renderer = BatchRenderer {
                pdf,
                interpreter_settings: &interpreter_settings,
                scale,
                background,
                max_output_size,
                output_bytes: &output_bytes,
            };
            #[cfg(target_os = "emscripten")]
            let rendered: PyResult<Vec<Vec<u8>>> = {
                let cache = RenderCache::new();
                page_numbers
                    .iter()
                    .map(|&page_number| renderer.render(&cache, page_number))
                    .collect()
            };
            #[cfg(not(target_os = "emscripten"))]
            let rendered: PyResult<Vec<Vec<u8>>> = {
                let estimated_page_bytes =
                    max_pixels.saturating_mul(ESTIMATED_RENDER_BYTES_PER_PIXEL);
                let memory_limited_workers =
                    usize::try_from((MAX_PARALLEL_RENDER_BYTES / estimated_page_bytes).max(1))
                        .unwrap_or(usize::MAX);
                let worker_count = workers.min(page_numbers.len()).min(memory_limited_workers);
                if worker_count == 1 {
                    let cache = RenderCache::new();
                    page_numbers
                        .iter()
                        .map(|&page_number| renderer.render(&cache, page_number))
                        .collect()
                } else {
                    let pool = rayon::ThreadPoolBuilder::new()
                        .num_threads(worker_count)
                        .thread_name(|index| format!("pylopdf-render-{index}"))
                        .build()
                        .map_err(|error| {
                            PdfError::new_err(format!(
                                "failed to create render worker pool: {error}"
                            ))
                        })?;
                    let next_page = AtomicUsize::new(0);
                    let groups = pool.install(|| {
                        (0..worker_count)
                            .into_par_iter()
                            .map(|_| {
                                let cache = RenderCache::new();
                                let mut rendered = Vec::new();
                                loop {
                                    let index = next_page.fetch_add(1, Ordering::Relaxed);
                                    let Some(&page_number) = page_numbers.get(index) else {
                                        break;
                                    };
                                    rendered.push((index, renderer.render(&cache, page_number)?));
                                }
                                Ok(rendered)
                            })
                            .collect::<PyResult<Vec<_>>>()
                    });
                    let mut ordered = Vec::with_capacity(page_numbers.len());
                    ordered.resize_with(page_numbers.len(), || None);
                    for (index, png) in groups?.into_iter().flatten() {
                        ordered[index] = Some(png);
                    }
                    Ok(ordered
                        .into_iter()
                        .map(|png| png.expect("every claimed page is rendered"))
                        .collect())
                }
            };
            rendered
        })
    }

    /// Render a one-based page and return a straight-alpha RGBA8 Pixmap.
    fn render_page_pixmap(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: f32,
        background: Option<(u8, u8, u8, u8)>,
        clip: Option<(f64, f64, f64, f64)>,
    ) -> PyResult<crate::pixmap::Pixmap> {
        let pixmap = self.render_pixmap_impl(py, page_number, scale, background)?;
        let width = u32::from(pixmap.width());
        let height = u32::from(pixmap.height());
        // Release the GIL: unpremultiplication, conversion, and cropping are costly.
        let (width, height, data) = py
            .detach(|| match clip {
                Some(clip) => cropped_rgba_bytes(pixmap, width, height, scale, clip),
                None => Ok((width, height, rgba_bytes(pixmap)?)),
            })
            .map_err(PdfError::new_err)?;
        Ok(crate::pixmap::Pixmap {
            width,
            height,
            data: Arc::new(data),
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
    ///
    /// hayro-svg 0.7 materializes its internal String before returning, so the
    /// limit prevents conversion to a second Python string rather than bounding
    /// the converter's temporary allocation.
    fn render_page_svg(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        max_output_size: Option<usize>,
    ) -> PyResult<String> {
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
            let svg = hayro_svg::convert(page, &cache, &interpreter_settings, &settings);
            if let Some(limit) = max_output_size
                && svg.len() > limit
            {
                return Err(limit_err(
                    "svg_output_size",
                    format!("rendered SVG exceeds the {limit}-byte UTF-8 output limit"),
                ));
            }
            Ok(svg)
        })
    }

    /// Return the TOC as flat `(level, title, one-based page number)` entries.
    ///
    /// Return empty when absent and skip entries that do not resolve to a page.
    fn get_toc(&self, py: Python<'_>) -> PyResult<Vec<TocEntry>> {
        py.detach(|| collect_toc(&self.doc))
    }

    /// Replace the TOC from `(level, title, one-based page)` entries; empty deletes.
    ///
    /// Preflight all input before mutation. lopdf writes non-ASCII titles as
    /// UTF-16BE with a BOM.
    fn set_toc(&mut self, entries: Vec<(u32, String, u32)>) -> PyResult<()> {
        if entries.len() > MAX_TOC_ENTRIES {
            return Err(PdfError::new_err(format!(
                "cannot set more than {MAX_TOC_ENTRIES} TOC entries"
            )));
        }
        let pages = self.doc.get_pages();
        let mut prepared = Vec::with_capacity(entries.len());
        let mut source_bytes = 0usize;
        let mut encoded_bytes = 0usize;
        let mut previous_level = 0u32;
        for (level, title, page) in entries {
            if level == 0 || level > previous_level.saturating_add(1) {
                return Err(PdfError::new_err(format!(
                    "invalid TOC level {level}; levels start at 1 and can increase by at most one"
                )));
            }
            if level as usize > MAX_TOC_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "TOC depth exceeds the {MAX_TOC_TREE_DEPTH}-level safety limit"
                )));
            }
            add_input_text_budget(
                &mut source_bytes,
                title.len(),
                MAX_TOC_TEXT_BYTES,
                "toc_input_size",
                "TOC source text",
            )?;
            let encoded_len = pdf_text_string_len(&title).ok_or_else(|| {
                limit_err(
                    "toc_input_size",
                    "TOC encoded text exceeds the platform text-size limit",
                )
            })?;
            add_input_text_budget(
                &mut encoded_bytes,
                encoded_len,
                MAX_TOC_TEXT_BYTES,
                "toc_input_size",
                "TOC encoded text",
            )?;
            let page_id = *pages
                .get(&page)
                .ok_or_else(|| PdfError::new_err(format!("page {page} does not exist")))?;
            prepared.push((level, title, page_id));
            previous_level = level;
        }
        let is_empty = prepared.is_empty();
        if !is_empty {
            let object_count = u32::try_from(prepared.len())
                .ok()
                .and_then(|count| count.checked_mul(2))
                .and_then(|count| count.checked_add(1))
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
            self.doc
                .max_id
                .checked_add(object_count)
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        }
        self.doc.catalog().map_err(to_py_err)?;

        self.invalidate_hayro_pdf();
        // Discard existing outlines and construction state.
        self.doc.bookmarks.clear();
        self.doc.bookmark_table.clear();
        self.doc.max_bookmark_id = 0;
        if let Ok(catalog) = self.doc.catalog_mut() {
            catalog.remove(b"Outlines");
        }
        if is_empty {
            self.doc.prune_objects();
            return Ok(());
        }
        // parents[level - 1] = latest bookmark ID at that level.
        let mut parents: Vec<u32> = Vec::new();
        for (level, title, page_id) in prepared {
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
    #[pyo3(signature = (
        user_password,
        owner_password,
        permissions,
        file_encryption_key,
        max_size=None
    ))]
    fn save_bytes_encrypted(
        &self,
        py: Python<'_>,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        py.detach(|| {
            let mut cloned = self.encrypted_clone(
                user_password,
                owner_password,
                permissions,
                file_encryption_key,
            )?;
            serialize_pdf(&mut cloned, None, max_size)
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
    #[allow(clippy::too_many_arguments)] // Mirrors Python's keyword-oriented drawing API.
    #[pyo3(signature = (
        page_number,
        rect,
        data,
        image_rotation,
        keep_proportion,
        overlay,
        max_size=Some(DEFAULT_MAX_IMAGE_INPUT_SIZE),
        max_pixels=Some(DEFAULT_MAX_IMAGE_PIXELS)
    ))]
    fn insert_image(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        data: Vec<u8>,
        image_rotation: i64,
        keep_proportion: bool,
        overlay: bool,
        max_size: Option<usize>,
        max_pixels: Option<u64>,
    ) -> PyResult<()> {
        validate_image_input(&data, max_size, max_pixels)?;
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.preflight_page_content(page_number)?;
        let (parts, matrix) = py.detach(|| -> PyResult<_> {
            let parts = draw::parse_image(data).map_err(PdfError::new_err)?.ok_or_else(|| {
                PdfError::new_err(
                    "unsupported image format (pass JPEG or PNG; convert other formats with Pillow or similar first)",
                )
            })?;
            let matrix = draw::image_placement_matrix(
                crop,
                rotation,
                [rect.0, rect.1, rect.2, rect.3],
                parts.width,
                parts.height,
                keep_proportion,
                image_rotation,
            );
            Ok((parts, matrix))
        })?;
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let xobj_id =
                draw::add_image_xobject(&mut self.doc, parts).map_err(PdfError::new_err)?;
            self.bake_page_attrs(page_id)?;
            let name = format!("PyloIm{}", xobj_id.0);
            self.doc
                .add_xobject(page_id, name.as_bytes(), xobj_id)
                .map_err(to_py_err)?;
            self.push_page_content(page_id, draw::draw_ops(matrix, &name), overlay)
        })
    }

    /// Read and draw bounded JPEG/PNG input from a filesystem path.
    #[allow(clippy::too_many_arguments)] // Mirrors Python's keyword-oriented drawing API.
    #[pyo3(signature = (
        page_number,
        rect,
        path,
        image_rotation,
        keep_proportion,
        overlay,
        max_size=Some(DEFAULT_MAX_IMAGE_INPUT_SIZE),
        max_pixels=Some(DEFAULT_MAX_IMAGE_PIXELS)
    ))]
    fn insert_image_file(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        path: &str,
        image_rotation: i64,
        keep_proportion: bool,
        overlay: bool,
        max_size: Option<usize>,
        max_pixels: Option<u64>,
    ) -> PyResult<()> {
        if max_size == Some(0) {
            return Err(PyValueError::new_err(
                "max_size must be a positive integer or None",
            ));
        }
        if max_pixels == Some(0) {
            return Err(PyValueError::new_err(
                "max_pixels must be a positive integer or None",
            ));
        }
        let data = py.detach(|| read_image_input(path, max_size))?;
        self.insert_image(
            py,
            page_number,
            rect,
            data,
            image_rotation,
            keep_proportion,
            overlay,
            max_size,
            max_pixels,
        )
    }

    /// Draw a rendered RGBA8 Pixmap directly into display `rect`.
    ///
    /// This avoids a PNG encode/decode round trip while preserving alpha.
    #[allow(clippy::too_many_arguments)] // Mirrors Python's keyword-oriented drawing API.
    fn insert_pixmap(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        pixmap: PyRef<'_, Pixmap>,
        image_rotation: i64,
        keep_proportion: bool,
        overlay: bool,
    ) -> PyResult<()> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.preflight_page_content(page_number)?;
        let width = pixmap.width;
        let height = pixmap.height;
        let data = Arc::clone(&pixmap.data);
        drop(pixmap);
        let (parts, matrix) = py.detach(|| -> PyResult<_> {
            let parts = draw::rgba_parts(width, height, &data).map_err(PdfError::new_err)?;
            let matrix = draw::image_placement_matrix(
                crop,
                rotation,
                [rect.0, rect.1, rect.2, rect.3],
                width,
                height,
                keep_proportion,
                image_rotation,
            );
            Ok((parts, matrix))
        })?;
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let xobj_id =
                draw::add_image_xobject(&mut self.doc, parts).map_err(PdfError::new_err)?;
            self.bake_page_attrs(page_id)?;
            let name = format!("PyloIm{}", xobj_id.0);
            self.doc
                .add_xobject(page_id, name.as_bytes(), xobj_id)
                .map_err(to_py_err)?;
            self.push_page_content(page_id, draw::draw_ops(matrix, &name), overlay)
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
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.preflight_page_content(page_number)?;
        let placement = PagePlacement {
            rect: [rect.0, rect.1, rect.2, rect.3],
            keep_proportion,
            overlay,
        };
        self.invalidate_hayro_pdf();
        py.detach(|| {
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                other.doc.clone(),
                src_page_number,
                placement,
            )
        })
    }

    /// Import a page from this document's pre-edit snapshot as a Form XObject.
    fn show_pdf_page_self(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        src_page_number: u32,
        keep_proportion: bool,
        overlay: bool,
    ) -> PyResult<()> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.preflight_page_content(page_number)?;
        let placement = PagePlacement {
            rect: [rect.0, rect.1, rect.2, rect.3],
            keep_proportion,
            overlay,
        };
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let source = self.doc.clone();
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                source,
                src_page_number,
                placement,
            )
        })
    }

    /// Read annotations from a one-based page.
    ///
    /// Each item is `(Subtype, display Rect, Contents, URI)`. Rect uses rotated
    /// top-left-origin display space; Contents/URI are optional.
    fn read_annotations(&self, py: Python<'_>, page_number: u32) -> PyResult<Vec<AnnotationTuple>> {
        py.detach(|| {
            let (crop, rotation) = self.page_display_geometry(page_number)?;
            let page_id = self.page_id(page_number)?;
            let Some(annots) = self.page_annotation_items(page_id)? else {
                return Ok(Vec::new());
            };
            let mut out = Vec::new();
            let mut encoded_bytes = 0usize;
            let mut returned_bytes = 0usize;
            for item in annots {
                let dict = match item {
                    Object::Reference(id) => {
                        match self.doc.get_object(*id).and_then(Object::as_dict) {
                            Ok(dict) => dict,
                            Err(_) => continue,
                        }
                    }
                    Object::Dictionary(dict) => dict,
                    _ => continue,
                };
                let subtype = match dict.get(b"Subtype").and_then(Object::as_name) {
                    Ok(name) => bounded_annotation_bytes(
                        name,
                        &mut encoded_bytes,
                        &mut returned_bytes,
                        "subtype text",
                    )?,
                    Err(_) => continue,
                };
                let Some(rect) = resolve_box(&self.doc, dict, b"Rect") else {
                    continue;
                };
                let display = draw::pdf_rect_to_display(crop, rotation, rect);
                let contents = match dict.get(b"Contents") {
                    Ok(object) => bounded_annotation_text(
                        &self.doc,
                        object,
                        &mut encoded_bytes,
                        &mut returned_bytes,
                        "Contents text",
                    )?,
                    Err(_) => None,
                };
                let uri_bytes = dict
                    .get(b"A")
                    .ok()
                    .and_then(|action| deref_object(&self.doc, action).as_dict().ok())
                    .filter(|action| {
                        matches!(action.get(b"S").and_then(Object::as_name), Ok(b"URI"))
                    })
                    .and_then(|action| action.get(b"URI").ok())
                    .map(|object| deref_object(&self.doc, object))
                    .and_then(|object| object.as_str().ok());
                let uri = match uri_bytes {
                    Some(bytes) => Some(bounded_annotation_bytes(
                        bytes,
                        &mut encoded_bytes,
                        &mut returned_bytes,
                        "URI text",
                    )?),
                    None => None,
                };
                out.push((
                    subtype,
                    (display[0], display[1], display[2], display[3]),
                    contents,
                    uri,
                ));
            }
            Ok(out)
        })
    }

    /// Read link annotations from a one-based page and resolve destinations.
    ///
    /// Support `/A` actions (URI, GoTo, GoToR, Launch, Named) and direct `/Dest`.
    /// Resolve GoTo names from `/Names` trees and legacy `/Dests`.
    fn read_links(&self, py: Python<'_>, page_number: u32) -> PyResult<Vec<LinkTuple>> {
        py.detach(|| {
            let (crop, rotation) = self.page_display_geometry(page_number)?;
            let page_id = self.page_id(page_number)?;
            let Some(annots) = self.page_annotation_items(page_id)? else {
                return Ok(Vec::new());
            };
            // Build ObjectId → lopdf page-number lookup for destination resolution.
            let page_map: BTreeMap<ObjectId, u32> = self
                .doc
                .get_pages()
                .into_iter()
                .map(|(number, id)| (id, number))
                .collect();
            let mut out = Vec::new();
            let mut encoded_bytes = 0usize;
            let mut returned_bytes = 0usize;
            let mut named_destinations = None;
            for item in annots {
                let dict = match item {
                    Object::Reference(id) => {
                        match self.doc.get_object(*id).and_then(Object::as_dict) {
                            Ok(dict) => dict,
                            Err(_) => continue,
                        }
                    }
                    Object::Dictionary(dict) => dict,
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

                let action = dict
                    .get(b"A")
                    .ok()
                    .and_then(|action| deref_object(&self.doc, action).as_dict().ok());
                if let Some(action) = action {
                    match action.get(b"S").and_then(Object::as_name) {
                        Ok(b"URI") => {
                            let uri = match action
                                .get(b"URI")
                                .ok()
                                .map(|object| deref_object(&self.doc, object))
                                .and_then(|object| object.as_str().ok())
                            {
                                Some(bytes) => Some(bounded_annotation_bytes(
                                    bytes,
                                    &mut encoded_bytes,
                                    &mut returned_bytes,
                                    "URI text",
                                )?),
                                None => None,
                            };
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
                                let (page, to, zoom, name) = self.resolve_dest(
                                    dest,
                                    &page_map,
                                    &mut named_destinations,
                                    &mut encoded_bytes,
                                    &mut returned_bytes,
                                )?;
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
                            let file = match action.get(b"F") {
                                Ok(object) => bounded_annotation_filespec_name(
                                    &self.doc,
                                    object,
                                    &mut encoded_bytes,
                                    &mut returned_bytes,
                                )?,
                                Err(_) => None,
                            };
                            // External-document destinations retain names without page resolution.
                            let name_bytes = action.get(b"D").ok().and_then(|destination| {
                                match deref_object(&self.doc, destination) {
                                    Object::Name(name) | Object::String(name, _) => {
                                        Some(name.as_slice())
                                    }
                                    _ => None,
                                }
                            });
                            let name = match name_bytes {
                                Some(bytes) => Some(bounded_annotation_bytes(
                                    bytes,
                                    &mut encoded_bytes,
                                    &mut returned_bytes,
                                    "named destination",
                                )?),
                                None => None,
                            };
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
                            let file = match action.get(b"F") {
                                Ok(object) => bounded_annotation_filespec_name(
                                    &self.doc,
                                    object,
                                    &mut encoded_bytes,
                                    &mut returned_bytes,
                                )?,
                                Err(_) => None,
                            };
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
                            let name_bytes = action
                                .get(b"N")
                                .ok()
                                .and_then(|object| deref_object(&self.doc, object).as_name().ok());
                            let name = match name_bytes {
                                Some(bytes) => Some(bounded_annotation_bytes(
                                    bytes,
                                    &mut encoded_bytes,
                                    &mut returned_bytes,
                                    "named action",
                                )?),
                                None => None,
                            };
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
                    let (page, to, zoom, name) = self.resolve_dest(
                        dest,
                        &page_map,
                        &mut named_destinations,
                        &mut encoded_bytes,
                        &mut returned_bytes,
                    )?;
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
        })
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
        if rects.is_empty() || rects.len() > MAX_HIGHLIGHT_RECTS {
            return Err(PdfError::new_err(format!(
                "highlight annotations require 1 to {MAX_HIGHLIGHT_RECTS} rectangles"
            )));
        }
        validate_annotation_input(
            [
                b"Highlight".as_slice(),
                content.as_deref().unwrap_or_default().as_bytes(),
            ],
            "content",
        )?;
        let encoded_content = match content {
            Some(content) => {
                let encoded = text_string(&content);
                if encoded.as_str().is_ok_and(|bytes| {
                    bytes
                        .len()
                        .checked_add(b"Highlight".len())
                        .is_none_or(|size| size > MAX_ANNOTATION_METADATA_BYTES)
                }) {
                    return Err(limit_err(
                        "annotation_input_size",
                        format!(
                            "encoded annotation subtype and content exceed the {MAX_ANNOTATION_METADATA_BYTES}-byte safety limit"
                        ),
                    ));
                }
                Some(encoded)
            }
            None => None,
        };
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        self.ensure_page_annotation_capacity(page_id, 1)?;
        self.invalidate_hayro_pdf();

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
            "AIS" => Object::Boolean(false),
        });
        let form_dict = dictionary! {
            "Type" => "XObject",
            "Subtype" => "Form",
            "FormType" => 1,
            "BBox" => Object::Array(bbox.iter().map(|&v| Object::Real(v as f32)).collect()),
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
        if let Some(encoded_content) = encoded_content {
            annot.set("Contents", encoded_content);
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
        validate_annotation_input([b"Link".as_slice(), uri.as_bytes()], "URI")?;
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        self.ensure_page_annotation_capacity(page_id, 1)?;
        self.invalidate_hayro_pdf();
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
    fn pdfa_claim(
        &self,
        py: Python<'_>,
        max_size: Option<usize>,
    ) -> PyResult<Option<(i64, String)>> {
        py.detach(|| {
            let Some(stream) = self
                .doc
                .catalog()
                .ok()
                .and_then(|catalog| catalog.get(b"Metadata").ok())
                .map(|metadata| deref_object(&self.doc, metadata))
                .and_then(|metadata| metadata.as_stream().ok())
            else {
                return Ok(None);
            };
            let data =
                decoded_stream_content(stream, max_size, "xmp_metadata_size", "XMP metadata")?;
            let xmp = String::from_utf8_lossy(&data);
            let Some(part) =
                xmp_value(&xmp, "pdfaid:part").and_then(|value| value.parse::<i64>().ok())
            else {
                return Ok(None);
            };
            let conformance = xmp_value(&xmp, "pdfaid:conformance").unwrap_or_default();
            Ok(Some((part, conformance)))
        })
    }

    /// Read page-label definitions from the PageLabels number tree.
    ///
    /// Each item is `(start index, style, prefix, first number)`. Recurse through
    /// Kids and return entries sorted by start page.
    fn get_page_labels(&self, py: Python<'_>) -> PyResult<Vec<PageLabelEntry>> {
        py.detach(|| collect_page_labels(&self.doc))
    }

    /// Write labels as a flat number tree; empty removes it, Python validates.
    fn set_page_labels(
        &mut self,
        labels: Vec<(i64, Option<String>, Option<String>, i64)>,
    ) -> PyResult<()> {
        if labels.len() > MAX_PAGE_LABEL_ENTRIES {
            return Err(PdfError::new_err(format!(
                "cannot set more than {MAX_PAGE_LABEL_ENTRIES} page-label ranges"
            )));
        }
        let mut nums = Vec::with_capacity(labels.len() * 2);
        let mut encoded_text_bytes = 0usize;
        let mut decoded_text_bytes = 0usize;
        for (start, style, prefix, st) in labels {
            let mut label = Dictionary::new();
            if let Some(s) = style {
                add_input_text_budget(
                    &mut encoded_text_bytes,
                    s.len(),
                    MAX_PAGE_LABEL_TEXT_BYTES,
                    "page_label_input_size",
                    "encoded page-label text",
                )?;
                add_input_text_budget(
                    &mut decoded_text_bytes,
                    s.len(),
                    MAX_PAGE_LABEL_TEXT_BYTES,
                    "page_label_input_size",
                    "page-label source text",
                )?;
                label.set("S", Object::Name(s.into_bytes()));
            }
            if let Some(p) = prefix {
                add_input_text_budget(
                    &mut decoded_text_bytes,
                    p.len(),
                    MAX_PAGE_LABEL_TEXT_BYTES,
                    "page_label_input_size",
                    "page-label source text",
                )?;
                let encoded_len = pdf_text_string_len(&p).ok_or_else(|| {
                    limit_err(
                        "page_label_input_size",
                        "encoded page-label text exceeds the platform text-size limit",
                    )
                })?;
                add_input_text_budget(
                    &mut encoded_text_bytes,
                    encoded_len,
                    MAX_PAGE_LABEL_TEXT_BYTES,
                    "page_label_input_size",
                    "encoded page-label text",
                )?;
                let encoded = text_string(&p);
                label.set("P", encoded);
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
        self.invalidate_hayro_pdf();
        Ok(())
    }

    /// Return AcroForm fields as `(full name, kind, value)`.
    ///
    /// Kind is text/checkbox/radio/button/combobox/listbox/signature. Value is
    /// stringified `/V`, including state names such as Yes/Off, or None.
    fn get_form_fields(&self, py: Python<'_>) -> PyResult<Vec<(String, String, Option<String>)>> {
        py.detach(|| {
            Ok(self
                .collect_form_fields()?
                .into_iter()
                .map(|(name, _, ft, ff, value)| {
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
                    (name, kind.to_owned(), value.map(|value| value.to_string()))
                })
                .collect())
        })
    }

    /// Return checkbox/radio state names from widget `AP /N` keys.
    fn form_button_states(&self, py: Python<'_>, name: &str) -> PyResult<Vec<String>> {
        validate_form_field_input(name, None)?;
        py.detach(|| {
            let (field_id, ft) = self
                .collect_form_fields()?
                .into_iter()
                .find(|(field_name, ..)| field_name == name)
                .map(|(_, id, ft, ..)| (id, ft))
                .ok_or_else(|| PdfError::new_err(format!("form field not found: {name:?}")))?;
            if ft != "Btn" {
                return Ok(Vec::new());
            }
            let (widget_states, _, _) = self.button_appearance_states(field_id)?;
            let mut states = Vec::new();
            let mut seen = HashSet::new();
            let mut returned_name_bytes = 0usize;
            for (_, widget_state_names) in widget_states {
                for state_name in widget_state_names {
                    if !seen.insert(state_name.clone()) {
                        continue;
                    }
                    if states.len() >= MAX_FORM_BUTTON_STATE_NAMES {
                        return Err(PdfError::new_err(format!(
                            "AcroForm button states exceed the {MAX_FORM_BUTTON_STATE_NAMES}-name safety limit"
                        )));
                    }
                    let state = String::from_utf8_lossy(&state_name).into_owned();
                    add_form_budget(
                        &mut returned_name_bytes,
                        state.len(),
                        MAX_FORM_BUTTON_STATE_NAME_BYTES,
                        "returned button-state name",
                    )?;
                    states.push(state);
                }
            }
            Ok(states)
        })
    }

    /// Set a form-field value and generate native widget appearances.
    #[pyo3(signature = (
        name,
        value,
        font_data=None,
        font_index=0,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE)
    ))]
    fn set_form_field(
        &mut self,
        py: Python<'_>,
        name: &str,
        value: &str,
        font_data: Option<Vec<u8>>,
        font_index: u32,
        max_font_size: Option<usize>,
    ) -> PyResult<()> {
        validate_form_field_input(name, Some(value))?;
        validate_font_input(font_data.as_deref(), max_font_size)?;
        let encoded_value = form_text_string(value);
        if encoded_value
            .as_str()
            .is_ok_and(|bytes| bytes.len() > MAX_FORM_FIELD_VALUE_BYTES)
        {
            return Err(PdfError::new_err(format!(
                "encoded form field value exceeds the {MAX_FORM_FIELD_VALUE_BYTES}-byte safety limit"
            )));
        }
        let result = py.detach(|| {
            let original = self.doc.clone();
            match self.set_form_field_inner(name, value, font_data, font_index) {
                Ok(()) => Ok(()),
                Err(error) => {
                    self.doc = original;
                    Err(error)
                }
            }
        });
        if result.is_ok() {
            self.invalidate_hayro_pdf();
        }
        result
    }

    /// Set a form-field value using bounded OpenType font input from a path.
    #[pyo3(signature = (
        name,
        value,
        path,
        font_index=0,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE)
    ))]
    fn set_form_field_file(
        &mut self,
        py: Python<'_>,
        name: &str,
        value: &str,
        path: &str,
        font_index: u32,
        max_font_size: Option<usize>,
    ) -> PyResult<()> {
        validate_form_field_input(name, Some(value))?;
        validate_font_input(None, max_font_size)?;
        let data = py.detach(|| read_font_input(path, max_font_size))?;
        self.set_form_field(py, name, value, Some(data), font_index, max_font_size)
    }

    /// Return sorted attachment names independent of name-tree order.
    fn embfile_names(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        py.detach(|| {
            let mut names = Vec::new();
            visit_embedded_files(&self.doc, |name, _| {
                names.push(name);
                Ok(None::<()>)
            })?;
            names.sort();
            Ok(names)
        })
    }

    /// Return attachment contents, bounding every decoding layer when requested.
    fn embfile_get(
        &self,
        py: Python<'_>,
        name: &str,
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        validate_embedded_file_lookup_name(name)?;
        py.detach(|| {
            let filespec_obj = visit_embedded_files(&self.doc, |entry_name, value| {
                Ok((entry_name == name).then_some(value))
            })?
            .ok_or_else(|| PdfError::new_err(format!("attachment not found: {name:?}")))?;
            let filespec = match filespec_obj {
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
            decoded_stream_content(
                stream,
                max_size,
                "embedded_file_size",
                &format!("attachment {name:?}"),
            )
        })
    }

    /// Add an attachment, rejecting duplicate names.
    #[pyo3(signature = (
        name,
        data,
        filename,
        desc,
        max_size=Some(DEFAULT_MAX_EMBEDDED_FILE_SIZE)
    ))]
    fn embfile_add(
        &mut self,
        py: Python<'_>,
        name: String,
        data: Vec<u8>,
        filename: Option<String>,
        desc: Option<String>,
        max_size: Option<usize>,
    ) -> PyResult<()> {
        validate_embedded_file_input_text(&name, filename.as_deref(), desc.as_deref())?;
        if max_size == Some(0) {
            return Err(PyValueError::new_err(
                "max_size must be a positive integer or None",
            ));
        }
        if let Some(limit) = max_size
            && data.len() > limit
        {
            return Err(limit_err(
                "embedded_file_size",
                format!(
                    "attachment input is {} bytes, exceeding the {limit}-byte limit",
                    data.len()
                ),
            ));
        }
        let result = py.detach(|| {
            let target = embedded_files_write_target(&self.doc)?;
            let entries = collect_embedded_files(&self.doc)?;
            if entries.iter().any(|(n, _)| *n == name) {
                return Err(PdfError::new_err(format!(
                    "an attachment with this name already exists: {name:?} (call embfile_del first)"
                )));
            }
            if entries.len() >= MAX_EMBEDDED_FILE_ENTRIES {
                return Err(PdfError::new_err(format!(
                    "cannot add attachment: the {MAX_EMBEDDED_FILE_ENTRIES}-entry safety limit is reached"
                )));
            }
            let total_name_bytes = entries
                .iter()
                .try_fold(name.len(), |total, (entry_name, _)| {
                    total.checked_add(entry_name.len())
                })
                .ok_or_else(|| {
                    PdfError::new_err("attachment names exceed the platform size limit")
                })?;
            if total_name_bytes > MAX_EMBEDDED_FILE_NAME_BYTES {
                return Err(PdfError::new_err(format!(
                    "cannot add attachment: decoded names would exceed the {MAX_EMBEDDED_FILE_NAME_BYTES}-byte safety limit"
                )));
            }
            let total_encoded_name_bytes =
                entries
                    .iter()
                    .map(|(entry_name, _)| entry_name)
                    .chain(std::iter::once(&name))
                    .try_fold(0usize, |total, entry_name| {
                        let Object::String(encoded, _) = text_string(entry_name) else {
                            unreachable!("text_string always returns a PDF string")
                        };
                        total.checked_add(encoded.len())
                    })
                    .ok_or_else(|| {
                        PdfError::new_err("attachment names exceed the platform size limit")
                    })?;
            if total_encoded_name_bytes > MAX_EMBEDDED_FILE_NAME_BYTES {
                return Err(PdfError::new_err(format!(
                    "cannot add attachment: encoded names would exceed the {MAX_EMBEDDED_FILE_NAME_BYTES}-byte safety limit"
                )));
            }
            let previous_max_id = self.doc.max_id;
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
            if let Err(error) = write_embedded_files(&mut self.doc, entries, target) {
                self.doc.objects.remove(&ef_id);
                self.doc.objects.remove(&filespec_id);
                self.doc.max_id = previous_max_id;
                return Err(error);
            }
            Ok(())
        });
        if result.is_ok() {
            self.invalidate_hayro_pdf();
        }
        result
    }

    /// Delete an attachment, raising an error when absent.
    fn embfile_del(&mut self, py: Python<'_>, name: &str) -> PyResult<()> {
        validate_embedded_file_lookup_name(name)?;
        let result = py.detach(|| {
            let target = embedded_files_write_target(&self.doc)?;
            let entries = collect_embedded_files(&self.doc)?;
            let before = entries.len();
            let remaining: Vec<EmbeddedFileEntry> =
                entries.into_iter().filter(|(n, _)| n != name).collect();
            if remaining.len() == before {
                return Err(PdfError::new_err(format!("attachment not found: {name:?}")));
            }
            write_embedded_files(&mut self.doc, remaining, target)
        });
        if result.is_ok() {
            self.invalidate_hayro_pdf();
        }
        result
    }

    /// Insert display-coordinate OCR words as an invisible text layer.
    ///
    /// Store only Unicode and position using a non-embedded Identity-H CID font,
    /// ToUnicode, and invisible `Tr 3`; it appears only in extraction and search.
    #[pyo3(signature = (page_number, words, text_rotation=0))]
    fn insert_ocr_layer(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        words: Vec<(f64, f64, f64, f64, String)>,
        text_rotation: u16,
    ) -> PyResult<()> {
        if !matches!(text_rotation, 0 | 90 | 180 | 270) {
            return Err(PdfError::new_err(
                "text_rotation must be 0, 90, 180, or 270",
            ));
        }
        let (page_id, expected_font_number, name, to_unicode, ops) = py.detach(|| {
            if words.len() > MAX_OCR_LAYER_WORDS {
                return Err(PdfError::new_err(format!(
                    "cannot insert more than {MAX_OCR_LAYER_WORDS} OCR words per call"
                )));
            }
            let text_bytes = words.iter().try_fold(0usize, |total, word| {
                total.checked_add(word.4.len()).ok_or_else(|| {
                    PdfError::new_err("OCR layer text exceeds the platform size limit")
                })
            })?;
            if text_bytes > MAX_OCR_LAYER_TEXT_BYTES {
                return Err(PdfError::new_err(format!(
                    "OCR layer text exceeds the {MAX_OCR_LAYER_TEXT_BYTES}-byte safety limit"
                )));
            }
            let (crop, rotation) = self.page_display_geometry(page_number)?;
            let page_id = self.preflight_page_content(page_number)?;
            let cid_map = ocr::assign_cids(&words).map_err(PdfError::new_err)?;
            let to_unicode = ocr::build_to_unicode(&cid_map);
            let expected_font_number = self.doc.max_id.checked_add(4).ok_or_else(|| {
                PdfError::new_err("OCR layer objects exceed the PDF object-ID limit")
            })?;
            let name = format!("PyloF{expected_font_number}");
            let ops = ocr::ocr_ops(crop, rotation, &words, &cid_map, &name, text_rotation);
            Ok((page_id, expected_font_number, name, to_unicode, ops))
        })?;

        // From this point onward malformed page/resource state could leave a
        // partial edit, so invalidate before the first PDF object is added.
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let font_id = ocr::add_ocr_font(&mut self.doc, to_unicode);
            debug_assert_eq!(font_id.0, expected_font_number);
            self.bake_page_attrs(page_id)?;
            self.doc
                .get_or_create_resources(page_id)
                .map_err(to_py_err)?;
            draw::add_page_font(&mut self.doc, page_id, &name, font_id).map_err(to_py_err)?;
            self.push_page_content(page_id, ops, true)
        })
    }

    /// Draw text from display-coordinate baseline `point` on a one-based page.
    ///
    /// `lines` contains WinAnsi bytes, one per line; Python validates and
    /// converts cp1252. `base_font` is a Standard 14 name and is not embedded.
    // This boundary mirrors the Python signature, so the argument count is intentional.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        point,
        lines,
        base_font,
        winansi,
        fontsize,
        color,
        overlay,
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
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
        overlay: bool,
        max_text_size: Option<usize>,
    ) -> PyResult<()> {
        validate_generated_text_line_count(lines.len(), max_text_size)?;
        validate_generated_text_input(lines.iter().map(Vec::as_slice), max_text_size)?;
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.preflight_page_content(page_number)?;
        self.invalidate_hayro_pdf();
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
            self.push_page_content(page_id, ops, overlay)
        })
    }

    /// Lay out and draw Standard 14 text inside a display-coordinate rectangle.
    ///
    /// Layout completes before any PDF object is added, so a negative spare
    /// height leaves the document byte-for-byte unmodified in memory.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        rect,
        text,
        base_font,
        winansi,
        fontsize,
        line_height,
        align,
        color,
        overlay,
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_page_textbox(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        text: &str,
        base_font: &str,
        winansi: bool,
        fontsize: f64,
        line_height: f64,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        let rect = [rect.0, rect.1, rect.2, rect.3];
        let layout = py.detach(|| {
            draw::standard_textbox_layout(
                text,
                (rect[2] - rect[0], rect[3] - rect[1]),
                base_font,
                fontsize,
                line_height,
                align == 3,
                max_text_size.map(|_| crate::layout::MAX_GENERATED_TEXT_LINES),
            )
            .map_err(generated_text_err)
        })?;
        if !layout.fits() {
            return Ok(layout.spare_height);
        }

        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.preflight_page_content(page_number)?;
        self.invalidate_hayro_pdf();
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
            let ops = draw::textbox_text_ops(
                crop, rotation, rect, &layout, align, &name, fontsize, color,
            )
            .map_err(PdfError::new_err)?;
            self.push_page_content(page_id, ops, overlay)?;
            Ok(layout.spare_height)
        })
    }

    /// Draw subset-embedded OpenType text through a krilla-generated Form.
    ///
    /// The temporary source page uses the target page's rotation-resolved
    /// display size and coordinate system, then follows `show_pdf_page`'s
    /// existing object-import and placement path.
    // This boundary mirrors the Python signature, so the argument count is intentional.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        point,
        lines,
        font_data,
        font_index,
        fontsize,
        color,
        overlay,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_embedded_text(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        point: (f64, f64),
        lines: Vec<String>,
        font_data: Vec<u8>,
        font_index: u32,
        fontsize: f64,
        color: (f64, f64, f64),
        overlay: bool,
        max_font_size: Option<usize>,
        max_text_size: Option<usize>,
    ) -> PyResult<()> {
        validate_generated_text_line_count(lines.len(), max_text_size)?;
        validate_generated_text_input(lines.iter().map(String::as_bytes), max_text_size)?;
        validate_font_input(Some(&font_data), max_font_size)?;
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        self.preflight_page_content(page_number)?;
        let (pdf_width, pdf_height) = (crop[2] - crop[0], crop[3] - crop[1]);
        let page_size = if matches!(rotation, 90 | 270) {
            (pdf_height, pdf_width)
        } else {
            (pdf_width, pdf_height)
        };
        let generated = py.detach(|| {
            generate::embedded_text_page(
                page_size, point, &lines, font_data, font_index, fontsize, color,
            )
            .map_err(PdfError::new_err)
        })?;
        let generated_doc = Document::load_mem(&generated)
            .map_err(|error| lopdf_err(Some("failed to import generated text"), &error))?;
        let source = Self::from_doc(generated_doc, Some(generated), None, None, None);
        self.show_pdf_page(
            py,
            page_number,
            (0.0, 0.0, page_size.0, page_size.1),
            &source,
            1,
            false,
            overlay,
        )
    }

    /// Draw subset-embedded text using bounded OpenType font input from a path.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        point,
        lines,
        path,
        font_index,
        fontsize,
        color,
        overlay,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_embedded_text_file(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        point: (f64, f64),
        lines: Vec<String>,
        path: &str,
        font_index: u32,
        fontsize: f64,
        color: (f64, f64, f64),
        overlay: bool,
        max_font_size: Option<usize>,
        max_text_size: Option<usize>,
    ) -> PyResult<()> {
        validate_generated_text_line_count(lines.len(), max_text_size)?;
        validate_generated_text_input(lines.iter().map(String::as_bytes), max_text_size)?;
        validate_font_input(None, max_font_size)?;
        let data = py.detach(|| read_font_input(path, max_font_size))?;
        self.insert_embedded_text(
            py,
            page_number,
            point,
            lines,
            data,
            font_index,
            fontsize,
            color,
            overlay,
            max_font_size,
            max_text_size,
        )
    }

    /// Lay out and draw subset-embedded OpenType text inside a display rectangle.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        rect,
        text,
        font_data,
        font_index,
        fontsize,
        line_height,
        align,
        color,
        overlay,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_embedded_textbox(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        text: &str,
        font_data: Vec<u8>,
        font_index: u32,
        fontsize: f64,
        line_height: f64,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_font_size: Option<usize>,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        validate_font_input(Some(&font_data), max_font_size)?;
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let (pdf_width, pdf_height) = (crop[2] - crop[0], crop[3] - crop[1]);
        let page_size = if matches!(rotation, 90 | 270) {
            (pdf_height, pdf_width)
        } else {
            (pdf_width, pdf_height)
        };
        let rect_array = [rect.0, rect.1, rect.2, rect.3];
        let (generated, spare_height) = py.detach(|| {
            generate::embedded_textbox_page(
                page_size,
                rect_array,
                text,
                font_data,
                font_index,
                fontsize,
                line_height,
                align,
                color,
                max_text_size.map(|_| crate::layout::MAX_GENERATED_TEXT_LINES),
            )
            .map_err(generated_text_err)
        })?;
        let Some(generated) = generated else {
            return Ok(spare_height);
        };
        self.preflight_page_content(page_number)?;
        let generated_doc = Document::load_mem(&generated)
            .map_err(|error| lopdf_err(Some("failed to import generated text"), &error))?;
        let source = Self::from_doc(generated_doc, Some(generated), None, None, None);
        self.show_pdf_page(
            py,
            page_number,
            (0.0, 0.0, page_size.0, page_size.1),
            &source,
            1,
            false,
            overlay,
        )?;
        Ok(spare_height)
    }

    /// Lay out subset-embedded text using bounded OpenType font input from a path.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        rect,
        text,
        path,
        font_index,
        fontsize,
        line_height,
        align,
        color,
        overlay,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_embedded_textbox_file(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        text: &str,
        path: &str,
        font_index: u32,
        fontsize: f64,
        line_height: f64,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_font_size: Option<usize>,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        validate_font_input(None, max_font_size)?;
        let data = py.detach(|| read_font_input(path, max_font_size))?;
        self.insert_embedded_textbox(
            py,
            page_number,
            rect,
            text,
            data,
            font_index,
            fontsize,
            line_height,
            align,
            color,
            overlay,
            max_font_size,
            max_text_size,
        )
    }

    /// Replace text on a one-based page and return the replacement count.
    ///
    /// Prepare bounded, copy-on-write replacement for simply encoded fonts.
    ///
    /// The replacement model follows lopdf `replace_partial_text`, while
    /// Python's docstring documents its limitations and public budgets.
    fn replace_text_on_page(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        search: &str,
        replacement: &str,
        default_char: Option<String>,
        max_size: Option<usize>,
    ) -> PyResult<usize> {
        if search.is_empty() {
            return Err(PyValueError::new_err("search must be at least 1 character"));
        }
        if max_size == Some(0) {
            return Err(PyValueError::new_err(
                "max_size must be a positive integer or None",
            ));
        }
        if default_char
            .as_deref()
            .is_some_and(|value| value.chars().count() != 1)
        {
            return Err(PyValueError::new_err(
                "default_char must contain exactly one character",
            ));
        }
        let input_size = search
            .len()
            .checked_add(replacement.len())
            .and_then(|size| size.checked_add(default_char.as_deref().map_or(0, str::len)))
            .ok_or_else(|| {
                limit_err(
                    "replacement_input_size",
                    "text replacement input size overflow",
                )
            })?;
        if input_size > MAX_TEXT_REPLACEMENT_INPUT_BYTES {
            return Err(limit_err(
                "replacement_input_size",
                format!(
                    "text replacement inputs total {input_size} UTF-8 bytes, exceeding the \
                     {MAX_TEXT_REPLACEMENT_INPUT_BYTES}-byte safety limit"
                ),
            ));
        }

        let page_id = self.page_id(page_number)?;
        let streams = draw::inspect_page_contents(&self.doc, page_id).map_err(PdfError::new_err)?;
        let default_char = default_char.as_deref().unwrap_or("?");
        let replacement_count = py.detach(|| {
            // lopdf font lookup misses direct Resources inherited from a page
            // tree, so prepare the same materialized dictionary we will commit.
            let mut page = resolve_inherited_page_dict(&self.doc, page_id).map_err(to_py_err)?;
            let content_data = match max_size {
                Some(limit) => {
                    let decode_limit = limit.saturating_sub(streams.len());
                    let content = self
                        .doc
                        .get_page_content_with_limit(page_id, decode_limit)
                        .map_err(|error| match error {
                            lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded {
                                ..
                            }) => limit_err(
                                "replacement_output_size",
                                format!(
                                    "page content exceeds the configured text replacement limit of \
                                     {limit} bytes"
                                ),
                            ),
                            _ => {
                                lopdf_err(Some("text replacement content decoding failed"), &error)
                            }
                        })?;
                    if content.len() > limit {
                        return Err(limit_err(
                            "replacement_output_size",
                            format!(
                                "page content is {} bytes, exceeding the configured text \
                                 replacement limit of {limit}",
                                content.len()
                            ),
                        ));
                    }
                    content
                }
                None => self.doc.get_page_content(page_id),
            };

            let prepared = text_replace::prepare(
                &self.doc,
                &page,
                &content_data,
                search,
                replacement,
                default_char,
                max_size,
            )
            .map_err(|error| match error {
                TextReplacementError::Pdf(lopdf::Error::Decompress(
                    DecompressError::MemoryLimitExceeded { .. },
                ))
                | TextReplacementError::OutputSize => limit_err(
                    "replacement_output_size",
                    match max_size {
                        Some(limit) => format!(
                            "text replacement would exceed the configured limit of {limit} bytes"
                        ),
                        None => "text replacement output size overflow".to_owned(),
                    },
                ),
                TextReplacementError::Pdf(error) => {
                    lopdf_err(Some("text replacement failed"), &error)
                }
                TextReplacementError::OperandDepth => PdfError::new_err(
                    "text replacement content exceeds the 64-level operand-depth safety limit",
                ),
                TextReplacementError::TooManyFonts => {
                    PdfError::new_err("text replacement exceeds the 4096-font page safety limit")
                }
            })?;
            let Some((count, encoded)) = prepared else {
                return Ok(0);
            };

            let new_object_number = self
                .doc
                .max_id
                .checked_add(1)
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
            let new_content_id = (new_object_number, 0);
            let mut stream = Stream::new(Dictionary::new(), encoded);
            stream
                .compress()
                .map_err(|error| lopdf_err(Some("text replacement compression failed"), &error))?;
            page.set("Contents", new_content_id);

            // Everything above is fallible. Commit the page-owned stream and
            // resolved page dictionary only after preparation succeeds.
            self.doc
                .objects
                .insert(new_content_id, Object::Stream(stream));
            self.doc.max_id = new_object_number;
            self.doc.objects.insert(page_id, Object::Dictionary(page));
            Ok(count)
        })?;
        if replacement_count > 0 {
            self.isolated_content_pages.remove(&page_id);
            self.invalidate_hayro_pdf();
        }
        Ok(replacement_count)
    }
}
