//! Optional content: the groups of `/OCProperties` and their visibility.
//!
//! hayro-interpret 0.8 (`ocg.rs`) decides visibility from the default
//! configuration `/D` alone: with `/BaseState /OFF`, every reference that
//! `/OCGs` lists starts hidden, then the references of `/ON` are shown and
//! those of `/OFF` hidden, in that order; any other group is visible. Reads
//! follow that rule, and edits rewrite `/D` so that rendering and extraction
//! change with them.

use super::*;

/// The most references that `/OCGs`, a configuration array, or the radio
/// groups of one configuration may list (`LimitError` code `layer_count`).
pub(super) const MAX_LAYER_ENTRIES: usize = 65_536;

/// The most bytes that one group's name and intents may decode to
/// (`layer_text_size`).
pub(super) const MAX_LAYER_TEXT_BYTES: usize = 1024 * 1024;

/// The most intents a write takes, and the longest of them in bytes.
const MAX_LAYER_INTENTS: usize = 64;
const MAX_LAYER_INTENT_BYTES: usize = 127;

/// One group as `layer_info` returns it: name, intents, and visibility.
pub(super) type LayerInfo = (String, Vec<String>, bool);

fn alloc_err(what: &str, error: std::collections::TryReserveError) -> PyErr {
    PdfError::new_err(format!("failed to allocate {what}: {error}"))
}

fn stale_layer(id: ObjectId) -> PyErr {
    StaleObjectError::new_err(format!(
        "optional-content group {} {} R no longer exists",
        id.0, id.1
    ))
}

/// The group dictionary of `id`, or a stale-handle error.
fn group_dictionary(doc: &Document, id: ObjectId) -> PyResult<&Dictionary> {
    match doc.objects.get(&id) {
        Some(Object::Dictionary(group)) => Ok(group),
        _ => Err(stale_layer(id)),
    }
}

/// The catalog's `/OCProperties` dictionary, resolved as hayro resolves it.
fn oc_properties(doc: &Document) -> Option<&Dictionary> {
    let catalog = doc.catalog().ok()?;
    deref_dict_ref(doc, catalog.get(b"OCProperties").ok()?)
}

/// The default configuration `/D` of `properties`.
fn default_configuration<'a>(
    doc: &'a Document,
    properties: &'a Dictionary,
) -> Option<&'a Dictionary> {
    deref_dict_ref(doc, properties.get(b"D").ok()?)
}

/// The array of `object`, through one reference, as hayro's `get::<Array>`.
fn array_of<'a>(doc: &'a Document, object: &'a Object) -> Option<&'a [Object]> {
    match deref_object(doc, object) {
        Object::Array(items) => Some(items),
        _ => None,
    }
}

/// The references that `dict[key]` lists, in order; other elements are
/// skipped, as hayro's `as_obj_ref` skips them.
fn references(doc: &Document, dict: &Dictionary, key: &[u8]) -> PyResult<Vec<ObjectId>> {
    let Some(items) = dict.get(key).ok().and_then(|object| array_of(doc, object)) else {
        return Ok(Vec::new());
    };
    if items.len() > MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!(
                "optional-content array /{} lists {} entries; the limit is {MAX_LAYER_ENTRIES}",
                String::from_utf8_lossy(key),
                items.len()
            ),
        ));
    }
    let mut out = Vec::new();
    out.try_reserve_exact(items.len())
        .map_err(|error| alloc_err("optional-content references", error))?;
    out.extend(items.iter().filter_map(|item| item.as_reference().ok()));
    Ok(out)
}

/// Whether the default configuration starts every listed group hidden.
fn base_state_off(config: &Dictionary) -> bool {
    matches!(config.get(b"BaseState"), Ok(Object::Name(name)) if name == b"OFF")
}

/// Whether hayro shows group `id`: the rule of `OcgState::from_catalog`.
fn group_visible(doc: &Document, id: ObjectId) -> PyResult<bool> {
    let Some(properties) = oc_properties(doc) else {
        return Ok(true);
    };
    let Some(config) = default_configuration(doc, properties) else {
        return Ok(true);
    };
    let mut visible =
        !(base_state_off(config) && references(doc, properties, b"OCGs")?.contains(&id));
    if references(doc, config, b"ON")?.contains(&id) {
        visible = true;
    }
    if references(doc, config, b"OFF")?.contains(&id) {
        visible = false;
    }
    Ok(visible)
}

/// Implements [`_Document::layers`].
pub(super) fn layers(this: &_Document) -> PyResult<Vec<(u32, u16)>> {
    let doc = &this.doc;
    let Some(properties) = oc_properties(doc) else {
        return Ok(Vec::new());
    };
    let listed = references(doc, properties, b"OCGs")?;
    let mut seen = HashSet::new();
    seen.try_reserve(listed.len())
        .map_err(|error| alloc_err("optional-content group lookup", error))?;
    let mut out = Vec::new();
    out.try_reserve_exact(listed.len())
        .map_err(|error| alloc_err("optional-content groups", error))?;
    for id in listed {
        if matches!(doc.objects.get(&id), Some(Object::Dictionary(_))) && seen.insert(id) {
            out.push(id);
        }
    }
    Ok(out)
}

/// Decode `object` as a text string under the shared per-group budget.
fn charged_text(doc: &Document, object: &Object, used: &mut usize) -> PyResult<String> {
    let text = match deref_object(doc, object) {
        string @ Object::String(bytes, _) => {
            if bytes.len() > MAX_LAYER_TEXT_BYTES - *used {
                return Err(layer_text_err());
            }
            decode_text_string(string).unwrap_or_default()
        }
        Object::Name(name) => String::from_utf8_lossy(name).into_owned(),
        _ => String::new(),
    };
    *used += text.len();
    if *used > MAX_LAYER_TEXT_BYTES {
        return Err(layer_text_err());
    }
    Ok(text)
}

fn layer_text_err() -> PyErr {
    limit_err(
        "layer_text_size",
        format!("an optional-content group's name and intents exceed {MAX_LAYER_TEXT_BYTES} bytes"),
    )
}

/// The intents of `group` (`/Intent`, a name or an array of names), with
/// `View` when absent, as ISO 32000-2 Table 96 defaults it.
fn group_intents(doc: &Document, group: &Dictionary, used: &mut usize) -> PyResult<Vec<String>> {
    let mut out = Vec::new();
    match group.get(b"Intent").map(|object| deref_object(doc, object)) {
        Ok(Object::Array(items)) => {
            if items.len() > MAX_LAYER_ENTRIES {
                return Err(limit_err(
                    "layer_count",
                    format!("an optional-content group lists {} intents", items.len()),
                ));
            }
            out.try_reserve_exact(items.len())
                .map_err(|error| alloc_err("optional-content intents", error))?;
            for item in items {
                if let Object::Name(_) = deref_object(doc, item) {
                    out.push(charged_text(doc, item, used)?);
                }
            }
        }
        Ok(intent @ Object::Name(_)) => {
            out.try_reserve_exact(1)
                .map_err(|error| alloc_err("optional-content intents", error))?;
            out.push(charged_text(doc, intent, used)?);
        }
        _ => {
            out.try_reserve_exact(1)
                .map_err(|error| alloc_err("optional-content intents", error))?;
            out.push("View".to_owned());
        }
    }
    Ok(out)
}

