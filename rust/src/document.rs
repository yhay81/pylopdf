//! Python bindings for `lopdf::Document`.
//!
//! This is a thin type- and error-conversion layer. Python's
//! `pylopdf.Document` provides the ergonomic API.

use std::borrow::Cow;
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::io::{self, Read, Write};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use hayro::hayro_interpret::InterpreterCache;
use hayro::hayro_interpret::font::{FallbackFontQuery, FontData, FontQuery};
use hayro::hayro_interpret::hayro_cmap::CidFamily;
use hayro::hayro_interpret::{InterpreterSettings, InterpreterWarning, TransformExt};
use hayro::hayro_syntax::Pdf;
use hayro::kurbo::Affine;
use hayro::vello_cpu::color::AlphaColor;
use hayro::vello_cpu::peniko::ImageAlphaType;
use hayro::vello_cpu::{RasterizerSettings, RenderContext, Resources, TargetInit};
use hayro::{PixmapSettings, RenderCache, RenderSettings, render_into};
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

use crate::bundled_font_cache::{FontBytes, PackageFace, load_package_font};
use crate::content_guard::{ContentGuard, PageSelection};
use crate::draw::{self, ObjectSink};
use crate::form;
use crate::generate;
use crate::generated_pdf;
use crate::image_compression;
use crate::object_recovery;
use crate::object_stream_work;
use crate::ocr;
use crate::page_cleanup::PageCleanupPlan;
use crate::pixmap::Pixmap;
use crate::prune::{prune_unreachable_objects, reachable_object_ids};
use crate::recovery::{self, XrefRecovery};
use crate::renumber::renumber_objects;
use crate::text_replace::{self, TextReplacementError};
use crate::unallocated::normalize_object_numbers;
use indexed::IndexedDocument;

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

/// Bound named-destination name trees at the scale of large hyperref manuals.
///
/// TOC, link, import, and page-cleanup traversal share these limits so every
/// well-formed tree that resolves can also be cleaned and imported.
pub(crate) const MAX_NAMED_DEST_ENTRIES: usize = 65_536;
pub(crate) const MAX_NAMED_DEST_TREE_NODES: usize = 65_536;
pub(crate) const MAX_NAMED_DEST_TREE_EDGES: usize = 131_072;
pub(crate) const MAX_NAMED_DEST_TREE_DEPTH: usize = 32;
pub(crate) const MAX_NAMED_DEST_NAME_BYTES: usize = 16 * 1024 * 1024;

/// Bound outline traversal and returned TOC metadata for large manuals.
///
/// Page cleanup shares the node and edge limits so every outline that
/// `set_toc` can write remains readable and editable.
const MAX_TOC_ENTRIES: usize = 65_536;
pub(crate) const MAX_TOC_TREE_NODES: usize = 65_536;
pub(crate) const MAX_TOC_TREE_EDGES: usize = 131_072;
const MAX_TOC_TREE_DEPTH: usize = 64;
const MAX_TOC_DEST_DEPTH: usize = 32;
const MAX_TOC_TEXT_BYTES: usize = 16 * 1024 * 1024;

/// Bound generated invisible OCR-layer content per call.
const MAX_OCR_LAYER_WORDS: usize = 4_096;
const MAX_OCR_LAYER_TEXT_BYTES: usize = 1024 * 1024;

/// Bound lopdf's simple-font replacement inputs before encoding work.
const MAX_TEXT_REPLACEMENT_INPUT_BYTES: usize = 4096;

/// Explicit page batches may repeat pages up to the larger of the page count
/// and this floor, so repetition never costs more than one full document pass.
const MIN_PAGE_ENTRY_LIMIT: usize = 4_096;
/// Replace call-scoped hayro caches after this many page entries so fonts and
/// objects parsed during whole-document batches cannot accumulate unbounded.
const PAGE_BATCH_CHUNK: usize = 4_096;
const MAX_PAGE_TREE_DEPTH: usize = 256;
const MAX_PAGE_TREE_REFERENCE_DEPTH: usize = 32;

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

/// Bound and materialize page drawing resources before dependent objects exist.
const MAX_PAGE_RESOURCE_ENTRIES: usize = 4_096;
const MAX_PAGE_RESOURCE_DIRECT_OBJECTS: usize = 8_192;
const MAX_PAGE_RESOURCE_DIRECT_BYTES: usize = 1024 * 1024;
const MAX_PAGE_RESOURCE_REFERENCE_DEPTH: usize = 32;
const MAX_PAGE_RESOURCE_DIRECT_DEPTH: usize = 32;

/// Bound page-tree inheritance and one generated Form XObject import.
const MAX_INHERITED_PAGE_TREE_DEPTH: usize = 64;
const MAX_FORM_IMPORT_OBJECTS: usize = 65_536;
const MAX_FORM_CONTENT_BYTES: usize = 64 * 1024 * 1024;

/// Bound sequential decoder work independently from each layer's byte budget.
const MAX_STREAM_FILTERS: usize = 16;

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

/// Preallocated target for appending one page annotation without later growth.
enum PageAnnotationAppendPlan {
    Direct,
    Indirect(ObjectId),
    Replacement(Vec<Object>),
}

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

/// Page operand of one destination array before it is resolved to a page.
#[derive(Clone, Copy)]
enum DestinationPage {
    Reference(ObjectId),
    /// Some producers write a zero-based page index instead of a reference.
    Index(i64),
    Missing,
}

/// Destination fit types that carry a target point.
#[derive(Clone, Copy)]
enum DestinationFit {
    Xyz,
    /// `/FitH` or `/FitBH`, carrying a top coordinate.
    Horizontal,
    /// `/FitV` or `/FitBV`, carrying a left coordinate.
    Vertical,
    Other,
}

/// A destination array reduced to the operands `read_links` reports.
///
/// Owning these few values lets one name-tree index serve every link read
/// until the next edit, without borrowing the document between calls.
#[derive(Clone, Copy)]
struct LinkDestination {
    page: DestinationPage,
    fit: DestinationFit,
    /// Numeric array elements 2 through 4: the fit's coordinates and zoom.
    numbers: [Option<f64>; 3],
}

impl LinkDestination {
    const UNRESOLVED: Self = Self {
        page: DestinationPage::Missing,
        fit: DestinationFit::Other,
        numbers: [None; 3],
    };

    /// Reduce a dereferenced destination array or `/D` dictionary.
    fn from_object(doc: &Document, object: &Object) -> Self {
        let mut resolved = object;
        // A named destination value may be a dictionary containing `/D`.
        if let Object::Dictionary(dictionary) = resolved {
            match dictionary.get(b"D") {
                Ok(inner) => resolved = deref_object(doc, inner),
                Err(_) => return Self::UNRESOLVED,
            }
        }
        let Object::Array(array) = resolved else {
            return Self::UNRESOLVED;
        };
        // Element 0 should be a page reference, but some producers write a
        // zero-based integer. Keep references for the reverse page lookup.
        let page = match array.first() {
            Some(Object::Reference(id)) => DestinationPage::Reference(*id),
            Some(Object::Integer(index)) => DestinationPage::Index(*index),
            _ => DestinationPage::Missing,
        };
        let fit = match array
            .get(1)
            .and_then(|object| deref_object(doc, object).as_name().ok())
        {
            Some(b"XYZ") => DestinationFit::Xyz,
            Some(b"FitH" | b"FitBH") => DestinationFit::Horizontal,
            Some(b"FitV" | b"FitBV") => DestinationFit::Vertical,
            _ => DestinationFit::Other,
        };
        let number = |index: usize| {
            array
                .get(index)
                .and_then(|object| match deref_object(doc, object) {
                    Object::Integer(value) => Some(*value as f64),
                    Object::Real(value) => Some(f64::from(*value)),
                    _ => None,
                })
        };
        Self {
            page,
            fit,
            numbers: [number(2), number(3), number(4)],
        }
    }
}

/// One distinct name-tree key and its reduced destination.
struct NamedDestinationEntry {
    key_start: u32,
    key_len: u32,
    destination: LinkDestination,
}

impl NamedDestinationEntry {
    fn key<'a>(&self, keys: &'a [u8]) -> &'a [u8] {
        let start = self.key_start as usize;
        &keys[start..start + self.key_len as usize]
    }
}

/// Owned `/Names/Dests` index that link reads reuse until the next edit.
///
/// Distinct keys share one exactly sized buffer that the 16 MiB key budget
/// bounds, and entries are stored in key order for binary search instead of
/// hashing one owned key per name.
#[derive(Default)]
struct NamedDestinationIndex {
    keys: Vec<u8>,
    entries: Vec<NamedDestinationEntry>,
}

impl NamedDestinationIndex {
    /// Index the complete tree under the shared limits, or fail without a
    /// partial index.
    fn build(doc: &Document) -> PyResult<Self> {
        let mut index = Self::default();
        let Some(root) = named_destination_root(doc) else {
            return Ok(index);
        };
        // Borrow every entry first, so the owned index is allocated exactly.
        let mut found = Vec::new();
        visit_named_destinations(doc, root, |key, value| {
            if found.len() == found.capacity() {
                found.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow named-destination index: {error}"))
                })?;
            }
            found.push((key, found.len(), value));
            Ok(())
        })?;
        // An in-place sort tie-broken by traversal order keeps each key's
        // first occurrence, like the borrowed index.
        found.sort_unstable_by(|left, right| left.0.cmp(right.0).then(left.1.cmp(&right.1)));
        found.dedup_by(|later, earlier| later.0 == earlier.0);
        // The walker admits all key text to the 16 MiB budget, so the
        // total and every offset fit in u32.
        let key_bytes = found.iter().map(|(key, _, _)| key.len()).sum();
        index.keys.try_reserve_exact(key_bytes).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate named-destination index keys: {error}"
            ))
        })?;
        index
            .entries
            .try_reserve_exact(found.len())
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate named-destination index: {error}"
                ))
            })?;
        let too_large =
            || PdfError::new_err("named-destination index exceeds the platform size limit");
        for (key, _, value) in found {
            let key_start = u32::try_from(index.keys.len()).map_err(|_| too_large())?;
            let key_len = u32::try_from(key.len()).map_err(|_| too_large())?;
            index.keys.extend_from_slice(key);
            index.entries.push(NamedDestinationEntry {
                key_start,
                key_len,
                destination: LinkDestination::from_object(doc, value),
            });
        }
        Ok(index)
    }

    fn get(&self, name: &[u8]) -> Option<LinkDestination> {
        let position = self
            .entries
            .binary_search_by(|entry| entry.key(&self.keys).cmp(name))
            .ok()?;
        Some(self.entries[position].destination)
    }
}

/// Named-destination lookups shared by link reads.
///
/// `invalidate_hayro_pdf` drops it on every edit, so cached destinations
/// always describe the current document. Page lookups use the document's own
/// page index.
#[derive(Default)]
struct LinkResolutionCache {
    /// Built on the first named lookup, so pages without named links never
    /// walk the tree. A refused build leaves it unset.
    named: Option<NamedDestinationIndex>,
}

impl LinkResolutionCache {
    /// Look up one name-tree destination, indexing the tree on first use.
    fn named_destination(
        &mut self,
        doc: &Document,
        name: &[u8],
    ) -> PyResult<Option<LinkDestination>> {
        if self.named.is_none() {
            self.named = Some(NamedDestinationIndex::build(doc)?);
        }
        Ok(self.named.as_ref().and_then(|index| index.get(name)))
    }
}

/// Info strings, page count, version, encryption, and any cross-reference
/// recovery warning.
type MetadataTuple = (
    HashMap<String, String>,
    u32,
    String,
    bool,
    Option<&'static str>,
);

/// Target geometry for one placed PDF page.
#[derive(Clone, Copy)]
struct PagePlacement {
    rect: [f64; 4],
    keep_proportion: bool,
}

/// Resource category materialized into a page-owned drawing dictionary.
#[derive(Clone, Copy)]
enum PageResourceKind {
    XObject,
    Font,
}

impl PageResourceKind {
    fn key(self) -> &'static [u8] {
        match self {
            Self::XObject => b"XObject",
            Self::Font => b"Font",
        }
    }

    fn name_prefix(self) -> &'static str {
        match self {
            Self::XObject => "PyloX",
            Self::Font => "PyloF",
        }
    }
}

/// Fully materialized page dictionary with one reserved resource-name slot.
struct PageResourcePlan {
    page: Dictionary,
    kind: PageResourceKind,
    name: String,
}

impl PageResourcePlan {
    fn name(&self) -> &str {
        &self.name
    }

    fn bind(mut self, resource_id: ObjectId) -> PyResult<Dictionary> {
        let resources = self
            .page
            .get_mut(b"Resources")
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        let entries = resources
            .get_mut(self.kind.key())
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        let slot = entries.get_mut(self.name.as_bytes()).map_err(to_py_err)?;
        *slot = Object::Reference(resource_id);
        Ok(self.page)
    }
}

/// Prepared page-content and resource commits for one drawing operation.
struct PageDrawingPlan {
    content: draw::ContentAppendPlan,
    resource: PageResourcePlan,
}

/// Source-owned objects and Form stream prepared before target mutation.
struct FormImportPlan {
    objects: Vec<(ObjectId, Object)>,
    form: Stream,
    new_max_id: u32,
    crop: [f64; 4],
    rotation: i64,
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

/// One flattened AcroForm field: name, object, type, flags, and normalized
/// value. Fields that inherit one type or value share it.
type FormFieldEntry = (String, ObjectId, Arc<String>, i64, Option<Arc<str>>);

/// One widget and the raw state names from its normal appearance dictionary.
type WidgetStateNames = (ObjectId, Vec<Vec<u8>>);

/// Resolve one reference level, returning the original object on failure.
fn deref_object<'a>(doc: &'a Document, obj: &'a Object) -> &'a Object {
    match obj {
        Object::Reference(id) => doc.get_object(*id).unwrap_or(obj),
        other => other,
    }
}

/// Resolve an indirect object, reading an indirect null and a missing object
/// alike as absent.
///
/// ISO 32000-2 §7.3.7 treats a null dictionary value as an omitted key, and
/// §7.3.10 reads a reference to a missing object as null. lopdf's error for a
/// cyclic or over-long reference chain still surfaces.
fn non_null_object(doc: &Document, id: ObjectId) -> lopdf::Result<Option<&Object>> {
    match doc.get_object(id) {
        Ok(Object::Null) | Err(lopdf::Error::ObjectNotFound(_)) => Ok(None),
        Ok(object) => Ok(Some(object)),
        Err(error) => Err(error),
    }
}

/// Resolve a value, reading null, an indirect null, and a reference to a
/// missing object alike as absent, as [`non_null_object`] does.
fn non_null_value<'a>(doc: &'a Document, object: &'a Object) -> lopdf::Result<Option<&'a Object>> {
    match object {
        Object::Reference(id) => non_null_object(doc, *id),
        Object::Null => Ok(None),
        other => Ok(Some(other)),
    }
}

/// Visit a named-destination tree with complete node-local safety budgets.
fn visit_named_destinations<'a>(
    doc: &'a Document,
    root: &'a Object,
    mut visitor: impl FnMut(&'a [u8], &'a Object) -> PyResult<()>,
) -> PyResult<()> {
    let mut stack = Vec::new();
    stack.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate named-destination tree stack: {error}"
        ))
    })?;
    stack.push((root, 1usize));
    let mut visited = HashSet::new();
    let mut nodes = 0usize;
    let mut edges = 0usize;
    let mut entries = 0usize;
    let mut name_bytes = 0usize;
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object {
            if visited.contains(id) {
                continue;
            }
            visited.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow named-destination cycle detection: {error}"
                ))
            })?;
            visited.insert(*id);
        }
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes = nodes.saturating_add(1);
        if nodes > MAX_NAMED_DEST_TREE_NODES {
            return Err(named_destination_count_err(format!(
                "named-destination tree exceeds the {MAX_NAMED_DEST_TREE_NODES}-node safety limit"
            )));
        }
        if let Ok(names_object) = node.get(b"Names")
            && let Ok(names) = deref_object(doc, names_object).as_array()
        {
            entries = entries.saturating_add(names.len().div_ceil(2));
            if entries > MAX_NAMED_DEST_ENTRIES {
                return Err(named_destination_count_err(format!(
                    "named-destination tree exceeds the {MAX_NAMED_DEST_ENTRIES}-entry safety limit"
                )));
            }
            for pair in names.chunks(2) {
                let [key, value] = pair else { continue };
                let Ok(key) = deref_object(doc, key).as_str() else {
                    continue;
                };
                add_named_destination_text_budget(&mut name_bytes, key.len())?;
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
            edges = edges.saturating_add(kids.len());
            if edges > MAX_NAMED_DEST_TREE_EDGES {
                return Err(named_destination_count_err(format!(
                    "named-destination tree exceeds the {MAX_NAMED_DEST_TREE_EDGES}-edge safety limit"
                )));
            }
            stack.try_reserve(kids.len()).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow named-destination tree stack: {error}"
                ))
            })?;
            for kid in kids.iter().rev() {
                stack.push((kid, depth + 1));
            }
        }
    }
    Ok(())
}

/// Return the root of the catalog `/Names/Dests` tree, if any.
fn named_destination_root(doc: &Document) -> Option<&Object> {
    doc.catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Names").ok())
        .map(|names| deref_object(doc, names))
        .and_then(|names| names.as_dict().ok())
        .and_then(|names| names.get(b"Dests").ok())
}

/// Build one borrowed index so TOC resolution scans a name tree at most once.
fn named_destination_index(doc: &Document) -> PyResult<HashMap<&[u8], &Object>> {
    let mut index = HashMap::new();
    let Some(root) = named_destination_root(doc) else {
        return Ok(index);
    };
    visit_named_destinations(doc, root, |key, value| {
        if !index.contains_key(key) {
            index.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to grow named-destination index: {error}"))
            })?;
            index.insert(key, value);
        }
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

/// Report one named-destination count beyond the shared large-manual boundary.
pub(crate) fn named_destination_count_err(message: String) -> PyErr {
    limit_err("named_destination_count", message)
}

/// Charge named-destination key or lookup text without overflow or partial output.
pub(crate) fn add_named_destination_text_budget(total: &mut usize, amount: usize) -> PyResult<()> {
    *total = total.saturating_add(amount);
    if *total > MAX_NAMED_DEST_NAME_BYTES {
        return Err(limit_err(
            "named_destination_text_size",
            format!(
                "named-destination text exceeds the {MAX_NAMED_DEST_NAME_BYTES}-byte safety limit"
            ),
        ));
    }
    Ok(())
}

/// The budget charged for destination names looked up during one traversal.
///
/// A name costs the budget of the structure that contains it, so a large
/// outline or name tree is never limited by one page's 1 MiB link budget.
#[derive(Clone, Copy)]
pub(crate) enum DestinationNameBudget {
    /// One page's links share its 1 MiB annotation metadata budget.
    Annotation,
    /// Outline walks share the 16 MiB TOC source-text budget.
    Toc,
    /// Name-tree rewrites share the 16 MiB named-destination key budget.
    NamedDestination,
}

impl DestinationNameBudget {
    fn charge(self, total: &mut usize, amount: usize) -> PyResult<()> {
        match self {
            Self::Annotation => add_annotation_budget(total, amount, "local destination text"),
            Self::Toc => add_toc_text_budget(total, amount, "destination text"),
            Self::NamedDestination => add_named_destination_text_budget(total, amount),
        }
    }
}

/// Resolve a local destination without copying its source object graph.
pub(crate) fn resolve_local_destination_array<'a>(
    doc: &'a Document,
    destination: &'a Object,
    named_destinations: &mut Option<HashMap<&'a [u8], &'a Object>>,
    source_bytes: &mut usize,
    budget: DestinationNameBudget,
) -> PyResult<Option<&'a [Object]>> {
    let mut current = destination;
    let mut visited = Vec::new();
    visited
        .try_reserve_exact(MAX_TOC_DEST_DEPTH)
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to inspect local destination references: {error}"
            ))
        })?;
    for _ in 0..MAX_TOC_DEST_DEPTH {
        match current {
            Object::Reference(id) => {
                if visited.contains(id) {
                    return Ok(None);
                }
                visited.push(*id);
                let Some(object) = doc.objects.get(id) else {
                    return Ok(None);
                };
                current = object;
            }
            Object::Name(name) | Object::String(name, _) => {
                budget.charge(source_bytes, name.len())?;
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
            Object::Array(array) => return Ok(Some(array)),
            _ => return Ok(None),
        }
    }
    Err(PdfError::new_err(format!(
        "local destination exceeds the {MAX_TOC_DEST_DEPTH}-level safety limit"
    )))
}

/// Charge encoded or returned TOC text without overflow or partial output.
fn add_toc_text_budget(total: &mut usize, amount: usize, label: &str) -> PyResult<()> {
    *total = total
        .checked_add(amount)
        .ok_or_else(|| limit_err("toc_text_size", "TOC text exceeds the platform size limit"))?;
    if *total > MAX_TOC_TEXT_BYTES {
        return Err(limit_err(
            "toc_text_size",
            format!("TOC {label} exceeds the {MAX_TOC_TEXT_BYTES}-byte safety limit"),
        ));
    }
    Ok(())
}

/// Report one TOC outline count beyond the shared large-manual boundary.
pub(crate) fn toc_entry_count_err(message: String) -> PyErr {
    limit_err("toc_entry_count", message)
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
    page_map: &HashMap<ObjectId, u32>,
    named_destinations: &mut Option<HashMap<&'a [u8], &'a Object>>,
    source_bytes: &mut usize,
) -> PyResult<Option<u32>> {
    let mut current = destination;
    let mut visited = HashSet::new();
    for _ in 0..MAX_TOC_DEST_DEPTH {
        match current {
            Object::Reference(id) => {
                if visited.contains(id) {
                    return Ok(None);
                }
                visited.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to grow TOC destination cycle detection: {error}"
                    ))
                })?;
                visited.insert(*id);
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
fn collect_toc(doc: &IndexedDocument) -> PyResult<Vec<TocEntry>> {
    let Some(outlines_object) = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Outlines").ok())
    else {
        return Ok(Vec::new());
    };
    // Page cleanup reads a null or missing outline root as absent as well.
    let outlines = match non_null_value(doc, outlines_object) {
        Ok(None) => return Ok(Vec::new()),
        Ok(Some(Object::Dictionary(outlines))) => outlines,
        _ => return Err(PdfError::new_err("catalog Outlines is not a dictionary")),
    };
    let (start, mut edges) = match outlines.get(b"First") {
        Ok(first) => (first, 1usize),
        Err(_) => (outlines_object, 0usize),
    };
    let page_map = doc.page_numbers()?;
    let mut named_destinations = None;
    let mut stack = Vec::new();
    stack.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!("failed to allocate TOC outline stack: {error}"))
    })?;
    stack.push((start, 1usize));
    let mut visited = HashSet::new();
    let mut nodes = 0usize;
    let mut source_bytes = 0usize;
    let mut returned_bytes = 0usize;
    let mut out = Vec::new();
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object {
            if visited.contains(id) {
                continue;
            }
            visited.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow TOC outline cycle detection: {error}"
                ))
            })?;
            visited.insert(*id);
        }
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes = nodes
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("TOC outline tree exceeds the platform size limit"))?;
        if nodes > MAX_TOC_TREE_NODES {
            return Err(toc_entry_count_err(format!(
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
                page_map,
                &mut named_destinations,
                &mut source_bytes,
            )?
        {
            if out.len() >= MAX_TOC_ENTRIES {
                return Err(toc_entry_count_err(format!(
                    "TOC exceeds the {MAX_TOC_ENTRIES}-entry safety limit"
                )));
            }
            if out.len() == out.capacity() {
                out.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow returned TOC entries: {error}"))
                })?;
            }
            out.push((
                u32::try_from(depth).map_err(|error| PdfError::new_err(error.to_string()))?,
                title,
                page,
            ));
        }

        if let Ok(next) = node.get(b"Next") {
            edges = edges.checked_add(1).ok_or_else(|| {
                PdfError::new_err("TOC outline tree exceeds the platform size limit")
            })?;
            if edges > MAX_TOC_TREE_EDGES {
                return Err(toc_entry_count_err(format!(
                    "TOC outline tree exceeds the {MAX_TOC_TREE_EDGES}-edge safety limit"
                )));
            }
            if stack.len() == stack.capacity() {
                stack.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow TOC outline stack: {error}"))
                })?;
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
                return Err(toc_entry_count_err(format!(
                    "TOC outline tree exceeds the {MAX_TOC_TREE_EDGES}-edge safety limit"
                )));
            }
            if stack.len() == stack.capacity() {
                stack.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow TOC outline stack: {error}"))
                })?;
            }
            stack.push((first, depth + 1));
        }
    }
    Ok(out)
}

/// How one field-tree traversal node takes part in the walk.
#[derive(Clone, Copy)]
enum FieldRole {
    /// A `/Fields` entry or a named kid, which can be a returned leaf.
    Field,
    /// A nameless intermediate field folded into the nearest field above it,
    /// whose reserved leaf entry, if any, is at this index.
    Folded(Option<usize>),
}

/// One field-tree traversal node: object, prefix, inherited FT/Ff/value,
/// depth, and role.
///
/// The prefix and inherited text are shared with the node that pushed it.
/// Only the first visit of a kid is charged to the text budgets, while
/// `/Kids` can push it once per edge, so a copy per push would let a long
/// name or type grow with the edge count. Names and types are `Arc<String>`
/// so that their fallibly allocated text moves in instead of being copied
/// into an `Arc<str>`.
type FieldNode = (
    ObjectId,
    Arc<String>,
    Option<Arc<String>>,
    i64,
    Option<Arc<str>>,
    usize,
    FieldRole,
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

/// Bound concurrent pixmap plus straight-alpha conversion buffers to ~512 MB.
#[cfg(not(target_os = "emscripten"))]
const MAX_PARALLEL_RENDER_BYTES: u64 = 512_000_000;
#[cfg(not(target_os = "emscripten"))]
const ESTIMATED_RENDER_BYTES_PER_PIXEL: u64 = 8;

/// Display a lopdf error followed by its wrapped causes.
///
/// lopdf 0.45 keeps causes out of `Display` and exposes them through
/// `Error::source`, so join the chain to retain limit and decoder details.
pub(crate) struct LopdfErrorChain<'a>(pub(crate) &'a lopdf::Error);

