//! The fonts and Form XObjects that pages and documents hold: their
//! inventories, facts, font programs, and glyph widths.

use super::*;
use crate::content::ContentError;
use crate::content::fonts::FontMetrics;

/// Bound the resource walk of one page's inventory.
const MAX_INVENTORY_ENTRIES: usize = 4_096;
const MAX_INVENTORY_FORMS: usize = 4_096;
const MAX_INVENTORY_DEPTH: usize = 32;

/// Embedded CMaps that the widths of a composite font read are this small.
const MAX_FONT_CMAP_BYTES: usize = 16 * 1024 * 1024;

/// What a page's resources reach, once each in resource order: the image
/// XObjects, the Form XObjects, and the font dictionaries, by object number.
pub(super) struct ResourceInventory {
    pub(super) images: Vec<u32>,
    pub(super) forms: Vec<u32>,
    pub(super) fonts: Vec<u32>,
}

/// Which inventory a caller reads, which names the code of the refusal when
/// the page lists more than the walk admits.
#[derive(Clone, Copy)]
pub(super) enum InventoryKind {
    Images,
    Forms,
    Fonts,
}

impl InventoryKind {
    fn code(self) -> &'static str {
        match self {
            Self::Images => "image_inventory_count",
            Self::Forms => "form_inventory_count",
            Self::Fonts => "font_inventory_count",
        }
    }
}

/// The subtypes of a font dictionary that a page's `/Font` resources select.
const FONT_SUBTYPES: [&[u8]; 5] = [b"Type0", b"Type1", b"MMType1", b"TrueType", b"Type3"];

/// Walk a page's inherited resources and, depth first, the resources of the
/// Form XObjects they list, recording each indirect image XObject, Form
/// XObject, and font dictionary once in resource order. Inline images,
/// directly stored fonts, and what annotation appearances or Type 3 glyph
/// procedures hold are not listed, as in pymupdf.
pub(super) fn page_resource_inventory(
    this: &_Document,
    page_number: u32,
    kind: InventoryKind,
) -> PyResult<ResourceInventory> {
    let doc = &this.doc;
    let page_id = this.page_id(page_number)?;
    let page = resolve_inherited_page_dict(doc, page_id)?;
    let mut inventory = ResourceInventory {
        images: Vec::new(),
        forms: Vec::new(),
        fonts: Vec::new(),
    };
    let mut seen: HashSet<ObjectId> = HashSet::new();
    let grow = |label: &str, error: std::collections::TryReserveError| {
        PdfError::new_err(format!(
            "failed to grow the resource inventory {label}: {error}"
        ))
    };
    let push = |list: &mut Vec<u32>, id: u32, what: &str| -> PyResult<()> {
        if list.len() >= MAX_INVENTORY_ENTRIES {
            return Err(limit_err(
                kind.code(),
                format!("the page lists more than {MAX_INVENTORY_ENTRIES} {what}"),
            ));
        }
        list.try_reserve(1)
            .map_err(|error| grow("entries", error))?;
        list.push(id);
        Ok(())
    };
    // (resources, depth)
    let mut pending: Vec<(Dictionary, usize)> = Vec::new();
    if let Ok(resources) = page.get(b"Resources")
        && let Ok(resources) = deref_object(doc, resources).as_dict()
    {
        pending
            .try_reserve(1)
            .map_err(|error| grow("walk", error))?;
        pending.push((resources.clone(), 0));
    }
    while let Some((resources, depth)) = pending.pop() {
        if let Ok(fonts) = resources.get(b"Font")
            && let Ok(fonts) = deref_object(doc, fonts).as_dict()
        {
            for (_, value) in fonts.iter() {
                let Ok(id) = value.as_reference() else {
                    continue;
                };
                if font_dictionary(doc, id).is_some() && seen.insert(id) {
                    push(&mut inventory.fonts, id.0, "fonts")?;
                }
            }
        }
        let Ok(x_objects) = resources.get(b"XObject") else {
            continue;
        };
        let Ok(x_objects) = deref_object(doc, x_objects).as_dict() else {
            continue;
        };
        let mut forms = Vec::new();
        for (_, value) in x_objects.iter() {
            let Ok(id) = value.as_reference() else {
                continue;
            };
            let Ok(stream) = doc.get_object(id).and_then(Object::as_stream) else {
                continue;
            };
            match stream.dict.get(b"Subtype").and_then(Object::as_name) {
                Ok(b"Image") => {
                    if seen.insert(id) {
                        push(&mut inventory.images, id.0, "image XObjects")?;
                    }
                }
                Ok(b"Form") => {
                    if depth + 1 > MAX_INVENTORY_DEPTH || !seen.insert(id) {
                        continue;
                    }
                    if inventory.forms.len() >= MAX_INVENTORY_FORMS {
                        return Err(limit_err(
                            kind.code(),
                            format!("the page nests more than {MAX_INVENTORY_FORMS} Form XObjects"),
                        ));
                    }
                    push(&mut inventory.forms, id.0, "Form XObjects")?;
                    if let Ok(form_resources) = stream.dict.get(b"Resources")
                        && let Ok(form_resources) = deref_object(doc, form_resources).as_dict()
                    {
                        forms.try_reserve(1).map_err(|error| grow("walk", error))?;
                        forms.push((form_resources.clone(), depth + 1));
                    }
                }
                _ => {}
            }
        }
        // Visit nested Forms in resource order once the listing is complete.
        pending
            .try_reserve(forms.len())
            .map_err(|error| grow("walk", error))?;
        pending.extend(forms.into_iter().rev());
    }
    Ok(inventory)
}

