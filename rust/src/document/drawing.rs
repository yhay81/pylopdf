//! Page content and resource plumbing for drawing, Form XObject imports,
//! and canvases.

use super::*;

/// Bound and materialize page drawing resources before dependent objects exist.
pub(super) const MAX_PAGE_RESOURCE_ENTRIES: usize = 4_096;

pub(super) const MAX_PAGE_RESOURCE_DIRECT_OBJECTS: usize = 8_192;

pub(super) const MAX_PAGE_RESOURCE_DIRECT_BYTES: usize = 1024 * 1024;

pub(super) const MAX_PAGE_RESOURCE_REFERENCE_DEPTH: usize = 32;

pub(super) const MAX_PAGE_RESOURCE_DIRECT_DEPTH: usize = 32;

pub(super) const MAX_FORM_IMPORT_OBJECTS: usize = 65_536;

pub(super) const MAX_FORM_CONTENT_BYTES: usize = 64 * 1024 * 1024;

/// Target geometry for one placed PDF page.
#[derive(Clone, Copy)]
pub(super) struct PagePlacement {
    pub(super) rect: [f64; 4],
    pub(super) keep_proportion: bool,
}

/// Resource category materialized into a page-owned drawing dictionary.
#[derive(Clone, Copy)]
pub(super) enum PageResourceKind {
    XObject,
    Font,
}

impl PageResourceKind {
    pub(super) fn key(self) -> &'static [u8] {
        match self {
            Self::XObject => b"XObject",
            Self::Font => b"Font",
        }
    }

    pub(super) fn name_prefix(self) -> &'static str {
        match self {
            Self::XObject => "PyloX",
            Self::Font => "PyloF",
        }
    }
}

/// Fully materialized page dictionary with one reserved resource-name slot.
pub(super) struct PageResourcePlan {
    pub(super) page: Dictionary,
    pub(super) kind: PageResourceKind,
    pub(super) name: String,
}

impl PageResourcePlan {
    pub(super) fn name(&self) -> &str {
        &self.name
    }

    pub(super) fn bind(mut self, resource_id: ObjectId) -> PyResult<Dictionary> {
        let resources = self
            .page
            .get_mut(b"Resources")
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        let entries = resources
            .get_mut(self.kind.key())
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        let slot = entries.get_mut(self.name.as_bytes()).map_err(to_py_err)?;
        *slot = Object::Reference(resource_id);
        Ok(self.page)
    }
}

/// Prepared page-content and resource commits for one drawing operation.
pub(super) struct PageDrawingPlan {
    pub(super) content: draw::ContentAppendPlan,
    pub(super) resource: PageResourcePlan,
}

/// Source-owned objects and Form stream prepared before target mutation.
pub(super) struct FormImportPlan {
    pub(super) objects: Vec<(ObjectId, Object)>,
    pub(super) form: Stream,
    pub(super) new_max_id: u32,
    pub(super) crop: [f64; 4],
    pub(super) rotation: i64,
}

/// Resolve page `/Resources` or one of its categories under a bounded chain.
///
/// A null value, an indirect null, and a reference to a missing object read
/// as an absent entry, which the caller starts empty.
pub(super) fn resolve_page_resource_dictionary<'a>(
    doc: &'a Document,
    object: &'a Object,
    label: &str,
) -> PyResult<Option<&'a Dictionary>> {
    let mut current = object;
    let mut visited = Vec::new();
    visited
        .try_reserve_exact(MAX_PAGE_RESOURCE_REFERENCE_DEPTH)
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to inspect {label} reference chain: {error}"
            ))
        })?;
    loop {
        let Object::Reference(id) = current else {
            return match current {
                Object::Null => Ok(None),
                Object::Dictionary(dictionary) => Ok(Some(dictionary)),
                _ => Err(PdfError::new_err(format!(
                    "{label} must resolve to a dictionary"
                ))),
            };
        };
        if visited.contains(id) {
            return Err(PdfError::new_err(format!(
                "{label} contains a reference cycle"
            )));
        }
        if visited.len() >= MAX_PAGE_RESOURCE_REFERENCE_DEPTH {
            return Err(PdfError::new_err(format!(
                "{label} exceeds the {MAX_PAGE_RESOURCE_REFERENCE_DEPTH}-reference-depth safety limit"
            )));
        }
        visited.push(*id);
        let Some(object) = doc.objects.get(id) else {
            return Ok(None);
        };
        current = object;
    }
}

pub(super) fn page_resource_shape_error(label: &str, detail: &str, limit: usize) -> PyErr {
    PdfError::new_err(format!("{label} exceeds the {limit}-{detail} safety limit"))
}

