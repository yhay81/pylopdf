//! The outline: items read as handles, edited in place, and written as one
//! tree with destinations, colors, styles, and open states.
//!
//! Every read and write shares the outline limits of the flat TOC walk
//! (`MAX_TOC_TREE_NODES`, `MAX_TOC_TREE_EDGES`, `MAX_TOC_TREE_DEPTH`, and the
//! 16 MiB text budgets), so an outline that one call writes stays readable
//! and editable by the others.

use super::*;

/// What an outline item leads to, as Python's target records carry it: the
/// kind (`"none"`, `"goto"`, `"uri"`, `"gotor"`, `"launch"`, or `"named"`), a
/// one-based page of this document (`"goto"`) or a zero-based page of
/// another (`"gotor"`), a display point, a zoom, the URI or file, and a
/// destination or action name.
pub(super) type TargetTuple = (
    String,
    Option<u32>,
    Option<(f64, f64)>,
    Option<f64>,
    Option<String>,
    Option<String>,
);

/// One outline item as [`_Document::outline_item`] reads it: level, title,
/// target, color, `/F` flags, `/Count`, and whether it has children.
pub(super) type OutlineItemTuple = (
    u32,
    String,
    TargetTuple,
    Option<(f64, f64, f64)>,
    i64,
    i64,
    bool,
);

/// One item of the complete outline in document order: object number and
/// generation (0 and 0 for a direct dictionary), level, title, target,
/// color, `/F` flags, and `/Count`.
pub(super) type OutlineEntryTuple = (
    u32,
    u16,
    u32,
    String,
    TargetTuple,
    Option<(f64, f64, f64)>,
    i64,
    i64,
);

/// One item to write: level, title, target, color, `/F` flags, and whether
/// its children show.
pub(super) type OutlineSpec = (u32, String, TargetTuple, Option<(f64, f64, f64)>, u32, bool);

fn no_target() -> TargetTuple {
    ("none".to_owned(), None, None, None, None, None)
}

/// The source and returned text of one outline read, charged against the
/// 16 MiB TOC budgets.
#[derive(Default)]
struct TextBudget {
    source: usize,
    returned: usize,
    input: bool,
}

impl TextBudget {
    fn charge(&mut self, amount: usize, returned: bool) -> PyResult<()> {
        let (total, label) = if returned {
            (&mut self.returned, "returned text")
        } else {
            (&mut self.source, "source text")
        };
        if self.input {
            add_input_text_budget(
                total,
                amount,
                MAX_TOC_TEXT_BYTES,
                "toc_input_size",
                &format!("outline {label}"),
            )
        } else {
            add_toc_text_budget(total, amount, label)
        }
    }

    /// Return bytes as lossy UTF-8 after charging both budgets.
    fn bytes(&mut self, bytes: &[u8]) -> PyResult<String> {
        self.charge(bytes.len(), false)?;
        let text = String::from_utf8_lossy(bytes).into_owned();
        self.charge(text.len(), true)?;
        Ok(text)
    }

    /// Decode a PDF text string after charging both budgets.
    fn text(&mut self, doc: &Document, object: &Object) -> PyResult<Option<String>> {
        let object = deref_object(doc, object);
        let Object::String(encoded, _) = object else {
            return Ok(None);
        };
        self.charge(encoded.len(), false)?;
        let Ok(text) = decode_text_string(object) else {
            return Ok(None);
        };
        self.charge(text.len(), true)?;
        Ok(Some(text))
    }

    /// Decode a file specification's name, as link reads take it.
    fn file(&mut self, doc: &Document, object: &Object) -> PyResult<Option<String>> {
        let object = deref_object(doc, object);
        let name = match object {
            Object::Dictionary(filespec) => {
                filespec.get(b"UF").or_else(|_| filespec.get(b"F")).ok()
            }
            _ => Some(object),
        };
        match name {
            Some(name) => self.text(doc, name),
            None => Ok(None),
        }
    }
}

