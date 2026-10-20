//! Register the AcroForm fields of imported pages in the receiving document.
//!
//! `insert_pdf` transplants a renumbered copy of the source graph and drops
//! the source catalog, so without this step the imported widgets would stay
//! on their pages while no field of the receiving `/AcroForm` reached them.
//! The import follows qpdf's page-copy conventions. Imported top-level fields
//! join the receiving `/Fields`; a top-level partial name that the receiving
//! document already uses gains the smallest unused `+N` suffix, one suffix per
//! distinct name, and JavaScript that names the field is not rewritten. Source
//! default resources join the receiving `/DR`: one that equals the receiving
//! resource of its name is shared, and any other under a name already in use
//! gains the smallest unused `_N` suffix that every imported default
//! appearance string follows. When the receiving document-wide `/DA` or `/Q`
//! differs from the source's, the source value moves onto the imported
//! top-level fields that relied on it. The source `/NeedAppearances` carries
//! over when true, and `/CO` entries of imported fields are appended; `/XFA`
//! and `/SigFlags` describe the source document as a whole and are not copied.
//!
//! A partial import walks the source field tree under the limits of
//! `get_form_fields()`. A widget belongs to the imported pages when one of them
//! lists it in `/Annots`, or when no source page lists it and its `/P` names
//! an imported page. The import removes every other widget, every field left
//! without a widget of its own, and every top-level field without one. Fields
//! that never had a widget stay below an imported parent, and a retained
//! widget whose `/P` names no imported page is pointed at the imported page
//! that lists it. A complete import keeps the field tree as it is and does not
//! walk it, so the size of a field tree never refuses one; the merged form is
//! then as readable by `get_form_fields()` as its size allows. An import into
//! a document that already has a form compares top-level names within the
//! 1 MiB name budget.
//!
//! Every read, traversal, and allocation precedes the first change to the
//! receiving document: source-side edits apply to the disposable renumbered
//! copy, receiving-side capacity is reserved before the imported objects move
//! in, and the commit only moves prepared values into reserved space.

use std::collections::{HashMap, HashSet};

use lopdf::{Dictionary, Document, Object, ObjectId, StringFormat, decode_text_string};
use pyo3::prelude::*;

use crate::document::{
    FieldKid, MAX_FORM_FIELD_NAME_BYTES, MAX_FORM_FIELD_TREE_DEPTH, MAX_FORM_FIELD_WIDGETS,
    MAX_PAGE_ANNOTATIONS, PdfError, add_form_tree_edges, bounded_form_text, classify_field_kid,
    deref_object, field_kids, form_text_string, inheritable_field_value, is_null_equivalent,
    looks_like_widget, resolve_i64, validate_page_resource_shape, visit_form_node,
};

/// Reference chains that the import follows before reading a value as absent.
const MAX_REFERENCE_DEPTH: usize = 32;
/// Entries of one receiving `/DR` category that a rename may have to avoid.
const MAX_RESOURCE_CATEGORY_ENTRIES: usize = 4_096;
/// Objects and bytes that comparing one pair of default resources may read;
/// a larger pair counts as different.
const MAX_COMPARED_OBJECTS: usize = 4_096;
const MAX_COMPARED_BYTES: usize = 1024 * 1024;

fn allocation_error(error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!("failed to allocate AcroForm import: {error}"))
}

fn push<T>(values: &mut Vec<T>, value: T) -> PyResult<()> {
    values.try_reserve(1).map_err(allocation_error)?;
    values.push(value);
    Ok(())
}

fn insert<T: std::hash::Hash + Eq>(values: &mut HashSet<T>, value: T) -> PyResult<bool> {
    values.try_reserve(1).map_err(allocation_error)?;
    Ok(values.insert(value))
}

fn copy_bytes(value: &[u8]) -> PyResult<Vec<u8>> {
    let mut copied = Vec::new();
    copied
        .try_reserve_exact(value.len())
        .map_err(allocation_error)?;
    copied.extend_from_slice(value);
    Ok(copied)
}

fn copy_string(value: &str) -> PyResult<String> {
    let mut copied = String::new();
    copied
        .try_reserve_exact(value.len())
        .map_err(allocation_error)?;
    copied.push_str(value);
    Ok(copied)
}

/// A page-tree or catalog dictionary is never a field, whatever names it.
fn is_page_tree_or_catalog(dictionary: &Dictionary) -> bool {
    matches!(
        dictionary.get_type().ok(),
        Some(b"Page" | b"Pages" | b"Catalog")
    )
}

/// Resolve a value through at most 32 references.
///
/// Null, an indirect null, a reference to a missing object, and a cyclic or
/// over-long chain all read as absent.
fn resolve<'a>(doc: &'a Document, object: &'a Object) -> Option<&'a Object> {
    let mut current = object;
    for _ in 0..=MAX_REFERENCE_DEPTH {
        match current {
            Object::Reference(id) => current = doc.objects.get(id)?,
            Object::Null => return None,
            other => return Some(other),
        }
    }
    None
}

/// A dictionary or array of the receiving document: an indirect object and
/// the direct dictionary keys below it.
struct Location {
    id: ObjectId,
    path: Vec<Vec<u8>>,
}

impl Location {
    fn object<'a>(&self, doc: &'a Document) -> Option<&'a Object> {
        let mut object = doc.objects.get(&self.id)?;
        for key in &self.path {
            object = object.as_dict().ok()?.get(key).ok()?;
        }
        Some(object)
    }

    fn object_mut<'a>(&self, doc: &'a mut Document) -> Option<&'a mut Object> {
        let mut object = doc.objects.get_mut(&self.id)?;
        for key in &self.path {
            object = object.as_dict_mut().ok()?.get_mut(key).ok()?;
        }
        Some(object)
    }

    fn dictionary<'a>(&self, doc: &'a Document) -> Option<&'a Dictionary> {
        self.object(doc)?.as_dict().ok()
    }

    fn array<'a>(&self, doc: &'a Document) -> Option<&'a Vec<Object>> {
        self.object(doc)?.as_array().ok()
    }
}

/// Keys and values to add to one located receiving dictionary.
type DictionarySet = (Location, Vec<(Vec<u8>, Object)>);
/// Values to append to one located receiving array.
type ArrayAppend = (Location, Vec<Object>);

/// What one key of a located dictionary holds.
enum Slot {
    /// Absent, null, an indirect null, or a reference to a missing object.
    Absent,
    /// A dictionary or array, located through at most 32 references.
    Found(Location),
    /// Any other value, including a cyclic or over-long reference chain.
    Unusable,
}

fn slot(doc: &Document, parent: &Location, key: &[u8]) -> PyResult<Slot> {
    let Some(dictionary) = parent.dictionary(doc) else {
        return Ok(Slot::Unusable);
    };
    let Ok(value) = dictionary.get(key) else {
        return Ok(Slot::Absent);
    };
    match value {
        Object::Null => Ok(Slot::Absent),
        Object::Dictionary(_) | Object::Array(_) => {
            let mut path = Vec::new();
            path.try_reserve_exact(parent.path.len() + 1)
                .map_err(allocation_error)?;
            for step in &parent.path {
                path.push(copy_bytes(step)?);
            }
            path.push(copy_bytes(key)?);
            Ok(Slot::Found(Location {
                id: parent.id,
                path,
            }))
        }
        Object::Reference(id) => {
            let mut id = *id;
            for _ in 0..=MAX_REFERENCE_DEPTH {
                match doc.objects.get(&id) {
                    None | Some(Object::Null) => return Ok(Slot::Absent),
                    Some(Object::Reference(next)) => id = *next,
                    Some(Object::Dictionary(_) | Object::Array(_)) => {
                        return Ok(Slot::Found(Location {
                            id,
                            path: Vec::new(),
                        }));
                    }
                    Some(_) => return Ok(Slot::Unusable),
                }
            }
            Ok(Slot::Unusable)
        }
        _ => Ok(Slot::Unusable),
    }
}

