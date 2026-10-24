//! Blanking the pixels of an image that a redaction area covers.
//!
//! hayro draws the image with its masks at its pixel size, as
//! `ImageObject.to_pixmap(alpha=True)` does, so every filter, colour space,
//! and mask that hayro reads decodes the same way. The pixels whose squares
//! meet an area become opaque black, and the result is written again as an
//! 8-bit DeviceRGB image with a soft mask for what was transparent; a stencil
//! mask stays a stencil mask whose covered samples paint nothing. The image's
//! original masks are not carried over, since the alpha holds them.

use hayro::kurbo::{Affine, Point, Rect};

use crate::content::Interpretation;

use super::*;

/// Pixels of one image that blanking decodes.
const MAX_BLANKED_PIXELS: u64 = 64_000_000;

/// Inline-image abbreviations of ISO 32000-2 Tables 91 and 92.
const KEYS: [(&[u8], &[u8]); 9] = [
    (b"W", b"Width"),
    (b"H", b"Height"),
    (b"BPC", b"BitsPerComponent"),
    (b"CS", b"ColorSpace"),
    (b"D", b"Decode"),
    (b"DP", b"DecodeParms"),
    (b"F", b"Filter"),
    (b"I", b"Interpolate"),
    (b"IM", b"ImageMask"),
];
const VALUES: [(&[u8], &[u8]); 12] = [
    (b"G", b"DeviceGray"),
    (b"RGB", b"DeviceRGB"),
    (b"CMYK", b"DeviceCMYK"),
    (b"I", b"Indexed"),
    (b"AHx", b"ASCIIHexDecode"),
    (b"A85", b"ASCII85Decode"),
    (b"LZW", b"LZWDecode"),
    (b"Fl", b"FlateDecode"),
    (b"RL", b"RunLengthDecode"),
    (b"CCF", b"CCITTFaxDecode"),
    (b"DCT", b"DCTDecode"),
    (b"L", b"Length"),
];

fn expand_name(name: &[u8], table: &[(&[u8], &[u8])]) -> Vec<u8> {
    table
        .iter()
        .find(|(short, _)| *short == name)
        .map_or_else(|| name.to_vec(), |(_, long)| long.to_vec())
}

fn expand_value(value: &Object) -> Object {
    match value {
        Object::Name(name) => Object::Name(expand_name(name, &VALUES)),
        Object::Array(items) => Object::Array(items.iter().map(expand_value).collect()),
        other => other.clone(),
    }
}

/// An inline image as an image XObject: abbreviations expanded and a named
/// colour space resolved through the container's resources.
fn inline_image_stream(doc: &Document, image: &Stream, resources: &Dictionary) -> Stream {
    let mut dict = Dictionary::new();
    dict.set("Type", Object::Name(b"XObject".to_vec()));
    dict.set("Subtype", Object::Name(b"Image".to_vec()));
    for (key, value) in &image.dict {
        if key == b"Length" || key == b"L" {
            continue;
        }
        let key = expand_name(key, &KEYS);
        let mut value = expand_value(value);
        if key == b"ColorSpace"
            && let Object::Name(name) = &value
            && !matches!(
                name.as_slice(),
                b"DeviceGray" | b"DeviceRGB" | b"DeviceCMYK" | b"Indexed" | b"Pattern"
            )
            && let Some(resolved) = resources
                .get(b"ColorSpace")
                .ok()
                .and_then(|value| crate::redact::resolve(doc, value))
                .and_then(|value| value.as_dict().ok())
                .and_then(|spaces| spaces.get(name).ok())
        {
            value = resolved.clone();
        }
        dict.set(key, value);
    }
    Stream::new(dict, image.content.clone())
}

/// Whether the square of one pixel, in the image's unit space, meets a
/// convex polygon there.
fn square_meets(polygon: &[Point; 4], x0: f64, y0: f64, x1: f64, y1: f64) -> bool {
    let square = [
        Point::new(x0, y0),
        Point::new(x1, y0),
        Point::new(x1, y1),
        Point::new(x0, y1),
    ];
    let separated = |a: &[Point; 4], b: &[Point; 4]| {
        (0..4).any(|index| {
            let p = a[index];
            let q = a[(index + 1) % 4];
            let normal = (q.y - p.y, p.x - q.x);
            let project = |point: &Point| normal.0 * point.x + normal.1 * point.y;
            let (min_a, max_a) = a
                .iter()
                .map(project)
                .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| {
                    (lo.min(v), hi.max(v))
                });
            let (min_b, max_b) = b
                .iter()
                .map(project)
                .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| {
                    (lo.min(v), hi.max(v))
                });
            max_a <= min_b || max_b <= min_a
        })
    };
    !separated(polygon, &square) && !separated(&square, polygon)
}

