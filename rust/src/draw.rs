//! Drawing primitives for image insertion and overlaying pages from another PDF.
//!
//! Existing content streams are never decoded or re-encoded because a round trip
//! through lopdf's content parser can trigger #535-class edge cases. Drawing only
//! appends streams to `/Contents`. Existing graphics state is isolated by wrapping
//! the original sequence in q/Q streams once.

use std::io::Write;

use lopdf::{Dictionary, Document, Object, ObjectId, Stream, dictionary};

/// Decoded image data used to build a PDF Image XObject.
pub struct ImageParts {
    pub width: u32,
    pub height: u32,
    /// ColorSpace name for the XObject dictionary.
    pub color_space: &'static str,
    /// Filter name: DCTDecode passes JPEG through; FlateDecode compresses samples.
    pub filter: &'static str,
    /// Stream data after applying the filter.
    pub data: Vec<u8>,
    /// Raw grayscale alpha for an SMask, before Flate compression.
    pub alpha: Option<Vec<u8>>,
}

/// Detect image format from magic bytes and convert it to XObject data.
///
/// JPEG passes through with DCTDecode; PNG is decoded and Flate-compressed.
/// Return None for unsupported formats so the caller can report an error.
pub fn parse_image(data: &[u8]) -> Result<Option<ImageParts>, String> {
    if data.starts_with(&[0xFF, 0xD8, 0xFF]) {
        return jpeg_parts(data).map(Some);
    }
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return png_parts(data).map(Some);
    }
    Ok(None)
}

/// Read dimensions/components from a JPEG SOF marker and pass bytes through.
fn jpeg_parts(data: &[u8]) -> Result<ImageParts, String> {
    let mut pos = 2usize;
    while pos + 4 <= data.len() {
        if data[pos] != 0xFF {
            pos += 1;
            continue;
        }
        let marker = data[pos + 1];
        // Skip standalone RST/TEM markers and 0xFF fill bytes.
        if marker == 0xFF || (0xD0..=0xD7).contains(&marker) || marker == 0x01 {
            pos += 2;
            continue;
        }
        let length = usize::from(u16::from_be_bytes([data[pos + 2], data[pos + 3]]));
        // SOF0-15 carry dimensions, excluding C4=DHT, C8=JPG, and CC=DAC.
        if matches!(marker, 0xC0..=0xCF) && !matches!(marker, 0xC4 | 0xC8 | 0xCC) {
            if length < 8
                || pos
                    .checked_add(2 + length)
                    .is_none_or(|end| end > data.len())
            {
                return Err("corrupt JPEG SOF segment length".to_owned());
            }
            // Reading components at pos+9 requires at least 10 bytes.
            if pos.checked_add(10).is_none_or(|end| end > data.len()) {
                return Err("corrupt JPEG SOF segment".to_owned());
            }
            let height = u32::from(u16::from_be_bytes([data[pos + 5], data[pos + 6]]));
            let width = u32::from(u16::from_be_bytes([data[pos + 7], data[pos + 8]]));
            let components = data[pos + 9];
            let color_space = match components {
                1 => "DeviceGray",
                3 => "DeviceRGB",
                4 => "DeviceCMYK",
                other => return Err(format!("unsupported JPEG color component count: {other}")),
            };
            if width == 0 || height == 0 {
                return Err("JPEG dimensions are zero".to_owned());
            }
            return Ok(ImageParts {
                width,
                height,
                color_space,
                filter: "DCTDecode",
                data: data.to_vec(),
                alpha: None,
            });
        }
        pos += 2 + length;
    }
    Err("no JPEG SOF marker found".to_owned())
}

