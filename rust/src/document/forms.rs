//! AcroForm field-tree reads, form filling, and widget appearances.

use super::*;

/// Bound AcroForm field-tree traversal and returned metadata.
pub(super) const MAX_FORM_FIELD_ENTRIES: usize = 4_096;

pub(super) const MAX_FORM_FIELD_TREE_NODES: usize = 4_096;

pub(super) const MAX_FORM_FIELD_TREE_EDGES: usize = 8_192;

pub(crate) const MAX_FORM_FIELD_TREE_DEPTH: usize = 64;

pub(crate) const MAX_FORM_FIELD_WIDGETS: usize = 4_096;

pub(crate) const MAX_FORM_FIELD_NAME_BYTES: usize = 1024 * 1024;

pub(super) const MAX_FORM_FIELD_VALUE_BYTES: usize = 1024 * 1024;

pub(super) const MAX_FORM_FIELD_VALUE_ITEMS: usize = 4_096;

pub(super) const MAX_FORM_BUTTON_STATE_ENTRIES: usize = 8_192;

pub(super) const MAX_FORM_BUTTON_STATE_NAMES: usize = 4_096;

pub(super) const MAX_FORM_BUTTON_STATE_NAME_BYTES: usize = 1024 * 1024;

pub(super) const TEXT_FIELD_MULTILINE: i64 = 1 << 12;

pub(super) const TEXT_FIELD_PASSWORD: i64 = 1 << 13;

pub(super) const TEXT_FIELD_FILE_SELECT: i64 = 1 << 20;

pub(super) const TEXT_FIELD_COMB: i64 = 1 << 24;

#[derive(Clone, Copy)]
pub(super) enum WidgetTextLayout {
    SingleLine,
    Multiline,
    Comb(usize),
}

/// One flattened AcroForm field: name, object, type, flags, and normalized
/// value. Fields that inherit one type or value share it.
pub(super) type FormFieldEntry = (String, ObjectId, Arc<String>, i64, Option<Arc<str>>);

/// One widget and the raw state names from its normal appearance dictionary.
pub(super) type WidgetStateNames = (ObjectId, Vec<Vec<u8>>);

/// How one field-tree traversal node takes part in the walk.
#[derive(Clone, Copy)]
pub(super) enum FieldRole {
    /// A `/Fields` entry or a named kid, which can be a returned leaf.
    Field,
    /// A nameless intermediate field folded into the nearest field above it,
    /// whose reserved leaf entry, if any, is at this index.
    Folded(Option<usize>),
}

/// One field-tree traversal node: object, prefix, inherited FT/Ff/value,
/// depth, and role.
///
/// The prefix and inherited text are shared with the node that pushed it.
/// Only the first visit of a kid is charged to the text budgets, while
/// `/Kids` can push it once per edge, so a copy per push would let a long
/// name or type grow with the edge count. Names and types are `Arc<String>`
/// so that their fallibly allocated text moves in instead of being copied
/// into an `Arc<str>`.
pub(super) type FieldNode = (
    ObjectId,
    Arc<String>,
    Option<Arc<String>>,
    i64,
    Option<Arc<str>>,
    usize,
    FieldRole,
);

/// Font input that generated form appearances draw with.
///
/// Each widget that draws text opens a face from font bytes, so a value that
/// needs no embedded font, such as a button state, never reads them. A loaded
/// `Font` was opened when Python created it, at the instance it selected.
pub(super) enum FormFont {
    Data {
        data: FontBytes,
        index: u32,
        /// The weight drawn on a variable face; see `generation_face`.
        weight: Option<u16>,
    },
    Face(GenerationFace),
}

impl FormFont {
    pub(super) fn face(&self) -> Result<GenerationFace, String> {
        match self {
            Self::Data {
                data,
                index,
                weight,
            } => GenerationFace::new(Arc::clone(data), *index, *weight),
            // A clone shares the opened font instead of reading it again.
            Self::Face(face) => Ok(face.clone()),
        }
    }
}

pub(super) fn validate_form_field_input(name: &str, value: Option<&str>) -> PyResult<()> {
    for (label, text, limit) in [
        ("form field name", Some(name), MAX_FORM_FIELD_NAME_BYTES),
        ("form field value", value, MAX_FORM_FIELD_VALUE_BYTES),
    ] {
        if text.is_some_and(|text| text.len() > limit) {
            return Err(limit_err(
                "form_field_input_size",
                format!("{label} exceeds the {limit}-byte UTF-8 safety limit"),
            ));
        }
    }
    Ok(())
}

/// Preserve control characters that PDFDocEncoding intentionally leaves unmapped.
pub(crate) fn form_text_string(text: &str) -> Object {
    if !text.chars().any(char::is_control) {
        return text_string(text);
    }
    let mut encoded = vec![0xfe, 0xff];
    for unit in text.encode_utf16() {
        encoded.extend_from_slice(&unit.to_be_bytes());
    }
    Object::String(encoded, StringFormat::Hexadecimal)
}

/// Add to one AcroForm text budget without overflow or partial output.
pub(super) fn add_form_budget(
    total: &mut usize,
    amount: usize,
    limit: usize,
    label: &str,
) -> PyResult<()> {
    *total = total
        .checked_add(amount)
        .ok_or_else(|| PdfError::new_err("AcroForm metadata exceeds the platform size limit"))?;
    if *total > limit {
        return Err(PdfError::new_err(format!(
            "AcroForm {label} text exceeds the {limit}-byte safety limit"
        )));
    }
    Ok(())
}

pub(super) fn try_push_form<T>(values: &mut Vec<T>, value: T, label: &str) -> PyResult<()> {
    if values.len() == values.capacity() {
        values.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow AcroForm {label}: {error}"))
        })?;
    }
    values.push(value);
    Ok(())
}

pub(super) fn try_clone_form_string(value: &str, label: &str) -> PyResult<String> {
    let mut owned = String::new();
    owned.try_reserve_exact(value.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate AcroForm {label}: {error}"))
    })?;
    owned.push_str(value);
    Ok(owned)
}

pub(super) fn try_clone_form_bytes(value: &[u8], label: &str) -> PyResult<Vec<u8>> {
    let mut owned = Vec::new();
    owned.try_reserve_exact(value.len()).map_err(|error| {
        PdfError::new_err(format!("failed to allocate AcroForm {label}: {error}"))
    })?;
    owned.extend_from_slice(value);
    Ok(owned)
}

pub(super) fn try_lossy_form_string(value: &[u8], label: &str) -> PyResult<String> {
    match String::from_utf8_lossy(value) {
        Cow::Borrowed(value) => try_clone_form_string(value, label),
        Cow::Owned(value) => Ok(value),
    }
}

/// Borrow an inheritable field attribute unless the field omits it.
///
/// ISO 32000-2 §7.3.7 treats a null dictionary value as an omitted key, and
/// §7.3.10 reads a reference to a missing object as null, so a null value, an
/// indirect null, and such a reference inherit from the parent field as an
/// absent key does. Any other value, including an unusable one and a cyclic or
/// over-long reference chain, belongs to the field and stops inheritance.
pub(crate) fn inheritable_field_value<'a>(
    doc: &'a Document,
    field: &'a Dictionary,
    key: &[u8],
) -> Option<&'a Object> {
    let value = field.get(key).ok()?;
    let omitted = match value {
        Object::Null => true,
        Object::Reference(id) => matches!(
            doc.get_object(*id),
            Ok(Object::Null) | Err(lopdf::Error::ObjectNotFound(_))
        ),
        _ => false,
    };
    (!omitted).then_some(value)
}

/// Decode one field-name component while bounding encoded and decoded text.
pub(crate) fn bounded_form_text(
    doc: &Document,
    object: &Object,
    encoded_bytes: &mut usize,
    decoded_bytes: &mut usize,
    limit: usize,
    label: &str,
) -> PyResult<Option<String>> {
    let object = deref_object(doc, object);
    let Object::String(encoded, _) = object else {
        return Ok(None);
    };
    add_form_budget(
        encoded_bytes,
        encoded.len(),
        limit,
        &format!("encoded {label}"),
    )?;
    let Ok(decoded) = decode_text_string(object) else {
        return Ok(None);
    };
    add_form_budget(
        decoded_bytes,
        decoded.len(),
        limit,
        &format!("decoded {label}"),
    )?;
    Ok(Some(decoded))
}

/// Normalize one field value while bounding arrays and all produced text.
pub(super) fn bounded_form_value(
    doc: &Document,
    object: &Object,
    encoded_bytes: &mut usize,
    decoded_bytes: &mut usize,
    value_items: &mut usize,
) -> PyResult<Option<String>> {
    match deref_object(doc, object) {
        Object::Name(name) => {
            add_form_budget(
                encoded_bytes,
                name.len(),
                MAX_FORM_FIELD_VALUE_BYTES,
                "encoded field-value",
            )?;
            let decoded = try_lossy_form_string(name, "decoded field value")?;
            add_form_budget(
                decoded_bytes,
                decoded.len(),
                MAX_FORM_FIELD_VALUE_BYTES,
                "decoded field-value",
            )?;
            Ok(Some(decoded))
        }
        object @ Object::String(encoded, _) => {
            add_form_budget(
                encoded_bytes,
                encoded.len(),
                MAX_FORM_FIELD_VALUE_BYTES,
                "encoded field-value",
            )?;
            let Ok(decoded) = decode_text_string(object) else {
                return Ok(None);
            };
            add_form_budget(
                decoded_bytes,
                decoded.len(),
                MAX_FORM_FIELD_VALUE_BYTES,
                "decoded field-value",
            )?;
            Ok(Some(decoded))
        }
        Object::Array(items) => {
            *value_items = value_items.checked_add(items.len()).ok_or_else(|| {
                PdfError::new_err("AcroForm field values exceed the platform size limit")
            })?;
            if *value_items > MAX_FORM_FIELD_VALUE_ITEMS {
                return Err(PdfError::new_err(format!(
                    "AcroForm field values exceed the {MAX_FORM_FIELD_VALUE_ITEMS}-item safety limit"
                )));
            }
            let mut values = Vec::new();
            for item in items {
                let item = deref_object(doc, item);
                let Object::String(encoded, _) = item else {
                    continue;
                };
                add_form_budget(
                    encoded_bytes,
                    encoded.len(),
                    MAX_FORM_FIELD_VALUE_BYTES,
                    "encoded field-value",
                )?;
                let Ok(decoded) = decode_text_string(item) else {
                    continue;
                };
                add_form_budget(
                    decoded_bytes,
                    decoded.len(),
                    MAX_FORM_FIELD_VALUE_BYTES,
                    "decoded field-value",
                )?;
                try_push_form(&mut values, decoded, "choice-value items")?;
            }
            let separators = values
                .len()
                .saturating_sub(1)
                .checked_mul(2)
                .ok_or_else(|| {
                    PdfError::new_err("AcroForm field value length exceeds the platform size limit")
                })?;
            if separators != 0 {
                add_form_budget(
                    decoded_bytes,
                    separators,
                    MAX_FORM_FIELD_VALUE_BYTES,
                    "decoded field-value",
                )?;
            }
            let output_size = values.iter().try_fold(separators, |size, value| {
                size.checked_add(value.len()).ok_or_else(|| {
                    PdfError::new_err("AcroForm field value length exceeds the platform size limit")
                })
            })?;
            let mut joined = String::new();
            joined.try_reserve_exact(output_size).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate AcroForm joined field value: {error}"
                ))
            })?;
            for (index, value) in values.into_iter().enumerate() {
                if index != 0 {
                    joined.push_str(", ");
                }
                joined.push_str(&value);
            }
            Ok(Some(joined))
        }
        _ => Ok(None),
    }
}

/// One AcroForm `/Kids` entry as every field-tree reader classifies it.
pub(crate) enum FieldKid<'a> {
    /// A dictionary with its own partial field name.
    Field(ObjectId),
    /// A dictionary without one or kids to fold, which is a widget of the
    /// parent field.
    Widget(ObjectId),
    /// A dictionary without one but with kids, which is a nameless
    /// intermediate field folded into the parent field. `widget` is true when
    /// it also looks like a widget annotation.
    Intermediate {
        id: ObjectId,
        kids: &'a [Object],
        widget: bool,
    },
    /// Any other referenced value, such as a number or a cyclic reference chain.
    Unusable,
    /// A direct entry, an indirect null, or a reference to a missing object.
    Ignored,
}

/// Mark one AcroForm field-tree node visited within the node limit.
///
/// Returns false when the node was already visited.
pub(crate) fn visit_form_node(visited: &mut HashSet<ObjectId>, id: ObjectId) -> PyResult<bool> {
    if visited.contains(&id) {
        return Ok(false);
    }
    if visited.len() == visited.capacity() {
        visited.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow AcroForm field-tree cycle set: {error}"
            ))
        })?;
    }
    visited.insert(id);
    if visited.len() > MAX_FORM_FIELD_TREE_NODES {
        return Err(PdfError::new_err(format!(
            "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_NODES}-node safety limit"
        )));
    }
    Ok(true)
}

/// Add the entries of one `/Kids` array to the AcroForm field-tree edges.
pub(crate) fn add_form_tree_edges(edges: &mut usize, count: usize) -> PyResult<()> {
    *edges = edges
        .checked_add(count)
        .ok_or_else(|| PdfError::new_err("AcroForm field tree exceeds the platform size limit"))?;
    if *edges > MAX_FORM_FIELD_TREE_EDGES {
        return Err(PdfError::new_err(format!(
            "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_EDGES}-edge safety limit"
        )));
    }
    Ok(())
}

