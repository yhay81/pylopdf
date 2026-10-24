//! Recoloring pages and documents to gray, RGB, or CMYK.
//!
//! Everything a page draws is recolored: the colour operators of its
//! content, of the Form XObjects, tiling patterns, Type 3 glyph procedures,
//! and annotation appearances it reaches, its images (drawn by hayro and
//! written again as 8-bit samples of the target space), its inline images,
//! its shadings with exponential or stitching functions, and its Indexed
//! palettes. `Document.recolor` changes every object once, in place;
//! `Page.recolor` first gives the page its own copy of each object that other
//! pages share and that recoloring changes, with the containers that lead
//! to it, so other pages keep their colours. The call restores a clone on
//! any error.

use std::collections::{HashMap, HashSet};

use crate::content::syntax::Instructions;
use crate::recolor::{self, Space, Target};
use crate::redact::{self, OperationEdit};

use super::*;

/// Nesting of Forms, patterns, and glyph procedures that recoloring follows.
const MAX_DEPTH: usize = 50;
/// Pixels of one image that recoloring converts.
const MAX_RECOLORED_PIXELS: u64 = 64_000_000;

fn target_of(name: &str) -> PyResult<Target> {
    match name {
        "gray" => Ok(Target::Gray),
        "rgb" => Ok(Target::Rgb),
        "cmyk" => Ok(Target::Cmyk),
        other => Err(PyValueError::new_err(format!(
            "colorspace must be 'gray', 'rgb', or 'cmyk': {other:?}"
        ))),
    }
}

/// Implements [`_Document::recolor`]: one page, or every page.
pub(super) fn recolor(
    this: &mut _Document,
    py: Python<'_>,
    page_number: Option<u32>,
    colorspace: &str,
) -> PyResult<()> {
    let target = target_of(colorspace)?;
    let original = this.doc.clone();
    let isolated = this.isolated_content_pages.clone();
    let result = (|| -> PyResult<()> {
        let pages: Vec<u32> = match page_number {
            Some(number) => vec![number],
            None => (1..=this.page_count()?)
                .map(|number| {
                    u32::try_from(number).map_err(|_| PdfError::new_err("page number out of range"))
                })
                .collect::<PyResult<_>>()?,
        };
        let mut done = HashSet::new();
        for number in pages {
            let page_id = this.page_id(number)?;
            if page_number.is_some() {
                isolate_page(this, page_id)?;
            }
            recolor_page(this, py, page_id, target, &mut done)?;
        }
        Ok(())
    })();
    this.invalidate_hayro_pdf();
    if result.is_err() {
        this.doc.replace(original);
        this.isolated_content_pages = isolated;
    }
    result
}

/// Whether recoloring changes an object of this kind.
fn colorable(object: &Object) -> bool {
    match object {
        Object::Stream(stream) => {
            let dict = &stream.dict;
            matches!(dict.get(b"Subtype"), Ok(Object::Name(name)) if name == b"Form")
                || (matches!(dict.get(b"Subtype"), Ok(Object::Name(name)) if name == b"Image")
                    && !matches!(dict.get(b"ImageMask"), Ok(Object::Boolean(true))))
                || dict.has(b"PatternType")
                || dict.has(b"ShadingType")
                // A Type 3 glyph procedure or an appearance without a subtype.
                || !dict.has(b"Subtype") && !dict.has(b"Type")
        }
        Object::Dictionary(dict) => dict.has(b"ShadingType") || dict.has(b"PatternType"),
        Object::Array(items) => {
            matches!(items.first(), Some(Object::Name(name)) if name == b"Indexed")
        }
        _ => false,
    }
}

/// The values of a page dictionary that lead to what it draws.
fn page_roots(page: &Dictionary) -> Vec<&Object> {
    page.iter()
        .filter(|(key, _)| key.as_slice() != b"Parent")
        .map(|(_, value)| value)
        .collect()
}