/// Read a document-wide quadding as `field_quadding` applies it.
fn effective_quadding(doc: &Document, form: &Dictionary) -> i64 {
    inheritable_field_value(doc, form, b"Q")
        .and_then(|object| resolve_i64(doc, object))
        .filter(|value| (0..=2).contains(value))
        .unwrap_or(0)
}

fn is_true(doc: &Document, form: &Dictionary, key: &[u8]) -> bool {
    form.get(key)
        .ok()
        .and_then(|object| resolve(doc, object))
        .is_some_and(|object| matches!(object, Object::Boolean(true)))
}

/// Take the source interactive form dictionary out of the disposable copy.
///
/// The import builds the receiving dictionary itself, so the source one never
/// reaches the receiving document.
fn take_source_form(source: &mut Document) -> Option<Dictionary> {
    let catalog_id = source.trailer.get(b"Root").ok()?.as_reference().ok()?;
    let entry = source
        .get_object(catalog_id)
        .ok()?
        .as_dict()
        .ok()?
        .get(b"AcroForm")
        .ok()?;
    match entry {
        Object::Dictionary(_) => match source
            .get_object_mut(catalog_id)
            .ok()?
            .as_dict_mut()
            .ok()?
            .remove(b"AcroForm")
        {
            Some(Object::Dictionary(dictionary)) => Some(dictionary),
            _ => None,
        },
        Object::Reference(id) => {
            let mut id = *id;
            for _ in 0..=MAX_REFERENCE_DEPTH {
                match source.objects.get(&id)? {
                    Object::Reference(next) => id = *next,
                    Object::Dictionary(dictionary) if !is_page_tree_or_catalog(dictionary) => {
                        return match source.objects.remove(&id) {
                            Some(Object::Dictionary(dictionary)) => Some(dictionary),
                            _ => None,
                        };
                    }
                    _ => return None,
                }
            }
            None
        }
        _ => None,
    }
}

/// Read a dictionary as a field: one with a type, a partial name, kids, or a
/// widget subtype.
///
/// Some producers list every annotation in `/Fields`; pdf.js
/// `issue19281.pdf` lists 35,200 link annotations and no field. Such entries
/// are no fields, so the import neither registers nor walks them.
fn is_field_dictionary(dictionary: &Dictionary) -> bool {
    !is_page_tree_or_catalog(dictionary)
        && (dictionary.has(b"FT")
            || dictionary.has(b"T")
            || dictionary.has(b"Kids")
            || dictionary
                .get(b"Subtype")
                .and_then(Object::as_name)
                .is_ok_and(|subtype| subtype == b"Widget"))
}

/// Collect the distinct top-level field dictionaries of the source `/Fields`.
fn source_top_level_fields(source: &Document, form: &Dictionary) -> PyResult<Vec<ObjectId>> {
    let Some(Object::Array(entries)) = form
        .get(b"Fields")
        .ok()
        .and_then(|object| resolve(source, object))
    else {
        return Ok(Vec::new());
    };
    let mut ids = Vec::new();
    let mut seen = HashSet::new();
    for entry in entries {
        let Object::Reference(id) = entry else {
            continue;
        };
        let Ok(Object::Dictionary(dictionary)) = source.get_object(*id) else {
            continue;
        };
        if is_field_dictionary(dictionary) && insert(&mut seen, *id)? {
            push(&mut ids, *id)?;
        }
    }
    Ok(ids)
}

/// Where the annotations of a partial import's source pages are listed.
struct PageAnnotations<'a> {
    /// Each annotation that an imported page lists, and the first such page.
    imported: HashMap<ObjectId, ObjectId>,
    /// Every annotation that any source page lists.
    listed: HashSet<ObjectId>,
    imported_pages: &'a HashSet<ObjectId>,
    /// When pages are deleted in place, the deleted ones: an annotation that
    /// no page lists then stays unless its `/P` names one of them.
    deleted_pages: Option<HashSet<ObjectId>>,
}

impl<'a> PageAnnotations<'a> {
    fn read(
        source: &Document,
        source_pages: &[ObjectId],
        imported_pages: &'a HashSet<ObjectId>,
    ) -> PyResult<Self> {
        let mut imported = HashMap::new();
        let mut listed = HashSet::new();
        for &page_id in source_pages {
            let Some(page) = source
                .objects
                .get(&page_id)
                .and_then(|object| object.as_dict().ok())
            else {
                continue;
            };
            let Some(Object::Array(items)) = page
                .get(b"Annots")
                .ok()
                .and_then(|object| resolve(source, object))
            else {
                continue;
            };
            // Only the imported pages' arrays are bounded, as link import
            // bounds them; listing the others costs one pass over them.
            let is_imported = imported_pages.contains(&page_id);
            if is_imported && items.len() > MAX_PAGE_ANNOTATIONS {
                return Err(PdfError::new_err(format!(
                    "page annotations exceed the {MAX_PAGE_ANNOTATIONS}-entry safety limit"
                )));
            }
            listed.try_reserve(items.len()).map_err(allocation_error)?;
            if is_imported {
                imported
                    .try_reserve(items.len())
                    .map_err(allocation_error)?;
            }
            for item in items {
                if let Object::Reference(id) = item {
                    listed.insert(*id);
                    if is_imported {
                        imported.entry(*id).or_insert(page_id);
                    }
                }
            }
        }
        Ok(Self {
            imported,
            listed,
            imported_pages,
            deleted_pages: None,
        })
    }

    /// Read the annotations of a deletion, where `pages` lists every page
    /// before it and `retained` the pages that remain.
    fn read_deletion(
        doc: &Document,
        pages: &[ObjectId],
        retained: &'a HashSet<ObjectId>,
    ) -> PyResult<Self> {
        let mut annotations = Self::read(doc, pages, retained)?;
        let mut deleted = HashSet::new();
        for &page in pages {
            if !retained.contains(&page) {
                insert(&mut deleted, page)?;
            }
        }
        annotations.deleted_pages = Some(deleted);
        Ok(annotations)
    }

    /// An annotation is on the imported pages when one lists it in `/Annots`,
    /// or when no source page lists it and its `/P` names an imported page, as
    /// an invisible signature widget often does.
    fn contains(&self, source: &Document, id: ObjectId) -> bool {
        if self.imported.contains_key(&id) {
            return true;
        }
        if self.listed.contains(&id) {
            return false;
        }
        let page = source
            .get_object(id)
            .and_then(Object::as_dict)
            .and_then(|annotation| annotation.get(b"P"))
            .and_then(Object::as_reference);
        match &self.deleted_pages {
            Some(deleted) => !page.is_ok_and(|page| deleted.contains(&page)),
            None => page.is_ok_and(|page| self.imported_pages.contains(&page)),
        }
    }
}

/// Whether a field subtree holds a widget, and one that an imported page
/// lists, and whether the field's resolved type is a button.
#[derive(Clone, Copy, Default)]
struct Coverage {
    widgets: bool,
    imported: bool,
    button: bool,
}

/// One array of the disposable copy.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum ArrayLocation {
    /// The direct array under a key of the dictionary with this ID.
    Entry(ObjectId, &'static [u8]),
    /// An indirect array object.
    Object(ObjectId),
}

/// Locate and borrow the array that `key` of a source dictionary holds.
fn locate_array<'a>(
    source: &'a Document,
    dictionary_id: ObjectId,
    dictionary: &'a Dictionary,
    key: &'static [u8],
) -> Option<(ArrayLocation, &'a [Object])> {
    match dictionary.get(key).ok()? {
        Object::Array(items) => Some((ArrayLocation::Entry(dictionary_id, key), items)),
        object @ Object::Reference(_) => match source.dereference(object) {
            Ok((Some(id), Object::Array(items))) => Some((ArrayLocation::Object(id), items)),
            _ => None,
        },
        _ => None,
    }
}

/// The source field tree of a partial import, walked once under the limits
/// of `get_form_fields()`.
struct PartialTree<'a> {
    coverage: HashMap<ObjectId, Coverage>,
    annotations: PageAnnotations<'a>,
}

