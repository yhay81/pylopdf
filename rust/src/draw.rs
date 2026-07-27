//! Drawing primitives for image insertion and overlaying pages from another PDF.
//!
//! Existing content streams are never decoded or re-encoded because a round trip
//! through lopdf's content parser can trigger #535-class edge cases. Drawing only
//! appends streams to `/Contents`. Existing graphics state is isolated by wrapping
//! the original sequence in q/Q streams once.

use std::collections::HashSet;
use std::io::{self, Write};

use lopdf::{Dictionary, Document, Object, ObjectId, Stream, dictionary};
use pdf_base14_metrics::Base14Font;

use crate::layout::{TextBoxLayout, layout_textbox};

const MAX_PAGE_CONTENT_STREAMS: usize = 4096;
const MAX_CONTENT_REFERENCE_DEPTH: usize = 32;

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

/// Return the PNG IHDR pixel count without allocating decoded storage.
pub fn png_pixel_count(data: &[u8]) -> Option<u64> {
    if data.len() < 24 || !data.starts_with(b"\x89PNG\r\n\x1a\n") || &data[12..16] != b"IHDR" {
        return None;
    }
    let width = u32::from_be_bytes(data[16..20].try_into().ok()?);
    let height = u32::from_be_bytes(data[20..24].try_into().ok()?);
    Some(u64::from(width) * u64::from(height))
}

/// Detect image format from magic bytes and convert it to XObject data.
///
/// JPEG passes through with DCTDecode; PNG is decoded and Flate-compressed.
/// Return None for unsupported formats so the caller can report an error.
pub fn parse_image(data: Vec<u8>) -> Result<Option<ImageParts>, String> {
    if data.starts_with(&[0xFF, 0xD8, 0xFF]) {
        return jpeg_parts(data).map(Some);
    }
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return png_parts(&data).map(Some);
    }
    Ok(None)
}

fn reserve_bytes(capacity: usize, label: &str) -> Result<Vec<u8>, String> {
    let mut data = Vec::new();
    data.try_reserve_exact(capacity)
        .map_err(|error| format!("failed to allocate {label}: {error}"))?;
    Ok(data)
}

fn zeroed_bytes(length: usize, label: &str) -> Result<Vec<u8>, String> {
    let mut data = reserve_bytes(length, label)?;
    data.resize(length, 0);
    Ok(data)
}

/// Convert straight-alpha RGBA8 pixels directly to a PDF Image XObject.
pub fn rgba_parts(width: u32, height: u32, data: &[u8]) -> Result<ImageParts, String> {
    if width == 0 || height == 0 {
        return Err("Pixmap dimensions are zero".to_owned());
    }
    let pixel_count = usize::try_from(u64::from(width) * u64::from(height))
        .map_err(|_| "Pixmap dimensions are too large".to_owned())?;
    let expected_len = pixel_count
        .checked_mul(4)
        .ok_or_else(|| "Pixmap dimensions are too large".to_owned())?;
    if data.len() != expected_len {
        return Err("Pixmap RGBA buffer length does not match its dimensions".to_owned());
    }

    let rgb_len = pixel_count
        .checked_mul(3)
        .ok_or_else(|| "Pixmap dimensions are too large".to_owned())?;
    let opaque = data.chunks_exact(4).all(|pixel| pixel[3] == u8::MAX);
    let mut rgb = reserve_bytes(rgb_len, "Pixmap RGB plane")?;
    let mut alpha = if opaque {
        None
    } else {
        Some(reserve_bytes(pixel_count, "Pixmap alpha plane")?)
    };
    for pixel in data.chunks_exact(4) {
        rgb.extend_from_slice(&pixel[..3]);
        if let Some(alpha) = &mut alpha {
            alpha.push(pixel[3]);
        }
    }

    Ok(ImageParts {
        width,
        height,
        color_space: "DeviceRGB",
        filter: "FlateDecode",
        data: flate_compress(&rgb)?,
        alpha,
    })
}