/// Borrow a field's `/Kids` array, reading any other value as an absent entry.
pub(crate) fn field_kids<'a>(doc: &'a Document, field: &'a Dictionary) -> Option<&'a [Object]> {
    let kids = field.get(b"Kids").ok()?;
    deref_object(doc, kids).as_array().ok().map(Vec::as_slice)
}

/// Treat a dictionary with `/Subtype /Widget` or a `/Rect` as a widget annotation.
pub(crate) fn looks_like_widget(dict: &Dictionary) -> bool {
    dict.get(b"Subtype")
        .and_then(Object::as_name)
        .is_ok_and(|name| name == b"Widget")
        || dict.has(b"Rect")
}

/// Classify one AcroForm `/Kids` entry for the walk and `field_widgets`.
///
/// A `/T` that is null, an indirect null, or a reference to a missing object
/// is an omitted partial name, so such a kid is nameless, as one without `/T`
/// is. Any other `/T`, including an unusable value or a cyclic or over-long
/// reference chain, makes the kid a child field.
///
/// A nameless kid whose `/Kids` resolves to a non-empty array is a field,
/// because a field merged with its only widget omits `/Kids` (ISO 32000-2
/// §12.7.4.1), and a field without a partial name represents the field above
/// it (§12.7.4.2). It is therefore an intermediate field whose kids fold into
/// the parent field, and it stays a widget too when it looks like one, so a
/// malformed merged widget with stray kids keeps its appearance in sync. Any
/// other nameless kid is a widget of its parent field: an absent,
/// null-equivalent, non-array, or empty `/Kids` names no kids to fold, and a
/// field with such a `/Kids` is its own widget.
pub(crate) fn classify_field_kid<'a>(doc: &'a Document, kid: &Object) -> FieldKid<'a> {
    let Object::Reference(id) = kid else {
        return FieldKid::Ignored;
    };
    match doc.get_object(*id) {
        Ok(Object::Dictionary(child)) => {
            if child
                .get(b"T")
                .is_ok_and(|name| !is_null_equivalent(doc, name))
            {
                return FieldKid::Field(*id);
            }
            match field_kids(doc, child) {
                Some(kids) if !kids.is_empty() => FieldKid::Intermediate {
                    id: *id,
                    kids,
                    widget: looks_like_widget(child),
                },
                _ => FieldKid::Widget(*id),
            }
        }
        Ok(Object::Null) | Err(lopdf::Error::ObjectNotFound(_)) => FieldKid::Ignored,
        _ => FieldKid::Unusable,
    }
}

