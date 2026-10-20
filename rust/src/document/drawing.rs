//! Page content and resource plumbing for drawing, Form XObject imports,
//! `show_pdf_page`, and shapes.

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

/// A page Form to place: one placed before, or a prepared import.
pub(super) enum PageForm {
    Placed(PlacedForm),
    Import(FormImportPlan),
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
}

impl FormImportTarget for Document {
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

    /// Place a page Form, committing its prepared import first if it has one,
    /// and return the placed Form.
    pub(super) fn place_pdf_page(
        &mut self,
        page_id: ObjectId,
        target_geometry: ([f64; 4], i64),
        form: PageForm,
        placement: PagePlacement,
        drawing_plan: PageDrawingPlan,
    ) -> PyResult<PlacedForm> {
        let form = match form {
            PageForm::Placed(form) => form,
            PageForm::Import(plan) => {
                let len = plan.form.content.len();
                let (form_id, crop, rotation) = commit_page_form_import(&mut self.doc, plan);
                PlacedForm::new(form_id, len, crop, rotation)
            }
        };
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

    /// Generate, import, and place a recorded shape; see `commit_shape`.
    pub(super) fn place_shape(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        shape: &_Shape,
        overlay: bool,
    ) -> PyResult<()> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_size = draw::display_size(crop, rotation);
        let (page_id, drawing_plan) =
            self.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
        let plan = shape.plan().map_err(PdfError::new_err)?;
        let generated = py
            .detach(|| shape.generate(&plan, page_size))
            .map_err(PdfError::new_err)?;
        let mut sources = Vec::new();
        sources
            .try_reserve_exact(generated.len())
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to allocate generated shape documents: {error}"
                ))
            })?;
        for document in generated {
            let mut source = generated_pdf::load(&document.pdf)
                .map_err(|error| lopdf_err(Some("failed to import a generated shape"), &error))?;
            if let Some(face) = document.instance.and_then(|slot| shape.face(slot)) {
                face.name_subset(&mut source);
            }
            sources.push(source);
        }
        let placement = PagePlacement {
            rect: [0.0, 0.0, page_size.0, page_size.1],
            keep_proportion: false,
        };
        if let [
            Segment::Drawing {
                document: 0,
                page: 0,
            },
        ] = plan.segments.as_slice()
            && sources.len() == 1
            && let Some(source) = sources.pop()
        {
            // One generated page is placed as `insert_text` places its text.
            let form_plan = py.detach(|| prepare_page_form_import(self.doc.max_id, source, 1))?;
            self.invalidate_hayro_pdf_keeping_placements();
            py.detach(|| {
                self.place_pdf_page(
                    page_id,
                    (crop, rotation),
                    PageForm::Import(form_plan),
                    placement,
                    drawing_plan,
                )
            })?;
            return Ok(());
        }

        // Otherwise a composed Form draws the generated pages and the
        // Standard 14 text in order. It uses the generated pages' coordinate
        // space, so they are drawn unscaled and Standard 14 text is placed on
        // an unrotated page of the same size. Each document is imported with
        // object numbers after those of the documents before it and their
        // Forms, which the import adds after its objects.
        let form_crop = [
            0.0,
            0.0,
            f64::from(page_size.0 as f32),
            f64::from(page_size.1 as f32),
        ];
        let mut imports = Vec::new();
        imports.try_reserve_exact(sources.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate shape imports: {error}"))
        })?;
        let mut target_max_id = self.doc.max_id;
        for (source, page_count) in sources.into_iter().zip(plan.page_counts()) {
            let page_numbers = (1..=page_count)
                .map(u32::try_from)
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| PdfError::new_err(error.to_string()))?;
            let import =
                py.detach(|| prepare_pages_form_import(target_max_id, source, &page_numbers))?;
            target_max_id = u32::try_from(import.forms.len())
                .ok()
                .and_then(|forms| import.new_max_id.checked_add(forms))
                .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
            imports.push(import);
        }
        let mut content = Vec::new();
        let mut standard_fonts: Vec<&'static str> = Vec::new();
        for segment in &plan.segments {
            let ops = match *segment {
                Segment::Drawing { document, page } => draw::draw_ops(
                    [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
                    &format!("PyloK{document}_{page}"),
                ),
                Segment::Standard(index) => {
                    let font = shape.standard_font(index).map_err(PdfError::new_err)?;
                    let slot = match standard_fonts.iter().position(|known| *known == font) {
                        Some(slot) => slot,
                        None => {
                            standard_fonts.try_reserve(1).map_err(|error| {
                                PdfError::new_err(format!(
                                    "failed to grow the shape's Standard 14 fonts: {error}"
                                ))
                            })?;
                            standard_fonts.push(font);
                            standard_fonts.len() - 1
                        }
                    };
                    shape
                        .standard_ops(index, form_crop, &format!("PyloS{slot}"))
                        .map_err(PdfError::new_err)?
                }
            };
            content.try_reserve(ops.len()).map_err(|error| {
                PdfError::new_err(format!("failed to grow the shape's content: {error}"))
            })?;
            content.extend_from_slice(&ops);
        }

        self.invalidate_hayro_pdf_keeping_placements();
        py.detach(|| {
            let mut xobjects = Dictionary::new();
            for (document, import) in imports.into_iter().enumerate() {
                for (page, (form_id, _)) in commit_pages_form_import(&mut self.doc, import)
                    .into_iter()
                    .enumerate()
                {
                    xobjects.set(
                        format!("PyloK{document}_{page}"),
                        Object::Reference(form_id),
                    );
                }
            }
            let mut fonts = Dictionary::new();
            for (slot, font) in standard_fonts.iter().enumerate() {
                let mut font_dict = dictionary! {
                    "Type" => "Font",
                    "Subtype" => "Type1",
                    "BaseFont" => Object::Name(font.as_bytes().to_vec()),
                };
                if !matches!(*font, "Symbol" | "ZapfDingbats") {
                    font_dict.set("Encoding", Object::Name(b"WinAnsiEncoding".to_vec()));
                }
                let font_id = self.doc.add_object(font_dict);
                fonts.set(format!("PyloS{slot}"), Object::Reference(font_id));
            }
            let mut resources = Dictionary::new();
            if !xobjects.is_empty() {
                resources.set("XObject", xobjects);
            }
            if !fonts.is_empty() {
                resources.set("Font", fonts);
            }
            let len = content.len();
            let form = Stream::new(
                dictionary! {
                    "Type" => "XObject",
                    "Subtype" => "Form",
                    "FormType" => 1,
                    "BBox" => Object::Array(
                        form_crop.iter().map(|&value| Object::Real(value as f32)).collect(),
                    ),
                    "Resources" => resources,
                },
                content,
            )
            .with_compression(false);
            let form_id = self.doc.add_object(form);
            self.place_pdf_page(
                page_id,
                (crop, rotation),
                PageForm::Placed(PlacedForm::new(form_id, len, form_crop, 0)),
                placement,
                drawing_plan,
            )
        })?;
        Ok(())
    }
}

