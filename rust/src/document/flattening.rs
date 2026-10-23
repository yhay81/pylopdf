//! Flattening (`Document.flatten`): annotation and widget appearances drawn
//! into page content, and the annotations removed.

use super::*;

/// The appearances of one page that flattening draws, and the `/Annots`
/// entries that it keeps.
struct FlatPage {
    page_id: ObjectId,
    keep: Vec<Object>,
    drawn: Vec<Object>,
    changed: bool,
}

/// Resolve a reference chain of at most 32 links to a stream's object ID.
fn appearance_stream_id(doc: &Document, object: &Object) -> Option<ObjectId> {
    let mut id = object.as_reference().ok()?;
    for _ in 0..32 {
        match doc.get_object(id).ok()? {
            Object::Stream(_) => return Some(id),
            Object::Reference(next) => id = *next,
            _ => return None,
        }
    }
    None
}

/// The normal appearance stream that hayro draws: `/AP /N` itself, or the
/// state that `/AS` names, else `/Off`.
fn normal_appearance(doc: &Document, annotation: &Dictionary) -> Option<ObjectId> {
    let appearances = annotation
        .get(b"AP")
        .ok()
        .and_then(|appearances| deref_dict_ref(doc, appearances))?;
    let normal = appearances.get(b"N").ok()?;
    if let Some(id) = appearance_stream_id(doc, normal) {
        return Some(id);
    }
    let states = deref_dict_ref(doc, normal)?;
    let state = annotation
        .get(b"AS")
        .ok()
        .and_then(|state| deref_object(doc, state).as_name().ok());
    state
        .and_then(|state| states.get(state).ok())
        .and_then(|stream| appearance_stream_id(doc, stream))
        .or_else(|| {
            states
                .get(b"Off")
                .ok()
                .and_then(|stream| appearance_stream_id(doc, stream))
        })
}

/// `N` finite numbers of an array, each resolved through one reference.
fn numbers<const N: usize>(doc: &Document, object: Option<&Object>) -> Option<[f64; N]> {
    let values = deref_object(doc, object?).as_array().ok()?;
    if values.len() != N {
        return None;
    }
    let mut out = [0.0; N];
    for (slot, value) in out.iter_mut().zip(values) {
        *slot = match deref_object(doc, value) {
            Object::Integer(value) => *value as f64,
            Object::Real(value) => f64::from(*value),
            _ => return None,
        };
        if !slot.is_finite() {
            return None;
        }
    }
    Some(out)
}

/// The matrix that maps an appearance onto its annotation rectangle
/// (ISO 32000-2 §12.5.5), and that rectangle, or `None` when either box is
/// unusable.
fn placement(
    doc: &Document,
    annotation: &Dictionary,
    stream: &Stream,
) -> Option<([f64; 6], [f64; 4])> {
    let bbox = numbers::<4>(doc, stream.dict.get(b"BBox").ok())?;
    let rect = numbers::<4>(doc, annotation.get(b"Rect").ok())?;
    let rect = [
        rect[0].min(rect[2]),
        rect[1].min(rect[3]),
        rect[0].max(rect[2]),
        rect[1].max(rect[3]),
    ];
    let [a, b, c, d, e, f] = numbers::<6>(doc, stream.dict.get(b"Matrix").ok())
        .unwrap_or([1.0, 0.0, 0.0, 1.0, 0.0, 0.0]);
    let corners = [
        (bbox[0], bbox[1]),
        (bbox[2], bbox[1]),
        (bbox[2], bbox[3]),
        (bbox[0], bbox[3]),
    ]
    .map(|(x, y)| (a * x + c * y + e, b * x + d * y + f));
    let [x0, y0, x1, y1] = draw::bounding_rect(&corners);
    let (width, height) = (x1 - x0, y1 - y0);
    if !(width > 0.0 && height > 0.0) {
        return None;
    }
    let (sx, sy) = ((rect[2] - rect[0]) / width, (rect[3] - rect[1]) / height);
    let matrix = [sx, 0.0, 0.0, sy, rect[0] - sx * x0, rect[1] - sy * y0];
    matrix
        .iter()
        .all(|value| value.is_finite())
        .then_some((matrix, rect))
}