impl _Document {
    /// Flatten AcroForm fields into `(full name, ObjectId, FT, Ff, V)`.
    ///
    /// FT/Ff/V inherit from parents when a field omits them or holds a null
    /// value, an indirect null, or a reference to a missing object; FT and Ff
    /// also inherit past any other unreadable value. Full names join `/T`
    /// components with dots. A leaf has resolved FT and no child carrying `/T`.
    /// Traversal and returned text are bounded so malformed trees cannot
    /// produce partial results.
    pub(super) fn collect_form_fields(&self) -> PyResult<Vec<FormFieldEntry>> {
        let Some(acroform_object) = self
            .doc
            .catalog()
            .ok()
            .and_then(|catalog| catalog.get(b"AcroForm").ok())
        else {
            return Ok(Vec::new());
        };
        let Ok(acroform) = deref_object(&self.doc, acroform_object).as_dict() else {
            return Ok(Vec::new());
        };
        let Ok(fields_object) = acroform.get(b"Fields") else {
            return Ok(Vec::new());
        };
        let Ok(fields) = deref_object(&self.doc, fields_object).as_array() else {
            return Ok(Vec::new());
        };
        if fields.len() > MAX_FORM_FIELD_TREE_EDGES {
            return Err(PdfError::new_err(format!(
                "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_EDGES}-edge safety limit"
            )));
        }

        // Each field with a resolved type reserves its leaf entry before its
        // kids are read, so a nameless intermediate field folded into it can
        // withdraw the entry when it holds a child field.
        let mut leaves: Vec<Option<FormFieldEntry>> = Vec::new();
        let mut stack: Vec<FieldNode> = Vec::new();
        stack.try_reserve_exact(fields.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate AcroForm field-tree stack: {error}"
            ))
        })?;
        let top_level_prefix = Arc::new(String::new());
        for field in fields {
            // As in `/Kids`, an indirect null or a reference to a missing
            // object costs only its edge, like a direct entry.
            if let Ok(id) = field.as_reference()
                && !is_null_equivalent(&self.doc, field)
            {
                let prefix = Arc::clone(&top_level_prefix);
                stack.push((id, prefix, None, 0, None, 1, FieldRole::Field));
            }
        }
        let mut visited = HashSet::new();
        let mut edges = fields.len();
        let mut widget_refs = 0usize;
        let mut encoded_name_bytes = 0usize;
        let mut decoded_name_bytes = 0usize;
        let mut materialized_name_bytes = 0usize;
        let mut encoded_value_bytes = 0usize;
        let mut decoded_value_bytes = 0usize;
        let mut returned_value_bytes = 0usize;
        let mut value_items = 0usize;
        while let Some((id, prefix, inh_ft, inh_ff, inh_v, depth, role)) = stack.pop() {
            if !visit_form_node(&mut visited, id)? {
                continue;
            }
            let Ok(dict) = self.doc.get_object(id).and_then(Object::as_dict) else {
                continue;
            };
            let component = match dict.get(b"T") {
                Ok(object) => bounded_form_text(
                    &self.doc,
                    object,
                    &mut encoded_name_bytes,
                    &mut decoded_name_bytes,
                    MAX_FORM_FIELD_NAME_BYTES,
                    "field-name",
                )?,
                Err(_) => None,
            };
            // A node without a partial name shares the name above it.
            let name = match component {
                Some(component) if prefix.is_empty() => Arc::new(component),
                Some(component) => {
                    let len = component
                        .len()
                        .checked_add(1)
                        .and_then(|len| len.checked_add(prefix.len()))
                        .ok_or_else(|| {
                            PdfError::new_err(
                                "AcroForm field-name length exceeds the platform size limit",
                            )
                        })?;
                    let mut name = String::new();
                    name.try_reserve_exact(len).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow AcroForm materialized field name: {error}"
                        ))
                    })?;
                    name.push_str(&prefix);
                    name.push('.');
                    name.push_str(&component);
                    Arc::new(name)
                }
                None => prefix,
            };
            add_form_budget(
                &mut materialized_name_bytes,
                name.len(),
                MAX_FORM_FIELD_NAME_BYTES,
                "materialized field-name",
            )?;
            let ft = match dict.get(b"FT") {
                Ok(object) => {
                    let object = deref_object(&self.doc, object);
                    match object.as_name() {
                        Ok(name) => {
                            add_form_budget(
                                &mut encoded_name_bytes,
                                name.len(),
                                MAX_FORM_FIELD_NAME_BYTES,
                                "encoded field-name/type",
                            )?;
                            let decoded = try_lossy_form_string(name, "decoded field type")?;
                            add_form_budget(
                                &mut decoded_name_bytes,
                                decoded.len(),
                                MAX_FORM_FIELD_NAME_BYTES,
                                "decoded field-name/type",
                            )?;
                            Some(Arc::new(decoded))
                        }
                        Err(_) => inh_ft,
                    }
                }
                Err(_) => inh_ft,
            };
            let ff = dict
                .get(b"Ff")
                .ok()
                .and_then(|object| resolve_i64(&self.doc, object))
                .unwrap_or(inh_ff);
            let value = match inheritable_field_value(&self.doc, dict, b"V") {
                Some(object) => bounded_form_value(
                    &self.doc,
                    object,
                    &mut encoded_value_bytes,
                    &mut decoded_value_bytes,
                    &mut value_items,
                )?
                .map(Arc::from),
                None => inh_v,
            };

            let mut has_child_fields = false;
            let leaf = match role {
                FieldRole::Field if ft.is_some() => {
                    try_push_form(&mut leaves, None, "returned field entries")?;
                    Some(leaves.len() - 1)
                }
                FieldRole::Field => None,
                FieldRole::Folded(leaf) => leaf,
            };
            if let Some(kids) = field_kids(&self.doc, dict) {
                add_form_tree_edges(&mut edges, kids.len())?;
                for kid in kids {
                    // An ignored entry costs only its edge. Every other entry
                    // that is neither a child field nor an intermediate field
                    // counts toward the widget budget, as does an intermediate
                    // that looks like a widget, so that budget covers every
                    // widget that `field_widgets` can return.
                    let kid = classify_field_kid(&self.doc, kid);
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
                    // An intermediate is walked like a child field, but it adds
                    // no name component and reports its child fields to the
                    // field it folds into.
                    let (kid_id, kid_role) = match kid {
                        FieldKid::Field(kid_id) => {
                            has_child_fields = true;
                            (kid_id, FieldRole::Field)
                        }
                        FieldKid::Intermediate { id, .. } => (id, FieldRole::Folded(leaf)),
                        FieldKid::Widget(_) | FieldKid::Unusable | FieldKid::Ignored => continue,
                    };
                    if depth >= MAX_FORM_FIELD_TREE_DEPTH {
                        return Err(PdfError::new_err(format!(
                            "AcroForm field tree exceeds the {MAX_FORM_FIELD_TREE_DEPTH}-level safety limit"
                        )));
                    }
                    try_push_form(
                        &mut stack,
                        (
                            kid_id,
                            Arc::clone(&name),
                            ft.clone(),
                            ff,
                            value.clone(),
                            depth + 1,
                            kid_role,
                        ),
                        "field-tree stack",
                    )?;
                }
            }
            if let Some(index) = leaf {
                if has_child_fields {
                    leaves[index] = None;
                } else if let (FieldRole::Field, Some(ft)) = (role, ft) {
                    // Copy the name only while another node shares it, such
                    // as an intermediate folded into this field.
                    let name = Arc::try_unwrap(name)
                        .or_else(|name| try_clone_form_string(&name, "returned field name"))?;
                    leaves[index] = Some((name, id, ft, ff, value));
                }
            }
        }
        let leaf_count = leaves.iter().flatten().count();
        if leaf_count > MAX_FORM_FIELD_ENTRIES {
            return Err(PdfError::new_err(format!(
                "AcroForm field tree exceeds the {MAX_FORM_FIELD_ENTRIES}-entry safety limit"
            )));
        }
        let mut out = Vec::new();
        out.try_reserve_exact(leaf_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate AcroForm returned field entries: {error}"
            ))
        })?;
        for (name, id, ft, ff, value) in leaves.into_iter().flatten() {
            if let Some(value) = &value {
                add_form_budget(
                    &mut returned_value_bytes,
                    value.len(),
                    MAX_FORM_FIELD_VALUE_BYTES,
                    "returned field-value",
                )?;
            }
            out.push((name, id, ft, ff, value));
        }
        out.sort_unstable_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
        Ok(out)
    }

    /// Return the widgets that the walk classifies below a field in document
    /// order, or the field itself when it has none.
    ///
    /// The widgets of a nameless intermediate field take its place among the
    /// kids, and each intermediate is expanded once, so a cycle ends. The walk
    /// visits every intermediate below a field it returns, so a successful
    /// walk has already met the node, edge, and widget limits enforced here;
    /// they keep this traversal bounded on its own. The frame stack holds one
    /// frame per intermediate on the current path, so the node limit bounds
    /// its depth too, while a depth limit could reject an intermediate that the
    /// walk reached at a smaller depth through another field.
    pub(super) fn field_widgets(&self, field_id: ObjectId) -> PyResult<Vec<ObjectId>> {
        let mut widgets = Vec::new();
        let Ok(dict) = self.doc.get_object(field_id).and_then(Object::as_dict) else {
            try_push_form(&mut widgets, field_id, "field widgets")?;
            return Ok(widgets);
        };
        let kids = field_kids(&self.doc, dict).unwrap_or_default();
        let mut edges = 0usize;
        add_form_tree_edges(&mut edges, kids.len())?;
        let mut frames = Vec::new();
        try_push_form(&mut frames, kids.iter(), "field-widget stack")?;
        let mut expanded = HashSet::new();
        while let Some(frame) = frames.last_mut() {
            let Some(kid) = frame.next() else {
                frames.pop();
                continue;
            };
            let (kid_id, is_widget, folded_kids) = match classify_field_kid(&self.doc, kid) {
                FieldKid::Widget(kid_id) => (kid_id, true, None),
                FieldKid::Intermediate { id, kids, widget } => (id, widget, Some(kids)),
                FieldKid::Field(_) | FieldKid::Unusable | FieldKid::Ignored => continue,
            };
            if is_widget {
                if widgets.len() >= MAX_FORM_FIELD_WIDGETS {
                    return Err(PdfError::new_err(format!(
                        "AcroForm field exceeds the {MAX_FORM_FIELD_WIDGETS}-widget safety limit"
                    )));
                }
                try_push_form(&mut widgets, kid_id, "field widgets")?;
            }
            let Some(kids) = folded_kids else {
                continue;
            };
            // The field is the first expanded node, so a cycle back to it ends.
            if expanded.is_empty() {
                visit_form_node(&mut expanded, field_id)?;
            }
            if visit_form_node(&mut expanded, kid_id)? {
                add_form_tree_edges(&mut edges, kids.len())?;
                try_push_form(&mut frames, kids.iter(), "field-widget stack")?;
            }
        }
        if widgets.is_empty() {
            try_push_form(&mut widgets, field_id, "field widgets")?;
        }
        Ok(widgets)
    }

    pub(super) fn visible_field_widgets(&self, field_id: ObjectId) -> PyResult<Vec<ObjectId>> {
        let mut visible = Vec::new();
        for widget_id in self.field_widgets(field_id)? {
            let is_visible = self
                .doc
                .get_object(widget_id)
                .and_then(Object::as_dict)
                .is_ok_and(looks_like_widget);
            if is_visible {
                try_push_form(&mut visible, widget_id, "visible field widgets")?;
            }
        }
        Ok(visible)
    }

    /// Borrow and bound every normal-appearance state name for one button field.
    pub(super) fn button_appearance_states(
        &self,
        field_id: ObjectId,
    ) -> PyResult<(Vec<WidgetStateNames>, usize, usize)> {
        let widgets = self.visible_field_widgets(field_id)?;
        if widgets.len() > MAX_FORM_FIELD_WIDGETS {
            return Err(PdfError::new_err(format!(
                "AcroForm field exceeds the {MAX_FORM_FIELD_WIDGETS}-widget safety limit"
            )));
        }
        let mut result = Vec::new();
        let mut entries = 0usize;
        let mut encoded_name_bytes = 0usize;
        for widget_id in widgets {
            let normal = self
                .doc
                .get_object(widget_id)
                .and_then(Object::as_dict)
                .ok()
                .and_then(|widget| widget.get(b"AP").ok())
                .and_then(|object| deref_object(&self.doc, object).as_dict().ok())
                .and_then(|appearance| appearance.get(b"N").ok())
                .and_then(|object| deref_object(&self.doc, object).as_dict().ok());
            let mut states = Vec::new();
            if let Some(normal) = normal {
                entries = entries.checked_add(normal.len()).ok_or_else(|| {
                    PdfError::new_err("AcroForm button states exceed the platform size limit")
                })?;
                if entries > MAX_FORM_BUTTON_STATE_ENTRIES {
                    return Err(PdfError::new_err(format!(
                        "AcroForm button states exceed the {MAX_FORM_BUTTON_STATE_ENTRIES}-entry safety limit"
                    )));
                }
                for (state, _) in normal {
                    add_form_budget(
                        &mut encoded_name_bytes,
                        state.len(),
                        MAX_FORM_BUTTON_STATE_NAME_BYTES,
                        "encoded button-state name",
                    )?;
                    let state = try_clone_form_bytes(state, "button-state name")?;
                    try_push_form(&mut states, state, "button-state names")?;
                }
            }
            try_push_form(&mut result, (widget_id, states), "button widget states")?;
        }
        Ok((result, entries, encoded_name_bytes))
    }

    /// Set AcroForm NeedAppearances, including indirect dictionaries.
    pub(super) fn set_need_appearances(&mut self, value: bool) -> PyResult<()> {
        let acroform_ref = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"AcroForm").ok())
            .and_then(|a| a.as_reference().ok());
        match acroform_ref {
            Some(id) => {
                let acroform = self
                    .doc
                    .get_object_mut(id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                acroform.set("NeedAppearances", value);
            }
            None => {
                let catalog = self.doc.catalog_mut().map_err(to_py_err)?;
                let acroform = catalog
                    .get_mut(b"AcroForm")
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                acroform.set("NeedAppearances", value);
            }
        }
        Ok(())
    }

    /// Read an inheritable field attribute from a field/widget parent chain.
    ///
    /// A null value, an indirect null, and a reference to a missing object
    /// continue to the parent, as an absent key does.
    pub(super) fn resolve_field_attr(&self, field_id: ObjectId, key: &[u8]) -> Option<Object> {
        let mut current = Some(field_id);
        let mut visited = HashSet::new();
        while let Some(id) = current {
            if !visited.insert(id) || visited.len() > 64 {
                return None;
            }
            let dict = self.doc.get_object(id).ok()?.as_dict().ok()?;
            if let Some(value) = inheritable_field_value(&self.doc, dict, key) {
                return Some(value.clone());
            }
            current = dict.get(b"Parent").and_then(Object::as_reference).ok();
        }
        None
    }

    /// Resolve the quadding (0 left, 1 centered, 2 right) of a variable-text
    /// field's appearances.
    ///
    /// When the bounded `/Parent` walk finds no `/Q`, the interactive form
    /// dictionary's document-wide `/Q` applies (ISO 32000-2 Table 225), where
    /// a null value, an indirect null, and a reference to a missing object
    /// also read as absent. A present non-integer or out-of-range value aligns
    /// left; on a field it still overrides the document default.
    pub(super) fn field_quadding(&self, field_id: ObjectId) -> u8 {
        self.resolve_field_attr(field_id, b"Q")
            .or_else(|| {
                let acroform = self.doc.catalog().ok()?.get(b"AcroForm").ok()?;
                let acroform = deref_dict_ref(&self.doc, acroform)?;
                inheritable_field_value(&self.doc, acroform, b"Q").cloned()
            })
            .as_ref()
            .and_then(|object| resolve_i64(&self.doc, object))
            .and_then(|value| u8::try_from(value).ok())
            .filter(|value| *value <= 2)
            .unwrap_or(0)
    }

    /// Resolve a valid text-field comb size or explain malformed flag data.
    pub(super) fn field_comb_max_len(
        &self,
        field_id: ObjectId,
        flags: i64,
    ) -> Result<Option<usize>, String> {
        if flags & TEXT_FIELD_COMB == 0 {
            return Ok(None);
        }
        if flags & (TEXT_FIELD_MULTILINE | TEXT_FIELD_PASSWORD | TEXT_FIELD_FILE_SELECT) != 0 {
            return Err(
                "comb fields cannot also be multiline, password, or file-select fields".to_owned(),
            );
        }
        let max_len = self
            .resolve_field_attr(field_id, b"MaxLen")
            .as_ref()
            .and_then(|object| resolve_i64(&self.doc, object))
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| *value > 0)
            .ok_or_else(|| "comb field requires a positive MaxLen".to_owned())?;
        Ok(Some(max_len))
    }

    /// Clone a widget AP dictionary, update/remove `/N`, and write it locally.
    pub(super) fn set_widget_normal_appearance(
        &mut self,
        widget_id: ObjectId,
        normal: Option<Object>,
    ) -> PyResult<()> {
        let mut appearance = self
            .doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .ok()
            .and_then(|widget| widget.get(b"AP").ok())
            .and_then(|object| deref_dict(&self.doc, object))
            .unwrap_or_default();
        if let Some(normal) = normal {
            appearance.set("N", normal);
        } else {
            appearance.remove(b"N");
        }
        let widget = self
            .doc
            .get_object_mut(widget_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        if appearance.is_empty() {
            widget.remove(b"AP");
        } else {
            widget.set("AP", Object::Dictionary(appearance));
        }
        Ok(())
    }

    /// Return the normal-appearance state dictionary for a button widget.
    pub(super) fn button_normal_appearance(&self, widget_id: ObjectId) -> Dictionary {
        self.doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .ok()
            .and_then(|widget| widget.get(b"AP").ok())
            .and_then(|object| deref_dict(&self.doc, object))
            .and_then(|appearance| {
                appearance
                    .get(b"N")
                    .ok()
                    .and_then(|object| deref_dict(&self.doc, object))
            })
            .unwrap_or_default()
    }

    /// Treat a non-empty stream as an authored appearance worth preserving.
    pub(super) fn appearance_has_content(&self, appearance: &Object) -> bool {
        let resolved = match appearance {
            Object::Reference(id) => self.doc.get_object(*id).ok(),
            other => Some(other),
        };
        resolved
            .and_then(|object| object.as_stream().ok())
            .is_some_and(|stream| !stream.content.is_empty())
    }

    pub(super) fn widget_has_normal_stream(&self, widget_id: ObjectId) -> bool {
        self.doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .ok()
            .and_then(|widget| widget.get(b"AP").ok())
            .and_then(|appearance| deref_dict(&self.doc, appearance))
            .and_then(|appearance| appearance.get(b"N").ok().cloned())
            .is_some_and(|normal| self.appearance_has_content(&normal))
    }

    /// Ensure a widget has usable Off and selected-state vector appearances.
    pub(super) fn synthesize_button_appearance(
        &mut self,
        widget_id: ObjectId,
        state: &str,
        radio: bool,
    ) -> PyResult<()> {
        let widget = self
            .doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?
            .clone();
        let style =
            form::WidgetStyle::from_widget(&self.doc, &widget, None).map_err(PdfError::new_err)?;
        let mut normal = self.button_normal_appearance(widget_id);
        for (name, on) in [("Off", false), (state, state != "Off")] {
            let keep_existing = normal
                .get(name.as_bytes())
                .ok()
                .is_some_and(|appearance| self.appearance_has_content(appearance));
            if !keep_existing {
                let appearance_id = self.doc.add_object(style.button_stream(on, radio));
                normal.set(name, Object::Reference(appearance_id));
            }
        }
        self.set_widget_normal_appearance(widget_id, Some(Object::Dictionary(normal)))
    }

    pub(super) fn sync_button_widget_appearances(
        &mut self,
        field_id: ObjectId,
        flags: i64,
        value: &str,
    ) -> PyResult<()> {
        let (widget_states, mut planned_entries, mut planned_name_bytes) =
            self.button_appearance_states(field_id)?;
        let requested_is_known = widget_states
            .iter()
            .any(|(_, states)| states.iter().any(|state| state == value.as_bytes()));
        for (index, (_, states)) in widget_states.iter().enumerate() {
            let selected = value != "Off"
                && (states.iter().any(|state| state == value.as_bytes())
                    || (!requested_is_known && index == 0));
            let selected_state = if selected { value } else { "Off" };
            for (position, planned_state) in ["Off", selected_state].into_iter().enumerate() {
                if position == 1 && selected_state == "Off" {
                    // The duplicate name in this pair never creates a second key.
                    continue;
                }
                if states.iter().any(|state| state == planned_state.as_bytes()) {
                    continue;
                }
                planned_entries = planned_entries.checked_add(1).ok_or_else(|| {
                    PdfError::new_err("AcroForm button states exceed the platform size limit")
                })?;
                if planned_entries > MAX_FORM_BUTTON_STATE_ENTRIES {
                    return Err(PdfError::new_err(format!(
                        "AcroForm button state update exceeds the {MAX_FORM_BUTTON_STATE_ENTRIES}-entry safety limit"
                    )));
                }
                add_form_budget(
                    &mut planned_name_bytes,
                    planned_state.len(),
                    MAX_FORM_BUTTON_STATE_NAME_BYTES,
                    "encoded button-state name",
                )?;
            }
        }
        let radio = flags & (1 << 15) != 0;
        for (index, (widget_id, states)) in widget_states.into_iter().enumerate() {
            let selected = value != "Off"
                && (states.iter().any(|state| state == value.as_bytes())
                    || (!requested_is_known && index == 0));
            let state = if selected { value } else { "Off" };
            self.synthesize_button_appearance(widget_id, state, radio)?;
            let widget = self
                .doc
                .get_object_mut(widget_id)
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            widget.set("AS", Object::Name(state.as_bytes().to_vec()));
        }
        Ok(())
    }

    /// The `/DA` that a widget draws its text with: its own, else the nearest
    /// one along its `/Parent` chain (ISO 32000-2 Table 228 makes it
    /// inheritable), else the AcroForm dictionary's, or `None` without any.
    pub(super) fn widget_default_appearance(
        &self,
        widget_id: ObjectId,
    ) -> Option<annotation_appearance::DefaultAppearance> {
        let doc = &*self.doc;
        let mut current = doc.get_dictionary(widget_id).ok()?;
        for _ in 0..MAX_FORM_FIELD_TREE_DEPTH {
            if let Ok(Object::String(bytes, _)) =
                current.get(b"DA").map(|value| deref_object(doc, value))
            {
                return Some(annotation_appearance::parse_default_appearance(bytes));
            }
            match current.get(b"Parent") {
                Ok(Object::Reference(parent)) => match doc.get_dictionary(*parent) {
                    Ok(parent) => current = parent,
                    Err(_) => break,
                },
                _ => break,
            }
        }
        doc.catalog()
            .ok()
            .and_then(|catalog| catalog.get(b"AcroForm").ok())
            .and_then(|form| deref_dict_ref(doc, form))
            .and_then(|form| form.get(b"DA").ok())
            .and_then(|value| match deref_object(doc, value) {
                Object::String(bytes, _) => {
                    Some(annotation_appearance::parse_default_appearance(bytes))
                }
                _ => None,
            })
    }

    /// Generate a text/choice widget appearance, preserving AP down/rollover data.
    pub(super) fn synthesize_text_appearance(
        &mut self,
        widget_id: ObjectId,
        value: &str,
        layout: WidgetTextLayout,
        align: u8,
        font: Option<&FormFont>,
    ) -> PyResult<bool> {
        let widget = self
            .doc
            .get_object(widget_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?
            .clone();
        let da = self.widget_default_appearance(widget_id);
        let style = form::WidgetStyle::from_widget(&self.doc, &widget, da.as_ref())
            .map_err(PdfError::new_err)?;
        if let WidgetTextLayout::Comb(max_len) = layout {
            form::validate_comb_text(value, max_len).map_err(PdfError::new_err)?;
        }

        if font.is_none() && !draw::is_winansi(value) {
            // Preserve the value and NeedAppearances compatibility behavior when
            // no embeddable font is available, but never leave a stale value AP.
            self.set_widget_normal_appearance(widget_id, None)?;
            return Ok(false);
        }

        let (resources, text_ops) = match font {
            Some(font) if !value.is_empty() => {
                let face = font.face().map_err(generated_text_err)?;
                let generated = match layout {
                    WidgetTextLayout::Comb(max_len) => generate::embedded_widget_comb_text_page(
                        (style.layout_width, style.layout_height),
                        style.content_rect(),
                        value,
                        max_len,
                        align,
                        &face,
                        style.text.color,
                    ),
                    _ => generate::embedded_widget_text_page(
                        (style.layout_width, style.layout_height),
                        style.content_rect(),
                        value,
                        &face,
                        matches!(layout, WidgetTextLayout::Multiline),
                        align,
                        style.text.color,
                    ),
                }
                .map_err(generated_text_err)?;
                let mut generated_doc = generated_pdf::load(&generated).map_err(|error| {
                    lopdf_err(Some("failed to import generated form appearance"), &error)
                })?;
                face.name_subset(&mut generated_doc);
                let (form_id, crop, rotation) =
                    import_page_as_form(&mut self.doc, generated_doc, 1)?;
                let name = format!("PyloTx{}", form_id.0);
                let content = draw::PlacedContent::Form { crop, rotation };
                let matrix = draw::placement_matrix(
                    [0.0, 0.0, style.layout_width, style.layout_height],
                    0,
                    [0.0, 0.0, style.layout_width, style.layout_height],
                    &content,
                    false,
                );
                let resources = dictionary! {
                    "XObject" => dictionary! {
                        name.as_bytes() => Object::Reference(form_id),
                    },
                };
                (Some(resources), draw::draw_ops(matrix, &name))
            }
            Some(_) => (None, Vec::new()),
            None => {
                let text_ops = match layout {
                    WidgetTextLayout::Comb(max_len) => {
                        form::standard_comb_text_ops(&style, value, max_len, align)
                    }
                    _ => form::standard_text_ops(
                        &style,
                        value,
                        matches!(layout, WidgetTextLayout::Multiline),
                        align,
                    ),
                }
                .map_err(generated_text_err)?;
                if text_ops.is_empty() {
                    (None, text_ops)
                } else {
                    let mut font = dictionary! {
                        "Type" => "Font",
                        "Subtype" => "Type1",
                        "BaseFont" => style.text.base_font,
                    };
                    if !matches!(style.text.base_font, "Symbol" | "ZapfDingbats") {
                        font.set("Encoding", "WinAnsiEncoding");
                    }
                    let font_id = self.doc.add_object(font);
                    let resources = dictionary! {
                        "Font" => dictionary! {
                            style.text.resource.as_bytes() => Object::Reference(font_id),
                        },
                    };
                    (Some(resources), text_ops)
                }
            }
        };
        let content = style
            .decorated_text_ops(&text_ops)
            .map_err(generated_text_err)?;
        let appearance_id = self.doc.add_object(style.stream(resources, content));
        self.set_widget_normal_appearance(widget_id, Some(Object::Reference(appearance_id)))?;
        Ok(true)
    }

    /// Fill missing appearances on untouched fields when their current values
    /// can be represented without guessing a font.
    pub(super) fn synthesize_missing_form_appearances(
        &mut self,
        target_name: &str,
    ) -> PyResult<()> {
        for (name, field_id, field_type, flags, value) in self.collect_form_fields()? {
            if name == target_name {
                continue;
            }
            match field_type.as_str() {
                "Tx" | "Ch" => {
                    let value = value.as_deref().map(str::to_owned).unwrap_or_default();
                    if !draw::is_winansi(&value) {
                        continue;
                    }
                    let layout = if field_type.as_str() == "Tx" {
                        match self.field_comb_max_len(field_id, flags) {
                            Ok(Some(max_len)) => WidgetTextLayout::Comb(max_len),
                            Ok(None) if flags & TEXT_FIELD_MULTILINE != 0 => {
                                WidgetTextLayout::Multiline
                            }
                            Ok(None) => WidgetTextLayout::SingleLine,
                            Err(_) => continue,
                        }
                    } else {
                        WidgetTextLayout::SingleLine
                    };
                    let align = self.field_quadding(field_id);
                    for widget_id in self.visible_field_widgets(field_id)? {
                        if !self.widget_has_normal_stream(widget_id)
                            && self
                                .synthesize_text_appearance(widget_id, &value, layout, align, None)
                                .is_err()
                        {
                            continue;
                        }
                    }
                }
                "Btn" if flags & (1 << 16) == 0 => {
                    let state = value
                        .as_deref()
                        .map(str::to_owned)
                        .unwrap_or_else(|| "Off".to_owned());
                    if self
                        .sync_button_widget_appearances(field_id, flags, &state)
                        .is_err()
                    {
                        continue;
                    }
                }
                _ => {}
            }
        }
        Ok(())
    }

    pub(super) fn form_appearances_complete(&self) -> PyResult<bool> {
        for (_, field_id, field_type, flags, _) in self.collect_form_fields()? {
            let widgets = self.visible_field_widgets(field_id)?;
            let complete = match field_type.as_str() {
                "Tx" | "Ch" => widgets
                    .into_iter()
                    .all(|widget_id| self.widget_has_normal_stream(widget_id)),
                "Btn" if flags & (1 << 16) == 0 => widgets.into_iter().all(|widget_id| {
                    let Ok(widget) = self.doc.get_object(widget_id).and_then(Object::as_dict)
                    else {
                        return false;
                    };
                    let Some(state) = widget.get(b"AS").and_then(Object::as_name).ok() else {
                        return false;
                    };
                    self.button_normal_appearance(widget_id)
                        .get(state)
                        .ok()
                        .is_some_and(|appearance| self.appearance_has_content(appearance))
                }),
                _ => true,
            };
            if !complete {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// Set a validated form-field value, restoring the document on any error.
    pub(super) fn fill_form_field(
        &mut self,
        py: Python<'_>,
        name: &str,
        value: &str,
        font: Option<FormFont>,
    ) -> PyResult<()> {
        let encoded_value = form_text_string(value);
        if encoded_value
            .as_str()
            .is_ok_and(|bytes| bytes.len() > MAX_FORM_FIELD_VALUE_BYTES)
        {
            return Err(PdfError::new_err(format!(
                "encoded form field value exceeds the {MAX_FORM_FIELD_VALUE_BYTES}-byte safety limit"
            )));
        }
        let result = py.detach(|| {
            let original = self.doc.clone();
            match self.set_form_field_inner(name, value, font.as_ref()) {
                Ok(()) => Ok(()),
                Err(error) => {
                    self.doc.replace(original);
                    Err(error)
                }
            }
        });
        if result.is_ok() {
            self.invalidate_hayro_pdf();
        }
        result
    }

    /// Mutating implementation kept behind an atomic clone in `fill_form_field`.
    pub(super) fn set_form_field_inner(
        &mut self,
        name: &str,
        value: &str,
        font: Option<&FormFont>,
    ) -> PyResult<()> {
        let (field_id, ft, ff) = self
            .collect_form_fields()?
            .into_iter()
            .find(|(field_name, ..)| field_name == name)
            .map(|(_, id, ft, ff, _)| (id, ft, ff))
            .ok_or_else(|| PdfError::new_err(format!("form field not found: {name:?}")))?;
        match ft.as_str() {
            "Tx" | "Ch" => {
                {
                    let field = self
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                        .map_err(to_py_err)?;
                    field.set("V", form_text_string(value));
                }
                let layout = if ft.as_str() == "Tx" {
                    match self
                        .field_comb_max_len(field_id, ff)
                        .map_err(PdfError::new_err)?
                    {
                        Some(max_len) => WidgetTextLayout::Comb(max_len),
                        None if ff & TEXT_FIELD_MULTILINE != 0 => WidgetTextLayout::Multiline,
                        None => WidgetTextLayout::SingleLine,
                    }
                } else {
                    WidgetTextLayout::SingleLine
                };
                let align = self.field_quadding(field_id);
                for widget_id in self.visible_field_widgets(field_id)? {
                    self.synthesize_text_appearance(widget_id, value, layout, align, font)?;
                }
            }
            "Btn" if ff & (1 << 16) != 0 => {
                return Err(PdfError::new_err(
                    "pushbutton fields do not have fillable values",
                ));
            }
            "Btn" => {
                {
                    let field = self
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                        .map_err(to_py_err)?;
                    field.set("V", Object::Name(value.as_bytes().to_vec()));
                }
                self.sync_button_widget_appearances(field_id, ff, value)?;
            }
            "Sig" => {
                return Err(PdfError::new_err(
                    "filling signature fields is not supported (see the pyHanko integration for digital signatures)",
                ));
            }
            other => {
                return Err(PdfError::new_err(format!(
                    "unsupported field type: {other:?}"
                )));
            }
        }
        self.synthesize_missing_form_appearances(name)?;
        self.set_need_appearances(!self.form_appearances_complete()?)
    }
}

/// One field as `form_fields` returns it: full name, object number, kind,
/// value, and inherited flags.
pub(crate) type FormFieldTuple = (String, u32, &'static str, Option<String>, i64);

/// Implements [`_Document::form_fields`].
pub(super) fn form_fields(this: &_Document, py: Python<'_>) -> PyResult<Vec<FormFieldTuple>> {
    py.detach(|| {
        Ok(this
            .collect_form_fields()?
            .into_iter()
            .map(|(name, id, ft, ff, value)| {
                let kind = match ft.as_str() {
                    "Tx" => "text",
                    "Sig" => "signature",
                    "Ch" => {
                        if ff & (1 << 17) != 0 {
                            "combobox"
                        } else {
                            "listbox"
                        }
                    }
                    "Btn" => {
                        if ff & (1 << 16) != 0 {
                            "pushbutton"
                        } else if ff & (1 << 15) != 0 {
                            "radio"
                        } else {
                            "checkbox"
                        }
                    }
                    _ => "unknown",
                };
                (name, id.0, kind, value.map(|value| value.to_string()), ff)
            })
            .collect())
    })
}

/// Implements [`_Document::form_field_choices`].
///
/// `/Opt` is read along the bounded `/Parent` chain, as MuPDF reads it. An
/// option is a text string, which is both the export value and the label, or
/// an array of the export value and the label; other entries are skipped.
/// The options stop at the field-value limits: 4,096 items and 1 MiB of
/// encoded and of decoded text.
pub(super) fn form_field_choices(
    this: &_Document,
    py: Python<'_>,
    xref: u32,
) -> PyResult<Vec<(String, String)>> {
    py.detach(|| {
        let Some(field_id) = this
            .collect_form_fields()?
            .into_iter()
            .find(|(_, id, ..)| id.0 == xref)
            .map(|(_, id, ..)| id)
        else {
            return Err(PdfError::new_err(format!("form field not found: {xref}")));
        };
        let Some(options) = this.resolve_field_attr(field_id, b"Opt") else {
            return Ok(Vec::new());
        };
        let Ok(options) = deref_object(&this.doc, &options).as_array() else {
            return Ok(Vec::new());
        };
        if options.len() > MAX_FORM_FIELD_VALUE_ITEMS {
            return Err(PdfError::new_err(format!(
                "AcroForm choice options exceed the {MAX_FORM_FIELD_VALUE_ITEMS}-item safety limit"
            )));
        }
        let mut encoded = 0usize;
        let mut decoded = 0usize;
        let mut text = |object: &Object| {
            bounded_form_text(
                &this.doc,
                object,
                &mut encoded,
                &mut decoded,
                MAX_FORM_FIELD_VALUE_BYTES,
                "choice-option",
            )
        };
        let mut choices = Vec::new();
        for option in options {
            let choice = match deref_object(&this.doc, option) {
                Object::Array(pair) if pair.len() == 2 => {
                    match (text(&pair[0])?, text(&pair[1])?) {
                        (Some(value), Some(label)) => (value, label),
                        _ => continue,
                    }
                }
                object => match text(object)? {
                    Some(value) => {
                        let label = try_clone_form_string(&value, "choice label")?;
                        (value, label)
                    }
                    None => continue,
                },
            };
            try_push_form(&mut choices, choice, "choice options")?;
        }
        Ok(choices)
    })
}

/// Implements [`_Document::form_button_states`].
pub(super) fn form_button_states(
    this: &_Document,
    py: Python<'_>,
    name: &str,
) -> PyResult<Vec<String>> {
    validate_form_field_input(name, None)?;
    py.detach(|| {
        let (field_id, ft) = this
            .collect_form_fields()?
            .into_iter()
            .find(|(field_name, ..)| field_name == name)
            .map(|(_, id, ft, ..)| (id, ft))
            .ok_or_else(|| PdfError::new_err(format!("form field not found: {name:?}")))?;
        if ft.as_str() != "Btn" {
            return Ok(Vec::new());
        }
        let (widget_states, _, _) = this.button_appearance_states(field_id)?;
        let mut states = Vec::new();
        let mut seen = HashSet::new();
        let mut returned_name_bytes = 0usize;
        for (_, widget_state_names) in widget_states {
            for state_name in widget_state_names {
                if !seen.insert(state_name.clone()) {
                    continue;
                }
                if states.len() >= MAX_FORM_BUTTON_STATE_NAMES {
                    return Err(PdfError::new_err(format!(
                        "AcroForm button states exceed the {MAX_FORM_BUTTON_STATE_NAMES}-name safety limit"
                    )));
                }
                let state = String::from_utf8_lossy(&state_name).into_owned();
                add_form_budget(
                    &mut returned_name_bytes,
                    state.len(),
                    MAX_FORM_BUTTON_STATE_NAME_BYTES,
                    "returned button-state name",
                )?;
                states.push(state);
            }
        }
        Ok(states)
    })
}

/// Implements [`_Document::set_form_field`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn set_form_field(
    this: &mut _Document,
    py: Python<'_>,
    name: &str,
    value: &str,
    font_data: Option<Vec<u8>>,
    font_index: u32,
    max_font_size: Option<usize>,
    weight: Option<u16>,
) -> PyResult<()> {
    validate_form_field_input(name, Some(value))?;
    validate_font_input(font_data.as_deref(), max_font_size)?;
    let font = font_data.map(|data| FormFont::Data {
        data: Arc::new(data),
        index: font_index,
        weight,
    });
    this.fill_form_field(py, name, value, font)
}

/// Implements [`_Document::set_form_field_file`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn set_form_field_file(
    this: &mut _Document,
    py: Python<'_>,
    name: &str,
    value: &str,
    path: &str,
    font_index: u32,
    max_font_size: Option<usize>,
    package_font: Option<(String, String)>,
    weight: Option<u16>,
) -> PyResult<()> {
    validate_form_field_input(name, Some(value))?;
    let data = load_generation_font(py, path, package_font, max_font_size)?;
    let font = FormFont::Data {
        data,
        index: font_index,
        weight,
    };
    this.fill_form_field(py, name, value, Some(font))
}

/// Implements [`_Document::set_form_field_font`].
pub(super) fn set_form_field_font(
    this: &mut _Document,
    py: Python<'_>,
    name: &str,
    value: &str,
    font: &Bound<'_, _Font>,
) -> PyResult<()> {
    validate_form_field_input(name, Some(value))?;
    let face = font.get().face().ok_or_else(|| {
        PdfError::new_err("form appearances draw an OpenType Font, not a Standard 14 font")
    })?;
    this.fill_form_field(py, name, value, Some(FormFont::Face(face)))
}

/// One widget as `read_widgets` returns it: its position in `/Annots`, its
/// object number (0 for a direct dictionary), the object number of its
/// terminal field (0 when no field of the form owns it), its display
/// rectangle, `/F` flags, its `/MK` border colour, background colour, and
/// caption, its border, the font resource, size, and colour of the `/DA` it
/// draws with, the state names of its normal appearance, and `/AS`.
pub(super) type WidgetTuple = (
    u32,
    u32,
    u32,
    (f64, f64, f64, f64),
    i64,
    (Vec<f64>, Vec<f64>, Option<String>),
    Option<(f64, String, Vec<f64>)>,
    Option<(String, f64, (f64, f64, f64))>,
    Vec<String>,
    Option<String>,
);

/// What one form field records about a widget it owns: the field's object,
/// its kind, flags, and value.
type WidgetField = (ObjectId, Arc<String>, i64, Option<Arc<str>>);

impl _Document {
    /// Map each widget object of the form to the terminal field that owns
    /// it, under the field-tree limits.
    fn widget_fields(&self) -> PyResult<HashMap<ObjectId, WidgetField>> {
        let mut owners = HashMap::new();
        for (_, field_id, kind, flags, value) in self.collect_form_fields()? {
            for widget in self.field_widgets(field_id)? {
                owners.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow the widget map: {error}"))
                })?;
                owners
                    .entry(widget)
                    .or_insert((field_id, Arc::clone(&kind), flags, value.clone()));
            }
        }
        Ok(owners)
    }

    fn widget_item(
        &self,
        index: u32,
        xref: u32,
        dict: &Dictionary,
        (crop, rotation): ([f64; 4], i64),
        owners: &HashMap<ObjectId, WidgetField>,
        budget: &mut (usize, usize),
    ) -> PyResult<Option<WidgetTuple>> {
        let doc = &*self.doc;
        if !matches!(
            dict.get(b"Subtype")
                .map(|value| deref_object(doc, value))
                .and_then(Object::as_name),
            Ok(b"Widget")
        ) {
            return Ok(None);
        }
        let Some(rect) = resolve_box(doc, dict, b"Rect") else {
            return Ok(None);
        };
        let display = draw::pdf_rect_to_display(crop, rotation, rect);
        let flags = dict
            .get(b"F")
            .map(|value| deref_object(doc, value))
            .and_then(Object::as_i64)
            .unwrap_or(0);
        let mark = dict
            .get(b"MK")
            .ok()
            .and_then(|value| deref_dict_ref(doc, value));
        let (encoded, returned) = budget;
        let caption = match mark.and_then(|mark| mark.get(b"CA").ok()) {
            Some(caption) => {
                bounded_annotation_text(doc, caption, encoded, returned, "widget caption")?
            }
            None => None,
        };
        let colors = (
            mark.map(|mark| annotation_color(doc, mark, b"BC"))
                .unwrap_or_default(),
            mark.map(|mark| annotation_color(doc, mark, b"BG"))
                .unwrap_or_default(),
            caption,
        );
        let field = if xref == 0 {
            None
        } else {
            owners.get(&(xref, 0)).or_else(|| {
                owners
                    .iter()
                    .find(|(id, _)| id.0 == xref)
                    .map(|(_, field)| field)
            })
        };
        let da = if xref == 0 {
            None
        } else {
            self.widget_default_appearance((xref, 0))
        }
        .map(|da| (da.font, da.size, da.color));
        let mut states = Vec::new();
        if let Some(normal) = dict
            .get(b"AP")
            .ok()
            .and_then(|appearance| deref_dict_ref(doc, appearance))
            .and_then(|appearance| appearance.get(b"N").ok())
            .and_then(|normal| deref_dict_ref(doc, normal))
        {
            for (name, _) in normal {
                states.push(bounded_annotation_bytes(
                    name,
                    encoded,
                    returned,
                    "button state",
                )?);
            }
        }
        let current = match dict.get(b"AS").map(|value| deref_object(doc, value)) {
            Ok(Object::Name(name)) => Some(bounded_annotation_bytes(
                name,
                encoded,
                returned,
                "button state",
            )?),
            _ => None,
        };
        Ok(Some((
            index,
            xref,
            field.map_or(0, |field| field.0.0),
            (display[0], display[1], display[2], display[3]),
            flags,
            colors,
            annotation_appearance::border(doc, dict).map(|border| {
                (
                    border.width,
                    String::from_utf8_lossy(&border.style).into_owned(),
                    border.dashes,
                )
            }),
            da,
            states,
            current,
        )))
    }
}

/// Implements [`_Document::read_widgets`].
pub(super) fn read_widgets(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<Vec<WidgetTuple>> {
    py.detach(|| {
        let geometry = this.page_display_geometry(page_number)?;
        let page_id = this.page_id(page_number)?;
        let Some(items) = this.page_annotation_items(page_id)? else {
            return Ok(Vec::new());
        };
        let owners = this.widget_fields()?;
        let mut budget = (0, 0);
        let mut out = Vec::new();
        for (index, item) in items.iter().enumerate() {
            let (xref, dict) = match item {
                Object::Reference(id) => match this.doc.get_object(*id).and_then(Object::as_dict) {
                    Ok(dict) => (id.0, dict),
                    Err(_) => continue,
                },
                Object::Dictionary(dict) => (0, dict),
                _ => continue,
            };
            let index = u32::try_from(index).unwrap_or(u32::MAX);
            if let Some(widget) =
                this.widget_item(index, xref, dict, geometry, &owners, &mut budget)?
            {
                try_push_form(&mut out, widget, "widgets")?;
            }
        }
        Ok(out)
    })
}

/// Implements [`_Document::read_widget`].
pub(super) fn read_widget(
    this: &_Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
) -> PyResult<WidgetTuple> {
    py.detach(|| {
        let geometry = this.page_display_geometry(page_number)?;
        let page_id = this.page_id(page_number)?;
        let (index, dict) = this.annotation_at(page_id, index, xref)?;
        let owners = this.widget_fields()?;
        this.widget_item(index, xref, dict, geometry, &owners, &mut (0, 0))?
            .ok_or_else(|| {
                StaleObjectError::new_err(format!("annotation {xref} is no longer a widget"))
            })
    })
}

/// An edit of a widget's geometry and style, as the setters of `Widget`
/// pass it; `clear` names the properties to remove.
#[derive(FromPyObject)]
#[pyo3(from_item_all)]
pub(super) struct WidgetEdit {
    #[pyo3(default)]
    rect: Option<(f64, f64, f64, f64)>,
    #[pyo3(default)]
    border: Option<(f64, String, Vec<f64>)>,
    #[pyo3(default)]
    border_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    background_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    caption: Option<String>,
    #[pyo3(default)]
    text_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    font_name: Option<String>,
    #[pyo3(default)]
    font_size: Option<f64>,
    #[pyo3(default)]
    flags: Option<i64>,
    /// The OpenType face that text and choice values are drawn with.
    #[pyo3(default)]
    font: Option<Py<_Font>>,
    /// Always given, so that the edit is not all defaults.
    clear: Vec<String>,
}

fn rgb_entry(name: &str, (red, green, blue): (f64, f64, f64)) -> PyResult<Object> {
    if ![red, green, blue]
        .iter()
        .all(|component| component.is_finite() && (0.0..=1.0).contains(component))
    {
        return Err(PdfError::new_err(format!(
            "a widget {name} has components from 0 to 1"
        )));
    }
    Ok(Object::Array(vec![
        Object::Real(red as f32),
        Object::Real(green as f32),
        Object::Real(blue as f32),
    ]))
}

/// Implements [`_Document::edit_widget`]: change a widget's geometry and
/// style, then draw its appearance again for its field's value, restoring
/// the document on any error.
pub(super) fn edit_widget(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    index: u32,
    xref: u32,
    edit: WidgetEdit,
) -> PyResult<u32> {
    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let page_id = this.page_id(page_number)?;
    let (_, widget) = this.annotation_at(page_id, index, xref)?;
    if !matches!(
        widget.get(b"Subtype").and_then(Object::as_name),
        Ok(b"Widget")
    ) {
        return Err(StaleObjectError::new_err(format!(
            "annotation {xref} is no longer a widget"
        )));
    }
    let mut dict = widget.clone();
    let mut mark = dict
        .get(b"MK")
        .ok()
        .and_then(|mark| deref_dict_ref(&this.doc, mark))
        .cloned()
        .unwrap_or_default();
    if let Some(caption) = &edit.caption {
        validate_annotation_input([caption.as_bytes()], "caption")?;
        mark.set("CA", text_string(caption));
    }
    if let Some(color) = edit.border_color {
        mark.set("BC", rgb_entry("border colour", color)?);
    }
    if let Some(color) = edit.background_color {
        mark.set("BG", rgb_entry("background colour", color)?);
    }
    for property in &edit.clear {
        match property.as_str() {
            "border_color" => {
                mark.remove(b"BC");
            }
            "background_color" => {
                mark.remove(b"BG");
            }
            "caption" => {
                mark.remove(b"CA");
            }
            "border" => {
                dict.remove(b"BS");
                dict.remove(b"Border");
            }
            other => {
                return Err(PdfError::new_err(format!(
                    "a widget property that cannot be removed: {other:?}"
                )));
            }
        }
    }
    dict.set("MK", mark);
    if let Some((width, style, dashes)) = &edit.border {
        if !(width.is_finite() && *width >= 0.0)
            || !matches!(style.as_str(), "S" | "D" | "B" | "I" | "U")
            || dashes.len() > annotation_appearance::MAX_BORDER_DASHES
            || (!dashes.is_empty() && !annotation_appearance::valid_dashes(dashes))
            || (style == "D") == dashes.is_empty()
        {
            return Err(PdfError::new_err(
                "the border is not one that pylopdf writes",
            ));
        }
        let mut border = dictionary! {
            "W" => Object::Real(*width as f32),
            "S" => Object::Name(style.as_bytes().to_vec()),
        };
        if !dashes.is_empty() {
            border.set(
                "D",
                Object::Array(
                    dashes
                        .iter()
                        .map(|&dash| Object::Real(dash as f32))
                        .collect(),
                ),
            );
        }
        dict.remove(b"Border");
        dict.set("BS", border);
    }
    if let Some(flags) = edit.flags {
        if !(0..=1023).contains(&flags) {
            return Err(PdfError::new_err("annotation flags are bits 1 to 10"));
        }
        dict.set("F", flags);
    }
    if let Some((x0, y0, x1, y1)) = edit.rect {
        if ![x0, y0, x1, y1].iter().all(|value| value.is_finite()) || x1 <= x0 || y1 <= y0 {
            return Err(PdfError::new_err(
                "a widget rectangle is finite and not empty",
            ));
        }
        let quad = draw::display_rect_quad_pdf(crop, rotation, [x0, y0, x1, y1]);
        let rect = draw::bounding_rect_iter(quad.iter().copied());
        dict.set(
            "Rect",
            Object::Array(
                rect.iter()
                    .map(|&value| Object::Real(value as f32))
                    .collect(),
            ),
        );
    }
    let styled = edit.text_color.is_some() || edit.font_name.is_some() || edit.font_size.is_some();
    if styled {
        let current = if xref == 0 {
            None
        } else {
            this.widget_default_appearance((xref, 0))
        }
        .unwrap_or_else(|| annotation_appearance::parse_default_appearance(b""));
        let (red, green, blue) = edit.text_color.unwrap_or(current.color);
        rgb_entry("text colour", (red, green, blue))?;
        let resource = match &edit.font_name {
            Some(name) => annotation_appearance::DA_FONTS
                .iter()
                .find(|(_, font)| font == name)
                .map(|(resource, _)| (*resource).to_owned())
                .ok_or_else(|| PdfError::new_err(format!("unknown Standard 14 font {name:?}")))?,
            None => current.font.clone(),
        };
        let size = edit.font_size.unwrap_or(current.size);
        if !(size.is_finite() && (0.0..=10_000.0).contains(&size)) {
            return Err(PdfError::new_err(
                "a widget font size is from 0, which fits the text, to 10000",
            ));
        }
        dict.set(
            "DA",
            Object::string_literal(format!(
                "/{resource} {} Tf {} {} {} rg",
                draw::fmt(size),
                draw::fmt(red),
                draw::fmt(green),
                draw::fmt(blue)
            )),
        );
    }
    let font = edit
        .font
        .as_ref()
        .map(|font| {
            Python::attach(|py| font.borrow(py).face()).ok_or_else(|| {
                PdfError::new_err("form appearances draw an OpenType Font, not a Standard 14 font")
            })
        })
        .transpose()?
        .map(FormFont::Face);

    let (_, id) = edited_annotation(this, page_id, index, xref)?;
    py.detach(|| {
        let original = this.doc.clone();
        if let Ok(object) = this.doc.get_object_mut(id) {
            *object = Object::Dictionary(dict);
        }
        let result = this.redraw_widget(id, font.as_ref());
        if let Err(error) = result {
            this.doc.replace(original);
            return Err(error);
        }
        Ok(())
    })?;
    this.invalidate_hayro_pdf();
    Ok(id.0)
}

impl _Document {
    /// Draw one widget's appearance again for its field's current value:
    /// text and choice values, every state of a check box or radio button,
    /// and a pushbutton's caption. Widgets of signature fields and of no
    /// field keep theirs.
    fn redraw_widget(&mut self, widget_id: ObjectId, font: Option<&FormFont>) -> PyResult<()> {
        let owners = self.widget_fields()?;
        let Some((field_id, kind, flags, value)) = owners.get(&widget_id).cloned() else {
            return Ok(());
        };
        match kind.as_str() {
            "Tx" | "Ch" => {
                let value = value.as_deref().unwrap_or_default().to_owned();
                let layout = if kind.as_str() == "Tx" {
                    match self
                        .field_comb_max_len(field_id, flags)
                        .map_err(PdfError::new_err)?
                    {
                        Some(max_len) => WidgetTextLayout::Comb(max_len),
                        None if flags & TEXT_FIELD_MULTILINE != 0 => WidgetTextLayout::Multiline,
                        None => WidgetTextLayout::SingleLine,
                    }
                } else {
                    WidgetTextLayout::SingleLine
                };
                let align = self.field_quadding(field_id);
                self.synthesize_text_appearance(widget_id, &value, layout, align, font)?;
            }
            "Btn" if flags & (1 << 16) != 0 => {
                let widget = self
                    .doc
                    .get_dictionary(widget_id)
                    .map_err(to_py_err)?
                    .clone();
                let da = self.widget_default_appearance(widget_id);
                let style = form::WidgetStyle::from_widget(&self.doc, &widget, da.as_ref())
                    .map_err(PdfError::new_err)?;
                let caption = widget
                    .get(b"MK")
                    .ok()
                    .and_then(|mark| deref_dict_ref(&self.doc, mark))
                    .and_then(|mark| mark.get(b"CA").ok())
                    .and_then(|caption| decode_text_string(deref_object(&self.doc, caption)).ok())
                    .unwrap_or_default();
                let text = if caption.is_empty() || !draw::is_winansi(&caption) {
                    Vec::new()
                } else {
                    form::standard_text_ops(&style, &caption, false, 1)
                        .map_err(PdfError::new_err)?
                };
                let resources = (!text.is_empty()).then(|| {
                    let mut font = dictionary! {
                        "Type" => "Font",
                        "Subtype" => "Type1",
                        "BaseFont" => style.text.base_font,
                    };
                    if !matches!(style.text.base_font, "Symbol" | "ZapfDingbats") {
                        font.set("Encoding", "WinAnsiEncoding");
                    }
                    let font_id = self.doc.add_object(font);
                    dictionary! {
                        "Font" => dictionary! {
                            style.text.resource.as_bytes() => Object::Reference(font_id),
                        },
                    }
                });
                let content = style.decorated_text_ops(&text).map_err(PdfError::new_err)?;
                let appearance = self.doc.add_object(style.stream(resources, content));
                self.set_widget_normal_appearance(widget_id, Some(Object::Reference(appearance)))?;
            }
            "Btn" => {
                let radio = flags & (1 << 15) != 0;
                let widget = self
                    .doc
                    .get_dictionary(widget_id)
                    .map_err(to_py_err)?
                    .clone();
                let style = form::WidgetStyle::from_widget(&self.doc, &widget, None)
                    .map_err(PdfError::new_err)?;
                let mut normal = self.button_normal_appearance(widget_id);
                let mut names: Vec<Vec<u8>> = normal.iter().map(|(name, _)| name.clone()).collect();
                if names.is_empty() {
                    names.push(b"Off".to_vec());
                    names.push(b"Yes".to_vec());
                }
                for name in names {
                    let on = name != b"Off";
                    let appearance = self.doc.add_object(style.button_stream(on, radio));
                    normal.set(name, Object::Reference(appearance));
                }
                self.set_widget_normal_appearance(widget_id, Some(Object::Dictionary(normal)))?;
            }
            _ => {}
        }
        Ok(())
    }
}

/// Implements [`_Document::field_widget_locations`]: the one-based page and
/// object number of each widget of a field that a page lists, in page order.
pub(super) fn field_widget_locations(
    this: &_Document,
    py: Python<'_>,
    xref: u32,
) -> PyResult<Vec<(u32, u32)>> {
    py.detach(|| {
        let field_id = this
            .collect_form_fields()?
            .into_iter()
            .map(|(_, id, ..)| id)
            .find(|id| id.0 == xref)
            .ok_or_else(|| {
                StaleObjectError::new_err(format!("object {xref} is no longer a field of the form"))
            })?;
        let widgets: HashSet<ObjectId> = this.field_widgets(field_id)?.into_iter().collect();
        let mut out = Vec::new();
        for page_number in 1..=u32::try_from(this.doc.page_count()?).unwrap_or(u32::MAX) {
            let page_id = this.page_id(page_number)?;
            let Some(items) = this.page_annotation_items(page_id)? else {
                continue;
            };
            for item in items {
                if let Object::Reference(id) = item
                    && widgets.contains(id)
                {
                    try_push_form(&mut out, (page_number, id.0), "field widgets")?;
                }
            }
        }
        Ok(out)
    })
}

/// A field's label (`/TU`), maximum length (inherited `/MaxLen`), default
/// value (inherited `/DV`), and the document's `/NeedAppearances`.
pub(super) type FieldDetails = (Option<String>, Option<i64>, Option<String>, bool);

/// Implements [`_Document::field_details`].
pub(super) fn field_details(this: &_Document, py: Python<'_>, xref: u32) -> PyResult<FieldDetails> {
    py.detach(|| {
        let field_id = this.form_field_id(xref)?;
        let doc = &*this.doc;
        let field = doc.get_dictionary(field_id).map_err(to_py_err)?;
        let mut budget = (0usize, 0usize);
        let label = match field.get(b"TU") {
            Ok(label) => {
                bounded_annotation_text(doc, label, &mut budget.0, &mut budget.1, "field label")?
            }
            Err(_) => None,
        };
        let max_length = this
            .resolve_field_attr(field_id, b"MaxLen")
            .and_then(|value| deref_object(doc, &value).as_i64().ok())
            .filter(|value| *value >= 0);
        let default = match this.resolve_field_attr(field_id, b"DV") {
            Some(Object::Name(name)) => Some(bounded_annotation_bytes(
                &name,
                &mut budget.0,
                &mut budget.1,
                "field default value",
            )?),
            Some(value @ Object::String(..)) => bounded_annotation_text(
                doc,
                &value,
                &mut budget.0,
                &mut budget.1,
                "field default value",
            )?,
            _ => None,
        };
        let need = doc
            .catalog()
            .ok()
            .and_then(|catalog| catalog.get(b"AcroForm").ok())
            .and_then(|form| deref_dict_ref(doc, form))
            .and_then(|form| form.get(b"NeedAppearances").ok())
            .and_then(|value| deref_object(doc, value).as_bool().ok())
            .unwrap_or(false);
        Ok((label, max_length, default, need))
    })
}

/// An edit of a field's properties, as the setters of `Field` pass it.
#[derive(FromPyObject)]
#[pyo3(from_item_all)]
pub(super) struct FieldEdit {
    #[pyo3(default)]
    label: Option<String>,
    #[pyo3(default)]
    max_length: Option<i64>,
    #[pyo3(default)]
    flags: Option<i64>,
    #[pyo3(default)]
    choices: Option<Vec<(String, String)>>,
    #[pyo3(default)]
    default_value: Option<String>,
    #[pyo3(default)]
    reset: Option<bool>,
    #[pyo3(default)]
    font: Option<Py<_Font>>,
    /// Always given, so that the edit is not all defaults.
    clear: Vec<String>,
}

/// The `/Ff` bits that decide a field's kind, which an edit keeps.
const FIELD_KIND_FLAGS: i64 = (1 << 15) | (1 << 16) | (1 << 17);

/// The most options that a choice field takes.
const MAX_FIELD_CHOICES: usize = 4_096;

impl _Document {
    /// The object of the terminal field with object number `xref`.
    fn form_field_id(&self, xref: u32) -> PyResult<ObjectId> {
        self.collect_form_fields()?
            .into_iter()
            .map(|(_, id, ..)| id)
            .find(|id| id.0 == xref)
            .ok_or_else(|| {
                StaleObjectError::new_err(format!("object {xref} is no longer a field of the form"))
            })
    }
}

/// Implements [`_Document::edit_field`]: change a field's label, maximum
/// length, flags, choices, or default value, or reset it, then draw its
/// widgets again for its value; on any error the document is restored.
pub(super) fn edit_field(
    this: &mut _Document,
    py: Python<'_>,
    xref: u32,
    edit: FieldEdit,
) -> PyResult<()> {
    let field_id = this.form_field_id(xref)?;
    let (name, kind, flags, value) = this
        .collect_form_fields()?
        .into_iter()
        .find(|(_, id, ..)| *id == field_id)
        .map(|(name, _, kind, flags, value)| (name, kind, flags, value))
        .ok_or_else(|| StaleObjectError::new_err("the field left the form"))?;
    let texts = [&edit.label, &edit.default_value];
    validate_annotation_input(
        texts
            .iter()
            .map(|text| text.as_deref().unwrap_or_default().as_bytes())
            .chain(
                edit.choices
                    .iter()
                    .flatten()
                    .flat_map(|(value, label)| [value.as_bytes(), label.as_bytes()]),
            ),
        "field texts",
    )?;
    if let Some(new) = edit.flags {
        if !(0..=i64::from(u32::MAX)).contains(&new) {
            return Err(PdfError::new_err("field flags are a 32-bit value"));
        }
        if new & FIELD_KIND_FLAGS != flags & FIELD_KIND_FLAGS {
            return Err(PdfError::new_err(
                "the radio, pushbutton, and combo box flags decide a field's kind; create a field of the other kind instead",
            ));
        }
    }
    if let Some(length) = edit.max_length
        && (kind.as_str() != "Tx" || !(0..=1_000_000).contains(&length))
    {
        return Err(PdfError::new_err(
            "only a text field has a maximum length, from 0 to 1000000",
        ));
    }
    if let Some(choices) = &edit.choices
        && (kind.as_str() != "Ch" || choices.len() > MAX_FIELD_CHOICES)
    {
        return Err(PdfError::new_err(format!(
            "only a choice field has choices, at most {MAX_FIELD_CHOICES}"
        )));
    }
    let font = edit
        .font
        .as_ref()
        .map(|font| {
            Python::attach(|py| font.borrow(py).face()).ok_or_else(|| {
                PdfError::new_err("form appearances draw an OpenType Font, not a Standard 14 font")
            })
        })
        .transpose()?
        .map(FormFont::Face);
    let reset = edit.reset.unwrap_or(false);
    let default = if reset {
        this.resolve_field_attr(field_id, b"DV")
    } else {
        None
    };
    let result = py.detach(|| {
        let original = this.doc.clone();
        let outcome = (|| -> PyResult<()> {
            let field = this
                .doc
                .get_object_mut(field_id)
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            if let Some(label) = &edit.label {
                field.set("TU", form_text_string(label));
            }
            if let Some(length) = edit.max_length {
                field.set("MaxLen", length);
            }
            if let Some(new) = edit.flags {
                field.set("Ff", new);
            }
            if let Some(text) = &edit.default_value {
                field.set(
                    "DV",
                    if kind.as_str() == "Btn" {
                        Object::Name(text.as_bytes().to_vec())
                    } else {
                        form_text_string(text)
                    },
                );
            }
            if let Some(choices) = &edit.choices {
                field.set(
                    "Opt",
                    Object::Array(
                        choices
                            .iter()
                            .map(|(value, label)| {
                                if value == label {
                                    form_text_string(value)
                                } else {
                                    Object::Array(vec![
                                        form_text_string(value),
                                        form_text_string(label),
                                    ])
                                }
                            })
                            .collect(),
                    ),
                );
            }
            for property in &edit.clear {
                let key: &[u8] = match property.as_str() {
                    "label" => b"TU",
                    "max_length" => b"MaxLen",
                    "default_value" => b"DV",
                    other => {
                        return Err(PdfError::new_err(format!(
                            "a field property that cannot be removed: {other:?}"
                        )));
                    }
                };
                field.remove(key);
            }
            // Draw the widgets again for the value the field ends with.
            let target = if reset {
                match (&default, kind.as_str()) {
                    (Some(Object::Name(state)), "Btn") => {
                        Some(String::from_utf8_lossy(state).into_owned())
                    }
                    (Some(value @ Object::String(..)), _) => {
                        Some(decode_text_string(value).unwrap_or_default())
                    }
                    (_, "Btn") => Some("Off".to_owned()),
                    _ => None,
                }
            } else {
                value.as_deref().map(str::to_owned)
            };
            let fillable = match kind.as_str() {
                "Tx" | "Ch" => true,
                "Btn" => flags & (1 << 16) == 0,
                _ => false,
            };
            if fillable {
                this.set_form_field_inner(
                    &name,
                    target.as_deref().unwrap_or_default(),
                    font.as_ref(),
                )?;
                if target.is_none()
                    && let Ok(field) = this
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                {
                    field.remove(b"V");
                }
            }
            Ok(())
        })();
        if let Err(error) = outcome {
            this.doc.replace(original);
            return Err(error);
        }
        Ok(())
    });
    if result.is_ok() {
        this.invalidate_hayro_pdf();
    }
    result
}

/// Implements [`_Document::set_need_appearances_flag`].
pub(super) fn set_need_appearances_flag(this: &mut _Document, value: bool) -> PyResult<()> {
    let has_form = this
        .doc
        .catalog()
        .ok()
        .is_some_and(|catalog| catalog.has(b"AcroForm"));
    if !has_form {
        return Err(PdfError::new_err("the document has no interactive form"));
    }
    this.invalidate_hayro_pdf_keeping_placements();
    this.set_need_appearances(value)
}

/// A field to create, or a widget to add to an existing field of the same
/// kind, as `page.widgets.add` passes a field specification.
#[derive(FromPyObject)]
#[pyo3(from_item_all)]
pub(super) struct FieldSpec {
    /// `text`, `checkbox`, `radio`, `combobox`, `listbox`, `pushbutton`, or
    /// `signature`.
    kind: String,
    name: String,
    rect: (f64, f64, f64, f64),
    #[pyo3(default)]
    value: Option<String>,
    #[pyo3(default)]
    default_value: Option<String>,
    #[pyo3(default)]
    flags: i64,
    #[pyo3(default)]
    label: Option<String>,
    #[pyo3(default)]
    max_length: Option<i64>,
    #[pyo3(default)]
    choices: Option<Vec<(String, String)>>,
    #[pyo3(default)]
    on_state: Option<String>,
    #[pyo3(default)]
    caption: Option<String>,
    #[pyo3(default)]
    border: Option<(f64, String, Vec<f64>)>,
    #[pyo3(default)]
    border_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    background_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    text_color: Option<(f64, f64, f64)>,
    #[pyo3(default)]
    font_name: Option<String>,
    #[pyo3(default)]
    font_size: Option<f64>,
    #[pyo3(default)]
    align: Option<u8>,
    #[pyo3(default)]
    font: Option<Py<_Font>>,
}

/// The `/FT` and the `/Ff` bits that make a field of each kind.
fn field_kind(kind: &str) -> PyResult<(&'static str, i64)> {
    Ok(match kind {
        "text" => ("Tx", 0),
        "checkbox" => ("Btn", 0),
        // Radio groups keep one button on, as Acrobat creates them.
        "radio" => ("Btn", (1 << 15) | (1 << 14)),
        "pushbutton" => ("Btn", 1 << 16),
        "combobox" => ("Ch", 1 << 17),
        "listbox" => ("Ch", 0),
        "signature" => ("Sig", 0),
        other => return Err(PdfError::new_err(format!("unknown field kind {other:?}"))),
    })
}

/// The kind of an existing field, from its `/FT` and `/Ff`.
fn existing_kind(field_type: &str, flags: i64) -> &'static str {
    match field_type {
        "Tx" => "text",
        "Ch" if flags & (1 << 17) != 0 => "combobox",
        "Ch" => "listbox",
        "Btn" if flags & (1 << 16) != 0 => "pushbutton",
        "Btn" if flags & (1 << 15) != 0 => "radio",
        "Btn" => "checkbox",
        "Sig" => "signature",
        _ => "unknown",
    }
}

/// The field entries that a merged field and widget dictionary moves to a
/// new parent when the field gains a second widget.
const FIELD_KEYS: [&[u8]; 12] = [
    b"FT", b"T", b"TU", b"TM", b"Ff", b"V", b"DV", b"AA", b"Opt", b"MaxLen", b"TI", b"I",
];

impl _Document {
    /// Return the AcroForm dictionary as an object, moving a direct one
    /// into an object and creating an empty one with Helvetica as `/DR`.
    fn acroform_object(&mut self) -> PyResult<ObjectId> {
        let current = self
            .doc
            .catalog()
            .map_err(to_py_err)?
            .get(b"AcroForm")
            .ok()
            .cloned();
        let id = match current {
            Some(Object::Reference(id)) if self.doc.get_dictionary(id).is_ok() => return Ok(id),
            Some(Object::Dictionary(form)) => self.doc.add_object(form),
            _ => {
                let helvetica = self.doc.add_object(dictionary! {
                    "Type" => "Font",
                    "Subtype" => "Type1",
                    "BaseFont" => "Helvetica",
                    "Encoding" => "WinAnsiEncoding",
                });
                self.doc.add_object(dictionary! {
                    "Fields" => Object::Array(Vec::new()),
                    "DA" => Object::string_literal("/Helv 0 Tf 0 g"),
                    "DR" => dictionary! { "Font" => dictionary! { "Helv" => helvetica } },
                })
            }
        };
        self.doc
            .catalog_mut()
            .map_err(to_py_err)?
            .set("AcroForm", id);
        Ok(id)
    }

    /// The AcroForm's `/Fields`, made a direct array of the form dictionary
    /// when it was an indirect one.
    fn form_fields_mut(&mut self, form_id: ObjectId) -> PyResult<&mut Vec<Object>> {
        let fields = match self
            .doc
            .get_dictionary(form_id)
            .map_err(to_py_err)?
            .get(b"Fields")
        {
            Ok(Object::Reference(id)) => self
                .doc
                .get_object(*id)
                .and_then(Object::as_array)
                .cloned()
                .unwrap_or_default(),
            Ok(Object::Array(fields)) => fields.clone(),
            _ => Vec::new(),
        };
        let form = self
            .doc
            .get_object_mut(form_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        form.set("Fields", Object::Array(fields));
        form.get_mut(b"Fields")
            .and_then(Object::as_array_mut)
            .map_err(to_py_err)
    }

    /// Remove `id` from the `/Kids` of `parent`, or from `/Fields` and `/CO`
    /// when `parent` is `None`.
    fn detach_form_node(&mut self, parent: Option<ObjectId>, id: ObjectId) -> PyResult<()> {
        let is_id = |item: &Object| matches!(item, Object::Reference(item) if *item == id);
        match parent {
            Some(parent) => {
                let kids = match self
                    .doc
                    .get_dictionary(parent)
                    .map_err(to_py_err)?
                    .get(b"Kids")
                {
                    Ok(Object::Reference(kids)) => *kids,
                    _ => {
                        if let Ok(Object::Array(kids)) = self
                            .doc
                            .get_object_mut(parent)
                            .and_then(Object::as_dict_mut)
                            .and_then(|parent| parent.get_mut(b"Kids"))
                        {
                            kids.retain(|item| !is_id(item));
                        }
                        return Ok(());
                    }
                };
                if let Ok(kids) = self.doc.get_object_mut(kids).and_then(Object::as_array_mut) {
                    kids.retain(|item| !is_id(item));
                }
            }
            None => {
                let form_id = self.acroform_object()?;
                self.form_fields_mut(form_id)?.retain(|item| !is_id(item));
                if let Ok(Object::Array(order)) = self
                    .doc
                    .get_object_mut(form_id)
                    .and_then(Object::as_dict_mut)
                    .and_then(|form| form.get_mut(b"CO"))
                {
                    order.retain(|item| !is_id(item));
                }
            }
        }
        Ok(())
    }

    /// Take a widget out of every page's `/Annots`.
    fn unlist_widget(&mut self, widget: ObjectId) -> PyResult<()> {
        let pages = self.doc.page_count()?;
        for page_number in 1..=u32::try_from(pages).unwrap_or(u32::MAX) {
            let page_id = self.page_id(page_number)?;
            let listed = self.page_annotation_items(page_id)?.is_some_and(|items| {
                items
                    .iter()
                    .any(|item| matches!(item, Object::Reference(id) if *id == widget))
            });
            if listed {
                self.indirect_page_annotations_mut(page_id)?
                    .retain(|item| !matches!(item, Object::Reference(id) if *id == widget));
            }
        }
        Ok(())
    }

    /// Remove a terminal field with its widgets: from the pages, and from
    /// the form tree, removing a parent that it leaves without kids.
    fn remove_form_field(&mut self, field_id: ObjectId) -> PyResult<()> {
        for widget in self.field_widgets(field_id)? {
            self.unlist_widget(widget)?;
        }
        let mut node = field_id;
        loop {
            let parent = self
                .doc
                .get_dictionary(node)
                .ok()
                .and_then(|dict| dict.get(b"Parent").ok())
                .and_then(|parent| parent.as_reference().ok());
            self.detach_form_node(parent, node)?;
            let Some(parent) = parent else { break };
            let empty = field_kids(
                &self.doc,
                self.doc.get_dictionary(parent).map_err(to_py_err)?,
            )
            .is_none_or(<[Object]>::is_empty);
            let named = self
                .doc
                .get_dictionary(parent)
                .is_ok_and(|parent| parent.has(b"T") || parent.has(b"FT"));
            if !empty || !named {
                break;
            }
            node = parent;
        }
        Ok(())
    }
}

/// Implements [`_Document::add_widget`]: create the field that `spec`
/// describes with its first widget, or add a widget to the existing field
/// of that name and kind, and draw it; return the widget's object number.
pub(super) fn add_widget(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    spec: FieldSpec,
) -> PyResult<u32> {
    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let page_id = this.page_id(page_number)?;
    let (field_type, kind_flags) = field_kind(&spec.kind)?;
    validate_form_field_input(&spec.name, spec.value.as_deref())?;
    validate_annotation_input(
        [
            spec.label.as_deref().unwrap_or_default().as_bytes(),
            spec.caption.as_deref().unwrap_or_default().as_bytes(),
            spec.default_value.as_deref().unwrap_or_default().as_bytes(),
            spec.on_state.as_deref().unwrap_or_default().as_bytes(),
        ]
        .into_iter()
        .chain(
            spec.choices
                .iter()
                .flatten()
                .flat_map(|(value, label)| [value.as_bytes(), label.as_bytes()]),
        ),
        "field texts",
    )?;
    if spec.name.is_empty() {
        return Err(PdfError::new_err("a field has a name"));
    }
    if !(0..=i64::from(u32::MAX)).contains(&spec.flags)
        || spec.flags & (FIELD_KIND_FLAGS | (1 << 14)) != 0
    {
        return Err(PdfError::new_err(
            "field flags are a 32-bit value without the bits that the kind sets",
        ));
    }
    let (x0, y0, x1, y1) = spec.rect;
    if ![x0, y0, x1, y1].iter().all(|value| value.is_finite()) || x1 <= x0 || y1 <= y0 {
        return Err(PdfError::new_err(
            "a widget rectangle is finite and not empty",
        ));
    }
    if spec
        .choices
        .as_ref()
        .is_some_and(|choices| choices.len() > MAX_FIELD_CHOICES)
    {
        return Err(PdfError::new_err(format!(
            "a choice field has at most {MAX_FIELD_CHOICES} choices"
        )));
    }
    let on_state = spec.on_state.clone().unwrap_or_else(|| "Yes".to_owned());
    if matches!(spec.kind.as_str(), "checkbox" | "radio")
        && (on_state.is_empty() || on_state == "Off")
    {
        return Err(PdfError::new_err(
            "a button's on state is a name other than Off",
        ));
    }
    let existing = this
        .collect_form_fields()?
        .into_iter()
        .find(|(name, ..)| *name == spec.name)
        .map(|(_, id, field_type, flags, _)| (id, existing_kind(&field_type, flags)));
    match existing {
        Some((_, kind)) if kind != spec.kind => {
            return Err(PdfError::new_err(format!(
                "the form already has a {kind} field named {:?}",
                spec.name
            )));
        }
        None if spec.name.contains('.') => {
            return Err(PdfError::new_err(
                "a new field's name has no dots; dots join the names of a field's parents",
            ));
        }
        _ => {}
    }
    let quad = draw::display_rect_quad_pdf(crop, rotation, [x0, y0, x1, y1]);
    let rect = draw::bounding_rect_iter(quad.iter().copied());
    let mut widget = dictionary! {
        "Type" => "Annot",
        "Subtype" => "Widget",
        "Rect" => Object::Array(rect.iter().map(|&value| Object::Real(value as f32)).collect()),
        "F" => 4,
        "P" => page_id,
    };
    let mut mark = Dictionary::new();
    let border_color = spec.border_color.unwrap_or((0.0, 0.0, 0.0));
    mark.set("BC", rgb_entry("border colour", border_color)?);
    if let Some(color) = spec.background_color {
        mark.set("BG", rgb_entry("background colour", color)?);
    }
    if let Some(caption) = &spec.caption {
        mark.set("CA", form_text_string(caption));
    }
    // A widget on a turned page shows upright.
    if rotation != 0 {
        mark.set("R", rotation);
    }
    widget.set("MK", mark);
    let (width, style, dashes) = spec
        .border
        .clone()
        .unwrap_or_else(|| (1.0, "S".to_owned(), Vec::new()));
    if !(width.is_finite() && width >= 0.0)
        || !matches!(style.as_str(), "S" | "D" | "B" | "I" | "U")
        || dashes.len() > annotation_appearance::MAX_BORDER_DASHES
        || (!dashes.is_empty() && !annotation_appearance::valid_dashes(&dashes))
        || (style == "D") == dashes.is_empty()
    {
        return Err(PdfError::new_err(
            "the border is not one that pylopdf writes",
        ));
    }
    let mut border = dictionary! {
        "W" => Object::Real(width as f32),
        "S" => Object::Name(style.into_bytes()),
    };
    if !dashes.is_empty() {
        border.set(
            "D",
            Object::Array(
                dashes
                    .iter()
                    .map(|&dash| Object::Real(dash as f32))
                    .collect(),
            ),
        );
    }
    widget.set("BS", border);
    if matches!(
        spec.kind.as_str(),
        "text" | "combobox" | "listbox" | "pushbutton"
    ) {
        let (red, green, blue) = spec.text_color.unwrap_or((0.0, 0.0, 0.0));
        rgb_entry("text colour", (red, green, blue))?;
        let resource = match &spec.font_name {
            Some(name) => annotation_appearance::DA_FONTS
                .iter()
                .find(|(_, font)| font == name)
                .map(|(resource, _)| *resource)
                .ok_or_else(|| PdfError::new_err(format!("unknown Standard 14 font {name:?}")))?,
            None => "Helv",
        };
        let size = spec.font_size.unwrap_or(0.0);
        if !(size.is_finite() && (0.0..=10_000.0).contains(&size)) {
            return Err(PdfError::new_err(
                "a widget font size is from 0, which fits the text, to 10000",
            ));
        }
        widget.set(
            "DA",
            Object::string_literal(format!(
                "/{resource} {} Tf {} {} {} rg",
                draw::fmt(size),
                draw::fmt(red),
                draw::fmt(green),
                draw::fmt(blue)
            )),
        );
        if let Some(align) = spec.align {
            if align > 2 {
                return Err(PdfError::new_err(
                    "a field aligns left (0), centre (1), or right (2)",
                ));
            }
            widget.set("Q", i64::from(align));
        }
    }
    let mut field = dictionary! {
        "FT" => field_type,
        "T" => form_text_string(&spec.name),
    };
    if kind_flags | spec.flags != 0 {
        field.set("Ff", kind_flags | spec.flags);
    }
    if let Some(label) = &spec.label {
        field.set("TU", form_text_string(label));
    }
    if let Some(length) = spec.max_length {
        if spec.kind != "text" || !(0..=1_000_000).contains(&length) {
            return Err(PdfError::new_err(
                "only a text field has a maximum length, from 0 to 1000000",
            ));
        }
        field.set("MaxLen", length);
    }
    if let Some(choices) = &spec.choices {
        if !matches!(spec.kind.as_str(), "combobox" | "listbox") {
            return Err(PdfError::new_err("only a choice field has choices"));
        }
        field.set(
            "Opt",
            Object::Array(
                choices
                    .iter()
                    .map(|(value, label)| {
                        if value == label {
                            form_text_string(value)
                        } else {
                            Object::Array(vec![form_text_string(value), form_text_string(label)])
                        }
                    })
                    .collect(),
            ),
        );
    }
    if let Some(default) = &spec.default_value {
        field.set(
            "DV",
            if field_type == "Btn" {
                Object::Name(default.as_bytes().to_vec())
            } else {
                form_text_string(default)
            },
        );
    }
    let font = spec
        .font
        .as_ref()
        .map(|font| {
            Python::attach(|py| font.borrow(py).face()).ok_or_else(|| {
                PdfError::new_err("form appearances draw an OpenType Font, not a Standard 14 font")
            })
        })
        .transpose()?
        .map(FormFont::Face);
    let append = this.prepare_page_annotation_append(page_id)?;

    this.invalidate_hayro_pdf();
    let result = py.detach(|| {
        let original = this.doc.clone();
        let outcome = (|| -> PyResult<ObjectId> {
            let form_id = this.acroform_object()?;
            let widget_id = match existing {
                Some((field_id, _)) => {
                    // A merged field and widget becomes a widget of a new
                    // parent field, which takes its place in the form tree.
                    let merged = this.doc.get_dictionary(field_id).is_ok_and(|dict| {
                        matches!(
                            dict.get(b"Subtype").and_then(Object::as_name),
                            Ok(b"Widget")
                        )
                    });
                    let parent_id = if merged {
                        let old = this
                            .doc
                            .get_dictionary(field_id)
                            .map_err(to_py_err)?
                            .clone();
                        let mut parent = dictionary! {
                            "Kids" => Object::Array(vec![Object::Reference(field_id)]),
                        };
                        for key in FIELD_KEYS {
                            if let Ok(value) = old.get(key) {
                                parent.set(key, value.clone());
                            }
                        }
                        let grand = old.get(b"Parent").and_then(Object::as_reference).ok();
                        if let Some(grand) = grand {
                            parent.set("Parent", grand);
                        }
                        let parent_id = this.doc.add_object(parent);
                        let kid = this
                            .doc
                            .get_object_mut(field_id)
                            .and_then(Object::as_dict_mut)
                            .map_err(to_py_err)?;
                        for key in FIELD_KEYS {
                            kid.remove(key);
                        }
                        kid.set("Parent", parent_id);
                        this.replace_form_node(grand, field_id, parent_id)?;
                        parent_id
                    } else {
                        field_id
                    };
                    widget.set("Parent", parent_id);
                    let widget_id = this.doc.add_object(widget);
                    let parent = this
                        .doc
                        .get_object_mut(parent_id)
                        .and_then(Object::as_dict_mut)
                        .map_err(to_py_err)?;
                    match parent.get_mut(b"Kids") {
                        Ok(Object::Array(kids)) => kids.push(Object::Reference(widget_id)),
                        Ok(Object::Reference(kids)) => {
                            let kids = *kids;
                            this.doc
                                .get_object_mut(kids)
                                .and_then(Object::as_array_mut)
                                .map_err(to_py_err)?
                                .push(Object::Reference(widget_id));
                        }
                        _ => {
                            parent.set("Kids", Object::Array(vec![Object::Reference(widget_id)]));
                        }
                    }
                    widget_id
                }
                None if spec.kind == "radio" => {
                    field.set("Kids", Object::Array(Vec::new()));
                    let field_id = this.doc.add_object(field);
                    widget.set("Parent", field_id);
                    let widget_id = this.doc.add_object(widget);
                    if let Ok(field) = this
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                    {
                        field.set("Kids", Object::Array(vec![Object::Reference(widget_id)]));
                    }
                    this.form_fields_mut(form_id)?
                        .push(Object::Reference(field_id));
                    widget_id
                }
                None => {
                    for (key, value) in field {
                        widget.set(key, value);
                    }
                    let widget_id = this.doc.add_object(widget);
                    this.form_fields_mut(form_id)?
                        .push(Object::Reference(widget_id));
                    widget_id
                }
            };
            this.commit_page_annotation_append(page_id, append, widget_id)?;
            // Draw the widget: a button's two states, else the value.
            if matches!(spec.kind.as_str(), "checkbox" | "radio") {
                let widget = this
                    .doc
                    .get_dictionary(widget_id)
                    .map_err(to_py_err)?
                    .clone();
                let style = form::WidgetStyle::from_widget(&this.doc, &widget, None)
                    .map_err(PdfError::new_err)?;
                let radio = spec.kind == "radio";
                let on = this.doc.add_object(style.button_stream(true, radio));
                let off = this.doc.add_object(style.button_stream(false, radio));
                let selected = spec.value.as_deref() == Some(on_state.as_str());
                let entry = this
                    .doc
                    .get_object_mut(widget_id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                entry.set(
                    "AP",
                    dictionary! {
                        "N" => dictionary! {
                            on_state.as_bytes() => Object::Reference(on),
                            "Off" => Object::Reference(off),
                        },
                    },
                );
                entry.set("AS", if selected { on_state.as_str() } else { "Off" });
                let field_id = this
                    .widget_fields()?
                    .get(&widget_id)
                    .map(|field| field.0)
                    .ok_or_else(|| PdfError::new_err("the new widget has no field"))?;
                let field = this
                    .doc
                    .get_object_mut(field_id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                if selected {
                    field.set("V", Object::Name(on_state.as_bytes().to_vec()));
                } else if !field.has(b"V") {
                    field.set("V", "Off");
                }
            } else if matches!(spec.kind.as_str(), "text" | "combobox" | "listbox") {
                let current = this
                    .collect_form_fields()?
                    .into_iter()
                    .find(|(name, ..)| *name == spec.name)
                    .and_then(|(.., value)| value.map(|value| value.to_string()));
                let value = spec.value.clone().or(current);
                this.set_form_field_inner(
                    &spec.name,
                    value.as_deref().unwrap_or_default(),
                    font.as_ref(),
                )?;
                if value.is_none() {
                    let field_id = this
                        .widget_fields()?
                        .get(&widget_id)
                        .map(|field| field.0)
                        .ok_or_else(|| PdfError::new_err("the new widget has no field"))?;
                    if let Ok(field) = this
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                    {
                        field.remove(b"V");
                    }
                }
            } else {
                this.redraw_widget(widget_id, font.as_ref())?;
                if spec.kind == "signature" {
                    let widget = this
                        .doc
                        .get_dictionary(widget_id)
                        .map_err(to_py_err)?
                        .clone();
                    let style = form::WidgetStyle::from_widget(&this.doc, &widget, None)
                        .map_err(PdfError::new_err)?;
                    let content = style.decoration_ops();
                    let appearance = this.doc.add_object(style.stream(None, content));
                    this.set_widget_normal_appearance(
                        widget_id,
                        Some(Object::Reference(appearance)),
                    )?;
                }
            }
            this.set_need_appearances(!this.form_appearances_complete()?)?;
            Ok(widget_id)
        })();
        match outcome {
            Ok(widget_id) => Ok(widget_id),
            Err(error) => {
                this.doc.replace(original);
                Err(error)
            }
        }
    })?;
    Ok(result.0)
}

impl _Document {
    /// Make the `/Kids` entry of `parent`, or the `/Fields` and `/CO` entries
    /// without one, that names `old` name `new`.
    fn replace_form_node(
        &mut self,
        parent: Option<ObjectId>,
        old: ObjectId,
        new: ObjectId,
    ) -> PyResult<()> {
        let swap = |items: &mut Vec<Object>| {
            for item in items {
                if matches!(item, Object::Reference(id) if *id == old) {
                    *item = Object::Reference(new);
                }
            }
        };
        match parent {
            Some(parent) => {
                let kids = match self
                    .doc
                    .get_dictionary(parent)
                    .map_err(to_py_err)?
                    .get(b"Kids")
                {
                    Ok(Object::Reference(kids)) => Some(*kids),
                    _ => None,
                };
                match kids {
                    Some(kids) => {
                        if let Ok(kids) =
                            self.doc.get_object_mut(kids).and_then(Object::as_array_mut)
                        {
                            swap(kids);
                        }
                    }
                    None => {
                        if let Ok(Object::Array(kids)) = self
                            .doc
                            .get_object_mut(parent)
                            .and_then(Object::as_dict_mut)
                            .and_then(|parent| parent.get_mut(b"Kids"))
                        {
                            swap(kids);
                        }
                    }
                }
            }
            None => {
                let form_id = self.acroform_object()?;
                swap(self.form_fields_mut(form_id)?);
                if let Ok(Object::Array(order)) = self
                    .doc
                    .get_object_mut(form_id)
                    .and_then(Object::as_dict_mut)
                    .and_then(|form| form.get_mut(b"CO"))
                {
                    swap(order);
                }
            }
        }
        Ok(())
    }
}

/// Implements [`_Document::remove_widget`]: take a widget off its page and
/// out of its field, removing the field with its last widget.
pub(super) fn remove_widget(
    this: &mut _Document,
    page_number: u32,
    index: u32,
    xref: u32,
) -> PyResult<()> {
    let page_id = this.page_id(page_number)?;
    let (index, widget) = this.annotation_at(page_id, index, xref)?;
    if !matches!(
        widget.get(b"Subtype").and_then(Object::as_name),
        Ok(b"Widget")
    ) {
        return Err(StaleObjectError::new_err(format!(
            "annotation {xref} is no longer a widget"
        )));
    }
    this.invalidate_hayro_pdf();
    if xref == 0 {
        let items = this.indirect_page_annotations_mut(page_id)?;
        if (index as usize) < items.len() {
            items.remove(index as usize);
        }
        return Ok(());
    }
    let widget_id = (xref, 0);
    let owner = this.widget_fields()?.get(&widget_id).map(|field| field.0);
    this.unlist_widget(widget_id)?;
    match owner {
        Some(field_id) if field_id == widget_id || this.field_widgets(field_id)?.len() <= 1 => {
            this.remove_form_field(field_id)
        }
        Some(_) => {
            let parent = this
                .doc
                .get_dictionary(widget_id)
                .ok()
                .and_then(|dict| dict.get(b"Parent").ok())
                .and_then(|parent| parent.as_reference().ok());
            this.detach_form_node(parent, widget_id)
        }
        None => Ok(()),
    }
}

/// Implements [`_Document::remove_field`].
pub(super) fn remove_field(this: &mut _Document, xref: u32) -> PyResult<()> {
    let field_id = this.form_field_id(xref)?;
    this.invalidate_hayro_pdf();
    this.remove_form_field(field_id)
}
