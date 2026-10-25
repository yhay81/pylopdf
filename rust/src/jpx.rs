//! JPEG 2000 input: a JP2 file or a raw codestream, read with
//! hayro-jpeg2000, the decoder that hayro renders `/JPXDecode` images with.
//!
//! Drawing stores such data as it is, so only the header is read here:
//! the size, the colour model, an alpha channel, and whether a palette maps
//! the samples. Decoding into samples serves Pixmaps and new documents,
//! which krilla writes from pixels.

use std::panic::{AssertUnwindSafe, catch_unwind};

use hayro_jpeg2000::{ColorSpace, DecodeSettings, DecoderContext, Image};

/// The JP2 signature box as hayro-jpeg2000 recognizes it.
const JP2_SIGNATURE: &[u8] = b"\x00\x00\x00\x0cjP  ";
/// The SOC and SIZ markers that open a raw codestream.
const CODESTREAM_SIGNATURE: &[u8] = &[0xFF, 0x4F, 0xFF, 0x51];
/// Metres per inch, to state a JP2 resolution box in dots per inch.
const METRES_PER_INCH: f64 = 0.0254;

/// Whether `data` opens with a JP2 signature box or a raw codestream.
pub fn is_jpx(data: &[u8]) -> bool {
    data.starts_with(JP2_SIGNATURE) || data.starts_with(CODESTREAM_SIGNATURE)
}

/// The colour model of a JPEG 2000 image, an ICC profile's by its channels.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum JpxColor {
    Gray,
    Rgb,
    Cmyk,
}

impl JpxColor {
    fn of(space: &ColorSpace) -> Option<Self> {
        match space {
            ColorSpace::Gray
            | ColorSpace::Icc {
                num_channels: 1, ..
            } => Some(Self::Gray),
            ColorSpace::RGB
            | ColorSpace::Icc {
                num_channels: 3, ..
            } => Some(Self::Rgb),
            ColorSpace::CMYK
            | ColorSpace::Icc {
                num_channels: 4, ..
            } => Some(Self::Cmyk),
            _ => None,
        }
    }

    fn channels(self) -> usize {
        match self {
            Self::Gray => 1,
            Self::Rgb => 3,
            Self::Cmyk => 4,
        }
    }
}

/// What drawing needs to know of a JPEG 2000 image before storing it.
pub struct JpxHeader {
    pub width: u32,
    pub height: u32,
    pub alpha: bool,
    /// Whether a palette maps the samples, which hayro reads as gray
    /// indices unless the PDF names an Indexed colour space.
    pub palette: bool,
}

/// Decoded 8-bit samples: gray or RGB, CMYK converted, with straight alpha
/// last when the image has it.
pub struct JpxSamples {
    pub width: u32,
    pub height: u32,
    pub gray: bool,
    pub alpha: bool,
    pub data: Vec<u8>,
}

/// Run a hayro-jpeg2000 call, turning a panic on malformed input into an error.
fn guarded<T>(read: impl FnOnce() -> Result<T, String>) -> Result<T, String> {
    catch_unwind(AssertUnwindSafe(read)).unwrap_or_else(|_| {
        Err(
            "failed to read the JPEG 2000 image: the decoder panicked on malformed input"
                .to_owned(),
        )
    })
}

fn open(data: &[u8]) -> Result<(Image<'_>, JpxColor), String> {
    let image = Image::new(data, &DecodeSettings::default())
        .map_err(|error| format!("failed to read the JPEG 2000 image: {error}"))?;
    let color = JpxColor::of(image.color_space()).ok_or_else(|| {
        "unsupported JPEG 2000 color space (pass gray, RGB, or CMYK data)".to_owned()
    })?;
    Ok((image, color))
}

/// Read the header of a JP2 file or a raw codestream.
pub fn header(data: &[u8]) -> Result<JpxHeader, String> {
    guarded(|| {
        let (image, _) = open(data)?;
        Ok(JpxHeader {
            width: image.width(),
            height: image.height(),
            alpha: image.has_alpha(),
            palette: header_box(data, *b"pclr").is_some(),
        })
    })
}