/// Bound direct resource shapes before cloning them into a page-owned dictionary.
///
/// Indirect references are leaves; their target dictionaries are validated
/// separately when selected as Resources or the requested resource category.
pub(crate) fn validate_page_resource_shape(dict: &Dictionary, label: &str) -> PyResult<()> {
    if dict.len() > MAX_PAGE_RESOURCE_ENTRIES {
        return Err(page_resource_shape_error(
            label,
            "entry",
            MAX_PAGE_RESOURCE_ENTRIES,
        ));
    }
    let mut pending = Vec::new();
    pending.try_reserve_exact(dict.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate {label} direct-object validation stack: {error}"
        ))
    })?;
    for (_, value) in dict.iter() {
        pending.push((value, 2usize));
    }
    let mut objects = 1usize;
    let mut bytes = 0usize;
    for (key, _) in dict.iter() {
        bytes = bytes.checked_add(key.len()).ok_or_else(|| {
            PdfError::new_err(format!(
                "{label} direct data exceeds the platform size limit"
            ))
        })?;
    }
    while let Some((object, depth)) = pending.pop() {
        objects = objects.checked_add(1).ok_or_else(|| {
            PdfError::new_err(format!(
                "{label} direct objects exceed the platform size limit"
            ))
        })?;
        if objects > MAX_PAGE_RESOURCE_DIRECT_OBJECTS {
            return Err(page_resource_shape_error(
                label,
                "object",
                MAX_PAGE_RESOURCE_DIRECT_OBJECTS,
            ));
        }
        if depth > MAX_PAGE_RESOURCE_DIRECT_DEPTH {
            return Err(page_resource_shape_error(
                label,
                "level",
                MAX_PAGE_RESOURCE_DIRECT_DEPTH,
            ));
        }
        let mut add_bytes = |amount: usize| -> PyResult<()> {
            bytes = bytes.checked_add(amount).ok_or_else(|| {
                PdfError::new_err(format!(
                    "{label} direct data exceeds the platform size limit"
                ))
            })?;
            if bytes > MAX_PAGE_RESOURCE_DIRECT_BYTES {
                return Err(page_resource_shape_error(
                    label,
                    "byte",
                    MAX_PAGE_RESOURCE_DIRECT_BYTES,
                ));
            }
            Ok(())
        };
        let child_count = match object {
            Object::Name(value) | Object::String(value, _) => {
                add_bytes(value.len())?;
                0
            }
            Object::Array(items) => items.len(),
            Object::Dictionary(child) => {
                for (key, _) in child.iter() {
                    add_bytes(key.len())?;
                }
                child.len()
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
        if child_count != 0 && depth >= MAX_PAGE_RESOURCE_DIRECT_DEPTH {
            return Err(page_resource_shape_error(
                label,
                "level",
                MAX_PAGE_RESOURCE_DIRECT_DEPTH,
            ));
        }
        let scheduled = objects
            .checked_add(pending.len())
            .and_then(|total| total.checked_add(child_count))
            .ok_or_else(|| {
                PdfError::new_err(format!(
                    "{label} direct objects exceed the platform size limit"
                ))
            })?;
        if scheduled > MAX_PAGE_RESOURCE_DIRECT_OBJECTS {
            return Err(page_resource_shape_error(
                label,
                "object",
                MAX_PAGE_RESOURCE_DIRECT_OBJECTS,
            ));
        }
        pending.try_reserve(child_count).map_err(|error| {
            PdfError::new_err(format!(
                "failed to grow {label} direct-object validation stack: {error}"
            ))
        })?;
        match object {
            Object::Array(items) => {
                for child in items {
                    pending.push((child, depth + 1));
                }
            }
            Object::Dictionary(child) => {
                for (_, value) in child.iter() {
                    pending.push((value, depth + 1));
                }
            }
            Object::Stream(stream) => {
                for (_, value) in stream.dict.iter() {
                    pending.push((value, depth + 1));
                }
            }
            _ => {}
        }
    }
    if bytes > MAX_PAGE_RESOURCE_DIRECT_BYTES {
        return Err(page_resource_shape_error(
            label,
            "byte",
            MAX_PAGE_RESOURCE_DIRECT_BYTES,
        ));
    }
    Ok(())
}

pub(super) fn prepare_page_resource(
    doc: &Document,
    page_id: ObjectId,
    kind: PageResourceKind,
) -> PyResult<PageResourcePlan> {
    let mut page = resolve_inherited_page_dict(doc, page_id)?;
    let resources = match page.get(b"Resources") {
        Ok(object) => resolve_page_resource_dictionary(doc, object, "page Resources")?,
        Err(_) => None,
    };
    let mut resources = match resources {
        Some(resolved) => {
            validate_page_resource_shape(resolved, "page Resources")?;
            resolved.clone()
        }
        None => Dictionary::new(),
    };
    let key = kind.key();
    let label = match kind {
        PageResourceKind::XObject => "page Resources/XObject",
        PageResourceKind::Font => "page Resources/Font",
    };
    let entries = match resources.get(key) {
        Ok(object) => resolve_page_resource_dictionary(doc, object, label)?,
        Err(_) => None,
    };
    let mut entries = match entries {
        Some(resolved) => {
            validate_page_resource_shape(resolved, label)?;
            resolved.clone()
        }
        None => Dictionary::new(),
    };
    if !resources.has(key) && resources.len() >= MAX_PAGE_RESOURCE_ENTRIES {
        return Err(page_resource_shape_error(
            "page Resources",
            "entry",
            MAX_PAGE_RESOURCE_ENTRIES,
        ));
    }
    if entries.len() >= MAX_PAGE_RESOURCE_ENTRIES {
        return Err(page_resource_shape_error(
            label,
            "entry",
            MAX_PAGE_RESOURCE_ENTRIES,
        ));
    }

    let mut suffix = doc
        .max_id
        .checked_add(1)
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let name = loop {
        let candidate = format!("{}{suffix}", kind.name_prefix());
        if !entries.has(candidate.as_bytes()) {
            break candidate;
        }
        suffix = suffix
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF resource-name suffix limit reached"))?;
    };
    entries.set(name.as_bytes(), Object::Null);
    resources.set(key, entries);
    page.set("Resources", resources);
    Ok(PageResourcePlan { page, kind, name })
}

/// Read an indirect-capable box array as normalized `[x0, y0, x1, y1]`.
pub(super) fn resolve_box(doc: &Document, dict: &Dictionary, key: &[u8]) -> Option<[f64; 4]> {
    let obj = dict.get(key).ok()?;
    let obj = match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?,
        other => other,
    };
    let arr = obj.as_array().ok()?;
    if arr.len() != 4 {
        return None;
    }
    let mut v = [0f64; 4];
    for (slot, item) in v.iter_mut().zip(arr) {
        let resolved = match item {
            Object::Reference(id) => doc.get_object(*id).ok()?,
            other => other,
        };
        *slot = f64::from(resolved.as_float().ok()?);
    }
    Some([
        v[0].min(v[2]),
        v[1].min(v[3]),
        v[0].max(v[2]),
        v[1].max(v[3]),
    ])
}