/// Return the catalog's outline root, or None when the document has none.
fn outline_root(doc: &Document) -> PyResult<Option<(&Dictionary, Option<ObjectId>)>> {
    let Some(object) = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Outlines").ok())
    else {
        return Ok(None);
    };
    match non_null_value(doc, object) {
        Ok(None) => Ok(None),
        Ok(Some(Object::Dictionary(root))) => {
            let (id, _) = doc.dereference(object).map_err(to_py_err)?;
            Ok(Some((root, id)))
        }
        _ => Err(PdfError::new_err("catalog Outlines is not a dictionary")),
    }
}

/// Return the dictionary of an outline item, or a stale-object error.
fn item_dictionary(doc: &Document, id: ObjectId) -> PyResult<&Dictionary> {
    match doc.objects.get(&id) {
        Some(Object::Dictionary(item)) => Ok(item),
        _ => Err(StaleObjectError::new_err(format!(
            "outline item {} {} R no longer exists",
            id.0, id.1
        ))),
    }
}

/// Return `count` copies of `value` in an exactly reserved vector.
fn filled<T: Clone>(count: usize, value: T, what: &str) -> PyResult<Vec<T>> {
    let mut out = Vec::new();
    out.try_reserve_exact(count)
        .map_err(|error| PdfError::new_err(format!("failed to allocate {what}: {error}")))?;
    out.resize(count, value);
    Ok(out)
}

/// Record `id` in a cycle set that grows fallibly; false when it was there.
fn first_visit(set: &mut HashSet<ObjectId>, id: ObjectId) -> PyResult<bool> {
    if set.contains(&id) {
        return Ok(false);
    }
    set.try_reserve(1).map_err(|error| {
        PdfError::new_err(format!("failed to grow outline cycle detection: {error}"))
    })?;
    Ok(set.insert(id))
}

/// Return the referenced items of a `/First`–`/Next` chain, at most
/// `MAX_TOC_ENTRIES`, ending at the first entry that is no reference to a
/// dictionary or that the chain already listed.
fn child_ids(doc: &Document, parent: &Dictionary) -> PyResult<Vec<ObjectId>> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    let mut next = parent.get(b"First").ok();
    while let Some(Object::Reference(id)) = next {
        if !first_visit(&mut seen, *id)? {
            break;
        }
        let Some(Object::Dictionary(item)) = doc.objects.get(id) else {
            break;
        };
        if out.len() >= MAX_TOC_ENTRIES {
            return Err(toc_entry_count_err(format!(
                "outline children exceed the {MAX_TOC_ENTRIES}-entry safety limit"
            )));
        }
        out.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow outline children: {error}"))
        })?;
        out.push(*id);
        next = item.get(b"Next").ok();
    }
    Ok(out)
}

/// An editable item has children only when its first child names a
/// dictionary; a null, missing, or mistyped target is absent.
fn item_has_children(doc: &Document, item: &Dictionary) -> bool {
    match item.get(b"First") {
        Ok(Object::Reference(id)) => matches!(doc.objects.get(id), Some(Object::Dictionary(_))),
        _ => false,
    }
}

/// Return the color of an outline item, three finite components.
fn item_color(doc: &Document, item: &Dictionary) -> Option<(f64, f64, f64)> {
    let Ok(Object::Array(components)) = item.get(b"C").map(|object| deref_object(doc, object))
    else {
        return None;
    };
    let number = |object: &Object| match deref_object(doc, object) {
        Object::Integer(value) => Some(*value as f64),
        Object::Real(value) => Some(f64::from(*value)),
        _ => None,
    };
    match components.as_slice() {
        [red, green, blue] => {
            let (red, green, blue) = (number(red)?, number(green)?, number(blue)?);
            [red, green, blue]
                .iter()
                .all(|value| value.is_finite() && (0.0..=1.0).contains(value))
                .then_some((red, green, blue))
        }
        _ => None,
    }
}

/// Return an integer entry of an outline item, or 0.
fn item_integer(doc: &Document, item: &Dictionary, key: &[u8]) -> i64 {
    item.get(key)
        .ok()
        .map(|object| deref_object(doc, object))
        .and_then(|object| object.as_i64().ok())
        .unwrap_or(0)
}

impl _Document {
    /// Read what an outline item leads to, through `/A` or else `/Dest`.
    fn outline_target(
        &self,
        item: &Dictionary,
        cache: &mut LinkResolutionCache,
        budget: &mut TextBudget,
    ) -> PyResult<TargetTuple> {
        self.outline_target_entries(item.get(b"A").ok(), item.get(b"Dest").ok(), cache, budget)
    }