impl std::fmt::Display for LopdfErrorChain<'_> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.0)?;
        let mut source = std::error::Error::source(self.0);
        while let Some(cause) = source {
            write!(formatter, ": {cause}")?;
            source = cause.source();
        }
        Ok(())
    }
}

/// Convert a lopdf error to a Python exception with a context prefix.
///
/// Password/decryption failures become PasswordError; others become PdfError.
fn lopdf_err(prefix: Option<&str>, e: &lopdf::Error) -> PyErr {
    let chain = LopdfErrorChain(e);
    let message = match prefix {
        Some(p) => format!("{p}: {chain}"),
        None => chain.to_string(),
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
        let chain = LopdfErrorChain(error);
        let message = match prefix {
            Some(prefix) => format!("{prefix}: {chain}"),
            None => chain.to_string(),
        };
        limit_err(limit_code, message)
    } else {
        lopdf_err(prefix, error)
    }
}

/// Convert a lenient-load failure, including a refused recovery scan.
fn recovery_load_err(
    prefix: Option<&str>,
    error: &recovery::LoadError,
    limit_code: &'static str,
) -> PyErr {
    match error {
        recovery::LoadError::Pdf(error) => load_err(prefix, error, limit_code),
        recovery::LoadError::RecoveryScan { work, budget } => {
            let message = format!(
                "cross-reference recovery could scan about {work} bytes, exceeding the \
                 {budget}-byte linear safety limit for this input"
            );
            limit_err(
                "recovery_scan_size",
                match prefix {
                    Some(prefix) => format!("{prefix}: {message}"),
                    None => message,
                },
            )
        }
    }
}

/// Recover skipped objects and decide whether hayro may read the original bytes.
///
/// Encrypted input and input whose cross-reference data lopdf recovered never
/// keep their original bytes: hayro would decrypt or recover them on its own
/// and could resolve different objects. `object_recovery` decides the rest.
fn original_bytes_match(
    doc: &mut Document,
    recovery: Option<XrefRecovery>,
    data: &[u8],
) -> PyResult<bool> {
    if recovery.is_some() || doc.is_encrypted() || doc.was_encrypted() {
        return Ok(false);
    }
    object_recovery::reconcile_loaded_objects(doc, data)
}

/// Construct a machine-readable resource-limit exception.
///
/// The first exception argument is a stable code; Python supplies a friendly
/// `str(error)` and `error.code` view over the two arguments.
pub(crate) fn limit_err(code: &'static str, message: impl Into<String>) -> PyErr {
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
    read_bounded_file(path, max_size, limit_code, size_label, error_prefix).map(|(data, _)| data)
}

/// Read a bounded path and return the metadata of the opened file.
pub(crate) fn read_bounded_file(
    path: &str,
    max_size: Option<usize>,
    limit_code: &'static str,
    size_label: &str,
    error_prefix: &str,
) -> PyResult<(Vec<u8>, std::fs::Metadata)> {
    let io_error = |error| PdfError::new_err(format!("{error_prefix} {path}: {error}"));
    let file = std::fs::File::open(path).map_err(io_error)?;
    let metadata = file.metadata().map_err(io_error)?;
    validate_file_size(metadata.len(), max_size, limit_code, size_label)?;
    let read_limit = max_size.map(|limit| limit.saturating_add(1));
    let initial_capacity = usize::try_from(metadata.len())
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
    Ok((data, metadata))
}

/// Reject a file whose reported size already exceeds a caller's budget.
pub(crate) fn validate_file_size(
    size: u64,
    max_size: Option<usize>,
    limit_code: &'static str,
    size_label: &str,
) -> PyResult<()> {
    if let Some(limit) = max_size
        && size > limit as u64
    {
        return Err(limit_err(
            limit_code,
            format!("{size_label} is {size} bytes, exceeding the {limit}-byte limit"),
        ));
    }
    Ok(())
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
    read_font_file(path, max_font_size).map(|(data, _)| data)
}

/// Read bounded OpenType font input and return the metadata of the opened file.
pub(crate) fn read_font_file(
    path: &str,
    max_font_size: Option<usize>,
) -> PyResult<(Vec<u8>, std::fs::Metadata)> {
    read_bounded_file(
        path,
        max_font_size,
        "font_input_size",
        "font input",
        "failed to load font",
    )
}

/// Load generation font input from a path without holding the GIL.
///
/// A face Python selected from an installed font package shares its bytes
/// through `bundled_font_cache`; explicit font paths are read on every call.
/// Both apply the same `font_input_size` boundary before using any bytes.
fn load_generation_font(
    py: Python<'_>,
    path: &str,
    package_font: Option<(String, String)>,
    max_font_size: Option<usize>,
) -> PyResult<FontBytes> {
    validate_font_input(None, max_font_size)?;
    let face = package_font
        .map(|(language, kind)| PackageFace::parse(&language, &kind))
        .transpose()?;
    py.detach(|| match face {
        Some(face) => load_package_font(face, path, max_font_size),
        None => read_font_input(path, max_font_size).map(Arc::new),
    })
}

/// Inspect a font path without reading it, failing as a bounded read would
/// for a missing file or a reported size above the font boundary.
pub(crate) fn font_file_metadata(
    path: &str,
    max_font_size: Option<usize>,
) -> PyResult<std::fs::Metadata> {
    let metadata = std::fs::metadata(path)
        .map_err(|error| PdfError::new_err(format!("failed to load font {path}: {error}")))?;
    validate_file_size(
        metadata.len(),
        max_font_size,
        "font_input_size",
        "font input",
    )?;
    Ok(metadata)
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

fn try_clone_info_metadata_text(value: &str, label: &str) -> PyResult<String> {
    let mut owned = String::new();
    owned.try_reserve_exact(value.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate Info metadata {label}: {error}"))
    })?;
    owned.push_str(value);
    Ok(owned)
}

fn try_info_metadata_text_string(value: &str) -> PyResult<Object> {
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
fn info_dictionary(doc: &Document) -> Option<&Dictionary> {
    match doc.trailer.get(b"Info").ok()? {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok(),
        Object::Dictionary(dictionary) => Some(dictionary),
        _ => None,
    }
}

/// Decode only the eight public standard Info fields under aggregate budgets.
fn collect_info_metadata(doc: &Document) -> PyResult<HashMap<String, String>> {
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
        let Object::String(encoded, _) = resolved else {
            continue;
        };
        add_info_metadata_budget(&mut source_bytes, encoded.len(), "source text")?;
        let Ok(text) = decode_text_string(resolved) else {
            continue;
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
fn pdf_metadata_to_tuple(
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
    // before materializing Python-facing strings.
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

/// Write one PDF while restoring lopdf's ephemeral xref writer state.
///
/// lopdf materializes neither object streams nor the final xref stream in
/// `Document::objects`, but its writer still advances `max_id` for them and
/// rewrites the trailer dictionary. Leaving those changes behind makes each
/// otherwise identical save choose new object IDs. Save options may
/// intentionally raise the PDF version and change the xref type; those fields
/// are outside this snapshot and retain their existing mutation semantics.
fn write_pdf_stably<W: Write>(
    document: &mut Document,
    target: &mut W,
    options: Option<SaveOptions>,
) -> std::io::Result<()> {
    let max_id = document.max_id;
    let trailer = document.trailer.clone();
    let result = match options {
        Some(options) => document.save_with_options(target, options),
        None => document.save_to(target),
    };
    document.max_id = max_id;
    document.trailer = trailer;
    result
}

/// Stream one PDF file through the stable writer-state boundary.
fn write_pdf_file_stably(
    document: &mut Document,
    path: &str,
    options: Option<SaveOptions>,
) -> std::io::Result<()> {
    let file = std::fs::File::create(path)?;
    let mut writer = std::io::BufWriter::new(file);
    write_pdf_stably(document, &mut writer, options)?;
    writer
        .into_inner()
        .map(|_| ())
        .map_err(std::io::IntoInnerError::into_error)
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
    let result = write_pdf_stably(document, &mut output, options);
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

/// Treat null page attributes as omitted without bypassing malformed-value checks.
fn page_attribute_has_value(doc: &Document, dictionary: &Dictionary, key: &[u8]) -> bool {
    let Ok(mut value) = dictionary.get(key) else {
        return false;
    };
    // A null dictionary value is equivalent to an omitted key, including an
    // indirect null and a reference to a missing object, which ISO 32000-2
    // §7.3.10 reads as null. Import renumbering turns such references into
    // null, so imported, flattened, and read pages inherit the same value.
    // Leave over-deep reference chains to the existing readers.
    for _ in 0..MAX_PAGE_RESOURCE_REFERENCE_DEPTH {
        match value {
            Object::Null => return false,
            Object::Reference(id) => match doc.objects.get(id) {
                Some(object) => value = object,
                None => return false,
            },
            _ => return true,
        }
    }
    !matches!(value, Object::Null)
}

/// Resolve one dictionary of a page-attribute inheritance walk.
///
/// A `/Parent` that names a missing object or an indirect null ends
/// inheritance, as a null `/Parent` does.
fn inheritance_node(doc: &Document, id: ObjectId) -> PyResult<Option<&Dictionary>> {
    non_null_object(doc, id)
        .and_then(|node| node.map(Object::as_dict).transpose())
        .map_err(to_py_err)
}

/// Materialize inherited page attributes before discarding the source page tree.
fn resolve_inherited_page_dict(doc: &Document, page_id: ObjectId) -> PyResult<Dictionary> {
    let page = doc
        .get_object(page_id)
        .and_then(Object::as_dict)
        .map_err(to_py_err)?;
    let mut dict = page.clone();
    let mut missing = INHERITABLE_PAGE_KEYS.map(|key| !page_attribute_has_value(doc, &dict, key));
    if !missing.iter().any(|&value| value) {
        return Ok(dict);
    }
    let mut visited = Vec::new();
    visited
        .try_reserve_exact(MAX_INHERITED_PAGE_TREE_DEPTH)
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to inspect inherited page attributes: {error}"
            ))
        })?;
    visited.push(page_id);
    let mut parent = page.get(b"Parent").and_then(Object::as_reference).ok();
    while let Some(parent_id) = parent {
        if visited.contains(&parent_id) {
            return Err(PdfError::new_err(
                "page inheritance tree contains a reference cycle",
            ));
        }
        if visited.len() >= MAX_INHERITED_PAGE_TREE_DEPTH {
            return Err(PdfError::new_err(format!(
                "page inheritance tree exceeds the {MAX_INHERITED_PAGE_TREE_DEPTH}-level safety limit"
            )));
        }
        visited.push(parent_id);
        let Some(parent_dict) = inheritance_node(doc, parent_id)? else {
            break;
        };
        for (index, key) in INHERITABLE_PAGE_KEYS.iter().enumerate() {
            if missing[index]
                && page_attribute_has_value(doc, parent_dict, key)
                && let Ok(value) = parent_dict.get(key)
            {
                dict.set(*key, value.clone());
                missing[index] = false;
            }
        }
        if !missing.iter().any(|&value| value) {
            break;
        }
        parent = parent_dict
            .get(b"Parent")
            .and_then(Object::as_reference)
            .ok();
    }
    // Reparenting must retain source defaults instead of inheriting values
    // from a destination tree with a different crop, rotation, or resources.
    if !page_attribute_has_value(doc, &dict, b"Resources") {
        dict.set("Resources", Dictionary::new());
    }
    if !page_attribute_has_value(doc, &dict, b"Rotate") {
        dict.set("Rotate", 0);
    }
    if !page_attribute_has_value(doc, &dict, b"CropBox")
        && page_attribute_has_value(doc, &dict, b"MediaBox")
        && let Ok(media_box) = dict.get(b"MediaBox")
    {
        dict.set("CropBox", media_box.clone());
    }
    Ok(dict)
}

/// Replace root Pages Kids/Count with `ordered`, flattening the tree.
///
/// Materialize inheritance on every page and point Parent to root. Every
/// fallible step runs before the first change. Callers prune obsolete
/// intermediate nodes when the result is saved.
fn rebuild_flat_page_tree(
    doc: &mut Document,
    pages_id: ObjectId,
    ordered: &[ObjectId],
    cleanup: Option<&PageCleanupPlan>,
) -> PyResult<()> {
    doc.get_object(pages_id)
        .and_then(Object::as_dict)
        .map_err(to_py_err)?;
    let count = i64::try_from(ordered.len()).map_err(|e| PdfError::new_err(e.to_string()))?;
    let mut kids = Vec::new();
    kids.try_reserve_exact(ordered.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate rebuilt page-tree Kids: {error}"
        ))
    })?;
    let mut resolved_pages = Vec::new();
    resolved_pages
        .try_reserve_exact(ordered.len())
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate rebuilt page dictionaries: {error}"
            ))
        })?;
    for &page_id in ordered {
        let mut dict = resolve_inherited_page_dict(doc, page_id)?;
        if let Some(cleanup) = cleanup {
            cleanup.clean_page(page_id, &mut dict);
        }
        dict.set("Parent", pages_id);
        kids.push(Object::Reference(page_id));
        resolved_pages.push((page_id, dict));
    }
    for (page_id, dict) in resolved_pages {
        doc.objects.insert(page_id, Object::Dictionary(dict));
    }
    let pages_dict = doc
        .get_object_mut(pages_id)
        .and_then(Object::as_dict_mut)
        .map_err(to_py_err)?;
    pages_dict.set("Kids", kids);
    pages_dict.set("Count", count);
    Ok(())
}

/// Return the root page-tree node that the catalog names.
fn page_tree_root(doc: &Document) -> PyResult<ObjectId> {
    doc.catalog()
        .map_err(to_py_err)?
        .get(b"Pages")
        .and_then(Object::as_reference)
        .map_err(|_| PdfError::new_err("Catalog Pages must be an indirect page-tree node"))
}

/// Whether hayro resolved exactly the walker's pages, in page order.
fn hayro_pages_match(pdf: &Pdf, page_ids: &[ObjectId]) -> bool {
    let pages = pdf.pages();
    pages.len() == page_ids.len()
        && pages
            .iter()
            .zip(page_ids)
            .all(|(page, &(number, generation))| {
                page.raw().obj_id().is_some_and(|id| {
                    i64::from(id.obj_number) == i64::from(number)
                        && i64::from(id.gen_number) == i64::from(generation)
                })
            })
}

/// Borrow an imported annotation operand under a bounded reference walk.
fn resolve_import_object<'a>(
    doc: &'a Document,
    object: &'a Object,
) -> PyResult<Option<&'a Object>> {
    let mut current = object;
    let mut visited = Vec::new();
    visited
        .try_reserve_exact(MAX_TOC_DEST_DEPTH)
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to inspect imported link references: {error}"
            ))
        })?;
    for _ in 0..MAX_TOC_DEST_DEPTH {
        let Object::Reference(id) = current else {
            return Ok(Some(current));
        };
        if visited.contains(id) {
            return Ok(None);
        }
        visited.push(*id);
        let Some(object) = doc.objects.get(id) else {
            return Ok(None);
        };
        current = object;
    }
    Err(PdfError::new_err(format!(
        "imported link references exceed the {MAX_TOC_DEST_DEPTH}-level safety limit"
    )))
}

/// Copy only the scalar operands of a valid, imported local destination.
fn imported_destination(
    doc: &Document,
    array: &[Object],
    source_pages: &[ObjectId],
    imported_pages: &HashSet<ObjectId>,
) -> PyResult<Option<Object>> {
    // The longest explicit PDF destination is /FitR: page, mode, and four reals.
    if array.len() > 6 {
        return Err(PdfError::new_err(
            "imported link destination exceeds the 6-item safety limit",
        ));
    }
    let page_id = match array.first() {
        Some(Object::Reference(id)) => *id,
        Some(Object::Integer(index)) => {
            let Some(id) = usize::try_from(*index)
                .ok()
                .and_then(|index| source_pages.get(index))
            else {
                return Ok(None);
            };
            *id
        }
        _ => return Ok(None),
    };
    if !imported_pages.contains(&page_id) {
        return Ok(None);
    }
    let Some(mode) = array.get(1) else {
        return Ok(None);
    };
    let Some(mode @ Object::Name(name)) = resolve_import_object(doc, mode)? else {
        return Ok(None);
    };
    if !matches!(
        name.as_slice(),
        b"XYZ" | b"Fit" | b"FitH" | b"FitV" | b"FitR" | b"FitB" | b"FitBH" | b"FitBV"
    ) {
        return Ok(None);
    }
    let mut copied = Vec::new();
    copied.try_reserve_exact(array.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate imported link destination: {error}"
        ))
    })?;
    copied.push(Object::Reference(page_id));
    copied.push(mode.clone());
    for operand in &array[2..] {
        let Some(operand) = resolve_import_object(doc, operand)? else {
            return Ok(None);
        };
        match operand {
            Object::Null | Object::Integer(_) => copied.push(operand.clone()),
            Object::Real(value) if value.is_finite() => copied.push(operand.clone()),
            _ => return Ok(None),
        }
    }
    Ok(Some(Object::Array(copied)))
}

/// Prepare source-local links before the source Catalog and page tree disappear.
fn prepare_imported_page_links<'a>(
    doc: &'a Document,
    page_id: ObjectId,
    source_pages: &[ObjectId],
    imported_pages: &HashSet<ObjectId>,
    named_destinations: &mut Option<HashMap<&'a [u8], &'a Object>>,
    annotation_edits: &mut HashMap<ObjectId, Option<Dictionary>>,
) -> PyResult<Option<Vec<Object>>> {
    let page = doc
        .get_object(page_id)
        .and_then(Object::as_dict)
        .map_err(to_py_err)?;
    let Ok(annots) = page.get(b"Annots") else {
        return Ok(None);
    };
    let Some(Object::Array(items)) = resolve_import_object(doc, annots)? else {
        return Ok(None);
    };
    if items.len() > MAX_PAGE_ANNOTATIONS {
        return Err(PdfError::new_err(format!(
            "page annotations exceed the {MAX_PAGE_ANNOTATIONS}-entry safety limit"
        )));
    }
    let mut retained = Vec::new();
    retained.try_reserve_exact(items.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate imported page annotations: {error}"
        ))
    })?;
    let mut source_bytes = 0usize;
    for item in items {
        if let Object::Reference(id) = item
            && let Some(prepared) = annotation_edits.get(id)
        {
            if prepared.is_some() {
                retained.push(item.clone());
            }
            continue;
        }
        let Some(Object::Dictionary(annotation)) = resolve_import_object(doc, item)? else {
            retained.push(item.clone());
            continue;
        };
        if matches!(
            annotation.get_type().ok(),
            Some(b"Page" | b"Pages" | b"Catalog")
        ) {
            return Err(PdfError::new_err(
                "page annotation cannot alias a page or catalog dictionary",
            ));
        }
        if !matches!(
            annotation.get(b"Subtype").and_then(Object::as_name),
            Ok(b"Link")
        ) {
            retained.push(item.clone());
            continue;
        }
        let action = match annotation.get(b"A") {
            Ok(action) => {
                resolve_import_object(doc, action)?.and_then(|object| object.as_dict().ok())
            }
            Err(_) => None,
        };
        let destination = match action {
            Some(action) if matches!(action.get(b"S").and_then(Object::as_name), Ok(b"GoTo")) => {
                action.get(b"D").ok()
            }
            Some(_) => {
                retained.push(item.clone());
                continue;
            }
            None => annotation.get(b"Dest").ok(),
        };
        let Some(destination) = destination else {
            retained.push(item.clone());
            continue;
        };
        let destination = match resolve_local_destination_array(
            doc,
            destination,
            named_destinations,
            &mut source_bytes,
            DestinationNameBudget::Annotation,
        )? {
            Some(array) => imported_destination(doc, array, source_pages, imported_pages)?,
            None => None,
        };
        let prepared = destination.map(|destination| {
            let mut prepared = annotation.clone();
            if let Some(action) = action {
                let mut action = action.clone();
                action.set("D", destination);
                prepared.set("A", action);
                prepared.remove(b"Dest");
            } else {
                prepared.set("Dest", destination);
                prepared.remove(b"A");
            }
            prepared
        });
        if let Object::Reference(id) = item {
            if prepared.is_some() {
                retained.push(item.clone());
            }
            annotation_edits.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to allocate imported link edits: {error}"))
            })?;
            annotation_edits.insert(*id, prepared);
        } else if let Some(prepared) = prepared {
            retained.push(Object::Dictionary(prepared));
        }
    }
    Ok(Some(retained))
}

/// Resolve page `/Resources` or one of its categories under a bounded chain.
///
/// A null value, an indirect null, and a reference to a missing object read
/// as an absent entry, which the caller starts empty.
fn resolve_page_resource_dictionary<'a>(
    doc: &'a Document,
    object: &'a Object,
    label: &str,
) -> PyResult<Option<&'a Dictionary>> {
    let mut current = object;
    let mut visited = Vec::new();
    visited
        .try_reserve_exact(MAX_PAGE_RESOURCE_REFERENCE_DEPTH)
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to inspect {label} reference chain: {error}"
            ))
        })?;
    loop {
        let Object::Reference(id) = current else {
            return match current {
                Object::Null => Ok(None),
                Object::Dictionary(dictionary) => Ok(Some(dictionary)),
                _ => Err(PdfError::new_err(format!(
                    "{label} must resolve to a dictionary"
                ))),
            };
        };
        if visited.contains(id) {
            return Err(PdfError::new_err(format!(
                "{label} contains a reference cycle"
            )));
        }
        if visited.len() >= MAX_PAGE_RESOURCE_REFERENCE_DEPTH {
            return Err(PdfError::new_err(format!(
                "{label} exceeds the {MAX_PAGE_RESOURCE_REFERENCE_DEPTH}-reference-depth safety limit"
            )));
        }
        visited.push(*id);
        let Some(object) = doc.objects.get(id) else {
            return Ok(None);
        };
        current = object;
    }
}

fn page_resource_shape_error(label: &str, detail: &str, limit: usize) -> PyErr {
    PdfError::new_err(format!("{label} exceeds the {limit}-{detail} safety limit"))
}

/// Bound direct resource shapes before cloning them into a page-owned dictionary.
///
/// Indirect references are leaves; their target dictionaries are validated
/// separately when selected as Resources or the requested resource category.
fn validate_page_resource_shape(dict: &Dictionary, label: &str) -> PyResult<()> {
    if dict.len() > MAX_PAGE_RESOURCE_ENTRIES {
        return Err(page_resource_shape_error(
            label,
            "entry",
            MAX_PAGE_RESOURCE_ENTRIES,
        ));
    }
    let mut pending = Vec::new();
    pending.try_reserve_exact(dict.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate {label} direct-object validation stack: {error}"
        ))
    })?;
    for (_, value) in dict.iter() {
        pending.push((value, 2usize));
    }
    let mut objects = 1usize;
    let mut bytes = 0usize;
    for (key, _) in dict.iter() {
        bytes = bytes.checked_add(key.len()).ok_or_else(|| {
            PdfError::new_err(format!(
                "{label} direct data exceeds the platform size limit"
            ))
        })?;
    }
    while let Some((object, depth)) = pending.pop() {
        objects = objects.checked_add(1).ok_or_else(|| {
            PdfError::new_err(format!(
                "{label} direct objects exceed the platform size limit"
            ))
        })?;
        if objects > MAX_PAGE_RESOURCE_DIRECT_OBJECTS {
            return Err(page_resource_shape_error(
                label,
                "object",
                MAX_PAGE_RESOURCE_DIRECT_OBJECTS,
            ));
        }
        if depth > MAX_PAGE_RESOURCE_DIRECT_DEPTH {
            return Err(page_resource_shape_error(
                label,
                "level",
                MAX_PAGE_RESOURCE_DIRECT_DEPTH,
            ));
        }
        let mut add_bytes = |amount: usize| -> PyResult<()> {
            bytes = bytes.checked_add(amount).ok_or_else(|| {
                PdfError::new_err(format!(
                    "{label} direct data exceeds the platform size limit"
                ))
            })?;
            if bytes > MAX_PAGE_RESOURCE_DIRECT_BYTES {
                return Err(page_resource_shape_error(
                    label,
                    "byte",
                    MAX_PAGE_RESOURCE_DIRECT_BYTES,
                ));
            }
            Ok(())
        };
        let child_count = match object {
            Object::Name(value) | Object::String(value, _) => {
                add_bytes(value.len())?;
                0
            }
            Object::Array(items) => items.len(),
            Object::Dictionary(child) => {
                for (key, _) in child.iter() {
                    add_bytes(key.len())?;
                }
                child.len()
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
        if child_count != 0 && depth >= MAX_PAGE_RESOURCE_DIRECT_DEPTH {
            return Err(page_resource_shape_error(
                label,
                "level",
                MAX_PAGE_RESOURCE_DIRECT_DEPTH,
            ));
        }
        let scheduled = objects
            .checked_add(pending.len())
            .and_then(|total| total.checked_add(child_count))
            .ok_or_else(|| {
                PdfError::new_err(format!(
                    "{label} direct objects exceed the platform size limit"
                ))
            })?;
        if scheduled > MAX_PAGE_RESOURCE_DIRECT_OBJECTS {
            return Err(page_resource_shape_error(
                label,
                "object",
                MAX_PAGE_RESOURCE_DIRECT_OBJECTS,
            ));
        }
        pending.try_reserve(child_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow {label} direct-object validation stack: {error}"
            ))
        })?;
        match object {
            Object::Array(items) => {
                for child in items {
                    pending.push((child, depth + 1));
                }
            }
            Object::Dictionary(child) => {
                for (_, value) in child.iter() {
                    pending.push((value, depth + 1));
                }
            }
            Object::Stream(stream) => {
                for (_, value) in stream.dict.iter() {
                    pending.push((value, depth + 1));
                }
            }
            _ => {}
        }
    }
    if bytes > MAX_PAGE_RESOURCE_DIRECT_BYTES {
        return Err(page_resource_shape_error(
            label,
            "byte",
            MAX_PAGE_RESOURCE_DIRECT_BYTES,
        ));
    }
    Ok(())
}