impl<'a> PartialTree<'a> {
    /// Walk every field below the top-level fields depth-first and record,
    /// after a node's kids, whether its subtree holds a widget and one that an
    /// imported page lists.
    ///
    /// The walk charges nodes, edges, widgets, and levels as
    /// `collect_form_fields` does, with one edge per top-level field, and
    /// resolves inherited field types the same way. A kid that leads back to a
    /// node still on the path contributes nothing.
    fn walk(
        source: &Document,
        top_level: &[ObjectId],
        annotations: PageAnnotations<'a>,
    ) -> PyResult<Self> {
        enum Frame {
            Enter(ObjectId, usize, bool),
            Exit(ObjectId, bool),
        }
        let mut stack = Vec::new();
        stack
            .try_reserve_exact(top_level.len())
            .map_err(allocation_error)?;
        for &id in top_level.iter().rev() {
            stack.push(Frame::Enter(id, 1, false));
        }
        let mut visited = HashSet::new();
        let mut coverage: HashMap<ObjectId, Coverage> = HashMap::new();
        let mut edges = 0usize;
        add_form_tree_edges(&mut edges, top_level.len())?;
        let mut widget_refs = 0usize;
        while let Some(frame) = stack.pop() {
            match frame {
                Frame::Enter(id, depth, inherited_button) => {
                    if !visit_form_node(&mut visited, id)? {
                        continue;
                    }
                    let Ok(dictionary) = source.get_object(id).and_then(Object::as_dict) else {
                        continue;
                    };
                    if is_page_tree_or_catalog(dictionary) {
                        continue;
                    }
                    let button = match dictionary.get(b"FT") {
                        Ok(object) => deref_object(source, object)
                            .as_name()
                            .map_or(inherited_button, |name| name == b"Btn"),
                        Err(_) => inherited_button,
                    };
                    push(&mut stack, Frame::Exit(id, button))?;
                    let Some(kids) = field_kids(source, dictionary) else {
                        continue;
                    };
                    add_form_tree_edges(&mut edges, kids.len())?;
                    for kid in kids.iter().rev() {
                        let kid = classify_field_kid(source, kid);
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
                        let (FieldKid::Field(kid_id) | FieldKid::Intermediate { id: kid_id, .. }) =
                            kid
                        else {
                            continue;
                        };
                        if depth >= MAX_FORM_FIELD_TREE_DEPTH {
                            return Err(PdfError::new_err(format!(
                                "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_DEPTH}-level safety limit"
                            )));
                        }
                        push(&mut stack, Frame::Enter(kid_id, depth + 1, button))?;
                    }
                }
                Frame::Exit(id, button) => {
                    let Ok(dictionary) = source.get_object(id).and_then(Object::as_dict) else {
                        continue;
                    };
                    let own = looks_like_widget(dictionary);
                    let mut covered = Coverage {
                        widgets: own,
                        imported: own && annotations.contains(source, id),
                        button,
                    };
                    for kid in field_kids(source, dictionary).unwrap_or_default() {
                        match classify_field_kid(source, kid) {
                            FieldKid::Widget(kid_id) => {
                                covered.widgets = true;
                                covered.imported |= annotations.contains(source, kid_id);
                            }
                            FieldKid::Field(kid_id) | FieldKid::Intermediate { id: kid_id, .. } => {
                                if let Some(kid) = coverage.get(&kid_id) {
                                    covered.widgets |= kid.widgets;
                                    covered.imported |= kid.imported;
                                }
                            }
                            FieldKid::Unusable | FieldKid::Ignored => {}
                        }
                    }
                    coverage.try_reserve(1).map_err(allocation_error)?;
                    coverage.insert(id, covered);
                }
            }
        }
        Ok(Self {
            coverage,
            annotations,
        })
    }

    fn imports(&self, id: ObjectId) -> bool {
        self.coverage
            .get(&id)
            .is_some_and(|coverage| coverage.imported)
    }

    /// Keep a widget that an imported page lists, a field subtree with such a
    /// widget or without any widget, and every entry that is no widget or
    /// field.
    fn keeps(&self, source: &Document, kid: &Object) -> bool {
        match classify_field_kid(source, kid) {
            FieldKid::Widget(id) => self.annotations.contains(source, id),
            FieldKid::Field(id) | FieldKid::Intermediate { id, .. } => self
                .coverage
                .get(&id)
                .is_none_or(|coverage| coverage.imported || !coverage.widgets),
            FieldKid::Unusable | FieldKid::Ignored => true,
        }
    }

    /// Plan the `/Kids` entries to remove from every walked field.
    ///
    /// Fields that are not imported are cleaned too, because a retained
    /// widget's `/Parent` can still reach one. A button field's own `/Opt`
    /// holds one export value per kid (ISO 32000-2 §12.7.5.2.4), so it loses
    /// the same entries when its length matches.
    fn removals(&self, source: &Document) -> PyResult<Vec<(ArrayLocation, Vec<usize>)>> {
        let mut removals = Vec::new();
        let mut planned = HashSet::new();
        for (&id, coverage) in &self.coverage {
            let reference = Object::Reference(id);
            let Ok((Some(dictionary_id), Object::Dictionary(dictionary))) =
                source.dereference(&reference)
            else {
                continue;
            };
            let Some((kids_location, kids)) =
                locate_array(source, dictionary_id, dictionary, b"Kids")
            else {
                continue;
            };
            let mut removed = Vec::new();
            for (index, kid) in kids.iter().enumerate() {
                if !self.keeps(source, kid) {
                    push(&mut removed, index)?;
                }
            }
            if removed.is_empty() {
                continue;
            }
            if coverage.button
                && let Some((options_location, options)) =
                    locate_array(source, dictionary_id, dictionary, b"Opt")
                && options.len() == kids.len()
                && insert(&mut planned, options_location)?
            {
                let mut copied = Vec::new();
                copied
                    .try_reserve_exact(removed.len())
                    .map_err(allocation_error)?;
                copied.extend_from_slice(&removed);
                push(&mut removals, (options_location, copied))?;
            }
            if insert(&mut planned, kids_location)? {
                push(&mut removals, (kids_location, removed))?;
            }
        }
        Ok(removals)
    }

    /// Collect the fields that the retained top-level fields reach through
    /// kept `/Kids` entries.
    fn retained_fields(
        &self,
        source: &Document,
        top_level: &[ObjectId],
    ) -> PyResult<HashSet<ObjectId>> {
        let mut retained = HashSet::new();
        let mut stack = Vec::new();
        stack
            .try_reserve_exact(top_level.len())
            .map_err(allocation_error)?;
        stack.extend_from_slice(top_level);
        while let Some(id) = stack.pop() {
            if !insert(&mut retained, id)? {
                continue;
            }
            let Ok(dictionary) = source.get_object(id).and_then(Object::as_dict) else {
                continue;
            };
            if is_page_tree_or_catalog(dictionary) {
                continue;
            }
            for kid in field_kids(source, dictionary).unwrap_or_default() {
                if let FieldKid::Field(kid_id) | FieldKid::Intermediate { id: kid_id, .. } =
                    classify_field_kid(source, kid)
                    && self.keeps(source, kid)
                {
                    push(&mut stack, kid_id)?;
                }
            }
        }
        Ok(retained)
    }

    /// Point every retained widget whose `/P` names no imported page at the
    /// imported page that lists it.
    fn page_fixes(
        &self,
        source: &Document,
        retained: &HashSet<ObjectId>,
    ) -> PyResult<Vec<(ObjectId, ObjectId)>> {
        let imported_pages = self.annotations.imported_pages;
        let mut fixes = Vec::new();
        let mut fix = |id: ObjectId| -> PyResult<()> {
            let Some(&page_id) = self.annotations.imported.get(&id) else {
                return Ok(());
            };
            let Ok(widget) = source.get_object(id).and_then(Object::as_dict) else {
                return Ok(());
            };
            let stale = match widget.get(b"P") {
                Ok(Object::Reference(page)) => !imported_pages.contains(page),
                Ok(_) => true,
                Err(_) => false,
            };
            if stale {
                push(&mut fixes, (id, page_id))?;
            }
            Ok(())
        };
        for &id in retained {
            let Ok(dictionary) = source.get_object(id).and_then(Object::as_dict) else {
                continue;
            };
            if is_page_tree_or_catalog(dictionary) {
                continue;
            }
            if looks_like_widget(dictionary) {
                fix(id)?;
            }
            for kid in field_kids(source, dictionary).unwrap_or_default() {
                if let FieldKid::Widget(kid_id) = classify_field_kid(source, kid) {
                    fix(kid_id)?;
                }
            }
        }
        fixes.sort_unstable();
        fixes.dedup();
        Ok(fixes)
    }
}