/// Decode PNG to 8-bit Gray/RGB plus separate alpha, then Flate-compress it.
fn png_parts(data: &[u8]) -> Result<ImageParts, String> {
    let mut decoder = png::Decoder::new(std::io::Cursor::new(data));
    // Let the decoder expand palettes/tRNS and reduce 16-bit data to 8-bit.
    decoder.set_transformations(png::Transformations::normalize_to_color8());
    let mut reader = decoder
        .read_info()
        .map_err(|e| format!("failed to read PNG: {e}"))?;
    let buf_size = reader
        .output_buffer_size()
        .ok_or_else(|| "PNG is too large".to_owned())?;
    let mut buf = vec![0u8; buf_size];
    let info = reader
        .next_frame(&mut buf)
        .map_err(|e| format!("failed to decode PNG: {e}"))?;
    buf.truncate(info.buffer_size());
    let (width, height) = (info.width, info.height);
    if width == 0 || height == 0 {
        return Err("PNG dimensions are zero".to_owned());
    }

    let (color_space, samples, alpha) = match info.color_type {
        png::ColorType::Grayscale => ("DeviceGray", buf, None),
        png::ColorType::Rgb => ("DeviceRGB", buf, None),
        png::ColorType::GrayscaleAlpha => {
            let mut gray = Vec::with_capacity(buf.len() / 2);
            let mut a = Vec::with_capacity(buf.len() / 2);
            for pair in buf.chunks_exact(2) {
                gray.push(pair[0]);
                a.push(pair[1]);
            }
            ("DeviceGray", gray, Some(a))
        }
        png::ColorType::Rgba => {
            let mut rgb = Vec::with_capacity(buf.len() / 4 * 3);
            let mut a = Vec::with_capacity(buf.len() / 4);
            for px in buf.chunks_exact(4) {
                rgb.extend_from_slice(&px[..3]);
                a.push(px[3]);
            }
            ("DeviceRGB", rgb, Some(a))
        }
        // Indexed data was already expanded by normalize_to_color8.
        png::ColorType::Indexed => return Err("failed to expand palette PNG".to_owned()),
    };

    Ok(ImageParts {
        width,
        height,
        color_space,
        filter: "FlateDecode",
        data: flate_compress(&samples)?,
        alpha,
    })
}

/// Compress with zlib for PDF FlateDecode.
pub fn flate_compress(data: &[u8]) -> Result<Vec<u8>, String> {
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    encoder
        .write_all(data)
        .and_then(|()| encoder.finish())
        .map_err(|e| format!("Flate compression failed: {e}"))
}

/// Add an Image XObject and optional SMask, returning its ObjectId.
pub fn add_image_xobject(doc: &mut Document, parts: ImageParts) -> Result<ObjectId, String> {
    let smask_id = match &parts.alpha {
        Some(alpha) => {
            let compressed = flate_compress(alpha)?;
            let dict = dictionary! {
                "Type" => "XObject",
                "Subtype" => "Image",
                "Width" => i64::from(parts.width),
                "Height" => i64::from(parts.height),
                "ColorSpace" => "DeviceGray",
                "BitsPerComponent" => 8,
                "Filter" => "FlateDecode",
            };
            Some(doc.add_object(Stream::new(dict, compressed).with_compression(false)))
        }
        None => None,
    };
    let mut dict = dictionary! {
        "Type" => "XObject",
        "Subtype" => "Image",
        "Width" => i64::from(parts.width),
        "Height" => i64::from(parts.height),
        "ColorSpace" => parts.color_space,
        "BitsPerComponent" => 8,
        "Filter" => parts.filter,
    };
    if let Some(id) = smask_id {
        dict.set("SMask", Object::Reference(id));
    }
    Ok(doc.add_object(Stream::new(dict, parts.data).with_compression(false)))
}

/// Map a top-left-origin, downward-y display point to unrotated PDF user space.
///
/// `crop` is the page CropBox; `rotation` is normalized to 0/90/180/270.
/// The mapping follows PDF's convention of displaying clockwise rotation.
pub(crate) fn display_to_pdf(crop: [f64; 4], rotation: i64, x: f64, y: f64) -> (f64, f64) {
    let [cx0, cy0, cx1, cy1] = crop;
    match rotation {
        90 => (cx0 + y, cy0 + x),
        180 => (cx1 - x, cy0 + y),
        270 => (cx1 - y, cy1 - x),
        _ => (cx0 + x, cy1 - y),
    }
}

/// Map an unrotated PDF user-space point to top-left-origin display space.
///
/// This is the inverse of `display_to_pdf`.
pub(crate) fn pdf_to_display(crop: [f64; 4], rotation: i64, x: f64, y: f64) -> (f64, f64) {
    let [cx0, cy0, cx1, cy1] = crop;
    match rotation {
        90 => (y - cy0, x - cx0),
        180 => (cx1 - x, y - cy0),
        270 => (cy1 - y, cx1 - x),
        _ => (x - cx0, cy1 - y),
    }
}