fn prepare_page_resource(
    doc: &Document,
    page_id: ObjectId,
    kind: PageResourceKind,
) -> PyResult<PageResourcePlan> {
    let mut page = resolve_inherited_page_dict(doc, page_id)?;
    let resources = match page.get(b"Resources") {
        Ok(object) => resolve_page_resource_dictionary(doc, object, "page Resources")?,
        Err(_) => None,
    };
    let mut resources = match resources {
        Some(resolved) => {
            validate_page_resource_shape(resolved, "page Resources")?;
            resolved.clone()
        }
        None => Dictionary::new(),
    };
    let key = kind.key();
    let label = match kind {
        PageResourceKind::XObject => "page Resources/XObject",
        PageResourceKind::Font => "page Resources/Font",
    };
    let entries = match resources.get(key) {
        Ok(object) => resolve_page_resource_dictionary(doc, object, label)?,
        Err(_) => None,
    };
    let mut entries = match entries {
        Some(resolved) => {
            validate_page_resource_shape(resolved, label)?;
            resolved.clone()
        }
        None => Dictionary::new(),
    };
    if !resources.has(key) && resources.len() >= MAX_PAGE_RESOURCE_ENTRIES {
        return Err(page_resource_shape_error(
            "page Resources",
            "entry",
            MAX_PAGE_RESOURCE_ENTRIES,
        ));
    }
    if entries.len() >= MAX_PAGE_RESOURCE_ENTRIES {
        return Err(page_resource_shape_error(
            label,
            "entry",
            MAX_PAGE_RESOURCE_ENTRIES,
        ));
    }

    let mut suffix = doc
        .max_id
        .checked_add(1)
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let name = loop {
        let candidate = format!("{}{suffix}", kind.name_prefix());
        if !entries.has(candidate.as_bytes()) {
            break candidate;
        }
        suffix = suffix
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF resource-name suffix limit reached"))?;
    };
    entries.set(name.as_bytes(), Object::Null);
    resources.set(key, entries);
    page.set("Resources", resources);
    Ok(PageResourcePlan { page, kind, name })
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

fn collect_form_page_content(doc: &Document, page_id: ObjectId) -> PyResult<Vec<u8>> {
    let streams = draw::inspect_page_contents(doc, page_id).map_err(PdfError::new_err)?;
    let payload_limit = MAX_FORM_CONTENT_BYTES - 5;
    let mut content = Vec::new();
    for stream_id in streams {
        let Ok(stream) = doc.get_object(stream_id).and_then(Object::as_stream) else {
            continue;
        };
        let remaining = payload_limit.saturating_sub(content.len());
        if remaining == 0 {
            return Err(PdfError::new_err(format!(
                "Form source page content exceeds the {MAX_FORM_CONTENT_BYTES}-byte safety limit"
            )));
        }
        let data_limit = remaining - 1;
        let decoded = decoded_stream_content_cow(
            stream,
            Some(data_limit),
            "form_content_size",
            "Form source page content",
        )?;
        let addition = decoded.len().checked_add(1).ok_or_else(|| {
            PdfError::new_err("Form source page content exceeds the platform size limit")
        })?;
        content.try_reserve_exact(addition).map_err(|error| {
            PdfError::new_err(format!("failed to grow Form source page content: {error}"))
        })?;
        content.extend_from_slice(&decoded);
        content.push(b'\n');
    }
    content.try_reserve_exact(5).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate Form source page wrapper: {error}"
        ))
    })?;
    let payload_len = content.len();
    content.resize(payload_len + 5, 0);
    content.copy_within(0..payload_len, 2);
    content[..2].copy_from_slice(b"q\n");
    content[payload_len + 2..].copy_from_slice(b"\nQ\n");
    Ok(content)
}

/// Prepare one source page as an unplaced Form without mutating the target.
fn prepare_page_form_import(
    target_max_id: u32,
    mut source: Document,
    page_number: u32,
) -> PyResult<FormImportPlan> {
    if source.objects.len() > MAX_FORM_IMPORT_OBJECTS {
        return Err(PdfError::new_err(format!(
            "Form import exceeds the {MAX_FORM_IMPORT_OBJECTS}-object safety limit"
        )));
    }
    let object_count = u32::try_from(source.objects.len())
        .map_err(|error| PdfError::new_err(error.to_string()))?;
    target_max_id
        .checked_add(object_count)
        .and_then(|highest_source_id| highest_source_id.checked_add(1))
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let starting_id = target_max_id
        .checked_add(1)
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let missing_page = |_| PdfError::new_err(format!("source page {page_number} does not exist"));
    let mut source_pages = collect_page_ids(&source, None, None)?;
    page_id_from_index(&source_pages, page_number).map_err(missing_page)?;
    renumber_objects(&mut source, starting_id, &mut source_pages)?;
    let source_id = page_id_from_index(&source_pages, page_number).map_err(missing_page)?;
    let source_dict = resolve_inherited_page_dict(&source, source_id)?;
    let source_crop = resolve_box(&source, &source_dict, b"CropBox")
        .or_else(|| resolve_box(&source, &source_dict, b"MediaBox"))
        .unwrap_or([0.0, 0.0, 595.0, 842.0]);
    let source_rotation = source_dict
        .get(b"Rotate")
        .ok()
        .and_then(|object| resolve_i64(&source, object))
        .unwrap_or(0)
        .rem_euclid(360);

    let form_content = collect_form_page_content(&source, source_id)?;
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

    // Move only the source objects that the Form can reach. The page tree
    // stays behind, so a reference into it dangles, and outlines,
    // annotations, attachments, metadata, and the page's original content
    // streams would be unreachable in the target. Pruning the complete
    // target after the import found the same objects, but walked every
    // target object for each placement.
    let reachable = reachable_object_ids(
        &source,
        form_dict.iter().map(|(_, value)| value),
        |object| !matches!(object.type_name(), Ok(b"Catalog" | b"Pages" | b"Page")),
    )?;
    let new_max_id = source.max_id;
    let mut objects = Vec::new();
    objects
        .try_reserve_exact(reachable.len())
        .map_err(|error| {
            PdfError::new_err(format!("failed to allocate Form import objects: {error}"))
        })?;
    for (id, object) in source.objects {
        if reachable.contains(&id) {
            objects.push((id, object));
        }
    }
    Ok(FormImportPlan {
        objects,
        form: Stream::new(form_dict, form_content).with_compression(false),
        new_max_id,
        crop: source_crop,
        rotation: source_rotation,
    })
}

/// Destination of a prepared Form import.
///
/// The indexed document keeps its page index while the renumbered IDs are
/// unused; a plain lopdf document inserts them directly.
trait FormImportTarget: ObjectSink {
    /// Insert renumbered objects at their IDs and set the highest object number.
    fn insert_new_objects(&mut self, objects: Vec<(ObjectId, Object)>, max_id: u32);
}

impl FormImportTarget for Document {
    fn insert_new_objects(&mut self, objects: Vec<(ObjectId, Object)>, max_id: u32) {
        for (id, object) in objects {
            self.objects.insert(id, object);
        }
        self.max_id = max_id;
    }
}

/// Commit a fully prepared Form import without further recoverable work.
fn commit_page_form_import(
    target: &mut impl FormImportTarget,
    plan: FormImportPlan,
) -> (ObjectId, [f64; 4], i64) {
    target.insert_new_objects(plan.objects, plan.new_max_id);
    let form_id = target.add_object(plan.form);
    (form_id, plan.crop, plan.rotation)
}

/// Import a page as an unplaced Form XObject and return its display metadata.
///
/// This is shared by page placement and krilla-generated widget appearances.
fn import_page_as_form(
    target: &mut Document,
    source: Document,
    page_number: u32,
) -> PyResult<(ObjectId, [f64; 4], i64)> {
    let plan = prepare_page_form_import(target.max_id, source, page_number)?;
    Ok(commit_page_form_import(target, plan))
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

/// Convert a hayro Pixmap to straight-alpha RGBA8 bytes in place.
///
/// vello_cpu unpremultiplies its own buffer and reinterprets it as bytes, so
/// this conversion allocates nothing.
fn rgba_bytes(pixmap: hayro::vello_cpu::Pixmap) -> Vec<u8> {
    pixmap.take_rgba8(ImageAlphaType::Alpha)
}

/// Pixel viewport of a display-coordinate clip on the scaled page raster.
///
/// The clip is intersected with the page and rounded outward to pixel
/// boundaries. Returns the viewport origin and size in pixels.
fn clip_viewport(
    page_width: u16,
    page_height: u16,
    scale: f32,
    clip: (f64, f64, f64, f64),
) -> Result<(f64, f64, u16, u16), String> {
    if ![clip.0, clip.1, clip.2, clip.3]
        .into_iter()
        .all(f64::is_finite)
        || clip.0 >= clip.2
        || clip.1 >= clip.3
    {
        return Err("clip must be a finite rectangle with x0 < x1 and y0 < y1".to_owned());
    }
    let scale = f64::from(scale);
    let (width, height) = (f64::from(page_width), f64::from(page_height));
    let x0 = (clip.0 * scale).floor().clamp(0.0, width);
    let y0 = (clip.1 * scale).floor().clamp(0.0, height);
    let x1 = (clip.2 * scale).ceil().clamp(0.0, width);
    let y1 = (clip.3 * scale).ceil().clamp(0.0, height);
    if x0 >= x1 || y0 >= y1 {
        return Err("clip does not intersect the rendered page".to_owned());
    }
    // Both extents are bounded by the u16 page raster, so the casts are exact.
    Ok((x0, y0, (x1 - x0) as u16, (y1 - y0) as u16))
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

/// Render one page, or one display-coordinate clip of it, from an immutable
/// hayro snapshot and caller-owned cache.
///
/// A clip renders through an offset viewport, so only its pixels are
/// rasterized and allocated. Full-page render-size limits still apply.
fn render_pdf_page<'a>(
    pdf: &'a Pdf,
    cache: &RenderCache<'a>,
    interpreter_settings: &InterpreterSettings,
    page_number: u32,
    scale: f32,
    background: Option<(u8, u8, u8, u8)>,
    clip: Option<(f64, f64, f64, f64)>,
) -> Result<hayro::vello_cpu::Pixmap, String> {
    render_pixel_count(pdf, page_number, scale)?;
    let pages = pdf.pages();
    let page = page_number
        .checked_sub(1)
        .and_then(|index| pages.get(index as usize))
        .ok_or_else(|| format!("page {page_number} does not exist"))?;
    let (page_width, page_height) = page.render_dimensions();
    // Match hayro::render, which truncates the scaled page size.
    let (width, height) = ((page_width * scale) as u16, (page_height * scale) as u16);
    let (x0, y0, width, height) = match clip {
        Some(clip) => clip_viewport(width, height, scale, clip)?,
        None => (0.0, 0.0, width, height),
    };
    let transform = Affine::translate((-x0, -y0))
        * Affine::scale(f64::from(scale))
        * page.initial_transform(true).to_kurbo();
    let mut context = RenderContext::new(width, height);
    render_into(
        page,
        cache,
        interpreter_settings,
        &RenderSettings::default(),
        &mut context,
        transform,
    );
    context.flush();
    let bg_color = background.map_or(PixmapSettings::default().bg_color, |(r, g, b, a)| {
        AlphaColor::from_rgba8(r, g, b, a)
    });
    let mut pixmap = hayro::vello_cpu::Pixmap::new(width, height);
    context.render_with(
        &mut pixmap,
        &mut Resources::default(),
        RasterizerSettings {
            target_init: TargetInit::Clear(bg_color),
            ..Default::default()
        },
    );
    Ok(pixmap)
}

/// Convert a rendered pixmap into the bounded fast PNG representation used publicly.
fn rendered_png(pixmap: hayro::vello_cpu::Pixmap, max_size: Option<usize>) -> PyResult<Vec<u8>> {
    let width = u32::from(pixmap.width());
    let height = u32::from(pixmap.height());
    let data = rgba_bytes(pixmap);
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
        Err(crate::extract::PngEncodeError::Allocation(error)) => Err(PdfError::new_err(error)),
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
        // Keep the shared admission compatible with the pinned Wasm toolchain.
        let mut total = self.output_bytes.load(Ordering::Relaxed);
        loop {
            let Some(new_total) = total
                .checked_add(buffer.len())
                .filter(|&value| value <= self.max_size)
            else {
                self.exceeded = true;
                return Err(io::Error::other("PNG batch output size limit exceeded"));
            };
            match self.output_bytes.compare_exchange_weak(
                total,
                new_total,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(current) => total = current,
            }
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
    let data = rgba_bytes(pixmap);
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

struct BatchRenderer<'shared> {
    interpreter_settings: &'shared InterpreterSettings,
    scale: f32,
    background: Option<(u8, u8, u8, u8)>,
    max_output_size: Option<usize>,
    output_bytes: &'shared AtomicUsize,
}

impl BatchRenderer<'_> {
    /// Render and encode one page through the shared output budget.
    fn render<'a>(
        &self,
        pdf: &'a Pdf,
        cache: &RenderCache<'a>,
        page_number: u32,
    ) -> PyResult<Vec<u8>> {
        let pixmap = render_pdf_page(
            pdf,
            cache,
            self.interpreter_settings,
            page_number,
            self.scale,
            self.background,
            None,
        )
        .map_err(PdfError::new_err)?;
        match self.max_output_size {
            Some(limit) => rendered_batch_png(pixmap, limit, self.output_bytes),
            None => rendered_png(pixmap, None),
        }
    }

    /// Render one ordered page batch with one hayro cache per bounded chunk,
    /// so whole-document batches never retain every resource.
    ///
    /// Each chunk asks the document for its cache: on the creating thread the
    /// first chunk reuses the retained cache, and a chunk beyond the retained
    /// page bound leaves the next chunk a fresh one.
    fn render_serial(
        &self,
        pdf: &Arc<Pdf>,
        caches: &crate::render_cache::DocumentRenderCache,
        page_numbers: &[u32],
    ) -> PyResult<Vec<Vec<u8>>> {
        let mut rendered = Vec::new();
        rendered
            .try_reserve_exact(page_numbers.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate serial render results: {error}"))
            })?;
        for chunk in page_numbers.chunks(PAGE_BATCH_CHUNK) {
            caches.with_cache(pdf, chunk, |pdf, cache| {
                for &page_number in chunk {
                    rendered.push(self.render(pdf, cache, page_number)?);
                }
                Ok::<_, PyErr>(())
            })?;
        }
        Ok(rendered)
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

/// Borrow a dictionary while allowing an indirect reference.
fn deref_dict_ref<'a>(doc: &'a Document, obj: &'a Object) -> Option<&'a Dictionary> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok(),
        Object::Dictionary(dictionary) => Some(dictionary),
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

struct TextMarkupAppearancePlan {
    annotation_id: ObjectId,
    kind: draw::TextMarkupKind,
    quads: Vec<[(f64, f64); 4]>,
    segment_counts: Vec<usize>,
    bbox: [f64; 4],
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
        .and_then(|appearance| deref_dict_ref(doc, appearance))
        .and_then(|appearance| appearance.get(b"N").ok())
        .is_some_and(|normal| {
            let resolved = deref_object(doc, normal);
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
/// but hayro 0.8 requires `/AP /N`. Keep this compatibility work outside the
/// editable document and refuse aggregate geometry amplification.
fn missing_text_markup_appearance_plans(
    doc: &Document,
) -> PyResult<Option<Vec<TextMarkupAppearancePlan>>> {
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
        let Some(new_total_quads) = total_quads.checked_add(quad_count) else {
            return Ok(None);
        };
        total_quads = new_total_quads;
        if total_quads > MAX_HIGHLIGHT_RECTS {
            return Ok(None);
        }

        let mut quads = Vec::new();
        quads.try_reserve_exact(quad_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate text-markup appearance quads: {error}"
            ))
        })?;
        let mut segment_counts = Vec::new();
        segment_counts
            .try_reserve_exact(quad_count)
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate text-markup appearance segment counts: {error}"
                ))
            })?;
        let mut valid = true;
        for chunk in quad_points.as_chunks::<8>().0 {
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
            let Some(new_total_segments) = total_segments.checked_add(segment_count) else {
                return Ok(None);
            };
            total_segments = new_total_segments;
            if total_segments > MAX_TEXT_MARKUP_SEGMENTS {
                return Ok(None);
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

        let bbox = draw::bounding_rect_iter(quads.iter().flatten().copied());
        if bbox.iter().any(|value| !value.is_finite()) || bbox[0] >= bbox[2] || bbox[1] >= bbox[3] {
            continue;
        }
        plans.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow text-markup appearance plans: {error}"
            ))
        })?;
        plans.push(TextMarkupAppearancePlan {
            annotation_id,
            kind,
            quads,
            segment_counts,
            bbox,
            color,
            opacity,
        });
    }
    Ok(Some(plans))
}

/// Add bounded text-markup appearances to a rendering clone only.
fn synthesize_missing_text_markup_appearances_for_render(
    doc: &mut Document,
    plans: Vec<TextMarkupAppearancePlan>,
) -> PyResult<bool> {
    let Ok(object_count) = u32::try_from(plans.len()).map(|count| count.saturating_mul(2)) else {
        return Ok(false);
    };
    if doc.max_id.checked_add(object_count).is_none() {
        return Ok(false);
    }

    let mut changed = false;
    for plan in plans {
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
            "BBox" => Object::Array(plan.bbox.iter().map(|&value| Object::Real(value as f32)).collect()),
            "Resources" => dictionary! {
                "ExtGState" => dictionary! { "PyloGS" => Object::Reference(gs_id) },
            },
        };
        let appearance_ops =
            draw::text_markup_ap_ops(plan.kind, &plan.quads, plan.color, &plan.segment_counts)
                .map_err(PdfError::new_err)?;
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
    Ok(changed)
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

/// Reject a stream filter array before lopdf materializes or applies it.
fn validate_stream_filter_count(stream: &Stream, context: &str) -> PyResult<()> {
    let count = match stream.dict.get(b"Filter") {
        Ok(Object::Name(_)) => 1,
        Ok(Object::Array(filters)) => filters.len(),
        _ => return Ok(()),
    };
    if count > MAX_STREAM_FILTERS {
        return Err(limit_err(
            "stream_filter_count",
            format!(
                "{context} uses {count} filter layers, exceeding the \
                 {MAX_STREAM_FILTERS}-layer safety limit"
            ),
        ));
    }
    Ok(())
}

/// Build one normalized stream copy without infallibly cloning its payload.
fn normalized_stream_copy(stream: &Stream, filters: &[&[u8]], context: &str) -> PyResult<Stream> {
    let mut content = Vec::new();
    content
        .try_reserve_exact(stream.content.len())
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate encoded {context} for filter normalization: {error}"
            ))
        })?;
    content.extend_from_slice(&stream.content);
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
    let mut checked_stream = Stream {
        dict: stream.dict.clone(),
        content,
        allows_compression: stream.allows_compression,
        start_position: stream.start_position,
    };
    checked_stream.dict.set("Filter", normalized_filter);
    Ok(checked_stream)
}

/// Decode a general-purpose stream with an optional bound on every filter layer.
///
/// Unlike lopdf's lenient `get_plain_content`, reported decoder failures and
/// unsupported filters are errors rather than a reason to return encoded bytes
/// as if they were plain.
fn decoded_stream_content_cow<'a>(
    stream: &'a Stream,
    max_size: Option<usize>,
    limit_code: &'static str,
    context: &str,
) -> PyResult<Cow<'a, [u8]>> {
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
        return Ok(Cow::Borrowed(&stream.content));
    }
    validate_stream_filter_count(stream, context)?;

    let raw_filters = stream.filters().map_err(|error| {
        PdfError::new_err(format!(
            "{context} has an invalid Filter: {}",
            LopdfErrorChain(&error)
        ))
    })?;
    if raw_filters.is_empty() {
        if let Some(limit) = max_size
            && stream.content.len() > limit
        {
            return Err(reject_raw(stream.content.len(), limit));
        }
        return Ok(Cow::Borrowed(&stream.content));
    }
    let normalized_filters: Vec<&[u8]> = raw_filters
        .iter()
        .map(|filter| canonical_filter_name(filter))
        .collect();
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
            PdfError::new_err(format!(
                "failed to decode {context}: {}",
                LopdfErrorChain(&error)
            ))
        }
    };
    let decode = |candidate: &Stream| match max_size {
        Some(limit) => candidate
            .decompressed_content_with_limit(limit)
            .map_err(decode_error),
        None => candidate.decompressed_content().map_err(decode_error),
    };
    if raw_filters
        .iter()
        .zip(&normalized_filters)
        .all(|(raw, normalized)| *raw == *normalized)
    {
        return decode(stream).map(Cow::Owned);
    }

    let checked_stream = normalized_stream_copy(stream, &normalized_filters, context)?;
    decode(&checked_stream).map(Cow::Owned)
}

/// Materialize a strictly decoded stream for Python-owned return data.
fn decoded_stream_content(
    stream: &Stream,
    max_size: Option<usize>,
    limit_code: &'static str,
    context: &str,
) -> PyResult<Vec<u8>> {
    match decoded_stream_content_cow(stream, max_size, limit_code, context)? {
        Cow::Owned(data) => Ok(data),
        Cow::Borrowed(data) => {
            let mut owned = Vec::new();
            owned.try_reserve_exact(data.len()).map_err(|error| {
                PdfError::new_err(format!("failed to allocate {context}: {error}"))
            })?;
            owned.extend_from_slice(data);
            Ok(owned)
        }
    }
}

/// Return the maximum direct array/dictionary nesting in one object.
///
/// Indirect references are leaves here. This makes cycles harmless while
/// bounding the recursive object shapes consumed by downstream libraries.
fn direct_object_depth(object: &Object, stop_above: Option<usize>) -> PyResult<usize> {
    enum Pending<'a> {
        Visit(&'a Object, usize),
        ArrayTail(&'a [Object], usize),
    }

    let mut maximum = 1usize;
    let mut pending = Vec::new();
    pending.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate direct-object depth traversal stack: {error}"
        ))
    })?;
    pending.push(Pending::Visit(object, 1usize));
    while let Some(work) = pending.pop() {
        let (current, depth) = match work {
            Pending::Visit(current, depth) => (current, depth),
            Pending::ArrayTail(items, depth) => {
                let Some((current, remaining)) = items.split_first() else {
                    continue;
                };
                if !remaining.is_empty() {
                    pending.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow direct-object depth traversal stack: {error}"
                        ))
                    })?;
                    pending.push(Pending::ArrayTail(remaining, depth));
                }
                (current, depth)
            }
        };
        maximum = maximum.max(depth);
        if stop_above.is_some_and(|limit| maximum > limit) {
            return Ok(maximum);
        }
        let child_depth = depth.saturating_add(1);
        match current {
            Object::Array(items) if !items.is_empty() => {
                pending.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to grow direct-object depth traversal stack: {error}"
                    ))
                })?;
                pending.push(Pending::ArrayTail(items, child_depth));
            }
            Object::Dictionary(dict) => {
                pending.try_reserve(dict.len()).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to grow direct-object depth traversal stack: {error}"
                    ))
                })?;
                for (_, item) in dict.iter() {
                    pending.push(Pending::Visit(item, child_depth));
                }
            }
            Object::Stream(stream) => {
                pending.try_reserve(stream.dict.len()).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to grow direct-object depth traversal stack: {error}"
                    ))
                })?;
                for (_, item) in stream.dict.iter() {
                    pending.push(Pending::Visit(item, child_depth));
                }
            }
            _ => {}
        }
    }
    Ok(maximum)
}

/// Collect cheap structural complexity without decoding streams.
fn document_complexity(doc: &IndexedDocument) -> PyResult<ComplexityTuple> {
    let page_count = doc.page_count()?;
    let object_count = doc.objects.len();
    let mut stream_count = 0usize;
    let mut encoded_stream_bytes = 0u64;
    let mut max_object_depth = 0usize;
    for (_, object) in doc.trailer.iter() {
        max_object_depth = max_object_depth.max(direct_object_depth(object, None)?);
    }
    for object in doc.objects.values() {
        max_object_depth = max_object_depth.max(direct_object_depth(object, None)?);
        if let Object::Stream(stream) = object {
            stream_count = stream_count.saturating_add(1);
            encoded_stream_bytes = encoded_stream_bytes.saturating_add(stream.content.len() as u64);
        }
    }
    Ok((
        page_count,
        object_count,
        stream_count,
        encoded_stream_bytes,
        max_object_depth,
    ))
}

struct PageTreeFrame<'a> {
    kids: &'a [Object],
    next: usize,
    depth: usize,
}

/// What a successful page-tree walk read besides page `/Type` entries.
///
/// The walk reads `/Root`, the catalog's `/Pages`, each node's `/Type` and
/// `/Kids`, every object on a `/Kids` reference chain, and the object count,
/// which bounds the traversed edges and only grows during edits that keep the
/// page index. Those edits check their changes against these facts.
#[derive(Default)]
struct PageTreeFacts {
    /// Objects on `/Kids` reference chains, including the arrays they reach.
    kids_objects: HashSet<ObjectId>,
    /// Absent objects named by a skipped `/Kids` entry or its reference chain.
    ///
    /// A new object at one of these IDs could turn the entry into a page.
    missing_kids: HashSet<ObjectId>,
}

impl PageTreeFacts {
    fn record_kids_object(&mut self, id: ObjectId) -> PyResult<()> {
        self.kids_objects.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow page tree Kids index: {error}"))
        })?;
        self.kids_objects.insert(id);
        Ok(())
    }

    /// Record the absent object that left a `/Kids` entry unresolved, if any.
    ///
    /// This fetches the entry and the references it leads through, as far as
    /// lopdf's dereference fetches before its 128-reference limit fails. A
    /// cycle or a final object that is not a dictionary names no absent
    /// object, and no edit that keeps the index changes such a chain.
    fn record_missing_kid(&mut self, doc: &Document, kid_id: ObjectId) -> PyResult<()> {
        const LOPDF_DEREFERENCE_LIMIT: usize = 128;
        let mut id = kid_id;
        // The entry itself, then one fetch per reference including the one
        // that exceeds the limit.
        for _ in 0..=LOPDF_DEREFERENCE_LIMIT + 1 {
            match doc.objects.get(&id) {
                None => {
                    self.missing_kids.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow page tree missing-object index: {error}"
                        ))
                    })?;
                    self.missing_kids.insert(id);
                    return Ok(());
                }
                Some(Object::Reference(next)) => id = *next,
                Some(_) => return Ok(()),
            }
        }
        Ok(())
    }
}