fn is_page_tree(object: &Object) -> bool {
    matches!(
        object.as_dict().ok().and_then(|dict| dict.get(b"Type").ok()),
        Some(Object::Name(name)) if name == b"Page" || name == b"Pages"
    )
}

/// Give a page its own copy of every object that other pages also reach,
/// that recoloring changes or that leads to one, and point the page's own
/// objects at the copies.
fn isolate_page(this: &mut _Document, page_id: ObjectId) -> PyResult<()> {
    // The page's resources become its own, so inherited ones stay shared.
    if let Some(resources) = this.resolve_page_attr(page_id, b"Resources")? {
        this.doc
            .set_page_object_entry(page_id, "Resources", resources)?;
    }
    let doc: &Document = &this.doc;
    let page = doc.get_dictionary(page_id).map_err(to_py_err)?;
    let own =
        crate::prune::reachable_object_ids(doc, page_roots(page), |object| !is_page_tree(object))?;
    let mut others = HashSet::new();
    let page_count = this.page_count()?;
    for number in 1..=page_count {
        let number =
            u32::try_from(number).map_err(|_| PdfError::new_err("page number out of range"))?;
        let other = this.page_id(number)?;
        if other == page_id {
            continue;
        }
        let dict = this.doc.get_dictionary(other).map_err(to_py_err)?;
        let mut roots = page_roots(dict);
        let inherited = this.resolve_page_attr(other, b"Resources")?;
        if let Some(inherited) = &inherited {
            roots.push(inherited);
        }
        others.extend(crate::prune::reachable_object_ids(
            &this.doc,
            roots,
            |object| !is_page_tree(object),
        )?);
    }
    let shared: HashSet<ObjectId> = own.intersection(&others).copied().collect();
    if shared.is_empty() {
        return Ok(());
    }
    // Shared objects that change, and the shared containers above them.
    let doc: &Document = &this.doc;
    let mut copy: HashSet<ObjectId> = shared
        .iter()
        .copied()
        .filter(|id| doc.get_object(*id).is_ok_and(colorable))
        .collect();
    loop {
        let before = copy.len();
        for id in &shared {
            if copy.contains(id) {
                continue;
            }
            if let Ok(object) = doc.get_object(*id)
                && references_any(object, &copy)
            {
                copy.insert(*id);
            }
        }
        if copy.len() == before {
            break;
        }
    }
    let mut map: HashMap<ObjectId, ObjectId> = HashMap::new();
    let mut next = this.doc.max_id;
    for id in &copy {
        next = next
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF object ID limit reached"))?;
        map.insert(*id, (next, 0));
    }
    let mut copies = Vec::new();
    for (id, new_id) in &map {
        let mut object = this.doc.get_object(*id).map_err(to_py_err)?.clone();
        redirect(&mut object, &map);
        copies.push((*new_id, object));
    }
    for (id, object) in copies {
        this.doc.objects.insert(id, object);
    }
    this.doc.max_id = next;
    // The page's own objects, and the page, name the copies.
    for id in own.difference(&shared) {
        if let Some(object) = this.doc.objects.get_mut(id) {
            redirect(object, &map);
        }
    }
    let mut page = this.doc.get_dictionary(page_id).map_err(to_py_err)?.clone();
    for (key, value) in page.iter_mut() {
        if key.as_slice() != b"Parent" {
            redirect(value, &map);
        }
    }
    this.doc.replace_page(page_id, page);
    Ok(())
}

fn references_any(object: &Object, set: &HashSet<ObjectId>) -> bool {
    let mut stack = vec![object];
    while let Some(object) = stack.pop() {
        match object {
            Object::Reference(id) if set.contains(id) => return true,
            Object::Array(items) => stack.extend(items),
            Object::Dictionary(dict) => stack.extend(dict.iter().map(|(_, value)| value)),
            Object::Stream(stream) => stack.extend(stream.dict.iter().map(|(_, value)| value)),
            _ => {}
        }
    }
    false
}

