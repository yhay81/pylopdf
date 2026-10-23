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
        owned
            .try_reserve_exact(pages.len())
            .map_err(|error| PdfError::new_err(format!("failed to copy page index: {error}")))?;
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

    /// Remove one rotation or page-box entry from a page object; such
    /// entries never name page-tree objects, so the page index survives.
    pub(super) fn remove_page_box_entry(&mut self, page_id: ObjectId, key: &str) -> PyResult<()> {
        debug_assert!(UNWALKED_PAGE_ENTRIES.contains(&key) && key != "Contents");
        self.doc
            .get_object_mut(page_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?
            .remove(key.as_bytes());
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
    pub(super) fn page_annotations_mut(&mut self, page_id: ObjectId) -> PyResult<&mut Vec<Object>> {
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
    fn max_id(&self) -> u32 {
        self.doc.max_id
    }

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