/// Implements [`_Document::show_pdf_page`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn show_pdf_page(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    other: &_Document,
    src_page_number: u32,
    keep_proportion: bool,
    overlay: bool,
) -> PyResult<()> {
    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let (page_id, drawing_plan) =
        this.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
    let placement = PagePlacement {
        rect: [rect.0, rect.1, rect.2, rect.3],
        keep_proportion,
    };
    let source = FormSource {
        revision: other.revision,
        page: src_page_number,
    };
    let form = match this.placements.form(&this.doc, source) {
        Some(form) => PageForm::Placed(form),
        None => PageForm::Import(py.detach(|| {
            prepare_page_form_import(this.doc.max_id, other.doc.clone(), src_page_number)
        })?),
    };
    let remember = matches!(form, PageForm::Import(_)) && this.placements.reserve_form();
    this.invalidate_hayro_pdf_keeping_placements();
    let form = py
        .detach(|| this.place_pdf_page(page_id, (crop, rotation), form, placement, drawing_plan))?;
    if remember {
        this.placements.remember_form(source, form);
    }
    Ok(())
}

/// Implements [`_Document::show_pdf_page_self`].
pub(super) fn show_pdf_page_self(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    rect: (f64, f64, f64, f64),
    src_page_number: u32,
    keep_proportion: bool,
    overlay: bool,
) -> PyResult<()> {
    let (crop, rotation) = this.page_display_geometry(page_number)?;
    let (page_id, drawing_plan) =
        this.prepare_page_drawing(page_number, overlay, PageResourceKind::XObject)?;
    let placement = PagePlacement {
        rect: [rect.0, rect.1, rect.2, rect.3],
        keep_proportion,
    };
    let source = FormSource {
        revision: this.revision,
        page: src_page_number,
    };
    let form = match this.placements.form(&this.doc, source) {
        Some(form) => PageForm::Placed(form),
        None => PageForm::Import(py.detach(|| {
            let source = this.doc.clone();
            prepare_page_form_import(this.doc.max_id, source, src_page_number)
        })?),
    };
    // This edit replaces the revision. Placing on another page leaves the
    // source page and everything it reaches as they were, so its Form stays
    // reusable under the new revision; a page placed on itself has changed.
    this.placements.forget_form(source);
    let remember = src_page_number != page_number && this.placements.reserve_form();
    this.invalidate_hayro_pdf_keeping_placements();
    let form = py
        .detach(|| this.place_pdf_page(page_id, (crop, rotation), form, placement, drawing_plan))?;
    if remember {
        let source = FormSource {
            revision: this.revision,
            page: src_page_number,
        };
        this.placements.remember_form(source, form);
    }
    Ok(())
}

/// Implements [`_Document::commit_shape`].
pub(super) fn commit_shape(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    shape: &Bound<'_, _Shape>,
    overlay: bool,
) -> PyResult<()> {
    let shape = shape.borrow();
    if shape.is_empty() {
        return Ok(());
    }
    this.place_shape(py, page_number, &shape, overlay)
}