fn redirect(object: &mut Object, map: &HashMap<ObjectId, ObjectId>) {
    let mut stack = vec![object];
    while let Some(object) = stack.pop() {
        match object {
            Object::Reference(id) => {
                if let Some(new_id) = map.get(id) {
                    *id = *new_id;
                }
            }
            Object::Array(items) => stack.extend(items.iter_mut()),
            Object::Dictionary(dict) => stack.extend(dict.iter_mut().map(|(_, value)| value)),
            Object::Stream(stream) => stack.extend(stream.dict.iter_mut().map(|(_, value)| value)),
            _ => {}
        }
    }
}

/// A content stream to recolor: an object, or the page's joined content.
enum Holder {
    Page(ObjectId),
    Object(ObjectId),
}

/// Recolor everything one page draws, skipping objects already done.
fn recolor_page(
    this: &mut _Document,
    py: Python<'_>,
    page_id: ObjectId,
    target: Target,
    done: &mut HashSet<ObjectId>,
) -> PyResult<()> {
    let resources = match this.resolve_page_attr(page_id, b"Resources")? {
        Some(Object::Dictionary(dict)) => dict,
        _ => Dictionary::new(),
    };
    let mut queue: Vec<(Holder, Dictionary, usize)> = vec![(Holder::Page(page_id), resources, 0)];
    // Annotation appearances fall back to the page's resources.
    let page_resources = queue[0].1.clone();
    let page = this.doc.get_dictionary(page_id).map_err(to_py_err)?.clone();
    if let Some(annotations) = page
        .get(b"Annots")
        .ok()
        .and_then(|value| redact::resolve(&this.doc, value))
        .and_then(|value| value.as_array().ok())
    {
        for item in annotations {
            let Some(annotation) =
                redact::resolve(&this.doc, item).and_then(|value| value.as_dict().ok())
            else {
                continue;
            };
            for id in appearance_ids(&this.doc, annotation) {
                queue.push((Holder::Object(id), page_resources.clone(), 0));
            }
        }
    }
    let mut images = Vec::new();
    let mut shadings = Vec::new();
    while let Some((holder, resources, depth)) = queue.pop() {
        if let Holder::Object(id) = holder
            && !done.insert(id)
        {
            continue;
        }
        let own_resources = match holder {
            Holder::Page(_) => resources.clone(),
            Holder::Object(id) => this
                .doc
                .get_object(id)
                .and_then(Object::as_stream)
                .ok()
                .and_then(|stream| stream.dict.get(b"Resources").ok())
                .and_then(|value| redact::resolve(&this.doc, value))
                .and_then(|value| value.as_dict().ok())
                .cloned()
                .unwrap_or(resources),
        };
        recolor_stream(this, py, &holder, &own_resources, target)?;
        if depth >= MAX_DEPTH {
            continue;
        }
        // What the resources name: Forms, images, patterns, shadings,
        // palettes, and Type 3 glyph procedures.
        let doc: &Document = &this.doc;
        let category = |key: &[u8]| {
            own_resources
                .get(key)
                .ok()
                .and_then(|value| redact::resolve(doc, value))
                .and_then(|value| value.as_dict().ok())
                .cloned()
                .unwrap_or_default()
        };
        for (_, value) in &category(b"XObject") {
            let Ok(id) = value.as_reference() else {
                continue;
            };
            let Ok(stream) = doc.get_object(id).and_then(Object::as_stream) else {
                continue;
            };
            match stream.dict.get(b"Subtype") {
                Ok(Object::Name(name)) if name == b"Form" => {
                    queue.push((Holder::Object(id), own_resources.clone(), depth + 1));
                }
                Ok(Object::Name(name)) if name == b"Image" => images.push(id),
                _ => {}
            }
        }
        for (_, value) in &category(b"Pattern") {
            let Ok(id) = value.as_reference() else {
                continue;
            };
            match doc.get_object(id) {
                Ok(Object::Stream(_)) => {
                    queue.push((Holder::Object(id), own_resources.clone(), depth + 1))
                }
                Ok(Object::Dictionary(pattern)) => {
                    if let Ok(shading) = pattern.get(b"Shading") {
                        shadings.push(shading.clone());
                    }
                }
                _ => {}
            }
        }
        for (_, value) in &category(b"Shading") {
            shadings.push(value.clone());
        }
        for (_, value) in &category(b"Font") {
            let Some(font) = redact::resolve(doc, value).and_then(|value| value.as_dict().ok())
            else {
                continue;
            };
            if !matches!(font.get(b"Subtype"), Ok(Object::Name(name)) if name == b"Type3") {
                continue;
            }
            let font_resources = font
                .get(b"Resources")
                .ok()
                .and_then(|value| redact::resolve(doc, value))
                .and_then(|value| value.as_dict().ok())
                .cloned()
                .unwrap_or_else(|| own_resources.clone());
            if let Some(procs) = font
                .get(b"CharProcs")
                .ok()
                .and_then(|value| redact::resolve(doc, value))
                .and_then(|value| value.as_dict().ok())
            {
                for (_, proc) in procs {
                    if let Ok(id) = proc.as_reference() {
                        queue.push((Holder::Object(id), font_resources.clone(), depth + 1));
                    }
                }
            }
        }
        recolor_palettes(this, &own_resources, target)?;
    }
    for id in images {
        if done.insert(id) {
            recolor_image(this, py, id, target)?;
        }
    }
    for shading in shadings {
        recolor_shading(this, &shading, target, done)?;
    }
    Ok(())
}