/// Remove the entries at the sorted `removed` indices.
fn filter(items: &mut Vec<Object>, removed: &[usize]) {
    let mut index = 0;
    items.retain(|_| {
        let keep = removed.binary_search(&index).is_err();
        index += 1;
        keep
    });
}

/// The receiving interactive form dictionary as the import finds it.
struct Receiving {
    catalog: ObjectId,
    form: Option<ReceivingForm>,
}

struct ReceivingForm {
    location: Location,
    fields: Option<Location>,
    names: HashSet<String>,
    resources: Option<Location>,
    appearance: Option<Vec<u8>>,
    quadding: i64,
    need_appearances: bool,
    calculation_order: Option<Location>,
}

impl Receiving {
    fn read(target: &Document) -> PyResult<Self> {
        let catalog = target
            .trailer
            .get(b"Root")
            .and_then(Object::as_reference)
            .map_err(|_| PdfError::new_err("receiving document has no catalog"))?;
        let catalog_location = Location {
            id: catalog,
            path: Vec::new(),
        };
        if catalog_location.dictionary(target).is_none() {
            return Err(PdfError::new_err(
                "receiving document catalog is not a dictionary",
            ));
        }
        let location = match slot(target, &catalog_location, b"AcroForm")? {
            Slot::Found(location) if location.dictionary(target).is_some() => location,
            _ => {
                return Ok(Self {
                    catalog,
                    form: None,
                });
            }
        };
        let form = location
            .dictionary(target)
            .expect("the receiving form was located as a dictionary");
        let fields = match slot(target, &location, b"Fields")? {
            Slot::Found(fields) if fields.array(target).is_some() => Some(fields),
            _ => None,
        };
        let mut names = HashSet::new();
        if let Some(items) = fields.as_ref().and_then(|fields| fields.array(target)) {
            let mut encoded = 0usize;
            let mut decoded = 0usize;
            for item in items {
                let Object::Reference(id) = item else {
                    continue;
                };
                let Ok(field) = target.get_object(*id).and_then(Object::as_dict) else {
                    continue;
                };
                let Ok(partial) = field.get(b"T") else {
                    continue;
                };
                if let Some(name) = bounded_form_text(
                    target,
                    partial,
                    &mut encoded,
                    &mut decoded,
                    MAX_FORM_FIELD_NAME_BYTES,
                    "field-name",
                )? && !name.is_empty()
                {
                    insert(&mut names, name)?;
                }
            }
        }
        let resources = match slot(target, &location, b"DR")? {
            Slot::Found(resources) if resources.dictionary(target).is_some() => Some(resources),
            _ => None,
        };
        let appearance = match form
            .get(b"DA")
            .ok()
            .and_then(|object| resolve(target, object))
        {
            Some(Object::String(bytes, _)) => Some(copy_bytes(bytes)?),
            _ => None,
        };
        let calculation_order = match slot(target, &location, b"CO")? {
            Slot::Found(order) if order.array(target).is_some() => Some(order),
            _ => None,
        };
        Ok(Self {
            catalog,
            form: Some(ReceivingForm {
                quadding: effective_quadding(target, form),
                need_appearances: is_true(target, form, b"NeedAppearances"),
                location,
                fields,
                names,
                resources,
                appearance,
                calculation_order,
            }),
        })
    }
}

/// Resource names that imported default appearance strings must follow,
/// keyed by `/DR` category and then by the source name.
type ResourceRenames = HashMap<Vec<u8>, HashMap<Vec<u8>, Vec<u8>>>;

/// Plan the source default resources that join an existing receiving `/DR`.
///
/// A source resource that equals the receiving resource of its name is
/// shared. Any other under a name that the receiving category already holds
/// gains the smallest `_N` suffix that neither category uses. A receiving
/// category that is no dictionary keeps its value.
fn merge_default_resources(
    target: &Document,
    resources: &Location,
    source: &Document,
    source_resources: &Dictionary,
    sets: &mut Vec<DictionarySet>,
) -> PyResult<ResourceRenames> {
    validate_page_resource_shape(source_resources, "imported AcroForm /DR")?;
    let mut renames = ResourceRenames::new();
    let mut new_categories = Vec::new();
    for (category, value) in source_resources.iter() {
        let Some(source_category) = resolve(source, value) else {
            continue;
        };
        let location = match slot(target, resources, category)? {
            Slot::Absent => {
                push(&mut new_categories, (copy_bytes(category)?, value.clone()))?;
                continue;
            }
            Slot::Found(location) => location,
            Slot::Unusable => continue,
        };
        let (Some(receiving), Object::Dictionary(source_category)) =
            (location.dictionary(target), source_category)
        else {
            continue;
        };
        if matches!(value, Object::Reference(_)) {
            validate_page_resource_shape(source_category, "imported AcroForm /DR category")?;
        }
        if receiving.len() > MAX_RESOURCE_CATEGORY_ENTRIES {
            return Err(PdfError::new_err(format!(
                "AcroForm /DR category exceeds the {MAX_RESOURCE_CATEGORY_ENTRIES}-entry safety limit"
            )));
        }
        let mut entries = Vec::new();
        entries
            .try_reserve_exact(source_category.len())
            .map_err(allocation_error)?;
        let mut assigned = HashSet::new();
        let mut category_renames = HashMap::new();
        for (name, entry) in source_category.iter() {
            if is_null_equivalent(source, entry) {
                continue;
            }
            let occupied = match receiving.get(name) {
                Ok(existing) if !is_null_equivalent(target, existing) => Some(existing),
                _ => None,
            };
            if let Some(existing) = occupied
                && equivalent_resources(target, existing, source, entry)?
            {
                // The receiving resource already is this one, so imported
                // default appearance strings can keep naming it.
                continue;
            }
            let key = if occupied.is_some() {
                let renamed = unique_resource_name(name, receiving, source_category, &assigned)?;
                insert(&mut assigned, copy_bytes(&renamed)?)?;
                category_renames.try_reserve(1).map_err(allocation_error)?;
                category_renames.insert(copy_bytes(name)?, copy_bytes(&renamed)?);
                renamed
            } else {
                copy_bytes(name)?
            };
            entries.push((key, entry.clone()));
        }
        if !category_renames.is_empty() {
            renames.try_reserve(1).map_err(allocation_error)?;
            renames.insert(copy_bytes(category)?, category_renames);
        }
        if !entries.is_empty() {
            push(sets, (location, entries))?;
        }
    }
    if !new_categories.is_empty() {
        let location = Location {
            id: resources.id,
            path: copy_path(&resources.path)?,
        };
        push(sets, (location, new_categories))?;
    }
    Ok(renames)
}