/// Implements [`_Document::layer_info`].
pub(super) fn layer_info(this: &_Document, id: (u32, u16)) -> PyResult<LayerInfo> {
    let doc = &this.doc;
    let group = group_dictionary(doc, id)?;
    let mut used = 0;
    let name = match group.get(b"Name") {
        Ok(name) => charged_text(doc, name, &mut used)?,
        Err(_) => String::new(),
    };
    let intents = group_intents(doc, group, &mut used)?;
    Ok((name, intents, group_visible(doc, id)?))
}

/// Check a group name before any PDF work.
fn check_name(name: &str) -> PyResult<()> {
    if name.len() > MAX_LAYER_TEXT_BYTES {
        return Err(limit_err(
            "layer_input_size",
            format!(
                "an optional-content group name of {} bytes exceeds {MAX_LAYER_TEXT_BYTES}",
                name.len()
            ),
        ));
    }
    Ok(())
}

/// The `/Intent` value of `intents`, or `None` for the default `View`.
fn intent_object(intents: &[String]) -> PyResult<Option<Object>> {
    if intents.is_empty() || intents.len() > MAX_LAYER_INTENTS {
        return Err(PdfError::new_err(format!(
            "an optional-content group takes 1 to {MAX_LAYER_INTENTS} intents, not {}",
            intents.len()
        )));
    }
    for intent in intents {
        if intent.is_empty()
            || intent.len() > MAX_LAYER_INTENT_BYTES
            || !intent
                .bytes()
                .all(|byte| byte.is_ascii_graphic() && !b"()<>[]{}/%#".contains(&byte))
        {
            return Err(PdfError::new_err(format!(
                "an intent is a PDF name of 1 to {MAX_LAYER_INTENT_BYTES} printable ASCII non-delimiter bytes: {intent:?}"
            )));
        }
    }
    if let [only] = intents {
        if only == "View" {
            return Ok(None);
        }
        return Ok(Some(Object::Name(only.as_bytes().to_vec())));
    }
    let mut items = Vec::new();
    items
        .try_reserve_exact(intents.len())
        .map_err(|error| alloc_err("optional-content intents", error))?;
    items.extend(
        intents
            .iter()
            .map(|intent| Object::Name(intent.as_bytes().to_vec())),
    );
    Ok(Some(Object::Array(items)))
}

/// A planned `/OCProperties`: the dictionary and where each indirect part
/// of it lives, so that a commit only stores prepared values.
struct PropertiesPlan {
    /// The object that holds `/OCProperties`, or `None` for the catalog.
    properties_id: Option<ObjectId>,
    properties: Dictionary,
    ocgs_id: Option<ObjectId>,
    ocgs: Vec<Object>,
    /// The planned configuration: 0 for `/D`, `n` for `/Configs[n - 1]`.
    slot: usize,
    config_id: Option<ObjectId>,
    config: Dictionary,
    configs_id: Option<ObjectId>,
    configs: Vec<Object>,
}

impl PropertiesPlan {
    /// Read the current `/OCProperties`, `/OCGs`, and `/D`, or start empty
    /// ones; arrays inside `/D` are read later and stored back direct.
    fn read(doc: &Document) -> PyResult<Self> {
        let catalog = doc.catalog().map_err(to_py_err)?;
        let (properties_id, properties) = match catalog.get(b"OCProperties") {
            Ok(Object::Reference(id)) => match doc.objects.get(id) {
                Some(Object::Dictionary(dict)) => (Some(*id), dict.clone()),
                _ => (None, Dictionary::new()),
            },
            Ok(Object::Dictionary(dict)) => (None, dict.clone()),
            _ => (None, Dictionary::new()),
        };
        let (ocgs_id, ocgs) = match properties.get(b"OCGs") {
            Ok(Object::Reference(id)) => match doc.objects.get(id) {
                Some(Object::Array(items)) => (Some(*id), items.clone()),
                _ => (None, Vec::new()),
            },
            Ok(Object::Array(items)) => (None, items.clone()),
            _ => (None, Vec::new()),
        };
        if ocgs.len() > MAX_LAYER_ENTRIES {
            return Err(limit_err(
                "layer_count",
                format!(
                    "/OCGs lists {} entries; the limit is {MAX_LAYER_ENTRIES}",
                    ocgs.len()
                ),
            ));
        }
        let (config_id, config) = dictionary_slot(doc, properties.get(b"D").ok());
        let (configs_id, configs) = match properties.get(b"Configs") {
            Ok(Object::Reference(id)) => match doc.objects.get(id) {
                Some(Object::Array(items)) => (Some(*id), items.clone()),
                _ => (None, Vec::new()),
            },
            Ok(Object::Array(items)) => (None, items.clone()),
            _ => (None, Vec::new()),
        };
        if configs.len() > MAX_LAYER_ENTRIES {
            return Err(limit_err(
                "layer_count",
                format!(
                    "/Configs lists {} entries; the limit is {MAX_LAYER_ENTRIES}",
                    configs.len()
                ),
            ));
        }
        Ok(Self {
            properties_id,
            properties,
            ocgs_id,
            ocgs,
            slot: 0,
            config_id,
            config,
            configs_id,
            configs,
        })
    }

    /// Read the plan with configuration `slot` planned instead of `/D`.
    fn read_slot(doc: &Document, slot: usize) -> PyResult<Self> {
        let mut plan = Self::read(doc)?;
        if slot == 0 {
            return Ok(plan);
        }
        let Some(entry) = plan.configs.get(slot - 1) else {
            return Err(stale_configuration(slot));
        };
        let (config_id, config) = dictionary_slot(doc, Some(entry));
        if config_id.is_none() && !matches!(entry, Object::Dictionary(_)) {
            return Err(stale_configuration(slot));
        }
        plan.slot = slot;
        plan.config_id = config_id;
        plan.config = config;
        Ok(plan)
    }

    fn lists(&self, id: ObjectId) -> bool {
        self.ocgs
            .iter()
            .any(|item| item.as_reference().ok() == Some(id))
    }

    /// Append `id` to `/OCGs` unless it is listed.
    fn list(&mut self, id: ObjectId) -> PyResult<()> {
        if self.lists(id) {
            return Ok(());
        }
        if self.ocgs.len() >= MAX_LAYER_ENTRIES {
            return Err(limit_err(
                "layer_count",
                format!("/OCGs already lists {MAX_LAYER_ENTRIES} groups"),
            ));
        }
        self.ocgs
            .try_reserve(1)
            .map_err(|error| alloc_err("optional-content groups", error))?;
        self.ocgs.push(Object::Reference(id));
        Ok(())
    }

    /// The references of the configuration array `key`, read through the
    /// document and detached for a direct write.
    fn config_refs(&self, doc: &Document, key: &[u8]) -> PyResult<Vec<ObjectId>> {
        references(doc, &self.config, key)
    }

    /// Whether `id` is visible under the planned `/D`, by hayro's rule.
    fn visible(&self, on: &[ObjectId], off: &[ObjectId], id: ObjectId) -> bool {
        if off.contains(&id) {
            return false;
        }
        if on.contains(&id) {
            return true;
        }
        !(base_state_off(&self.config) && self.lists(id))
    }