pub(super) fn collect_form_page_content(doc: &Document, page_id: ObjectId) -> PyResult<Vec<u8>> {
    let streams = draw::inspect_page_contents(doc, page_id).map_err(PdfError::new_err)?;
    let payload_limit = MAX_FORM_CONTENT_BYTES - 5;
    let mut content = Vec::new();
    for stream_id in streams {
        let Ok(stream) = doc.get_object(stream_id).and_then(Object::as_stream) else {
            continue;
        };
        let remaining = payload_limit.saturating_sub(content.len());
        if remaining == 0 {
            return Err(PdfError::new_err(format!(
                "Form source page content exceeds the {MAX_FORM_CONTENT_BYTES}-byte safety limit"
            )));
        }
        let data_limit = remaining - 1;
        let decoded = decoded_stream_content_cow(
            stream,
            Some(data_limit),
            "form_content_size",
            "Form source page content",
        )?;
        let addition = decoded.len().checked_add(1).ok_or_else(|| {
            PdfError::new_err("Form source page content exceeds the platform size limit")
        })?;
        content.try_reserve_exact(addition).map_err(|error| {
            PdfError::new_err(format!("failed to grow Form source page content: {error}"))
        })?;
        content.extend_from_slice(&decoded);
        content.push(b'\n');
    }
    content.try_reserve_exact(5).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate Form source page wrapper: {error}"
        ))
    })?;
    let payload_len = content.len();
    content.resize(payload_len + 5, 0);
    content.copy_within(0..payload_len, 2);
    content[..2].copy_from_slice(b"q\n");
    content[payload_len + 2..].copy_from_slice(b"\nQ\n");
    Ok(content)
}

/// Prepare one source page as an unplaced Form without mutating the target.
pub(super) fn prepare_page_form_import(
    target_max_id: u32,
    source: Document,
    page_number: u32,
) -> PyResult<FormImportPlan> {
    let mut plan = prepare_pages_form_import(target_max_id, source, &[page_number])?;
    let form = plan
        .forms
        .pop()
        .ok_or_else(|| PdfError::new_err("Form import prepared no Form"))?;
    Ok(FormImportPlan {
        objects: plan.objects,
        form: form.stream,
        new_max_id: plan.new_max_id,
        crop: form.crop,
        rotation: form.rotation,
    })
}

/// One prepared Form and the display metadata of the page it came from.
pub(super) struct PreparedForm {
    pub(super) stream: Stream,
    pub(super) crop: [f64; 4],
    pub(super) rotation: i64,
}

/// Source objects shared by several prepared Forms, imported together.
pub(super) struct PagesFormImportPlan {
    pub(super) objects: Vec<(ObjectId, Object)>,
    pub(super) forms: Vec<PreparedForm>,
    pub(super) new_max_id: u32,
}

