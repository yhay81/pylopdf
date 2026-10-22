//! Python bindings for `lopdf::Document`.
//!
//! This is a thin type- and error-conversion layer. Python's
//! `pylopdf.Document` provides the ergonomic API.
//!
//! `_Document` and its single `#[pymethods]` block live here. Each method
//! delegates to the domain submodule that implements it, and the
//! submodules share this module's imports through `use super::*`.

use std::borrow::Cow;
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::io::{self, Read, Write};
use std::panic::{AssertUnwindSafe, catch_unwind};
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

use crate::acroform_import::{AcroFormImport, FormPagePrune};
use crate::bundled_font_cache::{FontBytes, PackageFace, load_package_font};
use crate::canvas::{self, _Canvas, CanvasRecording};
use crate::content_guard::{ContentGuard, PageSelection};
use crate::draw::{self, ObjectSink};
use crate::font::_Font;
use crate::form;
use crate::generate::{self, GenerationFace};
use crate::generated_pdf;
use crate::image_compression;
use crate::object_recovery;
use crate::object_stream_work;
use crate::ocr;
use crate::page_cleanup::PageCleanupPlan;
use crate::pixmap::Pixmap;
use crate::placement_reuse::{self, FormSource, PlacedForm, PlacedImage, PlacementReuse};
use crate::prune::{prune_unreachable_objects, reachable_object_ids};
use crate::read_depth::{self, MAX_READ_DEPTH};
use crate::recovery::{self, XrefRecovery};
use crate::renumber::renumber_objects;
use crate::shape::{_Shape, Segment};
use crate::structure_prune::StructurePagePrune;
use crate::text_replace::{self, TextReplacementError};
use crate::unallocated::normalize_object_numbers;
use indexed::IndexedDocument;

mod annotations;
mod attachments;
mod drawing;
mod errors;
mod extraction;
mod fonts;
mod forms;
mod geometry;
mod images;
/// The page index lives in its own module so that only `DerefMut`,
/// `replace`, and the page-tree-neutral edits below can reach the wrapped
/// document mutably.
mod indexed;
mod input;
mod labels;
mod limits;
mod loading;
/// The typed low-level object model behind `Document.objects`.
mod lowlevel;
mod metadata;
mod navigation;
mod objects;
mod page_tree;
mod rendering;
mod replacement;
mod saving;
mod streams;
mod text_insertion;

