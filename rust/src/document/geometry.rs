//! Page geometry: inherited attributes, rotation, page boxes, and display
//! size.

use super::*;

/// Bound page-tree inheritance and one generated Form XObject import.
pub(super) const MAX_INHERITED_PAGE_TREE_DEPTH: usize = 64;

/// Page dictionary keys that may be inherited from parent nodes.
pub(super) const INHERITABLE_PAGE_KEYS: [&[u8]; 4] =
    [b"Resources", b"MediaBox", b"CropBox", b"Rotate"];

/// Safely convert f64 to PDF real representation (`lopdf::Object::Real = f32`).
pub(super) fn checked_pdf_real(value: f64, name: &str) -> PyResult<f32> {
    let converted = value as f32;
    if !value.is_finite() || !converted.is_finite() {
        return Err(PdfError::new_err(format!(
            "{name} must be a finite value within PDF real-number range: {value:?}"
        )));
    }
    Ok(converted)
}

/// Treat null page attributes as omitted without bypassing malformed-value checks.
pub(super) fn page_attribute_has_value(
    doc: &Document,
    dictionary: &Dictionary,
    key: &[u8],
) -> bool {
    let Ok(mut value) = dictionary.get(key) else {
        return false;
    };
    // A null dictionary value is equivalent to an omitted key, including an
    // indirect null and a reference to a missing object, which ISO 32000-2
    // §7.3.10 reads as null. Import renumbering turns such references into
    // null, so imported, flattened, and read pages inherit the same value.
    // Leave over-deep reference chains to the existing readers.
    for _ in 0..MAX_PAGE_RESOURCE_REFERENCE_DEPTH {
        match value {
            Object::Null => return false,
            Object::Reference(id) => match doc.objects.get(id) {
                Some(object) => value = object,
                None => return false,
            },
            _ => return true,
        }
    }
    !matches!(value, Object::Null)
}

/// Resolve one dictionary of a page-attribute inheritance walk.
///
/// A `/Parent` that names a missing object or an indirect null ends
/// inheritance, as a null `/Parent` does.
pub(super) fn inheritance_node(doc: &Document, id: ObjectId) -> PyResult<Option<&Dictionary>> {
    non_null_object(doc, id)
        .and_then(|node| node.map(Object::as_dict).transpose())
        .map_err(to_py_err)
}

/// Materialize inherited page attributes before discarding the source page tree.
pub(super) fn resolve_inherited_page_dict(
    doc: &Document,
    page_id: ObjectId,
) -> PyResult<Dictionary> {
    let page = doc
        .get_object(page_id)
        .and_then(Object::as_dict)
        .map_err(to_py_err)?;
    let mut dict = page.clone();
    let mut missing = INHERITABLE_PAGE_KEYS.map(|key| !page_attribute_has_value(doc, &dict, key));
    if !missing.iter().any(|&value| value) {
        return Ok(dict);
    }
    let mut visited = Vec::new();
    visited
        .try_reserve_exact(MAX_INHERITED_PAGE_TREE_DEPTH)
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to inspect inherited page attributes: {error}"
            ))
        })?;
    visited.push(page_id);
    let mut parent = page.get(b"Parent").and_then(Object::as_reference).ok();
    while let Some(parent_id) = parent {
        if visited.contains(&parent_id) {
            return Err(PdfError::new_err(
                "page inheritance tree contains a reference cycle",
            ));
        }
        if visited.len() >= MAX_INHERITED_PAGE_TREE_DEPTH {
            return Err(PdfError::new_err(format!(
                "page inheritance tree exceeds the {MAX_INHERITED_PAGE_TREE_DEPTH}-level safety limit"
            )));
        }
        visited.push(parent_id);
        let Some(parent_dict) = inheritance_node(doc, parent_id)? else {
            break;
        };
        for (index, key) in INHERITABLE_PAGE_KEYS.iter().enumerate() {
            if missing[index]
                && page_attribute_has_value(doc, parent_dict, key)
                && let Ok(value) = parent_dict.get(key)
            {
                dict.set(*key, value.clone());
                missing[index] = false;
            }
        }
        if !missing.iter().any(|&value| value) {
            break;
        }
        parent = parent_dict
            .get(b"Parent")
            .and_then(Object::as_reference)
            .ok();
    }
    // Reparenting must retain source defaults instead of inheriting values
    // from a destination tree with a different crop, rotation, or resources.
    if !page_attribute_has_value(doc, &dict, b"Resources") {
        dict.set("Resources", Dictionary::new());
    }
    if !page_attribute_has_value(doc, &dict, b"Rotate") {
        dict.set("Rotate", 0);
    }
    if !page_attribute_has_value(doc, &dict, b"CropBox")
        && page_attribute_has_value(doc, &dict, b"MediaBox")
        && let Ok(media_box) = dict.get(b"MediaBox")
    {
        dict.set("CropBox", media_box.clone());
    }
    Ok(dict)
}