/// Prepare source pages as unplaced Forms without mutating the target.
///
/// The source is renumbered once and every object that any Form reaches is
/// imported once, so Forms that draw with the same font share its objects.
pub(super) fn prepare_pages_form_import(
    target_max_id: u32,
    mut source: Document,
    page_numbers: &[u32],
) -> PyResult<PagesFormImportPlan> {
    if source.objects.len() > MAX_FORM_IMPORT_OBJECTS {
        return Err(PdfError::new_err(format!(
            "Form import exceeds the {MAX_FORM_IMPORT_OBJECTS}-object safety limit"
        )));
    }
    let object_count = u32::try_from(source.objects.len())
        .map_err(|error| PdfError::new_err(error.to_string()))?;
    target_max_id
        .checked_add(object_count)
        .and_then(|highest_source_id| highest_source_id.checked_add(1))
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let starting_id = target_max_id
        .checked_add(1)
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
    let missing_page =
        |page_number: u32| PdfError::new_err(format!("source page {page_number} does not exist"));
    let mut source_pages = collect_page_ids(&source, None, None)?;
    for &page_number in page_numbers {
        page_id_from_index(&source_pages, page_number).map_err(|_| missing_page(page_number))?;
    }
    renumber_objects(&mut source, starting_id, &mut source_pages)?;

    let mut forms = Vec::new();
    forms
        .try_reserve_exact(page_numbers.len())
        .map_err(|error| {
            PdfError::new_err(format!("failed to allocate prepared Forms: {error}"))
        })?;
    for &page_number in page_numbers {
        let source_id = page_id_from_index(&source_pages, page_number)
            .map_err(|_| missing_page(page_number))?;
        let source_dict = resolve_inherited_page_dict(&source, source_id)?;
        // The Form shows what rendering shows of the source page.
        let source_crop = draw::display_box(
            resolve_box(&source, &source_dict, b"MediaBox"),
            resolve_box(&source, &source_dict, b"CropBox"),
        );
        let source_rotation = source_dict
            .get(b"Rotate")
            .ok()
            .and_then(|object| resolve_i64(&source, object))
            .unwrap_or(0)
            .rem_euclid(360);

        let form_content = collect_form_page_content(&source, source_id)?;
        let mut form_dict = dictionary! {
            "Type" => "XObject",
            "Subtype" => "Form",
            "FormType" => 1,
            "BBox" => Object::Array(
                source_crop
                    .iter()
                    .map(|&value| Object::Real(value as f32))
                    .collect(),
            ),
        };
        if let Ok(resources) = source_dict.get(b"Resources") {
            form_dict.set("Resources", resources.clone());
        }
        if let Ok(group) = source_dict.get(b"Group") {
            form_dict.set("Group", group.clone());
        }
        forms.push(PreparedForm {
            stream: Stream::new(form_dict, form_content).with_compression(false),
            crop: source_crop,
            rotation: source_rotation,
        });
    }

    // Move only the source objects that the Forms can reach. The page tree
    // stays behind, so a reference into it dangles, and outlines,
    // annotations, attachments, metadata, and the pages' original content
    // streams would be unreachable in the target. Pruning the complete
    // target after the import found the same objects, but walked every
    // target object for each placement.
    let reachable = reachable_object_ids(
        &source,
        forms
            .iter()
            .flat_map(|form| form.stream.dict.iter().map(|(_, value)| value)),
        |object| !matches!(object.type_name(), Ok(b"Catalog" | b"Pages" | b"Page")),
    )?;
    let new_max_id = source.max_id;
    let mut objects = Vec::new();
    objects
        .try_reserve_exact(reachable.len())
        .map_err(|error| {
            PdfError::new_err(format!("failed to allocate Form import objects: {error}"))
        })?;
    for (id, object) in source.objects {
        if reachable.contains(&id) {
            objects.push((id, object));
        }
    }
    Ok(PagesFormImportPlan {
        objects,
        forms,
        new_max_id,
    })
}

/// Destination of a prepared Form import.
///
/// The indexed document keeps its page index while the renumbered IDs are
/// unused; a plain lopdf document inserts them directly.
pub(super) trait FormImportTarget: ObjectSink {
    /// Insert renumbered objects at their IDs and set the highest object number.
    fn insert_new_objects(&mut self, objects: Vec<(ObjectId, Object)>, max_id: u32);

    /// The highest object number.
    fn max_id(&self) -> u32;
}

impl FormImportTarget for Document {
    fn max_id(&self) -> u32 {
        self.max_id
    }

    fn insert_new_objects(&mut self, objects: Vec<(ObjectId, Object)>, max_id: u32) {
        for (id, object) in objects {
            self.objects.insert(id, object);
        }
        self.max_id = max_id;
    }
}

/// Commit a fully prepared Form import without further recoverable work.
pub(super) fn commit_page_form_import(
    target: &mut impl FormImportTarget,
    plan: FormImportPlan,
) -> (ObjectId, [f64; 4], i64) {
    target.insert_new_objects(plan.objects, plan.new_max_id);
    let form_id = target.add_object(plan.form);
    (form_id, plan.crop, plan.rotation)
}

/// Commit several prepared Forms that share one import, in their order, and
/// return each Form with its content length.
pub(super) fn commit_pages_form_import(
    target: &mut impl FormImportTarget,
    plan: PagesFormImportPlan,
) -> Vec<(ObjectId, usize)> {
    target.insert_new_objects(plan.objects, plan.new_max_id);
    plan.forms
        .into_iter()
        .map(|form| {
            let len = form.stream.content.len();
            (target.add_object(form.stream), len)
        })
        .collect()
}

/// Import a page as an unplaced Form XObject and return its display metadata.
///
/// This is shared by page placement and krilla-generated widget appearances.
pub(super) fn import_page_as_form(
    target: &mut Document,
    source: Document,
    page_number: u32,
) -> PyResult<(ObjectId, [f64; 4], i64)> {
    let plan = prepare_page_form_import(target.max_id, source, page_number)?;
    Ok(commit_page_form_import(target, plan))
}