/// Map a PDF-space rectangle to normalized display `(x0, y0, x1, y1)`.
pub fn pdf_rect_to_display(crop: [f64; 4], rotation: i64, rect: [f64; 4]) -> [f64; 4] {
    let (ax, ay) = pdf_to_display(crop, rotation, rect[0], rect[1]);
    let (bx, by) = pdf_to_display(crop, rotation, rect[2], rect[3]);
    [ax.min(bx), ay.min(by), ax.max(bx), ay.max(by)]
}

/// Return display-rectangle corners in PDF space: TL, TR, BL, BR.
///
/// Used for Acrobat-compatible zigzag QuadPoints and bounding rectangles.
pub fn display_rect_quad_pdf(crop: [f64; 4], rotation: i64, rect: [f64; 4]) -> [(f64, f64); 4] {
    let [x0, y0, x1, y1] = rect;
    [
        display_to_pdf(crop, rotation, x0, y0),
        display_to_pdf(crop, rotation, x1, y0),
        display_to_pdf(crop, rotation, x0, y1),
        display_to_pdf(crop, rotation, x1, y1),
    ]
}

/// Return the normalized bounding rectangle of PDF-space points.
pub fn bounding_rect(points: &[(f64, f64)]) -> [f64; 4] {
    let mut out = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for &(x, y) in points {
        out[0] = out[0].min(x);
        out[1] = out[1].min(y);
        out[2] = out[2].max(x);
        out[3] = out[3].max(y);
    }
    out
}

/// Build drawing operators for a highlight annotation appearance stream (`AP /N`).
///
/// `quads` are TL, TR, BL, BR corners returned by `display_rect_quad_pdf`.
/// The caller registers `/PyloGS` with Multiply blending and opacity in Resources.
pub fn highlight_ap_ops(quads: &[[(f64, f64); 4]], color: (f64, f64, f64)) -> Vec<u8> {
    let mut out = format!(
        "/PyloGS gs\n{} {} {} rg\n",
        fmt(color.0),
        fmt(color.1),
        fmt(color.2)
    )
    .into_bytes();
    for quad in quads {
        let [ul, ur, ll, lr] = *quad;
        out.extend_from_slice(
            format!(
                "{} {} m\n{} {} l\n{} {} l\n{} {} l\nh\nf\n",
                fmt(ul.0),
                fmt(ul.1),
                fmt(ur.0),
                fmt(ur.1),
                fmt(lr.0),
                fmt(lr.1),
                fmt(ll.0),
                fmt(ll.1),
            )
            .as_bytes(),
        );
    }
    out
}

/// Content variants that require different `cm` matrix construction.
pub enum PlacedContent {
    /// Image XObject drawn in a unit square; aspect ratio is width/height.
    Image { width: u32, height: u32 },
    /// Form XObject in source-page coordinates, with BBox equal to source CropBox.
    Form { crop: [f64; 4], rotation: i64 },
}

impl PlacedContent {
    /// Aspect ratio in display space.
    fn display_aspect(&self) -> f64 {
        match self {
            Self::Image { width, height } => f64::from(*width) / f64::from(*height),
            Self::Form { crop, rotation } => {
                let (w, h) = (crop[2] - crop[0], crop[3] - crop[1]);
                if matches!(rotation, 90 | 270) {
                    h / w
                } else {
                    w / h
                }
            }
        }
    }
}