/// Blank the pixels of one placement that the areas cover, returning the
/// new image and its soft mask, or `None` when hayro cannot draw the image.
pub(super) fn blank_image(
    this: &mut _Document,
    py: Python<'_>,
    interpretation: &Interpretation,
    index: usize,
    areas: &[Rect],
    page_id: ObjectId,
) -> PyResult<Option<(Stream, Option<Stream>)>> {
    let image = &interpretation.images[index];
    let Some(inverse) = invertible(image.matrix) else {
        return Ok(None);
    };
    // The image to draw: the XObject, or the inline image as one.
    let (xref, temporary) = match image.xobject {
        Some(id) => (id, false),
        None => {
            let Some(stream) = inline_stream(this, interpretation, index, page_id)? else {
                return Ok(None);
            };
            (this.doc.add_object(Object::Stream(stream)), true)
        }
    };
    let stencil = this
        .doc
        .get_object(xref)
        .and_then(Object::as_stream)
        .ok()
        .and_then(|stream| stream.dict.get(b"ImageMask").ok())
        .is_some_and(|value| matches!(value, Object::Boolean(true)));
    let pixmap = super::extraction::image_pixmap(this, py, xref.0, true);
    if temporary {
        this.doc.objects.remove(&xref);
    }
    let Ok(pixmap) = pixmap else {
        return Ok(None);
    };
    let (width, height) = (pixmap.width, pixmap.height);
    if u64::from(width) * u64::from(height) > MAX_BLANKED_PIXELS {
        return Err(limit_err(
            "image_pixel_count",
            format!("a redacted image has more than {MAX_BLANKED_PIXELS} pixels"),
        ));
    }
    let mut rgba: Vec<u8> = pixmap.data.as_ref().clone();
    let polygons: Vec<[Point; 4]> = areas
        .iter()
        .map(|area| {
            [
                Point::new(area.x0, area.y0),
                Point::new(area.x1, area.y0),
                Point::new(area.x1, area.y1),
                Point::new(area.x0, area.y1),
            ]
            .map(|corner| inverse * corner)
        })
        .collect();
    let (w, h) = (f64::from(width), f64::from(height));
    py.detach(|| {
        for polygon in &polygons {
            // Pixel row 0 is the top of the unit square.
            let (min_u, max_u, min_v, max_v) = polygon.iter().fold(
                (
                    f64::INFINITY,
                    f64::NEG_INFINITY,
                    f64::INFINITY,
                    f64::NEG_INFINITY,
                ),
                |(a, b, c, d), p| (a.min(p.x), b.max(p.x), c.min(p.y), d.max(p.y)),
            );
            let columns = ((min_u * w).floor().max(0.0) as u32)..((max_u * w).ceil().min(w) as u32);
            let rows = (((1.0 - max_v) * h).floor().max(0.0) as u32)
                ..(((1.0 - min_v) * h).ceil().min(h) as u32);
            for row in rows {
                for column in columns.clone() {
                    let (u0, u1) = (f64::from(column) / w, f64::from(column + 1) / w);
                    let (v1, v0) = (1.0 - f64::from(row) / h, 1.0 - f64::from(row + 1) / h);
                    if square_meets(polygon, u0, v0, u1, v1) {
                        let at = ((row * width + column) * 4) as usize;
                        rgba[at..at + 4].copy_from_slice(if stencil {
                            &[0, 0, 0, 0]
                        } else {
                            &[0, 0, 0, 255]
                        });
                    }
                }
            }
        }
    });
    let interpolate = this
        .doc
        .get_object(xref)
        .and_then(Object::as_stream)
        .ok()
        .and_then(|stream| stream.dict.get(b"Interpolate").ok().cloned());
    let streams = py.detach(|| -> PyResult<(Stream, Option<Stream>)> {
        let pixels = (width * height) as usize;
        let mut dict = lopdf::dictionary! {
            "Type" => "XObject",
            "Subtype" => "Image",
            "Width" => i64::from(width),
            "Height" => i64::from(height),
        };
        if let Some(interpolate) = interpolate {
            dict.set("Interpolate", interpolate);
        }
        if stencil {
            // Sample 0 paints, as the default decode array of a stencil mask says.
            let row_bytes = (width as usize).div_ceil(8);
            let mut bits = vec![0xFF_u8; row_bytes * height as usize];
            for pixel in 0..pixels {
                if rgba[pixel * 4 + 3] >= 128 {
                    let (row, column) = (pixel / width as usize, pixel % width as usize);
                    bits[row * row_bytes + column / 8] &= !(0x80 >> (column % 8));
                }
            }
            dict.set("ImageMask", true);
            dict.set("BitsPerComponent", 1);
            let data = draw::flate_compress(&bits).map_err(PdfError::new_err)?;
            dict.set("Filter", "FlateDecode");
            return Ok((Stream::new(dict, data), None));
        }
        let mut rgb = Vec::new();
        rgb.try_reserve_exact(pixels * 3).map_err(|error| {
            PdfError::new_err(format!("failed to allocate a redacted image: {error}"))
        })?;
        let mut alpha = Vec::new();
        let mut opaque = true;
        for pixel in 0..pixels {
            rgb.extend_from_slice(&rgba[pixel * 4..pixel * 4 + 3]);
            opaque &= rgba[pixel * 4 + 3] == 255;
        }
        if !opaque {
            alpha.try_reserve_exact(pixels).map_err(|error| {
                PdfError::new_err(format!("failed to allocate a redacted mask: {error}"))
            })?;
            alpha.extend((0..pixels).map(|pixel| rgba[pixel * 4 + 3]));
        }
        dict.set("ColorSpace", "DeviceRGB");
        dict.set("BitsPerComponent", 8);
        dict.set("Filter", "FlateDecode");
        let data = draw::flate_compress(&rgb).map_err(PdfError::new_err)?;
        let mask = if opaque {
            None
        } else {
            let mask_data = draw::flate_compress(&alpha).map_err(PdfError::new_err)?;
            Some(Stream::new(
                lopdf::dictionary! {
                    "Type" => "XObject",
                    "Subtype" => "Image",
                    "Width" => i64::from(width),
                    "Height" => i64::from(height),
                    "ColorSpace" => "DeviceGray",
                    "BitsPerComponent" => 8,
                    "Filter" => "FlateDecode",
                },
                mask_data,
            ))
        };
        Ok((Stream::new(dict, data), mask))
    })?;
    Ok(Some(streams))
}

