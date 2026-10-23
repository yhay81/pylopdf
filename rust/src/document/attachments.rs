//! Embedded files: the bounded EmbeddedFiles name-tree walk and attachment
//! edits.

use super::*;

/// Bound attachment name-tree traversal and returned metadata.
pub(super) const DEFAULT_MAX_EMBEDDED_FILE_SIZE: usize = 64 * 1024 * 1024;

pub(super) const MAX_EMBEDDED_FILE_ENTRIES: usize = 4_096;

pub(super) const MAX_EMBEDDED_FILE_TREE_NODES: usize = 4_096;

pub(super) const MAX_EMBEDDED_FILE_NAME_BYTES: usize = 1024 * 1024;

pub(super) const MAX_EMBEDDED_FILE_TREE_DEPTH: usize = 32;

pub(super) const MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES: usize = 1024 * 1024;

pub(super) const MAX_EMBEDDED_FILE_DIRECT_OBJECTS: usize = 4_096;

pub(super) const MAX_EMBEDDED_FILE_DIRECT_BYTES: usize = 1024 * 1024;

pub(super) const MAX_EMBEDDED_FILE_DIRECT_DEPTH: usize = 32;

/// One EmbeddedFiles name-tree item: display name and FileSpec object.
///
/// FileSpec may be either an indirect reference or an inline dictionary.
pub(super) type EmbeddedFileEntry = (String, Object);

/// Preflighted location of the Catalog's `/Names` dictionary.
#[derive(Clone, Copy)]
pub(super) enum EmbeddedFilesWriteTarget {
    Missing,
    Inline,
    Indirect(ObjectId),
}

pub(super) fn validate_embedded_file_lookup_name(name: &str) -> PyResult<()> {
    if name.len() > MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES {
        return Err(limit_err(
            "embedded_file_input_size",
            format!(
                "attachment lookup name exceeds the {MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES}-byte UTF-8 safety limit"
            ),
        ));
    }
    Ok(())
}

pub(super) fn validate_embedded_file_input_text(
    name: &str,
    filename: Option<&str>,
    desc: Option<&str>,
) -> PyResult<()> {
    let fname = filename.unwrap_or(name);
    let input_text_bytes = name
        .len()
        .checked_add(fname.len())
        .and_then(|total| desc.map_or(Some(total), |text| total.checked_add(text.len())))
        .ok_or_else(|| {
            limit_err(
                "embedded_file_input_size",
                "attachment input text exceeds the platform size limit",
            )
        })?;
    if input_text_bytes > MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES {
        return Err(limit_err(
            "embedded_file_input_size",
            format!(
                "attachment name, filename, and description exceed the \
                 {MAX_EMBEDDED_FILE_INPUT_TEXT_BYTES}-byte UTF-8 safety limit"
            ),
        ));
    }
    Ok(())
}