/// Compute `[a b c d e f]` placing content into display-space `rect`.
///
/// `rect` uses the target page's top-left-origin display coordinates.
/// `keep_proportion` preserves aspect ratio and centers content within `rect`.
pub fn placement_matrix(
    target_crop: [f64; 4],
    target_rotation: i64,
    rect: [f64; 4],
    content: &PlacedContent,
    keep_proportion: bool,
) -> [f64; 6] {
    let [mut x0, mut y0, mut x1, mut y1] = rect;
    if keep_proportion {
        let (rw, rh) = (x1 - x0, y1 - y0);
        let aspect = content.display_aspect();
        let (fit_w, fit_h) = if rw / rh > aspect {
            (rh * aspect, rh)
        } else {
            (rw, rw / aspect)
        };
        x0 += (rw - fit_w) / 2.0;
        y0 += (rh - fit_h) / 2.0;
        x1 = x0 + fit_w;
        y1 = y0 + fit_h;
    }

    // Target PDF corners: O=display bottom-left, U=right edge, V=up edge.
    let (ox, oy) = display_to_pdf(target_crop, target_rotation, x0, y1);
    let (ux, uy) = {
        let (px, py) = display_to_pdf(target_crop, target_rotation, x1, y1);
        (px - ox, py - oy)
    };
    let (vx, vy) = {
        let (px, py) = display_to_pdf(target_crop, target_rotation, x0, y0);
        (px - ox, py - oy)
    };

    match content {
        // Images use a unit square, so [U V O] is the matrix directly.
        PlacedContent::Image { .. } => [ux, uy, vx, vy, ox, oy],
        // Forms compose Q through source display coordinates, normalization,
        // and the target. Since (dx, dy) is affine in Q, the result is affine.
        PlacedContent::Form { crop, rotation } => {
            let [sx0, sy0, sx1, sy1] = *crop;
            let (sw, sh) = (sx1 - sx0, sy1 - sy0);
            let (sdw, sdh) = if matches!(rotation, 90 | 270) {
                (sh, sw)
            } else {
                (sw, sh)
            };
            // dx = ax*Qx + ay*Qy + a0, dy = bx*Qx + by*Qy + b0
            let (ax, ay, a0, bx, by, b0) = match rotation {
                90 => (0.0, 1.0, -sy0, 1.0, 0.0, -sx0),
                180 => (-1.0, 0.0, sx1, 0.0, 1.0, -sy0),
                270 => (0.0, -1.0, sy1, -1.0, 0.0, sx1),
                _ => (1.0, 0.0, -sx0, 0.0, -1.0, sy1),
            };
            // P(Q) = O + (dx/sdw)*U + (1 - dy/sdh)*V.
            let a = ux * ax / sdw - vx * bx / sdh;
            let b = uy * ax / sdw - vy * bx / sdh;
            let c = ux * ay / sdw - vx * by / sdh;
            let d = uy * ay / sdw - vy * by / sdh;
            let e = ox + ux * a0 / sdw + vx * (1.0 - b0 / sdh);
            let f = oy + uy * a0 / sdw + vy * (1.0 - b0 / sdh);
            [a, b, c, d, e, f]
        }
    }
}

/// Build drawing operators from a `cm` matrix and XObject name.
pub fn draw_ops(matrix: [f64; 6], name: &str) -> Vec<u8> {
    let [a, b, c, d, e, f] = matrix;
    format!(
        "q\n{} {} {} {} {} {} cm\n/{name} Do\nQ\n",
        fmt(a),
        fmt(b),
        fmt(c),
        fmt(d),
        fmt(e),
        fmt(f)
    )
    .into_bytes()
}

/// Format PDF content numbers with four decimals and no trailing zeros.
pub(crate) fn fmt(v: f64) -> String {
    let s = format!("{v:.4}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    if s.is_empty() || s == "-" {
        "0".to_owned()
    } else {
        s.to_owned()
    }
}

/// Register a font object reference in page Resources/Font.
///
/// Inherited attributes must already be materialized and Resources created.
/// Resources and Font may both be indirect, so resolve IDs before mutable borrows.
pub fn add_page_font(
    doc: &mut Document,
    page_id: ObjectId,
    name: &str,
    font_id: ObjectId,
) -> Result<(), lopdf::Error> {
    let res_ref = doc
        .get_object(page_id)?
        .as_dict()?
        .get(b"Resources")
        .ok()
        .and_then(|r| r.as_reference().ok());
    let font_ref = {
        let resources = match res_ref {
            Some(id) => doc.get_object(id)?.as_dict()?,
            None => doc
                .get_object(page_id)?
                .as_dict()?
                .get(b"Resources")?
                .as_dict()?,
        };
        resources
            .get(b"Font")
            .ok()
            .and_then(|f| f.as_reference().ok())
    };
    if let Some(fid) = font_ref {
        let fonts = doc.get_object_mut(fid)?.as_dict_mut()?;
        fonts.set(name, Object::Reference(font_id));
        return Ok(());
    }
    let resources = match res_ref {
        Some(id) => doc.get_object_mut(id)?.as_dict_mut()?,
        None => doc
            .get_object_mut(page_id)?
            .as_dict_mut()?
            .get_mut(b"Resources")?
            .as_dict_mut()?,
    };
    if !resources.has(b"Font") {
        resources.set("Font", Dictionary::new());
    }
    let fonts = resources.get_mut(b"Font")?.as_dict_mut()?;
    fonts.set(name, Object::Reference(font_id));
    Ok(())
}