pub(crate) use self::annotations::*;
use self::attachments::*;
pub(crate) use self::drawing::*;
pub(crate) use self::errors::*;
use self::extraction::*;
pub(crate) use self::fonts::*;
pub(crate) use self::forms::*;
use self::geometry::*;
pub(crate) use self::images::*;
pub(crate) use self::input::*;
use self::labels::*;
use self::limits::*;
pub(crate) use self::lowlevel::{_decode_text_string, _encode_text_string, StaleObjectError};
use self::lowlevel::{DEFAULT_MAX_STREAM_DATA_SIZE, Root, Step};
use self::metadata::*;
pub(crate) use self::navigation::*;
pub(crate) use self::objects::*;
use self::page_tree::*;
use self::rendering::*;
use self::saving::*;
use self::streams::*;

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
    /// One-based pages, ascending, whose dictionary nests deeper than hayro
    /// reads, so that the current snapshot holds a cut copy of it. Every
    /// operation that interprets one of them is refused.
    pages_too_deep: Vec<u32>,
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
    /// Process-unique identity of the current content, replaced by every
    /// edit, so a Form imported from this document is reused only while the
    /// document is unchanged.
    revision: u64,
    /// Images and pages placed in this document that drawing places again.
    placements: PlacementReuse,
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
            pages_too_deep: Vec::new(),
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
            revision: placement_reuse::next_revision(),
            placements: PlacementReuse::default(),
            render_cache: crate::render_cache::DocumentRenderCache::new(),
            is_repaired: recovery.is_some(),
            pending_warnings: Arc::new(Mutex::new(pending)),
        }
    }

    /// Drop cached views; call at the start of every editing method.
    ///
    /// The edit also forgets the images and pages that drawing placed, since
    /// it may change them. An edit that cannot calls
    /// `invalidate_hayro_pdf_keeping_placements` instead.
    fn invalidate_hayro_pdf(&mut self) {
        self.placements.clear();
        self.invalidate_hayro_pdf_keeping_placements();
    }

    /// Drop cached views for an edit that leaves every placed object as it is.
    ///
    /// Such an edit only adds objects; replaces page dictionaries; rewrites
    /// the page tree, the catalog, the document information dictionary, or
    /// navigation structures (outlines, name trees, and annotations); adds
    /// fields and new default-resource names to the interactive form; or
    /// removes unreachable objects. Placement reuse checks that an object
    /// still exists, which covers the removal. Like every edit, it gives the
    /// document a new revision.
    fn invalidate_hayro_pdf_keeping_placements(&mut self) {
        self.revision = placement_reuse::next_revision();
        self.hayro_pdf = None;
        self.hayro_source = HayroSource::Unavailable;
        self.content_guard.reset();
        self.pages_too_deep.clear();
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
        loading::new(max_text_size, max_interpretation_size, max_text_glyphs)
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
        loading::load(
            py,
            path,
            password,
            max_decompressed_size,
            max_page_content_size,
            max_file_size,
            max_pages,
            max_objects,
            max_total_decompressed_size,
            max_object_depth,
            max_text_size,
            max_interpretation_size,
            max_text_glyphs,
        )
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
        loading::load_bytes(
            py,
            data,
            password,
            max_decompressed_size,
            max_page_content_size,
            max_file_size,
            max_pages,
            max_objects,
            max_total_decompressed_size,
            max_object_depth,
            max_text_size,
            max_interpretation_size,
            max_text_glyphs,
        )
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
        loading::load_metadata(py, path, password, max_file_size)
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
        loading::load_metadata_bytes(py, data, password, max_file_size)
    }

    /// Return whether opening recovered damaged cross-reference data.
    fn is_repaired(&self) -> bool {
        self.is_repaired
    }

    /// Return the document's revision, which every edit replaces.
    fn edit_revision(&self) -> u64 {
        self.revision
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
        fonts::set_fallback_font(self, kind, data, index, max_font_size, language)
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
        fonts::set_fallback_font_files(self, py, sans_path, serif_path, max_font_size, language)
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
        fonts::set_fallback_font_locale_files(self, py, files, max_font_size)
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
        rendering::snapshot_source(self)
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
        saving::save_with_object_streams(self, py, path)
    }

    /// Serialize with PDF 1.5+ object and xref streams.
    #[pyo3(signature = (max_size=None))]
    fn save_bytes_with_object_streams(
        &mut self,
        py: Python<'_>,
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        self.invalidate_hayro_pdf_keeping_placements();
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
        metadata::set_metadata(self, key, value)
    }

    /// Atomically update multiple standard Info entries.
    fn set_metadata_batch(&mut self, entries: Vec<(String, String)>) -> PyResult<()> {
        self.set_metadata_entries(entries)
    }

    /// Remove the trailer's Info dictionary entry.
    fn remove_metadata(&mut self) {
        self.remove_info_dictionary();
    }

    /// Describe the encryption the document was loaded with, or None.
    fn encryption_method(&self) -> Option<String> {
        self.encryption_description()
    }

    /// Return the `/P` value of the encryption the document was loaded with,
    /// or None.
    fn encryption_permissions(&self) -> Option<i32> {
        self.encryption_permission_value()
    }

    /// Check a password against the encryption the document was loaded with,
    /// even after decryption; return (user matches, owner matches).
    fn check_original_password(&self, password: &str) -> PyResult<(bool, bool)> {
        validate_password_input(Some(password), "password")?;
        Ok(self.original_password_matches(password))
    }

    /// Delete one-based pages; duplicates name the same page.
    fn delete_pages(&mut self, page_numbers: Vec<u32>) -> PyResult<()> {
        page_tree::delete_pages(self, page_numbers)
    }

    /// Extract text from a one-based page.
    ///
    /// Collect glyph Unicode/positions through the hayro interpreter and
    /// assemble reading-order text. CJK fallbacks also apply to extraction.
    fn extract_text(&mut self, py: Python<'_>, page_numbers: Vec<u32>) -> PyResult<String> {
        extraction::extract_text(self, py, page_numbers)
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
        extraction::extract_layout(self, py, page_number)
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
        extraction::extract_raw_layout(self, py, page_number)
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
        extraction::find_tables(self, py, page_number, strategy, clip)
    }

    /// Extract images drawn on a one-based page.
    ///
    /// Return `(width, height, bbox, "jpeg"/"png", bytes)` items.
    fn extract_images(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<Vec<crate::extract::ImageTuple>> {
        extraction::extract_images(self, py, page_number)
    }

    /// List the image XObjects that a one-based page's resources reach.
    fn page_image_inventory(&self, page_number: u32) -> PyResult<Vec<ImageInventoryTuple>> {
        extraction::page_image_inventory(self, page_number)
    }

    /// Describe the images drawn on a one-based page.
    fn page_image_info(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<Vec<crate::extract::ImageInfoTuple>> {
        extraction::page_image_info(self, py, page_number)
    }

    /// Decode one image XObject by object number.
    fn extract_image(&mut self, py: Python<'_>, xref: u32) -> PyResult<ExtractedImageTuple> {
        extraction::extract_image(self, py, xref)
    }

    /// Collect the content model of a one-based page: its painted items in
    /// paint order and the fonts and graphics contexts they index.
    #[pyo3(signature = (page_number, annotations, clip, max_elements))]
    fn page_content(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        annotations: bool,
        clip: Option<(f64, f64, f64, f64)>,
        max_elements: Option<usize>,
    ) -> PyResult<Py<PyAny>> {
        extraction::page_content(self, py, page_number, annotations, clip, max_elements)
    }

    /// Extract vector paint operations on a one-based page.
    fn extract_drawings(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<Vec<crate::extract::DrawingTuple>> {
        extraction::extract_drawings(self, py, page_number)
    }

    /// Downsample and JPEG-recompress the selected safe raster images.
    #[allow(clippy::too_many_arguments)] // Mirrors pymupdf's keyword arguments.
    fn rewrite_images(
        &mut self,
        py: Python<'_>,
        dpi_threshold: Option<f64>,
        dpi_target: f64,
        quality: u8,
        lossy: bool,
        lossless: bool,
        color: bool,
        gray: bool,
    ) -> PyResult<image_compression::CompressionResult> {
        let options = image_compression::RewriteOptions {
            dpi_threshold,
            dpi_target,
            quality,
            lossy,
            lossless,
            color,
            gray,
        };
        images::rewrite_images(self, py, options)
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
        extraction::search_page(self, py, page_number, needle, max_hits)
    }

    /// Append every page from another document, whatever its page count.
    fn merge(&mut self, py: Python<'_>, other: &Self) -> PyResult<()> {
        page_tree::merge(self, py, other)
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
        page_tree::merge_pages(self, py, other, page_numbers, position)
    }

    /// Insert a blank page at zero-based `position`; None appends.
    fn new_page(&mut self, position: Option<usize>, width: f32, height: f32) -> PyResult<()> {
        page_tree::new_page(self, position, width, height)
    }

    /// Copy one-based pages, in order, to zero-based `position`; None appends.
    ///
    /// Each copy is an independent page dictionary with inheritance
    /// materialized; Contents and Resources remain shared with the source
    /// page. A page may be listed more than once.
    fn copy_pages(&mut self, page_numbers: Vec<u32>, position: Option<usize>) -> PyResult<()> {
        page_tree::copy_pages(self, page_numbers, position)
    }

    /// Keep specified one-based pages in order, also supporting reordering.
    ///
    /// PDF page-tree Parent must be unique, so duplicate selections require copies.
    fn select(&mut self, page_numbers: Vec<u32>) -> PyResult<()> {
        page_tree::select(self, page_numbers)
    }

    /// Render a one-based page to PNG.
    ///
    /// `background` is fill RGBA in 0–255; None preserves transparency.
    fn render_page_png(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: (f32, f32),
        alpha: bool,
        background: Option<(u8, u8, u8, u8)>,
        max_output_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        rendering::render_page_png(
            self,
            py,
            page_number,
            scale,
            alpha,
            background,
            max_output_size,
        )
    }

    /// Render one-based pages to PNG in input order on a bounded worker pool.
    #[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
    fn render_pages_png(
        &mut self,
        py: Python<'_>,
        page_numbers: Vec<u32>,
        scale: (f32, f32),
        alpha: bool,
        background: Option<(u8, u8, u8, u8)>,
        workers: usize,
        max_output_size: Option<usize>,
    ) -> PyResult<Vec<Vec<u8>>> {
        rendering::render_pages_png(
            self,
            py,
            page_numbers,
            scale,
            alpha,
            background,
            workers,
            max_output_size,
        )
    }

    /// Render a one-based page and return an RGB, or straight-alpha RGBA, Pixmap.
    fn render_page_pixmap(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: (f32, f32),
        alpha: bool,
        background: Option<(u8, u8, u8, u8)>,
        clip: Option<(f64, f64, f64, f64)>,
    ) -> PyResult<crate::pixmap::Pixmap> {
        rendering::render_page_pixmap(self, py, page_number, scale, alpha, background, clip)
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
        rendering::render_page_svg(self, py, page_number, max_output_size)
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
        navigation::set_toc(self, entries)
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
        saving::save_encrypted(
            self,
            py,
            path,
            user_password,
            owner_password,
            permissions,
            file_encryption_key,
        )
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
        saving::save_bytes_encrypted(
            self,
            py,
            user_password,
            owner_password,
            permissions,
            file_encryption_key,
            max_size,
        )
    }

    /// Return inherited, normalized 0..360 rotation for a one-based page.
    fn get_page_rotation(&self, page_number: u32) -> PyResult<i64> {
        self.resolve_page_rotation(self.page_id(page_number)?)
    }

    /// Set rotation for a one-based page; Python validates the value.
    fn set_page_rotation(&mut self, page_number: u32, rotation: i64) -> PyResult<()> {
        self.invalidate_hayro_pdf_keeping_placements();
        self.doc
            .set_page_entry(page_number, "Rotate", Object::Integer(rotation))
    }

    /// Return the rotation-resolved display width and height of a one-based
    /// page, from the normalized box that drawing maps coordinates with.
    fn page_display_size(&self, page_number: u32) -> PyResult<(f64, f64)> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        Ok(draw::display_size(crop, rotation))
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
        geometry::set_page_box(self, page_number, key, x0, y0, x1, y1)
    }

    /// Set a one-based page's MediaBox and remove its other page boxes.
    fn set_mediabox(
        &mut self,
        page_number: u32,
        x0: f64,
        y0: f64,
        x1: f64,
        y1: f64,
    ) -> PyResult<()> {
        geometry::set_mediabox(self, page_number, x0, y0, x1, y1)
    }

    /// Compress streams.
    ///
    /// Only a stream that Flate makes smaller changes, and the caches are
    /// dropped only when one does, so saving a compressed document again
    /// keeps the rendering snapshot and the remembered placements.
    fn compress(&mut self, py: Python<'_>) {
        saving::compress(self, py);
    }

    /// Decompress streams.
    fn decompress(&mut self, py: Python<'_>) {
        self.invalidate_hayro_pdf();
        py.detach(|| self.doc.decompress());
    }

    /// Merge identical objects and streams (`dedupe`), remove the objects
    /// that the trailer cannot reach (`prune`), and number the remaining
    /// objects consecutively (`renumber`), in that order.
    ///
    /// Merging leaves the duplicates unreachable, so it needs `prune`.
    /// Merging and renumbering change the IDs of placed objects, so they
    /// forget placements; removing unreachable objects keeps them.
    fn collect_garbage(
        &mut self,
        py: Python<'_>,
        dedupe: bool,
        prune: bool,
        renumber: bool,
    ) -> PyResult<()> {
        saving::collect_garbage(self, py, dedupe, prune, renumber)
    }

    /// Draw JPEG/PNG bytes into display `rect` on a one-based page.
    ///
    /// `rect` uses top-left-origin page display space, including rotation.
    /// Drawing only adds a content stream and never rewrites existing content.
    /// Bytes identical to an earlier placement place its XObject again.
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
    ) -> PyResult<u32> {
        images::insert_image(
            self,
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
    ) -> PyResult<u32> {
        images::insert_image_file(
            self,
            py,
            page_number,
            rect,
            path,
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
    /// Samples identical to an earlier placement place its XObject again.
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
    ) -> PyResult<u32> {
        images::insert_pixmap(
            self,
            py,
            page_number,
            rect,
            pixmap,
            image_rotation,
            keep_proportion,
            overlay,
        )
    }

    /// Import a one-based page from `other` as a Form XObject into display `rect`.
    ///
    /// Renumber source objects as in merge and wrap page content in a Form
    /// XObject to preserve vectors. The Form of an earlier placement of the
    /// same page is placed again while `other` is unchanged, without cloning it.
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
    ) -> PyResult<u32> {
        drawing::show_pdf_page(
            self,
            py,
            page_number,
            rect,
            other,
            src_page_number,
            keep_proportion,
            overlay,
        )
    }

    /// Import a page from this document's pre-edit snapshot as a Form XObject.
    ///
    /// Consecutive placements of one source page on other pages place one Form.
    fn show_pdf_page_self(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        src_page_number: u32,
        keep_proportion: bool,
        overlay: bool,
    ) -> PyResult<u32> {
        drawing::show_pdf_page_self(
            self,
            py,
            page_number,
            rect,
            src_page_number,
            keep_proportion,
            overlay,
        )
    }

    /// Read annotations from a one-based page.
    ///
    /// Each item is `(Subtype, display Rect, Contents, URI)`. Rect uses rotated
    /// top-left-origin display space; Contents/URI are optional.
    fn read_annotations(&self, py: Python<'_>, page_number: u32) -> PyResult<Vec<AnnotationTuple>> {
        annotations::read_annotations(self, py, page_number)
    }

    /// Read link annotations from a one-based page and resolve destinations.
    ///
    /// Support `/A` actions (URI, GoTo, GoToR, Launch, Named) and direct `/Dest`.
    /// Resolve GoTo names from `/Names` trees and legacy `/Dests`. The page
    /// index and name-tree index persist until the next edit.
    fn read_links(&mut self, py: Python<'_>, page_number: u32) -> PyResult<Vec<IdentifiedLink>> {
        navigation::read_links(self, py, page_number)
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
    ) -> PyResult<u32> {
        annotations::add_highlight_annotation(self, page_number, rects, color, opacity, content)
    }

    /// Add a URI link annotation to display `rect` on a one-based page.
    fn add_link_annotation(
        &mut self,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        uri: String,
    ) -> PyResult<()> {
        annotations::add_link_annotation(self, page_number, rect, uri)
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
        metadata::pdfa_claim(self, py, max_size)
    }

    /// Return the decoded catalog XMP metadata stream, or `None`.
    fn xml_metadata(&self, py: Python<'_>, max_size: Option<usize>) -> PyResult<Option<Vec<u8>>> {
        metadata::xml_metadata(self, py, max_size)
    }

    /// Store unfiltered XMP metadata in the catalog's `/Metadata` stream.
    fn set_xml_metadata(&mut self, data: Vec<u8>, max_size: Option<usize>) -> PyResult<()> {
        metadata::set_xml_metadata(self, data, max_size)
    }

    /// Remove the catalog's `/Metadata` entry.
    fn remove_xml_metadata(&mut self) -> PyResult<()> {
        metadata::remove_xml_metadata(self)
    }

    /// Set the catalog's `/PageMode` or `/PageLayout` to a valid name.
    fn set_catalog_name(&mut self, key: &str, value: &str) -> PyResult<()> {
        metadata::set_catalog_name(self, key, value)
    }

    /// Write or, for `None`, remove the catalog's `/Lang` language tag.
    #[pyo3(signature = (tag))]
    fn set_language_tag(&mut self, tag: Option<&str>) -> PyResult<()> {
        metadata::set_language_tag(self, tag)
    }

    /// Replace the catalog's `/MarkInfo` dictionary.
    fn set_mark_info(
        &mut self,
        marked: bool,
        user_properties: bool,
        suspects: bool,
    ) -> PyResult<()> {
        metadata::set_mark_info(self, marked, user_properties, suspects)
    }

    /// Return whether any page lists a link (`links`) or another
    /// non-widget annotation.
    fn has_annotation_kind(&self, py: Python<'_>, links: bool) -> PyResult<bool> {
        metadata::has_annotation_kind(self, py, links)
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
        labels::set_page_labels(self, labels)
    }

    /// Return AcroForm fields as `(full name, kind, value)`.
    ///
    /// Kind is text/checkbox/radio/button/combobox/listbox/signature. Value is
    /// stringified `/V`, including state names such as Yes/Off, or None.
    fn get_form_fields(&self, py: Python<'_>) -> PyResult<Vec<(String, String, Option<String>)>> {
        forms::get_form_fields(self, py)
    }

    /// Return checkbox/radio state names from widget `AP /N` keys.
    fn form_button_states(&self, py: Python<'_>, name: &str) -> PyResult<Vec<String>> {
        forms::form_button_states(self, py, name)
    }

    /// Set a form-field value and generate native widget appearances.
    ///
    /// `weight` draws a variable face at that weight-axis value; see
    /// `generation_face`. Widgets open the font only to draw text.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        name,
        value,
        font_data=None,
        font_index=0,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        weight=None
    ))]
    fn set_form_field(
        &mut self,
        py: Python<'_>,
        name: &str,
        value: &str,
        font_data: Option<Vec<u8>>,
        font_index: u32,
        max_font_size: Option<usize>,
        weight: Option<u16>,
    ) -> PyResult<()> {
        forms::set_form_field(
            self,
            py,
            name,
            value,
            font_data,
            font_index,
            max_font_size,
            weight,
        )
    }

    /// Set a form-field value using bounded OpenType font input from a path.
    ///
    /// `package_font` names an installed package face as for
    /// `insert_embedded_text_file`; explicit font paths are read on every call.
    /// `weight` is as for `set_form_field`.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        name,
        value,
        path,
        font_index=0,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        package_font=None,
        weight=None
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
        weight: Option<u16>,
    ) -> PyResult<()> {
        forms::set_form_field_file(
            self,
            py,
            name,
            value,
            path,
            font_index,
            max_font_size,
            package_font,
            weight,
        )
    }

    /// Set a form-field value, drawing text appearances with a loaded `Font`.
    ///
    /// The face's bytes were bounded when it was loaded, so only the field
    /// input budgets apply here. Every widget that draws text draws the
    /// face's selected instance, and filling follows `set_form_field`. Form
    /// appearances embed an OpenType face; Python refuses a Standard 14 `Font`
    /// before this call.
    fn set_form_field_font(
        &mut self,
        py: Python<'_>,
        name: &str,
        value: &str,
        font: &Bound<'_, _Font>,
    ) -> PyResult<()> {
        forms::set_form_field_font(self, py, name, value, font)
    }

    /// Return sorted attachment names independent of name-tree order.
    fn embfile_names(&self, py: Python<'_>) -> PyResult<Vec<String>> {
        attachments::embfile_names(self, py)
    }

    /// Return attachment contents, bounding every decoding layer when requested.
    fn embfile_get(
        &self,
        py: Python<'_>,
        name: &str,
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        attachments::embfile_get(self, py, name, max_size)
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
        attachments::embfile_add(self, py, name, data, filename, desc, max_size)
    }

    /// Delete an attachment, raising an error when absent.
    fn embfile_del(&mut self, py: Python<'_>, name: &str) -> PyResult<()> {
        attachments::embfile_del(self, py, name)
    }

    /// Return one attachment's metadata, or `None` when it is absent.
    fn embfile_info(
        &self,
        py: Python<'_>,
        name: &str,
    ) -> PyResult<Option<attachments::EmbeddedFileInfo>> {
        attachments::embfile_info(self, py, name)
    }

    /// Replace one attachment's data, raising an error when absent.
    #[pyo3(signature = (name, data, max_size=Some(DEFAULT_MAX_EMBEDDED_FILE_SIZE)))]
    fn embfile_upd(
        &mut self,
        py: Python<'_>,
        name: &str,
        data: Vec<u8>,
        max_size: Option<usize>,
    ) -> PyResult<()> {
        attachments::embfile_upd(self, py, name, data, max_size)
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
        text_insertion::insert_ocr_layer(self, py, page_number, words, text_rotation)
    }

    /// Draw text from display-coordinate baseline `point` on a one-based page
    /// and return the number of lines drawn.
    ///
    /// `lines` contains WinAnsi bytes, one per line; Python validates and
    /// converts cp1252. `base_font` is a Standard 14 name and is not embedded.
    /// Lines whose baseline would fall below the page are left out, as
    /// `LineMetrics::lines_on_page` decides.
    // This boundary mirrors the Python signature, so the argument count is intentional.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        point,
        lines,
        base_font,
        winansi,
        fontsize,
        lineheight,
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
        lineheight: Option<f64>,
        color: (f64, f64, f64),
        overlay: bool,
        max_text_size: Option<usize>,
    ) -> PyResult<usize> {
        text_insertion::insert_page_text(
            self,
            py,
            page_number,
            point,
            lines,
            base_font,
            winansi,
            fontsize,
            lineheight,
            color,
            overlay,
            max_text_size,
        )
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
        lineheight,
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
        lineheight: Option<f64>,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        text_insertion::insert_page_textbox(
            self,
            py,
            page_number,
            rect,
            text,
            base_font,
            winansi,
            fontsize,
            lineheight,
            align,
            color,
            overlay,
            max_text_size,
        )
    }

    /// Draw subset-embedded OpenType text through a krilla-generated Form and
    /// return the number of lines drawn.
    ///
    /// The temporary source page uses the target page's rotation-resolved
    /// display size and coordinate system, then follows `show_pdf_page`'s
    /// existing object-import and placement path. Lines whose baseline would
    /// fall below the page are left out; see `generate::embedded_text_page`.
    /// `weight` draws a variable face at that weight-axis value; see
    /// `generation_face`.
    // This boundary mirrors the Python signature, so the argument count is intentional.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        point,
        lines,
        font_data,
        font_index,
        fontsize,
        lineheight,
        color,
        overlay,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE),
        weight=None
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
        lineheight: Option<f64>,
        color: (f64, f64, f64),
        overlay: bool,
        max_font_size: Option<usize>,
        max_text_size: Option<usize>,
        weight: Option<u16>,
    ) -> PyResult<usize> {
        text_insertion::insert_embedded_text(
            self,
            py,
            page_number,
            point,
            lines,
            font_data,
            font_index,
            fontsize,
            lineheight,
            color,
            overlay,
            max_font_size,
            max_text_size,
            weight,
        )
    }

    /// Draw subset-embedded text using bounded OpenType font input from a path.
    ///
    /// `package_font` names the `(language, kind)` of a face Python selected
    /// from an installed font package; see `bundled_font_cache`. Explicit
    /// font paths pass `None` and are read on every call. `weight` is as for
    /// `insert_embedded_text`.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        point,
        lines,
        path,
        font_index,
        fontsize,
        lineheight,
        color,
        overlay,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE),
        package_font=None,
        weight=None
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
        lineheight: Option<f64>,
        color: (f64, f64, f64),
        overlay: bool,
        max_font_size: Option<usize>,
        max_text_size: Option<usize>,
        package_font: Option<(String, String)>,
        weight: Option<u16>,
    ) -> PyResult<usize> {
        text_insertion::insert_embedded_text_file(
            self,
            py,
            page_number,
            point,
            lines,
            path,
            font_index,
            fontsize,
            lineheight,
            color,
            overlay,
            max_font_size,
            max_text_size,
            package_font,
            weight,
        )
    }

    /// Draw a recorded shape over a one-based page as one Form XObject.
    ///
    /// Everything that can fail on the input, generation and the import
    /// plan included, is checked before the document changes. An empty shape
    /// leaves the document unchanged.
    fn commit_shape(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        shape: &Bound<'_, _Shape>,
        overlay: bool,
    ) -> PyResult<()> {
        drawing::commit_shape(self, py, page_number, shape, overlay)
    }

    /// Draw a recorded canvas over a one-based page, or under it with
    /// `overlay=False`, as one Form XObject.
    ///
    /// Generation, imports, image parsing, and the composed content are
    /// prepared before the document changes. An empty canvas leaves the
    /// document unchanged, and the canvas is empty afterwards either way.
    fn commit_canvas(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        canvas: &Bound<'_, _Canvas>,
        overlay: bool,
    ) -> PyResult<()> {
        drawing::commit_canvas(self, py, page_number, canvas, overlay)
    }

    /// Draw subset-embedded text with a loaded `Font`.
    ///
    /// The face's bytes were bounded when it was loaded, so only the text
    /// budgets apply here; drawing then follows `insert_embedded_text`.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        point,
        lines,
        font,
        fontsize,
        lineheight,
        color,
        overlay,
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_font_text(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        point: (f64, f64),
        lines: Vec<String>,
        font: &Bound<'_, _Font>,
        fontsize: f64,
        lineheight: Option<f64>,
        color: (f64, f64, f64),
        overlay: bool,
        max_text_size: Option<usize>,
    ) -> PyResult<usize> {
        text_insertion::insert_font_text(
            self,
            py,
            page_number,
            point,
            lines,
            font,
            fontsize,
            lineheight,
            color,
            overlay,
            max_text_size,
        )
    }

    /// Lay out and draw subset-embedded text with a loaded `Font`.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        rect,
        text,
        font,
        fontsize,
        lineheight,
        align,
        color,
        overlay,
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_font_textbox(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        text: &str,
        font: &Bound<'_, _Font>,
        fontsize: f64,
        lineheight: Option<f64>,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        text_insertion::insert_font_textbox(
            self,
            py,
            page_number,
            rect,
            text,
            font,
            fontsize,
            lineheight,
            align,
            color,
            overlay,
            max_text_size,
        )
    }

    /// Lay out and draw subset-embedded OpenType text inside a display rectangle.
    ///
    /// `weight` is as for `insert_embedded_text`.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        rect,
        text,
        font_data,
        font_index,
        fontsize,
        lineheight,
        align,
        color,
        overlay,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE),
        weight=None
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
        lineheight: Option<f64>,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_font_size: Option<usize>,
        max_text_size: Option<usize>,
        weight: Option<u16>,
    ) -> PyResult<f64> {
        text_insertion::insert_embedded_textbox(
            self,
            py,
            page_number,
            rect,
            text,
            font_data,
            font_index,
            fontsize,
            lineheight,
            align,
            color,
            overlay,
            max_font_size,
            max_text_size,
            weight,
        )
    }

    /// Lay out subset-embedded text using bounded OpenType font input from a path.
    ///
    /// `package_font` names an installed package face as for
    /// `insert_embedded_text_file`; explicit font paths are read on every call.
    /// `weight` is as for `insert_embedded_text`.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        page_number,
        rect,
        text,
        path,
        font_index,
        fontsize,
        lineheight,
        align,
        color,
        overlay,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE),
        package_font=None,
        weight=None
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
        lineheight: Option<f64>,
        align: u8,
        color: (f64, f64, f64),
        overlay: bool,
        max_font_size: Option<usize>,
        max_text_size: Option<usize>,
        package_font: Option<(String, String)>,
        weight: Option<u16>,
    ) -> PyResult<f64> {
        text_insertion::insert_embedded_textbox_file(
            self,
            py,
            page_number,
            rect,
            text,
            path,
            font_index,
            fontsize,
            lineheight,
            align,
            color,
            overlay,
            max_font_size,
            max_text_size,
            package_font,
            weight,
        )
    }

    /// Interpret a one-based page's content with the redaction engine and
    /// return each glyph's display-space origin, code, and approximation
    /// flags. A private hook for the geometry agreement tests.
    fn content_glyphs(
        &self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<Vec<replacement::ContentGlyphTuple>> {
        replacement::content_glyphs(self, py, page_number)
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
        replacement::replace_text_on_page(
            self,
            py,
            page_number,
            search,
            replacement,
            default_char,
            max_size,
        )
    }

    /// Return the distinct object numbers in ascending order.
    fn object_numbers(&self) -> Vec<u32> {
        lowlevel::object_numbers(self)
    }

    /// Return the generation and tagged value of an object number, or
    /// `None` when no object has it. The highest generation wins.
    fn object_lookup(&self, py: Python<'_>, number: u32) -> PyResult<Option<(u16, Py<PyAny>)>> {
        lowlevel::object_lookup(self, py, number)
    }

    /// Return the object ID of a one-based page.
    fn page_object_id(&self, page_number: u32) -> PyResult<(u32, u16)> {
        self.page_id(page_number)
    }

    /// Read the tagged value at `step` of the container that `root` and
    /// `path` address, resolving references.
    fn object_get(
        &self,
        py: Python<'_>,
        root: Root,
        path: Vec<Step>,
        step: Step,
    ) -> PyResult<Py<PyAny>> {
        lowlevel::object_get(self, py, root, path, step)
    }

    /// Return the keys of the dictionary that `root` and `path` address.
    fn object_keys(&self, root: Root, path: Vec<Step>) -> PyResult<Vec<Vec<u8>>> {
        lowlevel::object_keys(self, root, path)
    }

    /// Return the entry count of the container that `root` and `path` address.
    fn object_len(&self, root: Root, path: Vec<Step>) -> PyResult<usize> {
        lowlevel::object_len(self, root, path)
    }

    /// Set one entry of a container; return whether the page order changed.
    fn object_set(
        &mut self,
        root: Root,
        path: Vec<Step>,
        step: Step,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        lowlevel::object_set(self, root, path, step, value)
    }

    /// Delete one entry of a container; return whether the page order changed.
    fn object_delete(&mut self, root: Root, path: Vec<Step>, step: Step) -> PyResult<bool> {
        lowlevel::object_delete(self, root, path, step)
    }

    /// Insert into an array; return whether the page order changed.
    fn object_insert(
        &mut self,
        root: Root,
        path: Vec<Step>,
        index: usize,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        lowlevel::object_insert(self, root, path, index, value)
    }

    /// Add an indirect object; return its ID and whether the page order changed.
    fn object_add(&mut self, value: &Bound<'_, PyAny>) -> PyResult<(u32, u16, bool)> {
        lowlevel::object_add(self, value)
    }

    /// Replace the object stored under a number; return whether the page
    /// order changed.
    fn object_replace(&mut self, number: u32, value: &Bound<'_, PyAny>) -> PyResult<bool> {
        lowlevel::object_replace(self, number, value)
    }

    /// Add a stream object from decoded data, Flate-compressed when
    /// `compress` is set; return its ID and whether the page order changed.
    fn object_add_stream(
        &mut self,
        py: Python<'_>,
        dictionary: &Bound<'_, PyAny>,
        data: Vec<u8>,
        compress: bool,
    ) -> PyResult<(u32, u16, bool)> {
        lowlevel::object_add_stream(self, py, dictionary, data, compress)
    }

    /// Read a stream's data, decoded through every filter unless `decode` is
    /// false, refusing more than `max_size` bytes.
    #[pyo3(signature = (stream_id, path, decode=true, max_size=Some(DEFAULT_MAX_STREAM_DATA_SIZE)))]
    fn stream_read(
        &self,
        py: Python<'_>,
        stream_id: (u32, u16),
        path: Vec<Step>,
        decode: bool,
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        lowlevel::stream_read(self, py, stream_id, path, decode, max_size)
    }

    /// Replace a stream's data with decoded `data`, Flate-compressed when
    /// `compress` is set; return whether the page order changed.
    fn stream_write(
        &mut self,
        py: Python<'_>,
        stream_id: (u32, u16),
        path: Vec<Step>,
        data: Vec<u8>,
        compress: bool,
    ) -> PyResult<bool> {
        lowlevel::stream_write(self, py, stream_id, path, data, compress)
    }
}
