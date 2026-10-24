//! The page-tree walker, page batches, and structural page edits.

use super::*;

/// Explicit page batches may repeat pages up to the larger of the page count
/// and this floor, so repetition never costs more than one full document pass.
pub(super) const MIN_PAGE_ENTRY_LIMIT: usize = 4_096;

/// Replace call-scoped hayro caches after this many page entries so fonts and
/// objects parsed during whole-document batches cannot accumulate unbounded.
pub(super) const PAGE_BATCH_CHUNK: usize = 4_096;

pub(super) const MAX_PAGE_TREE_DEPTH: usize = 256;

pub(super) const MAX_PAGE_TREE_REFERENCE_DEPTH: usize = 32;

/// Replace root Pages Kids/Count with `ordered`, flattening the tree.
///
/// Materialize inheritance on every page and point Parent to root. Every
/// fallible step runs before the first change. Callers prune obsolete
/// intermediate nodes when the result is saved.
pub(super) fn rebuild_flat_page_tree(
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
pub(super) fn page_tree_root(doc: &Document) -> PyResult<ObjectId> {
    doc.catalog()
        .map_err(to_py_err)?
        .get(b"Pages")
        .and_then(Object::as_reference)
        .map_err(|_| PdfError::new_err("Catalog Pages must be an indirect page-tree node"))
}

pub(super) struct PageTreeFrame<'a> {
    pub(super) kids: &'a [Object],
    pub(super) next: usize,
    pub(super) depth: usize,
}

/// What a successful page-tree walk read besides page `/Type` entries.
///
/// The walk reads `/Root`, the catalog's `/Pages`, each node's `/Type` and
/// `/Kids`, every object on a `/Kids` reference chain, and the object count,
/// which bounds the traversed edges and only grows during edits that keep the
/// page index. Those edits check their changes against these facts.
#[derive(Default)]
pub(super) struct PageTreeFacts {
    /// Objects on `/Kids` reference chains, including the arrays they reach.
    pub(super) kids_objects: HashSet<ObjectId>,
    /// Absent objects named by a skipped `/Kids` entry or its reference chain.
    ///
    /// A new object at one of these IDs could turn the entry into a page.
    pub(super) missing_kids: HashSet<ObjectId>,
}

impl PageTreeFacts {
    pub(super) fn record_kids_object(&mut self, id: ObjectId) -> PyResult<()> {
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
    pub(super) fn record_missing_kid(&mut self, doc: &Document, kid_id: ObjectId) -> PyResult<()> {
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

pub(super) fn resolve_page_tree_kids<'a>(
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
pub(super) fn walk_page_tree(
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
pub(super) fn collect_page_ids(
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

/// Collect the pages that remain after a deletion or selection.
pub(super) fn retained_set(retained: &[ObjectId]) -> PyResult<HashSet<ObjectId>> {
    let mut set = HashSet::new();
    set.try_reserve(retained.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate retained-page tracking: {error}"
        ))
    })?;
    set.extend(retained.iter().copied());
    Ok(set)
}

/// Reject explicit page entries beyond the document-relative batch bound.
pub(super) fn check_page_entry_count(
    entries: usize,
    page_count: usize,
    verb: &str,
) -> PyResult<()> {
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

pub(super) fn page_id_from_index(page_ids: &[ObjectId], page_number: u32) -> PyResult<ObjectId> {
    page_number
        .checked_sub(1)
        .and_then(|index| page_ids.get(index as usize))
        .copied()
        .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))
}

impl _Document {
    /// Bound an explicit page batch, counting pages only above the floor.
    pub(super) fn check_page_entries(&self, entries: usize, verb: &str) -> PyResult<()> {
        if entries <= MIN_PAGE_ENTRY_LIMIT {
            return Ok(());
        }
        check_page_entry_count(entries, self.doc.page_count()?, verb)
    }

    /// Return the ObjectId of a one-based page.
    pub(super) fn page_id(&self, page_number: u32) -> PyResult<ObjectId> {
        self.doc.page_id(page_number)
    }