fn appearance_ids(doc: &Document, annotation: &Dictionary) -> Vec<ObjectId> {
    let mut ids = Vec::new();
    let Some(appearances) = annotation
        .get(b"AP")
        .ok()
        .and_then(|value| redact::resolve(doc, value))
        .and_then(|value| value.as_dict().ok())
    else {
        return ids;
    };
    for (_, value) in appearances {
        match redact::resolve(doc, value) {
            Some(Object::Stream(_)) => ids.extend(value.as_reference().ok()),
            Some(Object::Dictionary(states)) => {
                ids.extend(
                    states
                        .iter()
                        .filter_map(|(_, state)| state.as_reference().ok()),
                );
            }
            _ => {}
        }
    }
    ids
}

/// Rewrite the colour operators of one content stream, and its inline images.
fn recolor_stream(
    this: &mut _Document,
    py: Python<'_>,
    holder: &Holder,
    resources: &Dictionary,
    target: Target,
) -> PyResult<()> {
    let data = match holder {
        Holder::Page(page_id) => replacement::page_content(&this.doc, *page_id)?,
        Holder::Object(id) => {
            let Ok(stream) = this.doc.get_object(*id).and_then(Object::as_stream) else {
                return Ok(());
            };
            replacement::interpreted_stream(stream).map_err(replacement::content_error)?
        }
    };
    let instructions = Instructions::parse(&data, None).map_err(replacement::content_error)?;
    let (mut edits, inline) =
        recolor::recolor_content(&this.doc, &instructions, Some(resources), target)
            .map_err(replacement::content_error)?;
    for index in inline {
        if let Some(bytes) =
            recolor_inline_image(this, py, &instructions, index, resources, target)?
        {
            edits
                .operations
                .insert(index, OperationEdit::Replace(bytes));
        }
    }
    if edits.operations.is_empty() {
        return Ok(());
    }
    let bytes = redact::rewrite_container(&instructions, &edits, false)
        .map_err(replacement::content_error)?;
    let mut stream = match holder {
        Holder::Page(_) => Stream::new(Dictionary::new(), bytes),
        Holder::Object(id) => {
            let original = this
                .doc
                .get_object(*id)
                .and_then(Object::as_stream)
                .map_err(to_py_err)?;
            let mut dict = original.dict.clone();
            for key in [b"Length".as_slice(), b"Filter", b"DecodeParms", b"DL"] {
                dict.remove(key);
            }
            Stream::new(dict, bytes)
        }
    };
    stream
        .compress()
        .map_err(|error| lopdf_err(Some("recolored content compression failed"), &error))?;
    match holder {
        Holder::Page(page_id) => {
            let id = this.doc.add_object(stream);
            this.doc
                .set_page_object_entry(*page_id, "Contents", Object::Reference(id))?;
            this.isolated_content_pages.remove(page_id);
        }
        Holder::Object(id) => {
            this.doc.objects.insert(*id, Object::Stream(stream));
        }
    }
    this.invalidate_hayro_pdf();
    Ok(())
}

