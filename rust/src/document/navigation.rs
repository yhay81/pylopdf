//! Named destinations, links, and the outline.

use super::*;

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
pub(super) const MAX_TOC_ENTRIES: usize = 65_536;

pub(crate) const MAX_TOC_TREE_NODES: usize = 65_536;

pub(crate) const MAX_TOC_TREE_EDGES: usize = 131_072;

pub(super) const MAX_TOC_TREE_DEPTH: usize = 64;

pub(super) const MAX_TOC_DEST_DEPTH: usize = 32;

pub(super) const MAX_TOC_TEXT_BYTES: usize = 16 * 1024 * 1024;

/// One link annotation as `read_links` returns it: its index in the page's
/// `/Annots`, its object number (0 for a direct dictionary), its display
/// rectangle, and its target.
pub(super) type LinkItem = (u32, u32, DisplayRect, TargetTuple);

/// A rectangle in display coordinates: `(x0, y0, x1, y1)`.
type DisplayRect = (f64, f64, f64, f64);

/// Resolved link destination: page number, display point, zoom, named destination.
pub(super) type ResolvedDestination =
    (Option<u32>, Option<(f64, f64)>, Option<f64>, Option<String>);

/// Page operand of one destination array before it is resolved to a page.
#[derive(Clone, Copy)]
pub(super) enum DestinationPage {
    Reference(ObjectId),
    /// Some producers write a zero-based page index instead of a reference.
    Index(i64),
    Missing,
}

/// Destination fit types that carry a target point.
#[derive(Clone, Copy)]
pub(super) enum DestinationFit {
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
pub(super) struct LinkDestination {
    pub(super) page: DestinationPage,
    pub(super) fit: DestinationFit,
    /// Numeric array elements 2 through 4: the fit's coordinates and zoom.
    pub(super) numbers: [Option<f64>; 3],
}

impl LinkDestination {
    pub(super) const UNRESOLVED: Self = Self {
        page: DestinationPage::Missing,
        fit: DestinationFit::Other,
        numbers: [None; 3],
    };

