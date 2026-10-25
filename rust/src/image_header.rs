//! Headers of encoded JPEG, PNG, and JPEG 2000 images: what
//! `pylopdf.image_info` reports and the EXIF orientation that drawing
//! applies, read without decoding a sample.

/// What an image file states about itself.
pub struct ImageHeader {
    /// `"jpeg"`, `"png"`, or `"jpx"`.
    pub format: &'static str,
    pub width: u32,
    pub height: u32,
    /// Colour components per pixel without alpha: 1 gray, 3 RGB, 4 CMYK.
    pub components: u8,
    pub alpha: bool,
    /// Bits per component as stored, 8 for a PNG palette's entries.
    pub bits: u8,
    /// Dots per inch across and down, as Pixmap decoding reads them.
    pub resolution: (u32, u32),
    /// The EXIF orientation, 1 through 8; 1 when the file states none.
    pub orientation: u8,
}

const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

/// Read the header of a JPEG, PNG, or JPEG 2000 image.
pub fn read(data: &[u8]) -> Result<ImageHeader, String> {
    if data.starts_with(PNG_SIGNATURE) {
        png(data)
    } else if data.starts_with(&[0xFF, 0xD8, 0xFF]) {
        jpeg(data)
    } else if crate::jpx::is_jpx(data) {
        jpx(data)
    } else {
        Err("unsupported image format (pass JPEG, PNG, or JPEG 2000)".to_owned())
    }
}

/// The EXIF orientation of JPEG or PNG data, 1 through 8; 1 for any other
/// data, an unreadable header, or a value outside that range.
pub fn orientation(data: &[u8]) -> u8 {
    if data.starts_with(PNG_SIGNATURE) {
        png_chunks(data)
            .find(|(kind, _)| kind == b"eXIf")
            .map_or(1, |(_, tiff)| tiff_orientation(tiff))
    } else if data.starts_with(&[0xFF, 0xD8, 0xFF]) {
        jpeg_segments(data)
            .find_map(|(marker, payload)| {
                (marker == 0xE1 && payload.starts_with(b"Exif\0\0"))
                    .then(|| tiff_orientation(&payload[6..]))
            })
            .unwrap_or(1)
    } else {
        1
    }
}

/// The horizontal mirroring and clockwise quarter turn that show an image
/// stored with EXIF `orientation` upright: the image is mirrored first.
pub fn orientation_transform(orientation: u8) -> (bool, i64) {
    match orientation {
        2 => (true, 0),
        3 => (false, 180),
        4 => (true, 180),
        5 => (true, 270),
        6 => (false, 90),
        7 => (true, 90),
        8 => (false, 270),
        _ => (false, 0),
    }
}

fn png(data: &[u8]) -> Result<ImageHeader, String> {
    let ihdr = data
        .get(8..33)
        .filter(|ihdr| &ihdr[4..8] == b"IHDR")
        .ok_or_else(|| "failed to read the PNG: no IHDR chunk".to_owned())?;
    let width = u32::from_be_bytes(ihdr[8..12].try_into().unwrap_or_default());
    let height = u32::from_be_bytes(ihdr[12..16].try_into().unwrap_or_default());
    let (bits, color) = (ihdr[16], ihdr[17]);
    let (components, alpha, bits) = match color {
        0 => (1, false, bits),
        2 => (3, false, bits),
        3 => (3, false, 8),
        4 => (1, true, bits),
        6 => (3, true, bits),
        other => return Err(format!("failed to read the PNG: color type {other}")),
    };
    let mut header = ImageHeader {
        format: "png",
        width,
        height,
        components,
        alpha,
        bits,
        resolution: crate::pixmap::DEFAULT_RESOLUTION,
        orientation: 1,
    };
    for (kind, payload) in png_chunks(data) {
        match &kind {
            // Decoding expands transparency into an alpha channel.
            b"tRNS" => header.alpha = true,
            b"pHYs" if payload.len() >= 9 && payload[8] == 1 => {
                let x = u32::from_be_bytes(payload[0..4].try_into().unwrap_or_default());
                let y = u32::from_be_bytes(payload[4..8].try_into().unwrap_or_default());
                if x > 0 && y > 0 {
                    header.resolution = (
                        crate::pixmap::whole_resolution(f64::from(x) * 0.0254),
                        crate::pixmap::whole_resolution(f64::from(y) * 0.0254),
                    );
                }
            }
            b"eXIf" => header.orientation = tiff_orientation(payload),
            _ => {}
        }
    }
    check_size("PNG", header)
}

fn jpeg(data: &[u8]) -> Result<ImageHeader, String> {
    let mut orientation = 1;
    for (marker, payload) in jpeg_segments(data) {
        if marker == 0xE1 && payload.starts_with(b"Exif\0\0") {
            orientation = tiff_orientation(&payload[6..]);
        }
        // SOF0 to SOF15 carry the frame, except DHT, JPG, and DAC.
        if matches!(marker, 0xC0..=0xCF) && !matches!(marker, 0xC4 | 0xC8 | 0xCC) {
            let frame = payload
                .get(..6)
                .ok_or_else(|| "failed to read the JPEG: corrupt SOF segment".to_owned())?;
            let components = match frame[5] {
                count @ (1 | 3 | 4) => count,
                other => {
                    return Err(format!("failed to read the JPEG: {other} color components"));
                }
            };
            return check_size(
                "JPEG",
                ImageHeader {
                    format: "jpeg",
                    width: u32::from(u16::from_be_bytes([frame[3], frame[4]])),
                    height: u32::from(u16::from_be_bytes([frame[1], frame[2]])),
                    components,
                    alpha: false,
                    bits: frame[0],
                    resolution: crate::extract::jpeg_resolution(data),
                    orientation,
                },
            );
        }
    }
    Err("failed to read the JPEG: no SOF marker".to_owned())
}