fn resolve_page_tree_kids<'a>(
    doc: &'a Document,
    node: &'a Dictionary,
    mut facts: Option<&mut PageTreeFacts>,
) -> PyResult<&'a [Object]> {
    let mut current = node
        .get(b"Kids")
        .map_err(|_| PdfError::new_err("page tree node is missing Kids"))?;
    let mut visited = Vec::new();
    visited
        .try_reserve_exact(MAX_PAGE_TREE_REFERENCE_DEPTH)
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to inspect page tree Kids references: {error}"
            ))
        })?;
    loop {
        let Object::Reference(id) = current else {
            return current
                .as_array()
                .map(Vec::as_slice)
                .map_err(|_| PdfError::new_err("page tree Kids must resolve to an array"));
        };
        if visited.contains(id) {
            return Err(PdfError::new_err(
                "page tree Kids contains a reference cycle",
            ));
        }
        if visited.len() >= MAX_PAGE_TREE_REFERENCE_DEPTH {
            return Err(PdfError::new_err(format!(
                "page tree Kids exceeds the {MAX_PAGE_TREE_REFERENCE_DEPTH}-reference-depth safety limit"
            )));
        }
        visited.push(*id);
        if let Some(facts) = facts.as_deref_mut() {
            facts.record_kids_object(*id)?;
        }
        current = doc.objects.get(id).ok_or_else(|| {
            PdfError::new_err(format!(
                "page tree Kids references missing object {} {}",
                id.0, id.1
            ))
        })?;
    }
}

/// Visit pages in order, recording what the walk read into `facts` if given.
fn walk_page_tree(
    doc: &Document,
    max_pages: Option<usize>,
    mut facts: Option<&mut PageTreeFacts>,
    mut visitor: impl FnMut(u32, ObjectId) -> PyResult<()>,
) -> PyResult<usize> {
    let root_id = doc
        .catalog()
        .map_err(to_py_err)?
        .get(b"Pages")
        .and_then(Object::as_reference)
        .map_err(|_| PdfError::new_err("Catalog Pages must be an indirect page-tree node"))?;
    let root = doc
        .get_dictionary(root_id)
        .map_err(|error| PdfError::new_err(format!("cannot read page tree root: {error}")))?;
    if root.get_type().ok() != Some(b"Pages") {
        return Err(PdfError::new_err(
            "Catalog Pages does not reference a Pages dictionary",
        ));
    }

    let root_kids = resolve_page_tree_kids(doc, root, facts.as_deref_mut())?;
    let mut frames = Vec::new();
    frames.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate page tree traversal stack: {error}"
        ))
    })?;
    frames.push(PageTreeFrame {
        kids: root_kids,
        next: 0,
        depth: 1,
    });
    let mut visited = HashSet::new();
    visited.try_reserve(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate page tree cycle detection: {error}"
        ))
    })?;
    visited.insert(root_id);

    let edge_limit = doc.objects.len();
    let mut edges = 0usize;
    let mut page_count = 0usize;
    while let Some(frame) = frames.last_mut() {
        let Some(kid) = frame.kids.get(frame.next) else {
            frames.pop();
            continue;
        };
        frame.next += 1;
        let child_depth = frame
            .depth
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("page tree depth exceeds the platform size limit"))?;
        edges = edges.checked_add(1).ok_or_else(|| {
            PdfError::new_err("page tree edge count exceeds the platform size limit")
        })?;
        if edges > edge_limit {
            return Err(PdfError::new_err(format!(
                "page tree exceeds its {edge_limit}-edge object-count safety limit"
            )));
        }

        let Ok(kid_id) = kid.as_reference() else {
            continue;
        };
        let Ok(dictionary) = doc.get_dictionary(kid_id) else {
            if let Some(facts) = facts.as_deref_mut() {
                facts.record_missing_kid(doc, kid_id)?;
            }
            continue;
        };
        let Ok(type_name) = dictionary.get_type() else {
            continue;
        };
        if !matches!(type_name, b"Page" | b"Pages") {
            continue;
        }
        if visited.contains(&kid_id) {
            return Err(PdfError::new_err(format!(
                "page tree reuses object {} {} or contains a cycle",
                kid_id.0, kid_id.1
            )));
        }
        visited.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow page tree cycle detection: {error}"))
        })?;
        visited.insert(kid_id);

        if type_name == b"Page" {
            let next_count = page_count
                .checked_add(1)
                .ok_or_else(|| PdfError::new_err("page count exceeds the platform size limit"))?;
            if let Some(limit) = max_pages
                && next_count > limit
            {
                return Err(limit_err(
                    "page_count",
                    format!("PDF contains more than {limit} pages, exceeding the configured limit"),
                ));
            }
            let page_number = u32::try_from(next_count)
                .map_err(|_| PdfError::new_err("page count exceeds the PDF object-ID range"))?;
            visitor(page_number, kid_id)?;
            page_count = next_count;
            continue;
        }

        if child_depth > MAX_PAGE_TREE_DEPTH {
            return Err(PdfError::new_err(format!(
                "page tree exceeds the {MAX_PAGE_TREE_DEPTH}-level safety limit"
            )));
        }
        let kids = resolve_page_tree_kids(doc, dictionary, facts.as_deref_mut())?;
        frames.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow page tree traversal stack: {error}"))
        })?;
        frames.push(PageTreeFrame {
            kids,
            next: 0,
            depth: child_depth,
        });
    }
    Ok(page_count)
}

/// Collect page IDs in order, recording what the walk read into `facts` if given.
fn collect_page_ids(
    doc: &Document,
    max_pages: Option<usize>,
    facts: Option<&mut PageTreeFacts>,
) -> PyResult<Vec<ObjectId>> {
    let mut pages = Vec::new();
    if let Some(limit) = max_pages {
        pages
            .try_reserve_exact(limit.min(doc.objects.len()))
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate bounded page index: {error}"))
            })?;
    }
    walk_page_tree(doc, max_pages, facts, |_, id| {
        if pages.len() == pages.capacity() {
            pages.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to grow page index: {error}"))
            })?;
        }
        pages.push(id);
        Ok(())
    })?;
    Ok(pages)
}

/// Reject explicit page entries beyond the document-relative batch bound.
fn check_page_entry_count(entries: usize, page_count: usize, verb: &str) -> PyResult<()> {
    let limit = page_count.max(MIN_PAGE_ENTRY_LIMIT);
    if entries > limit {
        return Err(limit_err(
            "page_entry_count",
            format!(
                "cannot {verb} more than {limit} page entries per call (the larger of the page count and {MIN_PAGE_ENTRY_LIMIT})"
            ),
        ));
    }
    Ok(())
}

fn page_id_from_index(page_ids: &[ObjectId], page_number: u32) -> PyResult<ObjectId> {
    page_number
        .checked_sub(1)
        .and_then(|index| page_ids.get(index as usize))
        .copied()
        .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))
}

/// The page index lives in its own module so that only `DerefMut`,
/// `replace`, and the page-tree-neutral edits below can reach the wrapped
/// document mutably.
mod indexed {
    use std::collections::HashMap;
    use std::ops::{Deref, DerefMut};
    use std::sync::OnceLock;

    use lopdf::{Dictionary, Document, Object, ObjectId};
    use pyo3::PyResult;

    use super::{
        FormImportTarget, PageTreeFacts, PdfError, collect_page_ids, page_id_from_index, to_py_err,
    };
    use crate::draw::ObjectSink;

    /// Page entries that a page-tree walk never reads.
    ///
    /// The walk reads only `/Type` of a page object (and `/Pages` when a
    /// malformed file also uses the page as its catalog) besides the object
    /// count, so replacing one of these entries keeps the indexed tree. None
    /// is `/Annots`, so the annotation-array counts stay current as well.
    const UNWALKED_PAGE_ENTRIES: [&str; 7] = [
        "Rotate", "MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox", "Contents",
    ];

    /// Page object IDs in document order, derived from one page-tree walk.
    struct PageIndex {
        /// Page object IDs in one-based page order.
        pages: Vec<ObjectId>,
        /// One-based page number of each page object, built on the first
        /// reverse lookup.
        numbers: OnceLock<HashMap<ObjectId, u32>>,
        /// What the walk read besides page `/Type` entries.
        tree: PageTreeFacts,
        /// Pages per indirect `/Annots` array, built on the first append to an
        /// indirect array and kept current by annotation edits.
        annotation_arrays: Option<AnnotationArrays>,
    }

    enum AnnotationArrays {
        /// How many pages reference each indirect `/Annots` array object.
        Counted(HashMap<ObjectId, usize>),
        /// A page object is a reference to a dictionary that another page can
        /// also reach, so an edit through one page can change what another
        /// reads; annotation appends scan every page instead.
        Aliased,
    }

    /// The editable lopdf document and the page index derived from it.
    ///
    /// Reads build the index with one bounded page-tree walk and reuse it
    /// until the document changes, so per-page access is amortized O(1)
    /// instead of a complete walk per call. `DerefMut` drops the index before
    /// handing out any mutable access, and the borrow checker forbids reading
    /// the index while that access is alive. An edit can therefore never read,
    /// or leave behind, an index of a different page tree, even one built
    /// between two steps of a multi-step edit. A failed walk is never cached,
    /// so it raises again. This wrapper exists for that invariant, which is
    /// why it implements `Deref` without being a smart pointer.
    ///
    /// Page-tree-neutral edits are the exception, so loops that rotate, crop,
    /// draw on, or annotate every page stay linear. Each checks against the
    /// facts the walk recorded that it cannot change the walk's result, and
    /// drops the index when it cannot show that:
    ///
    /// - New objects keep it only at IDs that neither exist nor complete a
    ///   skipped `/Kids` entry.
    /// - A page replacement keeps it only for an indexed page whose `/Type`,
    ///   `/Pages`, and indirect `/Annots` reference stay the same.
    /// - Rotation, page-box, and `/Contents` entries are never walked.
    /// - An in-place annotation-array edit keeps it unless the walk read that
    ///   array as `/Kids`.
    ///
    /// None of these edits removes objects, so the object count that bounds
    /// the walked edges only grows.
    pub(super) struct IndexedDocument {
        doc: Document,
        page_index: OnceLock<PageIndex>,
    }

    impl IndexedDocument {
        pub(super) fn new(doc: Document) -> Self {
            Self {
                doc,
                page_index: OnceLock::new(),
            }
        }

        /// Replace the complete document and drop the old page index.
        pub(super) fn replace(&mut self, doc: Document) {
            self.page_index.take();
            self.doc = doc;
        }

        fn page_index(&self) -> PyResult<&PageIndex> {
            if let Some(index) = self.page_index.get() {
                return Ok(index);
            }
            let mut tree = PageTreeFacts::default();
            let pages = collect_page_ids(&self.doc, None, Some(&mut tree))?;
            // A concurrent reader of the same unchanged document may store an
            // equal index first; either one describes this page tree.
            Ok(self.page_index.get_or_init(|| PageIndex {
                pages,
                numbers: OnceLock::new(),
                tree,
                annotation_arrays: None,
            }))
        }

        /// Return page object IDs in one-based page order.
        pub(super) fn page_ids(&self) -> PyResult<&[ObjectId]> {
            Ok(&self.page_index()?.pages)
        }

        /// Copy the page order for an edit that consumes it.
        pub(super) fn owned_page_ids(&self) -> PyResult<Vec<ObjectId>> {
            let pages = self.page_ids()?;
            let mut owned = Vec::new();
            owned.try_reserve_exact(pages.len()).map_err(|error| {
                PdfError::new_err(format!("failed to copy page index: {error}"))
            })?;
            owned.extend_from_slice(pages);
            Ok(owned)
        }

        pub(super) fn page_count(&self) -> PyResult<usize> {
            Ok(self.page_ids()?.len())
        }

        /// Return the ObjectId of a one-based page.
        pub(super) fn page_id(&self, page_number: u32) -> PyResult<ObjectId> {
            page_id_from_index(self.page_ids()?, page_number)
        }

        /// Return the one-based page number of each page object.
        pub(super) fn page_numbers(&self) -> PyResult<&HashMap<ObjectId, u32>> {
            let index = self.page_index()?;
            if let Some(numbers) = index.numbers.get() {
                return Ok(numbers);
            }
            let numbers = reverse_page_index(&index.pages)?;
            Ok(index.numbers.get_or_init(|| numbers))
        }

        /// Whether `page_id` is an indexed page, or `None` when no index exists
        /// or its reverse map cannot be allocated.
        fn indexed_page(&self, page_id: ObjectId) -> Option<bool> {
            self.page_index.get()?;
            self.page_numbers()
                .ok()
                .map(|numbers| numbers.contains_key(&page_id))
        }

        /// Drop the index unless the edit about to happen keeps the walk's
        /// result, as `keeps_walk` decides from the recorded facts.
        fn keep_index_if(&mut self, keeps_walk: impl FnOnce(&PageIndex, &Document) -> bool) {
            if let Some(index) = self.page_index.get()
                && !keeps_walk(index, &self.doc)
            {
                self.page_index.take();
            }
        }

        /// Set one entry of a one-based page dictionary.
        pub(super) fn set_page_entry(
            &mut self,
            page_number: u32,
            key: &str,
            value: Object,
        ) -> PyResult<()> {
            let page_id = self.page_id(page_number)?;
            self.set_page_object_entry(page_id, key, value)
        }

        /// Set one entry of a page object.
        ///
        /// Rotation, page-box, and `/Contents` entries keep the page index;
        /// any other key could retype the page, so it drops the index like
        /// `DerefMut`.
        pub(super) fn set_page_object_entry(
            &mut self,
            page_id: ObjectId,
            key: &str,
            value: Object,
        ) -> PyResult<()> {
            if !UNWALKED_PAGE_ENTRIES.contains(&key) {
                self.page_index.take();
            }
            self.doc
                .get_object_mut(page_id)
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?
                .set(key, value);
            Ok(())
        }

        /// Replace a page object with a prepared page dictionary.
        ///
        /// The index survives when `page_id` is an indexed page and the new
        /// dictionary keeps the `/Type`, `/Pages`, and indirect `/Annots`
        /// reference of the dictionary it replaces. A page object that was a
        /// reference becomes a dictionary with those entries, so every walk
        /// through it still ends at a page.
        pub(super) fn replace_page(&mut self, page_id: ObjectId, page: Dictionary) {
            let keeps_walk = self.indexed_page(page_id) == Some(true)
                && self
                    .doc
                    .get_object(page_id)
                    .and_then(Object::as_dict)
                    .is_ok_and(|current| {
                        current.get(b"Type").ok() == page.get(b"Type").ok()
                            && current.get(b"Pages").ok() == page.get(b"Pages").ok()
                            && annotations_reference(current) == annotations_reference(&page)
                    });
            if !keeps_walk {
                self.page_index.take();
            }
            self.doc.objects.insert(page_id, Object::Dictionary(page));
        }

        /// Borrow a page's direct `/Annots` array.
        ///
        /// The walk never reads `/Annots`, and the entry stays a direct array,
        /// so the index and its annotation-array counts survive any change.
        pub(super) fn page_annotations_mut(
            &mut self,
            page_id: ObjectId,
        ) -> PyResult<&mut Vec<Object>> {
            self.doc
                .get_object_mut(page_id)
                .and_then(Object::as_dict_mut)
                .and_then(|page| page.get_mut(b"Annots"))
                .and_then(Object::as_array_mut)
                .map_err(to_py_err)
        }

        /// Borrow the indirect annotation array that `array_id` resolves to.
        ///
        /// A malformed file can use one array as both `/Annots` and `/Kids`,
        /// so the index survives only when the walk did not read the array.
        /// The walk reads no other array content.
        pub(super) fn annotation_array_mut(
            &mut self,
            array_id: ObjectId,
        ) -> PyResult<&mut Vec<Object>> {
            // Resolve the array object exactly as `get_object_mut` does below.
            let stored = self
                .doc
                .objects
                .get(&array_id)
                .ok_or(lopdf::Error::ObjectNotFound(array_id))
                .map_err(to_py_err)?;
            let (resolved, _) = self.doc.dereference(stored).map_err(to_py_err)?;
            let target = resolved.unwrap_or(array_id);
            self.keep_index_if(|index, _| !index.tree.kids_objects.contains(&target));
            self.doc
                .get_object_mut(array_id)
                .and_then(Object::as_array_mut)
                .map_err(to_py_err)
        }

        /// Replace a page's `/Annots` with a direct array.
        ///
        /// Counts of pages per indirect array follow the change; they are
        /// dropped, to be counted again, unless `page_id` is an indexed page.
        pub(super) fn set_page_annotations(
            &mut self,
            page_id: ObjectId,
            items: Vec<Object>,
        ) -> PyResult<()> {
            let indexed = self.indexed_page(page_id);
            let page = self
                .doc
                .get_object_mut(page_id)
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            let previous = annotations_reference(page);
            page.set("Annots", items);
            if let Some(array_id) = previous
                && let Some(index) = self.page_index.get_mut()
            {
                match (&mut index.annotation_arrays, indexed) {
                    (Some(AnnotationArrays::Counted(counts)), Some(true)) => {
                        if let Some(count) = counts.get_mut(&array_id) {
                            *count = count.saturating_sub(1);
                        }
                    }
                    (Some(AnnotationArrays::Counted(_)), _) => index.annotation_arrays = None,
                    _ => {}
                }
            }
            Ok(())
        }

        /// Whether another page references the indirect `/Annots` array
        /// `array_id` that `page_id` references.
        ///
        /// The first call counts the pages of every indirect array, so later
        /// appends cost O(1) instead of a scan of every page.
        pub(super) fn annotation_array_shared(
            &mut self,
            page_id: ObjectId,
            array_id: ObjectId,
        ) -> PyResult<bool> {
            let own = self.page_numbers()?.contains_key(&page_id)
                && page_annotations_reference(&self.doc, page_id) == Some(array_id);
            let index = self
                .page_index
                .get_mut()
                .ok_or_else(|| PdfError::new_err("page index was not built"))?;
            if index.annotation_arrays.is_none() {
                let arrays = AnnotationArrays::count(&self.doc, &index.pages)?;
                index.annotation_arrays = Some(arrays);
            }
            Ok(match &index.annotation_arrays {
                Some(AnnotationArrays::Counted(counts)) => {
                    counts.get(&array_id).copied().unwrap_or(0) > usize::from(own)
                }
                _ => index.pages.iter().any(|&other_page_id| {
                    other_page_id != page_id
                        && page_annotations_reference(&self.doc, other_page_id) == Some(array_id)
                }),
            })
        }
    }

    impl ObjectSink for IndexedDocument {
        /// Add an object at lopdf's next ID, keeping the index while that ID is
        /// unused by the walk.
        fn add_object<T: Into<Object>>(&mut self, object: T) -> ObjectId {
            let id = (self.doc.max_id.wrapping_add(1), 0);
            self.keep_index_if(|index, doc| is_unused_id(index, doc, id));
            self.doc.add_object(object)
        }
    }

    impl FormImportTarget for IndexedDocument {
        /// Insert renumbered objects, keeping the index while every ID is
        /// unused by the walk.
        fn insert_new_objects(&mut self, objects: Vec<(ObjectId, Object)>, max_id: u32) {
            self.keep_index_if(|index, doc| {
                objects.iter().all(|&(id, _)| is_unused_id(index, doc, id))
            });
            for (id, object) in objects {
                self.doc.objects.insert(id, object);
            }
            self.doc.max_id = max_id;
        }
    }

    impl Deref for IndexedDocument {
        type Target = Document;

        fn deref(&self) -> &Document {
            &self.doc
        }
    }

    impl DerefMut for IndexedDocument {
        /// Mutable access may change the page tree, so drop its index first.
        fn deref_mut(&mut self) -> &mut Document {
            self.page_index.take();
            &mut self.doc
        }
    }

    impl AnnotationArrays {
        /// Count the pages that reference each indirect `/Annots` array.
        fn count(doc: &Document, pages: &[ObjectId]) -> PyResult<Self> {
            let mut counts: HashMap<ObjectId, usize> = HashMap::new();
            for page_id in pages {
                let Some(Object::Dictionary(page)) = doc.objects.get(page_id) else {
                    return Ok(Self::Aliased);
                };
                if let Some(array_id) = annotations_reference(page) {
                    counts.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow page annotation-array counts: {error}"
                        ))
                    })?;
                    *counts.entry(array_id).or_insert(0) += 1;
                }
            }
            Ok(Self::Counted(counts))
        }
    }

    /// Whether a new object at `id` leaves the indexed walk unchanged.
    ///
    /// An existing object could be anything the walk read, and an absent
    /// object named by a skipped `/Kids` entry could become a page.
    fn is_unused_id(index: &PageIndex, doc: &Document, id: ObjectId) -> bool {
        !doc.objects.contains_key(&id) && !index.tree.missing_kids.contains(&id)
    }

    fn annotations_reference(page: &Dictionary) -> Option<ObjectId> {
        page.get(b"Annots").and_then(Object::as_reference).ok()
    }

    /// The indirect `/Annots` array a page object resolves to, if any.
    fn page_annotations_reference(doc: &Document, page_id: ObjectId) -> Option<ObjectId> {
        doc.get_object(page_id)
            .and_then(Object::as_dict)
            .ok()
            .and_then(annotations_reference)
    }

    fn reverse_page_index(page_ids: &[ObjectId]) -> PyResult<HashMap<ObjectId, u32>> {
        let mut index = HashMap::new();
        index.try_reserve(page_ids.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate reverse page index: {error}"))
        })?;
        for (offset, &id) in page_ids.iter().enumerate() {
            let page_number = offset
                .checked_add(1)
                .and_then(|number| u32::try_from(number).ok())
                .ok_or_else(|| PdfError::new_err("page count exceeds the PDF object-ID range"))?;
            index.insert(id, page_number);
        }
        Ok(index)
    }
}

/// Reject structural work above configured limits before rendering/extraction.
fn validate_structural_limits(
    doc: &Document,
    limits: DocumentLimits,
    validate_pages: bool,
) -> PyResult<Option<Vec<ObjectId>>> {
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
        for object in doc
            .trailer
            .iter()
            .map(|(_, object)| object)
            .chain(doc.objects.values())
        {
            let depth = direct_object_depth(object, Some(limit))?;
            if depth > limit {
                return Err(limit_err(
                    "object_depth",
                    format!(
                        "PDF direct object nesting depth is {depth}, exceeding the configured limit of {limit}"
                    ),
                ));
            }
        }
    }

    if validate_pages && (limits.max_pages.is_some() || limits.max_page_content_size.is_some()) {
        return collect_page_ids(doc, limits.max_pages, None).map(Some);
    }
    Ok(None)
}

/// Map bounded decoder failures to the applicable stable limit code.
fn decompression_err(error: lopdf::Error, code: &'static str) -> PyErr {
    if matches!(
        error,
        lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
    ) {
        limit_err(code, LopdfErrorChain(&error).to_string())
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
    validate_stream_filter_count(stream, "PDF stream")?;
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
    let first_unsupported = filters
        .iter()
        .position(|filter| !LOPDF_FILTERS.contains(filter));
    match first_unsupported {
        None => {
            let canonical = raw_filters
                .iter()
                .zip(&filters)
                .all(|(raw, normalized)| *raw == *normalized);
            if canonical {
                stream
                    .get_plain_content_with_limit(max_output)
                    .map(|content| content.len())
                    .map_err(|error| decompression_err(error, limit_code))
            } else {
                normalized_stream_copy(stream, &filters, "PDF stream")?
                    .get_plain_content_with_limit(max_output)
                    .map(|content| content.len())
                    .map_err(|error| decompression_err(error, limit_code))
            }
        }
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
                normalized_stream_copy(stream, &filters[..index], "PDF stream")?
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
    page_ids: Option<&[ObjectId]>,
    max_output: Option<usize>,
    max_page_content: Option<usize>,
    max_total: Option<usize>,
) -> PyResult<()> {
    if max_output.is_none() && max_page_content.is_none() && max_total.is_none() {
        return Ok(());
    }
    let page_content_ids: HashSet<ObjectId> = if max_page_content.is_some() {
        let pages = page_ids.ok_or_else(|| {
            PdfError::new_err("internal page-content validation index is unavailable")
        })?;
        let mut content_ids = HashSet::new();
        for page_id in pages {
            let streams = draw::inspect_page_contents(doc, *page_id).map_err(PdfError::new_err)?;
            content_ids.try_reserve(streams.len()).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate page-content decompression index: {error}"
                ))
            })?;
            content_ids.extend(streams);
        }
        content_ids
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

/// One locale-specific fallback pair for non-embedded CJK fonts.
#[derive(Default, Clone)]
struct FallbackFontPair {
    /// Sans/gothic family and the default when style is unknown.
    sans: Option<(Arc<Vec<u8>>, u32)>,
    /// Mincho-style serif font.
    serif: Option<(Arc<Vec<u8>>, u32)>,
}

impl FallbackFontPair {
    fn pick(&self, prefers_serif: bool) -> Option<&(Arc<Vec<u8>>, u32)> {
        if prefers_serif {
            self.serif.as_ref().or(self.sans.as_ref())
        } else {
            self.sans.as_ref().or(self.serif.as_ref())
        }
    }

    fn is_configured(&self) -> bool {
        self.sans.is_some() || self.serif.is_some()
    }
}

/// Locale slots aligned with the four predefined Adobe CJK collections.
#[derive(Default, Clone)]
struct FallbackFonts {
    ja: FallbackFontPair,
    ko: FallbackFontPair,
    zh_cn: FallbackFontPair,
    zh_tw: FallbackFontPair,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum CjkFontLanguage {
    Ja,
    Ko,
    ZhCn,
    ZhTw,
}

impl CjkFontLanguage {
    fn parse(language: &str) -> PyResult<Self> {
        match language {
            "ja" => Ok(Self::Ja),
            "ko" => Ok(Self::Ko),
            "zh-CN" => Ok(Self::ZhCn),
            "zh-TW" => Ok(Self::ZhTw),
            _ => Err(PdfError::new_err(format!(
                "font language must be 'ja', 'ko', 'zh-CN', or 'zh-TW': {language:?}"
            ))),
        }
    }