    fn outline_target_entries(
        &self,
        action: Option<&Object>,
        destination: Option<&Object>,
        cache: &mut LinkResolutionCache,
        budget: &mut TextBudget,
    ) -> PyResult<TargetTuple> {
        let doc = &self.doc;
        let action = action.and_then(|action| deref_object(doc, action).as_dict().ok());
        let Some(action) = action else {
            return match destination {
                Some(dest) => self.goto_target(dest, cache, budget),
                None => Ok(no_target()),
            };
        };
        match action.get(b"S").and_then(Object::as_name) {
            Ok(b"GoTo") => match action.get(b"D") {
                Ok(dest) => self.goto_target(dest, cache, budget),
                Err(_) => Ok(no_target()),
            },
            Ok(b"URI") => {
                let uri = match action
                    .get(b"URI")
                    .ok()
                    .map(|object| deref_object(doc, object))
                    .and_then(|object| object.as_str().ok())
                {
                    Some(bytes) => Some(budget.bytes(bytes)?),
                    None => None,
                };
                Ok(("uri".to_owned(), None, None, None, uri, None))
            }
            Ok(b"GoToR") => {
                let file = match action.get(b"F") {
                    Ok(object) => budget.file(doc, object)?,
                    Err(_) => None,
                };
                let (page, name) = match action.get(b"D").map(|object| deref_object(doc, object)) {
                    Ok(Object::Name(name) | Object::String(name, _)) => {
                        (None, Some(budget.bytes(name)?))
                    }
                    Ok(Object::Array(array)) => (
                        array
                            .first()
                            .map(|object| deref_object(doc, object))
                            .and_then(|object| object.as_i64().ok())
                            .and_then(|page| u32::try_from(page).ok()),
                        None,
                    ),
                    Ok(Object::Integer(page)) => (u32::try_from(*page).ok(), None),
                    _ => (None, None),
                };
                Ok(("gotor".to_owned(), page, None, None, file, name))
            }
            Ok(b"Launch") => {
                let file = match action.get(b"F") {
                    Ok(object) => budget.file(doc, object)?,
                    Err(_) => None,
                };
                Ok(("launch".to_owned(), None, None, None, file, None))
            }
            Ok(b"Named") => {
                let name = match action
                    .get(b"N")
                    .ok()
                    .and_then(|object| deref_object(doc, object).as_name().ok())
                {
                    Some(bytes) => Some(budget.bytes(bytes)?),
                    None => None,
                };
                Ok(("named".to_owned(), None, None, None, None, name))
            }
            _ => Ok(no_target()),
        }
    }

    /// Resolve a local destination to a page, point, and zoom.
    fn goto_target(
        &self,
        dest: &Object,
        cache: &mut LinkResolutionCache,
        budget: &mut TextBudget,
    ) -> PyResult<TargetTuple> {
        let (page, to, zoom, _) =
            self.resolve_destination(dest, cache, true, &mut |name| budget.bytes(name))?;
        Ok(match page {
            Some(page) => ("goto".to_owned(), Some(page), to, zoom, None, None),
            None => no_target(),
        })
    }

    /// Return an item's level, the number of `/Parent` steps to the outline
    /// root, at most `MAX_TOC_TREE_DEPTH`.
    fn outline_level(&self, item: &Dictionary, root: Option<ObjectId>) -> u32 {
        let mut level = 1u32;
        let mut parent = item.get(b"Parent").ok();
        for _ in 0..MAX_TOC_TREE_DEPTH {
            let Some(Object::Reference(id)) = parent else {
                break;
            };
            if Some(*id) == root {
                break;
            }
            let Some(Object::Dictionary(next)) = self.doc.objects.get(id) else {
                break;
            };
            if !next.has(b"Parent") {
                break;
            }
            level += 1;
            parent = next.get(b"Parent").ok();
        }
        level
    }
}