/// Visit valid `(name, FileSpec)` items in the EmbeddedFiles name tree.
///
/// Recurse through `/Kids` with depth, cycle, node, entry, and decoded-name
/// guards. Nodes and FileSpecs remain borrowed so read operations do not clone
/// adversarial direct object shapes. A FileSpec that is null, an indirect
/// null, or a reference to a missing object is skipped, so attachment
/// rewrites drop it. Returning `Some` stops the walk early.
pub(super) fn visit_embedded_files<'a, T>(
    doc: &'a Document,
    mut visitor: impl FnMut(String, &'a Object) -> PyResult<Option<T>>,
) -> PyResult<Option<T>> {
    let Some(root) = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"Names").ok())
        .map(|names| deref_object(doc, names))
        .and_then(|names| names.as_dict().ok())
        .and_then(|names| names.get(b"EmbeddedFiles").ok())
    else {
        return Ok(None);
    };
    let mut visited = HashSet::new();
    let mut stack = Vec::new();
    stack.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!("failed to allocate attachment tree stack: {error}"))
    })?;
    stack.push((root, 1usize));
    let mut nodes = 0usize;
    let mut edges = 0usize;
    let mut pairs_seen = 0usize;
    let mut encoded_name_bytes = 0usize;
    let mut name_bytes = 0usize;
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object {
            if visited.contains(id) {
                continue;
            }
            if visited.len() == visited.capacity() {
                visited.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow attachment tree cycle set: {error}"))
                })?;
            }
            visited.insert(*id);
        }
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes = nodes.saturating_add(1);
        if nodes > MAX_EMBEDDED_FILE_TREE_NODES {
            return Err(PdfError::new_err(format!(
                "attachment name tree exceeds the {MAX_EMBEDDED_FILE_TREE_NODES}-node safety limit"
            )));
        }
        if let Ok(pairs) = node.get(b"Names").and_then(Object::as_array) {
            pairs_seen = pairs_seen
                .checked_add(pairs.len().div_ceil(2))
                .ok_or_else(|| {
                    PdfError::new_err("attachment name tree exceeds the platform size limit")
                })?;
            if pairs_seen > MAX_EMBEDDED_FILE_ENTRIES {
                return Err(PdfError::new_err(format!(
                    "attachment name tree exceeds the {MAX_EMBEDDED_FILE_ENTRIES}-entry safety limit"
                )));
            }
            for pair in pairs.chunks(2) {
                let [key, value] = pair else { continue };
                let Object::String(encoded_name, _) = key else {
                    continue;
                };
                encoded_name_bytes = encoded_name_bytes
                    .checked_add(encoded_name.len())
                    .ok_or_else(|| {
                        PdfError::new_err("attachment names exceed the platform size limit")
                    })?;
                if encoded_name_bytes > MAX_EMBEDDED_FILE_NAME_BYTES {
                    return Err(PdfError::new_err(format!(
                        "encoded attachment names exceed the {MAX_EMBEDDED_FILE_NAME_BYTES}-byte safety limit"
                    )));
                }
                let Ok(name) = decode_text_string(key) else {
                    continue;
                };
                name_bytes = name_bytes.checked_add(name.len()).ok_or_else(|| {
                    PdfError::new_err("attachment names exceed the platform size limit")
                })?;
                if name_bytes > MAX_EMBEDDED_FILE_NAME_BYTES {
                    return Err(PdfError::new_err(format!(
                        "attachment names exceed the {MAX_EMBEDDED_FILE_NAME_BYTES}-byte safety limit"
                    )));
                }
                if !matches!(value, Object::Reference(_) | Object::Dictionary(_))
                    || matches!(non_null_value(doc, value), Ok(None))
                {
                    continue;
                }
                if let Some(result) = visitor(name, value)? {
                    return Ok(Some(result));
                }
            }
        }
        if let Ok(kids) = node.get(b"Kids").and_then(Object::as_array) {
            if !kids.is_empty() && depth >= MAX_EMBEDDED_FILE_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "attachment name tree exceeds the {MAX_EMBEDDED_FILE_TREE_DEPTH}-level safety limit"
                )));
            }
            edges = edges.checked_add(kids.len()).ok_or_else(|| {
                PdfError::new_err("attachment name tree exceeds the platform size limit")
            })?;
            if edges > MAX_EMBEDDED_FILE_TREE_NODES {
                return Err(PdfError::new_err(format!(
                    "attachment name tree exceeds the {MAX_EMBEDDED_FILE_TREE_NODES}-edge safety limit"
                )));
            }
            stack.try_reserve(kids.len()).map_err(|error| {
                PdfError::new_err(format!("failed to grow attachment tree stack: {error}"))
            })?;
            for kid in kids {
                stack.push((kid, depth + 1));
            }
        }
    }
    Ok(None)
}

pub(super) fn embedded_file_shape_error(detail: &str, limit: usize) -> PyErr {
    PdfError::new_err(format!(
        "inline attachment FileSpec exceeds the {limit}-{detail} safety limit"
    ))
}