    fn index(self) -> usize {
        match self {
            Self::Ja => 0,
            Self::Ko => 1,
            Self::ZhCn => 2,
            Self::ZhTw => 3,
        }
    }
}

impl FallbackFonts {
    fn pair(&self, language: CjkFontLanguage) -> &FallbackFontPair {
        match language {
            CjkFontLanguage::Ja => &self.ja,
            CjkFontLanguage::Ko => &self.ko,
            CjkFontLanguage::ZhCn => &self.zh_cn,
            CjkFontLanguage::ZhTw => &self.zh_tw,
        }
    }

    fn pair_mut(&mut self, language: CjkFontLanguage) -> &mut FallbackFontPair {
        match language {
            CjkFontLanguage::Ja => &mut self.ja,
            CjkFontLanguage::Ko => &mut self.ko,
            CjkFontLanguage::ZhCn => &mut self.zh_cn,
            CjkFontLanguage::ZhTw => &mut self.zh_tw,
        }
    }

    fn is_configured(&self) -> bool {
        self.ja.is_configured()
            || self.ko.is_configured()
            || self.zh_cn.is_configured()
            || self.zh_tw.is_configured()
    }
}

/// Lowercase BaseFont-name patterns indicating Japanese fonts.
const JP_NAME_HINTS: [&str; 12] = [
    "mincho", "gothic", "ryumin", "kozmin", "kozgo", "kozuka", "meiryo", "yugoth", "yumin",
    "hiragino", "ipaex", "ipam",
];

/// Lowercase BaseFont-name patterns indicating Korean fonts.
const KO_NAME_HINTS: [&str; 7] = [
    "batang",
    "gulim",
    "dotum",
    "malgun",
    "nanum",
    "hygothic",
    "hysmyeongjo",
];

/// Lowercase BaseFont-name patterns indicating Simplified Chinese fonts.
const ZH_CN_NAME_HINTS: [&str; 8] = [
    "simsun", "simhei", "fangsong", "kaiti", "yahei", "songti", "heiti", "cjk-sc",
];

/// Lowercase BaseFont-name patterns indicating Traditional Chinese fonts.
const ZH_TW_NAME_HINTS: [&str; 6] = [
    "mingliu", "pmingliu", "jhenghei", "dfkai", "cjk-tc", "cjk-hk",
];

/// Lowercase BaseFont-name patterns indicating a serif/mincho family.
const SERIF_NAME_HINTS: [&str; 12] = [
    "mincho", "ryumin", "kozmin", "yumin", "serif", "batang", "myeongjo", "simsun", "song", "ming",
    "kaiti", "kai",
];

fn hinted_cjk_language(name: &str) -> Option<CjkFontLanguage> {
    if JP_NAME_HINTS.iter().any(|hint| name.contains(hint)) {
        Some(CjkFontLanguage::Ja)
    } else if KO_NAME_HINTS.iter().any(|hint| name.contains(hint)) {
        Some(CjkFontLanguage::Ko)
    } else if ZH_CN_NAME_HINTS.iter().any(|hint| name.contains(hint)) {
        Some(CjkFontLanguage::ZhCn)
    } else if ZH_TW_NAME_HINTS.iter().any(|hint| name.contains(hint)) {
        Some(CjkFontLanguage::ZhTw)
    } else {
        None
    }
}

/// Return a configured fallback when a non-embedded font request is CJK.
///
/// Detect CJK through CIDSystemInfo (Adobe-Japan1/GB1/CNS1/Korea1) or BaseFont.
/// Adobe-Identity lacks CID-to-Unicode clues in its CMap, so use the name;
/// hayro resolves an embedded ToUnicode map when present.
fn pick_cjk_fallback(fonts: &FallbackFonts, query: &FallbackFontQuery) -> Option<(FontData, u32)> {
    let name = query
        .post_script_name
        .as_deref()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let language = match query.character_collection.as_ref().map(|cc| &cc.family) {
        Some(CidFamily::AdobeJapan1) => Some(CjkFontLanguage::Ja),
        Some(CidFamily::AdobeGB1) => Some(CjkFontLanguage::ZhCn),
        Some(CidFamily::AdobeCNS1) => Some(CjkFontLanguage::ZhTw),
        Some(CidFamily::AdobeKorea1) => Some(CjkFontLanguage::Ko),
        _ => hinted_cjk_language(&name),
    }?;
    let prefers_serif = SERIF_NAME_HINTS.iter().any(|hint| name.contains(hint));
    let slot = fonts.pair(language).pick(prefers_serif);
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

fn try_push_form<T>(values: &mut Vec<T>, value: T, label: &str) -> PyResult<()> {
    if values.len() == values.capacity() {
        values.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow AcroForm {label}: {error}"))
        })?;
    }
    values.push(value);
    Ok(())
}

fn try_clone_form_string(value: &str, label: &str) -> PyResult<String> {
    let mut owned = String::new();
    owned.try_reserve_exact(value.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate AcroForm {label}: {error}"))
    })?;
    owned.push_str(value);
    Ok(owned)
}

fn try_clone_form_bytes(value: &[u8], label: &str) -> PyResult<Vec<u8>> {
    let mut owned = Vec::new();
    owned.try_reserve_exact(value.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate AcroForm {label}: {error}"))
    })?;
    owned.extend_from_slice(value);
    Ok(owned)
}

fn try_lossy_form_string(value: &[u8], label: &str) -> PyResult<String> {
    match String::from_utf8_lossy(value) {
        Cow::Borrowed(value) => try_clone_form_string(value, label),
        Cow::Owned(value) => Ok(value),
    }
}

/// Borrow an inheritable field attribute unless the field omits it.
///
/// ISO 32000-2 §7.3.7 treats a null dictionary value as an omitted key, and
/// §7.3.10 reads a reference to a missing object as null, so a null value, an
/// indirect null, and such a reference inherit from the parent field as an
/// absent key does. Any other value, including an unusable one and a cyclic or
/// over-long reference chain, belongs to the field and stops inheritance.
fn inheritable_field_value<'a>(
    doc: &'a Document,
    field: &'a Dictionary,
    key: &[u8],
) -> Option<&'a Object> {
    let value = field.get(key).ok()?;
    let omitted = match value {
        Object::Null => true,
        Object::Reference(id) => matches!(
            doc.get_object(*id),
            Ok(Object::Null) | Err(lopdf::Error::ObjectNotFound(_))
        ),
        _ => false,
    };
    (!omitted).then_some(value)
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
            let decoded = try_lossy_form_string(name, "decoded field value")?;
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
                try_push_form(&mut values, decoded, "choice-value items")?;
            }
            let separators = values
                .len()
                .saturating_sub(1)
                .checked_mul(2)
                .ok_or_else(|| {
                    PdfError::new_err("AcroForm field value length exceeds the platform size limit")
                })?;
            if separators != 0 {
                add_form_budget(
                    decoded_bytes,
                    separators,
                    MAX_FORM_FIELD_VALUE_BYTES,
                    "decoded field-value",
                )?;
            }
            let output_size = values.iter().try_fold(separators, |size, value| {
                size.checked_add(value.len()).ok_or_else(|| {
                    PdfError::new_err("AcroForm field value length exceeds the platform size limit")
                })
            })?;
            let mut joined = String::new();
            joined.try_reserve_exact(output_size).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate AcroForm joined field value: {error}"
                ))
            })?;
            for (index, value) in values.into_iter().enumerate() {
                if index != 0 {
                    joined.push_str(", ");
                }
                joined.push_str(&value);
            }
            Ok(Some(joined))
        }
        _ => Ok(None),
    }
}

/// Read null, an indirect null, and a reference to a missing object alike.
///
/// ISO 32000-2 §7.3.7 treats a null dictionary value as an omitted key, and
/// §7.3.10 reads a reference to a missing object as null. A cyclic or
/// over-long reference chain is an unusable value rather than null.
fn is_null_equivalent(doc: &Document, object: &Object) -> bool {
    match object {
        Object::Null => true,
        Object::Reference(id) => matches!(
            doc.get_object(*id),
            Ok(Object::Null) | Err(lopdf::Error::ObjectNotFound(_))
        ),
        _ => false,
    }
}

/// One AcroForm `/Kids` entry as every field-tree reader classifies it.
enum FieldKid<'a> {
    /// A dictionary with its own partial field name.
    Field(ObjectId),
    /// A dictionary without one or kids to fold, which is a widget of the
    /// parent field.
    Widget(ObjectId),
    /// A dictionary without one but with kids, which is a nameless
    /// intermediate field folded into the parent field. `widget` is true when
    /// it also looks like a widget annotation.
    Intermediate {
        id: ObjectId,
        kids: &'a [Object],
        widget: bool,
    },
    /// Any other referenced value, such as a number or a cyclic reference chain.
    Unusable,
    /// A direct entry, an indirect null, or a reference to a missing object.
    Ignored,
}

/// Mark one AcroForm field-tree node visited within the node limit.
///
/// Returns false when the node was already visited.
fn visit_form_node(visited: &mut HashSet<ObjectId>, id: ObjectId) -> PyResult<bool> {
    if visited.contains(&id) {
        return Ok(false);
    }
    if visited.len() == visited.capacity() {
        visited.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow AcroForm field-tree cycle set: {error}"
            ))
        })?;
    }
    visited.insert(id);
    if visited.len() > MAX_FORM_FIELD_TREE_NODES {
        return Err(PdfError::new_err(format!(
            "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_NODES}-node safety limit"
        )));
    }
    Ok(true)
}

/// Add the entries of one `/Kids` array to the AcroForm field-tree edges.
fn add_form_tree_edges(edges: &mut usize, count: usize) -> PyResult<()> {
    *edges = edges
        .checked_add(count)
        .ok_or_else(|| PdfError::new_err("AcroForm field tree exceeds the platform size limit"))?;
    if *edges > MAX_FORM_FIELD_TREE_EDGES {
        return Err(PdfError::new_err(format!(
            "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_EDGES}-edge safety limit"
        )));
    }
    Ok(())
}

/// Borrow a field's `/Kids` array, reading any other value as an absent entry.
fn field_kids<'a>(doc: &'a Document, field: &'a Dictionary) -> Option<&'a [Object]> {
    let kids = field.get(b"Kids").ok()?;
    deref_object(doc, kids).as_array().ok().map(Vec::as_slice)
}

/// Treat a dictionary with `/Subtype /Widget` or a `/Rect` as a widget annotation.
fn looks_like_widget(dict: &Dictionary) -> bool {
    dict.get(b"Subtype")
        .and_then(Object::as_name)
        .is_ok_and(|name| name == b"Widget")
        || dict.has(b"Rect")
}

