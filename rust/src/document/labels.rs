//! Page labels.

use super::*;

/// Bound page-label number-tree traversal and returned metadata.
pub(super) const MAX_PAGE_LABEL_ENTRIES: usize = 4_096;

pub(super) const MAX_PAGE_LABEL_TREE_NODES: usize = 4_096;

pub(super) const MAX_PAGE_LABEL_TEXT_BYTES: usize = 1024 * 1024;

pub(super) const MAX_PAGE_LABEL_TREE_DEPTH: usize = 32;

/// One PageLabels number-tree item: start, style, prefix, and first number.
pub(super) type PageLabelEntry = (i64, Option<String>, Option<String>, i64);

/// Collect page-label definitions from a bounded, cycle-aware number-tree walk.
pub(super) fn collect_page_labels(doc: &Document) -> PyResult<Vec<PageLabelEntry>> {
    let Some(root) = doc
        .catalog()
        .ok()
        .and_then(|catalog| catalog.get(b"PageLabels").ok())
    else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    let mut visited = HashSet::new();
    let mut stack = Vec::new();
    stack.try_reserve_exact(1).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate page-label number-tree stack: {error}"
        ))
    })?;
    stack.push((root, 1usize));
    let mut nodes = 0usize;
    let mut edges = 0usize;
    let mut pairs_seen = 0usize;
    let mut encoded_text_bytes = 0usize;
    let mut decoded_text_bytes = 0usize;
    while let Some((node_object, depth)) = stack.pop() {
        if let Object::Reference(id) = node_object {
            if visited.contains(id) {
                continue;
            }
            visited.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow page-label cycle detection: {error}"
                ))
            })?;
            visited.insert(*id);
        }
        let Ok(node) = deref_object(doc, node_object).as_dict() else {
            continue;
        };
        nodes = nodes.saturating_add(1);
        if nodes > MAX_PAGE_LABEL_TREE_NODES {
            return Err(PdfError::new_err(format!(
                "page-label number tree exceeds the {MAX_PAGE_LABEL_TREE_NODES}-node safety limit"
            )));
        }
        if let Ok(nums) = node.get(b"Nums").and_then(Object::as_array) {
            pairs_seen = pairs_seen
                .checked_add(nums.len().div_ceil(2))
                .ok_or_else(|| {
                    PdfError::new_err("page-label number tree exceeds the platform size limit")
                })?;
            if pairs_seen > MAX_PAGE_LABEL_ENTRIES {
                return Err(PdfError::new_err(format!(
                    "page-label number tree exceeds the {MAX_PAGE_LABEL_ENTRIES}-entry safety limit"
                )));
            }
            for pair in nums.chunks(2) {
                let [key, value] = pair else { continue };
                let Some(start) = resolve_i64(doc, key) else {
                    continue;
                };
                let Ok(label) = deref_object(doc, value).as_dict() else {
                    continue;
                };
                let style = label
                    .get(b"S")
                    .and_then(|object| deref_object(doc, object).as_name())
                    .ok()
                    .map(|name| {
                        encoded_text_bytes = encoded_text_bytes.saturating_add(name.len());
                        let text = String::from_utf8_lossy(name).into_owned();
                        decoded_text_bytes = decoded_text_bytes.saturating_add(text.len());
                        text
                    });
                let prefix = label
                    .get(b"P")
                    .ok()
                    .map(|object| deref_object(doc, object))
                    .and_then(|object| {
                        if let Object::String(encoded, _) = object {
                            encoded_text_bytes = encoded_text_bytes.saturating_add(encoded.len());
                        }
                        decode_text_string(object).ok()
                    })
                    .inspect(|text| {
                        decoded_text_bytes = decoded_text_bytes.saturating_add(text.len());
                    });
                if encoded_text_bytes > MAX_PAGE_LABEL_TEXT_BYTES
                    || decoded_text_bytes > MAX_PAGE_LABEL_TEXT_BYTES
                {
                    return Err(PdfError::new_err(format!(
                        "page-label text exceeds the {MAX_PAGE_LABEL_TEXT_BYTES}-byte safety limit"
                    )));
                }
                let first = label
                    .get(b"St")
                    .ok()
                    .and_then(|object| resolve_i64(doc, object))
                    .unwrap_or(1);
                if out.len() == out.capacity() {
                    out.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!(
                            "failed to grow returned page-label entries: {error}"
                        ))
                    })?;
                }
                out.push((start, style, prefix, first));
            }
        }
        if let Ok(kids) = node.get(b"Kids").and_then(Object::as_array) {
            if !kids.is_empty() && depth >= MAX_PAGE_LABEL_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "page-label number tree exceeds the {MAX_PAGE_LABEL_TREE_DEPTH}-level safety limit"
                )));
            }
            edges = edges.checked_add(kids.len()).ok_or_else(|| {
                PdfError::new_err("page-label number tree exceeds the platform size limit")
            })?;
            if edges > MAX_PAGE_LABEL_TREE_NODES {
                return Err(PdfError::new_err(format!(
                    "page-label number tree exceeds the {MAX_PAGE_LABEL_TREE_NODES}-edge safety limit"
                )));
            }
            stack.try_reserve(kids.len()).map_err(|error| {
                PdfError::new_err(format!(
                    "failed to grow page-label number-tree stack: {error}"
                ))
            })?;
            for kid in kids {
                stack.push((kid, depth + 1));
            }
        }
    }
    out.sort_unstable_by(|left, right| {
        left.0
            .cmp(&right.0)
            .then_with(|| left.1.cmp(&right.1))
            .then_with(|| left.2.cmp(&right.2))
            .then_with(|| left.3.cmp(&right.3))
    });
    Ok(out)
}