    /// Store the plan; every value is prepared, so this cannot fail.
    fn commit(self, doc: &mut Document) {
        let Self {
            properties_id,
            mut properties,
            ocgs_id,
            ocgs,
            slot,
            config_id,
            config,
            configs_id,
            mut configs,
        } = self;
        match ocgs_id {
            Some(id) => {
                doc.objects.insert(id, Object::Array(ocgs));
            }
            None => properties.set("OCGs", Object::Array(ocgs)),
        }
        match (config_id, slot) {
            (Some(id), _) => {
                doc.objects.insert(id, Object::Dictionary(config));
            }
            (None, 0) => properties.set("D", Object::Dictionary(config)),
            (None, slot) => configs[slot - 1] = Object::Dictionary(config),
        }
        if !configs.is_empty() || properties.has(b"Configs") {
            match configs_id {
                Some(id) => {
                    doc.objects.insert(id, Object::Array(configs));
                }
                None => properties.set("Configs", Object::Array(configs)),
            }
        }
        match properties_id {
            Some(id) => {
                doc.objects.insert(id, Object::Dictionary(properties));
            }
            None => {
                if let Ok(catalog) = doc.catalog_mut() {
                    catalog.set("OCProperties", Object::Dictionary(properties));
                }
            }
        }
    }
}

/// A dictionary stored directly or behind one reference, with the object
/// that holds it; anything else plans an empty dictionary.
fn dictionary_slot(doc: &Document, object: Option<&Object>) -> (Option<ObjectId>, Dictionary) {
    match object {
        Some(Object::Reference(id)) => match doc.objects.get(id) {
            Some(Object::Dictionary(dict)) => (Some(*id), dict.clone()),
            _ => (None, Dictionary::new()),
        },
        Some(Object::Dictionary(dict)) => (None, dict.clone()),
        _ => (None, Dictionary::new()),
    }
}

fn stale_configuration(slot: usize) -> PyErr {
    StaleObjectError::new_err(format!(
        "optional-content configuration {slot} no longer exists"
    ))
}

/// A reference array of `ids`, allocated fallibly.
fn reference_array(ids: &[ObjectId]) -> PyResult<Object> {
    let mut items = Vec::new();
    items
        .try_reserve_exact(ids.len())
        .map_err(|error| alloc_err("optional-content references", error))?;
    items.extend(ids.iter().map(|id| Object::Reference(*id)));
    Ok(Object::Array(items))
}

/// Push `id` once onto `ids` within the entry limit.
fn push_reference(ids: &mut Vec<ObjectId>, id: ObjectId) -> PyResult<()> {
    if ids.contains(&id) {
        return Ok(());
    }
    if ids.len() >= MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!("an optional-content array already lists {MAX_LAYER_ENTRIES} groups"),
        ));
    }
    ids.try_reserve(1)
        .map_err(|error| alloc_err("optional-content references", error))?;
    ids.push(id);
    Ok(())
}

/// Plan `/ON` and `/OFF` so that `id` gets `visible`: remove it from both,
/// then list it where the base state disagrees.
fn plan_visibility(
    plan: &PropertiesPlan,
    on: &mut Vec<ObjectId>,
    off: &mut Vec<ObjectId>,
    id: ObjectId,
    visible: bool,
) -> PyResult<()> {
    on.retain(|item| *item != id);
    off.retain(|item| *item != id);
    let base_visible = !(base_state_off(&plan.config) && plan.lists(id));
    if visible && !base_visible {
        push_reference(on, id)?;
    } else if !visible && base_visible {
        push_reference(off, id)?;
    }
    Ok(())
}

/// The radio-button groups of the planned `/D`, each a list of references.
fn radio_groups(doc: &Document, config: &Dictionary) -> PyResult<Vec<Vec<ObjectId>>> {
    let Some(groups) = config
        .get(b"RBGroups")
        .ok()
        .and_then(|object| array_of(doc, object))
    else {
        return Ok(Vec::new());
    };
    if groups.len() > MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!("/RBGroups lists {} groups", groups.len()),
        ));
    }
    let mut out = Vec::new();
    out.try_reserve_exact(groups.len())
        .map_err(|error| alloc_err("radio-button groups", error))?;
    let mut total = 0usize;
    for group in groups {
        let Some(items) = array_of(doc, group) else {
            continue;
        };
        total = total.saturating_add(items.len());
        if total > MAX_LAYER_ENTRIES {
            return Err(limit_err(
                "layer_count",
                format!("/RBGroups lists more than {MAX_LAYER_ENTRIES} references"),
            ));
        }
        let mut ids = Vec::new();
        ids.try_reserve_exact(items.len())
            .map_err(|error| alloc_err("radio-button groups", error))?;
        ids.extend(items.iter().filter_map(|item| item.as_reference().ok()));
        out.push(ids);
    }
    Ok(out)
}

/// Implements [`_Document::set_layer_visible`].
pub(super) fn set_layer_visible(
    this: &mut _Document,
    id: (u32, u16),
    visible: bool,
) -> PyResult<()> {
    let doc = &this.doc;
    group_dictionary(doc, id)?;
    let mut plan = PropertiesPlan::read(doc)?;
    plan.list(id)?;
    let mut on = plan.config_refs(doc, b"ON")?;
    let mut off = plan.config_refs(doc, b"OFF")?;
    let before = (on.clone(), off.clone(), plan.ocgs.len());
    if plan.visible(&on, &off, id) != visible {
        plan_visibility(&plan, &mut on, &mut off, id, visible)?;
    }
    if visible {
        // A viewer turns the other groups of a radio-button group off.
        for group in radio_groups(doc, &plan.config)? {
            if !group.contains(&id) {
                continue;
            }
            for &other in &group {
                if other != id && plan.visible(&on, &off, other) {
                    plan_visibility(&plan, &mut on, &mut off, other, false)?;
                }
            }
        }
    }
    if before == (on.clone(), off.clone(), plan.ocgs.len()) {
        return Ok(());
    }
    if on.is_empty() {
        plan.config.remove(b"ON");
    } else {
        plan.config.set("ON", reference_array(&on)?);
    }
    if off.is_empty() {
        plan.config.remove(b"OFF");
    } else {
        plan.config.set("OFF", reference_array(&off)?);
    }
    this.invalidate_hayro_pdf_keeping_placements();
    plan.commit(&mut this.doc);
    Ok(())
}

/// Implements [`_Document::add_layer`].
pub(super) fn add_layer(
    this: &mut _Document,
    name: &str,
    intents: Vec<String>,
    visible: bool,
) -> PyResult<(u32, u16)> {
    check_name(name)?;
    let intent = intent_object(&intents)?;
    let title = try_info_metadata_text_string(name)?;
    let doc = &this.doc;
    let mut plan = PropertiesPlan::read(doc)?;
    let fresh = oc_properties(doc).is_none();
    let id = (
        doc.max_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?,
        0,
    );
    plan.list(id)?;
    let mut on = plan.config_refs(doc, b"ON")?;
    let mut off = plan.config_refs(doc, b"OFF")?;
    plan_visibility(&plan, &mut on, &mut off, id, visible)?;
    let mut order = match plan
        .config
        .get(b"Order")
        .ok()
        .and_then(|object| array_of(doc, object))
    {
        Some(items) => {
            let mut copy = Vec::new();
            copy.try_reserve_exact(items.len() + 1)
                .map_err(|error| alloc_err("optional-content order", error))?;
            copy.extend_from_slice(items);
            Some(copy)
        }
        None if fresh => Some(Vec::new()),
        None => None,
    };
    if let Some(order) = &mut order {
        if order.len() >= MAX_LAYER_ENTRIES {
            return Err(limit_err(
                "layer_count",
                format!("/Order already lists {MAX_LAYER_ENTRIES} entries"),
            ));
        }
        order
            .try_reserve(1)
            .map_err(|error| alloc_err("optional-content order", error))?;
        order.push(Object::Reference(id));
    }
    let on = if on.is_empty() {
        None
    } else {
        Some(reference_array(&on)?)
    };
    let off = if off.is_empty() {
        None
    } else {
        Some(reference_array(&off)?)
    };
    let mut group = Dictionary::new();
    group.set("Type", Object::Name(b"OCG".to_vec()));
    group.set("Name", title);
    if let Some(intent) = intent {
        group.set("Intent", intent);
    }
    if let Some(order) = order {
        plan.config.set("Order", Object::Array(order));
    }
    match on {
        Some(on) => plan.config.set("ON", on),
        None => {
            plan.config.remove(b"ON");
        }
    }
    match off {
        Some(off) => plan.config.set("OFF", off),
        None => {
            plan.config.remove(b"OFF");
        }
    }
    this.invalidate_hayro_pdf_keeping_placements();
    let added = this.doc.add_object(Object::Dictionary(group));
    debug_assert_eq!(added, id);
    plan.commit(&mut this.doc);
    Ok(id)
}