impl _Document {
    /// Read a page attribute while resolving inheritance and indirect references.
    pub(super) fn resolve_page_attr(
        &self,
        page_id: ObjectId,
        key: &[u8],
    ) -> PyResult<Option<Object>> {
        let mut current = Some(page_id);
        let mut visited = Vec::new();
        visited
            .try_reserve_exact(MAX_INHERITED_PAGE_TREE_DEPTH)
            .map_err(|error| {
                PdfError::new_err(format!(
                    "failed to inspect inherited page attribute: {error}"
                ))
            })?;
        while let Some(id) = current {
            if visited.contains(&id) {
                return Err(PdfError::new_err(
                    "page inheritance tree contains a reference cycle",
                ));
            }
            if visited.len() >= MAX_INHERITED_PAGE_TREE_DEPTH {
                return Err(PdfError::new_err(format!(
                    "page inheritance tree exceeds the {MAX_INHERITED_PAGE_TREE_DEPTH}-level safety limit"
                )));
            }
            visited.push(id);
            // `page_id` comes from the page index, so only a parent can end
            // the walk here.
            let Some(dict) = inheritance_node(&self.doc, id)? else {
                break;
            };
            if page_attribute_has_value(&self.doc, dict, key)
                && let Ok(value) = dict.get(key)
            {
                let resolved = match value {
                    Object::Reference(rid) => self.doc.get_object(*rid).map_err(to_py_err)?.clone(),
                    other => other.clone(),
                };
                return Ok(Some(resolved));
            }
            current = dict.get(b"Parent").and_then(Object::as_reference).ok();
        }
        Ok(None)
    }

    /// Page display geometry: the box that rendering displays, plus rotation.
    pub(super) fn page_display_geometry(&self, page_number: u32) -> PyResult<([f64; 4], i64)> {
        self.resolve_display_geometry(self.page_id(page_number)?)
    }

    /// Display geometry of one page object, without walking the page tree.
    ///
    /// The box is `draw::display_box`, the CropBox clipped to the MediaBox,
    /// so drawing, annotations, and `Page.rect` share hayro's display space.
    pub(super) fn resolve_display_geometry(&self, page_id: ObjectId) -> PyResult<([f64; 4], i64)> {
        let rotation = self.resolve_page_rotation(page_id)?;
        let as_array = |(x0, y0, x1, y1)| [x0, y0, x1, y1];
        let media = self.resolve_page_box(page_id, "MediaBox")?.map(as_array);
        let crop = self.resolve_page_box(page_id, "CropBox")?.map(as_array);
        Ok((draw::display_box(media, crop), rotation))
    }

    /// Return inherited, normalized 0..360 rotation for one page object.
    pub(super) fn resolve_page_rotation(&self, page_id: ObjectId) -> PyResult<i64> {
        match self.resolve_page_attr(page_id, b"Rotate")? {
            Some(obj) => Ok(obj.as_i64().map_err(to_py_err)?.rem_euclid(360)),
            None => Ok(0),
        }
    }