/// Read dimensions/components from a JPEG SOF marker and pass bytes through.
fn jpeg_parts(data: Vec<u8>) -> Result<ImageParts, String> {
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
                data,
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
    let mut buf = zeroed_bytes(buf_size, "decoded PNG buffer")?;
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
            let pixel_count = buf.len() / 2;
            let mut alpha = reserve_bytes(pixel_count, "PNG alpha plane")?;
            for index in 0..pixel_count {
                let source = index * 2;
                buf[index] = buf[source];
                alpha.push(buf[source + 1]);
            }
            buf.truncate(pixel_count);
            ("DeviceGray", buf, Some(alpha))
        }
        png::ColorType::Rgba => {
            let pixel_count = buf.len() / 4;
            let mut alpha = reserve_bytes(pixel_count, "PNG alpha plane")?;
            for index in 0..pixel_count {
                let source = index * 4;
                let target = index * 3;
                let red = buf[source];
                let green = buf[source + 1];
                let blue = buf[source + 2];
                let alpha_value = buf[source + 3];
                buf[target] = red;
                buf[target + 1] = green;
                buf[target + 2] = blue;
                alpha.push(alpha_value);
            }
            buf.truncate(pixel_count * 3);
            ("DeviceRGB", buf, Some(alpha))
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

#[derive(Default)]
struct FallibleVecOutput {
    bytes: Vec<u8>,
}

impl Write for FallibleVecOutput {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        self.bytes.try_reserve(data.len()).map_err(|error| {
            io::Error::other(format!(
                "failed to allocate compressed image output: {error}"
            ))
        })?;
        self.bytes.extend_from_slice(data);
        Ok(data.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Compress with zlib for PDF FlateDecode.
pub fn flate_compress(data: &[u8]) -> Result<Vec<u8>, String> {
    let mut encoder = flate2::write::ZlibEncoder::new(
        FallibleVecOutput::default(),
        flate2::Compression::default(),
    );
    encoder
        .write_all(data)
        .and_then(|()| encoder.finish())
        .map(|output| output.bytes)
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

/// A text-markup annotation appearance synthesized from `QuadPoints`.
#[derive(Clone, Copy)]
pub enum TextMarkupKind {
    Highlight,
    Underline,
    Squiggly,
    StrikeOut,
}

impl TextMarkupKind {
    /// PDF blend mode used by the synthesized appearance.
    pub fn blend_mode(self) -> &'static str {
        match self {
            Self::Highlight => "Multiply",
            Self::Underline | Self::Squiggly | Self::StrikeOut => "Normal",
        }
    }
}

fn midpoint(a: (f64, f64), b: (f64, f64)) -> (f64, f64) {
    ((a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0)
}

fn interpolate(a: (f64, f64), b: (f64, f64), t: f64) -> (f64, f64) {
    (a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t)
}

fn inward_normal(quad: [(f64, f64); 4]) -> ((f64, f64), f64) {
    let [ul, ur, ll, lr] = quad;
    let top = midpoint(ul, ur);
    let bottom = midpoint(ll, lr);
    let dx = top.0 - bottom.0;
    let dy = top.1 - bottom.1;
    let length = dx.hypot(dy);
    ((dx / length, dy / length), length)
}

/// Build bounded drawing operators for a text-markup appearance stream.
///
/// `quads` use TL, TR, BL, BR order. `segment_counts` is preflighted by the
/// caller and controls only Squiggly path amplification.
pub fn text_markup_ap_ops(
    kind: TextMarkupKind,
    quads: &[[(f64, f64); 4]],
    color: (f64, f64, f64),
    segment_counts: &[usize],
) -> Vec<u8> {
    let paint = if matches!(kind, TextMarkupKind::Highlight) {
        "rg"
    } else {
        "RG"
    };
    let mut out = format!(
        "/PyloGS gs\n{} {} {} {paint}\n",
        fmt(color.0),
        fmt(color.1),
        fmt(color.2),
    )
    .into_bytes();
    if !matches!(kind, TextMarkupKind::Highlight) {
        out.extend_from_slice(b"1 w\n");
    }

    for (index, quad) in quads.iter().enumerate() {
        let [ul, ur, ll, lr] = *quad;
        match kind {
            TextMarkupKind::Highlight => {
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
            TextMarkupKind::Underline => {
                let (normal, cross_length) = inward_normal(*quad);
                let inset = cross_length.min(1.0);
                let start = (ll.0 + normal.0 * inset, ll.1 + normal.1 * inset);
                let end = (lr.0 + normal.0 * inset, lr.1 + normal.1 * inset);
                out.extend_from_slice(
                    format!(
                        "{} {} m\n{} {} l\nS\n",
                        fmt(start.0),
                        fmt(start.1),
                        fmt(end.0),
                        fmt(end.1),
                    )
                    .as_bytes(),
                );
            }
            TextMarkupKind::StrikeOut => {
                let start = midpoint(ul, ll);
                let end = midpoint(ur, lr);
                out.extend_from_slice(
                    format!(
                        "{} {} m\n{} {} l\nS\n",
                        fmt(start.0),
                        fmt(start.1),
                        fmt(end.0),
                        fmt(end.1),
                    )
                    .as_bytes(),
                );
            }
            TextMarkupKind::Squiggly => {
                let segments = segment_counts[index];
                let (normal, cross_length) = inward_normal(*quad);
                let inset = cross_length.min(0.5);
                let amplitude = (cross_length - inset).clamp(0.0, 2.0);
                for point_index in 0..=segments {
                    let t = point_index as f64 / segments as f64;
                    let base = interpolate(ll, lr, t);
                    let offset = inset + if point_index % 2 == 0 { amplitude } else { 0.0 };
                    let point = (base.0 + normal.0 * offset, base.1 + normal.1 * offset);
                    let operator = if point_index == 0 { "m" } else { "l" };
                    out.extend_from_slice(
                        format!("{} {} {operator}\n", fmt(point.0), fmt(point.1)).as_bytes(),
                    );
                }
                out.extend_from_slice(b"S\n");
            }
        }
    }
    out
}

/// Build drawing operators for a pylopdf-created Highlight appearance.
pub fn highlight_ap_ops(quads: &[[(f64, f64); 4]], color: (f64, f64, f64)) -> Vec<u8> {
    text_markup_ap_ops(TextMarkupKind::Highlight, quads, color, &[])
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

/// Place an image into a display-space rectangle with clockwise right-angle rotation.
pub fn image_placement_matrix(
    target_crop: [f64; 4],
    target_rotation: i64,
    rect: [f64; 4],
    width: u32,
    height: u32,
    keep_proportion: bool,
    image_rotation: i64,
) -> [f64; 6] {
    let content = if matches!(image_rotation, 90 | 270) {
        PlacedContent::Image {
            width: height,
            height: width,
        }
    } else {
        PlacedContent::Image { width, height }
    };
    let [ux, uy, vx, vy, ox, oy] = placement_matrix(
        target_crop,
        target_rotation,
        rect,
        &content,
        keep_proportion,
    );
    match image_rotation {
        // Rotate clockwise in display space. U points right and V points up.
        90 => [-vx, -vy, ux, uy, ox + vx, oy + vy],
        180 => [-ux, -uy, -vx, -vy, ox + ux + vx, oy + uy + vy],
        270 => [vx, vy, -ux, -uy, ox + ux, oy + uy],
        _ => [ux, uy, vx, vy, ox, oy],
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

/// Lay out Standard 14 text using Adobe's canonical AFM metrics.
pub fn standard_textbox_layout(
    text: &str,
    box_size: (f64, f64),
    base_font: &str,
    font_size: f64,
    line_height: f64,
    justify: bool,
    max_lines: Option<usize>,
) -> Result<TextBoxLayout, String> {
    let font = base14_font(base_font)
        .ok_or_else(|| format!("unsupported Standard 14 font: {base_font}"))?;
    let metrics = font.metrics();
    // FontBBox is intentionally conservative: Core 14 viewers substitute
    // different outlines, while the AFM ascender only covers typical letters.
    let mut ascent = f64::from(metrics.font_bbox.ury.max(0.0)) / 1000.0;
    let mut descent = f64::from((-metrics.font_bbox.lly).max(0.0)) / 1000.0;
    if ascent + descent <= 0.0 {
        ascent = f64::from(metrics.ascender.max(0.0)) / 1000.0;
        descent = f64::from((-metrics.descender).max(0.0)) / 1000.0;
    }
    if ascent + descent <= 0.0 {
        (ascent, descent) = (0.8, 0.2);
    }

    layout_textbox(
        text,
        box_size,
        font_size,
        line_height,
        ascent,
        descent,
        justify,
        max_lines,
        |line| standard_text_width(line, font, font_size),
    )
}

/// Build individually positioned Standard 14 textbox lines.
#[allow(clippy::too_many_arguments)]
pub fn textbox_text_ops(
    crop: [f64; 4],
    rotation: i64,
    rect: [f64; 4],
    layout: &TextBoxLayout,
    align: u8,
    font: &str,
    size: f64,
    color: (f64, f64, f64),
) -> Result<Vec<u8>, String> {
    let mut out = format!(
        "q\nBT\n/{font} {} Tf\n{} {} {} rg\n",
        fmt(size),
        fmt(color.0),
        fmt(color.1),
        fmt(color.2),
    )
    .into_bytes();
    let box_width = rect[2] - rect[0];
    for (line_number, line) in layout.lines.iter().enumerate() {
        let x_offset = match align {
            1 => (box_width - line.width) / 2.0,
            2 => box_width - line.width,
            _ => 0.0,
        };
        let baseline = (
            rect[0] + x_offset.max(0.0),
            rect[1] + layout.ascent * size + line_number as f64 * layout.leading,
        );
        let (ox, oy) = display_to_pdf(crop, rotation, baseline.0, baseline.1);
        let (rx, ry) = {
            let p = display_to_pdf(crop, rotation, baseline.0 + 1.0, baseline.1);
            (p.0 - ox, p.1 - oy)
        };
        let (ux, uy) = {
            let p = display_to_pdf(crop, rotation, baseline.0, baseline.1 - 1.0);
            (p.0 - ox, p.1 - oy)
        };
        let space_count = line.text.bytes().filter(|&byte| byte == b' ').count();
        let word_space = if line.justify && space_count > 0 {
            (box_width - line.width).max(0.0) / space_count as f64
        } else {
            0.0
        };
        out.extend_from_slice(
            format!(
                "{} Tw\n{} {} {} {} {} {} Tm\n",
                fmt(word_space),
                fmt(rx),
                fmt(ry),
                fmt(ux),
                fmt(uy),
                fmt(ox),
                fmt(oy),
            )
            .as_bytes(),
        );
        let encoded = encode_cp1252(&line.text)?;
        append_pdf_string(&mut out, &encoded);
        out.extend_from_slice(b" Tj\n");
    }
    out.extend_from_slice(b"ET\nQ\n");
    Ok(out)
}

fn standard_text_width(text: &str, font: Base14Font, font_size: f64) -> Result<f64, String> {
    let encoded = encode_cp1252(text)?;
    let metrics = font.metrics();
    let symbolic = matches!(font, Base14Font::Symbol | Base14Font::ZapfDingbats);
    let units = encoded
        .into_iter()
        .map(|byte| {
            if symbolic {
                metrics
                    .character_metrics
                    .iter()
                    .find(|metric| metric.code == i32::from(byte))
                    .map(|metric| metric.width_x)
            } else {
                font.winansi_width(byte)
            }
            .unwrap_or(250.0)
        })
        .map(f64::from)
        .sum::<f64>();
    Ok(units * font_size / 1000.0)
}

fn base14_font(name: &str) -> Option<Base14Font> {
    Some(match name {
        "Helvetica" => Base14Font::Helvetica,
        "Helvetica-Bold" => Base14Font::HelveticaBold,
        "Helvetica-Oblique" => Base14Font::HelveticaOblique,
        "Helvetica-BoldOblique" => Base14Font::HelveticaBoldOblique,
        "Times-Roman" => Base14Font::TimesRoman,
        "Times-Bold" => Base14Font::TimesBold,
        "Times-Italic" => Base14Font::TimesItalic,
        "Times-BoldItalic" => Base14Font::TimesBoldItalic,
        "Courier" => Base14Font::Courier,
        "Courier-Bold" => Base14Font::CourierBold,
        "Courier-Oblique" => Base14Font::CourierOblique,
        "Courier-BoldOblique" => Base14Font::CourierBoldOblique,
        "Symbol" => Base14Font::Symbol,
        "ZapfDingbats" => Base14Font::ZapfDingbats,
        _ => return None,
    })
}

fn encode_cp1252(text: &str) -> Result<Vec<u8>, String> {
    text.chars()
        .map(|character| {
            let code = character as u32;
            match code {
                0x00..=0x7f | 0xa0..=0xff => Ok(code as u8),
                0x20ac => Ok(0x80),
                0x201a => Ok(0x82),
                0x0192 => Ok(0x83),
                0x201e => Ok(0x84),
                0x2026 => Ok(0x85),
                0x2020 => Ok(0x86),
                0x2021 => Ok(0x87),
                0x02c6 => Ok(0x88),
                0x2030 => Ok(0x89),
                0x0160 => Ok(0x8a),
                0x2039 => Ok(0x8b),
                0x0152 => Ok(0x8c),
                0x017d => Ok(0x8e),
                0x2018 => Ok(0x91),
                0x2019 => Ok(0x92),
                0x201c => Ok(0x93),
                0x201d => Ok(0x94),
                0x2022 => Ok(0x95),
                0x2013 => Ok(0x96),
                0x2014 => Ok(0x97),
                0x02dc => Ok(0x98),
                0x2122 => Ok(0x99),
                0x0161 => Ok(0x9a),
                0x203a => Ok(0x9b),
                0x0153 => Ok(0x9c),
                0x017e => Ok(0x9e),
                0x0178 => Ok(0x9f),
                _ => Err("Standard 14 text contains a character outside WinAnsi".to_owned()),
            }
        })
        .collect()
}

/// Return whether every character has a WinAnsi encoding.
pub fn is_winansi(text: &str) -> bool {
    encode_cp1252(text).is_ok()
}

fn append_pdf_string(out: &mut Vec<u8>, text: &[u8]) {
    out.push(b'(');
    for &byte in text {
        match byte {
            b'(' | b')' | b'\\' => {
                out.push(b'\\');
                out.push(byte);
            }
            0x20..=0x7e => out.push(byte),
            _ => out.extend_from_slice(format!("\\{byte:03o}").as_bytes()),
        }
    }
    out.push(b')');
}

/// Wrap existing `/Contents` in q/Q streams once to isolate graphics state.
///
/// Do nothing when already wrapped by standalone `b"q\n"` and `b"Q\n"` streams.
fn content_is_exact_stream(doc: &Document, id: ObjectId, expected: &[u8]) -> bool {
    doc.get_object(id)
        .ok()
        .and_then(|object| object.as_stream().ok())
        .is_some_and(|stream| stream.content == expected)
}

fn contents_have_outer_wrapper(doc: &Document, streams: &[ObjectId]) -> bool {
    streams.len() >= 2
        && streams
            .first()
            .is_some_and(|&id| content_is_exact_stream(doc, id, b"q\n"))
        && streams
            .last()
            .is_some_and(|&id| content_is_exact_stream(doc, id, b"Q\n"))
}

/// Inspect a page's bounded raw content-stream shape.
///
/// This checks arrays before lopdf materializes their reference list and also
/// rejects cyclic or excessively deep indirect `/Contents` chains.
pub fn inspect_page_contents(doc: &Document, page_id: ObjectId) -> Result<Vec<ObjectId>, String> {
    let page = doc
        .get_dictionary(page_id)
        .map_err(|error| format!("cannot inspect page Contents: {error}"))?;
    if let Ok(mut contents) = page.get(b"Contents") {
        let mut visited = HashSet::new();
        let mut depth = 0usize;
        loop {
            match contents {
                Object::Reference(id) => {
                    if !visited.insert(*id) {
                        return Err("page Contents contains a reference cycle".to_owned());
                    }
                    depth += 1;
                    if depth > MAX_CONTENT_REFERENCE_DEPTH {
                        return Err(format!(
                            "page Contents exceeds the {MAX_CONTENT_REFERENCE_DEPTH}-reference-depth safety limit"
                        ));
                    }
                    let Some(object) = doc.objects.get(id) else {
                        break;
                    };
                    if matches!(object, Object::Stream(_)) {
                        break;
                    }
                    contents = object;
                }
                Object::Array(entries) => {
                    if entries.len() > MAX_PAGE_CONTENT_STREAMS {
                        return Err(format!(
                            "page Contents exceeds the {MAX_PAGE_CONTENT_STREAMS}-entry safety limit"
                        ));
                    }
                    break;
                }
                _ => break,
            }
        }
    }

    let streams = doc.get_page_contents(page_id);
    if streams.len() > MAX_PAGE_CONTENT_STREAMS {
        return Err(format!(
            "page Contents exceeds the {MAX_PAGE_CONTENT_STREAMS}-stream safety limit"
        ));
    }
    Ok(streams)
}

/// Reject a page whose content shape would amplify one drawing insertion.
///
/// The final count includes q/Q isolation streams when the existing contents
/// have not already been wrapped.
pub fn preflight_push_content(
    doc: &Document,
    page_id: ObjectId,
    already_isolated: bool,
) -> Result<(), String> {
    let streams = inspect_page_contents(doc, page_id)?;
    let already_wrapped = already_isolated || contents_have_outer_wrapper(doc, &streams);
    let additions = if streams.is_empty() || already_wrapped {
        1
    } else {
        3
    };
    if streams
        .len()
        .checked_add(additions)
        .is_none_or(|final_count| final_count > MAX_PAGE_CONTENT_STREAMS)
    {
        return Err(format!(
            "drawing would exceed the {MAX_PAGE_CONTENT_STREAMS}-stream page Contents safety limit"
        ));
    }
    Ok(())
}

fn ensure_contents_wrapped(
    doc: &mut Document,
    page_id: ObjectId,
    already_isolated: bool,
) -> Result<bool, lopdf::Error> {
    let contents = doc.get_page_contents(page_id);
    if contents.is_empty() {
        return Ok(false);
    }
    if already_isolated || contents_have_outer_wrapper(doc, &contents) {
        return Ok(true);
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
    Ok(true)
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
    already_isolated: bool,
) -> Result<bool, lopdf::Error> {
    preflight_push_content(doc, page_id, already_isolated).map_err(lopdf::Error::InvalidStream)?;
    let is_isolated = ensure_contents_wrapped(doc, page_id, already_isolated)?;
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
    Ok(is_isolated)
}
