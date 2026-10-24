//! Sanitization: removing what a document can carry besides its pages.
//!
//! `sanitize` runs on the document and restores a clone on any error. Hidden
//! text goes through the redaction engine (`redaction::apply_pass` with a
//! glyph test), annotations through their page arrays, and scripts,
//! actions, embedded files, metadata, and thumbnails through one walk over
//! every dictionary of the graph: an indirect action is classified once
//! before the walk, so that the walk can drop references to it without
//! resolving them while it mutates.

use std::collections::HashSet;

use super::redaction::{Selector, apply_pass};
use super::*;

/// The keys under which a dictionary names an action.
const ACTION_KEYS: [&[u8]; 3] = [b"A", b"OpenAction", b"Next"];
/// Actions that only navigate inside a document or to a URI, which
/// `actions=True` keeps on links and outline items.
const NAVIGATION: [&[u8]; 3] = [b"GoTo", b"URI", b"Named"];

/// What `sanitize` removes.
#[derive(Clone, Copy)]
pub(super) struct SanitizeOptions {
    pub(super) scripts: bool,
    pub(super) actions: bool,
    pub(super) embedded_files: bool,
    pub(super) metadata: bool,
    pub(super) hidden_text: bool,
    pub(super) comments: bool,
    pub(super) links: bool,
    pub(super) thumbnails: bool,
}

/// What a sanitization removed: scripts, other actions, embedded files,
/// metadata entries, hidden glyphs, comments, links, thumbnails, and
/// unreachable objects.
pub(super) type SanitizationTuple = (
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
    usize,
);

#[derive(Default)]
struct Removed {
    scripts: usize,
    actions: usize,
    embedded_files: usize,
    metadata: usize,
    hidden_glyphs: usize,
    comments: usize,
    links: usize,
    thumbnails: usize,
    objects: usize,
}

/// Implements [`_Document::sanitize`].
pub(super) fn sanitize(
    this: &mut _Document,
    py: Python<'_>,
    options: SanitizeOptions,
) -> PyResult<SanitizationTuple> {
    let original = this.doc.clone();
    let isolated = this.isolated_content_pages.clone();
    let result = sanitize_inner(this, py, options);
    match result {
        Ok(removed) => {
            // The document is written in full from now on: the bytes it was
            // opened from still hold everything that went.
            this.incremental_base = None;
            this.invalidate_hayro_pdf();
            Ok((
                removed.scripts,
                removed.actions,
                removed.embedded_files,
                removed.metadata,
                removed.hidden_glyphs,
                removed.comments,
                removed.links,
                removed.thumbnails,
                removed.objects,
            ))
        }
        Err(error) => {
            this.doc.replace(original);
            this.isolated_content_pages = isolated;
            this.invalidate_hayro_pdf();
            Err(error)
        }
    }
}

fn sanitize_inner(
    this: &mut _Document,
    py: Python<'_>,
    options: SanitizeOptions,
) -> PyResult<Removed> {
    let mut removed = Removed::default();
    let page_count = this.page_count()?;
    for page_number in 1..=page_count {
        let page_number = u32::try_from(page_number)
            .map_err(|_| PdfError::new_err("page number out of range"))?;
        let page_id = this.page_id(page_number)?;
        if options.hidden_text {
            fn invisible(glyph: &crate::content::interpret::Glyph) -> bool {
                // Mode 3 paints nothing and mode 7 only clips.
                matches!(glyph.render_mode, 3 | 7)
            }
            let counts = apply_pass(this, py, page_id, &Selector::Glyphs(invisible), &[])?;
            removed.hidden_glyphs += counts.glyphs;
            this.invalidate_hayro_pdf();
        }
        if options.comments || options.links || options.embedded_files {
            remove_page_annotations(this, page_id, options, &mut removed)?;
        }
        if options.thumbnails {
            let page = this.doc.get_dictionary_mut(page_id).map_err(to_py_err)?;
            if page.remove(b"Thumb").is_some() {
                removed.thumbnails += 1;
            }
        }
    }
    let doc: &Document = &this.doc;
    let actions = classify_actions(doc)?;
    let catalog_id = doc
        .trailer
        .get(b"Root")
        .and_then(Object::as_reference)
        .map_err(to_py_err)?;
    if options.embedded_files {
        attachments::visit_embedded_files(doc, |_, _| {
            removed.embedded_files += 1;
            Ok(None::<()>)
        })?;
    }
    let names_id = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Names").ok())
        .and_then(|value| doc.dereference(value).ok())
        .and_then(|(id, _)| id);
    let indirect = indirect_actions(doc)?;
    // Every dictionary in the graph, indirect or direct, in one walk.
    let ids: Vec<ObjectId> = this.doc.objects.keys().copied().collect();
    for id in ids {
        let Some(object) = this.doc.objects.get_mut(&id) else {
            continue;
        };
        let mut stack: Vec<(&mut Object, usize)> = vec![(object, 0)];
        while let Some((object, depth)) = stack.pop() {
            let dict = match object {
                Object::Dictionary(dict) => dict,
                Object::Stream(stream) => &mut stream.dict,
                Object::Array(items) => {
                    if depth == 0 && indirect.arrays.contains(&id) {
                        clean_action_array(items, options, &actions, &mut removed);
                    }
                    if depth < MAX_READ_DEPTH {
                        stack.extend(items.iter_mut().map(|item| (item, depth + 1)));
                    }
                    continue;
                }
                _ => continue,
            };
            clean_dictionary(
                dict,
                id == catalog_id && depth == 0,
                depth == 0 && names_id == Some(id),
                depth == 0 && indirect.triggers.contains(&id),
                options,
                &actions,
                &mut removed,
            );
            if depth < MAX_READ_DEPTH {
                stack.extend(dict.iter_mut().map(|(_, value)| (value, depth + 1)));
            }
        }
    }
    if options.metadata && this.doc.trailer.remove(b"Info").is_some() {
        removed.metadata += 1;
    }
    let before = this.doc.objects.len();
    prune_unreachable_objects(&mut this.doc)?;
    removed.objects = before.saturating_sub(this.doc.objects.len());
    Ok(removed)
}

