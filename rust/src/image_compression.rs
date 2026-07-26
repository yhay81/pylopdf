//! Conservative raster XObject downsampling and JPEG recompression.
//!
//! Placement DPI comes from hayro interpretation. Mutation stays in lopdf and
//! is limited to direct 8-bit DeviceGray/DeviceRGB DCT or Flate streams without
//! masks or custom decode semantics.

use std::collections::HashSet;
use std::f64::consts::PI;
use std::panic::{AssertUnwindSafe, catch_unwind};

use jpeg_encoder::{ColorType as JpegColorType, Encoder as JpegEncoder};
use lopdf::{Dictionary, Document, Object, ObjectId, Stream};
use zune_jpeg::JpegDecoder;
use zune_jpeg::zune_core::bytestream::ZCursor;
use zune_jpeg::zune_core::colorspace::ColorSpace;
use zune_jpeg::zune_core::options::DecoderOptions;

use crate::extract::ImageUsage;

const MAX_IMAGE_PIXELS: u64 = 64_000_000;
const MAX_TOTAL_IMAGE_PIXELS: u64 = 250_000_000;
const MAX_JPEG_DIMENSION: usize = u16::MAX as usize;

/// Unique interpreted images, rewritten images, skipped images, and byte totals.
pub(crate) type CompressionResult = (u32, u32, u32, u64, u64);

#[derive(Clone, Copy)]
enum ColorModel {
    Gray,
    Rgb,
}

impl ColorModel {
    fn components(self) -> usize {
        match self {
            Self::Gray => 1,
            Self::Rgb => 3,
        }
    }

    fn zune_color(self) -> ColorSpace {
        match self {
            Self::Gray => ColorSpace::Luma,
            Self::Rgb => ColorSpace::RGB,
        }
    }

    fn jpeg_color(self) -> JpegColorType {
        match self {
            Self::Gray => JpegColorType::Luma,
            Self::Rgb => JpegColorType::Rgb,
        }
    }
}

#[derive(Clone, Copy)]
struct Candidate<'a> {
    source: CandidateSource<'a>,
    width: u32,
    height: u32,
    color: ColorModel,
    previous_quality: Option<u8>,
}

#[derive(Clone, Copy)]
enum CandidateSource<'a> {
    Jpeg(&'a [u8]),
    Flate {
        stream: &'a Stream,
        max_decoded_bytes: usize,
    },
}

impl CandidateSource<'_> {
    fn encoded_len(&self) -> usize {
        match self {
            Self::Jpeg(data) => data.len(),
            Self::Flate { stream, .. } => stream.content.len(),
        }
    }
}

fn mask_references(doc: &Document, usages: &[ImageUsage]) -> HashSet<ObjectId> {
    let candidates = usages
        .iter()
        .map(|usage| usage.object_id)
        .collect::<HashSet<_>>();
    let mut references = HashSet::new();
    for object in doc.objects.values() {
        let Ok(stream) = object.as_stream() else {
            continue;
        };
        for key in [b"SMask".as_slice(), b"Mask".as_slice()] {
            if let Ok(Object::Reference(id)) = stream.dict.get(key)
                && candidates.contains(id)
            {
                references.insert(*id);
            }
        }
    }
    references
}

fn direct_name<'a>(dict: &'a Dictionary, key: &[u8]) -> Option<&'a [u8]> {
    dict.get(key).and_then(Object::as_name).ok()
}

fn direct_integer(dict: &Dictionary, key: &[u8]) -> Option<i64> {
    dict.get(key).and_then(Object::as_i64).ok()
}

fn safe_flate_decode_limit(
    dict: &Dictionary,
    width: u32,
    height: u32,
    color: ColorModel,
) -> Option<usize> {
    let samples = sample_buffer_len(width, height, color.components()).ok()?;
    let params = match dict.get(b"DecodeParms") {
        Err(_) | Ok(Object::Null) => return Some(samples),
        Ok(Object::Dictionary(params)) => params,
        _ => return None,
    };
    let predictor = direct_integer(params, b"Predictor").unwrap_or(1);
    if predictor == 1 {
        return Some(samples);
    }
    if !(10..=15).contains(&predictor)
        || direct_integer(params, b"Columns").unwrap_or(1) != i64::from(width)
        || direct_integer(params, b"Colors").unwrap_or(1) != color.components() as i64
        || direct_integer(params, b"BitsPerComponent").unwrap_or(8) != 8
    {
        return None;
    }
    samples.checked_add(height as usize)
}