/// Implements [`_Document::outline_children`].
pub(super) fn outline_children(
    this: &_Document,
    parent: Option<(u32, u16)>,
) -> PyResult<Vec<(u32, u16)>> {
    let doc = &this.doc;
    let ids = match parent {
        None => match outline_root(doc)? {
            Some((root, _)) => child_ids(doc, root)?,
            None => Vec::new(),
        },
        Some(id) => child_ids(doc, item_dictionary(doc, id)?)?,
    };
    Ok(ids)
}

/// Implements [`_Document::outline_item`].
pub(super) fn outline_item(
    this: &mut _Document,
    py: Python<'_>,
    id: (u32, u16),
) -> PyResult<OutlineItemTuple> {
    py.detach(|| {
        // Detach the cache so the read can borrow the document, and restore
        // it on success and failure because reads never edit.
        let mut cache = this.link_resolution.take().unwrap_or_default();
        let result = read_outline_item(this, id, &mut cache);
        this.link_resolution = Some(cache);
        result
    })
}

fn read_outline_item(
    this: &_Document,
    id: ObjectId,
    cache: &mut LinkResolutionCache,
) -> PyResult<OutlineItemTuple> {
    let doc = &this.doc;
    let item = item_dictionary(doc, id)?;
    let mut budget = TextBudget::default();
    let title = match item.get(b"Title") {
        Ok(title) => budget.text(doc, title)?.unwrap_or_default(),
        Err(_) => String::new(),
    };
    let target = this.outline_target(item, cache, &mut budget)?;
    let root = outline_root(doc)?.and_then(|(_, id)| id);
    Ok((
        this.outline_level(item, root),
        title,
        target,
        item_color(doc, item),
        item_integer(doc, item, b"F"),
        item_integer(doc, item, b"Count"),
        item_has_children(doc, item),
    ))
}

/// Implements [`_Document::outline_entries`]: every item in document order
/// under the limits of the flat TOC walk.
pub(super) fn outline_entries(
    this: &mut _Document,
    py: Python<'_>,
) -> PyResult<Vec<OutlineEntryTuple>> {
    py.detach(|| {
        let mut cache = this.link_resolution.take().unwrap_or_default();
        let result = collect_outline(this, &mut cache, None);
        this.link_resolution = Some(cache);
        result
    })
}

/// A prepared field replacement borrowed while validating the complete
/// outline's resulting text, before any PDF mutation.
enum OutlineEdit<'a> {
    Title(ObjectId, &'a Object),
    Target(ObjectId, &'a Option<(&'static str, Object)>),
}

impl OutlineEdit<'_> {
    fn id(&self) -> ObjectId {
        match self {
            Self::Title(id, _) | Self::Target(id, _) => *id,
        }
    }
}