/// Build text operators with display-space `point` as the baseline origin.
///
/// `lines` contains WinAnsi/cp1252 bytes, one item per line. `Tm` receives
/// display-space basis vectors so text remains upright on rotated pages.
/// Leading is 1.2 times the font size.
pub fn text_ops(
    crop: [f64; 4],
    rotation: i64,
    point: (f64, f64),
    lines: &[Vec<u8>],
    font: &str,
    size: f64,
    color: (f64, f64, f64),
) -> Vec<u8> {
    let (ox, oy) = display_to_pdf(crop, rotation, point.0, point.1);
    let (rx, ry) = {
        let p = display_to_pdf(crop, rotation, point.0 + 1.0, point.1);
        (p.0 - ox, p.1 - oy)
    };
    let (ux, uy) = {
        let p = display_to_pdf(crop, rotation, point.0, point.1 - 1.0);
        (p.0 - ox, p.1 - oy)
    };
    let mut out = format!(
        "q\nBT\n/{font} {} Tf\n{} {} {} rg\n{} {} {} {} {} {} Tm\n{} TL\n",
        fmt(size),
        fmt(color.0),
        fmt(color.1),
        fmt(color.2),
        fmt(rx),
        fmt(ry),
        fmt(ux),
        fmt(uy),
        fmt(ox),
        fmt(oy),
        fmt(size * 1.2),
    )
    .into_bytes();
    for (i, line) in lines.iter().enumerate() {
        if i > 0 {
            out.extend_from_slice(b"T*\n");
        }
        out.push(b'(');
        for &b in line {
            match b {
                b'(' | b')' | b'\\' => {
                    out.push(b'\\');
                    out.push(b);
                }
                0x20..=0x7E => out.push(b),
                _ => out.extend_from_slice(format!("\\{b:03o}").as_bytes()),
            }
        }
        out.extend_from_slice(b") Tj\n");
    }
    out.extend_from_slice(b"ET\nQ\n");
    out
}

/// Wrap existing `/Contents` in q/Q streams once to isolate graphics state.
///
/// Do nothing when already wrapped by standalone `b"q\n"` and `b"Q\n"` streams.
fn ensure_contents_wrapped(doc: &mut Document, page_id: ObjectId) -> Result<(), lopdf::Error> {
    let contents = doc.get_page_contents(page_id);
    if contents.is_empty() {
        return Ok(());
    }
    let stream_bytes = |doc: &Document, id: ObjectId| -> Option<Vec<u8>> {
        doc.get_object(id)
            .ok()
            .and_then(|o| o.as_stream().ok())
            .map(|s| s.content.clone())
    };
    let first = contents.first().and_then(|&id| stream_bytes(doc, id));
    let last = contents.last().and_then(|&id| stream_bytes(doc, id));
    if contents.len() >= 2 && first.as_deref() == Some(b"q\n") && last.as_deref() == Some(b"Q\n") {
        return Ok(());
    }
    let q_id =
        doc.add_object(Stream::new(Dictionary::new(), b"q\n".to_vec()).with_compression(false));
    let push_q_id =
        doc.add_object(Stream::new(Dictionary::new(), b"Q\n".to_vec()).with_compression(false));
    let mut list: Vec<Object> = vec![Object::Reference(q_id)];
    list.extend(contents.into_iter().map(Object::Reference));
    list.push(Object::Reference(push_q_id));
    let page = doc.get_object_mut(page_id).and_then(Object::as_dict_mut)?;
    page.set("Contents", list);
    Ok(())
}

/// Add drawing operators to the page as a new content stream.
///
/// Overlay appends above existing content; otherwise prepend below it.
/// Existing content is only wrapped and never modified internally.
pub fn push_content(
    doc: &mut Document,
    page_id: ObjectId,
    ops: Vec<u8>,
    overlay: bool,
) -> Result<(), lopdf::Error> {
    ensure_contents_wrapped(doc, page_id)?;
    let new_id = doc.add_object(Stream::new(Dictionary::new(), ops).with_compression(false));
    let mut list: Vec<Object> = doc
        .get_page_contents(page_id)
        .into_iter()
        .map(Object::Reference)
        .collect();
    if overlay {
        list.push(Object::Reference(new_id));
    } else {
        list.insert(0, Object::Reference(new_id));
    }
    let page = doc.get_object_mut(page_id).and_then(Object::as_dict_mut)?;
    page.set("Contents", list);
    Ok(())
}