/// Implements [`_Document::set_layer_name`].
pub(super) fn set_layer_name(this: &mut _Document, id: (u32, u16), name: &str) -> PyResult<()> {
    check_name(name)?;
    let title = try_info_metadata_text_string(name)?;
    group_dictionary(&this.doc, id)?;
    this.invalidate_hayro_pdf_keeping_placements();
    if let Some(Object::Dictionary(group)) = this.doc.objects.get_mut(&id) {
        group.set("Name", title);
    }
    Ok(())
}

/// Implements [`_Document::set_layer_intent`].
pub(super) fn set_layer_intent(
    this: &mut _Document,
    id: (u32, u16),
    intents: Vec<String>,
) -> PyResult<()> {
    let intent = intent_object(&intents)?;
    group_dictionary(&this.doc, id)?;
    this.invalidate_hayro_pdf_keeping_placements();
    if let Some(Object::Dictionary(group)) = this.doc.objects.get_mut(&id) {
        match intent {
            Some(intent) => group.set("Intent", intent),
            None => {
                group.remove(b"Intent");
            }
        }
    }
    Ok(())
}

/// The deepest `/Order` nesting that reads and writes take.
const MAX_ORDER_DEPTH: u32 = 32;

/// One entry of a configuration's `/Order` tree in preorder: its depth,
/// whether it is a nested array, the group of a layer entry (`(0, 0)` for
/// an array), and the label of a labeled array.
pub(super) type OrderEntry = (u32, bool, (u32, u16), Option<String>);

/// One configuration as `layer_configuration` returns it: name, creator,
/// base state, `/ON`, `/OFF`, `/Locked`, `/RBGroups`, and `/Order`.
pub(super) type ConfigurationInfo = (
    Option<String>,
    Option<String>,
    String,
    Vec<(u32, u16)>,
    Vec<(u32, u16)>,
    Vec<(u32, u16)>,
    Vec<Vec<(u32, u16)>>,
    Vec<OrderEntry>,
);

/// Implements [`_Document::layer_configuration_count`].
pub(super) fn layer_configuration_count(this: &_Document) -> PyResult<usize> {
    let doc = &this.doc;
    let Some(properties) = oc_properties(doc) else {
        return Ok(0);
    };
    let alternates = match properties
        .get(b"Configs")
        .ok()
        .and_then(|object| array_of(doc, object))
    {
        Some(items) => items.len(),
        None => 0,
    };
    if alternates > MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!("/Configs lists {alternates} entries; the limit is {MAX_LAYER_ENTRIES}"),
        ));
    }
    Ok(1 + alternates)
}

/// The configuration dictionary of `slot` as it stands, or a stale error.
fn configuration(doc: &Document, slot: usize) -> PyResult<Option<&Dictionary>> {
    let Some(properties) = oc_properties(doc) else {
        return Err(stale_configuration(slot));
    };
    if slot == 0 {
        return Ok(default_configuration(doc, properties));
    }
    let entry = properties
        .get(b"Configs")
        .ok()
        .and_then(|object| array_of(doc, object))
        .and_then(|items| items.get(slot - 1))
        .ok_or_else(|| stale_configuration(slot))?;
    match deref_dict_ref(doc, entry) {
        Some(config) => Ok(Some(config)),
        None => Err(stale_configuration(slot)),
    }
}

/// Read the `/Order` tree of `config` in preorder, cycle-aware.
fn read_order(doc: &Document, config: &Dictionary, used: &mut usize) -> PyResult<Vec<OrderEntry>> {
    let mut out: Vec<OrderEntry> = Vec::new();
    let Some(root) = config
        .get(b"Order")
        .ok()
        .and_then(|object| array_of(doc, object))
    else {
        return Ok(out);
    };
    let mut visited = HashSet::new();
    let mut stack: Vec<(&[Object], u32)> = Vec::new();
    stack
        .try_reserve(1)
        .map_err(|error| alloc_err("optional-content order", error))?;
    stack.push((root, 0));
    while let Some((items, depth)) = stack.pop() {
        let Some((first, rest)) = items.split_first() else {
            continue;
        };
        stack
            .try_reserve(1)
            .map_err(|error| alloc_err("optional-content order", error))?;
        stack.push((rest, depth));
        let mut nested = None;
        match first {
            Object::Reference(id) => match doc.objects.get(id) {
                Some(Object::Dictionary(_)) => {
                    push_order(&mut out, (depth, false, *id, None))?;
                }
                Some(Object::Array(items)) => {
                    visited
                        .try_reserve(1)
                        .map_err(|error| alloc_err("optional-content order", error))?;
                    if visited.insert(*id) {
                        nested = Some(items.as_slice());
                    }
                }
                _ => {}
            },
            Object::Array(items) => nested = Some(items.as_slice()),
            _ => {}
        }
        let Some(children) = nested else {
            continue;
        };
        if depth + 1 > MAX_ORDER_DEPTH {
            return Err(limit_err(
                "layer_count",
                format!("/Order nests more than {MAX_ORDER_DEPTH} levels"),
            ));
        }
        let (label, children) = match children.split_first() {
            Some((label @ Object::String(..), rest)) => {
                (Some(charged_text(doc, label, used)?), rest)
            }
            _ => (None, children),
        };
        push_order(&mut out, (depth, true, (0, 0), label))?;
        stack
            .try_reserve(1)
            .map_err(|error| alloc_err("optional-content order", error))?;
        stack.push((children, depth + 1));
    }
    Ok(out)
}

fn push_order(out: &mut Vec<OrderEntry>, entry: OrderEntry) -> PyResult<()> {
    if out.len() >= MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!("/Order lists more than {MAX_LAYER_ENTRIES} entries"),
        ));
    }
    out.try_reserve(1)
        .map_err(|error| alloc_err("optional-content order", error))?;
    out.push(entry);
    Ok(())
}