/// Whether an action dictionary runs JavaScript.
fn is_script(dict: &Dictionary) -> bool {
    matches!(dict.get(b"S"), Ok(Object::Name(name)) if name == b"JavaScript") || dict.has(b"JS")
}

/// Whether an action dictionary only navigates.
fn is_navigation(dict: &Dictionary) -> bool {
    matches!(dict.get(b"S"), Ok(Object::Name(name)) if NAVIGATION.contains(&name.as_slice()))
}

/// The indirect action dictionaries of the graph, by kind.
struct Actions {
    scripts: HashSet<ObjectId>,
    navigation: HashSet<ObjectId>,
    other: HashSet<ObjectId>,
}

fn classify_actions(doc: &Document) -> PyResult<Actions> {
    let mut actions = Actions {
        scripts: HashSet::new(),
        navigation: HashSet::new(),
        other: HashSet::new(),
    };
    for id in doc.objects.keys() {
        let object = doc.get_object(*id).map_err(to_py_err)?;
        let Ok(dict) = object.as_dict() else {
            continue;
        };
        let targets = if is_script(dict) {
            &mut actions.scripts
        } else if !dict.has(b"S") {
            continue;
        } else if is_navigation(dict) {
            &mut actions.navigation
        } else {
            &mut actions.other
        };
        targets.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to classify sanitization actions: {error}"))
        })?;
        targets.insert(*id);
    }
    Ok(actions)
}

/// What an action value is: a script, navigation, another action, or none.
#[derive(PartialEq)]
enum Kind {
    Script,
    Navigation,
    Other,
    NotAnAction,
}

fn kind(value: &Object, actions: &Actions) -> Kind {
    match value {
        Object::Reference(id) if actions.scripts.contains(id) => Kind::Script,
        Object::Reference(id) if actions.navigation.contains(id) => Kind::Navigation,
        Object::Reference(id) if actions.other.contains(id) => Kind::Other,
        Object::Dictionary(dict) if is_script(dict) => Kind::Script,
        Object::Dictionary(dict) if dict.has(b"S") && is_navigation(dict) => Kind::Navigation,
        Object::Dictionary(dict) if dict.has(b"S") => Kind::Other,
        _ => Kind::NotAnAction,
    }
}

/// Remove from one dictionary what the options select.
fn clean_dictionary(
    dict: &mut Dictionary,
    catalog: bool,
    names: bool,
    triggers: bool,
    options: SanitizeOptions,
    actions: &Actions,
    removed: &mut Removed,
) {
    if options.actions {
        // Actions that run when something happens, not when a reader clicks.
        if let Some(value) = dict.remove(b"AA") {
            removed.actions += match value {
                Object::Dictionary(triggers) => triggers.len(),
                _ => 1,
            };
        }
        if dict.remove(b"OpenAction").is_some() {
            removed.actions += 1;
        }
    }
    for key in ACTION_KEYS {
        let Ok(value) = dict.get_mut(key) else {
            continue;
        };
        if let Object::Array(items) = value {
            clean_action_array(items, options, actions, removed);
            continue;
        }
        let drop = match kind(value, actions) {
            Kind::Script if options.scripts => {
                removed.scripts += 1;
                true
            }
            Kind::Other if options.actions => {
                removed.actions += 1;
                true
            }
            _ => false,
        };
        if drop {
            dict.remove(key);
        }
    }
    if options.scripts {
        if let Ok(Object::Dictionary(triggers)) = dict.get_mut(b"AA") {
            clean_triggers(triggers, actions, removed);
            if triggers.is_empty() {
                dict.remove(b"AA");
            }
        }
        // XFA forms carry their own scripts.
        if dict.remove(b"XFA").is_some() {
            removed.scripts += 1;
        }
    }
    if catalog {
        if let Ok(Object::Dictionary(names)) = dict.get_mut(b"Names") {
            clean_names(names, options, removed);
        }
        if options.embedded_files {
            dict.remove(b"Collection");
        }
    }
    if names {
        clean_names(dict, options, removed);
    }
    if triggers && options.scripts {
        clean_triggers(dict, actions, removed);
    }
    if options.embedded_files {
        dict.remove(b"AF");
    }
    if options.metadata {
        for key in [b"Metadata".as_slice(), b"PieceInfo", b"LastModified"] {
            if dict.remove(key).is_some() {
                removed.metadata += 1;
            }
        }
    }
}