/// The font dictionary stored as object `id`, when its `/Subtype` is one that
/// `/Font` resources select; a descendant CIDFont is not one.
fn font_dictionary(doc: &Document, id: ObjectId) -> Option<&Dictionary> {
    let dict = doc.get_object(id).ok()?.as_dict().ok()?;
    if dict
        .get(b"Type")
        .and_then(Object::as_name)
        .is_ok_and(|kind| kind != b"Font")
    {
        return None;
    }
    let subtype = dict.get(b"Subtype").and_then(Object::as_name).ok()?;
    FONT_SUBTYPES.contains(&subtype).then_some(dict)
}

/// The font dictionary stored under object number `xref`, of any generation.
fn font_by_xref(doc: &Document, xref: u32) -> Option<(ObjectId, &Dictionary)> {
    doc.objects
        .range((xref, 0)..=(xref, u16::MAX))
        .find_map(|(id, _)| font_dictionary(doc, *id).map(|dict| (*id, dict)))
}

/// Implements [`_Document::font_xrefs`].
pub(super) fn font_xrefs(this: &_Document) -> PyResult<Vec<u32>> {
    let doc = &this.doc;
    let mut xrefs: Vec<u32> = Vec::new();
    for id in doc.objects.keys() {
        if xrefs.last() == Some(&id.0) || font_dictionary(doc, *id).is_none() {
            continue;
        }
        xrefs
            .try_reserve(1)
            .map_err(|error| PdfError::new_err(format!("failed to grow the font list: {error}")))?;
        xrefs.push(id.0);
    }
    Ok(xrefs)
}

/// The facts of one font dictionary: its base font name (empty when absent),
/// subtype, encoding name, whether its glyphs are in the PDF, and the format
/// of its embedded program.
pub(super) type FontFactsTuple = (String, String, Option<String>, bool, Option<&'static str>);

fn name_text(doc: &Document, object: Option<&Object>) -> Option<String> {
    object
        .map(|object| deref_object(doc, object))
        .and_then(|object| object.as_name().ok())
        .map(|name| String::from_utf8_lossy(name).into_owned())
}

/// The font descriptor of a font, or of a Type 0 font's descendant CIDFont.
fn font_descriptor<'a>(doc: &'a Document, font: &'a Dictionary) -> Option<&'a Dictionary> {
    let font = if matches!(font.get(b"Subtype").and_then(Object::as_name), Ok(b"Type0")) {
        let descendants = deref_object(doc, font.get(b"DescendantFonts").ok()?)
            .as_array()
            .ok()?;
        deref_object(doc, descendants.first()?).as_dict().ok()?
    } else {
        font
    };
    deref_object(doc, font.get(b"FontDescriptor").ok()?)
        .as_dict()
        .ok()
}

/// The embedded program of a font: the descriptor's stream and its format.
fn font_file<'a>(doc: &'a Document, font: &'a Dictionary) -> Option<(&'a Stream, &'static str)> {
    let descriptor = font_descriptor(doc, font)?;
    for (key, format) in [
        (b"FontFile".as_slice(), "type1"),
        (b"FontFile2", "truetype"),
        (b"FontFile3", "cff"),
    ] {
        let Ok(value) = descriptor.get(key) else {
            continue;
        };
        let Ok(stream) = deref_object(doc, value).as_stream() else {
            continue;
        };
        let format = match stream.dict.get(b"Subtype").and_then(Object::as_name) {
            Ok(b"OpenType") => "opentype",
            _ => format,
        };
        return Some((stream, format));
    }
    None
}