/// Plan which entries of every page's `/Annots` flattening draws and
/// removes. Links stay; popups go with annotations; an entry that is no
/// annotation dictionary stays, and hayro, which stops drawing at such an
/// entry, draws none of the entries after it, so flattening draws none
/// either. Hidden annotations are removed undrawn.
fn plan_pages(
    this: &_Document,
    page_ids: &[ObjectId],
    annotations: bool,
    widgets: bool,
) -> PyResult<(Vec<FlatPage>, HashSet<ObjectId>, u32, u32)> {
    let mut pages = Vec::new();
    let mut synthesized = HashSet::new();
    let (mut removed_annotations, mut removed_widgets) = (0u32, 0u32);
    for &page_id in page_ids {
        let Some(items) = this.page_annotation_items(page_id)? else {
            continue;
        };
        let mut page = FlatPage {
            page_id,
            keep: Vec::new(),
            drawn: Vec::new(),
            changed: false,
        };
        page.keep
            .try_reserve_exact(items.len())
            .and_then(|()| page.drawn.try_reserve_exact(items.len()))
            .map_err(|error| PdfError::new_err(format!("failed to plan flattening: {error}")))?;
        let mut drawing = true;
        for item in items {
            let Some(annotation) = deref_dict_ref(&this.doc, item) else {
                drawing = false;
                page.keep.push(item.clone());
                continue;
            };
            let subtype = annotation
                .get(b"Subtype")
                .ok()
                .and_then(|subtype| deref_object(&this.doc, subtype).as_name().ok());
            let widget = subtype == Some(b"Widget".as_slice());
            let selected = match subtype {
                Some(b"Link") => false,
                Some(b"Widget") => widgets,
                _ => annotations,
            };
            if !selected {
                page.keep.push(item.clone());
                continue;
            }
            page.changed = true;
            if widget {
                removed_widgets = removed_widgets.saturating_add(1);
            } else if subtype != Some(b"Popup".as_slice()) {
                removed_annotations = removed_annotations.saturating_add(1);
            }
            let flags = annotation
                .get(b"F")
                .ok()
                .and_then(|flags| deref_object(&this.doc, flags).as_i64().ok())
                .unwrap_or(0);
            if drawing && flags & 2 == 0 {
                if let Ok(id) = item.as_reference() {
                    synthesized.insert(id);
                }
                page.drawn.push(item.clone());
            }
        }
        if page.changed {
            pages.try_reserve(1).map_err(|error| {
                PdfError::new_err(format!("failed to plan flattening: {error}"))
            })?;
            pages.push(page);
        }
    }
    Ok((pages, synthesized, removed_annotations, removed_widgets))
}