impl _Document {
    /// Validate one drawing insertion before caches or PDF objects are changed.
    ///
    /// The first insertion on a page decodes its existing content to count
    /// the graphics states it leaves unbalanced. Like the content guard, it
    /// re-applies no decompression limit: loading measured every stream under
    /// the configured budgets, and interpretation decodes the same content. A
    /// stream that fails to decode counts as empty, as hayro skips it.
    pub(super) fn prepare_page_content(
        &self,
        page_number: u32,
        overlay: bool,
    ) -> PyResult<(ObjectId, draw::ContentAppendPlan)> {
        let page_id = self.page_id(page_number)?;
        let plan = draw::prepare_push_content(
            &self.doc,
            page_id,
            self.isolated_content_pages.contains(&page_id),
            overlay,
            |stream| {
                decoded_stream_content_cow(stream, None, "page_content_size", "page content").ok()
            },
        )
        .map_err(PdfError::new_err)?;
        Ok((page_id, plan))
    }

    /// Prepare all page-owned drawing targets before dependent work begins.
    pub(super) fn prepare_page_drawing(
        &self,
        page_number: u32,
        overlay: bool,
        kind: PageResourceKind,
    ) -> PyResult<(ObjectId, PageDrawingPlan)> {
        let (page_id, content) = self.prepare_page_content(page_number, overlay)?;
        let resource = prepare_page_resource(&self.doc, page_id, kind)?;
        Ok((page_id, PageDrawingPlan { content, resource }))
    }

    /// Append one prepared stream and retain verified in-memory isolation state.
    ///
    /// New streams and the page's `/Contents` keep the page index.
    pub(super) fn push_page_content(
        &mut self,
        page_id: ObjectId,
        ops: Vec<u8>,
        plan: draw::ContentAppendPlan,
    ) -> PyResult<()> {
        let (contents, is_isolated) = draw::add_content_streams(&mut self.doc, ops, plan);
        self.doc
            .set_page_object_entry(page_id, "Contents", Object::Array(contents))?;
        if is_isolated {
            self.isolated_content_pages.insert(page_id);
        }
        Ok(())
    }

    /// Bind a prepared resource slot and replace the page without allocation.
    ///
    /// The replacement keeps the page's walked entries, so the page index survives.
    pub(super) fn commit_drawing_resource(
        &mut self,
        page_id: ObjectId,
        plan: PageResourcePlan,
        resource_id: ObjectId,
    ) -> PyResult<()> {
        let page = plan.bind(resource_id)?;
        self.doc.replace_page(page_id, page);
        Ok(())
    }

    /// Place a Form XObject on a page and return it.
    pub(super) fn place_pdf_page(
        &mut self,
        page_id: ObjectId,
        target_geometry: ([f64; 4], i64),
        form: PlacedForm,
        placement: PagePlacement,
        drawing_plan: PageDrawingPlan,
    ) -> PyResult<PlacedForm> {
        let content = draw::PlacedContent::Form {
            crop: form.crop(),
            rotation: form.rotation(),
        };
        let matrix = draw::placement_matrix(
            target_geometry.0,
            target_geometry.1,
            placement.rect,
            &content,
            placement.keep_proportion,
        );
        let ops = draw::draw_ops(matrix, drawing_plan.resource.name());
        self.commit_drawing_resource(page_id, drawing_plan.resource, form.id())?;
        self.push_page_content(page_id, ops, drawing_plan.content)?;
        Ok(form)
    }
}

impl _Document {
    /// Whether this document placed an image with `digest` that is still
    /// intact, so a canvas that draws it need not parse it.
    pub(crate) fn has_placed_image(&mut self, digest: &placement_reuse::ImageDigest) -> bool {
        self.placements.image(&self.doc, digest).is_some()
    }

    /// Whether this document imported the page of `source` as a Form that is
    /// still intact, so a canvas that draws it need not copy its document.
    pub(crate) fn has_placed_form(&mut self, source: FormSource) -> bool {
        self.placements.form(&self.doc, source).is_some()
    }

    /// The process-unique identity of the current content.
    pub(crate) fn revision(&self) -> u64 {
        self.revision
    }