/// Implements [`_Document::set_page_labels`].
pub(super) fn set_page_labels(
    this: &mut _Document,
    labels: Vec<(i64, Option<String>, Option<String>, i64)>,
) -> PyResult<()> {
    if labels.len() > MAX_PAGE_LABEL_ENTRIES {
        return Err(PdfError::new_err(format!(
            "cannot set more than {MAX_PAGE_LABEL_ENTRIES} page-label ranges"
        )));
    }
    let nums_len = labels
        .len()
        .checked_mul(2)
        .ok_or_else(|| PdfError::new_err("page-label output exceeds the platform size limit"))?;
    let mut nums = Vec::new();
    nums.try_reserve_exact(nums_len).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate page-label output array: {error}"
        ))
    })?;
    let mut encoded_text_bytes = 0usize;
    let mut decoded_text_bytes = 0usize;
    for (start, style, prefix, st) in labels {
        let mut label = Dictionary::new();
        if let Some(s) = style {
            add_input_text_budget(
                &mut encoded_text_bytes,
                s.len(),
                MAX_PAGE_LABEL_TEXT_BYTES,
                "page_label_input_size",
                "encoded page-label text",
            )?;
            add_input_text_budget(
                &mut decoded_text_bytes,
                s.len(),
                MAX_PAGE_LABEL_TEXT_BYTES,
                "page_label_input_size",
                "page-label source text",
            )?;
            label.set("S", Object::Name(s.into_bytes()));
        }
        if let Some(p) = prefix {
            add_input_text_budget(
                &mut decoded_text_bytes,
                p.len(),
                MAX_PAGE_LABEL_TEXT_BYTES,
                "page_label_input_size",
                "page-label source text",
            )?;
            let encoded_len = pdf_text_string_len(&p).ok_or_else(|| {
                limit_err(
                    "page_label_input_size",
                    "encoded page-label text exceeds the platform text-size limit",
                )
            })?;
            add_input_text_budget(
                &mut encoded_text_bytes,
                encoded_len,
                MAX_PAGE_LABEL_TEXT_BYTES,
                "page_label_input_size",
                "encoded page-label text",
            )?;
            let encoded = text_string(&p);
            label.set("P", encoded);
        }
        if st != 1 {
            label.set("St", st);
        }
        nums.push(Object::Integer(start));
        nums.push(Object::Dictionary(label));
    }
    let catalog = this.doc.catalog_mut().map_err(to_py_err)?;
    if nums.is_empty() {
        catalog.remove(b"PageLabels");
    } else {
        catalog.set("PageLabels", dictionary! { "Nums" => Object::Array(nums) });
    }
    this.invalidate_hayro_pdf_keeping_placements();
    Ok(())
}