    /// Reduce a dereferenced destination array or `/D` dictionary.
    pub(super) fn from_object(doc: &Document, object: &Object) -> Self {
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
pub(super) struct NamedDestinationEntry {
    pub(super) key_start: u32,
    pub(super) key_len: u32,
    pub(super) destination: LinkDestination,
}

impl NamedDestinationEntry {
    pub(super) fn key<'a>(&self, keys: &'a [u8]) -> &'a [u8] {
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
pub(super) struct NamedDestinationIndex {
    pub(super) keys: Vec<u8>,
    pub(super) entries: Vec<NamedDestinationEntry>,
}

impl NamedDestinationIndex {
    /// Index the complete tree under the shared limits, or fail without a
    /// partial index.
    pub(super) fn build(doc: &Document) -> PyResult<Self> {
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

    pub(super) fn get(&self, name: &[u8]) -> Option<LinkDestination> {
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
pub(super) struct LinkResolutionCache {
    /// Built on the first named lookup, so pages without named links never
    /// walk the tree. A refused build leaves it unset.
    pub(super) named: Option<NamedDestinationIndex>,
}

impl LinkResolutionCache {
    /// Look up one name-tree destination, indexing the tree on first use.
    pub(super) fn named_destination(
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

/// Visit a named-destination tree with complete node-local safety budgets.
pub(super) fn visit_named_destinations<'a>(
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
pub(super) fn named_destination_root(doc: &Document) -> Option<&Object> {
    doc.catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Names").ok())
        .map(|names| deref_object(doc, names))
        .and_then(|names| names.as_dict().ok())
        .and_then(|names| names.get(b"Dests").ok())
}

/// Build one borrowed index so TOC resolution scans a name tree at most once.
pub(super) fn named_destination_index(doc: &Document) -> PyResult<HashMap<&[u8], &Object>> {
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
pub(super) fn lookup_legacy_named_dest<'a>(doc: &'a Document, name: &[u8]) -> Option<&'a Object> {
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
    pub(super) fn charge(self, total: &mut usize, amount: usize) -> PyResult<()> {
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
pub(super) fn add_toc_text_budget(total: &mut usize, amount: usize, label: &str) -> PyResult<()> {
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

/// Borrow an imported annotation operand under a bounded reference walk.
pub(super) fn resolve_import_object<'a>(
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
pub(super) fn imported_destination(
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
pub(super) fn prepare_imported_page_links<'a>(
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

impl _Document {
    /// Resolve an array, name, string, or `/D` dictionary to a one-based page,
    /// a display point, a zoom, and a named destination, charging the name
    /// through `name_text`, which returns it as text.
    ///
    /// `/XYZ` left/top, `/FitH` top, and `/FitV` left convert into the
    /// destination page's display space; `/Fit` and `/FitR` have no point.
    /// With `keep_unset`, a destination that leaves its coordinates null,
    /// which keeps the viewer's position, has no point either.
    pub(super) fn resolve_destination(
        &self,
        dest: &Object,
        cache: &mut LinkResolutionCache,
        keep_unset: bool,
        name_text: &mut dyn FnMut(&[u8]) -> PyResult<String>,
    ) -> PyResult<ResolvedDestination> {
        let doc = &self.doc;
        let mut nameddest = None;
        let resolved = deref_object(doc, dest);
        let destination = if let Object::Name(name) | Object::String(name, _) = resolved {
            nameddest = Some(name_text(name)?);
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
                    zoom = third.filter(|z| *z != 0.0);
                    if !(keep_unset && first.is_none() && second.is_none()) {
                        let left = first.unwrap_or(crop[0]);
                        let top = second.unwrap_or(crop[3]);
                        to = Some(draw::pdf_to_display(crop, rotation, left, top));
                    }
                }
                DestinationFit::Horizontal if !(keep_unset && first.is_none()) => {
                    let top = first.unwrap_or(crop[3]);
                    to = Some(draw::pdf_to_display(crop, rotation, crop[0], top));
                }
                DestinationFit::Vertical if !(keep_unset && first.is_none()) => {
                    let left = first.unwrap_or(crop[0]);
                    to = Some(draw::pdf_to_display(crop, rotation, left, crop[3]));
                }
                DestinationFit::Horizontal | DestinationFit::Vertical => {}
                DestinationFit::Other => {}
            }
        }
        Ok((page, to, zoom, nameddest))
    }

    /// Read one page's link annotations, resolving destinations through the
    /// page index and `cache` so repeated reads do not walk the page or name
    /// trees again.
    pub(super) fn page_links(
        &self,
        page_number: u32,
        cache: &mut LinkResolutionCache,
    ) -> PyResult<Vec<LinkItem>> {
        let page_id = self.doc.page_id(page_number)?;
        let (crop, rotation) = self.resolve_display_geometry(page_id)?;
        let Some(annots) = self.page_annotation_items(page_id)? else {
            return Ok(Vec::new());
        };
        let mut out = Vec::new();
        let mut budget = (0usize, 0usize);
        for (index, item) in annots.iter().enumerate() {
            let (xref, dict) = match item {
                Object::Reference(id) => match self.doc.get_object(*id).and_then(Object::as_dict) {
                    Ok(dict) => (id.0, dict),
                    Err(_) => continue,
                },
                Object::Dictionary(dict) => (0, dict),
                _ => continue,
            };
            let Some((rect, target)) =
                self.link_item(dict, (crop, rotation), cache, &mut budget)?
            else {
                continue;
            };
            let index = u32::try_from(index).unwrap_or(u32::MAX);
            push_fallibly(&mut out, (index, xref, rect, target), "returned links")?;
        }
        Ok(out)
    }

    /// Read the link at `index` of a page's `/Annots`, which must be object
    /// `xref`, or a direct dictionary for 0; an indirect link that moved is
    /// found again by its object number.
    pub(super) fn page_link(
        &self,
        page_number: u32,
        index: u32,
        xref: u32,
        cache: &mut LinkResolutionCache,
    ) -> PyResult<LinkItem> {
        let page_id = self.doc.page_id(page_number)?;
        let (crop, rotation) = self.resolve_display_geometry(page_id)?;
        let (index, dict) = self.annotation_at(page_id, index, xref)?;
        let mut budget = (0usize, 0usize);
        match self.link_item(dict, (crop, rotation), cache, &mut budget)? {
            Some((rect, target)) => Ok((index, xref, rect, target)),
            None => Err(StaleObjectError::new_err(format!(
                "annotation {xref} at index {index} is no longer a link"
            ))),
        }
    }

    /// Return a link annotation's display rectangle and target, or `None`
    /// for an annotation that is no link or has no rectangle. `budget` holds
    /// the encoded and returned text bytes of the call.
    fn link_item(
        &self,
        dict: &Dictionary,
        (crop, rotation): ([f64; 4], i64),
        cache: &mut LinkResolutionCache,
        budget: &mut (usize, usize),
    ) -> PyResult<Option<(DisplayRect, TargetTuple)>> {
        if !matches!(dict.get(b"Subtype").and_then(Object::as_name), Ok(b"Link")) {
            return Ok(None);
        }
        let Some(rect) = resolve_box(&self.doc, dict, b"Rect") else {
            return Ok(None);
        };
        let display = draw::pdf_rect_to_display(crop, rotation, rect);
        let target = self.link_target(dict, cache, &mut budget.0, &mut budget.1)?;
        Ok(Some((
            (display[0], display[1], display[2], display[3]),
            target,
        )))
    }

    /// Return where a link annotation leads: its `/A` action, else its
    /// `/Dest`, or `"none"` for an action that pylopdf does not read, such
    /// as JavaScript.
    fn link_target(
        &self,
        link: &Dictionary,
        cache: &mut LinkResolutionCache,
        encoded_bytes: &mut usize,
        returned_bytes: &mut usize,
    ) -> PyResult<TargetTuple> {
        let none = || ("none".to_owned(), None, None, None, None, None);
        let Some(action) = link
            .get(b"A")
            .ok()
            .and_then(|action| deref_object(&self.doc, action).as_dict().ok())
        else {
            return match link.get(b"Dest") {
                Ok(dest) => self.link_destination(dest, cache, encoded_bytes, returned_bytes),
                Err(_) => Ok(none()),
            };
        };
        let mut bytes =
            |object: Option<&Object>, name: bool, label: &str| -> PyResult<Option<String>> {
                let resolved = object.map(|object| deref_object(&self.doc, object));
                let raw = match resolved {
                    Some(Object::Name(bytes)) if name => Some(bytes.as_slice()),
                    Some(Object::String(bytes, _)) if !name => Some(bytes.as_slice()),
                    _ => None,
                };
                raw.map(|raw| bounded_annotation_bytes(raw, encoded_bytes, returned_bytes, label))
                    .transpose()
            };
        Ok(match action.get(b"S").and_then(Object::as_name) {
            Ok(b"URI") => {
                let uri = bytes(action.get(b"URI").ok(), false, "URI text")?;
                ("uri".to_owned(), None, None, None, uri, None)
            }
            Ok(b"GoTo") => match action.get(b"D") {
                Ok(dest) => {
                    return self.link_destination(dest, cache, encoded_bytes, returned_bytes);
                }
                Err(_) => none(),
            },
            Ok(b"GoToR") => {
                let file = match action.get(b"F") {
                    Ok(object) => bounded_annotation_filespec_name(
                        &self.doc,
                        object,
                        encoded_bytes,
                        returned_bytes,
                    )?,
                    Err(_) => None,
                };
                // A destination in another file is a name, or an array whose
                // first element is a zero-based page index (ISO 32000-2 12.6.4.3).
                let destination = action
                    .get(b"D")
                    .ok()
                    .map(|dest| deref_object(&self.doc, dest));
                let (page, name) = match destination {
                    Some(Object::Name(name) | Object::String(name, _)) => (
                        None,
                        Some(bounded_annotation_bytes(
                            name,
                            encoded_bytes,
                            returned_bytes,
                            "named destination",
                        )?),
                    ),
                    Some(Object::Array(items)) => (
                        items
                            .first()
                            .and_then(|page| page.as_i64().ok())
                            .and_then(|page| u32::try_from(page).ok()),
                        None,
                    ),
                    _ => (None, None),
                };
                ("gotor".to_owned(), page, None, None, file, name)
            }
            Ok(b"Launch") => {
                let file = match action.get(b"F") {
                    Ok(object) => bounded_annotation_filespec_name(
                        &self.doc,
                        object,
                        encoded_bytes,
                        returned_bytes,
                    )?,
                    Err(_) => None,
                };
                ("launch".to_owned(), None, None, None, file, None)
            }
            Ok(b"Named") => {
                let name = bytes(action.get(b"N").ok(), true, "named action")?;
                ("named".to_owned(), None, None, None, None, name)
            }
            _ => none(),
        })
    }

    /// Return a destination in this document as a `"goto"` target, without a
    /// point where the destination leaves its coordinates unset, or `"none"`
    /// when it names no page of this document.
    fn link_destination(
        &self,
        dest: &Object,
        cache: &mut LinkResolutionCache,
        encoded_bytes: &mut usize,
        returned_bytes: &mut usize,
    ) -> PyResult<TargetTuple> {
        let (page, to, zoom, _) = self.resolve_destination(dest, cache, true, &mut |name| {
            bounded_annotation_bytes(name, encoded_bytes, returned_bytes, "named destination")
        })?;
        Ok(match page {
            Some(page) => ("goto".to_owned(), Some(page), to, zoom, None, None),
            None => ("none".to_owned(), None, None, None, None, None),
        })
    }
}

/// Push onto a link-reading collection, refusing allocation failure.
fn push_fallibly<T>(values: &mut Vec<T>, value: T, label: &str) -> PyResult<()> {
    values
        .try_reserve(1)
        .map_err(|error| PdfError::new_err(format!("failed to grow {label}: {error}")))?;
    values.push(value);
    Ok(())
}

/// Implements [`_Document::read_link`].
pub(super) fn read_link(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
) -> PyResult<LinkItem> {
    py.detach(|| {
        let mut cache = this.link_resolution.take().unwrap_or_default();
        let link = this.page_link(page_number, index, xref, &mut cache);
        this.link_resolution = Some(cache);
        link
    })
}

/// Implements [`_Document::read_links`].
pub(super) fn read_links(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<Vec<LinkItem>> {
    py.detach(|| {
        // Detach the cache so link collection can borrow the document, and
        // restore it on success and failure because reads never edit.
        let mut cache = this.link_resolution.take().unwrap_or_default();
        let links = this.page_links(page_number, &mut cache);
        this.link_resolution = Some(cache);
        links
    })
}

fn finite(value: f64, label: &str) -> PyResult<f64> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(PdfError::new_err(format!(
            "a destination's {label} must be finite"
        )))
    }
}

/// Return the key and value that make an outline item or a link lead to
/// `target`: `/Dest` for a page of this document, `/A` for an action, or
/// nothing. `charge` bounds each text that the target holds.
pub(super) fn target_entry(
    this: &_Document,
    page_ids: &[ObjectId],
    target: &TargetTuple,
    charge: &mut dyn FnMut(&str, &str) -> PyResult<()>,
) -> PyResult<Option<(&'static str, Object)>> {
    let (kind, page, point, zoom, text, name) = target;
    let text_value =
        |label: &str, charge: &mut dyn FnMut(&str, &str) -> PyResult<()>| -> PyResult<Object> {
            let text = text
                .as_deref()
                .filter(|text| !text.is_empty())
                .ok_or_else(|| PdfError::new_err(format!("a {kind} target needs a {label}")))?;
            charge(text, label)?;
            if label == "file" {
                try_info_metadata_text_string(text)
            } else {
                Ok(Object::String(
                    text.as_bytes().to_vec(),
                    StringFormat::Literal,
                ))
            }
        };
    Ok(match kind.as_str() {
        "none" => None,
        "goto" => {
            let page_number =
                page.ok_or_else(|| PdfError::new_err("a destination needs a page"))?;
            let page_id = page_id_from_index(page_ids, page_number)?;
            let zoom = zoom.map(|zoom| finite(zoom, "zoom")).transpose()?;
            if zoom.is_some_and(|zoom| zoom < 0.0) {
                return Err(PdfError::new_err(
                    "a destination's zoom must not be negative",
                ));
            }
            let zoom = zoom
                .map(|zoom| checked_pdf_real(zoom, "destination zoom"))
                .transpose()?;
            let array = match (point, zoom) {
                (None, None) => vec![Object::Reference(page_id), Object::Name(b"Fit".to_vec())],
                _ => {
                    let (left, top) = match point {
                        Some((x, y)) => {
                            let (crop, rotation) = this.resolve_display_geometry(page_id)?;
                            let (left, top) = draw::display_to_pdf(
                                crop,
                                rotation,
                                finite(*x, "point")?,
                                finite(*y, "point")?,
                            );
                            (
                                Object::Real(checked_pdf_real(left, "destination left")?),
                                Object::Real(checked_pdf_real(top, "destination top")?),
                            )
                        }
                        None => (Object::Null, Object::Null),
                    };
                    vec![
                        Object::Reference(page_id),
                        Object::Name(b"XYZ".to_vec()),
                        left,
                        top,
                        zoom.map_or(Object::Null, Object::Real),
                    ]
                }
            };
            Some(("Dest", Object::Array(array)))
        }
        "uri" => Some((
            "A",
            Object::Dictionary(dictionary! {
                "S" => Object::Name(b"URI".to_vec()),
                "URI" => text_value("URI", charge)?,
            }),
        )),
        "gotor" => {
            let file = text_value("file", charge)?;
            let destination = match (name.as_deref(), page) {
                (Some(name), _) if !name.is_empty() => {
                    charge(name, "destination name")?;
                    Object::String(name.as_bytes().to_vec(), StringFormat::Literal)
                }
                (_, page) => Object::Array(vec![
                    Object::Integer(i64::from(page.unwrap_or(0))),
                    Object::Name(b"Fit".to_vec()),
                ]),
            };
            Some((
                "A",
                Object::Dictionary(dictionary! {
                    "S" => Object::Name(b"GoToR".to_vec()),
                    "F" => file,
                    "D" => destination,
                }),
            ))
        }
        "launch" => Some((
            "A",
            Object::Dictionary(dictionary! {
                "S" => Object::Name(b"Launch".to_vec()),
                "F" => text_value("file", charge)?,
            }),
        )),
        "named" => {
            let name = name
                .as_deref()
                .filter(|name| !name.is_empty())
                .ok_or_else(|| PdfError::new_err("a named action needs a name"))?;
            charge(name, "action name")?;
            Some((
                "A",
                Object::Dictionary(dictionary! {
                    "S" => Object::Name(b"Named".to_vec()),
                    "N" => Object::Name(name.as_bytes().to_vec()),
                }),
            ))
        }
        other => {
            return Err(PdfError::new_err(format!("unknown target kind {other:?}")));
        }
    })
}