fn invertible(matrix: Affine) -> Option<Affine> {
    let [a, b, c, d, _, _] = matrix.as_coeffs();
    let determinant = a * d - b * c;
    (determinant.is_finite() && determinant.abs() > 1e-12).then(|| matrix.inverse())
}

/// The inline image of one placement as an image XObject stream.
fn inline_stream(
    this: &_Document,
    interpretation: &Interpretation,
    index: usize,
    page_id: ObjectId,
) -> PyResult<Option<Stream>> {
    use crate::content::syntax::Instructions;

    let image = &interpretation.images[index];
    let doc: &Document = &this.doc;
    let data = match crate::redact::form_id(interpretation, image.container) {
        None => replacement::page_content(doc, page_id)?,
        Some(id) => {
            let stream = doc
                .get_object(id)
                .and_then(Object::as_stream)
                .map_err(to_py_err)?;
            replacement::interpreted_stream(stream).map_err(replacement::content_error)?
        }
    };
    let instructions = Instructions::parse(&data, None).map_err(replacement::content_error)?;
    let operations = instructions
        .to_operations()
        .map_err(replacement::content_error)?;
    let Some(Object::Stream(inline)) = operations
        .get(image.operation)
        .and_then(|operation| operation.operands.last())
    else {
        return Ok(None);
    };
    let page_resources = this.resolve_page_attr(page_id, b"Resources")?;
    let resources = match &page_resources {
        Some(Object::Dictionary(dict)) => dict.clone(),
        _ => Dictionary::new(),
    };
    Ok(Some(inline_image_stream(doc, inline, &resources)))
}