/// The samples of an image drawn by hayro, as 8-bit RGB, and its size.
fn drawn_rgb(this: &mut _Document, py: Python<'_>, xref: u32) -> Option<(u32, u32, Vec<u8>)> {
    let pixmap = super::extraction::image_pixmap(this, py, xref, false).ok()?;
    let (width, height) = (pixmap.width, pixmap.height);
    let data = pixmap.data.as_ref();
    let channels = if pixmap.layout.gray { 1 } else { 3 } + usize::from(pixmap.layout.alpha);
    let mut rgb = Vec::with_capacity((width * height) as usize * 3);
    for pixel in data.chunks_exact(channels) {
        if pixmap.layout.gray {
            rgb.extend_from_slice(&[pixel[0]; 3]);
        } else {
            rgb.extend_from_slice(&pixel[..3]);
        }
    }
    Some((width, height, rgb))
}

/// Draw an image XObject with hayro and write it again in `target`.
fn recolor_image(
    this: &mut _Document,
    py: Python<'_>,
    id: ObjectId,
    target: Target,
) -> PyResult<()> {
    let Ok(stream) = this.doc.get_object(id).and_then(Object::as_stream) else {
        return Ok(());
    };
    if matches!(stream.dict.get(b"ImageMask"), Ok(Object::Boolean(true))) {
        return Ok(());
    }
    if let Ok(space) = stream.dict.get(b"ColorSpace")
        && recolor::space_of(&this.doc, space) == Space::Device(target)
        && matches!(stream.dict.get(b"ColorSpace"), Ok(Object::Name(_)))
    {
        return Ok(());
    }
    let dict = stream.dict.clone();
    let Some((width, height, rgb)) = drawn_rgb(this, py, id.0) else {
        return Ok(());
    };
    if u64::from(width) * u64::from(height) > MAX_RECOLORED_PIXELS {
        return Err(limit_err(
            "image_pixel_count",
            format!("a recolored image has more than {MAX_RECOLORED_PIXELS} pixels"),
        ));
    }
    let samples = py.detach(|| recolor::convert_samples(&rgb, Target::Rgb, target));
    let data = draw::flate_compress(&samples).map_err(PdfError::new_err)?;
    let mut new_dict = Dictionary::new();
    for key in [
        b"Type".as_slice(),
        b"Subtype",
        b"Interpolate",
        b"SMask",
        b"Intent",
        b"OC",
        b"Alt",
        b"Metadata",
    ] {
        if let Ok(value) = dict.get(key) {
            new_dict.set(key.to_vec(), value.clone());
        }
    }
    // A colour-key mask names colours of the old space; hayro already drew
    // the image without it, so it goes.
    if let Ok(mask @ Object::Reference(_)) = dict.get(b"Mask") {
        new_dict.set("Mask", mask.clone());
    }
    new_dict.set("Width", i64::from(width));
    new_dict.set("Height", i64::from(height));
    new_dict.set("ColorSpace", Object::Name(target.device_name().to_vec()));
    new_dict.set("BitsPerComponent", 8);
    new_dict.set("Filter", "FlateDecode");
    this.doc
        .objects
        .insert(id, Object::Stream(Stream::new(new_dict, data)));
    this.invalidate_hayro_pdf();
    Ok(())
}