#[derive(Default)]
struct IndirectActions {
    triggers: HashSet<ObjectId>,
    arrays: HashSet<ObjectId>,
}

/// Find indirect action containers before graph mutation.
fn indirect_actions(doc: &Document) -> PyResult<IndirectActions> {
    let mut indirect = IndirectActions::default();
    let mut stack = Vec::new();
    for object in doc.objects.values() {
        stack.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate sanitization traversal: {error}"
            ))
        })?;
        stack.push(object);
        while let Some(object) = stack.pop() {
            let dict = match object {
                Object::Dictionary(dict) => dict,
                Object::Stream(stream) => &stream.dict,
                Object::Array(items) => {
                    stack.try_reserve(items.len()).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to allocate sanitization traversal: {error}"
                        ))
                    })?;
                    stack.extend(items);
                    continue;
                }
                _ => continue,
            };
            if let Some(id) = dict
                .get(b"AA")
                .ok()
                .and_then(|value| doc.dereference(value).ok())
                .and_then(|(id, value)| value.as_dict().ok().and(id))
            {
                indirect.triggers.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to allocate sanitization traversal: {error}"
                    ))
                })?;
                indirect.triggers.insert(id);
            }
            for key in ACTION_KEYS {
                if let Some(id) = dict
                    .get(key)
                    .ok()
                    .and_then(|value| doc.dereference(value).ok())
                    .and_then(|(id, value)| value.as_array().ok().and(id))
                {
                    indirect.arrays.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to allocate sanitization traversal: {error}"
                        ))
                    })?;
                    indirect.arrays.insert(id);
                }
            }
            stack.try_reserve(dict.len()).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate sanitization traversal: {error}"
                ))
            })?;
            stack.extend(dict.iter().map(|(_, value)| value));
        }
    }
    Ok(indirect)
}

/// Filter an action chain and count scripts and other actions separately.
fn clean_action_array(
    items: &mut Vec<Object>,
    options: SanitizeOptions,
    actions: &Actions,
    removed: &mut Removed,
) {
    items.retain(|item| match kind(item, actions) {
        Kind::Script if options.scripts => {
            removed.scripts += 1;
            false
        }
        Kind::Other if options.actions => {
            removed.actions += 1;
            false
        }
        _ => true,
    });
}

/// Remove script triggers without changing the other trigger actions.
fn clean_triggers(dict: &mut Dictionary, actions: &Actions, removed: &mut Removed) {
    let names: Vec<Vec<u8>> = dict
        .iter()
        .filter(|(_, value)| kind(value, actions) == Kind::Script)
        .map(|(name, _)| name.clone())
        .collect();
    for name in &names {
        dict.remove(name);
    }
    removed.scripts += names.len();
}

/// Clean a catalog name dictionary, whether direct or indirect.
fn clean_names(dict: &mut Dictionary, options: SanitizeOptions, removed: &mut Removed) {
    if options.scripts && dict.remove(b"JavaScript").is_some() {
        removed.scripts += 1;
    }
    if options.embedded_files {
        dict.remove(b"EmbeddedFiles");
    }
}

/// Remove a page's comments, links, and file attachments, with their popups.
fn remove_page_annotations(
    this: &mut _Document,
    page_id: ObjectId,
    options: SanitizeOptions,
    removed: &mut Removed,
) -> PyResult<()> {
    let Some(items) = this.page_annotation_items(page_id)? else {
        return Ok(());
    };
    let items = items.to_vec();
    let doc: &Document = &this.doc;
    let mut keep = Vec::new();
    for item in &items {
        let dict = crate::redact::resolve(doc, item).and_then(|value| value.as_dict().ok());
        let subtype = dict
            .and_then(|dict| dict.get(b"Subtype").ok())
            .and_then(|value| value.as_name().ok());
        let drop = match subtype {
            Some(b"Widget") => false,
            Some(b"Link") => {
                if options.links {
                    removed.links += 1;
                }
                options.links
            }
            Some(b"FileAttachment") if options.embedded_files => {
                removed.embedded_files += 1;
                true
            }
            // A popup goes with its parent, or alone with the comments.
            Some(b"Popup") => options.comments,
            _ => {
                if options.comments {
                    removed.comments += 1;
                }
                options.comments
            }
        };
        keep.push(!drop);
    }
    if keep.iter().all(|kept| *kept) {
        return Ok(());
    }
    let mut kept = Vec::new();
    for (item, keep) in items.into_iter().zip(keep) {
        if keep {
            kept.push(item);
        }
    }
    this.invalidate_hayro_pdf_keeping_placements();
    this.doc
        .set_page_object_entry(page_id, "Annots", Object::Array(kept))?;
    Ok(())
}