    /// The display width and height of a one-based page; a page number that
    /// names no page is refused.
    pub(crate) fn page_extent(&self, page_number: u32) -> PyResult<(f64, f64)> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        Ok(draw::display_size(crop, rotation))
    }

    /// A copy of the editable document, as a canvas imports pages from it.
    pub(crate) fn snapshot(&self) -> Document {
        (*self.doc).clone()
    }

    /// Generate, import, and place a recorded canvas; see `commit_canvas`.
    pub(super) fn place_canvas(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        recording: CanvasRecording,
        overlay: bool,
    ) -> PyResult<()> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_size = draw::display_size(crop, rotation);
        let form_crop = [
            0.0,
            0.0,
            f64::from(page_size.0 as f32),
            f64::from(page_size.1 as f32),
        ];
        if (form_crop[2], form_crop[3]) != recording.size {
            return Err(PdfError::new_err(
                "the page changed size while the canvas was open",
            ));
        }
        let (page_id, drawing_plan) = py.detach(|| {
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)
        })?;
        let form = self.prepare_canvas_form(py, recording)?;

        // Every fallible step on the input is done; change the document.
        self.invalidate_hayro_pdf_keeping_placements();
        let committed = py.detach(|| -> PyResult<_> {
            let committed = commit_canvas_form(&mut self.doc, form, None)?;
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                PlacedForm::new(committed.form_id, committed.len, form_crop, 0),
                PagePlacement {
                    rect: form_crop,
                    keep_proportion: false,
                },
                drawing_plan,
            )?;
            Ok(committed)
        })?;
        self.remember_canvas_placements(committed, Some(page_number));
        Ok(())
    }

    /// Generate the text, parse the images, and import the pages of a
    /// recorded canvas, and compose its content, so that committing it as a
    /// Form XObject has no fallible work on the input left.
    pub(super) fn prepare_canvas_form(
        &mut self,
        py: Python<'_>,
        mut recording: CanvasRecording,
    ) -> PyResult<CanvasForm> {
        // Text in embedded faces: one krilla page for each run of text
        // items, imported with the numbers after the target's objects.
        let plan = recording.plan_embedded()?;
        let mut target_max_id = self.doc.max_id;
        let mut text_imports = Vec::new();
        if !plan.is_empty() {
            let generated = py
                .detach(|| recording.generate(&plan))
                .map_err(PdfError::new_err)?;
            text_imports
                .try_reserve_exact(generated.len())
                .map_err(|error| {
                    PdfError::new_err(format!("failed to allocate canvas text imports: {error}"))
                })?;
            for (document, page_count) in generated.into_iter().zip(plan.page_counts()) {
                let mut source = generated_pdf::load(&document.pdf)
                    .map_err(|error| lopdf_err(Some("failed to import canvas text"), &error))?;
                if let Some(face) = document.instance.and_then(|slot| recording.face(slot)) {
                    face.name_subset(&mut source);
                }
                let page_numbers = (1..=page_count)
                    .map(u32::try_from)
                    .collect::<Result<Vec<_>, _>>()
                    .map_err(|error| PdfError::new_err(error.to_string()))?;
                let import =
                    py.detach(|| prepare_pages_form_import(target_max_id, source, &page_numbers))?;
                target_max_id = advance_max_id(&import)?;
                text_imports.push(import);
            }
        }

        // Images: one placed before, or new streams.
        let mut images = Vec::new();
        images
            .try_reserve_exact(recording.images.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate canvas images: {error}"))
            })?;
        let mut image_sizes = Vec::new();
        image_sizes
            .try_reserve_exact(recording.images.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate canvas images: {error}"))
            })?;
        for image in &mut recording.images {
            let digest = *image
                .digest()
                .ok_or_else(|| PdfError::new_err("a new document's image reached an open one"))?;
            match self.placements.image(&self.doc, &digest) {
                Some(placed) => {
                    image_sizes.push((placed.width(), placed.height()));
                    images.push(CanvasImageObject::Placed(placed));
                }
                None => {
                    let parts = py.detach(|| image.take_parts())?;
                    image_sizes.push((parts.width, parts.height));
                    let streams = py
                        .detach(|| draw::image_streams(parts))
                        .map_err(PdfError::new_err)?;
                    images.push(CanvasImageObject::New(streams));
                }
            }
        }

        // Pages: a Form imported before, or one import per source document.
        let mut pages: Vec<Option<PlacedForm>> = Vec::new();
        pages
            .try_reserve_exact(recording.pages.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate canvas pages: {error}"))
            })?;
        let mut wanted: Vec<Vec<(usize, u32)>> = Vec::new();
        wanted
            .try_reserve_exact(recording.snapshots.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate canvas pages: {error}"))
            })?;
        wanted.resize_with(recording.snapshots.len(), Vec::new);
        for (index, page) in recording.pages.iter().enumerate() {
            match self.placements.form(&self.doc, page.source) {
                Some(placed) => pages.push(Some(placed)),
                None => {
                    let snapshot = page.snapshot.ok_or_else(|| {
                        PdfError::new_err(
                            "the document changed while the canvas was open, so a page it draws is no longer available",
                        )
                    })?;
                    pages.push(None);
                    let pending = &mut wanted[snapshot];
                    pending.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!("failed to allocate canvas pages: {error}"))
                    })?;
                    pending.push((index, page.source.page));
                }
            }
        }
        let mut page_imports = Vec::new();
        for (snapshot, pending) in recording.snapshots.drain(..).zip(wanted) {
            if pending.is_empty() {
                continue;
            }
            let source = Arc::try_unwrap(snapshot.1).unwrap_or_else(|shared| (*shared).clone());
            let page_numbers: Vec<u32> = pending.iter().map(|(_, page)| *page).collect();
            let import =
                py.detach(|| prepare_pages_form_import(target_max_id, source, &page_numbers))?;
            target_max_id = advance_max_id(&import)?;
            page_imports.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to allocate canvas pages: {error}"))
            })?;
            page_imports.push((import, pending));
        }
        let mut page_geometry = Vec::new();
        page_geometry
            .try_reserve_exact(pages.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate canvas pages: {error}"))
            })?;
        page_geometry.resize(pages.len(), ([0.0; 4], 0));
        for (index, page) in pages.iter().enumerate() {
            if let Some(placed) = page {
                page_geometry[index] = (placed.crop(), placed.rotation());
            }
        }
        for (import, pending) in &page_imports {
            for (form, (index, _)) in import.forms.iter().zip(pending.iter()) {
                page_geometry[*index] = (form.crop, form.rotation);
            }
        }

        let composed = recording.compose(
            &plan,
            &canvas::Placements {
                images: image_sizes,
                pages: page_geometry,
            },
        )?;

        // Pages of this document that an earlier placement imported; the
        // commit moves them to the revision of this edit with the new ones.
        let mut reused_forms = Vec::new();
        for (index, page) in pages.iter().enumerate() {
            if let Some(form) = page
                && recording.pages[index].source.revision == self.revision
            {
                reused_forms.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to allocate canvas pages: {error}"))
                })?;
                reused_forms.push((index, *form));
            }
        }

        Ok(CanvasForm {
            size: recording.size,
            recording,
            text_imports,
            images,
            pages,
            page_imports,
            composed,
            reused_forms,
            target_revision: self.revision,
        })
    }

    /// Remember the new images and pages of a committed canvas for later
    /// placements; `target_page` is the page that the canvas draws on.
    pub(super) fn remember_canvas_placements(
        &mut self,
        committed: CommittedCanvas,
        target_page: Option<u32>,
    ) {
        let CommittedCanvas {
            recording,
            new_images,
            new_forms,
            reused_forms,
            target_revision,
            ..
        } = committed;

        // Remember the new images and pages for later placements. A page of
        // this document as it was before the canvas stays as it was unless
        // the canvas drew on that page, so its Form, imported now or before,
        // is remembered under the revision of this edit, which every later
        // lookup names, and so consecutive placements of one page on other
        // pages share one Form.
        for (index, id) in new_images {
            if let Some(placed) = PlacedImage::read(&self.doc, id)
                && let Some(digest) = recording.images[index].digest().copied()
                && self.placements.reserve_image()
            {
                self.placements.remember_image(digest, placed);
            }
        }
        for (index, form) in new_forms.into_iter().chain(reused_forms) {
            let mut source = recording.pages[index].source;
            if source.revision == target_revision {
                self.placements.forget_form(source);
                if Some(source.page) == target_page {
                    continue;
                }
                source.revision = self.revision;
            }
            if self.placements.reserve_form() {
                self.placements.remember_form(source, form);
            }
        }
    }
}