/// Compare a receiving and a source default resource structurally.
///
/// References are followed on both sides, a pair of references that is
/// already under comparison counts as equal, and streams compare their
/// dictionaries and raw data. Reading more than 4,096 objects or 1 MiB counts
/// as different, so an expensive pair is renamed rather than shared.
fn equivalent_resources(
    target: &Document,
    receiving: &Object,
    source: &Document,
    imported: &Object,
) -> PyResult<bool> {
    let mut pending: Vec<(&Object, &Object)> = Vec::new();
    push(&mut pending, (receiving, imported))?;
    let mut pairs = HashSet::new();
    let mut objects = 0usize;
    let mut bytes = 0usize;
    let mut charge = |amount: usize| {
        bytes = bytes.saturating_add(amount);
        bytes <= MAX_COMPARED_BYTES
    };
    while let Some((mut left, mut right)) = pending.pop() {
        objects += 1;
        if objects > MAX_COMPARED_OBJECTS {
            return Ok(false);
        }
        if let (Object::Reference(left_id), Object::Reference(right_id)) = (left, right)
            && !insert(&mut pairs, (*left_id, *right_id))?
        {
            continue;
        }
        let (Some(resolved_left), Some(resolved_right)) =
            (resolve(target, left), resolve(source, right))
        else {
            if resolve(target, left).is_none() && resolve(source, right).is_none() {
                continue;
            }
            return Ok(false);
        };
        left = resolved_left;
        right = resolved_right;
        let equal = match (left, right) {
            (Object::Boolean(left), Object::Boolean(right)) => left == right,
            (Object::Integer(left), Object::Integer(right)) => left == right,
            (Object::Real(left), Object::Real(right)) => left.to_bits() == right.to_bits(),
            (Object::Name(left), Object::Name(right))
            | (Object::String(left, _), Object::String(right, _)) => {
                charge(left.len()) && left == right
            }
            (Object::Array(left), Object::Array(right)) => {
                if left.len() != right.len() || !fits(objects, &pending, left.len()) {
                    return Ok(false);
                }
                pending.try_reserve(left.len()).map_err(allocation_error)?;
                pending.extend(left.iter().zip(right));
                true
            }
            (Object::Dictionary(left), Object::Dictionary(right)) => {
                fits(objects, &pending, left.len())
                    && compare_dictionaries(left, right, &mut pending, &mut charge)?
            }
            (Object::Stream(left), Object::Stream(right)) => {
                fits(objects, &pending, left.dict.len())
                    && charge(left.content.len())
                    && left.content == right.content
                    && compare_dictionaries(&left.dict, &right.dict, &mut pending, &mut charge)?
            }
            _ => false,
        };
        if !equal {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Whether `more` queued pairs keep the comparison within its object budget.
fn fits(compared: usize, pending: &[(&Object, &Object)], more: usize) -> bool {
    compared.saturating_add(pending.len()).saturating_add(more) <= MAX_COMPARED_OBJECTS
}

/// Queue the entry pairs of two dictionaries with the same keys.
fn compare_dictionaries<'a>(
    left: &'a Dictionary,
    right: &'a Dictionary,
    pending: &mut Vec<(&'a Object, &'a Object)>,
    charge: &mut impl FnMut(usize) -> bool,
) -> PyResult<bool> {
    if left.len() != right.len() {
        return Ok(false);
    }
    pending.try_reserve(left.len()).map_err(allocation_error)?;
    for (key, value) in left.iter() {
        let Ok(other) = right.get(key) else {
            return Ok(false);
        };
        if !charge(key.len()) {
            return Ok(false);
        }
        pending.push((value, other));
    }
    Ok(true)
}

fn copy_path(path: &[Vec<u8>]) -> PyResult<Vec<Vec<u8>>> {
    let mut copied = Vec::new();
    copied
        .try_reserve_exact(path.len())
        .map_err(allocation_error)?;
    for step in path {
        copied.push(copy_bytes(step)?);
    }
    Ok(copied)
}

/// Return `name` followed by `_` and the smallest positive integer that no
/// receiving or source entry and no earlier rename of this category uses.
fn unique_resource_name(
    name: &[u8],
    receiving: &Dictionary,
    source: &Dictionary,
    assigned: &HashSet<Vec<u8>>,
) -> PyResult<Vec<u8>> {
    let mut candidate = Vec::new();
    for suffix in 1usize.. {
        let digits = suffix.to_string();
        candidate.clear();
        candidate
            .try_reserve(name.len() + 1 + digits.len())
            .map_err(allocation_error)?;
        candidate.extend_from_slice(name);
        candidate.push(b'_');
        candidate.extend_from_slice(digits.as_bytes());
        if !receiving.has(&candidate) && !source.has(&candidate) && !assigned.contains(&candidate) {
            return Ok(candidate);
        }
    }
    unreachable!("a category holds fewer names than there are integers")
}

const fn is_whitespace(byte: u8) -> bool {
    matches!(byte, b'\0' | b'\t' | b'\n' | b'\x0c' | b'\r' | b' ')
}

const fn is_delimiter(byte: u8) -> bool {
    matches!(
        byte,
        b'(' | b')' | b'<' | b'>' | b'[' | b']' | b'{' | b'}' | b'/' | b'%'
    )
}

const fn is_regular(byte: u8) -> bool {
    !is_whitespace(byte) && !is_delimiter(byte)
}

/// Decode the `#xx` escapes of a name token without its leading slash.
fn decode_name(token: &[u8]) -> PyResult<Vec<u8>> {
    let mut decoded = Vec::new();
    decoded
        .try_reserve_exact(token.len())
        .map_err(allocation_error)?;
    let mut index = 0;
    while index < token.len() {
        let byte = token[index];
        if byte == b'#'
            && let Some(hex) = token.get(index + 1..index + 3)
            && hex.iter().all(u8::is_ascii_hexdigit)
            && let Ok(hex) = std::str::from_utf8(hex)
            && let Ok(value) = u8::from_str_radix(hex, 16)
        {
            decoded.push(value);
            index += 3;
        } else {
            decoded.push(byte);
            index += 1;
        }
    }
    Ok(decoded)
}

/// Append `name` as a name token, escaping the bytes that lopdf escapes.
fn encode_name(out: &mut Vec<u8>, name: &[u8]) {
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    out.push(b'/');
    for &byte in name {
        if byte == b'#' || !is_regular(byte) || !(b'!'..=b'~').contains(&byte) {
            out.extend_from_slice(&[
                b'#',
                HEX[usize::from(byte >> 4)],
                HEX[usize::from(byte & 0x0f)],
            ]);
        } else {
            out.push(byte);
        }
    }
}

/// Upper bound of one encoded name token.
fn encoded_name_len(name: &[u8]) -> usize {
    1 + name.len() * 3
}

/// Rename the resources that a default appearance string names.
///
/// The string is a content-stream fragment (ISO 32000-2 §12.7.4.3). Only an
/// operand that names a `/DR` resource changes: the font of `Tf`, the graphics
/// state of `gs`, the colour space of `cs` and `CS`, and the pattern of `scn`
/// and `SCN`. Every other byte is copied, and `None` means nothing changed.
fn rewrite_default_appearance(
    appearance: &[u8],
    renames: &ResourceRenames,
) -> PyResult<Option<Vec<u8>>> {
    #[derive(Clone, Copy)]
    enum Operand {
        Name(usize, usize),
        Other,
    }
    fn operand(value: Operand, last: &mut Option<Operand>, before_last: &mut Option<Operand>) {
        *before_last = *last;
        *last = Some(value);
    }
    let mut replacements: Vec<(usize, usize, &[u8])> = Vec::new();
    let mut last: Option<Operand> = None;
    let mut before_last: Option<Operand> = None;
    let mut index = 0;
    let len = appearance.len();
    while index < len {
        let byte = appearance[index];
        if is_whitespace(byte) {
            index += 1;
            continue;
        }
        match byte {
            b'%' => {
                while index < len && !matches!(appearance[index], b'\r' | b'\n') {
                    index += 1;
                }
            }
            b'/' => {
                let start = index;
                index += 1;
                while index < len && is_regular(appearance[index]) {
                    index += 1;
                }
                operand(Operand::Name(start, index), &mut last, &mut before_last);
            }
            b'(' => {
                let mut depth = 0usize;
                while index < len {
                    match appearance[index] {
                        b'\\' => index += 1,
                        b'(' => depth += 1,
                        b')' => {
                            depth -= 1;
                            if depth == 0 {
                                index += 1;
                                break;
                            }
                        }
                        _ => {}
                    }
                    index += 1;
                }
                operand(Operand::Other, &mut last, &mut before_last);
            }
            b'<' if appearance.get(index + 1) == Some(&b'<') => {
                index += 2;
                operand(Operand::Other, &mut last, &mut before_last);
            }
            b'<' => {
                while index < len && appearance[index] != b'>' {
                    index += 1;
                }
                index += 1;
                operand(Operand::Other, &mut last, &mut before_last);
            }
            b'>' | b')' | b'[' | b']' | b'{' | b'}' => {
                index += if appearance.get(index..index + 2) == Some(b">>") {
                    2
                } else {
                    1
                };
                operand(Operand::Other, &mut last, &mut before_last);
            }
            _ => {
                let start = index;
                while index < len && is_regular(appearance[index]) {
                    index += 1;
                }
                let token = &appearance[start..index];
                if matches!(token.first(), Some(b'0'..=b'9' | b'+' | b'-' | b'.'))
                    || matches!(token, b"true" | b"false" | b"null")
                {
                    operand(Operand::Other, &mut last, &mut before_last);
                    continue;
                }
                let named = match token {
                    b"Tf" => before_last.map(|operand| (b"Font".as_slice(), operand)),
                    b"gs" => last.map(|operand| (b"ExtGState".as_slice(), operand)),
                    b"cs" | b"CS" => last.map(|operand| (b"ColorSpace".as_slice(), operand)),
                    b"scn" | b"SCN" => last.map(|operand| (b"Pattern".as_slice(), operand)),
                    _ => None,
                };
                if let Some((category, Operand::Name(start, end))) = named {
                    let name = decode_name(&appearance[start + 1..end])?;
                    let device = category == b"ColorSpace"
                        && matches!(
                            name.as_slice(),
                            b"DeviceGray" | b"DeviceRGB" | b"DeviceCMYK" | b"Pattern"
                        );
                    if !device
                        && let Some(renamed) = renames
                            .get(category)
                            .and_then(|category| category.get(&name))
                    {
                        push(&mut replacements, (start, end, renamed.as_slice()))?;
                    }
                }
                last = None;
                before_last = None;
            }
        }
    }
    if replacements.is_empty() {
        return Ok(None);
    }
    let size = replacements
        .iter()
        .try_fold(appearance.len(), |size, (start, end, renamed)| {
            size.checked_add(encoded_name_len(renamed))
                .map(|size| size - (end - start))
        })
        .ok_or_else(|| {
            PdfError::new_err("default appearance string exceeds the platform size limit")
        })?;
    let mut rewritten = Vec::new();
    rewritten
        .try_reserve_exact(size)
        .map_err(allocation_error)?;
    let mut copied = 0;
    for (start, end, renamed) in replacements {
        rewritten.extend_from_slice(&appearance[copied..start]);
        encode_name(&mut rewritten, renamed);
        copied = end;
    }
    rewritten.extend_from_slice(&appearance[copied..]);
    Ok(Some(rewritten))
}

/// Rewrite one default appearance string value. A UTF-16 string is read as
/// its text when that text is ASCII, and a rewrite of it is plain ASCII.
fn rewrite_string(bytes: &[u8], renames: &ResourceRenames) -> PyResult<Option<Vec<u8>>> {
    if bytes.starts_with(&[0xfe, 0xff]) {
        let Ok(text) =
            decode_text_string(&Object::String(copy_bytes(bytes)?, StringFormat::Literal))
        else {
            return Ok(None);
        };
        if !text.is_ascii() {
            return Ok(None);
        }
        return rewrite_default_appearance(text.as_bytes(), renames);
    }
    rewrite_default_appearance(bytes, renames)
}

/// One change to the disposable source copy.
enum SourceEdit {
    /// Replace a dictionary's partial name, default appearance, quadding, or
    /// page entry.
    Entry(ObjectId, &'static str, Object),
    /// Replace an indirect default appearance string.
    String(ObjectId, Vec<u8>),
    /// Remove the entries at sorted indices from one array.
    Remove(ArrayLocation, Vec<usize>),
}

/// Plan the rewrite of every default appearance string in the source copy.
///
/// Fields, widgets, and free text annotations all name `/DR` resources from
/// `/DA`, so every dictionary is visited once.
fn plan_appearance_rewrites(
    source: &Document,
    renames: &ResourceRenames,
    edits: &mut Vec<SourceEdit>,
) -> PyResult<()> {
    let mut strings = HashSet::new();
    for (&id, object) in &source.objects {
        let dictionary = match object {
            Object::Dictionary(dictionary) => dictionary,
            Object::Stream(stream) => &stream.dict,
            _ => continue,
        };
        if is_page_tree_or_catalog(dictionary) {
            continue;
        }
        match dictionary.get(b"DA") {
            Ok(Object::String(bytes, _)) => {
                if let Some(rewritten) = rewrite_string(bytes, renames)? {
                    push(
                        edits,
                        SourceEdit::Entry(
                            id,
                            "DA",
                            Object::String(rewritten, StringFormat::Literal),
                        ),
                    )?;
                }
            }
            Ok(reference @ Object::Reference(_)) => {
                if let Ok((Some(string_id), Object::String(bytes, _))) =
                    source.dereference(reference)
                    && insert(&mut strings, string_id)?
                    && let Some(rewritten) = rewrite_string(bytes, renames)?
                {
                    push(edits, SourceEdit::String(string_id, rewritten))?;
                }
            }
            _ => {}
        }
    }
    Ok(())
}

/// Append `suffix` to a partial field name, keeping its encoding.
fn renamed_partial_name(partial: &Object, renamed: &str, suffix: &str) -> PyResult<Object> {
    let Object::String(bytes, format) = partial else {
        return Ok(form_text_string(renamed));
    };
    let utf16 = bytes.starts_with(&[0xfe, 0xff]);
    if utf16 && bytes.len() % 2 != 0 {
        return Ok(form_text_string(renamed));
    }
    let mut encoded = Vec::new();
    encoded
        .try_reserve_exact(bytes.len() + suffix.len() * if utf16 { 2 } else { 1 })
        .map_err(allocation_error)?;
    encoded.extend_from_slice(bytes);
    for byte in suffix.bytes() {
        if utf16 {
            encoded.push(0);
        }
        encoded.push(byte);
    }
    Ok(Object::String(encoded, *format))
}

/// Give each imported top-level partial name that the receiving document
/// already uses the smallest `+N` suffix that no receiving or imported
/// top-level name uses; every field with the same name shares one suffix.
fn plan_field_renames(
    source: &Document,
    retained: &[ObjectId],
    receiving: &HashSet<String>,
    edits: &mut Vec<SourceEdit>,
) -> PyResult<()> {
    let mut encoded = 0usize;
    let mut decoded = 0usize;
    let mut imported = Vec::new();
    for &id in retained {
        let Ok(field) = source.get_object(id).and_then(Object::as_dict) else {
            continue;
        };
        let Ok(partial) = field.get(b"T") else {
            continue;
        };
        if let Some(name) = bounded_form_text(
            source,
            partial,
            &mut encoded,
            &mut decoded,
            MAX_FORM_FIELD_NAME_BYTES,
            "field-name",
        )? && !name.is_empty()
        {
            push(&mut imported, (id, name))?;
        }
    }
    if !imported.iter().any(|(_, name)| receiving.contains(name)) {
        return Ok(());
    }
    let mut reserved: HashSet<&str> = HashSet::new();
    reserved
        .try_reserve(receiving.len() + imported.len())
        .map_err(allocation_error)?;
    reserved.extend(receiving.iter().map(String::as_str));
    reserved.extend(imported.iter().map(|(_, name)| name.as_str()));
    let mut assigned: HashSet<String> = HashSet::new();
    let mut renamed: HashMap<&str, String> = HashMap::new();
    for (id, name) in &imported {
        if !receiving.contains(name) {
            continue;
        }
        let new_name = match renamed.get(name.as_str()) {
            Some(new_name) => copy_string(new_name)?,
            None => {
                let mut candidate = String::new();
                for suffix in 1usize.. {
                    let digits = suffix.to_string();
                    candidate.clear();
                    candidate
                        .try_reserve(name.len() + 1 + digits.len())
                        .map_err(allocation_error)?;
                    candidate.push_str(name);
                    candidate.push('+');
                    candidate.push_str(&digits);
                    if !reserved.contains(candidate.as_str()) && !assigned.contains(&candidate) {
                        break;
                    }
                }
                insert(&mut assigned, copy_string(&candidate)?)?;
                renamed.try_reserve(1).map_err(allocation_error)?;
                renamed.insert(name.as_str(), copy_string(&candidate)?);
                candidate
            }
        };
        let partial = source
            .get_object(*id)
            .and_then(Object::as_dict)
            .ok()
            .and_then(|field| field.get(b"T").ok())
            .and_then(|partial| resolve(source, partial));
        let Some(partial) = partial else {
            continue;
        };
        let suffix = &new_name[name.len()..];
        push(
            edits,
            SourceEdit::Entry(*id, "T", renamed_partial_name(partial, &new_name, suffix)?),
        )?;
    }
    Ok(())
}

/// Apply planned edits to the disposable source copy.
fn apply_source_edits(source: &mut Document, edits: Vec<SourceEdit>) -> PyResult<()> {
    for edit in edits {
        match edit {
            SourceEdit::Entry(id, key, value) => {
                let Ok(dictionary) = source.get_object_mut(id).and_then(|object| match object {
                    Object::Stream(stream) => Ok(&mut stream.dict),
                    other => other.as_dict_mut(),
                }) else {
                    continue;
                };
                dictionary
                    .as_hashmap_mut()
                    .try_reserve(1)
                    .map_err(allocation_error)?;
                dictionary.set(key, value);
            }
            SourceEdit::String(id, bytes) => {
                if let Some(object) = source.objects.get_mut(&id) {
                    *object = Object::String(bytes, StringFormat::Literal);
                }
            }
            SourceEdit::Remove(array, removed) => {
                let items = match array {
                    ArrayLocation::Entry(id, key) => source
                        .objects
                        .get_mut(&id)
                        .and_then(|object| object.as_dict_mut().ok())
                        .and_then(|dictionary| dictionary.get_mut(key).ok())
                        .and_then(|items| items.as_array_mut().ok()),
                    ArrayLocation::Object(id) => source
                        .objects
                        .get_mut(&id)
                        .and_then(|object| object.as_array_mut().ok()),
                };
                if let Some(items) = items {
                    filter(items, &removed);
                }
            }
        }
    }
    Ok(())
}

/// The receiving-side commit, prepared and reserved before any receiving
/// content changes.
pub(crate) struct AcroFormImport {
    commit: Commit,
}

enum Commit {
    /// Link a new interactive form dictionary from the catalog.
    Create { catalog: ObjectId, form: Dictionary },
    /// Add keys to located dictionaries and values to located arrays.
    Extend {
        sets: Vec<DictionarySet>,
        appends: Vec<ArrayAppend>,
    },
}

impl AcroFormImport {
    /// Plan the registration of the source fields that the imported pages
    /// use, editing the disposable `source` copy in place.
    ///
    /// `source_pages` lists every page of `source` and `imported_pages` the
    /// imported ones; `complete` says that every source page is imported.
    /// Returns `None` when no field joins the receiving document.
    pub(crate) fn prepare(
        target: &Document,
        source: &mut Document,
        source_pages: &[ObjectId],
        imported_pages: &HashSet<ObjectId>,
        complete: bool,
    ) -> PyResult<Option<Self>> {
        let Some(mut source_form) = take_source_form(source) else {
            return Ok(None);
        };
        let top_level = source_top_level_fields(source, &source_form)?;
        if top_level.is_empty() {
            return Ok(None);
        }
        let mut edits = Vec::new();
        let (retained, retained_fields) = if complete {
            (top_level, None)
        } else {
            let annotations = PageAnnotations::read(source, source_pages, imported_pages)?;
            let tree = PartialTree::walk(source, &top_level, annotations)?;
            let mut retained = Vec::new();
            for &id in &top_level {
                if tree.imports(id) {
                    push(&mut retained, id)?;
                }
            }
            if retained.is_empty() {
                return Ok(None);
            }
            for (array, removed) in tree.removals(source)? {
                push(&mut edits, SourceEdit::Remove(array, removed))?;
            }
            let fields = tree.retained_fields(source, &retained)?;
            for (widget, page) in tree.page_fixes(source, &fields)? {
                push(
                    &mut edits,
                    SourceEdit::Entry(widget, "P", Object::Reference(page)),
                )?;
            }
            (retained, Some(fields))
        };

        let receiving = Receiving::read(target)?;

        let calculation_order = match source_form
            .get(b"CO")
            .ok()
            .and_then(|object| resolve(source, object))
        {
            Some(Object::Array(entries)) => {
                let mut order = Vec::new();
                for entry in entries {
                    if let Object::Reference(id) = entry
                        && retained_fields
                            .as_ref()
                            .is_none_or(|fields| fields.contains(id))
                    {
                        push(&mut order, entry.clone())?;
                    }
                }
                order
            }
            _ => Vec::new(),
        };
        let need_appearances = is_true(source, &source_form, b"NeedAppearances");
        let mut references = Vec::new();
        references
            .try_reserve_exact(retained.len())
            .map_err(allocation_error)?;
        references.extend(retained.iter().copied().map(Object::Reference));

        let Some(form) = receiving.form else {
            let mut created = Dictionary::new();
            created
                .as_hashmap_mut()
                .try_reserve(6)
                .map_err(allocation_error)?;
            created.set("Fields", Object::Array(references));
            for key in ["DR", "DA", "Q"] {
                if let Some(value) = source_form.remove(key.as_bytes())
                    && !is_null_equivalent(source, &value)
                {
                    created.set(key, value);
                }
            }
            if need_appearances {
                created.set("NeedAppearances", true);
            }
            if !calculation_order.is_empty() {
                created.set("CO", Object::Array(calculation_order));
            }
            apply_source_edits(source, edits)?;
            return Ok(Some(Self {
                commit: Commit::Create {
                    catalog: receiving.catalog,
                    form: created,
                },
            }));
        };

        plan_field_renames(source, &retained, &form.names, &mut edits)?;
        let mut sets = Vec::new();
        let mut appends = Vec::new();
        let mut form_entries = Vec::new();
        let source_resources = source_form
            .remove(b"DR")
            .filter(|value| !is_null_equivalent(source, value));
        let mut renames = ResourceRenames::new();
        if let Some(source_resources) = source_resources {
            match &form.resources {
                Some(resources) => {
                    if let Some(Object::Dictionary(source_resources)) =
                        resolve(source, &source_resources)
                    {
                        renames = merge_default_resources(
                            target,
                            resources,
                            source,
                            source_resources,
                            &mut sets,
                        )?;
                    }
                }
                None => push(&mut form_entries, (copy_bytes(b"DR")?, source_resources))?,
            }
        }
        if !renames.is_empty() {
            plan_appearance_rewrites(source, &renames, &mut edits)?;
        }

        let source_appearance = match source_form
            .get(b"DA")
            .ok()
            .and_then(|object| resolve(source, object))
        {
            Some(Object::String(bytes, _)) => Some(match rewrite_string(bytes, &renames)? {
                Some(rewritten) => rewritten,
                None => copy_bytes(bytes)?,
            }),
            _ => None,
        };
        let source_quadding = effective_quadding(source, &source_form);
        for &id in &retained {
            let Ok(field) = source.get_object(id).and_then(Object::as_dict) else {
                continue;
            };
            if let Some(appearance) = &source_appearance
                && form.appearance.as_ref() != Some(appearance)
                && inheritable_field_value(source, field, b"DA").is_none()
            {
                push(
                    &mut edits,
                    SourceEdit::Entry(
                        id,
                        "DA",
                        Object::String(copy_bytes(appearance)?, StringFormat::Literal),
                    ),
                )?;
            }
            if source_quadding != form.quadding
                && inheritable_field_value(source, field, b"Q").is_none()
            {
                push(
                    &mut edits,
                    SourceEdit::Entry(id, "Q", Object::Integer(source_quadding)),
                )?;
            }
        }

        match form.fields {
            Some(fields) => push(&mut appends, (fields, references))?,
            None => push(
                &mut form_entries,
                (copy_bytes(b"Fields")?, Object::Array(references)),
            )?,
        }
        if !calculation_order.is_empty() {
            match form.calculation_order {
                Some(order) => push(&mut appends, (order, calculation_order))?,
                None => push(
                    &mut form_entries,
                    (copy_bytes(b"CO")?, Object::Array(calculation_order)),
                )?,
            }
        }
        if need_appearances && !form.need_appearances {
            push(
                &mut form_entries,
                (copy_bytes(b"NeedAppearances")?, Object::Boolean(true)),
            )?;
        }
        if !form_entries.is_empty() {
            push(&mut sets, (form.location, form_entries))?;
        }
        apply_source_edits(source, edits)?;
        Ok(Some(Self {
            commit: Commit::Extend { sets, appends },
        }))
    }

    /// Reserve receiving capacity for the commit; content stays unchanged.
    pub(crate) fn reserve(&self, target: &mut Document) -> PyResult<()> {
        let unavailable =
            || PdfError::new_err("receiving AcroForm changed before the import was committed");
        match &self.commit {
            Commit::Create { catalog, .. } => target
                .objects
                .get_mut(catalog)
                .and_then(|object| object.as_dict_mut().ok())
                .ok_or_else(unavailable)?
                .as_hashmap_mut()
                .try_reserve(1)
                .map_err(allocation_error),
            Commit::Extend { sets, appends } => {
                for (location, entries) in sets {
                    location
                        .object_mut(target)
                        .and_then(|object| object.as_dict_mut().ok())
                        .ok_or_else(unavailable)?
                        .as_hashmap_mut()
                        .try_reserve(entries.len())
                        .map_err(allocation_error)?;
                }
                for (location, values) in appends {
                    location
                        .object_mut(target)
                        .and_then(|object| object.as_array_mut().ok())
                        .ok_or_else(unavailable)?
                        .try_reserve(values.len())
                        .map_err(allocation_error)?;
                }
                Ok(())
            }
        }
    }

    /// Move the prepared values into the reserved receiving space.
    pub(crate) fn apply(self, target: &mut Document) {
        match self.commit {
            Commit::Create { catalog, form } => {
                let form_id = target.add_object(Object::Dictionary(form));
                if let Some(catalog) = target
                    .objects
                    .get_mut(&catalog)
                    .and_then(|object| object.as_dict_mut().ok())
                {
                    catalog.set("AcroForm", Object::Reference(form_id));
                }
            }
            Commit::Extend { sets, appends } => {
                for (location, entries) in sets {
                    if let Some(dictionary) = location
                        .object_mut(target)
                        .and_then(|object| object.as_dict_mut().ok())
                    {
                        for (key, value) in entries {
                            dictionary.set(key, value);
                        }
                    }
                }
                for (location, values) in appends {
                    if let Some(items) = location
                        .object_mut(target)
                        .and_then(|object| object.as_array_mut().ok())
                    {
                        items.extend(values);
                    }
                }
            }
        }
    }
}

/// Remove the widgets and fields of deleted pages from a document's own form.
///
/// `delete_pages` and `select` drop pages from the page tree, but the field
/// tree still references their widgets, whose `/P` keeps each deleted page
/// reachable through every save. The removal reads the remaining pages as a
/// partial import reads its imported pages: it walks the field tree under the
/// limits of `get_form_fields()`, removes from every walked `/Kids` the
/// widgets of deleted pages and the fields whose widgets all lay there, trims
/// a button field's matching `/Opt`, drops such top-level fields from
/// `/Fields` and their entries from `/CO`, and points a remaining widget whose
/// `/P` names a deleted page at the remaining page that lists it. Fields that
/// never had a widget stay. Pruning then collects what nothing reaches.
pub(crate) struct FormPagePrune {
    kids: Vec<(ArrayLocation, Vec<usize>)>,
    fields: Option<(Location, Vec<usize>)>,
    calculation_order: Option<(Location, Vec<usize>)>,
    pages: Vec<(ObjectId, ObjectId)>,
}

impl FormPagePrune {
    /// Plan the removal; `pages` lists every page before the edit and
    /// `retained` the pages that remain. Returns `None` when nothing changes.
    pub(crate) fn prepare(
        doc: &Document,
        pages: &[ObjectId],
        retained: &HashSet<ObjectId>,
    ) -> PyResult<Option<Self>> {
        let Ok(catalog) = doc.trailer.get(b"Root").and_then(Object::as_reference) else {
            return Ok(None);
        };
        let catalog = Location {
            id: catalog,
            path: Vec::new(),
        };
        let Slot::Found(form_location) = slot(doc, &catalog, b"AcroForm")? else {
            return Ok(None);
        };
        let Some(form) = form_location.dictionary(doc) else {
            return Ok(None);
        };
        if is_page_tree_or_catalog(form) {
            return Ok(None);
        }
        let top_level = source_top_level_fields(doc, form)?;
        if top_level.is_empty() {
            return Ok(None);
        }
        let annotations = PageAnnotations::read_deletion(doc, pages, retained)?;
        let tree = PartialTree::walk(doc, &top_level, annotations)?;
        let kids = tree.removals(doc)?;
        // A malformed form can share one array between `/Kids` and `/Fields`
        // or `/CO`; it is then filtered once, by the `/Kids` plan.
        let planned = |location: &Location| {
            location.path.is_empty()
                && kids
                    .iter()
                    .any(|(array, _)| *array == ArrayLocation::Object(location.id))
        };

        let fields = match slot(doc, &form_location, b"Fields")? {
            Slot::Found(location) if !planned(&location) => {
                let mut removed = Vec::new();
                for (index, entry) in location
                    .array(doc)
                    .unwrap_or(&Vec::new())
                    .iter()
                    .enumerate()
                {
                    if let Object::Reference(id) = entry
                        && tree.coverage.contains_key(id)
                        && !tree.keeps(doc, entry)
                    {
                        push(&mut removed, index)?;
                    }
                }
                (!removed.is_empty()).then_some((location, removed))
            }
            _ => None,
        };
        let mut kept_top_level = Vec::new();
        for &id in &top_level {
            if tree.keeps(doc, &Object::Reference(id)) {
                push(&mut kept_top_level, id)?;
            }
        }
        let kept = tree.retained_fields(doc, &kept_top_level)?;
        let calculation_order = match slot(doc, &form_location, b"CO")? {
            Slot::Found(location) if !planned(&location) => {
                let mut removed = Vec::new();
                for (index, entry) in location
                    .array(doc)
                    .unwrap_or(&Vec::new())
                    .iter()
                    .enumerate()
                {
                    if let Object::Reference(id) = entry
                        && tree.coverage.contains_key(id)
                        && !kept.contains(id)
                    {
                        push(&mut removed, index)?;
                    }
                }
                (!removed.is_empty()).then_some((location, removed))
            }
            _ => None,
        };
        let pages = tree.page_fixes(doc, &kept)?;
        if kids.is_empty() && fields.is_none() && calculation_order.is_none() && pages.is_empty() {
            return Ok(None);
        }
        Ok(Some(Self {
            kids,
            fields,
            calculation_order,
            pages,
        }))
    }

    /// Apply the planned removals; every edit shrinks an array or replaces an
    /// existing `/P`, so nothing allocates.
    pub(crate) fn apply(self, doc: &mut Document) {
        for (array, removed) in self.kids {
            let items = match array {
                ArrayLocation::Entry(id, key) => doc
                    .objects
                    .get_mut(&id)
                    .and_then(|object| object.as_dict_mut().ok())
                    .and_then(|dictionary| dictionary.get_mut(key).ok())
                    .and_then(|items| items.as_array_mut().ok()),
                ArrayLocation::Object(id) => doc
                    .objects
                    .get_mut(&id)
                    .and_then(|object| object.as_array_mut().ok()),
            };
            if let Some(items) = items {
                filter(items, &removed);
            }
        }
        for (location, removed) in self.fields.into_iter().chain(self.calculation_order) {
            if let Some(items) = location
                .object_mut(doc)
                .and_then(|object| object.as_array_mut().ok())
            {
                filter(items, &removed);
            }
        }
        for (widget, page) in self.pages {
            if let Some(dictionary) = doc
                .objects
                .get_mut(&widget)
                .and_then(|object| object.as_dict_mut().ok())
                && let Ok(entry) = dictionary.get_mut(b"P")
            {
                *entry = Object::Reference(page);
            }
        }
    }
}