/// Implements [`_Document::layer_configuration`].
pub(super) fn layer_configuration(this: &_Document, slot: usize) -> PyResult<ConfigurationInfo> {
    let doc = &this.doc;
    let empty = Dictionary::new();
    let config = configuration(doc, slot)?.unwrap_or(&empty);
    let mut used = 0;
    let mut text = |key: &[u8]| -> PyResult<Option<String>> {
        match config.get(key) {
            Ok(object) if matches!(deref_object(doc, object), Object::String(..)) => {
                Ok(Some(charged_text(doc, object, &mut used)?))
            }
            _ => Ok(None),
        }
    };
    let name = text(b"Name")?;
    let creator = text(b"Creator")?;
    let base_state = match config
        .get(b"BaseState")
        .map(|object| deref_object(doc, object))
    {
        Ok(Object::Name(name)) if name == b"OFF" => "OFF",
        Ok(Object::Name(name)) if name == b"Unchanged" => "Unchanged",
        _ => "ON",
    };
    let on = references(doc, config, b"ON")?;
    let off = references(doc, config, b"OFF")?;
    let locked = references(doc, config, b"Locked")?;
    let radio = radio_groups(doc, config)?;
    let order = read_order(doc, config, &mut used)?;
    Ok((
        name,
        creator,
        base_state.to_owned(),
        on,
        off,
        locked,
        radio,
        order,
    ))
}

/// Check that every reference names a group dictionary.
fn check_groups(doc: &Document, ids: &[(u32, u16)]) -> PyResult<()> {
    if ids.len() > MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!(
                "{} groups exceed the limit of {MAX_LAYER_ENTRIES}",
                ids.len()
            ),
        ));
    }
    for &id in ids {
        group_dictionary(doc, id)?;
    }
    Ok(())
}

/// Store a planned configuration edit.
fn commit_configuration(this: &mut _Document, plan: PropertiesPlan, before: &Dictionary) {
    if &plan.config == before {
        return;
    }
    this.invalidate_hayro_pdf_keeping_placements();
    plan.commit(&mut this.doc);
}

/// Implements [`_Document::set_layer_configuration_text`].
pub(super) fn set_layer_configuration_text(
    this: &mut _Document,
    slot: usize,
    key: &str,
    value: Option<&str>,
) -> PyResult<()> {
    if !matches!(key, "Name" | "Creator") {
        return Err(PdfError::new_err(format!(
            "unsupported configuration entry: {key:?}"
        )));
    }
    let value = match value {
        Some(value) => {
            check_name(value)?;
            Some(try_info_metadata_text_string(value)?)
        }
        None => None,
    };
    configuration(&this.doc, slot)?;
    let mut plan = PropertiesPlan::read_slot(&this.doc, slot)?;
    let before = plan.config.clone();
    match value {
        Some(value) => plan.config.set(key, value),
        None => {
            plan.config.remove(key.as_bytes());
        }
    }
    commit_configuration(this, plan, &before);
    Ok(())
}

/// Implements [`_Document::set_layer_configuration_base_state`].
pub(super) fn set_layer_configuration_base_state(
    this: &mut _Document,
    slot: usize,
    state: &str,
) -> PyResult<()> {
    if !matches!(state, "ON" | "OFF" | "Unchanged") {
        return Err(PdfError::new_err(format!("bad base state: {state:?}")));
    }
    configuration(&this.doc, slot)?;
    let mut plan = PropertiesPlan::read_slot(&this.doc, slot)?;
    let before = plan.config.clone();
    plan.config
        .set("BaseState", Object::Name(state.as_bytes().to_vec()));
    commit_configuration(this, plan, &before);
    Ok(())
}

/// Implements [`_Document::set_layer_configuration_refs`].
pub(super) fn set_layer_configuration_refs(
    this: &mut _Document,
    slot: usize,
    key: &str,
    ids: Vec<(u32, u16)>,
) -> PyResult<()> {
    if !matches!(key, "ON" | "OFF" | "Locked") {
        return Err(PdfError::new_err(format!(
            "unsupported configuration array: {key:?}"
        )));
    }
    check_groups(&this.doc, &ids)?;
    configuration(&this.doc, slot)?;
    let mut plan = PropertiesPlan::read_slot(&this.doc, slot)?;
    let before = plan.config.clone();
    if ids.is_empty() {
        plan.config.remove(key.as_bytes());
    } else {
        plan.config.set(key, reference_array(&ids)?);
    }
    commit_configuration(this, plan, &before);
    Ok(())
}

/// Implements [`_Document::set_layer_configuration_radio_groups`].
pub(super) fn set_layer_configuration_radio_groups(
    this: &mut _Document,
    slot: usize,
    groups: Vec<Vec<(u32, u16)>>,
) -> PyResult<()> {
    let total = groups.iter().map(Vec::len).sum::<usize>();
    if groups.len() > MAX_LAYER_ENTRIES || total > MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!("radio-button groups exceed the limit of {MAX_LAYER_ENTRIES} groups"),
        ));
    }
    for group in &groups {
        check_groups(&this.doc, group)?;
    }
    configuration(&this.doc, slot)?;
    let mut plan = PropertiesPlan::read_slot(&this.doc, slot)?;
    let before = plan.config.clone();
    if groups.is_empty() {
        plan.config.remove(b"RBGroups");
    } else {
        let mut items = Vec::new();
        items
            .try_reserve_exact(groups.len())
            .map_err(|error| alloc_err("radio-button groups", error))?;
        for group in &groups {
            items.push(reference_array(group)?);
        }
        plan.config.set("RBGroups", Object::Array(items));
    }
    commit_configuration(this, plan, &before);
    Ok(())
}

/// Build `/Order` from preorder entries.
fn order_object(doc: &Document, entries: &[OrderEntry]) -> PyResult<Object> {
    if entries.len() > MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!(
                "/Order of {} entries exceeds {MAX_LAYER_ENTRIES}",
                entries.len()
            ),
        ));
    }
    let mut text = 0usize;
    let mut stack: Vec<Vec<Object>> = Vec::new();
    stack
        .try_reserve(MAX_ORDER_DEPTH as usize + 1)
        .map_err(|error| alloc_err("optional-content order", error))?;
    stack.push(Vec::new());
    for (depth, is_group, id, label) in entries {
        let depth = *depth as usize;
        // An entry nests at most one level below an array opened just before.
        if depth + 1 > stack.len() {
            return Err(PdfError::new_err(
                "an /Order entry can only nest inside the array before it",
            ));
        }
        while stack.len() > depth + 1 {
            let array = stack.pop().unwrap_or_default();
            if let Some(parent) = stack.last_mut() {
                parent
                    .try_reserve(1)
                    .map_err(|error| alloc_err("optional-content order", error))?;
                parent.push(Object::Array(array));
            }
        }
        if *is_group {
            if depth as u32 + 1 > MAX_ORDER_DEPTH {
                return Err(limit_err(
                    "layer_count",
                    format!("/Order nests more than {MAX_ORDER_DEPTH} levels"),
                ));
            }
            let mut array = Vec::new();
            if let Some(label) = label {
                text = text.saturating_add(label.len());
                if text > MAX_LAYER_TEXT_BYTES {
                    return Err(limit_err(
                        "layer_input_size",
                        format!("/Order labels exceed {MAX_LAYER_TEXT_BYTES} bytes"),
                    ));
                }
                array
                    .try_reserve(1)
                    .map_err(|error| alloc_err("optional-content order", error))?;
                array.push(try_info_metadata_text_string(label)?);
            }
            stack.push(array);
        } else {
            group_dictionary(doc, *id)?;
            let current = stack
                .last_mut()
                .ok_or_else(|| PdfError::new_err("empty order stack"))?;
            current
                .try_reserve(1)
                .map_err(|error| alloc_err("optional-content order", error))?;
            current.push(Object::Reference(*id));
        }
    }
    while stack.len() > 1 {
        let array = stack.pop().unwrap_or_default();
        if let Some(parent) = stack.last_mut() {
            parent
                .try_reserve(1)
                .map_err(|error| alloc_err("optional-content order", error))?;
            parent.push(Object::Array(array));
        }
    }
    Ok(Object::Array(stack.pop().unwrap_or_default()))
}