/// Classify one AcroForm `/Kids` entry for the walk and `field_widgets`.
///
/// A `/T` that is null, an indirect null, or a reference to a missing object
/// is an omitted partial name, so such a kid is nameless, as one without `/T`
/// is. Any other `/T`, including an unusable value or a cyclic or over-long
/// reference chain, makes the kid a child field.
///
/// A nameless kid whose `/Kids` resolves to a non-empty array is a field,
/// because a field merged with its only widget omits `/Kids` (ISO 32000-2
/// §12.7.4.1), and a field without a partial name represents the field above
/// it (§12.7.4.2). It is therefore an intermediate field whose kids fold into
/// the parent field, and it stays a widget too when it looks like one, so a
/// malformed merged widget with stray kids keeps its appearance in sync. Any
/// other nameless kid is a widget of its parent field: an absent,
/// null-equivalent, non-array, or empty `/Kids` names no kids to fold, and a
/// field with such a `/Kids` is its own widget.
fn classify_field_kid<'a>(doc: &'a Document, kid: &Object) -> FieldKid<'a> {
    let Object::Reference(id) = kid else {
        return FieldKid::Ignored;
    };
    match doc.get_object(*id) {
        Ok(Object::Dictionary(child)) => {
            if child
                .get(b"T")
                .is_ok_and(|name| !is_null_equivalent(doc, name))
            {
                return FieldKid::Field(*id);
            }
            match field_kids(doc, child) {
                Some(kids) if !kids.is_empty() => FieldKid::Intermediate {
                    id: *id,
                    kids,
                    widget: looks_like_widget(child),
                },
                _ => FieldKid::Widget(*id),
            }
        }
        Ok(Object::Null) | Err(lopdf::Error::ObjectNotFound(_)) => FieldKid::Ignored,
        _ => FieldKid::Unusable,
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
    /// Editable lopdf document with the page index that reads reuse.
    doc: IndexedDocument,
    /// CJK fallback configuration for rendering.
    fallback_fonts: FallbackFonts,
    /// Parsed hayro snapshot of current edit state, rebuilt after invalidation.
    ///
    /// Shared so the creating thread's retained render cache can own the
    /// snapshot it borrows without a self-reference in this object.
    hayro_pdf: Option<Arc<Pdf>>,
    /// Original unencrypted input, consumed by the first hayro parse.
    ///
    /// This avoids a potentially expensive lopdf serialization before the
    /// first render or extraction. Any edit discards it together with the
    /// parsed hayro view. Input is retained only when lopdf loaded exactly the
    /// object each in-use cross-reference entry names, as `object_recovery`
    /// checks: otherwise hayro could resolve different objects from the bytes
    /// than the graph that edits and saves use. That excludes input whose
    /// cross-reference data lopdf recovered, which hayro would recover
    /// independently. Input whose object streams would make hayro rebuild
    /// thousands of member tables is used only when the serialized graph
    /// cannot be built, as `object_stream_work` decides.
    hayro_source: HayroSource,
    /// Whether the current hayro snapshot was parsed from the original input
    /// rather than from a serialization of the graph; meaningful only while
    /// `hayro_pdf` is set. Tests read it through `snapshot_source`.
    hayro_pdf_from_original: bool,
    /// Content streams of the current hayro snapshot that were cleared for
    /// array and dictionary nesting before hayro could interpret them.
    ///
    /// Dropped with the snapshot, so edits never leave a stale verdict.
    content_guard: ContentGuard,
    /// Recently interpreted pages, keyed by one-based page number.
    text_pages: HashMap<u32, crate::extract::TextPage>,
    /// Least-recently-used to most-recently-used text-page keys.
    text_page_order: VecDeque<u32>,
    /// Recently interpreted table pages, keyed by one-based page number.
    table_pages: HashMap<u32, crate::extract::TablePage>,
    /// Least-recently-used to most-recently-used table-page keys.
    table_page_order: VecDeque<u32>,
    /// Page and named-destination lookups that link reads reuse until the
    /// next edit.
    link_resolution: Option<LinkResolutionCache>,
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
    /// Running sums of the two admission maps, so whole-document batches do
    /// not rescan every interpreted page for each newly admitted one.
    interpreted_text_total: usize,
    interpreted_glyph_total: usize,
    /// Pages whose existing contents were isolated during this document lifetime.
    isolated_content_pages: HashSet<ObjectId>,
    /// Render cache generation retained across calls on the creating thread.
    render_cache: crate::render_cache::DocumentRenderCache,
    /// Whether lenient loading recovered damaged cross-reference data.
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
            None,
        )
    }

    /// Construct from a loaded document and retain visible recovery state.
    fn from_loaded_doc(
        doc: Document,
        hayro_source: HayroSource,
        max_text_size: Option<usize>,
        max_interpretation_size: Option<usize>,
        max_text_glyphs: Option<usize>,
        recovery: Option<XrefRecovery>,
    ) -> Self {
        let pending = recovery
            .map(|recovery| vec![recovery.warning().to_owned()])
            .unwrap_or_default();
        Self {
            doc: IndexedDocument::new(doc),
            fallback_fonts: FallbackFonts::default(),
            hayro_pdf: None,
            hayro_source,
            hayro_pdf_from_original: false,
            content_guard: ContentGuard::default(),
            text_pages: HashMap::new(),
            text_page_order: VecDeque::new(),
            table_pages: HashMap::new(),
            table_page_order: VecDeque::new(),
            link_resolution: None,
            max_text_size,
            max_interpretation_size,
            max_text_glyphs,
            interpreted_text_sizes: HashMap::new(),
            interpreted_glyph_counts: HashMap::new(),
            interpreted_text_total: 0,
            interpreted_glyph_total: 0,
            isolated_content_pages: HashSet::new(),
            render_cache: crate::render_cache::DocumentRenderCache::new(),
            is_repaired: recovery.is_some(),
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
        self.content_guard.reset();
        self.link_resolution = None;
        self.invalidate_interpreted_pages();
    }

    /// Drop derived text/table pages and render caches while retaining the
    /// hayro snapshot.
    ///
    /// Fallback-font changes call this directly because cached hayro fonts
    /// depend on the font resolver as well as the snapshot.
    fn invalidate_interpreted_pages(&mut self) {
        self.render_cache.reset();
        self.text_pages.clear();
        self.text_page_order.clear();
        self.table_pages.clear();
        self.table_page_order.clear();
        self.interpreted_text_sizes.clear();
        self.interpreted_glyph_counts.clear();
        self.interpreted_text_total = 0;
        self.interpreted_glyph_total = 0;
    }

    /// Return the glyph payload still available to one page interpretation.
    fn text_budget(&self, page_number: u32) -> PyResult<Option<usize>> {
        let Some(limit) = self.max_text_size else {
            return Ok(None);
        };
        if let Some(admitted) = self.interpreted_text_sizes.get(&page_number) {
            return Ok(Some(*admitted));
        }
        Ok(Some(limit.saturating_sub(self.interpreted_text_total)))
    }

    /// Return the positioned glyph records still available to one page.
    fn glyph_budget(&self, page_number: u32) -> PyResult<Option<usize>> {
        let Some(limit) = self.max_text_glyphs else {
            return Ok(None);
        };
        if let Some(admitted) = self.interpreted_glyph_counts.get(&page_number) {
            return Ok(Some(*admitted));
        }
        Ok(Some(limit.saturating_sub(self.interpreted_glyph_total)))
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
        let text_total = self.interpreted_text_total.checked_add(text_size);
        let glyph_total = self.interpreted_glyph_total.checked_add(glyph_count);
        if let Some(limit) = self.max_text_size {
            let used = text_total.ok_or_else(|| {
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
            let used = glyph_total.ok_or_else(|| {
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
        // Overflow is reachable only without a configured limit, where the
        // totals are never compared.
        self.interpreted_text_total = text_total.unwrap_or(usize::MAX);
        self.interpreted_glyph_total = glyph_total.unwrap_or(usize::MAX);
        Ok(())
    }

    /// Bound an explicit page batch, counting pages only above the floor.
    fn check_page_entries(&self, entries: usize, verb: &str) -> PyResult<()> {
        if entries <= MIN_PAGE_ENTRY_LIMIT {
            return Ok(());
        }
        check_page_entry_count(entries, self.doc.page_count()?, verb)
    }

    /// Return the ObjectId of a one-based page.
    fn page_id(&self, page_number: u32) -> PyResult<ObjectId> {
        self.doc.page_id(page_number)
    }

    /// Read a page attribute while resolving inheritance and indirect references.
    fn resolve_page_attr(&self, page_id: ObjectId, key: &[u8]) -> PyResult<Option<Object>> {
        let mut current = Some(page_id);
        let mut visited = Vec::new();
        visited
            .try_reserve_exact(MAX_INHERITED_PAGE_TREE_DEPTH)
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to inspect inherited page attribute: {error}"
                ))
            })?;
        while let Some(id) = current {
            if visited.contains(&id) {
                return Err(PdfError::new_err(
                    "page inheritance tree contains a reference cycle",
                ));
            }
            if visited.len() >= MAX_INHERITED_PAGE_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "page inheritance tree exceeds the {MAX_INHERITED_PAGE_TREE_DEPTH}-level safety limit"
                )));
            }
            visited.push(id);
            // `page_id` comes from the page index, so only a parent can end
            // the walk here.
            let Some(dict) = inheritance_node(&self.doc, id)? else {
                break;
            };
            if page_attribute_has_value(&self.doc, dict, key)
                && let Ok(value) = dict.get(key)
            {
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
        self.resolve_display_geometry(self.page_id(page_number)?)
    }

    /// Display geometry of one page object, without walking the page tree.
    fn resolve_display_geometry(&self, page_id: ObjectId) -> PyResult<([f64; 4], i64)> {
        let rotation = self.resolve_page_rotation(page_id)?;
        let boxed = self
            .resolve_page_box(page_id, "CropBox")?
            .or(self.resolve_page_box(page_id, "MediaBox")?)
            .unwrap_or((0.0, 0.0, 595.0, 842.0));
        let (x0, y0, x1, y1) = boxed;
        Ok(([x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)], rotation))
    }

    /// Return inherited, normalized 0..360 rotation for one page object.
    fn resolve_page_rotation(&self, page_id: ObjectId) -> PyResult<i64> {
        match self.resolve_page_attr(page_id, b"Rotate")? {
            Some(obj) => Ok(obj.as_i64().map_err(to_py_err)?.rem_euclid(360)),
            None => Ok(0),
        }
    }

    /// Return a resolved page box of one page object, or None when absent.
    fn resolve_page_box(
        &self,
        page_id: ObjectId,
        key: &str,
    ) -> PyResult<Option<(f64, f64, f64, f64)>> {
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

    /// Validate one drawing insertion before caches or PDF objects are changed.
    fn prepare_page_content(
        &self,
        page_number: u32,
        overlay: bool,
    ) -> PyResult<(ObjectId, draw::ContentAppendPlan)> {
        let page_id = self.page_id(page_number)?;
        let plan = draw::prepare_push_content(
            &self.doc,
            page_id,
            self.isolated_content_pages.contains(&page_id),
            overlay,
        )
        .map_err(PdfError::new_err)?;
        Ok((page_id, plan))
    }

    /// Prepare all page-owned drawing targets before dependent work begins.
    fn prepare_page_drawing(
        &self,
        page_number: u32,
        overlay: bool,
        kind: PageResourceKind,
    ) -> PyResult<(ObjectId, PageDrawingPlan)> {
        let (page_id, content) = self.prepare_page_content(page_number, overlay)?;
        let resource = prepare_page_resource(&self.doc, page_id, kind)?;
        Ok((page_id, PageDrawingPlan { content, resource }))
    }

    /// Append one prepared stream and retain verified in-memory isolation state.
    ///
    /// New streams and the page's `/Contents` keep the page index.
    fn push_page_content(
        &mut self,
        page_id: ObjectId,
        ops: Vec<u8>,
        plan: draw::ContentAppendPlan,
    ) -> PyResult<()> {
        let (contents, is_isolated) = draw::add_content_streams(&mut self.doc, ops, plan);
        self.doc
            .set_page_object_entry(page_id, "Contents", Object::Array(contents))?;
        if is_isolated {
            self.isolated_content_pages.insert(page_id);
        }
        Ok(())
    }

    /// Bind a prepared resource slot and replace the page without allocation.
    ///
    /// The replacement keeps the page's walked entries, so the page index survives.
    fn commit_drawing_resource(
        &mut self,
        page_id: ObjectId,
        plan: PageResourcePlan,
        resource_id: ObjectId,
    ) -> PyResult<()> {
        let page = plan.bind(resource_id)?;
        self.doc.replace_page(page_id, page);
        Ok(())
    }

    /// Import and place a page from an owned source snapshot.
    fn place_pdf_page(
        &mut self,
        page_id: ObjectId,
        target_geometry: ([f64; 4], i64),
        form_plan: FormImportPlan,
        placement: PagePlacement,
        drawing_plan: PageDrawingPlan,
    ) -> PyResult<()> {
        let (form_id, src_crop, src_rotation) = commit_page_form_import(&mut self.doc, form_plan);
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
        let ops = draw::draw_ops(matrix, drawing_plan.resource.name());
        self.commit_drawing_resource(page_id, drawing_plan.resource, form_id)?;
        self.push_page_content(page_id, ops, drawing_plan.content)
    }

    /// Draw validated subset-embedded text; see `insert_embedded_text`.
    #[allow(clippy::too_many_arguments)]
    fn draw_embedded_text(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        point: (f64, f64),
        lines: &[String],
        font_data: FontBytes,
        font_index: u32,
        fontsize: f64,
        color: (f64, f64, f64),
        overlay: bool,
    ) -> PyResult<()> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
        let (pdf_width, pdf_height) = (crop[2] - crop[0], crop[3] - crop[1]);
        let page_size = if matches!(rotation, 90 | 270) {
            (pdf_height, pdf_width)
        } else {
            (pdf_width, pdf_height)
        };
        let generated = py.detach(|| {
            generate::embedded_text_page(
                page_size, point, lines, font_data, font_index, fontsize, color,
            )
            .map_err(PdfError::new_err)
        })?;
        let generated_doc = generated_pdf::load(&generated)
            .map_err(|error| lopdf_err(Some("failed to import generated text"), &error))?;
        let placement = PagePlacement {
            rect: [0.0, 0.0, page_size.0, page_size.1],
            keep_proportion: false,
        };
        let form_plan =
            py.detach(|| prepare_page_form_import(self.doc.max_id, generated_doc, 1))?;
        self.invalidate_hayro_pdf();
        py.detach(|| {
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                form_plan,
                placement,
                drawing_plan,
            )
        })
    }

    /// Lay out and draw validated subset-embedded text inside a display rectangle.
    ///
    /// An overflow returns its negative spare height without mutating the PDF.
    #[allow(clippy::too_many_arguments)]
    fn draw_embedded_textbox(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        text: &str,
        font_data: FontBytes,
        font_index: u32,
        fontsize: f64,
        line_height: f64,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
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
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
        let generated_doc = generated_pdf::load(&generated)
            .map_err(|error| lopdf_err(Some("failed to import generated text"), &error))?;
        let placement = PagePlacement {
            rect: [0.0, 0.0, page_size.0, page_size.1],
            keep_proportion: false,
        };
        let form_plan =
            py.detach(|| prepare_page_form_import(self.doc.max_id, generated_doc, 1))?;
        self.invalidate_hayro_pdf();
        py.detach(|| {
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                form_plan,
                placement,
                drawing_plan,
            )
        })?;
        Ok(spare_height)
    }

    /// Flatten AcroForm fields into `(full name, ObjectId, FT, Ff, V)`.
    ///
    /// FT/Ff/V inherit from parents when a field omits them or holds a null
    /// value, an indirect null, or a reference to a missing object; FT and Ff
    /// also inherit past any other unreadable value. Full names join `/T`
    /// components with dots. A leaf has resolved FT and no child carrying `/T`.
    /// Traversal and returned text are bounded so malformed trees cannot
    /// produce partial results.
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

        // Each field with a resolved type reserves its leaf entry before its
        // kids are read, so a nameless intermediate field folded into it can
        // withdraw the entry when it holds a child field.
        let mut leaves: Vec<Option<FormFieldEntry>> = Vec::new();
        let mut stack: Vec<FieldNode> = Vec::new();
        stack.try_reserve_exact(fields.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate AcroForm field-tree stack: {error}"
            ))
        })?;
        let top_level_prefix = Arc::new(String::new());
        for field in fields {
            // As in `/Kids`, an indirect null or a reference to a missing
            // object costs only its edge, like a direct entry.
            if let Ok(id) = field.as_reference()
                && !is_null_equivalent(&self.doc, field)
            {
                let prefix = Arc::clone(&top_level_prefix);
                stack.push((id, prefix, None, 0, None, 1, FieldRole::Field));
            }
        }
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
        while let Some((id, prefix, inh_ft, inh_ff, inh_v, depth, role)) = stack.pop() {
            if !visit_form_node(&mut visited, id)? {
                continue;
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
            // A node without a partial name shares the name above it.
            let name = match component {
                Some(component) if prefix.is_empty() => Arc::new(component),
                Some(component) => {
                    let len = component
                        .len()
                        .checked_add(1)
                        .and_then(|len| len.checked_add(prefix.len()))
                        .ok_or_else(|| {
                            PdfError::new_err(
                                "AcroForm field-name length exceeds the platform size limit",
                            )
                        })?;
                    let mut name = String::new();
                    name.try_reserve_exact(len).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow AcroForm materialized field name: {error}"
                        ))
                    })?;
                    name.push_str(&prefix);
                    name.push('.');
                    name.push_str(&component);
                    Arc::new(name)
                }
                None => prefix,
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
                            let decoded = try_lossy_form_string(name, "decoded field type")?;
                            add_form_budget(
                                &mut decoded_name_bytes,
                                decoded.len(),
                                MAX_FORM_FIELD_NAME_BYTES,
                                "decoded field-name/type",
                            )?;
                            Some(Arc::new(decoded))
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
            let value = match inheritable_field_value(&self.doc, dict, b"V") {
                Some(object) => bounded_form_value(
                    &self.doc,
                    object,
                    &mut encoded_value_bytes,
                    &mut decoded_value_bytes,
                    &mut value_items,
                )?
                .map(Arc::from),
                None => inh_v,
            };

            let mut has_child_fields = false;
            let leaf = match role {
                FieldRole::Field if ft.is_some() => {
                    try_push_form(&mut leaves, None, "returned field entries")?;
                    Some(leaves.len() - 1)
                }
                FieldRole::Field => None,
                FieldRole::Folded(leaf) => leaf,
            };
            if let Some(kids) = field_kids(&self.doc, dict) {
                add_form_tree_edges(&mut edges, kids.len())?;
                for kid in kids {
                    // An ignored entry costs only its edge. Every other entry
                    // that is neither a child field nor an intermediate field
                    // counts toward the widget budget, as does an intermediate
                    // that looks like a widget, so that budget covers every
                    // widget that `field_widgets` can return.
                    let kid = classify_field_kid(&self.doc, kid);
                    if matches!(
                        kid,
                        FieldKid::Widget(_)
                            | FieldKid::Unusable
                            | FieldKid::Intermediate { widget: true, .. }
                    ) {
                        widget_refs = widget_refs.saturating_add(1);
                        if widget_refs > MAX_FORM_FIELD_WIDGETS {
                            return Err(PdfError::new_err(format!(
                                "AcroForm field tree exceeds the {MAX_FORM_FIELD_WIDGETS}-widget safety limit"
                            )));
                        }
                    }
                    // An intermediate is walked like a child field, but it adds
                    // no name component and reports its child fields to the
                    // field it folds into.
                    let (kid_id, kid_role) = match kid {
                        FieldKid::Field(kid_id) => {
                            has_child_fields = true;
                            (kid_id, FieldRole::Field)
                        }
                        FieldKid::Intermediate { id, .. } => (id, FieldRole::Folded(leaf)),
                        FieldKid::Widget(_) | FieldKid::Unusable | FieldKid::Ignored => continue,
                    };
                    if depth >= MAX_FORM_FIELD_TREE_DEPTH {
                        return Err(PdfError::new_err(format!(
                            "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_DEPTH}-level safety limit"
                        )));
                    }
                    try_push_form(
                        &mut stack,
                        (
                            kid_id,
                            Arc::clone(&name),
                            ft.clone(),
                            ff,
                            value.clone(),
                            depth + 1,
                            kid_role,
                        ),
                        "field-tree stack",
                    )?;
                }
            }
            if let Some(index) = leaf {
                if has_child_fields {
                    leaves[index] = None;
                } else if let (FieldRole::Field, Some(ft)) = (role, ft) {
                    // Copy the name only while another node shares it, such
                    // as an intermediate folded into this field.
                    let name = Arc::try_unwrap(name)
                        .or_else(|name| try_clone_form_string(&name, "returned field name"))?;
                    leaves[index] = Some((name, id, ft, ff, value));
                }
            }
        }
        let leaf_count = leaves.iter().flatten().count();
        if leaf_count > MAX_FORM_FIELD_ENTRIES {
            return Err(PdfError::new_err(format!(
                "AcroForm field tree exceeds the {MAX_FORM_FIELD_ENTRIES}-entry safety limit"
            )));
        }
        let mut out = Vec::new();
        out.try_reserve_exact(leaf_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate AcroForm returned field entries: {error}"
            ))
        })?;
        for (name, id, ft, ff, value) in leaves.into_iter().flatten() {
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
        out.sort_unstable_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
        Ok(out)
    }

    /// Return the widgets that the walk classifies below a field in document
    /// order, or the field itself when it has none.
    ///
    /// The widgets of a nameless intermediate field take its place among the
    /// kids, and each intermediate is expanded once, so a cycle ends. The walk
    /// visits every intermediate below a field it returns, so a successful
    /// walk has already met the node, edge, and widget limits enforced here;
    /// they keep this traversal bounded on its own. The frame stack holds one
    /// frame per intermediate on the current path, so the node limit bounds
    /// its depth too, while a depth limit could reject an intermediate that the
    /// walk reached at a smaller depth through another field.
    fn field_widgets(&self, field_id: ObjectId) -> PyResult<Vec<ObjectId>> {
        let mut widgets = Vec::new();
        let Ok(dict) = self.doc.get_object(field_id).and_then(Object::as_dict) else {
            try_push_form(&mut widgets, field_id, "field widgets")?;
            return Ok(widgets);
        };
        let kids = field_kids(&self.doc, dict).unwrap_or_default();
        let mut edges = 0usize;
        add_form_tree_edges(&mut edges, kids.len())?;
        let mut frames = Vec::new();
        try_push_form(&mut frames, kids.iter(), "field-widget stack")?;
        let mut expanded = HashSet::new();
        while let Some(frame) = frames.last_mut() {
            let Some(kid) = frame.next() else {
                frames.pop();
                continue;
            };
            let (kid_id, is_widget, folded_kids) = match classify_field_kid(&self.doc, kid) {
                FieldKid::Widget(kid_id) => (kid_id, true, None),
                FieldKid::Intermediate { id, kids, widget } => (id, widget, Some(kids)),
                FieldKid::Field(_) | FieldKid::Unusable | FieldKid::Ignored => continue,
            };
            if is_widget {
                if widgets.len() >= MAX_FORM_FIELD_WIDGETS {
                    return Err(PdfError::new_err(format!(
                        "AcroForm field exceeds the {MAX_FORM_FIELD_WIDGETS}-widget safety limit"
                    )));
                }
                try_push_form(&mut widgets, kid_id, "field widgets")?;
            }
            let Some(kids) = folded_kids else {
                continue;
            };
            // The field is the first expanded node, so a cycle back to it ends.
            if expanded.is_empty() {
                visit_form_node(&mut expanded, field_id)?;
            }
            if visit_form_node(&mut expanded, kid_id)? {
                add_form_tree_edges(&mut edges, kids.len())?;
                try_push_form(&mut frames, kids.iter(), "field-widget stack")?;
            }
        }
        if widgets.is_empty() {
            try_push_form(&mut widgets, field_id, "field widgets")?;
        }
        Ok(widgets)
    }

    fn visible_field_widgets(&self, field_id: ObjectId) -> PyResult<Vec<ObjectId>> {
        let mut visible = Vec::new();
        for widget_id in self.field_widgets(field_id)? {
            let is_visible = self
                .doc
                .get_object(widget_id)
                .and_then(Object::as_dict)
                .is_ok_and(looks_like_widget);
            if is_visible {
                try_push_form(&mut visible, widget_id, "visible field widgets")?;
            }
        }
        Ok(visible)
    }

    /// Borrow and bound every normal-appearance state name for one button field.
    fn button_appearance_states(
        &self,
        field_id: ObjectId,
    ) -> PyResult<(Vec<WidgetStateNames>, usize, usize)> {
        let widgets = self.visible_field_widgets(field_id)?;
        if widgets.len() > MAX_FORM_FIELD_WIDGETS {
            return Err(PdfError::new_err(format!(
                "AcroForm field exceeds the {MAX_FORM_FIELD_WIDGETS}-widget safety limit"
            )));
        }
        let mut result = Vec::new();
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
                for (state, _) in normal {
                    add_form_budget(
                        &mut encoded_name_bytes,
                        state.len(),
                        MAX_FORM_BUTTON_STATE_NAME_BYTES,
                        "encoded button-state name",
                    )?;
                    let state = try_clone_form_bytes(state, "button-state name")?;
                    try_push_form(&mut states, state, "button-state names")?;
                }
            }
            try_push_form(&mut result, (widget_id, states), "button widget states")?;
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
    ///
    /// A null value, an indirect null, and a reference to a missing object
    /// continue to the parent, as an absent key does.
    fn resolve_field_attr(&self, field_id: ObjectId, key: &[u8]) -> Option<Object> {
        let mut current = Some(field_id);
        let mut visited = HashSet::new();
        while let Some(id) = current {
            if !visited.insert(id) || visited.len() > 64 {
                return None;
            }
            let dict = self.doc.get_object(id).ok()?.as_dict().ok()?;
            if let Some(value) = inheritable_field_value(&self.doc, dict, key) {
                return Some(value.clone());
            }
            current = dict.get(b"Parent").and_then(Object::as_reference).ok();
        }
        None
    }

    /// Resolve the quadding (0 left, 1 centered, 2 right) of a variable-text
    /// field's appearances.
    ///
    /// When the bounded `/Parent` walk finds no `/Q`, the interactive form
    /// dictionary's document-wide `/Q` applies (ISO 32000-2 Table 225), where
    /// a null value, an indirect null, and a reference to a missing object
    /// also read as absent. A present non-integer or out-of-range value aligns
    /// left; on a field it still overrides the document default.
    fn field_quadding(&self, field_id: ObjectId) -> u8 {
        self.resolve_field_attr(field_id, b"Q")
            .or_else(|| {
                let acroform = self.doc.catalog().ok()?.get(b"AcroForm").ok()?;
                let acroform = deref_dict_ref(&self.doc, acroform)?;
                inheritable_field_value(&self.doc, acroform, b"Q").cloned()
            })
            .as_ref()
            .and_then(|object| resolve_i64(&self.doc, object))
            .and_then(|value| u8::try_from(value).ok())
            .filter(|value| *value <= 2)
            .unwrap_or(0)
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
        font_data: Option<&FontBytes>,
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
                        Arc::clone(font_data),
                        font_index,
                        (0.0, 0.0, 0.0),
                    ),
                    _ => generate::embedded_widget_text_page(
                        (style.layout_width, style.layout_height),
                        style.content_rect(),
                        value,
                        Arc::clone(font_data),
                        font_index,
                        matches!(layout, WidgetTextLayout::Multiline),
                        align,
                        (0.0, 0.0, 0.0),
                    ),
                }
                .map_err(generated_text_err)?;
                let generated_doc = generated_pdf::load(&generated).map_err(|error| {
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
        let content = style
            .decorated_text_ops(&text_ops)
            .map_err(generated_text_err)?;
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
                    let layout = if field_type.as_str() == "Tx" {
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
                    let align = self.field_quadding(field_id);
                    for widget_id in self.visible_field_widgets(field_id)? {
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
        for (_, field_id, field_type, flags, _) in self.collect_form_fields()? {
            let widgets = self.visible_field_widgets(field_id)?;
            let complete = match field_type.as_str() {
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
            };
            if !complete {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// Set a validated form-field value, restoring the document on any error.
    fn fill_form_field(
        &mut self,
        py: Python<'_>,
        name: &str,
        value: &str,
        font_data: Option<FontBytes>,
        font_index: u32,
    ) -> PyResult<()> {
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
            match self.set_form_field_inner(name, value, font_data.as_ref(), font_index) {
                Ok(()) => Ok(()),
                Err(error) => {
                    self.doc.replace(original);
                    Err(error)
                }
            }
        });
        if result.is_ok() {
            self.invalidate_hayro_pdf();
        }
        result
    }

    /// Mutating implementation kept behind an atomic clone in `fill_form_field`.
    fn set_form_field_inner(
        &mut self,
        name: &str,
        value: &str,
        font_data: Option<&FontBytes>,
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
                let layout = if ft.as_str() == "Tx" {
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
                let align = self.field_quadding(field_id);
                for widget_id in self.visible_field_widgets(field_id)? {
                    self.synthesize_text_appearance(
                        widget_id, value, layout, align, font_data, font_index,
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

    /// Validate and preallocate one annotation append before dependent objects exist.
    fn prepare_page_annotation_append(
        &mut self,
        page_id: ObjectId,
    ) -> PyResult<PageAnnotationAppendPlan> {
        enum Target {
            PageArray,
            IndirectArray(ObjectId),
            Missing,
        }

        let (target, existing) = {
            let page = self
                .doc
                .get_object(page_id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?;
            match page.get(b"Annots") {
                // An indirect null and a missing object start a new array, as
                // a null value does.
                Ok(Object::Reference(id)) => {
                    match non_null_object(&self.doc, *id).map_err(to_py_err)? {
                        Some(annots) => (
                            Target::IndirectArray(*id),
                            annots.as_array().map_err(to_py_err)?.len(),
                        ),
                        None => (Target::Missing, 0),
                    }
                }
                Ok(Object::Array(items)) => (Target::PageArray, items.len()),
                _ => (Target::Missing, 0),
            }
        };
        let total = existing
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("page annotations exceed the platform size limit"))?;
        if total > MAX_PAGE_ANNOTATIONS {
            return Err(PdfError::new_err(format!(
                "page annotations exceed the {MAX_PAGE_ANNOTATIONS}-entry safety limit"
            )));
        }

        match target {
            Target::IndirectArray(arr_id) => {
                // copy_page/select duplicates may share indirect Annots arrays.
                // Clone on write while shared so additions do not leak.
                let shared = self.doc.annotation_array_shared(page_id, arr_id)?;
                if shared {
                    let source = self
                        .doc
                        .get_object(arr_id)
                        .and_then(Object::as_array)
                        .map_err(to_py_err)?;
                    let mut detached = Vec::new();
                    detached.try_reserve_exact(total).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to allocate detached page annotations: {error}"
                        ))
                    })?;
                    for item in source {
                        detached.push(item.clone());
                    }
                    Ok(PageAnnotationAppendPlan::Replacement(detached))
                } else {
                    let items = self.doc.annotation_array_mut(arr_id)?;
                    items.try_reserve_exact(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow indirect page annotations: {error}"
                        ))
                    })?;
                    Ok(PageAnnotationAppendPlan::Indirect(arr_id))
                }
            }
            Target::PageArray => {
                let items = self.doc.page_annotations_mut(page_id)?;
                items.try_reserve_exact(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow page annotations: {error}"))
                })?;
                Ok(PageAnnotationAppendPlan::Direct)
            }
            Target::Missing => {
                let mut items = Vec::new();
                items.try_reserve_exact(1).map_err(|error| {
                    PdfError::new_err(format!("failed to allocate page annotations: {error}"))
                })?;
                Ok(PageAnnotationAppendPlan::Replacement(items))
            }
        }
    }

    /// Commit a preallocated annotation reference without growing a collection.
    fn commit_page_annotation_append(
        &mut self,
        page_id: ObjectId,
        plan: PageAnnotationAppendPlan,
        annot_id: ObjectId,
    ) -> PyResult<()> {
        match plan {
            PageAnnotationAppendPlan::Direct => {
                self.doc
                    .page_annotations_mut(page_id)?
                    .push(Object::Reference(annot_id));
            }
            PageAnnotationAppendPlan::Indirect(array_id) => {
                self.doc
                    .annotation_array_mut(array_id)?
                    .push(Object::Reference(annot_id));
            }
            PageAnnotationAppendPlan::Replacement(mut items) => {
                items.push(Object::Reference(annot_id));
                self.doc.set_page_annotations(page_id, items)?;
            }
        }
        Ok(())
    }

    /// Return the hayro view, preferring original bytes before normalization.
    ///
    /// Editing methods invalidate the cache, preserving the invariant that
    /// rendered state always reflects edits. Consecutive renders rebuild once.
    /// Extraction and rendering index hayro's pages by page number, so every
    /// view must resolve exactly the page-tree walker's pages, in order.
    fn hayro_view(&mut self) -> PyResult<&Arc<Pdf>> {
        if self.hayro_pdf.is_none() {
            let page_ids = self.doc.owned_page_ids()?;
            let text_markup_appearances = missing_text_markup_appearance_plans(&self.doc)?;
            let prepare_appearances = text_markup_appearances
                .as_ref()
                .is_some_and(|plans| !plans.is_empty());
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
            // hayro rebuilds an object stream's member table for every member
            // it looks up, so a document with a very large container reads
            // faster from its serialized graph, which has none. The original
            // bytes stay the fallback when the serialization is refused or
            // lists other pages.
            let serialized_pdf = (!prepare_appearances && self.prefers_serialized_graph())
                .then(|| self.render_snapshot(None, None).ok())
                .flatten()
                .filter(|pdf| hayro_pages_match(pdf, &page_ids));
            if serialized_pdf.is_some() {
                self.hayro_source = HayroSource::Unavailable;
            }
            let source_pdf = (!prepare_appearances && serialized_pdf.is_none())
                .then(|| self.hayro_source.take_bytes())
                .flatten()
                .and_then(|data| Pdf::new(data).ok())
                .filter(|pdf| hayro_pages_match(pdf, &page_ids));
            self.hayro_pdf_from_original = source_pdf.is_some();
            let pdf = match serialized_pdf.or(source_pdf) {
                Some(pdf) => pdf,
                None => {
                    let pdf =
                        self.render_snapshot(text_markup_appearances, None)?;
                    if hayro_pages_match(&pdf, &page_ids) {
                        pdf
                    } else {
                        // hayro 0.7 stops at the first page-tree kid that is
                        // not a dictionary and reads untyped kids as pages,
                        // while the walker skips both, so list only the
                        // walker's pages in a rendering copy.
                        let text_markup_appearances =
                            missing_text_markup_appearance_plans(&self.doc)?;
                        let pdf =
                            self.render_snapshot(text_markup_appearances, Some(&page_ids))?;
                        if !hayro_pages_match(&pdf, &page_ids) {
                            return Err(PdfError::new_err(
                                "the renderer could not resolve the document page tree",
                            ));
                        }
                        pdf
                    }
                }
            };
            self.content_guard.reset();
            self.hayro_pdf = Some(Arc::new(pdf));
        }
        Ok(self
            .hayro_pdf
            .as_ref()
            .expect("constructed immediately before"))
    }

    /// Whether the retained original input should give way to a serialization
    /// of the graph, because rebuilding hayro's member tables would cost more.
    fn prefers_serialized_graph(&self) -> bool {
        matches!(
            &self.hayro_source,
            HayroSource::Bytes(data)
                if object_stream_work::MemberTables::of(&self.doc).prefer_serialized_graph(data.len())
        )
    }

    /// Return the hayro view after clearing `pages` for nesting.
    ///
    /// hayro 0.7 overflows its stack, and kills the process, on arrays or
    /// dictionaries nested hundreds of thousands deep in a content stream, on
    /// Type 4 function braces nested as deeply, and on chains of thousands of
    /// colour spaces or functions that name each other. Every path that lets
    /// hayro interpret a page therefore starts here; a refusal raises
    /// `LimitError` before any interpretation or cache insertion.
    fn hayro_view_for(&mut self, pages: PageSelection<'_>) -> PyResult<&Arc<Pdf>> {
        self.hayro_view()?;
        let pdf = self
            .hayro_pdf
            .as_ref()
            .expect("hayro view was built immediately before");
        self.content_guard.check(pdf, pages)?;
        Ok(pdf)
    }

    /// Parse a serialized snapshot, normalizing a rendering copy when
    /// appearances or the page tree need it.
    ///
    /// `flattened_pages` lists the walker's pages directly under the root
    /// page-tree node. None of this changes the editable or saved document.
    fn render_snapshot(
        &mut self,
        text_markup_appearances: Option<Vec<TextMarkupAppearancePlan>>,
        flattened_pages: Option<&[ObjectId]>,
    ) -> PyResult<Pdf> {
        let normalize = text_markup_appearances
                .as_ref()
                .is_some_and(|plans| !plans.is_empty())
            || flattened_pages.is_some();
        let data = if normalize {
            let mut doc = if self.max_interpretation_size.is_some() {
                let data = self.current_bytes()?;
                Document::load_mem(&data).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to prepare bounded PDF for rendering: {}",
                        LopdfErrorChain(&error)
                    ))
                })?
            } else {
                self.doc.clone()
            };
            if let Some(plans) = text_markup_appearances {
                synthesize_missing_text_markup_appearances_for_render(&mut doc, plans)?;
            }
            if let Some(pages) = flattened_pages {
                let pages_id = page_tree_root(&doc)?;
                rebuild_flat_page_tree(&mut doc, pages_id, pages, None)?;
            }
            serialize_pdf_with_limit(
                &mut doc,
                None,
                self.max_interpretation_size,
                "interpretation_size",
                "serialized rendering and extraction snapshot",
            )?
        } else {
            self.current_bytes()?
        };
        Pdf::new(data)
            .map_err(|e| PdfError::new_err(format!("failed to parse PDF for rendering: {e:?}")))
    }

    /// Refresh a cached text page's LRU position; return false when uncached.
    fn touch_text_page(&mut self, page_number: u32) -> bool {
        if !self.text_pages.contains_key(&page_number) {
            return false;
        }
        self.text_page_order.retain(|number| *number != page_number);
        self.text_page_order.push_back(page_number);
        true
    }

    /// Interpret one uncached page with the caller's interpreter cache, then
    /// admit and insert it into the bounded text-page cache.
    ///
    /// `pdf` must be the document's detached hayro snapshot: the interpreter
    /// cache borrows it, so the caller temporarily takes `hayro_pdf` and
    /// restores it afterwards. Nothing here may touch `hayro_pdf` or
    /// `hayro_source`.
    fn interpret_text_page<'a>(
        &mut self,
        pdf: &'a Pdf,
        cache: &InterpreterCache<'a>,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<()> {
        debug_assert!(!self.text_pages.contains_key(&page_number));
        let text_budget = self.text_budget(page_number)?;
        let glyph_budget = self.glyph_budget(page_number)?;
        let pages = pdf.pages();
        let page = page_number
            .checked_sub(1)
            .and_then(|index| pages.get(index as usize))
            .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
        let text_page =
            crate::extract::TextPage::new(pdf, page, cache, settings, text_budget, glyph_budget)
                .map_err(text_page_limit_err)?;
        self.admit_text_usage(page_number, text_page.text_size(), text_page.glyph_count())?;
        self.warn_skipped_no_unicode(text_page.skipped_no_unicode());

        if self.text_pages.len() >= TEXT_PAGE_CACHE_CAPACITY
            && let Some(evicted) = self.text_page_order.pop_front()
        {
            self.text_pages.remove(&evicted);
        }
        self.text_pages.insert(page_number, text_page);
        self.text_page_order.push_back(page_number);
        Ok(())
    }

    /// Return a cached, owned interpretation of a one-based page.
    fn text_page(
        &mut self,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<&crate::extract::TextPage> {
        if !self.touch_text_page(page_number) {
            self.hayro_view_for(PageSelection::One(page_number))?;
            let pdf = self
                .hayro_pdf
                .take()
                .expect("hayro view was built immediately before");
            let cache = InterpreterCache::new();
            let interpreted = self.interpret_text_page(&pdf, &cache, page_number, settings);
            drop(cache);
            self.hayro_pdf = Some(pdf);
            interpreted?;
        }
        Ok(self
            .text_pages
            .get(&page_number)
            .expect("text page was interpreted immediately before"))
    }

    /// Append plain text for one page chunk from the detached hayro view.
    ///
    /// `max_output_size` bounds the complete `out` buffer, so the budget is
    /// shared by every chunk of one call. The same detached-snapshot contract
    /// as `interpret_text_page` applies.
    fn extract_text_batch<'a>(
        &mut self,
        pdf: &'a Pdf,
        cache: &InterpreterCache<'a>,
        page_numbers: &[u32],
        max_output_size: Option<usize>,
        settings: &InterpreterSettings,
        out: &mut String,
    ) -> PyResult<()> {
        for number in page_numbers {
            let remaining = max_output_size.map(|limit| limit.saturating_sub(out.len()));
            if !self.touch_text_page(*number) {
                self.interpret_text_page(pdf, cache, *number, settings.clone())?;
            }
            let page_text = self
                .text_pages
                .get(number)
                .expect("text page was interpreted immediately before")
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
        Ok(())
    }

    /// Refresh a cached table page's LRU position; return false when uncached.
    fn touch_table_page(&mut self, page_number: u32) -> bool {
        if !self.table_pages.contains_key(&page_number) {
            return false;
        }
        self.table_page_order
            .retain(|number| *number != page_number);
        self.table_page_order.push_back(page_number);
        true
    }

    /// Interpret one uncached page's tables with the caller's interpreter
    /// cache, then admit and insert them into the bounded table-page cache.
    ///
    /// The same detached-snapshot contract as `interpret_text_page` applies.
    fn interpret_table_page<'a>(
        &mut self,
        pdf: &'a Pdf,
        cache: &InterpreterCache<'a>,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<()> {
        debug_assert!(!self.table_pages.contains_key(&page_number));
        let text_budget = self.text_budget(page_number)?;
        let glyph_budget = self.glyph_budget(page_number)?;
        let pages = pdf.pages();
        let page = page_number
            .checked_sub(1)
            .and_then(|index| pages.get(index as usize))
            .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
        let table_page =
            crate::extract::TablePage::new(pdf, page, cache, settings, text_budget, glyph_budget)
                .map_err(text_page_limit_err)?;
        self.admit_text_usage(
            page_number,
            table_page.text_size(),
            table_page.glyph_count(),
        )?;
        self.warn_skipped_no_unicode(table_page.skipped_no_unicode());

        if self.table_pages.len() >= TABLE_PAGE_CACHE_CAPACITY
            && let Some(evicted) = self.table_page_order.pop_front()
        {
            self.table_pages.remove(&evicted);
        }
        self.table_pages.insert(page_number, table_page);
        self.table_page_order.push_back(page_number);
        Ok(())
    }

    /// Return a cached, owned table interpretation of a one-based page.
    fn table_page(
        &mut self,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<&crate::extract::TablePage> {
        if !self.touch_table_page(page_number) {
            self.hayro_view_for(PageSelection::One(page_number))?;
            let pdf = self
                .hayro_pdf
                .take()
                .expect("hayro view was built immediately before");
            let cache = InterpreterCache::new();
            let interpreted = self.interpret_table_page(&pdf, &cache, page_number, settings);
            drop(cache);
            self.hayro_pdf = Some(pdf);
            interpreted?;
        }
        Ok(self
            .table_pages
            .get(&page_number)
            .expect("table page was interpreted immediately before"))
    }

    /// Surface glyphs skipped for missing Unicode as one deduplicated warning.
    ///
    /// Extraction silently loses such glyphs (for example a Type 3 font
    /// without a ToUnicode CMap, LaurenzV/hayro#1331); keep the message static
    /// so repeated pages cannot grow the pending list.
    fn warn_skipped_no_unicode(&self, skipped: usize) {
        if skipped == 0 {
            return;
        }
        let message = "text extraction skipped glyphs with no Unicode mapping \
             (for example a Type 3 font without a ToUnicode CMap)";
        if let Ok(mut pending) = self.pending_warnings.lock()
            && !pending.iter().any(|m| m == message)
        {
            pending.push(message.to_owned());
        }
    }

    /// Build InterpreterSettings with fallbacks and the warning sink.
    fn interpreter_settings(&self) -> InterpreterSettings {
        let mut settings = InterpreterSettings::default();
        if self.fallback_fonts.is_configured() {
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
                // hayro also reports a check box whose `/AS /Off` has no Off
                // stream. ISO 32000 makes that appearance optional, and forms
                // such as f1040 omit it for every unchecked box, so this
                // warning would fire on valid input during every operation.
                InterpreterWarning::UnresolvedAnnotationAppearance => return,
                _ => "encountered an unsupported PDF construct during interpretation",
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
        clip: Option<(f64, f64, f64, f64)>,
    ) -> PyResult<hayro::vello_cpu::Pixmap> {
        let interpreter_settings = self.interpreter_settings();
        py.detach(|| {
            self.hayro_view_for(PageSelection::One(page_number))?;
            let pdf = self
                .hayro_pdf
                .as_ref()
                .expect("hayro view was built immediately before");
            self.render_cache
                .with_cache(pdf, &[page_number], |pdf, cache| {
                    render_pdf_page(
                        pdf,
                        cache,
                        &interpreter_settings,
                        page_number,
                        scale,
                        background,
                        clip,
                    )
                })
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

    /// Validate existing pages before importing or allocating additional objects.
    fn preflight_page_tree_rebuild(&self, pages: &[ObjectId]) -> PyResult<()> {
        for &page_id in pages {
            resolve_inherited_page_dict(&self.doc, page_id)?;
        }
        Ok(())
    }

    /// Import selected one-based pages from `other` in order into self's object
    /// space and return their ObjectIds; the caller connects root Kids.
    ///
    /// `other_pages` is `other`'s page-tree order. Materialize inherited page
    /// attributes and repoint Parent to root Pages.
    fn transplant_pages(
        &mut self,
        other: &Self,
        mut other_pages: Vec<ObjectId>,
        page_numbers: &[u32],
        pages_id: ObjectId,
    ) -> PyResult<Vec<ObjectId>> {
        let starting_id = self
            .doc
            .max_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        let mut other_doc = other.doc.clone();
        renumber_objects(&mut other_doc, starting_id, &mut other_pages)?;
        let new_max_id = other_doc.max_id;

        let mut ordered_ids = Vec::new();
        ordered_ids
            .try_reserve_exact(page_numbers.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate imported page order: {error}"))
            })?;
        for number in page_numbers {
            let id = page_id_from_index(&other_pages, *number)?;
            ordered_ids.push(id);
        }
        let mut imported_pages = HashSet::new();
        imported_pages
            .try_reserve(ordered_ids.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate imported page lookup: {error}"))
            })?;
        imported_pages.extend(ordered_ids.iter().copied());
        let mut named_destinations = None;
        let mut annotation_edits = HashMap::new();

        // The source page tree is discarded; materialize inheritance per page.
        let mut resolved_pages = Vec::new();
        resolved_pages
            .try_reserve_exact(ordered_ids.len())
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate imported page dictionaries: {error}"
                ))
            })?;
        for &page_id in &ordered_ids {
            let mut dict = resolve_inherited_page_dict(&other_doc, page_id)?;
            if let Some(annots) = prepare_imported_page_links(
                &other_doc,
                page_id,
                &other_pages,
                &imported_pages,
                &mut named_destinations,
                &mut annotation_edits,
            )? {
                if annots.is_empty() {
                    dict.remove(b"Annots");
                } else {
                    dict.set("Annots", annots);
                }
            }
            dict.set("Parent", pages_id);
            resolved_pages.push((page_id, dict));
        }
        drop(named_destinations);
        for (id, annotation) in annotation_edits {
            if let Some(annotation) = annotation {
                other_doc.objects.insert(id, Object::Dictionary(annotation));
            }
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

    /// Return current pages with `new_ids` inserted at zero-based `position`.
    ///
    /// `new_ids` must not yet be reachable from root Kids or the bounded index.
    fn spliced_page_order(
        pages: Vec<ObjectId>,
        new_ids: Vec<ObjectId>,
        position: Option<usize>,
    ) -> PyResult<Vec<ObjectId>> {
        let final_len = pages
            .len()
            .checked_add(new_ids.len())
            .ok_or_else(|| PdfError::new_err("page order exceeds the platform size limit"))?;
        let mut order = Vec::new();
        order.try_reserve_exact(final_len).map_err(|error| {
            PdfError::new_err(format!("failed to allocate spliced page order: {error}"))
        })?;
        order.extend(pages);
        let pos = position.unwrap_or(order.len()).min(order.len());
        order.splice(pos..pos, new_ids);
        Ok(order)
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
        self.rebuild_page_tree_with_cleanup(pages_id, ordered, None)
    }

    fn rebuild_page_tree_with_cleanup(
        &mut self,
        pages_id: ObjectId,
        ordered: Vec<ObjectId>,
        cleanup: Option<&PageCleanupPlan>,
    ) -> PyResult<()> {
        rebuild_flat_page_tree(&mut self.doc, pages_id, &ordered, cleanup)
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
        cache: &mut LinkResolutionCache,
        encoded_bytes: &mut usize,
        returned_bytes: &mut usize,
    ) -> PyResult<ResolvedDestination> {
        let doc = &self.doc;
        let mut nameddest = None;
        let resolved = deref_object(doc, dest);
        let destination = if let Object::Name(name) | Object::String(name, _) = resolved {
            nameddest = Some(bounded_annotation_bytes(
                name,
                encoded_bytes,
                returned_bytes,
                "named destination",
            )?);
            match cache.named_destination(doc, name)? {
                Some(destination) => destination,
                None => lookup_legacy_named_dest(doc, name)
                    .map_or(LinkDestination::UNRESOLVED, |found| {
                        LinkDestination::from_object(doc, found)
                    }),
            }
        } else {
            LinkDestination::from_object(doc, resolved)
        };
        // Element 0 should be a page reference, but some producers write a
        // zero-based integer. Convert it without truncation and require an
        // existing page; anything else stays unresolved like an unknown
        // page reference.
        let page = match destination.page {
            DestinationPage::Reference(id) => doc.page_numbers()?.get(&id).copied(),
            DestinationPage::Index(index) => {
                let page_count = doc.page_count()?;
                usize::try_from(index)
                    .ok()
                    .filter(|index| *index < page_count)
                    .and_then(|index| u32::try_from(index + 1).ok())
            }
            _ => None,
        };
        let mut to = None;
        let mut zoom = None;
        if let Some(page_number) = page
            && let Ok(page_id) = doc.page_id(page_number)
            && let Ok((crop, rotation)) = self.resolve_display_geometry(page_id)
        {
            let [first, second, third] = destination.numbers;
            match destination.fit {
                DestinationFit::Xyz => {
                    // left/top may be Null; default to the crop's left/top edge.
                    let left = first.unwrap_or(crop[0]);
                    let top = second.unwrap_or(crop[3]);
                    zoom = third.filter(|z| *z != 0.0);
                    to = Some(draw::pdf_to_display(crop, rotation, left, top));
                }
                DestinationFit::Horizontal => {
                    let top = first.unwrap_or(crop[3]);
                    to = Some(draw::pdf_to_display(crop, rotation, crop[0], top));
                }
                DestinationFit::Vertical => {
                    let left = first.unwrap_or(crop[0]);
                    to = Some(draw::pdf_to_display(crop, rotation, left, crop[3]));
                }
                DestinationFit::Other => {}
            }
        }
        Ok((page, to, zoom, nameddest))
    }

    /// Read one page's link annotations, resolving destinations through the
    /// page index and `cache` so repeated reads do not walk the page or name
    /// trees again.
    fn page_links(
        &self,
        page_number: u32,
        cache: &mut LinkResolutionCache,
    ) -> PyResult<Vec<LinkTuple>> {
        let page_id = self.doc.page_id(page_number)?;
        let (crop, rotation) = self.resolve_display_geometry(page_id)?;
        let Some(annots) = self.page_annotation_items(page_id)? else {
            return Ok(Vec::new());
        };
        let mut out = Vec::new();
        let mut encoded_bytes = 0usize;
        let mut returned_bytes = 0usize;
        for item in annots {
            let dict = match item {
                Object::Reference(id) => match self.doc.get_object(*id).and_then(Object::as_dict) {
                    Ok(dict) => dict,
                    Err(_) => continue,
                },
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
                                cache,
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
                let (page, to, zoom, name) =
                    self.resolve_dest(dest, cache, &mut encoded_bytes, &mut returned_bytes)?;
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
    let mut stack = Vec::new();
    stack.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate page-label number-tree stack: {error}"
        ))
    })?;
    stack.push((root, 1usize));
    let mut nodes = 0usize;
    let mut edges = 0usize;
    let mut pairs_seen = 0usize;
    let mut encoded_text_bytes = 0usize;
    let mut decoded_text_bytes = 0usize;
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object {
            if visited.contains(id) {
                continue;
            }
            visited.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow page-label cycle detection: {error}"
                ))
            })?;
            visited.insert(*id);
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
                if out.len() == out.capacity() {
                    out.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow returned page-label entries: {error}"
                        ))
                    })?;
                }
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
            stack.try_reserve(kids.len()).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow page-label number-tree stack: {error}"
                ))
            })?;
            for kid in kids {
                stack.push((kid, depth + 1));
            }
        }
    }
    out.sort_unstable_by(|left, right| {
        left.0
            .cmp(&right.0)
            .then_with(|| left.1.cmp(&right.1))
            .then_with(|| left.2.cmp(&right.2))
            .then_with(|| left.3.cmp(&right.3))
    });
    Ok(out)
}