fn is_safe_image_stream<'a>(
    doc: &'a Document,
    usage: ImageUsage,
    mask_ids: &HashSet<ObjectId>,
) -> Option<Candidate<'a>> {
    if mask_ids.contains(&usage.object_id) {
        return None;
    }
    let stream = doc.get_object(usage.object_id).ok()?.as_stream().ok()?;
    if !matches!(direct_name(&stream.dict, b"Subtype"), Some(b"Image")) {
        return None;
    }
    if stream.dict.get(b"Decode").is_ok()
        || stream.dict.get(b"SMask").is_ok()
        || stream.dict.get(b"Mask").is_ok()
        || stream.dict.get(b"F").is_ok()
        || stream.dict.get(b"FFilter").is_ok()
        || stream.dict.get(b"FDecodeParms").is_ok()
    {
        return None;
    }
    if matches!(
        stream.dict.get(b"ImageMask").and_then(Object::as_bool),
        Ok(true)
    ) {
        return None;
    }
    if direct_integer(&stream.dict, b"BitsPerComponent") != Some(8) {
        return None;
    }
    let width = u32::try_from(direct_integer(&stream.dict, b"Width")?).ok()?;
    let height = u32::try_from(direct_integer(&stream.dict, b"Height")?).ok()?;
    if width != usage.width || height != usage.height || width == 0 || height == 0 {
        return None;
    }
    let color = match direct_name(&stream.dict, b"ColorSpace") {
        Some(b"DeviceGray" | b"G") => ColorModel::Gray,
        Some(b"DeviceRGB" | b"RGB") => ColorModel::Rgb,
        _ => return None,
    };
    let filters = stream.filters().ok()?;
    if filters.len() != 1 {
        return None;
    }
    let (source, previous_quality) = match filters[0] {
        b"DCTDecode" | b"DCT"
            if stream.dict.get(b"DecodeParms").is_err()
                && stream.content.starts_with(&[0xFF, 0xD8, 0xFF]) =>
        {
            let previous_quality = direct_integer(&stream.dict, b"PylopdfQuality")
                .and_then(|value| u8::try_from(value).ok())
                .filter(|value| (1..=100).contains(value));
            (CandidateSource::Jpeg(&stream.content), previous_quality)
        }
        b"FlateDecode" | b"Fl" => {
            let max_decoded_bytes = safe_flate_decode_limit(&stream.dict, width, height, color)?;
            (
                CandidateSource::Flate {
                    stream,
                    max_decoded_bytes,
                },
                None,
            )
        }
        _ => return None,
    };
    Some(Candidate {
        source,
        width,
        height,
        color,
        previous_quality,
    })
}

fn target_dimension(original: u32, min_dpi: Option<f64>, target_dpi: Option<f64>) -> u32 {
    let (Some(min_dpi), Some(target_dpi)) = (min_dpi, target_dpi) else {
        return original;
    };
    if !min_dpi.is_finite() || min_dpi <= target_dpi {
        return original;
    }
    let scaled = f64::from(original) * target_dpi / min_dpi;
    let nearest = scaled.round();
    let stable = if (scaled - nearest).abs() <= scaled.max(1.0) * 1e-9 {
        nearest
    } else {
        scaled.floor()
    };
    (stable as u32).clamp(1, original)
}