/// Implements [`_Document::set_layer_configuration_order`].
pub(super) fn set_layer_configuration_order(
    this: &mut _Document,
    slot: usize,
    entries: Vec<OrderEntry>,
) -> PyResult<()> {
    let order = order_object(&this.doc, &entries)?;
    configuration(&this.doc, slot)?;
    let mut plan = PropertiesPlan::read_slot(&this.doc, slot)?;
    let before = plan.config.clone();
    if entries.is_empty() {
        plan.config.remove(b"Order");
    } else {
        plan.config.set("Order", order);
    }
    commit_configuration(this, plan, &before);
    Ok(())
}

/// Implements [`_Document::apply_layer_configuration`].
pub(super) fn apply_layer_configuration(this: &mut _Document, slot: usize) -> PyResult<()> {
    let source = configuration(&this.doc, slot)?.cloned().unwrap_or_default();
    if slot == 0 {
        return Ok(());
    }
    let mut plan = PropertiesPlan::read(&this.doc)?;
    let before = plan.config.clone();
    let mut config = source;
    if !config.has(b"Order")
        && let Ok(order) = before.get(b"Order")
    {
        config.set("Order", order.clone());
    }
    plan.config = config;
    commit_configuration(this, plan, &before);
    Ok(())
}

/// Implements [`_Document::add_layer_configuration`].
pub(super) fn add_layer_configuration(
    this: &mut _Document,
    name: &str,
    creator: Option<&str>,
    base_state: &str,
    on: Vec<(u32, u16)>,
    off: Vec<(u32, u16)>,
) -> PyResult<usize> {
    check_name(name)?;
    if let Some(creator) = creator {
        check_name(creator)?;
    }
    if !matches!(base_state, "ON" | "OFF" | "Unchanged") {
        return Err(PdfError::new_err(format!("bad base state: {base_state:?}")));
    }
    check_groups(&this.doc, &on)?;
    check_groups(&this.doc, &off)?;
    let mut config = Dictionary::new();
    config.set("Name", try_info_metadata_text_string(name)?);
    if let Some(creator) = creator {
        config.set("Creator", try_info_metadata_text_string(creator)?);
    }
    if base_state != "ON" {
        config.set("BaseState", Object::Name(base_state.as_bytes().to_vec()));
    }
    if !on.is_empty() {
        config.set("ON", reference_array(&on)?);
    }
    if !off.is_empty() {
        config.set("OFF", reference_array(&off)?);
    }
    let mut plan = PropertiesPlan::read(&this.doc)?;
    if plan.configs.len() >= MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!("/Configs already lists {MAX_LAYER_ENTRIES} configurations"),
        ));
    }
    plan.configs
        .try_reserve(1)
        .map_err(|error| alloc_err("optional-content configurations", error))?;
    plan.configs.push(Object::Dictionary(config));
    let slot = plan.configs.len();
    this.invalidate_hayro_pdf_keeping_placements();
    plan.commit(&mut this.doc);
    Ok(slot)
}

/// One operand of a visibility expression in preorder: its depth, its
/// operator (`"and"`, `"or"`, `"not"`, or `""` for a group), and the group.
pub(crate) type ExpressionEntry = (u32, String, (u32, u16));

/// An `/OC` value as it crosses the boundary: kind (0 none, 1 group, 2
/// membership dictionary), the group, the policy, the groups, and the
/// visibility expression in preorder.
pub(crate) type LayerValue = (
    u8,
    (u32, u16),
    String,
    Vec<(u32, u16)>,
    Vec<ExpressionEntry>,
);

fn no_layer() -> LayerValue {
    (0, (0, 0), String::new(), Vec::new(), Vec::new())
}

const POLICIES: [&str; 4] = ["AllOn", "AnyOn", "AnyOff", "AllOff"];

/// Read a membership dictionary as hayro's `begin_ocmd` reads `/OCGs` and
/// `/P`, plus its visibility expression `/VE`.
fn read_membership(doc: &Document, membership: &Dictionary) -> PyResult<LayerValue> {
    let policy = match membership.get(b"P").map(|object| deref_object(doc, object)) {
        Ok(Object::Name(name)) => POLICIES
            .iter()
            .find(|policy| policy.as_bytes() == name.as_slice())
            .copied()
            .unwrap_or("AnyOn"),
        _ => "AnyOn",
    };
    let groups = match membership.get(b"OCGs") {
        Ok(Object::Reference(id)) if matches!(doc.objects.get(id), Some(Object::Dictionary(_))) => {
            vec![*id]
        }
        _ => references(doc, membership, b"OCGs")?,
    };
    let expression = match membership
        .get(b"VE")
        .ok()
        .and_then(|object| array_of(doc, object))
    {
        Some(items) => read_expression(doc, items)?,
        None => Vec::new(),
    };
    Ok((2, (0, 0), policy.to_owned(), groups, expression))
}

/// A pending node of a visibility expression: an array, or one group.
enum ExpressionFrame<'a> {
    Array(&'a [Object], u32, Option<ObjectId>),
    Group(ObjectId, u32),
    Leave(ObjectId),
}

/// Read a visibility expression in preorder, cycle-aware and bounded.
fn read_expression(doc: &Document, root: &[Object]) -> PyResult<Vec<ExpressionEntry>> {
    let mut out: Vec<ExpressionEntry> = Vec::new();
    let mut visited = HashSet::new();
    let mut stack = Vec::new();
    stack
        .try_reserve(1)
        .map_err(|error| alloc_err("visibility expression", error))?;
    stack.push(ExpressionFrame::Array(root, 0, None));
    while let Some(frame) = stack.pop() {
        let (items, depth, id) = match frame {
            ExpressionFrame::Leave(id) => {
                visited.remove(&id);
                continue;
            }
            ExpressionFrame::Group(id, depth) => {
                push_expression(&mut out, (depth, String::new(), id))?;
                continue;
            }
            ExpressionFrame::Array(items, depth, id) => (items, depth, id),
        };
        if depth > MAX_ORDER_DEPTH {
            return Err(limit_err(
                "layer_count",
                format!("a visibility expression nests more than {MAX_ORDER_DEPTH} levels"),
            ));
        }
        let Some((Object::Name(operator), operands)) = items.split_first() else {
            continue;
        };
        let operator = match operator.as_slice() {
            b"And" => "and",
            b"Or" => "or",
            b"Not" => "not",
            _ => continue,
        };
        if let Some(id) = id {
            visited
                .try_reserve(1)
                .map_err(|error| alloc_err("visibility expression", error))?;
            if !visited.insert(id) {
                return Err(PdfError::new_err(
                    "a visibility expression contains a cycle",
                ));
            }
        }
        push_expression(&mut out, (depth, operator.to_owned(), (0, 0)))?;
        if operands.len() > MAX_LAYER_ENTRIES.saturating_sub(stack.len()) {
            return Err(limit_err(
                "layer_count",
                "too many visibility expression operands",
            ));
        }
        stack
            .try_reserve(operands.len() + usize::from(id.is_some()))
            .map_err(|error| alloc_err("visibility expression", error))?;
        if let Some(id) = id {
            stack.push(ExpressionFrame::Leave(id));
        }
        // Push in reverse so that the operands pop in order.
        for operand in operands.iter().rev() {
            match operand {
                Object::Reference(id) => match doc.objects.get(id) {
                    Some(Object::Dictionary(_)) => {
                        stack.push(ExpressionFrame::Group(*id, depth + 1))
                    }
                    Some(Object::Array(items)) => {
                        stack.push(ExpressionFrame::Array(items, depth + 1, Some(*id)));
                    }
                    _ => {}
                },
                Object::Array(items) => stack.push(ExpressionFrame::Array(items, depth + 1, None)),
                _ => {}
            }
        }
    }
    Ok(out)
}