/// Decode a JP2 file or a raw codestream into 8-bit samples.
pub fn decode(data: &[u8]) -> Result<JpxSamples, String> {
    guarded(|| {
        let (image, color) = open(data)?;
        let (width, height, alpha) = (image.width(), image.height(), image.has_alpha());
        let mut context = DecoderContext::default();
        let decoded = image
            .decode(&mut context)
            .map_err(|error| format!("failed to decode the JPEG 2000 image: {error}"))?;
        let components = decoded.components();
        let channels = color.channels() + usize::from(alpha);
        let pixels = usize::try_from(u64::from(width) * u64::from(height))
            .map_err(|_| "the JPEG 2000 image is too large to decode".to_owned())?;
        if components.len() != channels
            || components
                .iter()
                .any(|component| component.samples().len() != pixels)
        {
            return Err(
                "failed to decode the JPEG 2000 image: the decoder returned an unexpected sample count"
                    .to_owned(),
            );
        }
        let length = pixels
            .checked_mul(channels)
            .ok_or_else(|| "the JPEG 2000 image is too large to decode".to_owned())?;
        let mut samples = Vec::new();
        samples
            .try_reserve_exact(length)
            .map_err(|error| format!("failed to allocate the decoded JPEG 2000 image: {error}"))?;
        samples.resize(length, 0);
        decoded.store_u8_into(&mut samples);
        if color == JpxColor::Cmyk {
            cmyk_to_rgb_in_place(&mut samples, alpha);
        }
        Ok(JpxSamples {
            width,
            height,
            gray: color == JpxColor::Gray,
            alpha,
            data: samples,
        })
    })
}

/// Convert CMYK samples, with alpha last when `alpha`, to RGB in place, as
/// `(1 - c)(1 - k)` per channel, and drop the unused tail.
fn cmyk_to_rgb_in_place(samples: &mut Vec<u8>, alpha: bool) {
    let source = 4 + usize::from(alpha);
    let target = 3 + usize::from(alpha);
    let pixels = samples.len() / source;
    for pixel in 0..pixels {
        let at = pixel * source;
        let [c, m, y, k] = [
            samples[at],
            samples[at + 1],
            samples[at + 2],
            samples[at + 3],
        ]
        .map(u32::from);
        let white = 255 - k;
        let out = pixel * target;
        samples[out] = ((255 - c) * white / 255) as u8;
        samples[out + 1] = ((255 - m) * white / 255) as u8;
        samples[out + 2] = ((255 - y) * white / 255) as u8;
        if alpha {
            samples[out + 3] = samples[at + 4];
        }
    }
    samples.truncate(pixels * target);
}

/// The horizontal and vertical resolution in dots per inch that a JP2
/// file's capture resolution box, else its display resolution box, states.
pub fn resolution(data: &[u8]) -> Option<(f64, f64)> {
    let boxes = header_box(data, *b"res ")?;
    let read = |kind: [u8; 4]| {
        let payload = find_box(boxes, kind)?;
        let field = |at: usize| {
            Some(f64::from(u16::from_be_bytes([
                *payload.get(at)?,
                *payload.get(at + 1)?,
            ])))
        };
        let exponent = |at: usize| Some(f64::from(i8::from_be_bytes([*payload.get(at)?])));
        let (vertical_n, vertical_d) = (field(0)?, field(2)?);
        let (horizontal_n, horizontal_d) = (field(4)?, field(6)?);
        let (vertical_e, horizontal_e) = (exponent(8)?, exponent(9)?);
        if vertical_d == 0.0 || horizontal_d == 0.0 {
            return None;
        }
        let horizontal = horizontal_n / horizontal_d * 10f64.powf(horizontal_e) * METRES_PER_INCH;
        let vertical = vertical_n / vertical_d * 10f64.powf(vertical_e) * METRES_PER_INCH;
        (horizontal > 0.0 && vertical > 0.0).then_some((horizontal, vertical))
    };
    read(*b"resc").or_else(|| read(*b"resd"))
}

/// The payload of a box of `kind` inside a JP2 file's header box.
fn header_box(data: &[u8], kind: [u8; 4]) -> Option<&[u8]> {
    if !data.starts_with(JP2_SIGNATURE) {
        return None;
    }
    find_box(find_box(data, *b"jp2h")?, kind)
}

/// The payload of the first box of `kind` among the boxes of `data`.
fn find_box(data: &[u8], kind: [u8; 4]) -> Option<&[u8]> {
    let mut rest = data;
    while rest.len() >= 8 {
        let length = u32::from_be_bytes(rest[..4].try_into().ok()?);
        let (header, length) = match length {
            0 => (8, rest.len()),
            1 => {
                let long = u64::from_be_bytes(rest.get(8..16)?.try_into().ok()?);
                (16, usize::try_from(long).ok()?)
            }
            length => (8, usize::try_from(length).ok()?),
        };
        if length < header || length > rest.len() {
            return None;
        }
        if rest[4..8] == kind {
            return Some(&rest[header..length]);
        }
        rest = &rest[length..];
    }
    None
}