fn decode_jpeg(candidate: &Candidate<'_>) -> Result<Vec<u8>, String> {
    let CandidateSource::Jpeg(data) = candidate.source else {
        return Err("internal image source mismatch".to_owned());
    };
    let options = DecoderOptions::default()
        .set_max_width(MAX_JPEG_DIMENSION)
        .set_max_height(MAX_JPEG_DIMENSION)
        .set_strict_mode(true)
        .jpeg_set_out_colorspace(candidate.color.zune_color());
    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut decoder = JpegDecoder::new_with_options(ZCursor::new(data), options);
        decoder
            .decode_headers()
            .map_err(|error| format!("failed to read JPEG headers: {error}"))?;
        let dimensions = decoder
            .dimensions()
            .ok_or_else(|| "JPEG dimensions are unavailable".to_owned())?;
        if dimensions != (candidate.width as usize, candidate.height as usize) {
            return Err("JPEG dimensions do not match its PDF image dictionary".to_owned());
        }
        decoder
            .decode()
            .map_err(|error| format!("failed to decode JPEG: {error}"))
    }));
    let pixels = result.map_err(|_| "JPEG decoder panicked on malformed input".to_owned())??;
    let expected = usize::try_from(candidate.width)
        .ok()
        .and_then(|width| {
            usize::try_from(candidate.height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .and_then(|pixels| pixels.checked_mul(candidate.color.components()))
        .ok_or_else(|| "JPEG decoded size overflowed".to_owned())?;
    if pixels.len() != expected {
        return Err("JPEG decoder returned an unexpected sample count".to_owned());
    }
    Ok(pixels)
}

fn decode_flate(candidate: &Candidate<'_>) -> Result<Vec<u8>, String> {
    let CandidateSource::Flate {
        stream,
        max_decoded_bytes,
    } = candidate.source
    else {
        return Err("internal image source mismatch".to_owned());
    };
    let result = catch_unwind(AssertUnwindSafe(|| {
        stream
            .decompressed_content_with_limit(max_decoded_bytes)
            .map_err(|error| format!("failed to decode Flate image: {error}"))
    }));
    let pixels = result.map_err(|_| "Flate decoder panicked on malformed input".to_owned())??;
    let expected = sample_buffer_len(
        candidate.width,
        candidate.height,
        candidate.color.components(),
    )?;
    if pixels.len() != expected {
        return Err("Flate image returned an unexpected sample count".to_owned());
    }
    Ok(pixels)
}

fn decode_pixels(candidate: &Candidate<'_>) -> Result<Vec<u8>, String> {
    match candidate.source {
        CandidateSource::Jpeg(_) => decode_jpeg(candidate),
        CandidateSource::Flate { .. } => decode_flate(candidate),
    }
}

struct ResampleKernel {
    start: usize,
    weights: Vec<f32>,
}

fn lanczos3(value: f64) -> f64 {
    let value = value.abs();
    if value < f64::EPSILON {
        1.0
    } else if value >= 3.0 {
        0.0
    } else {
        let pi_value = PI * value;
        (pi_value.sin() / pi_value) * ((pi_value / 3.0).sin() / (pi_value / 3.0))
    }
}

/// Precompute one normalized antialiasing kernel per target sample.
///
/// Target dimensions never exceed source dimensions. Widening the Lanczos3
/// support by the reduction ratio suppresses aliasing while keeping total
/// coefficients linear in the source dimension.
fn resample_kernels(source: u32, target: u32) -> Result<Vec<ResampleKernel>, String> {
    if target == 0 || target >= source {
        return Err("invalid image resize dimensions".to_owned());
    }
    let scale = f64::from(source) / f64::from(target);
    let support = 3.0 * scale;
    let last_source = i64::from(source) - 1;
    let mut kernels = Vec::new();
    kernels
        .try_reserve_exact(target as usize)
        .map_err(|_| "failed to allocate image resize kernels".to_owned())?;

    for output in 0..target {
        let center = (f64::from(output) + 0.5) * scale - 0.5;
        let start = ((center - support).ceil() as i64).clamp(0, last_source);
        let end = ((center + support).floor() as i64).clamp(start, last_source);
        let count = usize::try_from(end - start + 1)
            .map_err(|_| "image resize kernel length overflowed".to_owned())?;
        let mut weights = Vec::new();
        weights
            .try_reserve_exact(count)
            .map_err(|_| "failed to allocate image resize coefficients".to_owned())?;
        let mut sum = 0.0;
        for input in start..=end {
            let weight = lanczos3((input as f64 - center) / scale);
            weights.push(weight as f32);
            sum += weight;
        }
        if !sum.is_finite() || sum.abs() < f64::EPSILON {
            return Err("image resize kernel is not finite".to_owned());
        }
        for weight in &mut weights {
            *weight = (*weight as f64 / sum) as f32;
        }
        kernels.push(ResampleKernel {
            start: usize::try_from(start)
                .map_err(|_| "image resize kernel start overflowed".to_owned())?,
            weights,
        });
    }
    Ok(kernels)
}

fn sample_buffer_len(width: u32, height: u32, components: usize) -> Result<usize, String> {
    usize::try_from(width)
        .ok()
        .and_then(|width| {
            usize::try_from(height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .and_then(|pixels| pixels.checked_mul(components))
        .ok_or_else(|| "image resize buffer length overflowed".to_owned())
}

fn allocate_samples(width: u32, height: u32, components: usize) -> Result<Vec<u8>, String> {
    let len = sample_buffer_len(width, height, components)?;
    let mut samples = Vec::new();
    samples
        .try_reserve_exact(len)
        .map_err(|_| "failed to allocate image resize output".to_owned())?;
    samples.resize(len, 0);
    Ok(samples)
}

fn quantize_sample(value: f32) -> u8 {
    value.round().clamp(0.0, 255.0) as u8
}

fn resize_horizontal(
    input: &[u8],
    source_width: u32,
    height: u32,
    target_width: u32,
    components: usize,
) -> Result<Vec<u8>, String> {
    if input.len() != sample_buffer_len(source_width, height, components)? {
        return Err("image resize input length is inconsistent".to_owned());
    }
    let kernels = resample_kernels(source_width, target_width)?;
    let mut output = allocate_samples(target_width, height, components)?;
    let source_width = source_width as usize;
    let target_width = target_width as usize;
    for y in 0..height as usize {
        let source_row = y * source_width * components;
        let target_row = y * target_width * components;
        for (x, kernel) in kernels.iter().enumerate() {
            match components {
                1 => {
                    let mut value = 0.0f32;
                    for (offset, weight) in kernel.weights.iter().enumerate() {
                        value += f32::from(input[source_row + kernel.start + offset]) * weight;
                    }
                    output[target_row + x] = quantize_sample(value);
                }
                3 => {
                    let mut values = [0.0f32; 3];
                    for (offset, weight) in kernel.weights.iter().enumerate() {
                        let source = source_row + (kernel.start + offset) * 3;
                        values[0] += f32::from(input[source]) * weight;
                        values[1] += f32::from(input[source + 1]) * weight;
                        values[2] += f32::from(input[source + 2]) * weight;
                    }
                    let target = target_row + x * 3;
                    output[target] = quantize_sample(values[0]);
                    output[target + 1] = quantize_sample(values[1]);
                    output[target + 2] = quantize_sample(values[2]);
                }
                _ => return Err("unsupported image component count".to_owned()),
            }
        }
    }
    Ok(output)
}

fn resize_vertical(
    input: &[u8],
    width: u32,
    source_height: u32,
    target_height: u32,
    components: usize,
) -> Result<Vec<u8>, String> {
    if input.len() != sample_buffer_len(width, source_height, components)? {
        return Err("image resize input length is inconsistent".to_owned());
    }
    let kernels = resample_kernels(source_height, target_height)?;
    let mut output = allocate_samples(width, target_height, components)?;
    let row_stride = width as usize * components;
    for (y, kernel) in kernels.iter().enumerate() {
        let target_row = y * row_stride;
        for x in 0..width as usize {
            match components {
                1 => {
                    let mut value = 0.0f32;
                    for (offset, weight) in kernel.weights.iter().enumerate() {
                        value +=
                            f32::from(input[(kernel.start + offset) * row_stride + x]) * weight;
                    }
                    output[target_row + x] = quantize_sample(value);
                }
                3 => {
                    let mut values = [0.0f32; 3];
                    for (offset, weight) in kernel.weights.iter().enumerate() {
                        let source = (kernel.start + offset) * row_stride + x * 3;
                        values[0] += f32::from(input[source]) * weight;
                        values[1] += f32::from(input[source + 1]) * weight;
                        values[2] += f32::from(input[source + 2]) * weight;
                    }
                    let target = target_row + x * 3;
                    output[target] = quantize_sample(values[0]);
                    output[target + 1] = quantize_sample(values[1]);
                    output[target + 2] = quantize_sample(values[2]);
                }
                _ => return Err("unsupported image component count".to_owned()),
            }
        }
    }
    Ok(output)
}

fn resize_pixels(
    pixels: Vec<u8>,
    source: (u32, u32),
    target: (u32, u32),
    color: ColorModel,
) -> Result<Vec<u8>, String> {
    if source == target {
        return Ok(pixels);
    }
    let components = color.components();
    let horizontal_first =
        u64::from(target.0) * u64::from(source.1) <= u64::from(source.0) * u64::from(target.1);
    if horizontal_first {
        let pixels = if source.0 == target.0 {
            pixels
        } else {
            resize_horizontal(&pixels, source.0, source.1, target.0, components)?
        };
        if source.1 == target.1 {
            Ok(pixels)
        } else {
            resize_vertical(&pixels, target.0, source.1, target.1, components)
        }
    } else {
        let pixels = if source.1 == target.1 {
            pixels
        } else {
            resize_vertical(&pixels, source.0, source.1, target.1, components)?
        };
        if source.0 == target.0 {
            Ok(pixels)
        } else {
            resize_horizontal(&pixels, source.0, target.1, target.0, components)
        }
    }
}

fn encode_jpeg(
    pixels: &[u8],
    width: u32,
    height: u32,
    color: ColorModel,
    quality: u8,
) -> Result<Vec<u8>, String> {
    let width =
        u16::try_from(width).map_err(|_| "compressed JPEG width exceeds 65535".to_owned())?;
    let height =
        u16::try_from(height).map_err(|_| "compressed JPEG height exceeds 65535".to_owned())?;
    let mut output = Vec::new();
    let mut encoder = JpegEncoder::new(&mut output, quality);
    encoder.set_optimized_huffman_tables(true);
    encoder
        .encode(pixels, width, height, color.jpeg_color())
        .map_err(|error| format!("failed to encode JPEG: {error}"))?;
    Ok(output)
}

/// Rewrite safe JPEG or Flate XObjects when the encoded result is smaller.
pub(crate) fn compress_images(
    doc: &mut Document,
    usages: &[ImageUsage],
    target_dpi: Option<f64>,
    quality: u8,
) -> Result<CompressionResult, String> {
    let considered =
        u32::try_from(usages.len()).map_err(|_| "image count overflowed".to_owned())?;
    let mask_ids = mask_references(doc, usages);
    let mut rewritten = 0u32;
    let mut bytes_before = 0u64;
    let mut bytes_after = 0u64;
    let mut total_pixels = 0u64;

    for usage in usages {
        let (target_width, target_height, previous_quality, source_len, encoded) = {
            let Some(candidate) = is_safe_image_stream(doc, *usage, &mask_ids) else {
                continue;
            };
            let target_width = target_dimension(candidate.width, usage.min_dpi_x, target_dpi);
            let target_height = target_dimension(candidate.height, usage.min_dpi_y, target_dpi);
            if (target_width, target_height) == (candidate.width, candidate.height)
                && candidate
                    .previous_quality
                    .is_some_and(|previous| previous <= quality)
            {
                continue;
            }
            let image_pixels = u64::from(candidate.width) * u64::from(candidate.height);
            if image_pixels > MAX_IMAGE_PIXELS {
                continue;
            }
            total_pixels = total_pixels
                .checked_add(image_pixels)
                .ok_or_else(|| "image compression pixel count overflowed".to_owned())?;
            if total_pixels > MAX_TOTAL_IMAGE_PIXELS {
                return Err("image compression exceeds the 250000000-pixel safety limit".to_owned());
            }
            let pixels = decode_pixels(&candidate)?;
            let pixels = resize_pixels(
                pixels,
                (candidate.width, candidate.height),
                (target_width, target_height),
                candidate.color,
            )?;
            let encoded = encode_jpeg(
                &pixels,
                target_width,
                target_height,
                candidate.color,
                quality,
            )?;
            let source_len = candidate.source.encoded_len();
            if encoded.len() >= source_len {
                continue;
            }
            (
                target_width,
                target_height,
                candidate.previous_quality,
                source_len,
                encoded,
            )
        };

        let stream = doc
            .get_object_mut(usage.object_id)
            .map_err(|error| error.to_string())?
            .as_stream_mut()
            .map_err(|error| error.to_string())?;
        stream.dict.set("Width", i64::from(target_width));
        stream.dict.set("Height", i64::from(target_height));
        stream.dict.set("BitsPerComponent", 8);
        stream.dict.set("Filter", "DCTDecode");
        stream.dict.remove(b"DecodeParms");
        stream.dict.remove(b"DL");
        stream.dict.set(
            "PylopdfQuality",
            i64::from(previous_quality.map_or(quality, |previous| previous.min(quality))),
        );
        stream.set_content(encoded);
        stream.allows_compression = false;
        stream.start_position = None;

        rewritten += 1;
        bytes_before += source_len as u64;
        bytes_after += stream.content.len() as u64;
    }

    Ok((
        considered,
        rewritten,
        considered - rewritten,
        bytes_before,
        bytes_after,
    ))
}