/// Draw one page's planned appearances through one Form XObject and keep
/// only the entries that flattening leaves in `/Annots`.
fn flatten_page(this: &mut _Document, page: FlatPage) -> PyResult<()> {
    let mut ops = Vec::new();
    let mut xobjects = Dictionary::new();
    let mut bounds: Option<[f64; 4]> = None;
    let mut forms = Vec::new();
    for item in &page.drawn {
        let Some(annotation) = deref_dict_ref(&this.doc, item) else {
            continue;
        };
        let Some(stream_id) = normal_appearance(&this.doc, annotation) else {
            continue;
        };
        let Ok(Object::Stream(stream)) = this.doc.get_object(stream_id) else {
            continue;
        };
        let Some((matrix, rect)) = placement(&this.doc, annotation, stream) else {
            continue;
        };
        let name = format!("A{}", xobjects.len());
        ops.extend_from_slice(&draw::draw_ops(matrix, &name));
        xobjects.set(name.as_bytes(), Object::Reference(stream_id));
        forms.push(stream_id);
        bounds = Some(match bounds {
            None => rect,
            Some(b) => [
                b[0].min(rect[0]),
                b[1].min(rect[1]),
                b[2].max(rect[2]),
                b[3].max(rect[3]),
            ],
        });
    }
    if let Some(bounds) = bounds {
        // `Do` draws only Form XObjects; an appearance stream without
        // `/Subtype` is one by its role.
        for id in forms {
            if let Ok(Object::Stream(stream)) = this.doc.get_object_mut(id)
                && !stream.dict.has(b"Subtype")
            {
                stream.dict.set("Subtype", "Form");
            }
        }
        let wrapper = Stream::new(
            dictionary! {
                "Type" => "XObject",
                "Subtype" => "Form",
                "BBox" => Object::Array(bounds.iter().map(|&v| Object::Real(v as f32)).collect()),
                "Resources" => dictionary! { "XObject" => xobjects },
            },
            ops,
        );
        let content = draw::prepare_push_content(
            &this.doc,
            page.page_id,
            this.isolated_content_pages.contains(&page.page_id),
            true,
            |stream| {
                decoded_stream_content_cow(stream, None, "page_content_size", "page content").ok()
            },
        )
        .map_err(PdfError::new_err)?;
        let resource = prepare_page_resource(&this.doc, page.page_id, PageResourceKind::XObject)?;
        let name = resource.name().to_owned();
        let wrapper_id = this.doc.add_object(wrapper);
        this.commit_drawing_resource(page.page_id, resource, wrapper_id)?;
        this.push_page_content(
            page.page_id,
            draw::draw_ops([1.0, 0.0, 0.0, 1.0, 0.0, 0.0], &name),
            content,
        )?;
    }
    let dict = this
        .doc
        .get_object_mut(page.page_id)
        .and_then(Object::as_dict_mut)
        .map_err(to_py_err)?;
    if page.keep.is_empty() {
        dict.remove(b"Annots");
    } else {
        dict.set("Annots", Object::Array(page.keep));
    }
    Ok(())
}

/// Implements [`_Document::flatten`]: returns how many annotations and
/// widgets it removed. On any error the document is restored.
pub(super) fn flatten(
    this: &mut _Document,
    py: Python<'_>,
    annotations: bool,
    widgets: bool,
) -> PyResult<(u32, u32)> {
    if !annotations && !widgets {
        return Ok((0, 0));
    }
    let page_ids = this.doc.owned_page_ids()?;
    let (pages, selected, removed_annotations, removed_widgets) =
        plan_pages(this, &page_ids, annotations, widgets)?;
    let form = widgets
        && this
            .doc
            .catalog()
            .is_ok_and(|catalog| catalog.has(b"AcroForm"));
    if pages.is_empty() && !form {
        return Ok((0, 0));
    }
    // Annotations without an appearance are drawn as rendering draws them.
    let mut missing = if annotations {
        missing_appearance_plans(&this.doc)?
    } else {
        MissingAppearances::default()
    };
    missing.retain(|id| selected.contains(&id));

    this.invalidate_hayro_pdf();
    py.detach(|| {
        let original = this.doc.clone();
        let isolated = this.isolated_content_pages.clone();
        let outcome = (|| -> PyResult<()> {
            if !missing.is_empty() {
                synthesize_missing_appearances_for_render(&mut this.doc, missing)?;
            }
            for page in pages {
                flatten_page(this, page)?;
            }
            if form {
                this.doc
                    .catalog_mut()
                    .map_err(to_py_err)?
                    .remove(b"AcroForm");
            }
            Ok(())
        })();
        if let Err(error) = outcome {
            this.doc.replace(original);
            this.isolated_content_pages = isolated;
            return Err(error);
        }
        Ok(())
    })?;
    this.invalidate_hayro_pdf();
    Ok((removed_annotations, removed_widgets))
}