/// Convert one inline image of a content stream, returning its new bytes.
fn recolor_inline_image(
    this: &mut _Document,
    py: Python<'_>,
    instructions: &Instructions<'_>,
    index: usize,
    resources: &Dictionary,
    target: Target,
) -> PyResult<Option<Vec<u8>>> {
    let operations = instructions
        .to_operations()
        .map_err(replacement::content_error)?;
    let Some(Object::Stream(inline)) = operations
        .get(index)
        .and_then(|operation| operation.operands.last())
    else {
        return Ok(None);
    };
    if matches!(
        inline
            .dict
            .get(b"IM")
            .or_else(|_| inline.dict.get(b"ImageMask")),
        Ok(Object::Boolean(true))
    ) {
        return Ok(None);
    }
    let stream = super::redaction_images::inline_image_xobject(&this.doc, inline, resources);
    let id = this.doc.add_object(Object::Stream(stream));
    let drawn = drawn_rgb(this, py, id.0);
    this.doc.objects.remove(&id);
    let Some((width, height, rgb)) = drawn else {
        return Ok(None);
    };
    let samples = recolor::convert_samples(&rgb, Target::Rgb, target);
    let space = match target {
        Target::Gray => b"G".to_vec(),
        Target::Rgb => b"RGB".to_vec(),
        Target::Cmyk => b"CMYK".to_vec(),
    };
    let mut dict = Dictionary::new();
    dict.set("W", i64::from(width));
    dict.set("H", i64::from(height));
    dict.set("BPC", 8);
    dict.set("CS", Object::Name(space));
    let encoded = draw::flate_compress(&samples).map_err(PdfError::new_err)?;
    dict.set("F", Object::Name(b"Fl".to_vec()));
    let image = Stream::new(dict, encoded);
    redact::encode(vec![lopdf::content::Operation::new(
        "BI",
        vec![Object::Stream(image)],
    )])
    .map(Some)
    .map_err(replacement::content_error)
}

/// Convert the Indexed palettes of a resource dictionary in place.
fn recolor_palettes(this: &mut _Document, resources: &Dictionary, target: Target) -> PyResult<()> {
    let Some(spaces) = resources
        .get(b"ColorSpace")
        .ok()
        .and_then(|value| redact::resolve(&this.doc, value))
        .and_then(|value| value.as_dict().ok())
        .cloned()
    else {
        return Ok(());
    };
    for (_, value) in &spaces {
        let Ok(id) = value.as_reference() else {
            continue;
        };
        let Ok(Object::Array(items)) = this.doc.get_object(id) else {
            continue;
        };
        if let Some(converted) = converted_palette(&this.doc, items, target) {
            this.doc.objects.insert(id, Object::Array(converted));
        }
    }
    Ok(())
}

/// `[/Indexed base hival lookup]` with its base and lookup in `target`.
fn converted_palette(doc: &Document, items: &[Object], target: Target) -> Option<Vec<Object>> {
    if !matches!(items.first(), Some(Object::Name(name)) if name == b"Indexed") {
        return None;
    }
    let Space::Device(from) = recolor::space_of(doc, items.get(1)?) else {
        return None;
    };
    if from == target {
        return None;
    }
    let lookup = match redact::resolve(doc, items.get(3)?)? {
        Object::String(bytes, _) => bytes.clone(),
        Object::Stream(stream) => stream.decompressed_content().ok()?,
        _ => return None,
    };
    let converted = recolor::convert_samples(&lookup, from, target);
    Some(vec![
        Object::Name(b"Indexed".to_vec()),
        Object::Name(target.device_name().to_vec()),
        items.get(2)?.clone(),
        Object::String(converted, lopdf::StringFormat::Hexadecimal),
    ])
}