fn collect_outline(
    this: &_Document,
    cache: &mut LinkResolutionCache,
    edit: Option<&OutlineEdit<'_>>,
) -> PyResult<Vec<OutlineEntryTuple>> {
    let doc = &this.doc;
    let Some((root, _)) = outline_root(doc)? else {
        return Ok(Vec::new());
    };
    let Ok(first) = root.get(b"First") else {
        return Ok(Vec::new());
    };
    let mut stack = Vec::new();
    stack.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!("failed to allocate the outline stack: {error}"))
    })?;
    stack.push((first, 1usize));
    let mut visited = HashSet::new();
    let mut nodes = 0usize;
    let mut edges = 1usize;
    let mut budget = TextBudget {
        input: edit.is_some(),
        ..TextBudget::default()
    };
    let mut out = Vec::new();
    while let Some((node_object, depth)) = stack.pop() {
        let id = match node_object {
            Object::Reference(id) => {
                if !first_visit(&mut visited, *id)? {
                    continue;
                }
                *id
            }
            _ => (0, 0),
        };
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes += 1;
        if nodes > MAX_TOC_TREE_NODES {
            return Err(toc_entry_count_err(format!(
                "outline tree exceeds the {MAX_TOC_TREE_NODES}-node safety limit"
            )));
        }
        let changed = edit.filter(|edit| edit.id() == id);
        let title = match changed {
            Some(OutlineEdit::Title(_, title)) => Some(*title),
            _ => node.get(b"Title").ok(),
        };
        let title = match title {
            Some(title) => budget.text(doc, title)?.unwrap_or_default(),
            None => String::new(),
        };
        let target = match changed {
            Some(OutlineEdit::Target(_, entry)) => {
                let (action, destination) = match entry.as_ref() {
                    Some(("A", object)) => (Some(object), None),
                    Some((_, object)) => (None, Some(object)),
                    None => (None, None),
                };
                this.outline_target_entries(action, destination, cache, &mut budget)?
            }
            _ => this.outline_target(node, cache, &mut budget)?,
        };
        if edit.is_none() {
            if out.len() == out.capacity() {
                out.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow returned outline items: {error}"))
                })?;
            }
            out.push((
                id.0,
                id.1,
                u32::try_from(depth).map_err(|error| PdfError::new_err(error.to_string()))?,
                title,
                target,
                item_color(doc, node),
                item_integer(doc, node, b"F"),
                item_integer(doc, node, b"Count"),
            ));
        }
        for (key, child_depth) in [
            (b"Next".as_slice(), depth),
            (b"First".as_slice(), depth + 1),
        ] {
            let Ok(next) = node.get(key) else {
                continue;
            };
            if child_depth > MAX_TOC_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "outline tree exceeds the {MAX_TOC_TREE_DEPTH}-level safety limit"
                )));
            }
            edges += 1;
            if edges > MAX_TOC_TREE_EDGES {
                return Err(toc_entry_count_err(format!(
                    "outline tree exceeds the {MAX_TOC_TREE_EDGES}-edge safety limit"
                )));
            }
            if stack.len() == stack.capacity() {
                stack.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow the outline stack: {error}"))
                })?;
            }
            // `/First` goes on top, so a child comes before its next sibling.
            stack.push((next, child_depth));
        }
    }
    Ok(out)
}

/// Check caller text for one write against the 16 MiB input budgets.
#[derive(Default)]
struct InputBudget {
    source: usize,
    encoded: usize,
}

impl InputBudget {
    fn charge(&mut self, text: &str, label: &str) -> PyResult<()> {
        add_input_text_budget(
            &mut self.source,
            text.len(),
            MAX_TOC_TEXT_BYTES,
            "toc_input_size",
            &format!("outline {label}"),
        )?;
        let encoded = pdf_text_string_len(text).ok_or_else(|| {
            limit_err(
                "toc_input_size",
                "outline text exceeds the platform text-size limit",
            )
        })?;
        add_input_text_budget(
            &mut self.encoded,
            encoded,
            MAX_TOC_TEXT_BYTES,
            "toc_input_size",
            &format!("encoded outline {label}"),
        )
    }
}

/// Return a validated color as a `/C` array.
fn color_object(color: (f64, f64, f64)) -> PyResult<Object> {
    let (red, green, blue) = color;
    let mut components = Vec::new();
    for value in [red, green, blue] {
        if !(value.is_finite() && (0.0..=1.0).contains(&value)) {
            return Err(PdfError::new_err(format!(
                "outline color components must be from 0 to 1: {value}"
            )));
        }
        components.push(Object::Real(value as f32));
    }
    Ok(Object::Array(components))
}