/// Bound the direct object shape copied when a name-tree FileSpec is inline.
///
/// Indirect references remain leaves. This preserves ordinary inline
/// FileSpecs without allowing custom keys to amplify one add/delete operation
/// through an arbitrarily deep or wide clone.
pub(super) fn validate_inline_embedded_filespec(object: &Object) -> PyResult<()> {
    let mut pending = Vec::new();
    pending.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate inline attachment validation stack: {error}"
        ))
    })?;
    pending.push((object, 1usize));
    let mut objects = 0usize;
    let mut bytes = 0usize;
    while let Some((current, depth)) = pending.pop() {
        objects = objects.checked_add(1).ok_or_else(|| {
            PdfError::new_err("inline attachment FileSpec exceeds the platform size limit")
        })?;
        if objects > MAX_EMBEDDED_FILE_DIRECT_OBJECTS {
            return Err(embedded_file_shape_error(
                "object",
                MAX_EMBEDDED_FILE_DIRECT_OBJECTS,
            ));
        }
        if depth > MAX_EMBEDDED_FILE_DIRECT_DEPTH {
            return Err(embedded_file_shape_error(
                "level",
                MAX_EMBEDDED_FILE_DIRECT_DEPTH,
            ));
        }

        let mut add_bytes = |amount: usize| -> PyResult<()> {
            bytes = bytes.checked_add(amount).ok_or_else(|| {
                PdfError::new_err("inline attachment FileSpec exceeds the platform size limit")
            })?;
            if bytes > MAX_EMBEDDED_FILE_DIRECT_BYTES {
                return Err(embedded_file_shape_error(
                    "byte",
                    MAX_EMBEDDED_FILE_DIRECT_BYTES,
                ));
            }
            Ok(())
        };
        let child_count = match current {
            Object::Name(value) | Object::String(value, _) => {
                add_bytes(value.len())?;
                0
            }
            Object::Array(items) => items.len(),
            Object::Dictionary(dict) => {
                for (key, _) in dict.iter() {
                    add_bytes(key.len())?;
                }
                dict.len()
            }
            Object::Stream(stream) => {
                add_bytes(stream.content.len())?;
                for (key, _) in stream.dict.iter() {
                    add_bytes(key.len())?;
                }
                stream.dict.len()
            }
            _ => 0,
        };
        if child_count != 0 && depth >= MAX_EMBEDDED_FILE_DIRECT_DEPTH {
            return Err(embedded_file_shape_error(
                "level",
                MAX_EMBEDDED_FILE_DIRECT_DEPTH,
            ));
        }
        let scheduled = objects
            .checked_add(pending.len())
            .and_then(|total| total.checked_add(child_count))
            .ok_or_else(|| {
                PdfError::new_err("inline attachment FileSpec exceeds the platform size limit")
            })?;
        if scheduled > MAX_EMBEDDED_FILE_DIRECT_OBJECTS {
            return Err(embedded_file_shape_error(
                "object",
                MAX_EMBEDDED_FILE_DIRECT_OBJECTS,
            ));
        }
        pending.try_reserve(child_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow inline attachment validation stack: {error}"
            ))
        })?;
        let child_depth = depth + 1;
        match current {
            Object::Array(items) => {
                pending.extend(items.iter().map(|child| (child, child_depth)));
            }
            Object::Dictionary(dict) => {
                pending.extend(dict.iter().map(|(_, child)| (child, child_depth)));
            }
            Object::Stream(stream) => {
                pending.extend(stream.dict.iter().map(|(_, child)| (child, child_depth)));
            }
            _ => {}
        }
    }
    Ok(())
}

/// Collect owned entries for attachment-tree rewrite operations.
pub(super) fn collect_embedded_files(doc: &Document) -> PyResult<Vec<EmbeddedFileEntry>> {
    let mut out = Vec::new();
    visit_embedded_files(doc, |name, value| {
        if matches!(value, Object::Dictionary(_)) {
            validate_inline_embedded_filespec(value)?;
        }
        if out.len() == out.capacity() {
            out.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow attachment rewrite collection: {error}"
                ))
            })?;
        }
        out.push((name, value.clone()));
        Ok(None::<()>)
    })?;
    Ok(out)
}

