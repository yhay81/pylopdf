//! Drawing primitives for image insertion and overlaying pages from another PDF.
//!
//! Existing content streams are never modified or re-encoded, because a round
//! trip through lopdf's content parser can trigger #535-class edge cases.
//! Drawing only appends streams to `/Contents`. Existing graphics state is
//! isolated by wrapping the original sequence once in streams holding as many
//! `q` and `Q` as hayro's content parser finds it to need.

use std::borrow::Cow;
use std::io::{self, Write};
use std::panic::catch_unwind;

use hayro::hayro_syntax::content::TypedIter;
use hayro::hayro_syntax::content::ops::TypedInstruction;
use lopdf::{Dictionary, Document, Object, ObjectId, Stream, dictionary};
use pdf_base14_metrics::Base14Font;

use crate::layout::{LineMetrics, LinePlacement, TextBoxLayout, layout_textbox};

pub(crate) const MAX_PAGE_CONTENT_STREAMS: usize = 4096;
const MAX_CONTENT_REFERENCE_DEPTH: usize = 32;

/// Destination for new PDF objects.
///
/// `_Document` adds them through its page index, which survives while new
/// object IDs are unused; a plain lopdf document adds them directly.
pub trait ObjectSink {
    fn add_object<T: Into<Object>>(&mut self, object: T) -> ObjectId;
}

impl ObjectSink for Document {
    fn add_object<T: Into<Object>>(&mut self, object: T) -> ObjectId {
        Document::add_object(self, object)
    }
}

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