/// One prepared item of a complete outline.
struct PreparedItem {
    level: usize,
    title: Object,
    target: Option<(&'static str, Object)>,
    color: Option<Object>,
    flags: u32,
    open: bool,
}

/// Implements [`_Document::write_outline`]: replace the outline with
/// `entries`, all validated and prepared before any change.
pub(super) fn write_outline(this: &mut _Document, entries: Vec<OutlineSpec>) -> PyResult<()> {
    if entries.len() > MAX_TOC_ENTRIES {
        return Err(limit_err(
            "toc_entry_count",
            format!("cannot write more than {MAX_TOC_ENTRIES} outline items"),
        ));
    }
    let page_ids = this.doc.page_ids()?;
    let mut prepared = Vec::new();
    prepared.try_reserve_exact(entries.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate prepared outline items: {error}"
        ))
    })?;
    let mut budget = InputBudget::default();
    let mut previous_level = 0usize;
    for (level, title, target, color, flags, open) in &entries {
        let level = *level as usize;
        if level == 0 || level > previous_level + 1 {
            return Err(PdfError::new_err(format!(
                "invalid outline level {level}; levels start at 1 and can increase by at most one"
            )));
        }
        if level > MAX_TOC_TREE_DEPTH {
            return Err(PdfError::new_err(format!(
                "outline depth exceeds the {MAX_TOC_TREE_DEPTH}-level safety limit"
            )));
        }
        budget.charge(title, "title")?;
        prepared.push(PreparedItem {
            level,
            title: try_info_metadata_text_string(title)?,
            target: target_entry(this, page_ids, target, &mut |text, label| {
                budget.charge(text, label)
            })?,
            color: color.map(color_object).transpose()?,
            flags: *flags,
            open: *open,
        });
        previous_level = level;
    }
    drop(entries);
    let count = prepared.len();
    // Parent, first and last child, and next sibling, by position.
    let mut parent = filled(count, None, "outline parents")?;
    let mut first = filled(count, None, "outline children")?;
    let mut last = filled(count, None, "outline children")?;
    let mut next = filled(count, None, "outline siblings")?;
    let mut prev = filled(count, None, "outline siblings")?;
    let mut top: (Option<usize>, Option<usize>) = (None, None);
    // Levels are at most MAX_TOC_TREE_DEPTH, so the chain never grows past it.
    let mut chain: Vec<usize> = filled(MAX_TOC_TREE_DEPTH, 0, "the outline level chain")?;
    chain.clear();
    for (index, item) in prepared.iter().enumerate() {
        chain.truncate(item.level - 1);
        let siblings_last = match chain.last() {
            Some(&owner) => {
                parent[index] = Some(owner);
                if first[owner].is_none() {
                    first[owner] = Some(index);
                }
                last[owner].replace(index)
            }
            None => {
                if top.0.is_none() {
                    top.0 = Some(index);
                }
                top.1.replace(index)
            }
        };
        if let Some(previous) = siblings_last {
            next[previous] = Some(index);
            prev[index] = Some(previous);
        }
        chain.push(index);
    }
    // An item comes before its descendants, so a reverse pass finishes each
    // item's count before its parent adds it.
    let mut visible = filled(count, 0i64, "outline counts")?;
    let mut root_visible = 0i64;
    for index in (0..count).rev() {
        let shown = 1 + if prepared[index].open {
            visible[index]
        } else {
            0
        };
        match parent[index] {
            Some(owner) => visible[owner] += shown,
            None => root_visible += shown,
        }
    }
    let base = this.doc.max_id;
    let object_count = u32::try_from(count)
        .ok()
        .and_then(|count| count.checked_add(1))
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let end = base
        .checked_add(object_count)
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let root_id: ObjectId = (base + 1, 0);
    let item_id = |index: usize| -> ObjectId { (base + 2 + index as u32, 0) };
    let mut objects = Vec::new();
    objects.try_reserve_exact(count + 1).map_err(|error| {
        PdfError::new_err(format!("failed to allocate outline objects: {error}"))
    })?;
    for (index, item) in prepared.into_iter().enumerate() {
        let mut dictionary = Dictionary::new();
        dictionary.set("Title", item.title);
        dictionary.set(
            "Parent",
            Object::Reference(parent[index].map_or(root_id, item_id)),
        );
        if let Some(previous) = prev[index] {
            dictionary.set("Prev", Object::Reference(item_id(previous)));
        }
        if let Some(following) = next[index] {
            dictionary.set("Next", Object::Reference(item_id(following)));
        }
        if let (Some(child), Some(final_child)) = (first[index], last[index]) {
            dictionary.set("First", Object::Reference(item_id(child)));
            dictionary.set("Last", Object::Reference(item_id(final_child)));
            let shown = visible[index];
            dictionary.set("Count", if item.open { shown } else { -shown });
        }
        if let Some((key, value)) = item.target {
            dictionary.set(key, value);
        }
        if let Some(color) = item.color {
            dictionary.set("C", color);
        }
        if item.flags != 0 {
            dictionary.set("F", i64::from(item.flags));
        }
        objects.push((item_id(index), Object::Dictionary(dictionary)));
    }
    if let (Some(child), Some(final_child)) = top {
        objects.push((
            root_id,
            Object::Dictionary(dictionary! {
                "Type" => Object::Name(b"Outlines".to_vec()),
                "First" => Object::Reference(item_id(child)),
                "Last" => Object::Reference(item_id(final_child)),
                "Count" => root_visible,
            }),
        ));
    }
    this.doc.catalog().map_err(to_py_err)?;

    this.invalidate_hayro_pdf_keeping_placements();
    // lopdf's bookmark builder state never describes this outline.
    this.doc.bookmarks.clear();
    this.doc.bookmark_table.clear();
    this.doc.max_bookmark_id = 0;
    let catalog = this.doc.catalog_mut().map_err(to_py_err)?;
    catalog.remove(b"Outlines");
    if count > 0 {
        catalog.set("Outlines", Object::Reference(root_id));
        this.doc.insert_new_objects(objects, end);
    }
    // Remove the replaced outline's objects.
    prune_unreachable_objects(&mut this.doc)?;
    Ok(())
}