/// Validate the name-dictionary target before attachment objects are added.
///
/// A null `/Names`, an indirect null, and a reference to a missing object
/// read as an absent entry, which the rewrite replaces with a new dictionary.
pub(super) fn embedded_files_write_target(doc: &Document) -> PyResult<EmbeddedFilesWriteTarget> {
    let catalog = doc.catalog().map_err(to_py_err)?;
    let Ok(names) = catalog.get(b"Names") else {
        return Ok(EmbeddedFilesWriteTarget::Missing);
    };
    let Some(resolved) = non_null_value(doc, names).map_err(to_py_err)? else {
        return Ok(EmbeddedFilesWriteTarget::Missing);
    };
    match names {
        Object::Dictionary(_) => Ok(EmbeddedFilesWriteTarget::Inline),
        Object::Reference(id) => {
            resolved.as_dict().map_err(to_py_err)?;
            Ok(EmbeddedFilesWriteTarget::Indirect(*id))
        }
        _ => Err(PdfError::new_err(
            "Catalog Names entry must be a dictionary or indirect dictionary",
        )),
    }
}

/// Rewrite EmbeddedFiles as one flat node while preserving other name trees.
pub(super) fn write_embedded_files(
    doc: &mut Document,
    mut entries: Vec<EmbeddedFileEntry>,
    target: EmbeddedFilesWriteTarget,
) -> PyResult<()> {
    entries.sort_unstable_by(|a, b| a.0.cmp(&b.0));
    let flat_len = entries
        .len()
        .checked_mul(2)
        .ok_or_else(|| PdfError::new_err("attachment rewrite array length overflowed"))?;
    let mut flat = Vec::new();
    flat.try_reserve_exact(flat_len).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate attachment rewrite array: {error}"
        ))
    })?;
    for (name, filespec) in entries {
        flat.push(text_string(&name));
        flat.push(filespec);
    }
    let tree = Object::Dictionary(dictionary! { "Names" => Object::Array(flat) });
    match target {
        EmbeddedFilesWriteTarget::Indirect(id) => {
            let names = doc
                .get_object_mut(id)
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            names.set("EmbeddedFiles", tree);
        }
        EmbeddedFilesWriteTarget::Inline => {
            let catalog = doc.catalog_mut().map_err(to_py_err)?;
            let names = catalog
                .get_mut(b"Names")
                .and_then(Object::as_dict_mut)
                .map_err(to_py_err)?;
            names.set("EmbeddedFiles", tree);
        }
        EmbeddedFilesWriteTarget::Missing => {
            doc.catalog_mut().map_err(to_py_err)?.set(
                "Names",
                dictionary! {
                    "EmbeddedFiles" => tree,
                },
            );
        }
    }
    Ok(())
}

/// Implements [`_Document::embfile_names`].
pub(super) fn embfile_names(this: &_Document, py: Python<'_>) -> PyResult<Vec<String>> {
    py.detach(|| {
        let mut names = Vec::new();
        visit_embedded_files(&this.doc, |name, _| {
            if names.len() == names.capacity() {
                names.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow attachment name result: {error}"))
                })?;
            }
            names.push(name);
            Ok(None::<()>)
        })?;
        names.sort_unstable();
        Ok(names)
    })
}