fn push_expression(out: &mut Vec<ExpressionEntry>, entry: ExpressionEntry) -> PyResult<()> {
    if out.len() >= MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!("a visibility expression lists more than {MAX_LAYER_ENTRIES} entries"),
        ));
    }
    out.try_reserve(1)
        .map_err(|error| alloc_err("visibility expression", error))?;
    out.push(entry);
    Ok(())
}

/// Read the `/OC` entry of `holder` as hayro's `xobject_oc` reads it: a
/// reference to a membership dictionary or to a group, or a direct
/// membership dictionary.
fn read_oc(doc: &Document, holder: &Dictionary) -> PyResult<LayerValue> {
    match holder.get(b"OC") {
        Ok(Object::Reference(id)) => match doc.objects.get(id) {
            Some(Object::Dictionary(dict)) if is_membership(dict) => read_membership(doc, dict),
            Some(Object::Dictionary(_)) => Ok((1, *id, String::new(), Vec::new(), Vec::new())),
            _ => Ok(no_layer()),
        },
        Ok(Object::Dictionary(dict)) => read_membership(doc, dict),
        _ => Ok(no_layer()),
    }
}

fn is_membership(dict: &Dictionary) -> bool {
    matches!(dict.get(b"Type"), Ok(Object::Name(name)) if name == b"OCMD")
}

/// A checked `/OC` value, ready to store.
pub(super) enum PreparedLayer {
    Remove,
    Group(ObjectId),
    Membership(Dictionary),
}

/// Build a visibility expression array from preorder entries.
fn expression_object(doc: &Document, entries: &[ExpressionEntry]) -> PyResult<Object> {
    if entries.len() > MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!(
                "a visibility expression of {} entries exceeds {MAX_LAYER_ENTRIES}",
                entries.len()
            ),
        ));
    }
    let mut stack: Vec<Vec<Object>> = Vec::new();
    stack
        .try_reserve(MAX_ORDER_DEPTH as usize + 2)
        .map_err(|error| alloc_err("visibility expression", error))?;
    let mut closed: Option<Object> = None;
    for (index, (depth, operator, id)) in entries.iter().enumerate() {
        let depth = *depth as usize;
        let valid = if index == 0 {
            depth == 0 && !operator.is_empty()
        } else {
            depth >= 1 && depth <= stack.len()
        };
        if !valid || depth > MAX_ORDER_DEPTH as usize {
            return Err(PdfError::new_err(
                "a visibility expression is an operator whose operands nest one level below it",
            ));
        }
        while stack.len() > depth {
            let array = stack.pop().unwrap_or_default();
            match stack.last_mut() {
                Some(parent) => {
                    parent
                        .try_reserve(1)
                        .map_err(|error| alloc_err("visibility expression", error))?;
                    parent.push(Object::Array(array));
                }
                None => closed = Some(Object::Array(array)),
            }
        }
        if closed.is_some() {
            return Err(PdfError::new_err("a visibility expression has one root"));
        }
        let operator = match operator.as_str() {
            "" => {
                group_dictionary(doc, *id)?;
                let parent = stack
                    .last_mut()
                    .ok_or_else(|| PdfError::new_err("a group needs an operator"))?;
                parent
                    .try_reserve(1)
                    .map_err(|error| alloc_err("visibility expression", error))?;
                parent.push(Object::Reference(*id));
                continue;
            }
            "and" => &b"And"[..],
            "or" => &b"Or"[..],
            "not" => &b"Not"[..],
            other => {
                return Err(PdfError::new_err(format!(
                    "bad expression operator: {other:?}"
                )));
            }
        };
        let mut array = Vec::new();
        array
            .try_reserve(1)
            .map_err(|error| alloc_err("visibility expression", error))?;
        array.push(Object::Name(operator.to_vec()));
        stack.push(array);
    }
    while let Some(array) = stack.pop() {
        match stack.last_mut() {
            Some(parent) => {
                parent
                    .try_reserve(1)
                    .map_err(|error| alloc_err("visibility expression", error))?;
                parent.push(Object::Array(array));
            }
            None => closed = Some(Object::Array(array)),
        }
    }
    closed.ok_or_else(|| PdfError::new_err("an empty visibility expression"))
}

/// Check an `/OC` value from Python and build what it stores.
pub(super) fn prepare_layer(doc: &Document, value: &LayerValue) -> PyResult<PreparedLayer> {
    let (kind, id, policy, groups, expression) = value;
    match kind {
        0 => Ok(PreparedLayer::Remove),
        1 => {
            group_dictionary(doc, *id)?;
            Ok(PreparedLayer::Group(*id))
        }
        2 => {
            if !POLICIES.contains(&policy.as_str()) {
                return Err(PdfError::new_err(format!(
                    "bad visibility policy: {policy:?}"
                )));
            }
            check_groups(doc, groups)?;
            let mut membership = Dictionary::new();
            membership.set("Type", Object::Name(b"OCMD".to_vec()));
            membership.set("OCGs", reference_array(groups)?);
            if policy != "AnyOn" {
                membership.set("P", Object::Name(policy.as_bytes().to_vec()));
            }
            if !expression.is_empty() {
                membership.set("VE", expression_object(doc, expression)?);
            }
            Ok(PreparedLayer::Membership(membership))
        }
        _ => Err(PdfError::new_err(format!("bad layer value kind: {kind}"))),
    }
}

/// Store a prepared value as the `/OC` entry of object `holder`.
fn store_layer(doc: &mut Document, holder: ObjectId, prepared: PreparedLayer) {
    let value = match prepared {
        PreparedLayer::Remove => None,
        PreparedLayer::Group(id) => Some(Object::Reference(id)),
        PreparedLayer::Membership(membership) => Some(Object::Reference(
            doc.add_object(Object::Dictionary(membership)),
        )),
    };
    let dict = match doc.objects.get_mut(&holder) {
        Some(Object::Dictionary(dict)) => dict,
        Some(Object::Stream(stream)) => &mut stream.dict,
        _ => return,
    };
    match value {
        Some(value) => dict.set("OC", value),
        None => {
            dict.remove(b"OC");
        }
    }
}

/// Implements [`_Document::annotation_layer`].
pub(super) fn annotation_layer(
    this: &_Document,
    page_number: u32,
    index: u32,
    xref: u32,
) -> PyResult<(u32, LayerValue)> {
    let page_id = this.page_id(page_number)?;
    let (index, annotation) = this.annotation_at(page_id, index, xref)?;
    Ok((index, read_oc(&this.doc, annotation)?))
}

