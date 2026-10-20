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

/// One link returned by read_links: kind, display Rect, URI, one-based lopdf
/// destination page, destination display point, zoom, external file, and named
/// destination or Named action.
pub(super) type LinkTuple = (
    String,
    (f64, f64, f64, f64),
    Option<String>,
    Option<u32>,
    Option<(f64, f64)>,
    Option<f64>,
    Option<String>,
    Option<String>,
);

/// One link with the object number of its annotation (0 for a direct
/// dictionary) and the annotation's `/NM` name ("" when absent), as pymupdf
/// reports them in `xref` and `id`.
pub(super) type IdentifiedLink = (LinkTuple, u32, String);

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

/// One flattened TOC item: one-based level, title, and page number.
pub(super) type TocEntry = (u32, String, u32);

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

/// Decode one outline title while bounding both source and returned text.
pub(super) fn bounded_toc_title(
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
pub(super) fn outline_destination<'a>(
    doc: &'a Document,
    node: &'a Dictionary,
) -> Option<&'a Object> {
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
pub(super) fn resolve_toc_page<'a>(
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
pub(super) fn collect_toc(doc: &IndexedDocument) -> PyResult<Vec<TocEntry>> {
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
    /// Resolve an array/name/string/or `/D` dictionary to a one-based lopdf page,
    /// destination display point, zoom, and named destination.
    ///
    /// Convert `/XYZ` left/top, `/FitH` top, or `/FitV` left into the destination
    /// page's rotated top-left-origin display space. Point-less `/Fit` or `/FitR`
    /// destinations return None.
    pub(super) fn resolve_dest(
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
    pub(super) fn page_links(
        &self,
        page_number: u32,
        cache: &mut LinkResolutionCache,
    ) -> PyResult<Vec<IdentifiedLink>> {
        let page_id = self.doc.page_id(page_number)?;
        let (crop, rotation) = self.resolve_display_geometry(page_id)?;
        let Some(annots) = self.page_annotation_items(page_id)? else {
            return Ok(Vec::new());
        };
        let mut out = Vec::new();
        // The annotation that produced each link of `out`, filled in before
        // the next annotation so that every early `continue` stays correct.
        let mut identities: Vec<(u32, String)> = Vec::new();
        let mut identity = (0u32, String::new());
        let mut encoded_bytes = 0usize;
        let mut returned_bytes = 0usize;
        for item in annots {
            while identities.len() < out.len() {
                push_fallibly(&mut identities, identity.clone(), "link identities")?;
            }
            let (xref, dict) = match item {
                Object::Reference(id) => match self.doc.get_object(*id).and_then(Object::as_dict) {
                    Ok(dict) => (id.0, dict),
                    Err(_) => continue,
                },
                Object::Dictionary(dict) => (0, dict),
                _ => continue,
            };
            if !matches!(dict.get(b"Subtype").and_then(Object::as_name), Ok(b"Link")) {
                continue;
            }
            let name = match dict.get(b"NM") {
                Ok(name) => bounded_annotation_text(
                    &self.doc,
                    name,
                    &mut encoded_bytes,
                    &mut returned_bytes,
                    "annotation name",
                )?
                .unwrap_or_default(),
                Err(_) => String::new(),
            };
            identity = (xref, name);
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
        while identities.len() < out.len() {
            push_fallibly(&mut identities, identity.clone(), "link identities")?;
        }
        let mut identified = Vec::new();
        identified.try_reserve_exact(out.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate returned links: {error}"))
        })?;
        identified.extend(
            out.into_iter()
                .zip(identities)
                .map(|(link, (xref, name))| (link, xref, name)),
        );
        Ok(identified)
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

/// Implements [`_Document::set_toc`].
pub(super) fn set_toc(this: &mut _Document, entries: Vec<(u32, String, u32)>) -> PyResult<()> {
    if entries.len() > MAX_TOC_ENTRIES {
        return Err(limit_err(
            "toc_entry_count",
            format!("cannot set more than {MAX_TOC_ENTRIES} TOC entries"),
        ));
    }
    let pages = this.doc.page_ids()?;
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
        this.doc
            .max_id
            .checked_add(object_count)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    }
    let mut parents: Vec<u32> = Vec::new();
    parents.try_reserve_exact(max_level).map_err(|error| {
        PdfError::new_err(format!("failed to allocate TOC parent stack: {error}"))
    })?;
    this.doc.catalog().map_err(to_py_err)?;

    this.invalidate_hayro_pdf_keeping_placements();
    // Discard existing outlines and construction state.
    this.doc.bookmarks.clear();
    this.doc.bookmark_table.clear();
    this.doc.max_bookmark_id = 0;
    if let Ok(catalog) = this.doc.catalog_mut() {
        catalog.remove(b"Outlines");
    }
    if is_empty {
        prune_unreachable_objects(&mut this.doc)?;
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
        let id = this
            .doc
            .add_bookmark(Bookmark::new(title, [0.0, 0.0, 0.0], 0, page_id), parent);
        parents.truncate(level - 1);
        parents.push(id);
    }
    if let Some(outline_id) = this.doc.build_outline() {
        this.doc
            .catalog_mut()
            .map_err(to_py_err)?
            .set("Outlines", Object::Reference(outline_id));
    }
    // Prune old outline objects.
    prune_unreachable_objects(&mut this.doc)?;
    Ok(())
}

/// Implements [`_Document::read_links`].
pub(super) fn read_links(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<Vec<IdentifiedLink>> {
    py.detach(|| {
        // Detach the cache so link collection can borrow the document, and
        // restore it on success and failure because reads never edit.
        let mut cache = this.link_resolution.take().unwrap_or_default();
        let links = this.page_links(page_number, &mut cache);
        this.link_resolution = Some(cache);
        links
    })
}