    /// Return a resolved page box of one page object, or None when absent.
    pub(super) fn resolve_page_box(
        &self,
        page_id: ObjectId,
        key: &str,
    ) -> PyResult<Option<(f64, f64, f64, f64)>> {
        let Some(obj) = self.resolve_page_attr(page_id, key.as_bytes())? else {
            return Ok(None);
        };
        self.page_box_value(&obj, key).map(Some)
    }

    /// Return a box of the page dictionary itself, or None when absent:
    /// ISO 32000-2 Table 31 lets only the MediaBox and CropBox inherit.
    fn own_page_box(&self, page_id: ObjectId, key: &str) -> PyResult<Option<PageBox>> {
        let Some(page) = inheritance_node(&self.doc, page_id)? else {
            return Ok(None);
        };
        if !page_attribute_has_value(&self.doc, page, key.as_bytes()) {
            return Ok(None);
        }
        let Ok(value) = page.get(key.as_bytes()) else {
            return Ok(None);
        };
        let resolved = match value {
            Object::Reference(id) => self.doc.get_object(*id).map_err(to_py_err)?,
            other => other,
        };
        self.page_box_value(resolved, key).map(Some)
    }

    /// Read a page box value, an array of four numbers.
    fn page_box_value(&self, obj: &Object, key: &str) -> PyResult<PageBox> {
        let arr = obj.as_array().map_err(to_py_err)?;
        if arr.len() != 4 {
            return Err(PdfError::new_err(format!(
                "{key} must be a 4-element array ({} elements given)",
                arr.len()
            )));
        }
        let mut values = [0f64; 4];
        for (slot, item) in values.iter_mut().zip(arr) {
            let resolved = match item {
                Object::Reference(id) => self.doc.get_object(*id).map_err(to_py_err)?,
                other => other,
            };
            *slot = f64::from(resolved.as_float().map_err(to_py_err)?);
        }
        Ok((values[0], values[1], values[2], values[3]))
    }
}

/// A page box as `(x0, y0, x1, y1)` in PDF user space, as stored.
pub(super) type PageBox = (f64, f64, f64, f64);

/// The MediaBox, CropBox, BleedBox, TrimBox, and ArtBox of one page.
pub(super) type PageBoxes = (
    Option<PageBox>,
    Option<PageBox>,
    Option<PageBox>,
    Option<PageBox>,
    Option<PageBox>,
);

/// Implements [`_Document::page_boxes`]: the MediaBox and CropBox as the
/// page inherits them, and the BleedBox, TrimBox, and ArtBox of the page
/// dictionary itself.
pub(super) fn page_boxes(this: &_Document, page_number: u32) -> PyResult<PageBoxes> {
    let page_id = this.page_id(page_number)?;
    Ok((
        this.resolve_page_box(page_id, "MediaBox")?,
        this.resolve_page_box(page_id, "CropBox")?,
        this.own_page_box(page_id, "BleedBox")?,
        this.own_page_box(page_id, "TrimBox")?,
        this.own_page_box(page_id, "ArtBox")?,
    ))
}