/// Add a prepared canvas as a Form XObject whose BBox is the canvas,
/// with `matrix` as its `/Matrix` when given, and return it with what
/// `remember_canvas_placements` needs. The caller invalidates the
/// caches first.
pub(super) fn commit_canvas_form(
    target: &mut impl FormImportTarget,
    form: CanvasForm,
    matrix: Option<[f64; 6]>,
) -> PyResult<CommittedCanvas> {
    let CanvasForm {
        size,
        recording,
        text_imports,
        images,
        pages,
        page_imports,
        composed:
            canvas::Composed {
                root: content,
                groups,
                states: graphics_states,
            },
        reused_forms,
        target_revision,
    } = form;
    let form_crop = [0.0, 0.0, size.0, size.1];
    let mut xobjects = Dictionary::new();
    for (document, import) in text_imports.into_iter().enumerate() {
        for (page, (form_id, _)) in commit_pages_form_import(target, import)
            .into_iter()
            .enumerate()
        {
            xobjects.set(
                format!("PyloK{document}_{page}"),
                Object::Reference(form_id),
            );
        }
    }
    let mut new_images = Vec::new();
    for (index, image) in images.into_iter().enumerate() {
        let id = match image {
            CanvasImageObject::Placed(placed) => placed.id(),
            CanvasImageObject::New(streams) => {
                let id = draw::add_image_streams(target, streams);
                new_images.push((index, id));
                id
            }
        };
        xobjects.set(format!("PyloI{index}"), Object::Reference(id));
    }
    for (index, placed) in pages.iter().enumerate() {
        if let Some(placed) = placed {
            xobjects.set(format!("PyloP{index}"), Object::Reference(placed.id()));
        }
    }
    let mut new_forms = Vec::new();
    for (import, pending) in page_imports {
        let geometry: Vec<([f64; 4], i64)> = import
            .forms
            .iter()
            .map(|form| (form.crop, form.rotation))
            .collect();
        for (((form_id, len), (index, _)), (crop, rotation)) in
            commit_pages_form_import(target, import)
                .into_iter()
                .zip(pending.iter())
                .zip(geometry)
        {
            xobjects.set(format!("PyloP{index}"), Object::Reference(form_id));
            new_forms.push((*index, PlacedForm::new(form_id, len, crop, rotation)));
        }
    }
    let mut fonts = Dictionary::new();
    for (slot, font) in recording.standard_fonts.iter().enumerate() {
        let mut font_dict = dictionary! {
            "Type" => "Font",
            "Subtype" => "Type1",
            "BaseFont" => Object::Name(font.as_bytes().to_vec()),
        };
        if !matches!(*font, "Symbol" | "ZapfDingbats") {
            font_dict.set("Encoding", Object::Name(b"WinAnsiEncoding".to_vec()));
        }
        let font_id = target.add_object(font_dict);
        fonts.set(format!("PyloS{slot}"), Object::Reference(font_id));
    }
    // The groups and the composed Form share one resource
    // dictionary, which lists the groups and the graphics states,
    // and a soft mask's state names its group, so the dictionary,
    // the groups, and the states take the next numbers and are
    // inserted together.
    let base = target.max_id();
    let resources_id = (
        base.checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?,
        0,
    );
    let mut shared = Vec::new();
    shared
        .try_reserve_exact(groups.len() + graphics_states.len() + 1)
        .map_err(|error| PdfError::new_err(format!("failed to allocate canvas groups: {error}")))?;
    let mut group_ids = Vec::new();
    group_ids
        .try_reserve_exact(groups.len())
        .map_err(|error| PdfError::new_err(format!("failed to allocate canvas groups: {error}")))?;
    let mut next_id = resources_id.0;
    for (index, group) in groups.into_iter().enumerate() {
        next_id = next_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        xobjects.set(format!("PyloO{index}"), Object::Reference((next_id, 0)));
        group_ids.push((next_id, 0));
        // A soft mask's group names the colour space that a
        // luminosity mask reads, and hayro reads a soft mask of
        // either kind only when its group names one.
        let mut transparency = dictionary! {
            "Type" => "Group",
            "S" => "Transparency",
        };
        if group.mask.is_some() {
            transparency.set("CS", "DeviceRGB");
        }
        let form = Stream::new(
            dictionary! {
                "Type" => "XObject",
                "Subtype" => "Form",
                "FormType" => 1,
                "BBox" => Object::Array(
                    group.bbox.iter().map(|&value| Object::Real(value as f32)).collect(),
                ),
                "Group" => transparency,
                "Resources" => Object::Reference(resources_id),
            },
            group.content,
        )
        .with_compression(false);
        shared.push(((next_id, 0), Object::Stream(form)));
    }
    let mut states = Dictionary::new();
    for (slot, state) in graphics_states.iter().enumerate() {
        next_id = next_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        states.set(format!("PyloG{slot}"), Object::Reference((next_id, 0)));
        shared.push((
            (next_id, 0),
            Object::Dictionary(state.dictionary(&group_ids)),
        ));
    }
    let mut resources = Dictionary::new();
    if !xobjects.is_empty() {
        resources.set("XObject", xobjects);
    }
    if !fonts.is_empty() {
        resources.set("Font", fonts);
    }
    if !states.is_empty() {
        resources.set("ExtGState", states);
    }
    shared.push((resources_id, Object::Dictionary(resources)));
    target.insert_new_objects(shared, next_id);
    let len = content.len();
    let mut form = Stream::new(
        dictionary! {
            "Type" => "XObject",
            "Subtype" => "Form",
            "FormType" => 1,
            "BBox" => Object::Array(
                form_crop.iter().map(|&value| Object::Real(value as f32)).collect(),
            ),
            "Resources" => Object::Reference(resources_id),
        },
        content,
    )
    .with_compression(false);
    if let Some(matrix) = matrix {
        form.dict.set(
            "Matrix",
            Object::Array(
                matrix
                    .iter()
                    .map(|&value| Object::Real(value as f32))
                    .collect(),
            ),
        );
    }
    let form_id = target.add_object(form);
    Ok(CommittedCanvas {
        form_id,
        len,
        recording,
        new_images,
        new_forms,
        reused_forms,
        target_revision,
    })
}