/// Convert gray or RGB pixels, with or without straight alpha, directly to a
/// PDF Image XObject in DeviceGray or DeviceRGB.
pub fn pixmap_parts(
    width: u32,
    height: u32,
    layout: crate::pixmap::Layout,
    data: &[u8],
) -> Result<ImageParts, String> {
    if width == 0 || height == 0 {
        return Err("Pixmap dimensions are zero".to_owned());
    }
    let channels = layout.channels() as usize;
    let color_channels = channels - usize::from(layout.alpha);
    let color_space = if layout.gray {
        "DeviceGray"
    } else {
        "DeviceRGB"
    };
    let pixel_count = usize::try_from(u64::from(width) * u64::from(height))
        .map_err(|_| "Pixmap dimensions are too large".to_owned())?;
    let expected_len = pixel_count
        .checked_mul(channels)
        .ok_or_else(|| "Pixmap dimensions are too large".to_owned())?;
    if data.len() != expected_len {
        return Err("Pixmap buffer length does not match its dimensions".to_owned());
    }
    if !layout.alpha {
        // Color samples are already the XObject's plane, and opaque.
        return Ok(ImageParts {
            width,
            height,
            color_space,
            filter: "FlateDecode",
            data: flate_compress(data)?,
            alpha: None,
        });
    }

    let color_len = pixel_count
        .checked_mul(color_channels)
        .ok_or_else(|| "Pixmap dimensions are too large".to_owned())?;
    let opaque = data
        .chunks_exact(channels)
        .all(|pixel| pixel[color_channels] == u8::MAX);
    let mut color = reserve_bytes(color_len, "Pixmap color plane")?;
    let mut alpha = if opaque {
        None
    } else {
        Some(reserve_bytes(pixel_count, "Pixmap alpha plane")?)
    };
    for pixel in data.chunks_exact(channels) {
        color.extend_from_slice(&pixel[..color_channels]);
        if let Some(alpha) = &mut alpha {
            alpha.push(pixel[color_channels]);
        }
    }

    Ok(ImageParts {
        width,
        height,
        color_space,
        filter: "FlateDecode",
        data: flate_compress(&color)?,
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

/// An Image XObject and its optional SMask, prepared before a document changes.
pub struct ImageStreams {
    image: Stream,
    smask: Option<Stream>,
}

/// Prepare the streams of an Image XObject, compressing its alpha.
pub fn image_streams(parts: ImageParts) -> Result<ImageStreams, String> {
    let smask = match &parts.alpha {
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
            Some(Stream::new(dict, compressed).with_compression(false))
        }
        None => None,
    };
    let dict = dictionary! {
        "Type" => "XObject",
        "Subtype" => "Image",
        "Width" => i64::from(parts.width),
        "Height" => i64::from(parts.height),
        "ColorSpace" => parts.color_space,
        "BitsPerComponent" => 8,
        "Filter" => parts.filter,
    };
    Ok(ImageStreams {
        image: Stream::new(dict, parts.data).with_compression(false),
        smask,
    })
}

/// Add prepared image streams, returning the Image XObject's ObjectId.
pub fn add_image_streams(doc: &mut impl ObjectSink, streams: ImageStreams) -> ObjectId {
    let mut image = streams.image;
    if let Some(smask) = streams.smask {
        let id = doc.add_object(smask);
        image.dict.set("SMask", Object::Reference(id));
    }
    doc.add_object(image)
}

/// Width of hayro-syntax's default A4 page in points.
pub(crate) const A4_WIDTH: f64 = 210.0 * 72.0 / 25.4;
/// Height of hayro-syntax's default A4 page in points.
pub(crate) const A4_HEIGHT: f64 = 297.0 * 72.0 / 25.4;

/// Return the user-space box that rendering and extraction display.
///
/// `media` and `crop` are a page's inherited MediaBox and CropBox, absent
/// when the page has none. This follows hayro-syntax 0.8, whose
/// `Page::initial_transform` maps this box to display space: the CropBox,
/// or the MediaBox without one, clipped to the MediaBox, or to an A4
/// MediaBox at the origin without one. A clipped box without area keeps the
/// corner where the clipping left it and takes the size of A4, and a side
/// shorter than one point grows to one point.
pub(crate) fn display_box(media: Option<[f64; 4]>, crop: Option<[f64; 4]>) -> [f64; 4] {
    let normalized = |[x0, y0, x1, y1]: [f64; 4]| [x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)];
    let media = media.map_or([0.0, 0.0, A4_WIDTH, A4_HEIGHT], normalized);
    let crop = crop.map_or(media, normalized);
    let x0 = crop[0].max(media[0]);
    let y0 = crop[1].max(media[1]);
    let x1 = crop[2].min(media[2]).max(x0);
    let y1 = crop[3].min(media[3]).max(y0);
    // hayro compares the extents in f32 with its 2^-12 tolerance.
    let nearly_zero = |extent: f64| (extent as f32).abs() <= 1.0 / 4096.0;
    if nearly_zero(x1 - x0) || nearly_zero(y1 - y0) {
        return [x0, y0, x0 + A4_WIDTH, y0 + A4_HEIGHT];
    }
    [x0, y0, x1.max(x0 + 1.0), y1.max(y0 + 1.0)]
}

/// Map a top-left-origin, downward-y display point to unrotated PDF user space.
///
/// `crop` is the page's `display_box`; `rotation` is normalized to
/// 0/90/180/270. The mapping follows PDF's convention of displaying clockwise
/// rotation.
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

/// Return the display width and height of a page with `crop` and `rotation`.
pub(crate) fn display_size(crop: [f64; 4], rotation: i64) -> (f64, f64) {
    let (width, height) = (crop[2] - crop[0], crop[3] - crop[1]);
    if matches!(rotation, 90 | 270) {
        (height, width)
    } else {
        (width, height)
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
    bounding_rect_iter(points.iter().copied())
}

/// Return the normalized bounding rectangle of an owned point iterator.
pub fn bounding_rect_iter(points: impl IntoIterator<Item = (f64, f64)>) -> [f64; 4] {
    let mut out = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for (x, y) in points {
        out[0] = out[0].min(x);
        out[1] = out[1].min(y);
        out[2] = out[2].max(x);
        out[3] = out[3].max(y);
    }
    out
}

/// Content variants that require different `cm` matrix construction.
pub enum PlacedContent {
    /// Image XObject drawn in a unit square; aspect ratio is width/height.
    Image { width: u32, height: u32 },
    /// Form XObject in source-page coordinates, with BBox equal to the source
    /// page's `display_box`.
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

/// A device colour with channels from 0 to 1: gray or RGB.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum DeviceColor {
    Gray(f64),
    Rgb(f64, f64, f64),
}

impl DeviceColor {
    /// Return the operator that selects this colour for filling, such as
    /// `0.2 0.4 0.6 rg`.
    pub(crate) fn fill_operator(self) -> String {
        match self {
            Self::Gray(gray) => format!("{} g", fmt(gray)),
            Self::Rgb(red, green, blue) => {
                format!("{} {} {} rg", fmt(red), fmt(green), fmt(blue))
            }
        }
    }
}

/// Build text operators with display-space `point` as the baseline origin.
///
/// `lines` contains WinAnsi/cp1252 bytes, one item per line, and `leading`
/// is the distance between their baselines. `Tm` receives display-space basis
/// vectors so text remains upright on rotated pages.
#[allow(clippy::too_many_arguments)]
pub fn text_ops(
    crop: [f64; 4],
    rotation: i64,
    point: (f64, f64),
    lines: &[Vec<u8>],
    font: &str,
    size: f64,
    leading: f64,
    color: DeviceColor,
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
        "q\nBT\n/{font} {} Tf\n{}\n{} {} {} {} {} {} Tm\n{} TL\n",
        fmt(size),
        color.fill_operator(),
        fmt(rx),
        fmt(ry),
        fmt(ux),
        fmt(uy),
        fmt(ox),
        fmt(oy),
        fmt(leading),
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

/// Lay out Standard 14 text in a textbox: wrapped with Adobe's canonical AFM
/// widths, with lines placed by `placement`.
pub fn standard_textbox_layout(
    text: &str,
    box_size: (f64, f64),
    base_font: &str,
    font_size: f64,
    placement: LinePlacement,
    justify: bool,
    max_lines: Option<usize>,
) -> Result<TextBoxLayout, String> {
    let font = base14_font(base_font)
        .ok_or_else(|| format!("unsupported Standard 14 font: {base_font}"))?;
    layout_textbox(
        text,
        box_size,
        font_size,
        placement,
        justify,
        max_lines,
        |line| standard_text_width(line, font, font_size),
    )
}

/// Lay out Standard 14 text for a form appearance, `line_height` em apart and
/// fitted with the AFM font bounding box.
pub fn standard_form_layout(
    text: &str,
    box_size: (f64, f64),
    font: Base14Font,
    font_size: f64,
    line_height: f64,
    max_lines: Option<usize>,
) -> Result<TextBoxLayout, String> {
    let (ascent, descent) = standard_vertical_metrics(font);
    layout_textbox(
        text,
        box_size,
        font_size,
        LinePlacement {
            ascent,
            descent,
            line_height,
        },
        false,
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
    color: DeviceColor,
) -> Result<Vec<u8>, String> {
    let mut out = format!(
        "q\nBT\n/{font} {} Tf\n{}\n",
        fmt(size),
        color.fill_operator(),
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
        let positioning = format!(
            "{} Tw\n{} {} {} {} {} {} Tm\n",
            fmt(word_space),
            fmt(rx),
            fmt(ry),
            fmt(ux),
            fmt(uy),
            fmt(ox),
            fmt(oy),
        );
        out.try_reserve(positioning.len())
            .map_err(|error| format!("failed to grow textbox text operators: {error}"))?;
        out.extend_from_slice(positioning.as_bytes());
        let encoded = encode_cp1252(&line.text)?;
        append_pdf_string(&mut out, &encoded)?;
        out.try_reserve(4)
            .map_err(|error| format!("failed to grow textbox text operators: {error}"))?;
        out.extend_from_slice(b" Tj\n");
    }
    out.try_reserve(5)
        .map_err(|error| format!("failed to grow textbox text operators: {error}"))?;
    out.extend_from_slice(b"ET\nQ\n");
    Ok(out)
}

/// Return the ascender and descender of a Standard 14 font, which canvas
/// text places lines with: the font bounding boxes of the URW base-35 fonts
/// that MuPDF and other renderers draw these fonts with, not Adobe's AFM
/// metrics, as pymupdf 1.28.2 reports them, in thousandths of the em.
pub(crate) fn standard_line_metrics(font: Base14Font) -> LineMetrics {
    let (ascender, descender) = match font {
        Base14Font::Courier => (932, -317),
        Base14Font::CourierOblique => (920, -317),
        Base14Font::CourierBold => (1007, -393),
        Base14Font::CourierBoldOblique => (997, -393),
        Base14Font::Helvetica => (1075, -299),
        Base14Font::HelveticaOblique => (1070, -284),
        Base14Font::HelveticaBold => (1070, -307),
        Base14Font::HelveticaBoldOblique => (1073, -309),
        Base14Font::TimesRoman => (1053, -281),
        Base14Font::TimesItalic => (951, -270),
        Base14Font::TimesBold => (1044, -341),
        Base14Font::TimesBoldItalic => (972, -324),
        Base14Font::Symbol => (1010, -293),
        Base14Font::ZapfDingbats => (819, -144),
    };
    LineMetrics {
        ascender: f64::from(ascender) / 1000.0,
        descender: f64::from(descender) / 1000.0,
    }
}

/// Return the ascent and descent, as positive fractions of the em, that
/// Standard 14 form appearances place lines with.
pub(crate) fn standard_vertical_metrics(font: Base14Font) -> (f64, f64) {
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
    (ascent, descent)
}

/// Return the code of a character in a Standard 14 font's encoding when the
/// font has a glyph for it: WinAnsi for the text fonts and the built-in
/// encoding, addressed through WinAnsi bytes, for Symbol and ZapfDingbats.
pub(crate) fn standard_glyph_code(font: Base14Font, character: char) -> Option<u8> {
    let byte = cp1252_byte(character)?;
    let present = if matches!(font, Base14Font::Symbol | Base14Font::ZapfDingbats) {
        font.metrics()
            .character_metrics
            .iter()
            .any(|metric| metric.code == i32::from(byte))
    } else {
        font.winansi_width(byte).is_some()
    };
    present.then_some(byte)
}

pub(crate) fn standard_text_width(
    text: &str,
    font: Base14Font,
    font_size: f64,
) -> Result<f64, String> {
    let metrics = font.metrics();
    let symbolic = matches!(font, Base14Font::Symbol | Base14Font::ZapfDingbats);
    let mut units = 0.0;
    for character in text.chars() {
        let byte = cp1252_byte(character)
            .ok_or_else(|| "Standard 14 text contains a character outside WinAnsi".to_owned())?;
        let width = if symbolic {
            metrics
                .character_metrics
                .iter()
                .find(|metric| metric.code == i32::from(byte))
                .map(|metric| metric.width_x)
        } else {
            font.winansi_width(byte)
        }
        .unwrap_or(250.0);
        units += f64::from(width);
    }
    Ok(units * font_size / 1000.0)
}

pub(crate) fn base14_font(name: &str) -> Option<Base14Font> {
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

pub(crate) fn encode_cp1252(text: &str) -> Result<Vec<u8>, String> {
    let mut encoded = Vec::new();
    encoded
        .try_reserve_exact(text.len())
        .map_err(|error| format!("failed to allocate encoded Standard 14 text: {error}"))?;
    for character in text.chars() {
        encoded.push(
            cp1252_byte(character).ok_or_else(|| {
                "Standard 14 text contains a character outside WinAnsi".to_owned()
            })?,
        );
    }
    Ok(encoded)
}

fn cp1252_byte(character: char) -> Option<u8> {
    let code = character as u32;
    Some(match code {
        0x00..=0x7f | 0xa0..=0xff => code as u8,
        0x20ac => 0x80,
        0x201a => 0x82,
        0x0192 => 0x83,
        0x201e => 0x84,
        0x2026 => 0x85,
        0x2020 => 0x86,
        0x2021 => 0x87,
        0x02c6 => 0x88,
        0x2030 => 0x89,
        0x0160 => 0x8a,
        0x2039 => 0x8b,
        0x0152 => 0x8c,
        0x017d => 0x8e,
        0x2018 => 0x91,
        0x2019 => 0x92,
        0x201c => 0x93,
        0x201d => 0x94,
        0x2022 => 0x95,
        0x2013 => 0x96,
        0x2014 => 0x97,
        0x02dc => 0x98,
        0x2122 => 0x99,
        0x0161 => 0x9a,
        0x203a => 0x9b,
        0x0153 => 0x9c,
        0x017e => 0x9e,
        0x0178 => 0x9f,
        _ => return None,
    })
}

/// Return whether every character has a WinAnsi encoding.
pub fn is_winansi(text: &str) -> bool {
    text.chars()
        .all(|character| cp1252_byte(character).is_some())
}

fn append_pdf_string(out: &mut Vec<u8>, text: &[u8]) -> Result<(), String> {
    let additional = text
        .iter()
        .try_fold(2usize, |size, byte| {
            size.checked_add(match byte {
                b'(' | b')' | b'\\' => 2,
                0x20..=0x7e => 1,
                _ => 4,
            })
        })
        .ok_or_else(|| "encoded Standard 14 text exceeds the platform size limit".to_owned())?;
    out.try_reserve(additional)
        .map_err(|error| format!("failed to grow encoded Standard 14 text: {error}"))?;
    out.push(b'(');
    for &byte in text {
        match byte {
            b'(' | b')' | b'\\' => {
                out.push(b'\\');
                out.push(byte);
            }
            0x20..=0x7e => out.push(byte),
            _ => out.extend_from_slice(&[
                b'\\',
                b'0' + ((byte >> 6) & 0x07),
                b'0' + ((byte >> 3) & 0x07),
                b'0' + (byte & 0x07),
            ]),
        }
    }
    out.push(b')');
    Ok(())
}

fn content_is_exact_stream(doc: &Document, id: ObjectId, expected: &[u8]) -> bool {
    doc.get_object(id)
        .ok()
        .and_then(|object| object.as_stream().ok())
        .is_some_and(|stream| stream.content == expected)
}

/// Whether existing `/Contents` already sit between standalone `b"q\n"` and
/// `b"Q\n"` streams.
///
/// The shape alone does not isolate the streams between them, which may
/// restore the opening `q` early or leave a `q` of their own open, so it
/// spares a wrapper only together with a self-contained [`StateBalance`].
fn contents_have_outer_wrapper(doc: &Document, streams: &[ObjectId]) -> bool {
    streams.len() >= 2
        && streams
            .first()
            .is_some_and(|&id| content_is_exact_stream(doc, id, b"q\n"))
        && streams
            .last()
            .is_some_and(|&id| content_is_exact_stream(doc, id, b"Q\n"))
}

/// How existing page content leaves the graphics-state stack.
///
/// The counts follow the instructions that hayro's content parser yields to
/// its interpreter, so strings, comments, and inline image data hold no
/// operators, and counting ends where hayro stops interpreting the page.
#[derive(Clone, Copy, Default)]
struct StateBalance {
    /// `Q` operators that find no `q` of the content left to restore.
    unmatched_restores: usize,
    /// `q` operators that the content leaves without a `Q`.
    open_saves: usize,
    /// Whether any operator other than `q` runs outside every `q`/`Q` pair
    /// of the content.
    touches_base: bool,
}

impl StateBalance {
    /// Count the graphics states that one content stream saves and restores.
    ///
    /// hayro has panicked on malformed input elsewhere, so a panic of its
    /// parser is contained here as it is around interpretation. The content
    /// then counts as balanced, which leaves the single `q`/`Q` pair that
    /// drawing used before counting; hayro cannot interpret such a page.
    fn of(content: &[u8]) -> Self {
        catch_unwind(|| {
            let mut balance = Self::default();
            let mut instructions = TypedIter::new(content);
            while let Some(instruction) = instructions.next() {
                match instruction {
                    TypedInstruction::SaveState(_) => balance.open_saves += 1,
                    TypedInstruction::RestoreState(_) if balance.open_saves > 0 => {
                        balance.open_saves -= 1;
                    }
                    TypedInstruction::RestoreState(_) => {
                        balance.unmatched_restores += 1;
                        balance.touches_base = true;
                    }
                    _ => balance.touches_base |= balance.open_saves == 0,
                }
            }
            balance
        })
        .unwrap_or_default()
    }

    /// Whether the content restores every state that it saves and changes
    /// none outside its own `q`/`Q` pairs.
    fn is_self_contained(self) -> bool {
        !self.touches_base && self.open_saves == 0
    }
}

/// Count how the existing `/Contents` streams leave the graphics-state stack.
///
/// hayro joins the streams with a space and parses the result as one content
/// stream, so a string, comment, or inline image may continue from one stream
/// into the next. An entry that is no stream, or a stream that `decode`
/// cannot read, contributes nothing, as hayro skips a stream that it cannot
/// decode.
fn content_state_balance<'d>(
    doc: &'d Document,
    streams: &[ObjectId],
    decode: impl Fn(&'d Stream) -> Option<Cow<'d, [u8]>>,
) -> Result<StateBalance, String> {
    let mut parts = streams
        .iter()
        .filter_map(|id| doc.objects.get(id)?.as_stream().ok())
        .filter_map(decode);
    let Some(first) = parts.next() else {
        return Ok(StateBalance::default());
    };
    let Some(second) = parts.next() else {
        return Ok(StateBalance::of(&first));
    };
    let mut joined = Vec::new();
    for part in [first, second].into_iter().chain(parts) {
        let additional = part
            .len()
            .checked_add(1)
            .ok_or_else(|| "page content exceeds the platform size limit".to_owned())?;
        joined.try_reserve(additional).map_err(|error| {
            format!("failed to allocate page content for graphics-state counting: {error}")
        })?;
        joined.extend_from_slice(&part);
        joined.push(b' ');
    }
    Ok(StateBalance::of(&joined))
}

/// Inspect a page's bounded raw content-stream shape.
///
/// This checks arrays before lopdf materializes their reference list and also
/// rejects cyclic or excessively deep indirect `/Contents` chains. A reference
/// to a missing object or to an indirect null is dropped like a direct null,
/// because ISO 32000-2 §7.3.10 reads it as null and renderers stop reading
/// page content at the first entry that is not a stream. Array entries are
/// resolved one level, keeping the cost per entry constant.
pub fn inspect_page_contents(doc: &Document, page_id: ObjectId) -> Result<Vec<ObjectId>, String> {
    let page = doc
        .get_dictionary(page_id)
        .map_err(|error| format!("cannot inspect page Contents: {error}"))?;
    let Ok(mut contents) = page.get(b"Contents") else {
        return Ok(Vec::new());
    };
    let mut visited = Vec::new();
    visited
        .try_reserve_exact(MAX_CONTENT_REFERENCE_DEPTH)
        .map_err(|error| format!("failed to inspect page Contents references: {error}"))?;
    let mut streams = Vec::new();
    loop {
        match contents {
            Object::Reference(id) => {
                if visited.contains(id) {
                    return Err("page Contents contains a reference cycle".to_owned());
                }
                if visited.len() >= MAX_CONTENT_REFERENCE_DEPTH {
                    return Err(format!(
                        "page Contents exceeds the {MAX_CONTENT_REFERENCE_DEPTH}-reference-depth safety limit"
                    ));
                }
                visited.push(*id);
                match doc.objects.get(id) {
                    None => break,
                    Some(Object::Stream(_)) => {
                        streams.try_reserve_exact(1).map_err(|error| {
                            format!("failed to inspect page Contents streams: {error}")
                        })?;
                        streams.push(*id);
                        break;
                    }
                    Some(object) => contents = object,
                }
            }
            Object::Array(entries) => {
                if entries.len() > MAX_PAGE_CONTENT_STREAMS {
                    return Err(format!(
                        "page Contents exceeds the {MAX_PAGE_CONTENT_STREAMS}-entry safety limit"
                    ));
                }
                streams
                    .try_reserve_exact(entries.len())
                    .map_err(|error| format!("failed to inspect page Contents streams: {error}"))?;
                streams.extend(
                    entries
                        .iter()
                        .filter_map(|entry| entry.as_reference().ok())
                        .filter(|id| !matches!(doc.objects.get(id), None | Some(Object::Null))),
                );
                break;
            }
            _ => break,
        }
    }
    Ok(streams)
}

/// Fully allocated final `/Contents` order for one drawing insertion.
///
/// Placeholder slots are filled with newly assigned object references only
/// after every fallible collection allocation has completed.
pub struct ContentAppendPlan {
    contents: Vec<Object>,
    new_index: usize,
    opening: Option<(usize, Stream)>,
    closing: Option<(usize, Stream)>,
    is_isolated: bool,
}

/// Build an isolation stream holding `prefix` and then `count` operators.
fn isolation_stream(prefix: &[u8], operator: &[u8], count: usize) -> Result<Stream, String> {
    let length = operator
        .len()
        .checked_mul(count)
        .and_then(|length| length.checked_add(prefix.len()))
        .ok_or_else(|| {
            "page Contents isolation stream exceeds the platform size limit".to_owned()
        })?;
    let mut bytes = reserve_bytes(length, "page Contents isolation stream")?;
    bytes.extend_from_slice(prefix);
    for _ in 0..count {
        bytes.extend_from_slice(operator);
    }
    Ok(Stream::new(Dictionary::new(), bytes).with_compression(false))
}

/// Prepare a bounded final `/Contents` array before the PDF is changed.
///
/// Unless the page is known to be isolated, its existing streams are read
/// with `decode` and counted. The opening isolation stream then holds one `q`
/// more than the `Q` operators that find no `q` of the content, and the
/// closing stream one `Q` more than the `q` operators that the content leaves
/// open, so appended content starts from the page's initial graphics state.
/// The final count includes both streams unless the count shows that the
/// existing contents need none.
pub fn prepare_push_content<'d>(
    doc: &'d Document,
    page_id: ObjectId,
    already_isolated: bool,
    overlay: bool,
    decode: impl Fn(&'d Stream) -> Option<Cow<'d, [u8]>>,
) -> Result<ContentAppendPlan, String> {
    let streams = inspect_page_contents(doc, page_id)?;
    let final_count = |additions: usize| match streams.len().checked_add(additions) {
        Some(count) if count <= MAX_PAGE_CONTENT_STREAMS => Ok(count),
        Some(_) => Err(format!(
            "drawing would exceed the {MAX_PAGE_CONTENT_STREAMS}-stream page Contents safety limit"
        )),
        None => Err("drawing page Contents count exceeds the platform size limit".to_owned()),
    };
    // A page with no room for even the new stream is refused before decoding.
    final_count(1)?;
    let wrapper = if already_isolated || streams.is_empty() {
        None
    } else {
        let balance = content_state_balance(doc, &streams, decode)?;
        let wrapped = balance.is_self_contained() && contents_have_outer_wrapper(doc, &streams);
        (!wrapped).then_some(balance)
    };
    let final_count = final_count(if wrapper.is_some() { 3 } else { 1 })?;

    let mut contents = Vec::new();
    contents
        .try_reserve_exact(final_count)
        .map_err(|error| format!("failed to allocate final page Contents array: {error}"))?;
    let mut new_index = 0;
    if !overlay {
        contents.push(Object::Null);
    }
    let opening = match wrapper {
        Some(balance) => {
            let index = contents.len();
            contents.push(Object::Null);
            let count = balance.unmatched_restores.saturating_add(1);
            Some((index, isolation_stream(b"", b"q\n", count)?))
        }
        None => None,
    };
    contents.extend(streams.iter().copied().map(Object::Reference));
    let closing = match wrapper {
        Some(balance) => {
            let index = contents.len();
            contents.push(Object::Null);
            // The leading newline ends a comment that the last existing
            // stream leaves open, which would otherwise swallow the first `Q`.
            let count = balance.open_saves.saturating_add(1);
            Some((index, isolation_stream(b"\n", b"Q\n", count)?))
        }
        None => None,
    };
    if overlay {
        new_index = contents.len();
        contents.push(Object::Null);
    }
    debug_assert_eq!(contents.len(), final_count);
    Ok(ContentAppendPlan {
        contents,
        new_index,
        opening,
        closing,
        is_isolated: !streams.is_empty(),
    })
}

/// Add drawing operators as a new content stream for the page.
///
/// Return the page's final `/Contents` array and whether existing content is
/// now isolated. Overlay appends above existing content; otherwise prepend
/// below it. Existing content is only wrapped and never modified internally.
pub fn add_content_streams(
    doc: &mut impl ObjectSink,
    ops: Vec<u8>,
    mut plan: ContentAppendPlan,
) -> (Vec<Object>, bool) {
    if let Some((index, stream)) = plan.opening.take() {
        let id = doc.add_object(stream);
        plan.contents[index] = Object::Reference(id);
    }
    if let Some((index, stream)) = plan.closing.take() {
        let id = doc.add_object(stream);
        plan.contents[index] = Object::Reference(id);
    }
    let new_id = doc.add_object(Stream::new(Dictionary::new(), ops).with_compression(false));
    plan.contents[plan.new_index] = Object::Reference(new_id);
    (plan.contents, plan.is_isolated)
}