fn jpx(data: &[u8]) -> Result<ImageHeader, String> {
    let header = crate::jpx::header(data)?;
    let resolution =
        crate::jpx::resolution(data).map_or(crate::pixmap::DEFAULT_RESOLUTION, |(x, y)| {
            (
                crate::pixmap::whole_resolution(x),
                crate::pixmap::whole_resolution(y),
            )
        });
    check_size(
        "JPEG 2000",
        ImageHeader {
            format: "jpx",
            width: header.width,
            height: header.height,
            components: header.components,
            alpha: header.alpha,
            bits: header.bits,
            resolution,
            orientation: 1,
        },
    )
}

fn check_size(format: &str, header: ImageHeader) -> Result<ImageHeader, String> {
    if header.width == 0 || header.height == 0 {
        return Err(format!("failed to read the {format}: its size is zero"));
    }
    Ok(header)
}

/// The chunks of PNG data, as `(type, payload)`, up to the first that does
/// not fit.
fn png_chunks(data: &[u8]) -> impl Iterator<Item = ([u8; 4], &[u8])> {
    let mut rest = data.get(PNG_SIGNATURE.len()..).unwrap_or_default();
    std::iter::from_fn(move || {
        let length = usize::try_from(u32::from_be_bytes(rest.get(..4)?.try_into().ok()?)).ok()?;
        let kind: [u8; 4] = rest.get(4..8)?.try_into().ok()?;
        let end = length.checked_add(12)?;
        let payload = rest.get(8..8 + length)?;
        rest = rest.get(end..).unwrap_or_default();
        Some((kind, payload))
    })
}

/// The segments of JPEG data up to its first scan, as `(marker, payload)`.
fn jpeg_segments(data: &[u8]) -> impl Iterator<Item = (u8, &[u8])> {
    let mut at = 2;
    std::iter::from_fn(move || {
        loop {
            // Skip fill bytes and standalone markers.
            while data.get(at) == Some(&0xFF) && data.get(at + 1) == Some(&0xFF) {
                at += 1;
            }
            if data.get(at) != Some(&0xFF) {
                return None;
            }
            let marker = *data.get(at + 1)?;
            if marker == 0x01 || (0xD0..=0xD7).contains(&marker) {
                at += 2;
                continue;
            }
            if marker == 0xDA || marker == 0xD9 {
                return None;
            }
            let length = usize::from(u16::from_be_bytes([*data.get(at + 2)?, *data.get(at + 3)?]));
            let payload = data.get(at + 4..(at + 2).checked_add(length)?)?;
            at += 2 + length;
            return Some((marker, payload));
        }
    })
}

/// The orientation tag of a TIFF structure's first IFD, as EXIF stores it.
fn tiff_orientation(tiff: &[u8]) -> u8 {
    let little = match tiff.get(..4) {
        Some(b"II*\0") => true,
        Some(b"MM\0*") => false,
        _ => return 1,
    };
    let u16_at = |at: usize| -> Option<u16> {
        let bytes = [*tiff.get(at)?, *tiff.get(at + 1)?];
        Some(if little {
            u16::from_le_bytes(bytes)
        } else {
            u16::from_be_bytes(bytes)
        })
    };
    let u32_at = |at: usize| -> Option<u32> {
        let bytes: [u8; 4] = tiff.get(at..at + 4)?.try_into().ok()?;
        Some(if little {
            u32::from_le_bytes(bytes)
        } else {
            u32::from_be_bytes(bytes)
        })
    };
    let read = || -> Option<u8> {
        let ifd = usize::try_from(u32_at(4)?).ok()?;
        let count = usize::from(u16_at(ifd)?);
        (0..count).find_map(|index| {
            let entry = ifd.checked_add(2 + 12 * index)?;
            // Orientation is one SHORT, held in the entry's value field.
            (u16_at(entry)? == 0x0112 && u16_at(entry + 2)? == 3)
                .then(|| u16_at(entry + 8))
                .flatten()
                .and_then(|value| u8::try_from(value).ok())
        })
    };
    read().filter(|value| (1..=8).contains(value)).unwrap_or(1)
}

/// The header facts that `pylopdf.image_info` turns into an `ImageInfo`:
/// `(format, width, height, components, alpha, bits, resolution, orientation)`.
type ImageInfoTuple = (&'static str, u32, u32, u8, bool, u8, (u32, u32), u8);

/// Read the header of encoded image `data`, or of the file at `path` read
/// within `max_size` bytes.
#[pyo3::pyfunction]
#[pyo3(signature = (data=None, path=None, max_size=None))]
pub(crate) fn image_info(
    py: pyo3::Python<'_>,
    data: Option<Vec<u8>>,
    path: Option<String>,
    max_size: Option<usize>,
) -> pyo3::PyResult<ImageInfoTuple> {
    use crate::document::PdfError;
    let data = match (data, path) {
        (Some(data), None) => data,
        (None, Some(path)) => py.detach(|| crate::document::read_image_input(&path, max_size))?,
        _ => {
            return Err(PdfError::new_err(
                "image_info takes exactly one of data and path",
            ));
        }
    };
    let header = py.detach(|| read(&data)).map_err(PdfError::new_err)?;
    Ok((
        header.format,
        header.width,
        header.height,
        header.components,
        header.alpha,
        header.bits,
        header.resolution,
        header.orientation,
    ))
}