/// Implements [`_Document::set_annotation_layer`]; returns the annotation's
/// object number, new when it was stored in `/Annots` itself.
pub(super) fn set_annotation_layer(
    this: &mut _Document,
    page_number: u32,
    index: u32,
    xref: u32,
    value: LayerValue,
) -> PyResult<u32> {
    let page_id = this.page_id(page_number)?;
    let prepared = prepare_layer(&this.doc, &value)?;
    let (_, annotation) = this.annotation_at(page_id, index, xref)?;
    if matches!(prepared, PreparedLayer::Remove) && !annotation.has(b"OC") {
        return Ok(xref);
    }
    let (_, id) = edited_annotation(this, page_id, index, xref)?;
    store_layer(&mut this.doc, id, prepared);
    Ok(id.0)
}

/// The dictionary of the XObject stream `xref`, or a stale error.
fn xobject_dictionary(doc: &Document, xref: u32) -> PyResult<(ObjectId, &Dictionary)> {
    doc.objects
        .range((xref, 0)..=(xref, u16::MAX))
        .next()
        .and_then(|(id, object)| match object {
            Object::Stream(stream)
                if matches!(stream.dict.get(b"Subtype"), Ok(Object::Name(name)) if name == b"Image" || name == b"Form") =>
            {
                Some((*id, &stream.dict))
            }
            _ => None,
        })
        .ok_or_else(|| StaleObjectError::new_err(format!("object {xref} is no longer an image or form XObject")))
}

/// Implements [`_Document::xobject_layer`].
pub(super) fn xobject_layer(this: &_Document, xref: u32) -> PyResult<LayerValue> {
    let (_, dict) = xobject_dictionary(&this.doc, xref)?;
    read_oc(&this.doc, dict)
}

/// Implements [`_Document::set_xobject_layer`].
pub(super) fn set_xobject_layer(
    this: &mut _Document,
    xref: u32,
    value: LayerValue,
) -> PyResult<()> {
    let prepared = prepare_layer(&this.doc, &value)?;
    let (id, dict) = xobject_dictionary(&this.doc, xref)?;
    if matches!(prepared, PreparedLayer::Remove) && !dict.has(b"OC") {
        return Ok(());
    }
    // An XObject is drawn wherever it is placed, so placements stay valid.
    this.invalidate_hayro_pdf_keeping_placements();
    store_layer(&mut this.doc, id, prepared);
    Ok(())
}

/// Implements [`_Document::page_layers`]: the groups and membership
/// dictionaries that the page's inherited `/Resources /Properties` names.
pub(super) fn page_layers(
    this: &_Document,
    page_number: u32,
) -> PyResult<Vec<(String, LayerValue)>> {
    let doc = &this.doc;
    let page = resolve_inherited_page_dict(doc, this.page_id(page_number)?)?;
    let Some(properties) = page
        .get(b"Resources")
        .ok()
        .and_then(|resources| deref_dict_ref(doc, resources))
        .and_then(|resources| resources.get(b"Properties").ok())
        .and_then(|properties| deref_dict_ref(doc, properties))
    else {
        return Ok(Vec::new());
    };
    if properties.len() > MAX_LAYER_ENTRIES {
        return Err(limit_err(
            "layer_count",
            format!(
                "/Properties lists {} entries; the limit is {MAX_LAYER_ENTRIES}",
                properties.len()
            ),
        ));
    }
    let mut out = Vec::new();
    out.try_reserve_exact(properties.len())
        .map_err(|error| alloc_err("page layers", error))?;
    let mut used = 0usize;
    for (name, object) in properties.iter() {
        let value = match object {
            Object::Reference(id) => match doc.objects.get(id) {
                Some(Object::Dictionary(dict)) if is_membership(dict) => {
                    read_membership(doc, dict)?
                }
                Some(Object::Dictionary(dict)) if matches!(dict.get(b"Type"), Ok(Object::Name(kind)) if kind == b"OCG") => {
                    (1, *id, String::new(), Vec::new(), Vec::new())
                }
                _ => continue,
            },
            Object::Dictionary(dict) if is_membership(dict) => read_membership(doc, dict)?,
            _ => continue,
        };
        used += name.len();
        if used > MAX_LAYER_TEXT_BYTES {
            return Err(layer_text_err());
        }
        out.push((String::from_utf8_lossy(name).into_owned(), value));
    }
    Ok(out)
}

/// The optional-content groups that imported pages reach, planned into the
/// receiving document's `/OCProperties` before any import mutation.
pub(super) struct LayerImport {
    plan: PropertiesPlan,
}

impl LayerImport {
    /// Plan the groups of `source`'s `/OCGs` that `roots`, the imported page
    /// dictionaries, reach, each listed in `target`'s `/OCGs` with the
    /// visibility that `source`'s default configuration gives it and
    /// appended to `target`'s `/Order` when it has one.
    pub(super) fn prepare<'a>(
        target: &Document,
        source: &'a Document,
        roots: impl IntoIterator<Item = &'a Object>,
    ) -> PyResult<Option<Self>> {
        let Some(properties) = oc_properties(source) else {
            return Ok(None);
        };
        let listed = references(source, properties, b"OCGs")?;
        if listed.is_empty() {
            return Ok(None);
        }
        let reachable = crate::prune::reachable_object_ids(source, roots, |object| {
            !matches!(
                object.type_name().unwrap_or(b""),
                b"Catalog" | b"Pages" | b"Page"
            )
        })?;
        let mut imported = Vec::new();
        imported
            .try_reserve_exact(listed.len())
            .map_err(|error| alloc_err("imported optional-content groups", error))?;
        for id in listed {
            if reachable.contains(&id) && !imported.iter().any(|(item, _)| *item == id) {
                imported.push((id, group_visible(source, id)?));
            }
        }
        if imported.is_empty() {
            return Ok(None);
        }
        let fresh = oc_properties(target).is_none();
        let mut plan = PropertiesPlan::read(target)?;
        let mut on = plan.config_refs(target, b"ON")?;
        let mut off = plan.config_refs(target, b"OFF")?;
        let mut order = match plan
            .config
            .get(b"Order")
            .ok()
            .and_then(|object| array_of(target, object))
        {
            Some(items) => {
                let mut copy = Vec::new();
                copy.try_reserve_exact(items.len() + imported.len())
                    .map_err(|error| alloc_err("optional-content order", error))?;
                copy.extend_from_slice(items);
                Some(copy)
            }
            None if fresh => Some(Vec::new()),
            None => None,
        };
        for &(id, visible) in &imported {
            plan.list(id)?;
            plan_visibility(&plan, &mut on, &mut off, id, visible)?;
            if let Some(order) = &mut order {
                if order.len() >= MAX_LAYER_ENTRIES {
                    return Err(limit_err(
                        "layer_count",
                        format!("/Order already lists {MAX_LAYER_ENTRIES} entries"),
                    ));
                }
                order
                    .try_reserve(1)
                    .map_err(|error| alloc_err("optional-content order", error))?;
                order.push(Object::Reference(id));
            }
        }
        if let Some(order) = order {
            plan.config.set("Order", Object::Array(order));
        }
        if on.is_empty() {
            plan.config.remove(b"ON");
        } else {
            plan.config.set("ON", reference_array(&on)?);
        }
        if off.is_empty() {
            plan.config.remove(b"OFF");
        } else {
            plan.config.set("OFF", reference_array(&off)?);
        }
        Ok(Some(Self { plan }))
    }

    /// Store the planned `/OCProperties`; this cannot fail.
    pub(super) fn apply(self, doc: &mut Document) {
        self.plan.commit(doc);
    }
}