/// Implements [`_Document::font_facts`].
pub(super) fn font_facts(this: &_Document, xref: u32) -> Option<FontFactsTuple> {
    let doc = &this.doc;
    let (_, font) = font_by_xref(doc, xref)?;
    let subtype = name_text(doc, font.get(b"Subtype").ok())?;
    let name = name_text(doc, font.get(b"BaseFont").ok())
        .or_else(|| name_text(doc, font.get(b"Name").ok()))
        .unwrap_or_default();
    let encoding = font
        .get(b"Encoding")
        .ok()
        .and_then(|value| match deref_object(doc, value) {
            Object::Name(name) => Some(String::from_utf8_lossy(name).into_owned()),
            Object::Dictionary(dict) => name_text(doc, dict.get(b"BaseEncoding").ok()),
            Object::Stream(stream) => name_text(doc, stream.dict.get(b"CMapName").ok()),
            _ => None,
        });
    let program = font_file(doc, font).map(|(_, format)| format);
    let embedded = program.is_some() || subtype == "Type3";
    Some((name, subtype, encoding, embedded, program))
}

/// Implements [`_Document::font_program`].
pub(super) fn font_program(
    this: &_Document,
    py: Python<'_>,
    xref: u32,
    max_size: Option<usize>,
) -> PyResult<Option<(&'static str, Vec<u8>)>> {
    let doc = &this.doc;
    let Some((_, font)) = font_by_xref(doc, xref) else {
        return Err(PdfError::new_err(format!("object {xref} is no font")));
    };
    let Some((stream, format)) = font_file(doc, font) else {
        return Ok(None);
    };
    py.detach(|| {
        decoded_stream_content(stream, max_size, "font_program_size", "font program")
            .map(|data| Some((format, data)))
    })
}

/// Implements [`_Document::font_widths`].
///
/// The advance of every one-byte code of a simple or Type 3 font, or every
/// two-byte code of a Type 0 font, in em, as the content engine and hayro
/// read them; a font that neither can read takes Helvetica's widths, as
/// hayro draws it.
pub(super) fn font_widths(this: &_Document, py: Python<'_>, xref: u32) -> PyResult<Vec<f32>> {
    let doc = &this.doc;
    let Some((_, font)) = font_by_xref(doc, xref) else {
        return Err(PdfError::new_err(format!("object {xref} is no font")));
    };
    let composite = matches!(font.get(b"Subtype").and_then(Object::as_name), Ok(b"Type0"));
    py.detach(|| {
        let decode = |stream: &Stream| {
            decoded_stream_content(
                stream,
                Some(MAX_FONT_CMAP_BYTES),
                "font_cmap_size",
                "font CMap",
            )
            .map_err(|error| ContentError::Malformed(error.to_string()))
        };
        let metrics = FontMetrics::read(doc, font, &decode).unwrap_or_else(FontMetrics::fallback);
        let count: u32 = if composite { 1 << 16 } else { 1 << 8 };
        let mut widths = Vec::new();
        widths.try_reserve_exact(count as usize).map_err(|error| {
            PdfError::new_err(format!("failed to allocate font widths: {error}"))
        })?;
        // Advances are in thousandths of an em.
        widths.extend((0..count).map(|code| (metrics.advance(code).0 / 1000.0) as f32));
        Ok(widths)
    })
}

/// The facts of one Form XObject: its `/BBox` and `/Matrix`.
pub(super) type FormFactsTuple = ((f64, f64, f64, f64), (f64, f64, f64, f64, f64, f64));

/// Implements [`_Document::form_facts`].
pub(super) fn form_facts(this: &_Document, xref: u32) -> Option<FormFactsTuple> {
    let doc = &this.doc;
    let stream = doc
        .objects
        .range((xref, 0)..=(xref, u16::MAX))
        .find_map(|(_, object)| object.as_stream().ok())?;
    if !matches!(
        stream.dict.get(b"Subtype").and_then(Object::as_name),
        Ok(b"Form")
    ) {
        return None;
    }
    let numbers = |key: &[u8], count: usize| -> Option<Vec<f64>> {
        let array = deref_object(doc, stream.dict.get(key).ok()?)
            .as_array()
            .ok()?;
        let values: Option<Vec<f64>> = array
            .iter()
            .map(|value| match deref_object(doc, value) {
                Object::Integer(value) => Some(*value as f64),
                Object::Real(value) => Some(f64::from(*value)),
                _ => None,
            })
            .collect();
        values.filter(|values| values.len() == count)
    };
    let bbox = numbers(b"BBox", 4).unwrap_or_else(|| vec![0.0; 4]);
    let matrix = numbers(b"Matrix", 6).unwrap_or_else(|| vec![1.0, 0.0, 0.0, 1.0, 0.0, 0.0]);
    Some((
        (bbox[0], bbox[1], bbox[2], bbox[3]),
        (
            matrix[0], matrix[1], matrix[2], matrix[3], matrix[4], matrix[5],
        ),
    ))
}