/// A canvas prepared as a Form XObject, which `commit_canvas_form` adds.
pub(super) struct CanvasForm {
    /// The canvas's width and height, the Form's BBox.
    size: (f64, f64),
    recording: CanvasRecording,
    text_imports: Vec<PagesFormImportPlan>,
    images: Vec<CanvasImageObject>,
    pages: Vec<Option<PlacedForm>>,
    page_imports: Vec<(PagesFormImportPlan, Vec<(usize, u32)>)>,
    composed: canvas::Composed,
    /// Pages of this document that an earlier placement imported.
    reused_forms: Vec<(usize, PlacedForm)>,
    /// The document's revision when the canvas was prepared.
    target_revision: u64,
}

/// A canvas added as a Form XObject, with what remembering its placements
/// needs.
pub(super) struct CommittedCanvas {
    pub(super) form_id: ObjectId,
    /// The length of the Form's content.
    pub(super) len: usize,
    recording: CanvasRecording,
    new_images: Vec<(usize, ObjectId)>,
    new_forms: Vec<(usize, PlacedForm)>,
    reused_forms: Vec<(usize, PlacedForm)>,
    target_revision: u64,
}

/// An image that a canvas places: one placed before, or new streams.
enum CanvasImageObject {
    Placed(PlacedImage),
    New(draw::ImageStreams),
}

/// The highest object number after a prepared import and the Forms it adds.
fn advance_max_id(import: &PagesFormImportPlan) -> PyResult<u32> {
    u32::try_from(import.forms.len())
        .ok()
        .and_then(|forms| import.new_max_id.checked_add(forms))
        .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))
}

/// Implements [`_Document::commit_canvas`].
pub(super) fn commit_canvas(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    canvas: &Bound<'_, _Canvas>,
    overlay: bool,
) -> PyResult<()> {
    let recording = canvas.borrow_mut().take_recording()?;
    if recording.is_empty() {
        return Ok(());
    }
    this.place_canvas(py, page_number, recording, overlay)
}
