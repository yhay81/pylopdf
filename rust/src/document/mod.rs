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
use lopdf::encryption::crypt_filters::{Aes128CryptFilter, Aes256CryptFilter, CryptFilter};
use lopdf::encryption::{EncryptionState, EncryptionVersion, Permissions};
use lopdf::{
    DecompressError, Dictionary, Document, LoadOptions, Object, ObjectId, PdfMetadata, SaveOptions,
    Stream, StringFormat, decode_text_string, dictionary, text_string,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
#[cfg(not(target_os = "emscripten"))]
use rayon::prelude::*;

use crate::acroform_import::{AcroFormImport, FormPagePrune};
use crate::annotation_appearance;
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
use crate::pixmap::{EncodeFailure, Encoding, Layout};
use crate::placement_reuse::{self, FormSource, PlacedForm, PlacedImage, PlacementReuse};
use crate::prune::{prune_unreachable_objects, reachable_object_ids};
use crate::read_depth::{self, MAX_READ_DEPTH};
use crate::recovery::{self, XrefRecovery};
use crate::renumber::renumber_objects;
use crate::structure_prune::StructurePagePrune;
use crate::text_replace::{self, TextReplacementError};
use crate::unallocated::normalize_object_numbers;
use indexed::IndexedDocument;

mod annotations;
mod attachments;
mod drawing;
mod errors;
mod extraction;
mod flattening;
mod fonts;
mod forms;
mod geometry;
mod images;
/// Incremental saves that append one update to the original bytes.
mod incremental;
/// The page index lives in its own module so that only `DerefMut`,
/// `replace`, and the page-tree-neutral edits below can reach the wrapped
/// document mutably.
mod indexed;
mod input;
mod labels;
mod layers;
mod limits;
mod loading;
/// The typed low-level object model behind `Document.objects`.
mod lowlevel;
mod metadata;
mod navigation;
mod objects;
mod outline;
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
use self::incremental::IncrementalBase;
pub(crate) use self::input::*;
use self::labels::*;
use self::layers::*;
use self::limits::*;
pub(crate) use self::lowlevel::{_decode_text_string, _encode_text_string, StaleObjectError};
use self::lowlevel::{DEFAULT_MAX_STREAM_DATA_SIZE, Root, Step};
use self::metadata::*;
pub(crate) use self::navigation::*;
pub(crate) use self::objects::*;
use self::outline::*;
use self::page_tree::*;
use self::rendering::*;
use self::saving::EncryptionRequest;
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
    /// The original bytes that an incremental save extends, kept for the
    /// document's lifetime when they describe the loaded graph exactly.
    incremental_base: Option<IncrementalBase>,
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
        max_text_size: Option<usize>,
        max_interpretation_size: Option<usize>,
        max_text_glyphs: Option<usize>,
    ) -> Self {
        Self::from_loaded_doc(
            doc,
            HayroSource::Unavailable,
            None,
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
        incremental_base: Option<IncrementalBase>,
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
            incremental_base,
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

    /// Put a loaded OpenType face in one CJK fallback slot, or empty the slot.
    #[pyo3(signature = (language, kind, font=None))]
    fn set_fallback_face(
        &mut self,
        language: &str,
        kind: &str,
        font: Option<PyRef<'_, _Font>>,
    ) -> PyResult<()> {
        fonts::set_fallback_face(self, language, kind, font.as_deref())
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

    /// Whether an incremental save can extend the original bytes.
    fn can_save_incrementally(&self) -> bool {
        self.incremental_base.is_some()
    }

    /// Write the original bytes and one update section to `path`.
    fn save_incremental(&self, py: Python<'_>, path: &str, compress: bool) -> PyResult<()> {
        incremental::save_incremental(self, py, path, compress)
    }

    /// Return the original bytes and one update section.
    #[pyo3(signature = (compress, max_size=None))]
    fn save_bytes_incremental(
        &self,
        py: Python<'_>,
        compress: bool,
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        incremental::save_bytes_incremental(self, py, compress, max_size)
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

    /// Return the layout of a one-based page with every span's glyphs.
    ///
    /// Return the blocks of [`Self::extract_layout`], except that a span is
    /// `(bbox, (text, chars), size, origin, font, flags)` with
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

    /// List the xrefs of the image XObjects that a one-based page's
    /// resources reach, in resource order.
    fn page_image_inventory(&self, page_number: u32) -> PyResult<Vec<u32>> {
        extraction::page_image_inventory(self, page_number)
    }

    /// List the xrefs of every image XObject in ascending order.
    fn image_xrefs(&self) -> PyResult<Vec<u32>> {
        extraction::image_xrefs(self)
    }

    /// Read an image XObject's dictionary facts, or `None` when `xref` names
    /// no image.
    fn image_facts(&self, xref: u32) -> Option<ImageFactsTuple> {
        extraction::image_facts(self, xref)
    }

    /// Decode one image XObject, without its masks, by object number.
    fn extract_image(&mut self, py: Python<'_>, xref: u32) -> PyResult<ExtractedImageTuple> {
        extraction::extract_image(self, py, xref)
    }

    /// Render one image XObject at its pixel size, with its masks as alpha
    /// when `alpha` is set.
    fn image_pixmap(
        &mut self,
        py: Python<'_>,
        xref: u32,
        alpha: bool,
    ) -> PyResult<crate::pixmap::Pixmap> {
        extraction::image_pixmap(self, py, xref, alpha)
    }

    /// Collect the content model of a one-based page: its painted items of
    /// the kinds selected by `(text_runs, paths, images)`, in paint order,
    /// and the fonts and graphics contexts they index.
    #[pyo3(signature = (page_number, kinds, annotations, clip, max_elements))]
    fn page_content(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        kinds: (bool, bool, bool),
        annotations: bool,
        clip: Option<(f64, f64, f64, f64)>,
        max_elements: Option<usize>,
    ) -> PyResult<Py<PyAny>> {
        extraction::page_content(
            self,
            py,
            page_number,
            kinds,
            annotations,
            clip,
            max_elements,
        )
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
        matrix: (f64, f64, f64, f64),
        alpha: bool,
        background: Option<(u8, u8, u8, u8)>,
        max_output_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        rendering::render_page_png(
            self,
            py,
            page_number,
            matrix,
            alpha,
            background,
            max_output_size,
        )
    }

    /// Render one-based pages to encoded images in input order on a bounded
    /// worker pool.
    ///
    /// `gray` and `alpha` select the samples, `format` is `"png"` or
    /// `"jpeg"` with `quality`, and `annotations` false leaves every
    /// annotation's appearance out.
    #[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
    fn render_pages_encoded(
        &mut self,
        py: Python<'_>,
        page_numbers: Vec<u32>,
        matrix: (f64, f64, f64, f64),
        gray: bool,
        alpha: bool,
        background: Option<(u8, u8, u8, u8)>,
        annotations: bool,
        format: &str,
        quality: u8,
        workers: usize,
        max_output_size: Option<usize>,
    ) -> PyResult<Vec<Vec<u8>>> {
        let layout = Layout { gray, alpha };
        let encoding = Encoding::parse(format, quality, layout)?;
        rendering::render_pages_encoded(
            self,
            py,
            page_numbers,
            matrix,
            layout,
            background,
            annotations,
            encoding,
            workers,
            max_output_size,
        )
    }

    /// Render a one-based page and return a gray or RGB Pixmap, with straight
    /// alpha when `alpha` is true.
    ///
    /// `annotations` false leaves every annotation's appearance out.
    #[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
    fn render_page_pixmap(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        matrix: (f64, f64, f64, f64),
        gray: bool,
        alpha: bool,
        background: Option<(u8, u8, u8, u8)>,
        clip: Option<(f64, f64, f64, f64)>,
        annotations: bool,
    ) -> PyResult<crate::pixmap::Pixmap> {
        rendering::render_page_pixmap(
            self,
            py,
            page_number,
            matrix,
            Layout { gray, alpha },
            background,
            clip,
            annotations,
        )
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
        annotations: bool,
        max_output_size: Option<usize>,
    ) -> PyResult<String> {
        rendering::render_page_svg(self, py, page_number, annotations, max_output_size)
    }

    /// Return every outline item in document order: object number and
    /// generation (0 and 0 for a direct dictionary), level, title, target,
    /// color, `/F` flags, and `/Count`.
    fn outline_entries(&mut self, py: Python<'_>) -> PyResult<Vec<OutlineEntryTuple>> {
        outline::outline_entries(self, py)
    }

    /// Return the object IDs of the children of an outline item, or of the
    /// outline root for `None`.
    #[pyo3(signature = (parent=None))]
    fn outline_children(&self, parent: Option<(u32, u16)>) -> PyResult<Vec<(u32, u16)>> {
        outline::outline_children(self, parent)
    }

    /// Return one outline item: level, title, target, color, `/F` flags,
    /// `/Count`, and whether it has children.
    fn outline_item(&mut self, py: Python<'_>, item: (u32, u16)) -> PyResult<OutlineItemTuple> {
        outline::outline_item(self, py, item)
    }

    /// Replace the outline with `(level, title, target, color, flags, open)`
    /// items; an empty list removes it. Every item is prepared before the
    /// document changes.
    fn write_outline(&mut self, entries: Vec<OutlineSpec>) -> PyResult<()> {
        outline::write_outline(self, entries)
    }

    /// Replace an outline item's title.
    fn set_outline_title(&mut self, item: (u32, u16), title: &str) -> PyResult<()> {
        outline::set_outline_title(self, item, title)
    }

    /// Replace an outline item's color and `/F` flags.
    #[pyo3(signature = (item, color, flags))]
    fn set_outline_style(
        &mut self,
        item: (u32, u16),
        color: Option<(f64, f64, f64)>,
        flags: u32,
    ) -> PyResult<()> {
        outline::set_outline_style(self, item, color, flags)
    }

    /// Replace what an outline item leads to.
    fn set_outline_target(&mut self, item: (u32, u16), target: TargetTuple) -> PyResult<()> {
        outline::set_outline_target(self, item, target)
    }

    /// Show or hide an outline item's children and recount the outline.
    fn set_outline_open(&mut self, item: (u32, u16), is_open: bool) -> PyResult<()> {
        outline::set_outline_open(self, item, is_open)
    }

    /// Save an encrypted clone to a file while this document stays plaintext.
    ///
    /// `method` is `aes-256`, `aes-128`, or `keep`, which keeps the
    /// encryption the document was opened with.
    #[allow(clippy::too_many_arguments)] // One argument per Encryption field.
    fn save_encrypted(
        &self,
        py: Python<'_>,
        path: &str,
        method: &str,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
        document_id: &[u8],
    ) -> PyResult<()> {
        let request = EncryptionRequest {
            method,
            user_password,
            owner_password,
            permissions,
            file_encryption_key,
            document_id,
        };
        saving::save_encrypted(self, py, path, &request)
    }

    /// Serialize an encrypted clone while this document stays plaintext.
    #[pyo3(signature = (
        method,
        user_password,
        owner_password,
        permissions,
        file_encryption_key,
        document_id,
        max_size=None
    ))]
    #[allow(clippy::too_many_arguments)] // One argument per Encryption field.
    fn save_bytes_encrypted(
        &self,
        py: Python<'_>,
        method: &str,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
        document_id: &[u8],
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        let request = EncryptionRequest {
            method,
            user_password,
            owner_password,
            permissions,
            file_encryption_key,
            document_id,
        };
        saving::save_bytes_encrypted(self, py, &request, max_size)
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

    /// Return a one-based page's MediaBox and CropBox as it inherits them,
    /// and its own BleedBox, TrimBox, and ArtBox; an absent box is None.
    fn page_boxes(&self, page_number: u32) -> PyResult<geometry::PageBoxes> {
        geometry::page_boxes(self, page_number)
    }

    /// Remove a CropBox, BleedBox, TrimBox, or ArtBox from a one-based page.
    fn remove_page_box(&mut self, page_number: u32, key: &str) -> PyResult<()> {
        geometry::remove_page_box(self, page_number, key)
    }

    /// Return the matrix that maps a one-based page's PDF user space to its
    /// display space.
    fn page_display_matrix(&self, page_number: u32) -> PyResult<(f64, f64, f64, f64, f64, f64)> {
        geometry::page_display_matrix(self, page_number)
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

    /// Read the annotations of a one-based page other than links and
    /// widgets, with their rectangles in display space.
    fn read_annotations(&self, py: Python<'_>, page_number: u32) -> PyResult<Vec<AnnotationTuple>> {
        annotations::read_annotations(self, py, page_number)
    }

    /// Read one annotation of a one-based page, found as `read_link` finds a link.
    fn read_annotation(
        &self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
    ) -> PyResult<AnnotationTuple> {
        annotations::read_annotation(self, py, page_number, index, xref)
    }

    /// Change the properties of one annotation of a one-based page, drawing
    /// its appearance again, and return its object number.
    fn edit_annotation(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
        edit: annotations::AnnotationEdit,
    ) -> PyResult<u32> {
        annotations::edit_annotation(self, py, page_number, index, xref, edit)
    }

    /// Move, add, or remove the popup of one annotation of a one-based page
    /// and return the annotation's object number.
    fn set_annotation_popup(
        &mut self,
        page_number: u32,
        index: u32,
        xref: u32,
        rect: Option<(f64, f64, f64, f64)>,
    ) -> PyResult<u32> {
        annotations::set_annotation_popup(self, page_number, index, xref, rect)
    }

    /// Open or close the popup of one annotation of a one-based page.
    fn set_annotation_open(
        &mut self,
        page_number: u32,
        index: u32,
        xref: u32,
        open: bool,
    ) -> PyResult<u32> {
        annotations::set_annotation_open(self, page_number, index, xref, open)
    }

    /// Make one annotation of a one-based page a reply to another, or none.
    fn set_annotation_reply(
        &mut self,
        page_number: u32,
        index: u32,
        xref: u32,
        target: Option<u32>,
    ) -> PyResult<u32> {
        annotations::set_annotation_reply(self, page_number, index, xref, target)
    }

    /// Return the metadata of the file that one file attachment of a
    /// one-based page embeds, or `None` for another annotation.
    fn annotation_file_info(
        &self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
    ) -> PyResult<Option<attachments::EmbeddedFileInfo>> {
        annotations::annotation_file_info(self, py, page_number, index, xref)
    }

    /// Return the data of the file that one file attachment embeds.
    #[pyo3(signature = (page_number, index, xref, max_size=Some(DEFAULT_MAX_EMBEDDED_FILE_SIZE)))]
    fn annotation_file_read(
        &self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
        max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        annotations::annotation_file_read(self, py, page_number, index, xref, max_size)
    }

    /// Replace the data of the file that one file attachment embeds and
    /// return the annotation's object number.
    #[pyo3(signature = (page_number, index, xref, data, max_size=Some(DEFAULT_MAX_EMBEDDED_FILE_SIZE)))]
    fn annotation_file_write(
        &mut self,
        page_number: u32,
        index: u32,
        xref: u32,
        data: Vec<u8>,
        max_size: Option<usize>,
    ) -> PyResult<u32> {
        annotations::annotation_file_write(self, page_number, index, xref, data, max_size)
    }

    /// Read the display points and line endings of one annotation of a
    /// one-based page, found as `read_annotation` finds it.
    fn read_annotation_geometry(
        &self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
    ) -> PyResult<annotations::AnnotationGeometry> {
        annotations::read_annotation_geometry(self, py, page_number, index, xref)
    }

    /// Create a field with its first widget, or add a widget to an existing
    /// field, on a one-based page; return the widget's object number.
    fn add_widget(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        spec: forms::FieldSpec,
    ) -> PyResult<u32> {
        forms::add_widget(self, py, page_number, spec)
    }

    /// Remove one widget of a one-based page, and its field with the last one.
    fn remove_widget(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
    ) -> PyResult<()> {
        forms::remove_widget(self, py, page_number, index, xref)
    }

    /// Remove a field with all its widgets.
    fn remove_field(&mut self, py: Python<'_>, xref: u32) -> PyResult<()> {
        forms::remove_field(self, py, xref)
    }

    /// Gather top-level radio buttons that share a name under one parent
    /// field; returns how many groups were made.
    fn repair_radio_groups(&mut self, py: Python<'_>) -> PyResult<u32> {
        forms::repair_radio_groups(self, py)
    }

    /// Draw annotation and widget appearances into page content and remove
    /// them; returns how many annotations and widgets were removed.
    #[pyo3(signature = (annotations, widgets))]
    fn flatten(
        &mut self,
        py: Python<'_>,
        annotations: bool,
        widgets: bool,
    ) -> PyResult<(u32, u32)> {
        flattening::flatten(self, py, annotations, widgets)
    }

    /// Return a field's JavaScript scripts by `/AA` trigger.
    fn field_scripts(&self, py: Python<'_>, xref: u32) -> PyResult<Vec<(String, String)>> {
        forms::field_scripts(self, py, xref)
    }

    /// Set or remove one script of a field.
    #[pyo3(signature = (xref, trigger, source))]
    fn set_field_script(&mut self, xref: u32, trigger: &str, source: Option<&str>) -> PyResult<()> {
        forms::set_field_script(self, xref, trigger, source)
    }

    /// Return a widget's JavaScript scripts by trigger.
    fn widget_scripts(
        &self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
    ) -> PyResult<Vec<(String, String)>> {
        forms::widget_scripts(self, py, page_number, index, xref)
    }

    /// Set or remove one script of a widget; return its object number.
    #[pyo3(signature = (page_number, index, xref, trigger, source))]
    fn set_widget_script(
        &mut self,
        page_number: u32,
        index: u32,
        xref: u32,
        trigger: &str,
        source: Option<&str>,
    ) -> PyResult<u32> {
        forms::set_widget_script(self, page_number, index, xref, trigger, source)
    }

    /// Return a field's label, maximum length, default value, and the
    /// document's `/NeedAppearances`.
    fn field_details(&self, py: Python<'_>, xref: u32) -> PyResult<forms::FieldDetails> {
        forms::field_details(self, py, xref)
    }

    /// Change a field's properties or reset it, drawing its widgets again.
    fn edit_field(&mut self, py: Python<'_>, xref: u32, edit: forms::FieldEdit) -> PyResult<()> {
        forms::edit_field(self, py, xref, edit)
    }

    /// Set the AcroForm's `/NeedAppearances`.
    fn set_need_appearances_flag(&mut self, value: bool) -> PyResult<()> {
        forms::set_need_appearances_flag(self, value)
    }

    /// Return the one-based page and object number of each widget of a field.
    fn field_widget_locations(&self, py: Python<'_>, xref: u32) -> PyResult<Vec<(u32, u32)>> {
        forms::field_widget_locations(self, py, xref)
    }

    /// Read the form widgets of a one-based page.
    fn read_widgets(&self, py: Python<'_>, page_number: u32) -> PyResult<Vec<forms::WidgetTuple>> {
        forms::read_widgets(self, py, page_number)
    }

    /// Read one widget of a one-based page, found as `read_annotation` finds
    /// an annotation.
    fn read_widget(
        &self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
    ) -> PyResult<forms::WidgetTuple> {
        forms::read_widget(self, py, page_number, index, xref)
    }

    /// Change one widget's geometry and style and draw it again; return its
    /// object number.
    fn edit_widget(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
        edit: forms::WidgetEdit,
    ) -> PyResult<u32> {
        forms::edit_widget(self, py, page_number, index, xref, edit)
    }

    /// Read every named destination with its one-based page, display
    /// point, and zoom.
    fn named_destinations(
        &mut self,
        py: Python<'_>,
    ) -> PyResult<Vec<navigation::NamedDestinationItem>> {
        navigation::named_destinations(self, py)
    }

    /// Read the link annotations of a one-based page with their targets.
    ///
    /// Read `/A` actions (URI, GoTo, GoToR, Launch, Named) and `/Dest`, and
    /// resolve GoTo names from `/Names` trees and legacy `/Dests`. The page
    /// index and name-tree index persist until the next edit.
    fn read_links(&mut self, py: Python<'_>, page_number: u32) -> PyResult<Vec<LinkItem>> {
        navigation::read_links(self, py, page_number)
    }

    /// Read one link of a one-based page: the annotation at `index` of its
    /// `/Annots`, object `xref`, or that object wherever it moved.
    fn read_link(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        index: u32,
        xref: u32,
    ) -> PyResult<LinkItem> {
        navigation::read_link(self, py, page_number, index, xref)
    }

    /// Add the annotation that a specification describes to a one-based
    /// page, with its appearance, and return its object number.
    fn add_annotation(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        spec: annotations::AnnotationSpec,
    ) -> PyResult<u32> {
        annotations::add_annotation(self, py, page_number, spec)
    }

    /// Add a link annotation over display `rect` on a one-based page and
    /// return its object number.
    fn add_link(
        &mut self,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        target: TargetTuple,
    ) -> PyResult<u32> {
        annotations::add_link(self, page_number, rect, target)
    }

    /// Replace the display rectangle or the target of a one-based page's
    /// link, found as `read_link` finds it, and return the link's object
    /// number, which a link stored as a direct dictionary gains.
    #[pyo3(signature = (page_number, index, xref, rect=None, target=None))]
    fn set_link(
        &mut self,
        page_number: u32,
        index: u32,
        xref: u32,
        rect: Option<(f64, f64, f64, f64)>,
        target: Option<TargetTuple>,
    ) -> PyResult<u32> {
        annotations::set_link(self, page_number, index, xref, rect, target)
    }

    /// Remove annotations, each given as `(index, xref)` and found as
    /// `read_link` finds a link, and the popups they name from a one-based
    /// page's `/Annots`.
    fn remove_annotations(&mut self, page_number: u32, handles: Vec<(u32, u32)>) -> PyResult<()> {
        annotations::remove_annotations(self, page_number, handles)
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

    /// Return the optional-content groups that `/OCProperties /OCGs` lists.
    fn layers(&self) -> PyResult<Vec<(u32, u16)>> {
        layers::layers(self)
    }

    /// Return one group's name, intents, and visibility under `/D`.
    fn layer_info(&self, layer: (u32, u16)) -> PyResult<LayerInfo> {
        layers::layer_info(self, layer)
    }

    /// Add an optional-content group and return its object ID.
    fn add_layer(
        &mut self,
        name: &str,
        intents: Vec<String>,
        visible: bool,
    ) -> PyResult<(u32, u16)> {
        layers::add_layer(self, name, intents, visible)
    }

    /// Show or hide a group in the default configuration.
    fn set_layer_visible(&mut self, layer: (u32, u16), visible: bool) -> PyResult<()> {
        layers::set_layer_visible(self, layer, visible)
    }

    /// Replace a group's `/Name`.
    fn set_layer_name(&mut self, layer: (u32, u16), name: &str) -> PyResult<()> {
        layers::set_layer_name(self, layer, name)
    }

    /// Replace a group's `/Intent`.
    fn set_layer_intent(&mut self, layer: (u32, u16), intents: Vec<String>) -> PyResult<()> {
        layers::set_layer_intent(self, layer, intents)
    }

    /// Return AcroForm fields as `(full name, xref, kind, value, flags)`.
    ///
    /// Kind is text/checkbox/radio/pushbutton/combobox/listbox/signature or
    /// unknown. Value is stringified `/V`, including state names such as
    /// Yes/Off, or None; flags are the inherited `/Ff`.
    fn form_fields(&self, py: Python<'_>) -> PyResult<Vec<FormFieldTuple>> {
        forms::form_fields(self, py)
    }

    /// Return the `(export value, label)` options of a choice field's `/Opt`.
    fn form_field_choices(&self, py: Python<'_>, xref: u32) -> PyResult<Vec<(String, String)>> {
        forms::form_field_choices(self, py, xref)
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