/// Return an outline item for editing, or a stale-object error.
fn item_dictionary_mut(doc: &mut Document, id: ObjectId) -> PyResult<&mut Dictionary> {
    match doc.objects.get_mut(&id) {
        Some(Object::Dictionary(item)) => Ok(item),
        _ => Err(StaleObjectError::new_err(format!(
            "outline item {} {} R no longer exists",
            id.0, id.1
        ))),
    }
}

/// Implements [`_Document::set_outline_title`].
pub(super) fn set_outline_title(this: &mut _Document, id: (u32, u16), title: &str) -> PyResult<()> {
    InputBudget::default().charge(title, "title")?;
    let title = try_info_metadata_text_string(title)?;
    item_dictionary(&this.doc, id)?;
    collect_outline(
        this,
        &mut LinkResolutionCache::default(),
        Some(&OutlineEdit::Title(id, &title)),
    )?;
    this.invalidate_hayro_pdf_keeping_placements();
    item_dictionary_mut(&mut this.doc, id)?.set("Title", title);
    Ok(())
}

/// Implements [`_Document::set_outline_style`].
pub(super) fn set_outline_style(
    this: &mut _Document,
    id: (u32, u16),
    color: Option<(f64, f64, f64)>,
    flags: u32,
) -> PyResult<()> {
    let color = color.map(color_object).transpose()?;
    item_dictionary(&this.doc, id)?;
    this.invalidate_hayro_pdf_keeping_placements();
    let item = item_dictionary_mut(&mut this.doc, id)?;
    match color {
        Some(color) => item.set("C", color),
        None => {
            item.remove(b"C");
        }
    }
    if flags == 0 {
        item.remove(b"F");
    } else {
        item.set("F", i64::from(flags));
    }
    Ok(())
}

/// Implements [`_Document::set_outline_target`].
pub(super) fn set_outline_target(
    this: &mut _Document,
    id: (u32, u16),
    target: TargetTuple,
) -> PyResult<()> {
    item_dictionary(&this.doc, id)?;
    let mut budget = InputBudget::default();
    let entry = target_entry(this, this.doc.page_ids()?, &target, &mut |text, label| {
        budget.charge(text, label)
    })?;
    collect_outline(
        this,
        &mut LinkResolutionCache::default(),
        Some(&OutlineEdit::Target(id, &entry)),
    )?;
    this.invalidate_hayro_pdf_keeping_placements();
    let item = item_dictionary_mut(&mut this.doc, id)?;
    item.remove(b"Dest");
    item.remove(b"A");
    if let Some((key, value)) = entry {
        item.set(key, value);
    }
    Ok(())
}

/// Implements [`_Document::set_outline_open`]: show or hide an item's
/// children, then recount every `/Count` of the outline.
pub(super) fn set_outline_open(this: &mut _Document, id: (u32, u16), open: bool) -> PyResult<()> {
    let item = item_dictionary(&this.doc, id)?;
    if !item_has_children(&this.doc, item) {
        return Err(PdfError::new_err(
            "an outline item without children has no open state",
        ));
    }
    // Plan the counts with the item's new state before changing anything.
    let counts = outline_counts(&this.doc, Some((id, open)))?;
    let root = outline_root(&this.doc)?.and_then(|(_, root)| root);
    // Resolve every indirect write target before changing any count. The
    // root ID is the dictionary at the end of a possible reference chain.
    for (target, _) in &counts {
        if let Some(target) = target.or(root) {
            item_dictionary(&this.doc, target)?;
        }
    }
    this.invalidate_hayro_pdf_keeping_placements();
    for (target, count) in counts {
        if let Some(target) = target.or(root) {
            item_dictionary_mut(&mut this.doc, target)?.set("Count", count);
        } else {
            // A direct root has no object ID, but its count still belongs
            // to the same plan as every referenced item.
            this.doc
                .catalog_mut()
                .and_then(|catalog| catalog.get_mut(b"Outlines"))
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?
                .set("Count", count);
        }
    }
    Ok(())
}