/// Implements [`_Document::embfile_get`].
pub(super) fn embfile_get(
    this: &_Document,
    py: Python<'_>,
    name: &str,
    max_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    validate_embedded_file_lookup_name(name)?;
    py.detach(|| {
        let filespec_obj = visit_embedded_files(&this.doc, |entry_name, value| {
            Ok((entry_name == name).then_some(value))
        })?
        .ok_or_else(|| PdfError::new_err(format!("attachment not found: {name:?}")))?;
        let filespec = match filespec_obj {
            Object::Reference(id) => this
                .doc
                .get_object(*id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?,
            Object::Dictionary(dict) => dict,
            _ => return Err(PdfError::new_err("attachment's FileSpec is corrupt")),
        };
        let ef = match filespec.get(b"EF").map_err(to_py_err)? {
            Object::Reference(id) => this
                .doc
                .get_object(*id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?,
            Object::Dictionary(d) => d,
            _ => return Err(PdfError::new_err("attachment's EF dictionary is corrupt")),
        };
        let stream_ref = ef
            .get(b"F")
            .or_else(|_| ef.get(b"UF"))
            .and_then(Object::as_reference)
            .map_err(to_py_err)?;
        let stream = this
            .doc
            .get_object(stream_ref)
            .and_then(Object::as_stream)
            .map_err(to_py_err)?;
        decoded_stream_content(
            stream,
            max_size,
            "embedded_file_size",
            &format!("attachment {name:?}"),
        )
    })
}

/// Implements [`_Document::embfile_add`].
pub(super) fn embfile_add(
    this: &mut _Document,
    py: Python<'_>,
    name: String,
    data: Vec<u8>,
    filename: Option<String>,
    desc: Option<String>,
    max_size: Option<usize>,
) -> PyResult<()> {
    validate_embedded_file_input_text(&name, filename.as_deref(), desc.as_deref())?;
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    if let Some(limit) = max_size
        && data.len() > limit
    {
        return Err(limit_err(
            "embedded_file_size",
            format!(
                "attachment input is {} bytes, exceeding the {limit}-byte limit",
                data.len()
            ),
        ));
    }
    let result = py.detach(|| {
        let target = embedded_files_write_target(&this.doc)?;
        let entries = collect_embedded_files(&this.doc)?;
        if entries.iter().any(|(n, _)| *n == name) {
            return Err(PdfError::new_err(format!(
                "an attachment with this name already exists: {name:?} (call embfile_del first)"
            )));
        }
        if entries.len() >= MAX_EMBEDDED_FILE_ENTRIES {
            return Err(PdfError::new_err(format!(
                "cannot add attachment: the {MAX_EMBEDDED_FILE_ENTRIES}-entry safety limit is reached"
            )));
        }
        let total_name_bytes = entries
            .iter()
            .try_fold(name.len(), |total, (entry_name, _)| {
                total.checked_add(entry_name.len())
            })
            .ok_or_else(|| {
                PdfError::new_err("attachment names exceed the platform size limit")
            })?;
        if total_name_bytes > MAX_EMBEDDED_FILE_NAME_BYTES {
            return Err(PdfError::new_err(format!(
                "cannot add attachment: decoded names would exceed the {MAX_EMBEDDED_FILE_NAME_BYTES}-byte safety limit"
            )));
        }
        let total_encoded_name_bytes =
            entries
                .iter()
                .map(|(entry_name, _)| entry_name)
                .chain(std::iter::once(&name))
                .try_fold(0usize, |total, entry_name| {
                    let Object::String(encoded, _) = text_string(entry_name) else {
                        unreachable!("text_string always returns a PDF string")
                    };
                    total.checked_add(encoded.len())
                })
                .ok_or_else(|| {
                    PdfError::new_err("attachment names exceed the platform size limit")
                })?;
        if total_encoded_name_bytes > MAX_EMBEDDED_FILE_NAME_BYTES {
            return Err(PdfError::new_err(format!(
                "cannot add attachment: encoded names would exceed the {MAX_EMBEDDED_FILE_NAME_BYTES}-byte safety limit"
            )));
        }
        let previous_max_id = this.doc.max_id;
        let size = i64::try_from(data.len()).map_err(|e| PdfError::new_err(e.to_string()))?;
        // Keep compression allowed so save(deflate=True) can compress it.
        let ef_id = this.doc.add_object(Stream::new(
            dictionary! {
                "Type" => "EmbeddedFile",
                "Params" => dictionary! { "Size" => size },
            },
            data,
        ));
        let fname = filename.unwrap_or_else(|| name.clone());
        let mut filespec = dictionary! {
            "Type" => "Filespec",
            "F" => Object::string_literal(fname.clone()),
            "UF" => text_string(&fname),
            "EF" => dictionary! { "F" => ef_id, "UF" => ef_id },
        };
        if let Some(text) = desc {
            filespec.set("Desc", text_string(&text));
        }
        let filespec_id = this.doc.add_object(filespec);
        let mut entries = entries;
        entries.push((name, Object::Reference(filespec_id)));
        if let Err(error) = write_embedded_files(&mut this.doc, entries, target) {
            this.doc.objects.remove(&ef_id);
            this.doc.objects.remove(&filespec_id);
            this.doc.max_id = previous_max_id;
            return Err(error);
        }
        Ok(())
    });
    if result.is_ok() {
        this.invalidate_hayro_pdf_keeping_placements();
    }
    result
}

/// Implements [`_Document::embfile_del`].
pub(super) fn embfile_del(this: &mut _Document, py: Python<'_>, name: &str) -> PyResult<()> {
    validate_embedded_file_lookup_name(name)?;
    let result = py.detach(|| {
        let target = embedded_files_write_target(&this.doc)?;
        let entries = collect_embedded_files(&this.doc)?;
        let before = entries.len();
        let remaining: Vec<EmbeddedFileEntry> =
            entries.into_iter().filter(|(n, _)| n != name).collect();
        if remaining.len() == before {
            return Err(PdfError::new_err(format!("attachment not found: {name:?}")));
        }
        write_embedded_files(&mut this.doc, remaining, target)
    });
    if result.is_ok() {
        this.invalidate_hayro_pdf_keeping_placements();
    }
    result
}

/// One attachment's metadata: filename, description, `/Params /Size` (-1
/// when absent, as pymupdf reports it), and the raw creation and
/// modification date strings (empty when absent).
pub(super) type EmbeddedFileInfo = (String, String, i64, String, String);

/// Resolve a FileSpec value of the name tree to its dictionary.
pub(super) fn filespec_dict<'a>(
    doc: &'a Document,
    filespec: &'a Object,
) -> PyResult<&'a Dictionary> {
    match filespec {
        Object::Reference(id) => doc
            .get_object(*id)
            .and_then(Object::as_dict)
            .map_err(to_py_err),
        Object::Dictionary(dict) => Ok(dict),
        _ => Err(PdfError::new_err("attachment's FileSpec is corrupt")),
    }
}