    /// Return root Pages ObjectId, creating a minimal tree for empty documents.
    pub(super) fn ensure_page_tree(&mut self) -> lopdf::Result<ObjectId> {
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
    pub(super) fn preflight_page_tree_rebuild(&self, pages: &[ObjectId]) -> PyResult<()> {
        for &page_id in pages {
            resolve_inherited_page_dict(&self.doc, page_id)?;
        }
        Ok(())
    }

    /// Import selected one-based pages from `other` in order into self's object
    /// space and return their ObjectIds; the caller connects root Kids.
    ///
    /// `other_pages` is `other`'s page-tree order, and `page_numbers` must be
    /// unique. Materialize inherited page attributes and repoint Parent to
    /// root Pages. The returned AcroForm import registers the fields of the
    /// imported pages once the caller has connected them.
    pub(super) fn transplant_pages(
        &mut self,
        other: &Self,
        mut other_pages: Vec<ObjectId>,
        page_numbers: &[u32],
        pages_id: ObjectId,
    ) -> PyResult<(Vec<ObjectId>, Option<AcroFormImport>, Option<LayerImport>)> {
        let complete = page_numbers.len() == other_pages.len();
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
        let form_import = AcroFormImport::prepare(
            &self.doc,
            &mut other_doc,
            &other_pages,
            &imported_pages,
            complete,
        )?;
        let layer_import = LayerImport::prepare(
            &self.doc,
            &other_doc,
            resolved_pages
                .iter()
                .flat_map(|(_, dict)| dict.iter().map(|(_, value)| value)),
        )?;
        if let Some(form_import) = &form_import {
            form_import.reserve(&mut self.doc)?;
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
        Ok((ordered_ids, form_import, layer_import))
    }

    /// Return current pages with `new_ids` inserted at zero-based `position`.
    ///
    /// `new_ids` must not yet be reachable from root Kids or the bounded index.
    pub(super) fn spliced_page_order(
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

    /// Replace root Pages Kids/Count with the given order, flattening the tree.
    ///
    /// Materialize inheritance on every page and point Parent to root.
    /// The caller prunes obsolete intermediate nodes.
    pub(super) fn rebuild_page_tree(
        &mut self,
        pages_id: ObjectId,
        ordered: Vec<ObjectId>,
    ) -> PyResult<()> {
        self.rebuild_page_tree_with_cleanup(pages_id, ordered, None)
    }

    pub(super) fn rebuild_page_tree_with_cleanup(
        &mut self,
        pages_id: ObjectId,
        ordered: Vec<ObjectId>,
        cleanup: Option<&PageCleanupPlan>,
    ) -> PyResult<()> {
        rebuild_flat_page_tree(&mut self.doc, pages_id, &ordered, cleanup)
    }
}

/// Implements [`_Document::delete_pages`].
pub(super) fn delete_pages(this: &mut _Document, page_numbers: Vec<u32>) -> PyResult<()> {
    if page_numbers.is_empty() {
        return Ok(());
    }

    let pages = this.doc.page_ids()?;
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
    let pages_id = this
        .doc
        .catalog()
        .map_err(to_py_err)?
        .get(b"Pages")
        .and_then(Object::as_reference)
        .map_err(|_| PdfError::new_err("Catalog Pages must be an indirect page-tree node"))?;

    let cleanup = PageCleanupPlan::prepare(&this.doc, &retained, &deleted)?;
    let retained_ids = retained_set(&retained)?;
    let form_prune = FormPagePrune::prepare(&this.doc, pages, &retained_ids)?;
    let structure_prune =
        StructurePagePrune::prepare(&this.doc, pages, &retained_ids, cleanup.removed_links())?;
    this.rebuild_page_tree_with_cleanup(pages_id, retained, Some(&cleanup))?;
    cleanup.apply(&mut this.doc);
    if let Some(form_prune) = form_prune {
        form_prune.apply(&mut this.doc);
    }
    if let Some(structure_prune) = structure_prune {
        structure_prune.apply(&mut this.doc);
    }
    this.invalidate_hayro_pdf_keeping_placements();
    prune_unreachable_objects(&mut this.doc)?;
    Ok(())
}

/// Implements [`_Document::merge`].
pub(super) fn merge(this: &mut _Document, py: Python<'_>, other: &_Document) -> PyResult<()> {
    let page_count = other.doc.page_count()?;
    let count = u32::try_from(page_count).map_err(|e| PdfError::new_err(e.to_string()))?;
    let mut all = Vec::new();
    all.try_reserve_exact(page_count).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate complete imported page range: {error}"
        ))
    })?;
    all.extend(1..=count);
    this.merge_pages(py, other, all, None)
}

/// Implements [`_Document::merge_pages`].
pub(super) fn merge_pages(
    this: &mut _Document,
    py: Python<'_>,
    other: &_Document,
    page_numbers: Vec<u32>,
    position: Option<usize>,
) -> PyResult<()> {
    if page_numbers.is_empty() {
        return Ok(());
    }
    let existing_pages = this.doc.owned_page_ids()?;
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
    this.preflight_page_tree_rebuild(&existing_pages)?;
    py.detach(|| {
        // Reserve Pages/Catalog IDs in an empty target to avoid source collisions.
        let pages_id = this.ensure_page_tree().map_err(to_py_err)?;
        let (new_ids, form_import, layer_import) =
            this.transplant_pages(other, source_pages, &page_numbers, pages_id)?;
        this.invalidate_hayro_pdf_keeping_placements();
        let order = _Document::spliced_page_order(existing_pages, new_ids, position)?;
        this.rebuild_page_tree(pages_id, order)?;
        if let Some(form_import) = form_import {
            form_import.apply(&mut this.doc);
        }
        if let Some(layer_import) = layer_import {
            layer_import.apply(&mut this.doc);
        }
        // transplant_pages initially moves all non-page objects. Prune
        // attachments/metadata unreachable from selected pages or hidden
        // source data remains even for a full-range append.
        prune_unreachable_objects(&mut this.doc)?;
        Ok(())
    })
}