/// Implements [`_Document::remove_page_box`]: remove a CropBox, BleedBox,
/// TrimBox, or ArtBox from the page dictionary. When an ancestor would
/// still give the page a CropBox, the page takes its MediaBox as its
/// CropBox instead, so that removing the CropBox shows the whole MediaBox.
pub(super) fn remove_page_box(this: &mut _Document, page_number: u32, key: &str) -> PyResult<()> {
    if !matches!(key, "CropBox" | "BleedBox" | "TrimBox" | "ArtBox") {
        return Err(PdfError::new_err(format!(
            "a page's {key} cannot be removed"
        )));
    }
    let page_id = this.page_id(page_number)?;
    let parent = inheritance_node(&this.doc, page_id)?
        .and_then(|page| page.get(b"Parent").and_then(Object::as_reference).ok());
    let replacement = match parent {
        Some(parent) if key == "CropBox" && this.resolve_page_box(parent, "CropBox")?.is_some() => {
            let (x0, y0, x1, y1) = this.resolve_page_box(page_id, "MediaBox")?.unwrap_or((
                0.0,
                0.0,
                draw::A4_WIDTH,
                draw::A4_HEIGHT,
            ));
            Some(Object::Array(vec![
                Object::Real(checked_pdf_real(x0, "x0")?),
                Object::Real(checked_pdf_real(y0, "y0")?),
                Object::Real(checked_pdf_real(x1, "x1")?),
                Object::Real(checked_pdf_real(y1, "y1")?),
            ]))
        }
        _ => None,
    };
    this.invalidate_hayro_pdf_keeping_placements();
    match replacement {
        Some(mediabox) => this.doc.set_page_object_entry(page_id, key, mediabox),
        None => this.doc.remove_page_box_entry(page_id, key),
    }
}

/// Implements [`_Document::page_display_matrix`]: the matrix that maps
/// PDF user space to the page's display space, as `draw::pdf_to_display`
/// maps points.
pub(super) fn page_display_matrix(
    this: &_Document,
    page_number: u32,
) -> PyResult<(f64, f64, f64, f64, f64, f64)> {
    let (display, rotation) = this.page_display_geometry(page_number)?;
    let (e, f) = draw::pdf_to_display(display, rotation, 0.0, 0.0);
    let (ax, ay) = draw::pdf_to_display(display, rotation, 1.0, 0.0);
    let (cx, cy) = draw::pdf_to_display(display, rotation, 0.0, 1.0);
    Ok((ax - e, ay - f, cx - e, cy - f, e, f))
}

/// Implements [`_Document::set_page_box`].
pub(super) fn set_page_box(
    this: &mut _Document,
    page_number: u32,
    key: &str,
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
) -> PyResult<()> {
    this.invalidate_hayro_pdf_keeping_placements();
    let x0 = checked_pdf_real(x0, "x0")?;
    let y0 = checked_pdf_real(y0, "y0")?;
    let x1 = checked_pdf_real(x1, "x1")?;
    let y1 = checked_pdf_real(y1, "y1")?;
    this.doc.set_page_entry(
        page_number,
        key,
        Object::Array(vec![
            Object::Real(x0),
            Object::Real(y0),
            Object::Real(x1),
            Object::Real(y1),
        ]),
    )
}

/// Implements [`_Document::set_mediabox`]: set the MediaBox and remove the
/// CropBox, BleedBox, TrimBox, and ArtBox, as pymupdf does. A CropBox that
/// the page would still inherit from an ancestor is replaced by the new
/// MediaBox, so the whole MediaBox stays visible.
pub(super) fn set_mediabox(
    this: &mut _Document,
    page_number: u32,
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
) -> PyResult<()> {
    let page_id = this.page_id(page_number)?;
    let corners = [
        checked_pdf_real(x0, "x0")?,
        checked_pdf_real(y0, "y0")?,
        checked_pdf_real(x1, "x1")?,
        checked_pdf_real(y1, "y1")?,
    ];
    let mediabox = Object::Array(corners.iter().copied().map(Object::Real).collect());
    this.invalidate_hayro_pdf_keeping_placements();
    this.doc
        .set_page_object_entry(page_id, "MediaBox", mediabox.clone())?;
    for key in ["CropBox", "BleedBox", "TrimBox", "ArtBox"] {
        this.doc.remove_page_box_entry(page_id, key)?;
    }
    if this.resolve_page_box(page_id, "CropBox")?.is_some() {
        this.doc
            .set_page_object_entry(page_id, "CropBox", mediabox)?;
    }
    Ok(())
}