/// Return the embedded-file stream ID that a FileSpec's `/EF` names.
pub(super) fn embedded_stream_id(doc: &Document, filespec: &Dictionary) -> PyResult<ObjectId> {
    let ef = match filespec.get(b"EF").map_err(to_py_err)? {
        Object::Reference(id) => doc
            .get_object(*id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?,
        Object::Dictionary(d) => d,
        _ => return Err(PdfError::new_err("attachment's EF dictionary is corrupt")),
    };
    ef.get(b"F")
        .or_else(|_| ef.get(b"UF"))
        .and_then(Object::as_reference)
        .map_err(to_py_err)
}

/// Decode one optional text entry, charging its encoded bytes to `budget`.
pub(super) fn info_text(
    doc: &Document,
    dict: &Dictionary,
    key: &[u8],
    budget: &mut usize,
) -> PyResult<String> {
    let Some(object) = dict
        .get(key)
        .ok()
        .and_then(|value| non_null_value(doc, value).ok().flatten())
    else {
        return Ok(String::new());
    };
    let Object::String(bytes, _) = object else {
        return Ok(String::new());
    };
    *budget = budget.checked_sub(bytes.len()).ok_or_else(|| {
        PdfError::new_err(format!(
            "attachment metadata text exceeds the {MAX_EMBEDDED_FILE_DIRECT_BYTES}-byte safety limit"
        ))
    })?;
    Ok(decode_text_string(object).unwrap_or_default())
}

/// Implements [`_Document::embfile_info`].
pub(super) fn embfile_info(
    this: &_Document,
    py: Python<'_>,
    name: &str,
) -> PyResult<Option<EmbeddedFileInfo>> {
    validate_embedded_file_lookup_name(name)?;
    py.detach(|| {
        let Some(filespec_obj) = visit_embedded_files(&this.doc, |entry_name, value| {
            Ok((entry_name == name).then_some(value))
        })?
        else {
            return Ok(None);
        };
        filespec_info(&this.doc, filespec_dict(&this.doc, filespec_obj)?).map(Some)
    })
}

/// The metadata of a FileSpec, as `embfile_info` reports it.
pub(super) fn filespec_info(doc: &Document, filespec: &Dictionary) -> PyResult<EmbeddedFileInfo> {
    let mut budget = MAX_EMBEDDED_FILE_DIRECT_BYTES;
    let mut filename = info_text(doc, filespec, b"UF", &mut budget)?;
    if filename.is_empty() {
        filename = info_text(doc, filespec, b"F", &mut budget)?;
    }
    let description = info_text(doc, filespec, b"Desc", &mut budget)?;
    let params = embedded_stream_id(doc, filespec)
        .ok()
        .and_then(|id| doc.get_object(id).ok())
        .and_then(|object| object.as_stream().ok())
        .and_then(|stream| stream.dict.get(b"Params").ok())
        .and_then(|params| non_null_value(doc, params).ok().flatten())
        .and_then(|params| params.as_dict().ok());
    let (size, creation_date, mod_date) = match params {
        Some(params) => (
            params
                .get(b"Size")
                .ok()
                .and_then(|size| non_null_value(doc, size).ok().flatten())
                .and_then(|size| size.as_i64().ok())
                .filter(|size| *size >= 0)
                .unwrap_or(-1),
            info_text(doc, params, b"CreationDate", &mut budget)?,
            info_text(doc, params, b"ModDate", &mut budget)?,
        ),
        None => (-1, String::new(), String::new()),
    };
    Ok((filename, description, size, creation_date, mod_date))
}

/// Implements [`_Document::embfile_upd`].
///
/// The new data goes into a new stream that keeps the old stream's
/// dictionary entries other than its filters and length, with `/Params
/// /Size` set and `/CheckSum` dropped; the FileSpec's `/EF` then names it.
pub(super) fn embfile_upd(
    this: &mut _Document,
    py: Python<'_>,
    name: &str,
    data: Vec<u8>,
    max_size: Option<usize>,
) -> PyResult<()> {
    validate_embedded_file_lookup_name(name)?;
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    if let Some(limit) = max_size
        && data.len() > limit
    {
        return Err(limit_err(
            "embedded_file_size",
            format!(
                "attachment input is {} bytes, exceeding the {limit}-byte limit",
                data.len()
            ),
        ));
    }
    let result = py.detach(|| {
        let target = embedded_files_write_target(&this.doc)?;
        let mut entries = collect_embedded_files(&this.doc)?;
        let index = entries
            .iter()
            .position(|(entry_name, _)| entry_name == name)
            .ok_or_else(|| PdfError::new_err(format!("attachment not found: {name:?}")))?;
        let size = i64::try_from(data.len()).map_err(|e| PdfError::new_err(e.to_string()))?;
        let mut stream_dict = {
            let filespec = filespec_dict(&this.doc, &entries[index].1)?;
            embedded_stream_id(&this.doc, filespec)
                .ok()
                .and_then(|id| this.doc.get_object(id).ok())
                .and_then(|object| object.as_stream().ok())
                .map(|stream| stream.dict.clone())
                .unwrap_or_else(|| dictionary! { "Type" => "EmbeddedFile" })
        };
        for key in [
            &b"Filter"[..],
            b"DecodeParms",
            b"DL",
            b"Length",
            b"F",
            b"FFilter",
            b"FDecodeParms",
        ] {
            stream_dict.remove(key);
        }
        let mut params = match stream_dict.get(b"Params") {
            Ok(Object::Dictionary(params)) => params.clone(),
            Ok(Object::Reference(id)) => this
                .doc
                .get_object(*id)
                .and_then(Object::as_dict)
                .cloned()
                .unwrap_or_default(),
            _ => Dictionary::new(),
        };
        params.remove(b"CheckSum");
        params.set("Size", size);
        stream_dict.set("Params", params);
        let previous_max_id = this.doc.max_id;
        let stream_id = this.doc.add_object(Stream::new(stream_dict, data));
        let ef = dictionary! { "F" => stream_id, "UF" => stream_id };
        let edited = match entries[index].1 {
            Object::Reference(id) => this
                .doc
                .get_object_mut(id)
                .and_then(Object::as_dict_mut)
                .map(|filespec| filespec.set("EF", ef))
                .map_err(to_py_err),
            Object::Dictionary(ref mut filespec) => {
                filespec.set("EF", ef);
                write_embedded_files(&mut this.doc, entries, target)
            }
            _ => Err(PdfError::new_err("attachment's FileSpec is corrupt")),
        };
        if let Err(error) = edited {
            this.doc.objects.remove(&stream_id);
            this.doc.max_id = previous_max_id;
            return Err(error);
        }
        Ok(())
    });
    if result.is_ok() {
        this.invalidate_hayro_pdf_keeping_placements();
    }
    result
}