/// Implements [`_Document::new_page`].
pub(super) fn new_page(
    this: &mut _Document,
    position: Option<usize>,
    width: f32,
    height: f32,
) -> PyResult<()> {
    if !width.is_finite() || !height.is_finite() || width <= 0.0 || height <= 0.0 {
        return Err(PdfError::new_err(format!(
            "width / height must be positive finite values within PDF real-number range: ({width:?}, {height:?})"
        )));
    }
    let existing_pages = this.doc.owned_page_ids()?;
    this.preflight_page_tree_rebuild(&existing_pages)?;
    this.invalidate_hayro_pdf_keeping_placements();
    let pages_id = this.ensure_page_tree().map_err(to_py_err)?;
    let media_box = Object::Array(vec![
        Object::Real(0.0),
        Object::Real(0.0),
        Object::Real(width),
        Object::Real(height),
    ]);
    let page_id = this.doc.add_object(dictionary! {
        "Type" => "Page",
        "Parent" => pages_id,
        "MediaBox" => media_box.clone(),
        "CropBox" => media_box,
        "Rotate" => 0,
        "Resources" => Dictionary::new(),
    });
    let order = _Document::spliced_page_order(existing_pages, vec![page_id], position)?;
    this.rebuild_page_tree(pages_id, order)?;
    prune_unreachable_objects(&mut this.doc)?;
    Ok(())
}

/// Implements [`_Document::copy_pages`].
pub(super) fn copy_pages(
    this: &mut _Document,
    page_numbers: Vec<u32>,
    position: Option<usize>,
) -> PyResult<()> {
    if page_numbers.is_empty() {
        return Ok(());
    }
    let existing_pages = this.doc.owned_page_ids()?;
    check_page_entry_count(page_numbers.len(), existing_pages.len(), "copy")?;
    this.preflight_page_tree_rebuild(&existing_pages)?;
    let mut copies = Vec::new();
    copies
        .try_reserve_exact(page_numbers.len())
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate copied page dictionaries: {error}"
            ))
        })?;
    for &page_number in &page_numbers {
        let source_id = page_id_from_index(&existing_pages, page_number)?;
        copies.push(resolve_inherited_page_dict(&this.doc, source_id)?);
    }
    let mut new_ids = Vec::new();
    new_ids.try_reserve_exact(copies.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate copied page IDs: {error}"))
    })?;
    let pages_id = this.ensure_page_tree().map_err(to_py_err)?;
    this.invalidate_hayro_pdf_keeping_placements();
    for mut dict in copies {
        dict.set("Parent", pages_id);
        new_ids.push(this.doc.add_object(Object::Dictionary(dict)));
    }
    let order = _Document::spliced_page_order(existing_pages, new_ids, position)?;
    this.rebuild_page_tree(pages_id, order)?;
    prune_unreachable_objects(&mut this.doc)?;
    Ok(())
}

/// Implements [`_Document::select`].
pub(super) fn select(this: &mut _Document, page_numbers: Vec<u32>) -> PyResult<()> {
    let pages = this.doc.page_ids()?;
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
    let existing_pages_id = this
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
        this.doc
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
    let cleanup = PageCleanupPlan::prepare(&this.doc, &selected_ids, &removed)?;
    let (form_prune, structure_prune) = if removed.is_empty() {
        (None, None)
    } else {
        let retained_ids = retained_set(&selected_ids)?;
        (
            FormPagePrune::prepare(&this.doc, pages, &retained_ids)?,
            StructurePagePrune::prepare(&this.doc, pages, &retained_ids, cleanup.removed_links())?,
        )
    };

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
    let mut planned_max_id = this.doc.max_id;
    for page_id in selected_ids {
        let use_id = if seen.insert(page_id) {
            page_id
        } else {
            planned_max_id = planned_max_id
                .checked_add(1)
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
            (planned_max_id, 0)
        };
        let mut dict = resolve_inherited_page_dict(&this.doc, page_id)?;
        cleanup.clean_page(page_id, &mut dict);
        if let Some(pages_id) = existing_pages_id {
            dict.set("Parent", pages_id);
        }
        kids.push(Object::Reference(use_id));
        resolved_pages.push((use_id, dict));
    }

    this.invalidate_hayro_pdf_keeping_placements();
    let pages_id = match existing_pages_id {
        Some(pages_id) => pages_id,
        None => this.ensure_page_tree().map_err(to_py_err)?,
    };
    for (page_id, mut dict) in resolved_pages {
        dict.set("Parent", pages_id);
        this.doc.objects.insert(page_id, Object::Dictionary(dict));
    }
    this.doc.max_id = this.doc.max_id.max(planned_max_id);
    let pages_dict = this
        .doc
        .get_object_mut(pages_id)
        .and_then(Object::as_dict_mut)
        .map_err(to_py_err)?;
    pages_dict.set("Kids", kids);
    pages_dict.set("Count", count);

    cleanup.apply(&mut this.doc);
    if let Some(form_prune) = form_prune {
        form_prune.apply(&mut this.doc);
    }
    if let Some(structure_prune) = structure_prune {
        structure_prune.apply(&mut this.doc);
    }
    // Remove pages and intermediate nodes that became unreachable.
    prune_unreachable_objects(&mut this.doc)?;
    Ok(())
}