/// Convert a shading's colour space, background, and exponential or
/// stitching functions in place; other functions keep their colours.
fn recolor_shading(
    this: &mut _Document,
    shading: &Object,
    target: Target,
    done: &mut HashSet<ObjectId>,
) -> PyResult<()> {
    let Ok(id) = shading.as_reference() else {
        return Ok(());
    };
    if !done.insert(id) {
        return Ok(());
    }
    let doc: &Document = &this.doc;
    let Ok(object) = doc.get_object(id) else {
        return Ok(());
    };
    let dict = match object {
        Object::Dictionary(dict) => dict,
        Object::Stream(stream) => &stream.dict,
        _ => return Ok(()),
    };
    // Mesh shadings hold their colours in their data.
    if matches!(dict.get(b"ShadingType").and_then(Object::as_i64), Ok(4..=7)) {
        return Ok(());
    }
    let Some(Space::Device(from)) = dict
        .get(b"ColorSpace")
        .ok()
        .map(|space| recolor::space_of(doc, space))
    else {
        return Ok(());
    };
    let Some(function) = dict
        .get(b"Function")
        .ok()
        .and_then(|value| converted_function(doc, value, from, target))
    else {
        return Ok(());
    };
    let mut dict = dict.clone();
    dict.set("ColorSpace", Object::Name(target.device_name().to_vec()));
    dict.set("Function", function);
    if let Ok(Object::Array(background)) = dict.get(b"Background") {
        let values: Option<Vec<f32>> = background
            .iter()
            .map(|value| value.as_float().ok())
            .collect();
        if let Some(values) = values {
            dict.set(
                "Background",
                Object::Array(
                    recolor::convert(&values, from, target)
                        .into_iter()
                        .map(Object::Real)
                        .collect(),
                ),
            );
        }
    }
    let replacement = match doc.get_object(id) {
        Ok(Object::Stream(stream)) => Object::Stream(Stream::new(dict, stream.content.clone())),
        _ => Object::Dictionary(dict),
    };
    this.doc.objects.insert(id, replacement);
    Ok(())
}

/// A Type 2 or Type 3 function whose outputs are colours of `from`, as
/// a direct function with outputs in `to`; `None` for other functions.
fn converted_function(doc: &Document, value: &Object, from: Target, to: Target) -> Option<Object> {
    let dict = match redact::resolve(doc, value)? {
        Object::Dictionary(dict) => dict,
        _ => return None,
    };
    match dict.get(b"FunctionType").ok()?.as_i64().ok()? {
        2 => {
            let read = |key: &[u8], default: f32| -> Option<Vec<f32>> {
                match dict.get(key) {
                    Ok(value) => redact::resolve(doc, value)?
                        .as_array()
                        .ok()?
                        .iter()
                        .map(|item| item.as_float().ok())
                        .collect(),
                    Err(_) => Some(vec![default; from.components()]),
                }
            };
            let (c0, c1) = (read(b"C0", 0.0)?, read(b"C1", 1.0)?);
            if c0.len() != from.components() || c1.len() != from.components() {
                return None;
            }
            let mut out = dict.clone();
            out.remove(b"Range");
            out.set(
                "C0",
                recolor::convert(&c0, from, to)
                    .into_iter()
                    .map(Object::Real)
                    .collect::<Vec<_>>(),
            );
            out.set(
                "C1",
                recolor::convert(&c1, from, to)
                    .into_iter()
                    .map(Object::Real)
                    .collect::<Vec<_>>(),
            );
            Some(Object::Dictionary(out))
        }
        3 => {
            let functions = redact::resolve(doc, dict.get(b"Functions").ok()?)?
                .as_array()
                .ok()?;
            let converted: Option<Vec<Object>> = functions
                .iter()
                .map(|function| converted_function(doc, function, from, to))
                .collect();
            let mut out = dict.clone();
            out.remove(b"Range");
            out.set("Functions", converted?);
            Some(Object::Dictionary(out))
        }
        _ => None,
    }
}