/// Return the `/Count` of every referenced item with children and of the
/// root (as `None`), reading each item's open state from the sign of its
/// count, or from `change` for one item.
fn outline_counts(
    doc: &Document,
    change: Option<(ObjectId, bool)>,
) -> PyResult<Vec<(Option<ObjectId>, i64)>> {
    let Some((root, _)) = outline_root(doc)? else {
        return Ok(Vec::new());
    };
    // Items in document order with their parent position, as the TOC walk
    // visits them, under the same limits.
    let mut order: Vec<(ObjectId, Option<usize>, bool)> = Vec::new();
    let mut stack: Vec<(ObjectId, Option<usize>, usize)> = Vec::new();
    let mut visited = HashSet::new();
    let mut edges = 0usize;
    let push_chain = |stack: &mut Vec<(ObjectId, Option<usize>, usize)>,
                      owner: &Dictionary,
                      parent: Option<usize>,
                      depth: usize,
                      edges: &mut usize|
     -> PyResult<()> {
        let children = child_ids(doc, owner)?;
        *edges += children.len();
        if *edges > MAX_TOC_TREE_EDGES {
            return Err(toc_entry_count_err(format!(
                "outline tree exceeds the {MAX_TOC_TREE_EDGES}-edge safety limit"
            )));
        }
        if !children.is_empty() && depth > MAX_TOC_TREE_DEPTH {
            return Err(PdfError::new_err(format!(
                "outline tree exceeds the {MAX_TOC_TREE_DEPTH}-level safety limit"
            )));
        }
        stack.try_reserve(children.len()).map_err(|error| {
            PdfError::new_err(format!("failed to grow the outline stack: {error}"))
        })?;
        stack.extend(children.into_iter().rev().map(|id| (id, parent, depth)));
        Ok(())
    };
    push_chain(&mut stack, root, None, 1, &mut edges)?;
    while let Some((id, parent, depth)) = stack.pop() {
        if !first_visit(&mut visited, id)? {
            continue;
        }
        if order.len() >= MAX_TOC_TREE_NODES {
            return Err(toc_entry_count_err(format!(
                "outline tree exceeds the {MAX_TOC_TREE_NODES}-node safety limit"
            )));
        }
        let item = item_dictionary(doc, id)?;
        let open = match change {
            Some((changed, open)) if changed == id => open,
            _ => item_integer(doc, item, b"Count") > 0,
        };
        order.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the outline items: {error}"))
        })?;
        order.push((id, parent, open));
        let position = order.len() - 1;
        push_chain(&mut stack, item, Some(position), depth + 1, &mut edges)?;
    }
    let mut visible = filled(order.len(), 0i64, "outline counts")?;
    let mut has_children = filled(order.len(), false, "outline counts")?;
    let mut root_visible = 0i64;
    for index in (0..order.len()).rev() {
        let (_, parent, open) = order[index];
        let shown = 1 + if open { visible[index] } else { 0 };
        match parent {
            Some(owner) => {
                visible[owner] += shown;
                has_children[owner] = true;
            }
            None => root_visible += shown,
        }
    }
    let mut counts = Vec::new();
    counts.try_reserve_exact(order.len() + 1).map_err(|error| {
        PdfError::new_err(format!("failed to allocate outline counts: {error}"))
    })?;
    for (index, (id, _, open)) in order.iter().enumerate() {
        if has_children[index] {
            counts.push((
                Some(*id),
                if *open {
                    visible[index]
                } else {
                    -visible[index]
                },
            ));
        }
    }
    counts.push((None, root_visible));
    Ok(counts)
}