/// Visit valid `(name, FileSpec)` items in the EmbeddedFiles name tree.
///
/// Recurse through `/Kids` with depth, cycle, node, entry, and decoded-name
/// guards. Nodes and FileSpecs remain borrowed so read operations do not clone
/// adversarial direct object shapes. A FileSpec that is null, an indirect
/// null, or a reference to a missing object is skipped, so attachment
/// rewrites drop it. Returning `Some` stops the walk early.
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
    let mut stack = Vec::new();
    stack.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!("failed to allocate attachment tree stack: {error}"))
    })?;
    stack.push((root, 1usize));
    let mut nodes = 0usize;
    let mut edges = 0usize;
    let mut pairs_seen = 0usize;
    let mut encoded_name_bytes = 0usize;
    let mut name_bytes = 0usize;
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object {
            if visited.contains(id) {
                continue;
            }
            if visited.len() == visited.capacity() {
                visited.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow attachment tree cycle set: {error}"))
                })?;
            }
            visited.insert(*id);
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
                if !matches!(value, Object::Reference(_) | Object::Dictionary(_))
                    || matches!(non_null_value(doc, value), Ok(None))
                {
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
            stack.try_reserve(kids.len()).map_err(|error| {
                PdfError::new_err(format!("failed to grow attachment tree stack: {error}"))
            })?;
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
    let mut pending = Vec::new();
    pending.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate inline attachment validation stack: {error}"
        ))
    })?;
    pending.push((object, 1usize));
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
        pending.try_reserve(child_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow inline attachment validation stack: {error}"
            ))
        })?;
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
        if out.len() == out.capacity() {
            out.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow attachment rewrite collection: {error}"
                ))
            })?;
        }
        out.push((name, value.clone()));
        Ok(None::<()>)
    })?;
    Ok(out)
}

/// Validate the name-dictionary target before attachment objects are added.
///
/// A null `/Names`, an indirect null, and a reference to a missing object
/// read as an absent entry, which the rewrite replaces with a new dictionary.
fn embedded_files_write_target(doc: &Document) -> PyResult<EmbeddedFilesWriteTarget> {
    let catalog = doc.catalog().map_err(to_py_err)?;
    let Ok(names) = catalog.get(b"Names") else {
        return Ok(EmbeddedFilesWriteTarget::Missing);
    };
    let Some(resolved) = non_null_value(doc, names).map_err(to_py_err)? else {
        return Ok(EmbeddedFilesWriteTarget::Missing);
    };
    match names {
        Object::Dictionary(_) => Ok(EmbeddedFilesWriteTarget::Inline),
        Object::Reference(id) => {
            resolved.as_dict().map_err(to_py_err)?;
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
    entries.sort_unstable_by(|a, b| a.0.cmp(&b.0));
    let flat_len = entries
        .len()
        .checked_mul(2)
        .ok_or_else(|| PdfError::new_err("attachment rewrite array length overflowed"))?;
    let mut flat = Vec::new();
    flat.try_reserve_exact(flat_len).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate attachment rewrite array: {error}"
        ))
    })?;
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
            let (mut doc, recovery) = recovery::load_document(&data, options).map_err(|error| {
                recovery_load_err(
                    Some(&format!("failed to load {path}")),
                    &error,
                    decoder_limit_code,
                )
            })?;
            // Compare objects with their cross-reference entries before any
            // object changes number.
            let original_bytes_match = original_bytes_match(&mut doc, recovery, &data)?;
            let renumbered = normalize_object_numbers(&mut doc)?;
            let decrypted = !doc.is_encrypted();
            let page_ids = validate_structural_limits(&doc, limits, decrypted)?;
            if decrypted {
                validate_decompression_limits(
                    &doc,
                    page_ids.as_deref(),
                    limits.max_decompressed_size,
                    limits.max_page_content_size,
                    limits.max_total_decompressed_size,
                )?;
            }
            // Image compression maps hayro's object IDs to lopdf's, which no
            // longer match the original bytes once objects are renumbered.
            let hayro_source = if original_bytes_match && !renumbered {
                HayroSource::from_owned(data, limits.max_interpretation_size)
            } else {
                HayroSource::Unavailable
            };
            Ok(Self::from_loaded_doc(
                doc,
                hayro_source,
                limits.max_text_size,
                limits.max_interpretation_size,
                limits.max_text_glyphs,
                recovery,
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
            let (mut doc, recovery) = recovery::load_document(data, options)
                .map_err(|error| recovery_load_err(None, &error, decoder_limit_code))?;
            // Compare objects with their cross-reference entries before any
            // object changes number.
            let original_bytes_match = original_bytes_match(&mut doc, recovery, data)?;
            let renumbered = normalize_object_numbers(&mut doc)?;
            let decrypted = !doc.is_encrypted();
            let page_ids = validate_structural_limits(&doc, limits, decrypted)?;
            if decrypted {
                validate_decompression_limits(
                    &doc,
                    page_ids.as_deref(),
                    limits.max_decompressed_size,
                    limits.max_page_content_size,
                    limits.max_total_decompressed_size,
                )?;
            }
            // Image compression maps hayro's object IDs to lopdf's, which no
            // longer match the original bytes once objects are renumbered.
            let hayro_source = if original_bytes_match && !renumbered {
                HayroSource::from_borrowed(data, limits.max_interpretation_size)?
            } else {
                HayroSource::Unavailable
            };
            Ok(Self::from_loaded_doc(
                doc,
                hayro_source,
                limits.max_text_size,
                limits.max_interpretation_size,
                limits.max_text_glyphs,
                recovery,
            ))
        })
    }

    /// Return `(pages, objects, streams, encoded stream bytes, direct depth)`.
    fn complexity(&self) -> PyResult<ComplexityTuple> {
        document_complexity(&self.doc)
    }

    /// Read metadata quickly without loading the complete document.
    ///
    /// Return `(Info string dict, page count, version, encrypted, recovery warning)`.
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
            let (meta, recovery) =
                recovery::load_metadata(&data, password.as_deref()).map_err(|error| {
                    recovery_load_err(
                        Some(&format!("failed to load {path}")),
                        &error,
                        "decompressed_size",
                    )
                })?;
            let (metadata, page_count, version, encrypted) = pdf_metadata_to_tuple(meta)?;
            Ok((
                metadata,
                page_count,
                version,
                encrypted,
                recovery.map(XrefRecovery::warning),
            ))
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
            let (meta, recovery) = recovery::load_metadata(data, password.as_deref())
                .map_err(|error| recovery_load_err(None, &error, "decompressed_size"))?;
            let (metadata, page_count, version, encrypted) = pdf_metadata_to_tuple(meta)?;
            Ok((
                metadata,
                page_count,
                version,
                encrypted,
                recovery.map(XrefRecovery::warning),
            ))
        })
    }

    /// Return whether opening recovered damaged cross-reference data.
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
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        language="ja"
    ))]
    fn set_fallback_font(
        &mut self,
        kind: &str,
        data: Vec<u8>,
        index: u32,
        max_font_size: Option<usize>,
        language: &str,
    ) -> PyResult<()> {
        validate_font_input(Some(&data), max_font_size)?;
        let pair = self
            .fallback_fonts
            .pair_mut(CjkFontLanguage::parse(language)?);
        let slot = match kind {
            "sans" => &mut pair.sans,
            "serif" => &mut pair.serif,
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
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        language="ja"
    ))]
    fn set_fallback_font_file(
        &mut self,
        py: Python<'_>,
        kind: &str,
        path: &str,
        index: u32,
        max_font_size: Option<usize>,
        language: &str,
    ) -> PyResult<()> {
        validate_font_input(None, max_font_size)?;
        CjkFontLanguage::parse(language)?;
        let data = py.detach(|| read_font_input(path, max_font_size))?;
        self.set_fallback_font(kind, data, index, max_font_size, language)
    }

    /// Atomically read and configure both bundled CJK fallback font paths.
    #[pyo3(signature = (
        sans_path,
        serif_path,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        language="ja"
    ))]
    fn set_fallback_font_files(
        &mut self,
        py: Python<'_>,
        sans_path: &str,
        serif_path: &str,
        max_font_size: Option<usize>,
        language: &str,
    ) -> PyResult<()> {
        validate_font_input(None, max_font_size)?;
        let language = CjkFontLanguage::parse(language)?;
        let (sans, serif) = py.detach(|| {
            let sans = read_font_input(sans_path, max_font_size)?;
            let serif = read_font_input(serif_path, max_font_size)?;
            Ok::<_, PyErr>((sans, serif))
        })?;
        let pair = self.fallback_fonts.pair_mut(language);
        pair.sans = Some((Arc::new(sans), 0));
        pair.serif = Some((Arc::new(serif), 0));
        self.invalidate_interpreted_pages();
        Ok(())
    }

    /// Atomically configure every discovered locale font pair.
    ///
    /// Files read by earlier documents are reused while unchanged; see
    /// `bundled_font_cache`. Return how many files were read from disk.
    #[pyo3(signature = (
        files,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE)
    ))]
    fn set_fallback_font_locale_files(
        &mut self,
        py: Python<'_>,
        files: Vec<(String, String, String)>,
        max_font_size: Option<usize>,
    ) -> PyResult<usize> {
        validate_font_input(None, max_font_size)?;
        if files.len() > 4 {
            return Err(PdfError::new_err(
                "cannot configure more than four CJK locale font pairs",
            ));
        }
        let mut seen = [false; 4];
        let mut requests = Vec::new();
        requests.try_reserve_exact(files.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate CJK locale font requests: {error}"
            ))
        })?;
        for (language, sans_path, serif_path) in files {
            let language = CjkFontLanguage::parse(&language)?;
            if seen[language.index()] {
                return Err(PdfError::new_err(
                    "each CJK font language can be configured only once",
                ));
            }
            seen[language.index()] = true;
            requests.push((language, sans_path, serif_path));
        }
        if requests.is_empty() {
            return Ok(0);
        }
        let loaded = py.detach(|| {
            crate::bundled_font_cache::load_locale_font_pairs(&requests, max_font_size)
        })?;
        let mut fallback_fonts = self.fallback_fonts.clone();
        for ((language, _, _), (sans, serif)) in requests.iter().zip(loaded.pairs) {
            let pair = fallback_fonts.pair_mut(*language);
            pair.sans = Some((sans, 0));
            pair.serif = Some((serif, 0));
        }
        self.fallback_fonts = fallback_fonts;
        self.invalidate_interpreted_pages();
        Ok(loaded.files_read)
    }

    /// Return how many installed font package files the shared slots have
    /// read since the process started, so tests can pin reuse.
    #[staticmethod]
    fn package_font_reads() -> usize {
        crate::bundled_font_cache::files_read()
    }

    /// Return `(sequential, fallback, mismatched)` counts of generated-PDF
    /// imports since the process started, so tests can pin the calling-thread
    /// parser; mismatches are counted only while verification is enabled.
    #[staticmethod]
    fn generated_pdf_loads() -> (usize, usize, usize) {
        generated_pdf::load_counts()
    }

    /// Compare every later calling-thread import with lopdf's own parse, so
    /// tests can verify identical documents for real generated output.
    #[staticmethod]
    fn verify_generated_pdf_loads(enabled: bool) {
        generated_pdf::set_verification(enabled);
    }

    /// Report whether the calling-thread parser reads `data` exactly as lopdf
    /// does, or `None` when it declines the input.
    #[staticmethod]
    fn compare_generated_pdf_load(data: &[u8]) -> Option<bool> {
        generated_pdf::compare_with_lopdf(data)
    }

    /// Report which input the current hayro snapshot was parsed from
    /// (`"original"` bytes, a `"serialized"` graph, or `"none"` before the
    /// first render or extraction and after an edit), so tests can pin when
    /// the original bytes give way to the serialization.
    fn snapshot_source(&self) -> &'static str {
        match (&self.hayro_pdf, self.hayro_pdf_from_original) {
            (None, _) => "none",
            (Some(_), true) => "original",
            (Some(_), false) => "serialized",
        }
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
            write_pdf_file_stably(&mut self.doc, path, None)
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
            write_pdf_file_stably(&mut self.doc, path, Some(modern_save_options()))
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

    /// Return the page count, walking the page tree once per edit.
    fn page_count(&self) -> PyResult<usize> {
        self.doc.page_count()
    }

    /// Return the PDF version string, such as `"1.7"`.
    fn version(&self) -> String {
        self.doc.version.clone()
    }

    /// Return the eight standard Info strings under aggregate text budgets.
    fn get_metadata(&self, py: Python<'_>) -> PyResult<HashMap<String, String>> {
        py.detach(|| collect_info_metadata(&self.doc))
    }

    /// Set an Info entry, deleting it when the value is empty.
    fn set_metadata(&mut self, key: &str, value: &str) -> PyResult<()> {
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
        self.set_metadata_entries(entries)
    }

    /// Atomically update multiple standard Info entries.
    fn set_metadata_batch(&mut self, entries: Vec<(String, String)>) -> PyResult<()> {
        self.set_metadata_entries(entries)
    }

    /// Delete one-based pages; duplicates name the same page.
    fn delete_pages(&mut self, page_numbers: Vec<u32>) -> PyResult<()> {
        if page_numbers.is_empty() {
            return Ok(());
        }

        let pages = self.doc.page_ids()?;
        check_page_entry_count(page_numbers.len(), pages.len(), "delete")?;
        let mut deleted = HashSet::new();
        deleted.try_reserve(page_numbers.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate deleted-page tracking: {error}"))
        })?;
        for page_number in page_numbers {
            deleted.insert(page_id_from_index(pages, page_number)?);
        }
        let retained_count = pages
            .len()
            .checked_sub(deleted.len())
            .ok_or_else(|| PdfError::new_err("deleted page count exceeds the page index"))?;
        let mut retained = Vec::new();
        retained
            .try_reserve_exact(retained_count)
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate retained page order: {error}"))
            })?;
        for &page_id in pages {
            if !deleted.contains(&page_id) {
                retained.push(page_id);
            }
        }
        let pages_id = self
            .doc
            .catalog()
            .map_err(to_py_err)?
            .get(b"Pages")
            .and_then(Object::as_reference)
            .map_err(|_| PdfError::new_err("Catalog Pages must be an indirect page-tree node"))?;

        let cleanup = PageCleanupPlan::prepare(&self.doc, &retained, &deleted)?;
        self.rebuild_page_tree_with_cleanup(pages_id, retained, Some(&cleanup))?;
        cleanup.apply(&mut self.doc);
        self.invalidate_hayro_pdf();
        prune_unreachable_objects(&mut self.doc)?;
        Ok(())
    }

    /// Extract text from a one-based page.
    ///
    /// Collect glyph Unicode/positions through the hayro interpreter and
    /// assemble reading-order text. CJK fallbacks also apply to extraction.
    fn extract_text(&mut self, py: Python<'_>, page_numbers: Vec<u32>) -> PyResult<String> {
        self.check_page_entries(page_numbers.len(), "extract text from")?;
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
        // An empty batch must stay a true no-op that never builds the
        // rendering and extraction snapshot or consults its size limit.
        if page_numbers.is_empty() {
            return Ok(String::new());
        }
        let settings = self.interpreter_settings();
        py.detach(|| {
            self.hayro_view_for(PageSelection::Many(&page_numbers))?;
            let pdf = self
                .hayro_pdf
                .take()
                .expect("hayro view was built immediately before");
            // Share one interpreter cache per bounded chunk so fonts reused by
            // several pages parse once per chunk instead of once per page,
            // while whole-document batches never retain every parsed font.
            // The output budget spans the complete call, not one chunk.
            let mut out = String::new();
            let mut extracted = Ok(());
            for chunk in page_numbers.chunks(PAGE_BATCH_CHUNK) {
                let cache = InterpreterCache::new();
                extracted = self.extract_text_batch(
                    &pdf,
                    &cache,
                    chunk,
                    max_output_size,
                    &settings,
                    &mut out,
                );
                drop(cache);
                if extracted.is_err() {
                    break;
                }
            }
            self.hayro_pdf = Some(pdf);
            extracted.map(|()| out)
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

    /// Return raw glyph layout for a one-based page.
    ///
    /// Return `(width, height, blocks)`, where block=`(bbox, lines)`,
    /// line=`(bbox, spans, direction, writing mode)`,
    /// span=`(bbox, chars, size, origin, font, flags)`, and
    /// char=`(whole Unicode mapping, bbox, baseline origin)`.
    #[allow(clippy::type_complexity)]
    fn extract_raw_layout(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<(f64, f64, Vec<crate::extract::RawBlockTuple>)> {
        let settings = self.interpreter_settings();
        py.detach(|| {
            self.text_page(page_number, settings)?
                .raw_layout()
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
            self.table_page(page_number, settings)?
                .tables(text_strategy, clip)
                .map_err(text_page_limit_err)
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
        let pdf = self.hayro_view_for(PageSelection::One(page_number))?;
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
        let pdf = self.hayro_view_for(PageSelection::One(page_number))?;
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
        let pdf = self.hayro_view_for(PageSelection::All)?;
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
            self.doc.replace(edited);
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
                .map_err(|error| match error {
                    crate::extract::SearchError::TooManyHits => {
                        let limit = max_hits.expect("bounded search hit count");
                        limit_err(
                            "search_hit_count",
                            format!("search results exceed the {limit}-hit safety limit"),
                        )
                    }
                    crate::extract::SearchError::Allocation(message) => PdfError::new_err(message),
                })
        })
    }

    /// Append every page from another document, whatever its page count.
    fn merge(&mut self, py: Python<'_>, other: &Self) -> PyResult<()> {
        let page_count = other.doc.page_count()?;
        let count = u32::try_from(page_count).map_err(|e| PdfError::new_err(e.to_string()))?;
        let mut all = Vec::new();
        all.try_reserve_exact(page_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate complete imported page range: {error}"
            ))
        })?;
        all.extend(1..=count);
        self.merge_pages(py, other, all, None)
    }

    /// Import specified one-based pages from another document in order.
    ///
    /// `position` is a zero-based insertion point; None appends. Flatten the
    /// page tree under root while inserting. Each source page is imported at
    /// most once because a repeated entry would reuse one page object twice.
    fn merge_pages(
        &mut self,
        py: Python<'_>,
        other: &Self,
        page_numbers: Vec<u32>,
        position: Option<usize>,
    ) -> PyResult<()> {
        if page_numbers.is_empty() {
            return Ok(());
        }
        let existing_pages = self.doc.owned_page_ids()?;
        let source_pages = other.doc.owned_page_ids()?;
        check_page_entry_count(page_numbers.len(), source_pages.len(), "merge")?;
        let mut imported = HashSet::new();
        imported.try_reserve(page_numbers.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate imported page tracking: {error}"
            ))
        })?;
        for &page_number in &page_numbers {
            page_id_from_index(&source_pages, page_number)?;
            if !imported.insert(page_number) {
                return Err(PdfError::new_err(format!(
                    "page {page_number} is repeated; imported page entries must be unique"
                )));
            }
        }
        self.preflight_page_tree_rebuild(&existing_pages)?;
        py.detach(|| {
            // Reserve Pages/Catalog IDs in an empty target to avoid source collisions.
            let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
            let new_ids = self.transplant_pages(other, source_pages, &page_numbers, pages_id)?;
            self.invalidate_hayro_pdf();
            let order = Self::spliced_page_order(existing_pages, new_ids, position)?;
            self.rebuild_page_tree(pages_id, order)?;
            // transplant_pages initially moves all non-page objects. Prune
            // attachments/metadata unreachable from selected pages or hidden
            // source data remains even for a full-range append.
            prune_unreachable_objects(&mut self.doc)?;
            Ok(())
        })
    }

    /// Insert a blank page at zero-based `position`; None appends.
    fn new_page(&mut self, position: Option<usize>, width: f32, height: f32) -> PyResult<()> {
        if !width.is_finite() || !height.is_finite() || width <= 0.0 || height <= 0.0 {
            return Err(PdfError::new_err(format!(
                "width / height must be positive finite values within PDF real-number range: ({width:?}, {height:?})"
            )));
        }
        let existing_pages = self.doc.owned_page_ids()?;
        self.preflight_page_tree_rebuild(&existing_pages)?;
        self.invalidate_hayro_pdf();
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
        let media_box = Object::Array(vec![
            Object::Real(0.0),
            Object::Real(0.0),
            Object::Real(width),
            Object::Real(height),
        ]);
        let page_id = self.doc.add_object(dictionary! {
            "Type" => "Page",
            "Parent" => pages_id,
            "MediaBox" => media_box.clone(),
            "CropBox" => media_box,
            "Rotate" => 0,
            "Resources" => Dictionary::new(),
        });
        let order = Self::spliced_page_order(existing_pages, vec![page_id], position)?;
        self.rebuild_page_tree(pages_id, order)?;
        prune_unreachable_objects(&mut self.doc)?;
        Ok(())
    }

    /// Copy a one-based page to zero-based `position`; None appends.
    ///
    /// The page dictionary is an independent copy with inheritance materialized;
    /// Contents and Resources remain shared with the source page.
    fn copy_page(&mut self, page_number: u32, position: Option<usize>) -> PyResult<()> {
        let existing_pages = self.doc.owned_page_ids()?;
        let source_id = page_id_from_index(&existing_pages, page_number)?;
        self.preflight_page_tree_rebuild(&existing_pages)?;
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
        let mut dict = resolve_inherited_page_dict(&self.doc, source_id)?;
        dict.set("Parent", pages_id);
        self.invalidate_hayro_pdf();
        let new_id = self.doc.add_object(Object::Dictionary(dict));
        let order = Self::spliced_page_order(existing_pages, vec![new_id], position)?;
        self.rebuild_page_tree(pages_id, order)?;
        prune_unreachable_objects(&mut self.doc)?;
        Ok(())
    }

    /// Keep specified one-based pages in order, also supporting reordering.
    ///
    /// PDF page-tree Parent must be unique, so duplicate selections require copies.
    fn select(&mut self, page_numbers: Vec<u32>) -> PyResult<()> {
        let pages = self.doc.page_ids()?;
        check_page_entry_count(page_numbers.len(), pages.len(), "select")?;
        let mut selected_ids = Vec::new();
        selected_ids
            .try_reserve_exact(page_numbers.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate selected page IDs: {error}"))
            })?;
        for number in &page_numbers {
            let page_id = page_id_from_index(pages, *number)?;
            selected_ids.push(page_id);
        }
        let existing_pages_id = self
            .doc
            .catalog()
            .ok()
            .and_then(|catalog| catalog.get(b"Pages").and_then(Object::as_reference).ok());
        if !selected_ids.is_empty() && existing_pages_id.is_none() {
            return Err(PdfError::new_err(
                "document pages are not connected to a root Pages dictionary",
            ));
        }
        if let Some(pages_id) = existing_pages_id {
            self.doc
                .get_object(pages_id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?;
        }

        let mut removed = HashSet::new();
        removed.try_reserve(pages.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate excluded page tracking: {error}"
            ))
        })?;
        removed.extend(pages.iter().copied());
        for page_id in &selected_ids {
            removed.remove(page_id);
        }
        let cleanup = PageCleanupPlan::prepare(&self.doc, &selected_ids, &removed)?;

        // For repeated pages, create a copy with inheritance materialized because
        // PDF page-tree Parent references must be unique.
        let mut seen = HashSet::new();
        seen.try_reserve(selected_ids.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate selected-page duplicate tracking: {error}"
            ))
        })?;
        let count = i64::try_from(selected_ids.len()).map_err(|error| {
            PdfError::new_err(format!("selected page count is not representable: {error}"))
        })?;
        let mut kids = Vec::new();
        kids.try_reserve_exact(selected_ids.len())
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate selected page-tree Kids: {error}"
                ))
            })?;
        let mut resolved_pages = Vec::new();
        resolved_pages
            .try_reserve_exact(selected_ids.len())
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate selected page dictionaries: {error}"
                ))
            })?;
        let mut planned_max_id = self.doc.max_id;
        for page_id in selected_ids {
            let use_id = if seen.insert(page_id) {
                page_id
            } else {
                planned_max_id = planned_max_id
                    .checked_add(1)
                    .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
                (planned_max_id, 0)
            };
            let mut dict = resolve_inherited_page_dict(&self.doc, page_id)?;
            cleanup.clean_page(page_id, &mut dict);
            if let Some(pages_id) = existing_pages_id {
                dict.set("Parent", pages_id);
            }
            kids.push(Object::Reference(use_id));
            resolved_pages.push((use_id, dict));
        }

        self.invalidate_hayro_pdf();
        let pages_id = match existing_pages_id {
            Some(pages_id) => pages_id,
            None => self.ensure_page_tree().map_err(to_py_err)?,
        };
        for (page_id, mut dict) in resolved_pages {
            dict.set("Parent", pages_id);
            self.doc.objects.insert(page_id, Object::Dictionary(dict));
        }
        self.doc.max_id = self.doc.max_id.max(planned_max_id);
        let pages_dict = self
            .doc
            .get_object_mut(pages_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        pages_dict.set("Kids", kids);
        pages_dict.set("Count", count);

        cleanup.apply(&mut self.doc);
        // Remove pages and intermediate nodes that became unreachable.
        prune_unreachable_objects(&mut self.doc)?;
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
        let pixmap = self.render_pixmap_impl(py, page_number, scale, background, None)?;
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
        self.check_page_entries(page_numbers.len(), "render")?;
        let interpreter_settings = self.interpreter_settings();
        py.detach(|| {
            self.hayro_view_for(PageSelection::Many(&page_numbers))?;
            let pdf = self
                .hayro_pdf
                .as_ref()
                .expect("hayro view was built immediately before");
            let max_pixels = page_numbers
                .iter()
                .try_fold(0u64, |maximum, &page_number| {
                    render_pixel_count(pdf, page_number, scale).map(|pixels| maximum.max(pixels))
                })
                .map_err(PdfError::new_err)?;
            let output_bytes = AtomicUsize::new(0);
            let renderer = BatchRenderer {
                interpreter_settings: &interpreter_settings,
                scale,
                background,
                max_output_size,
                output_bytes: &output_bytes,
            };
            // A serial batch starts from the document's retained cache, so
            // fonts parsed by earlier single-page or batch calls are reused.
            let render_serial = || renderer.render_serial(pdf, &self.render_cache, &page_numbers);
            #[cfg(target_os = "emscripten")]
            let rendered = {
                let _ = max_pixels;
                render_serial()
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
                    render_serial()
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
                    let indexed = pool.install(|| {
                        (0..worker_count)
                            .into_par_iter()
                            .map(|_| {
                                let mut cache = RenderCache::new();
                                let mut cached_pages = 0usize;
                                let mut rendered = Vec::new();
                                loop {
                                    let index = next_page.fetch_add(1, Ordering::Relaxed);
                                    let Some(&page_number) = page_numbers.get(index) else {
                                        break;
                                    };
                                    // Bound each worker cache like a serial chunk.
                                    if cached_pages == PAGE_BATCH_CHUNK {
                                        cache = RenderCache::new();
                                        cached_pages = 0;
                                    }
                                    cached_pages += 1;
                                    rendered.try_reserve(1).map_err(|error| {
                                        PdfError::new_err(format!(
                                            "failed to grow worker render results: {error}"
                                        ))
                                    })?;
                                    rendered
                                        .push((index, renderer.render(pdf, &cache, page_number)?));
                                }
                                Ok::<_, PyErr>(rendered)
                            })
                            .try_reduce(Vec::new, |mut left, mut right| {
                                left.try_reserve(right.len()).map_err(|error| {
                                    PdfError::new_err(format!(
                                        "failed to merge worker render results: {error}"
                                    ))
                                })?;
                                left.append(&mut right);
                                Ok(left)
                            })
                    });
                    let mut ordered = Vec::new();
                    ordered
                        .try_reserve_exact(page_numbers.len())
                        .map_err(|error| {
                            PdfError::new_err(format!(
                                "failed to allocate ordered render results: {error}"
                            ))
                        })?;
                    ordered.resize_with(page_numbers.len(), || None);
                    for (index, png) in indexed? {
                        ordered[index] = Some(png);
                    }
                    let mut rendered = Vec::new();
                    rendered
                        .try_reserve_exact(page_numbers.len())
                        .map_err(|error| {
                            PdfError::new_err(format!(
                                "failed to allocate final render results: {error}"
                            ))
                        })?;
                    for png in ordered {
                        rendered.push(png.expect("every claimed page is rendered"));
                    }
                    Ok(rendered)
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
        let pixmap = self.render_pixmap_impl(py, page_number, scale, background, clip)?;
        let width = u32::from(pixmap.width());
        let height = u32::from(pixmap.height());
        // Release the GIL: unpremultiplication touches every rendered pixel.
        let data = py.detach(|| rgba_bytes(pixmap));
        Ok(crate::pixmap::Pixmap {
            width,
            height,
            data: Arc::new(data),
        })
    }

    /// Release the render cache retained for this document on the calling thread.
    ///
    /// `Document.close` calls this so a closed document does not keep parsed
    /// fonts and glyph outlines alive until it is garbage collected.
    fn release_render_cache(&mut self) {
        self.render_cache.reset();
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
    /// hayro-svg 0.8 materializes its internal String before returning, so the
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
            let pdf = self.hayro_view_for(PageSelection::One(page_number))?;
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
            return Err(limit_err(
                "toc_entry_count",
                format!("cannot set more than {MAX_TOC_ENTRIES} TOC entries"),
            ));
        }
        let pages = self.doc.page_ids()?;
        let mut prepared = Vec::new();
        prepared.try_reserve_exact(entries.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate prepared TOC entries: {error}"))
        })?;
        let mut source_bytes = 0usize;
        let mut encoded_bytes = 0usize;
        let mut previous_level = 0u32;
        let mut max_level = 0usize;
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
            let page_id = page_id_from_index(pages, page)?;
            prepared.push((level, title, page_id));
            previous_level = level;
            max_level = max_level.max(level as usize);
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
        let mut parents: Vec<u32> = Vec::new();
        parents.try_reserve_exact(max_level).map_err(|error| {
            PdfError::new_err(format!("failed to allocate TOC parent stack: {error}"))
        })?;
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
            prune_unreachable_objects(&mut self.doc)?;
            return Ok(());
        }
        // parents[level - 1] = latest bookmark ID at that level.
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
        prune_unreachable_objects(&mut self.doc)?;
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
        self.resolve_page_rotation(self.page_id(page_number)?)
    }

    /// Set rotation for a one-based page; Python validates the value.
    fn set_page_rotation(&mut self, page_number: u32, rotation: i64) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        self.doc
            .set_page_entry(page_number, "Rotate", Object::Integer(rotation))
    }

    /// Return a resolved page box such as MediaBox/CropBox, or None when absent.
    fn get_page_box(&self, page_number: u32, key: &str) -> PyResult<Option<(f64, f64, f64, f64)>> {
        self.resolve_page_box(self.page_id(page_number)?, key)
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
        self.doc.set_page_entry(
            page_number,
            key,
            Object::Array(vec![
                Object::Real(x0),
                Object::Real(y0),
                Object::Real(x1),
                Object::Real(y1),
            ]),
        )
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
    fn prune_objects(&mut self) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        prune_unreachable_objects(&mut self.doc)
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
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
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
            let ops = draw::draw_ops(matrix, drawing_plan.resource.name());
            self.commit_drawing_resource(page_id, drawing_plan.resource, xobj_id)?;
            self.push_page_content(page_id, ops, drawing_plan.content)
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
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
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
            let ops = draw::draw_ops(matrix, drawing_plan.resource.name());
            self.commit_drawing_resource(page_id, drawing_plan.resource, xobj_id)?;
            self.push_page_content(page_id, ops, drawing_plan.content)
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
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
        let placement = PagePlacement {
            rect: [rect.0, rect.1, rect.2, rect.3],
            keep_proportion,
        };
        let form_plan = py.detach(|| {
            prepare_page_form_import(self.doc.max_id, other.doc.clone(), src_page_number)
        })?;
        self.invalidate_hayro_pdf();
        py.detach(|| {
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                form_plan,
                placement,
                drawing_plan,
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
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
        let placement = PagePlacement {
            rect: [rect.0, rect.1, rect.2, rect.3],
            keep_proportion,
        };
        let form_plan = py.detach(|| {
            let source = self.doc.clone();
            prepare_page_form_import(self.doc.max_id, source, src_page_number)
        })?;
        self.invalidate_hayro_pdf();
        py.detach(|| {
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                form_plan,
                placement,
                drawing_plan,
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
    /// Resolve GoTo names from `/Names` trees and legacy `/Dests`. The page
    /// index and name-tree index persist until the next edit.
    fn read_links(&mut self, py: Python<'_>, page_number: u32) -> PyResult<Vec<LinkTuple>> {
        py.detach(|| {
            // Detach the cache so link collection can borrow the document, and
            // restore it on success and failure because reads never edit.
            let mut cache = self.link_resolution.take().unwrap_or_default();
            let links = self.page_links(page_number, &mut cache);
            self.link_resolution = Some(cache);
            links
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
        let annotation_append = self.prepare_page_annotation_append(page_id)?;

        let mut quads = Vec::new();
        quads.try_reserve_exact(rects.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate highlight quads: {error}"))
        })?;
        for (x0, y0, x1, y1) in rects {
            quads.push(draw::display_rect_quad_pdf(
                crop,
                rotation,
                [x0, y0, x1, y1],
            ));
        }
        let bbox = draw::bounding_rect_iter(quads.iter().flatten().copied());
        let appearance_ops = draw::highlight_ap_ops(&quads, color).map_err(PdfError::new_err)?;
        let quad_point_count = quads
            .len()
            .checked_mul(8)
            .ok_or_else(|| PdfError::new_err("highlight QuadPoints exceed the platform limit"))?;
        let mut quad_points = Vec::new();
        quad_points
            .try_reserve_exact(quad_point_count)
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate highlight QuadPoints: {error}"))
            })?;
        for &(x, y) in quads.iter().flatten() {
            quad_points.push(Object::Real(x as f32));
            quad_points.push(Object::Real(y as f32));
        }

        // Appearance BBox equals annotation Rect and draws in page space.
        self.invalidate_hayro_pdf();
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
        let ap_id = self
            .doc
            .add_object(Stream::new(form_dict, appearance_ops).with_compression(false));

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
        self.commit_page_annotation_append(page_id, annotation_append, annot_id)
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
        let annotation_append = self.prepare_page_annotation_append(page_id)?;
        let quad = draw::display_rect_quad_pdf(crop, rotation, [rect.0, rect.1, rect.2, rect.3]);
        let bbox = draw::bounding_rect(&quad);
        let annotation = dictionary! {
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
        };
        self.invalidate_hayro_pdf();
        let annot_id = self.doc.add_object(annotation);
        self.commit_page_annotation_append(page_id, annotation_append, annot_id)
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
        let nums_len = labels.len().checked_mul(2).ok_or_else(|| {
            PdfError::new_err("page-label output exceeds the platform size limit")
        })?;
        let mut nums = Vec::new();
        nums.try_reserve_exact(nums_len).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate page-label output array: {error}"
            ))
        })?;
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
            if ft.as_str() != "Btn" {
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
        self.fill_form_field(py, name, value, font_data.map(Arc::new), font_index)
    }

    /// Set a form-field value using bounded OpenType font input from a path.
    ///
    /// `package_font` names an installed package face as for
    /// `insert_embedded_text_file`; explicit font paths are read on every call.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        name,
        value,
        path,
        font_index=0,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        package_font=None
    ))]
    fn set_form_field_file(
        &mut self,
        py: Python<'_>,
        name: &str,
        value: &str,
        path: &str,
        font_index: u32,
        max_font_size: Option<usize>,
        package_font: Option<(String, String)>,
    ) -> PyResult<()> {
        validate_form_field_input(name, Some(value))?;
        let data = load_generation_font(py, path, package_font, max_font_size)?;
        self.fill_form_field(py, name, value, Some(data), font_index)
    }

    /// Return sorted attachment names independent of name-tree order.
    fn embfile_names(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        py.detach(|| {
            let mut names = Vec::new();
            visit_embedded_files(&self.doc, |name, _| {
                if names.len() == names.capacity() {
                    names.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!("failed to grow attachment name result: {error}"))
                    })?;
                }
                names.push(name);
                Ok(None::<()>)
            })?;
            names.sort_unstable();
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
        let (page_id, drawing_plan, expected_font_number, to_unicode, ops) = py.detach(|| {
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
            let (page_id, drawing_plan) =
                self.prepare_page_drawing(page_number, true, PageResourceKind::Font)?;
            let cid_map = ocr::assign_cids(&words).map_err(PdfError::new_err)?;
            let to_unicode = ocr::build_to_unicode(&cid_map);
            let expected_font_number = self.doc.max_id.checked_add(4).ok_or_else(|| {
                PdfError::new_err("OCR layer objects exceed the PDF object-ID limit")
            })?;
            let ops = ocr::ocr_ops(
                crop,
                rotation,
                &words,
                &cid_map,
                drawing_plan.resource.name(),
                text_rotation,
            );
            Ok((page_id, drawing_plan, expected_font_number, to_unicode, ops))
        })?;

        // From this point onward malformed page/resource state could leave a
        // partial edit, so invalidate before the first PDF object is added.
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let font_id = ocr::add_ocr_font(&mut self.doc, to_unicode);
            debug_assert_eq!(font_id.0, expected_font_number);
            self.commit_drawing_resource(page_id, drawing_plan.resource, font_id)?;
            self.push_page_content(page_id, ops, drawing_plan.content)
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
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::Font)?;
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
            let ops = draw::text_ops(
                crop,
                rotation,
                point,
                &lines,
                drawing_plan.resource.name(),
                fontsize,
                color,
            );
            self.commit_drawing_resource(page_id, drawing_plan.resource, font_id)?;
            self.push_page_content(page_id, ops, drawing_plan.content)
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
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::Font)?;
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
            let ops = draw::textbox_text_ops(
                crop,
                rotation,
                rect,
                &layout,
                align,
                drawing_plan.resource.name(),
                fontsize,
                color,
            )
            .map_err(PdfError::new_err)?;
            self.commit_drawing_resource(page_id, drawing_plan.resource, font_id)?;
            self.push_page_content(page_id, ops, drawing_plan.content)?;
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
        self.draw_embedded_text(
            py,
            page_number,
            point,
            &lines,
            Arc::new(font_data),
            font_index,
            fontsize,
            color,
            overlay,
        )
    }

    /// Draw subset-embedded text using bounded OpenType font input from a path.
    ///
    /// `package_font` names the `(language, kind)` of a face Python selected
    /// from an installed font package; see `bundled_font_cache`. Explicit
    /// font paths pass `None` and are read on every call.
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
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE),
        package_font=None
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
        package_font: Option<(String, String)>,
    ) -> PyResult<()> {
        validate_generated_text_line_count(lines.len(), max_text_size)?;
        validate_generated_text_input(lines.iter().map(String::as_bytes), max_text_size)?;
        let data = load_generation_font(py, path, package_font, max_font_size)?;
        self.draw_embedded_text(
            py,
            page_number,
            point,
            &lines,
            data,
            font_index,
            fontsize,
            color,
            overlay,
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
        self.draw_embedded_textbox(
            py,
            page_number,
            rect,
            text,
            Arc::new(font_data),
            font_index,
            fontsize,
            line_height,
            align,
            color,
            overlay,
            max_text_size,
        )
    }

    /// Lay out subset-embedded text using bounded OpenType font input from a path.
    ///
    /// `package_font` names an installed package face as for
    /// `insert_embedded_text_file`; explicit font paths are read on every call.
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
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE),
        package_font=None
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
        package_font: Option<(String, String)>,
    ) -> PyResult<f64> {
        validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        let data = load_generation_font(py, path, package_font, max_font_size)?;
        self.draw_embedded_textbox(
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
            for stream_id in &streams {
                if let Ok(stream) = self.doc.get_object(*stream_id).and_then(Object::as_stream) {
                    validate_stream_filter_count(stream, "text replacement page content")?;
                }
            }
            // lopdf font lookup misses direct Resources inherited from a page
            // tree, so prepare the same materialized dictionary we will commit.
            let mut page = resolve_inherited_page_dict(&self.doc, page_id)?;
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
            let content_id = self.doc.add_object(stream);
            debug_assert_eq!(content_id, new_content_id);
            self.doc.replace_page(page_id, page);
            Ok(count)
        })?;
        if replacement_count > 0 {
            self.isolated_content_pages.remove(&page_id);
            self.invalidate_hayro_pdf();
        }
        Ok(replacement_count)
    }
}
