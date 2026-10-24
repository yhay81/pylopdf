//! Pixel maps: gray or RGB samples, with or without straight alpha.
//!
//! Every wheel, the abi3-py311 wheels included, exposes the storage as a
//! read-only, zero-copy buffer shaped `(height, width)` for one channel and
//! `(height, width, channels)` otherwise, so `numpy.asarray(pixmap)` and
//! `memoryview(pixmap)` read the pixels without a copy and `bytes(pixmap)`
//! copies them once.

use jpeg_encoder::{
    ColorType as JpegColorType, Encoder as JpegEncoder, PixelDensity, PixelDensityUnit,
};
use pyo3::buffer::PyBuffer;
use pyo3::exceptions::{PyBufferError, PyTypeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyInt, PyTuple};
use std::{
    ffi::{CString, c_int, c_void},
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    ptr,
    sync::Arc,
};

use crate::document::{LimitError, PdfError};
use crate::extract::DEFAULT_IMAGE_RESOLUTION;

const TEMPORARY_PATH_ATTEMPTS: usize = 100;
const MAX_SYMLINK_DEPTH: usize = 40;
const DEFAULT_MAX_OUTPUT_SIZE: usize = 64 * 1024 * 1024;
const DEFAULT_QUALITY: u8 = 95;
/// The largest side of a Pixmap, as of a rendered page.
const MAX_SIDE: usize = u16::MAX as usize;
/// The largest resolution, the largest density that JPEG's JFIF header holds.
pub(crate) const MAX_RESOLUTION: u32 = u16::MAX as u32;
/// Metres per inch, for PNG's pixels per metre.
const METRES_PER_INCH: f64 = 0.0254;

/// A horizontal and vertical resolution in dots per inch, each from 1
/// through [`MAX_RESOLUTION`].
pub(crate) type Resolution = (u32, u32);

/// The resolution of a Pixmap that states none, as pymupdf and MuPDF report.
pub(crate) const DEFAULT_RESOLUTION: Resolution =
    (DEFAULT_IMAGE_RESOLUTION, DEFAULT_IMAGE_RESOLUTION);

/// Read a `(x, y)` resolution of two integers from 1 through 65,535.
fn extract_resolution(value: &Bound<'_, PyAny>) -> PyResult<Resolution> {
    let message = "resolution must be a tuple of two integers from 1 through 65535";
    let tuple = value
        .cast::<PyTuple>()
        .map_err(|_| PyTypeError::new_err(message))?;
    if tuple.len() != 2 {
        return Err(PyValueError::new_err(message));
    }
    let mut axes = [0_u32; 2];
    for (axis, item) in axes.iter_mut().zip(tuple.iter()) {
        if item.is_instance_of::<PyBool>() || !item.is_instance_of::<PyInt>() {
            return Err(PyTypeError::new_err(message));
        }
        *axis = item
            .extract::<u32>()
            .ok()
            .filter(|value| (1..=MAX_RESOLUTION).contains(value))
            .ok_or_else(|| PyValueError::new_err(message))?;
    }
    Ok((axes[0], axes[1]))
}

fn save_error(path: &Path, error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!(
        "failed to save the image to {}: {error}",
        path.display()
    ))
}

fn extract_max_size(value: &Bound<'_, PyAny>) -> PyResult<Option<usize>> {
    if value.is_none() {
        return Ok(None);
    }
    if value.is_instance_of::<PyBool>() || !value.is_instance_of::<PyInt>() {
        return Err(PyTypeError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    let size = value
        .extract::<usize>()
        .map_err(|_| PyValueError::new_err("max_size must be a positive integer or None"))?;
    if size == 0 {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    Ok(Some(size))
}

/// Read a JPEG quality: an integer from 1 through 100.
pub(crate) fn extract_quality(value: &Bound<'_, PyAny>) -> PyResult<u8> {
    if value.is_instance_of::<PyBool>() || !value.is_instance_of::<PyInt>() {
        return Err(PyTypeError::new_err(
            "quality must be an integer from 1 through 100",
        ));
    }
    value
        .extract::<u8>()
        .ok()
        .filter(|quality| (1..=100).contains(quality))
        .ok_or_else(|| PyValueError::new_err("quality must be an integer from 1 through 100"))
}

fn resolve_final_symlink(path: &Path) -> io::Result<PathBuf> {
    let mut resolved = path.to_path_buf();
    for _ in 0..MAX_SYMLINK_DEPTH {
        match fs::symlink_metadata(&resolved) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                let destination = fs::read_link(&resolved)?;
                resolved = if destination.is_absolute() {
                    destination
                } else {
                    resolved
                        .parent()
                        .unwrap_or_else(|| Path::new("."))
                        .join(destination)
                };
            }
            Ok(_) => return Ok(resolved),
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(resolved),
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::InvalidInput,
        "too many levels of symbolic links",
    ))
}

fn temporary_sibling(target: &Path) -> io::Result<(PathBuf, File)> {
    let parent = target.parent().unwrap_or_else(|| Path::new("."));
    for _ in 0..TEMPORARY_PATH_ATTEMPTS {
        let mut random = [0_u8; 16];
        getrandom::fill(&mut random)?;
        let mut encoded = [0_u8; 32];
        const HEX: &[u8; 16] = b"0123456789abcdef";
        for (index, byte) in random.iter().copied().enumerate() {
            encoded[index * 2] = HEX[usize::from(byte >> 4)];
            encoded[index * 2 + 1] = HEX[usize::from(byte & 0x0f)];
        }
        let suffix = std::str::from_utf8(&encoded).expect("hex digits are valid UTF-8");
        let path = parent.join(format!(".pylopdf-{suffix}.tmp"));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        format!(
            "failed to create a unique temporary output beside {}",
            target.display()
        ),
    ))
}

/// How a pixel's bytes are laid out: one gray or three RGB components, then
/// an optional straight alpha byte.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Layout {
    pub(crate) gray: bool,
    pub(crate) alpha: bool,
}

impl Layout {
    /// The layout with `channels` bytes per pixel: 1 gray, 2 gray and alpha,
    /// 3 RGB, or 4 RGB and alpha.
    pub(crate) fn of_channels(channels: usize) -> Option<Self> {
        match channels {
            1 => Some(Self {
                gray: true,
                alpha: false,
            }),
            2 => Some(Self {
                gray: true,
                alpha: true,
            }),
            3 => Some(Self {
                gray: false,
                alpha: false,
            }),
            4 => Some(Self {
                gray: false,
                alpha: true,
            }),
            _ => None,
        }
    }

    /// Bytes per pixel.
    pub(crate) fn channels(self) -> u32 {
        (if self.gray { 1 } else { 3 }) + u32::from(self.alpha)
    }

    fn png_color(self) -> png::ColorType {
        match (self.gray, self.alpha) {
            (true, false) => png::ColorType::Grayscale,
            (true, true) => png::ColorType::GrayscaleAlpha,
            (false, false) => png::ColorType::Rgb,
            (false, true) => png::ColorType::Rgba,
        }
    }

    fn colorspace(self) -> &'static str {
        if self.gray { "GRAY" } else { "RGB" }
    }
}

/// An encoded image format and its options.
#[derive(Clone, Copy, Debug)]
pub(crate) enum Encoding {
    Png,
    Jpeg { quality: u8 },
}

impl Encoding {
    /// Parse a public format name for samples of `layout`; JPEG has no alpha.
    pub(crate) fn parse(format: &str, quality: u8, layout: Layout) -> PyResult<Self> {
        match format {
            "png" => Ok(Self::Png),
            "jpeg" if layout.alpha => Err(PyValueError::new_err(
                "JPEG has no alpha channel; render without alpha or write PNG",
            )),
            "jpeg" => Ok(Self::Jpeg { quality }),
            _ => Err(PyValueError::new_err(format!(
                "format must be 'png' or 'jpeg': {format:?}"
            ))),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Png => "PNG",
            Self::Jpeg { .. } => "JPEG",
        }
    }
}

/// Write `width` by `height` samples in `layout` to `output` as `encoding`,
/// stating `resolution` in PNG's `pHYs` chunk or JPEG's JFIF density.
///
/// PNG uses fast compression, which keeps encoding from dominating render
/// time; recompress externally when a smaller file matters more.
pub(crate) fn write_image<W: Write>(
    output: W,
    width: u32,
    height: u32,
    layout: Layout,
    data: &[u8],
    encoding: Encoding,
    resolution: Resolution,
) -> Result<(), String> {
    match encoding {
        Encoding::Png => {
            let mut encoder = png::Encoder::new(output, width, height);
            encoder.set_color(layout.png_color());
            encoder.set_depth(png::BitDepth::Eight);
            encoder.set_compression(png::Compression::Fast);
            // Every resolution up to 65,535 dpi round-trips through whole
            // pixels per metre.
            let per_metre = |dpi: u32| (f64::from(dpi) / METRES_PER_INCH).round() as u32;
            encoder.set_pixel_dims(Some(png::PixelDimensions {
                xppu: per_metre(resolution.0),
                yppu: per_metre(resolution.1),
                unit: png::Unit::Meter,
            }));
            let mut writer = encoder.write_header().map_err(|error| error.to_string())?;
            writer
                .write_image_data(data)
                .and_then(|()| writer.finish())
                .map_err(|error| error.to_string())
        }
        Encoding::Jpeg { quality } => {
            let width = u16::try_from(width).map_err(|_| "JPEG width exceeds 65535".to_owned())?;
            let height =
                u16::try_from(height).map_err(|_| "JPEG height exceeds 65535".to_owned())?;
            let color = if layout.gray {
                JpegColorType::Luma
            } else {
                JpegColorType::Rgb
            };
            let mut encoder = JpegEncoder::new(output, quality);
            // Resolutions never exceed the JFIF header's u16 densities.
            encoder.set_density(PixelDensity {
                density: (resolution.0 as u16, resolution.1 as u16),
                unit: PixelDensityUnit::Inches,
            });
            encoder
                .encode(data, width, height, color)
                .map_err(|error| error.to_string())
        }
    }
}

/// Why bounded encoding stopped.
pub(crate) enum EncodeFailure {
    OutputLimit,
    Allocation(String),
    Encoding(String),
}

/// Encode samples without retaining output beyond `max_size`.
pub(crate) fn encode_bounded(
    width: u32,
    height: u32,
    layout: Layout,
    data: &[u8],
    encoding: Encoding,
    resolution: Resolution,
    max_size: Option<usize>,
) -> Result<Vec<u8>, EncodeFailure> {
    let mut output = crate::extract::BoundedOutput::new(max_size);
    let result = write_image(
        &mut output,
        width,
        height,
        layout,
        data,
        encoding,
        resolution,
    );
    if output.exceeded {
        return Err(EncodeFailure::OutputLimit);
    }
    if let Some(error) = output.allocation_error.take() {
        return Err(EncodeFailure::Allocation(error));
    }
    result.map_err(EncodeFailure::Encoding)?;
    Ok(output.bytes)
}

/// Replace RGB, or straight-alpha RGBA, samples with gray, or gray and
/// alpha, samples in place, with ITU-R BT.601 luma weights.
pub(crate) fn to_gray_in_place(data: &mut Vec<u8>, alpha: bool) {
    let source = if alpha { 4 } else { 3 };
    let target = if alpha { 2 } else { 1 };
    let pixels = data.len() / source;
    for index in 0..pixels {
        let pixel = &data[index * source..index * source + source];
        let luma =
            (77 * u32::from(pixel[0]) + 150 * u32::from(pixel[1]) + 29 * u32::from(pixel[2]) + 128)
                >> 8;
        let alpha_byte = if alpha { pixel[3] } else { 0 };
        data[index * target] = luma as u8;
        if alpha {
            data[index * target + 1] = alpha_byte;
        }
    }
    data.truncate(pixels * target);
}

/// Return straight-alpha RGBA8 samples for samples in `layout`, in one
/// fallible copy.
pub(crate) fn expand_to_rgba(layout: Layout, data: &[u8]) -> Result<Vec<u8>, String> {
    let channels = layout.channels() as usize;
    let pixels = data.len() / channels;
    let mut rgba = Vec::new();
    rgba.try_reserve_exact(pixels * 4)
        .map_err(|error| format!("failed to expand the Pixmap to RGBA: {error}"))?;
    for pixel in data.chunks_exact(channels) {
        let alpha = if layout.alpha {
            pixel[channels - 1]
        } else {
            u8::MAX
        };
        if layout.gray {
            rgba.extend_from_slice(&[pixel[0], pixel[0], pixel[0], alpha]);
        } else {
            rgba.extend_from_slice(&[pixel[0], pixel[1], pixel[2], alpha]);
        }
    }
    Ok(rgba)
}

/// The image format that a file name's extension names.
fn format_of(path: &Path) -> Option<&'static str> {
    let extension = path.extension()?.to_str()?.to_ascii_lowercase();
    match extension.as_str() {
        "png" => Some("png"),
        "jpg" | "jpeg" => Some("jpeg"),
        _ => None,
    }
}

/// Pixel map of a rendered page or image.
///
/// Data is row-major gray or RGB samples, each pixel followed by a straight,
/// non-premultiplied alpha byte when the Pixmap has alpha.
#[pyclass(frozen, module = "pylopdf.pylopdf_core")]
pub struct Pixmap {
    pub(crate) width: u32,
    pub(crate) height: u32,
    pub(crate) layout: Layout,
    /// Row-major samples, `layout.channels()` bytes per pixel.
    pub(crate) data: Arc<Vec<u8>>,
    /// Dots per inch, written into encoded images.
    pub(crate) resolution: Resolution,
}

impl Pixmap {
    fn encode(&self, encoding: Encoding, max_size: Option<usize>) -> PyResult<Vec<u8>> {
        match encode_bounded(
            self.width,
            self.height,
            self.layout,
            &self.data,
            encoding,
            self.resolution,
            max_size,
        ) {
            Ok(bytes) => Ok(bytes),
            Err(EncodeFailure::OutputLimit) => {
                let limit = max_size.expect("output can exceed only a configured limit");
                Err(LimitError::new_err((
                    "pixmap_output_size",
                    format!(
                        "the encoded {} exceeds the {limit}-byte output limit",
                        encoding.name()
                    ),
                )))
            }
            Err(EncodeFailure::Allocation(error)) => Err(PdfError::new_err(error)),
            Err(EncodeFailure::Encoding(error)) => Err(PdfError::new_err(format!(
                "failed to encode {}: {error}",
                encoding.name()
            ))),
        }
    }
}

#[pymethods]
impl Pixmap {
    /// Width in pixels.
    #[getter]
    fn width(&self) -> u32 {
        self.width
    }

    /// Height in pixels.
    #[getter]
    fn height(&self) -> u32 {
        self.height
    }

    /// Bytes per pixel: 1 gray, 2 gray and alpha, 3 RGB, or 4 RGB and alpha.
    #[getter]
    fn channels(&self) -> u32 {
        self.layout.channels()
    }

    /// Whether each pixel ends with a straight alpha byte.
    #[getter]
    fn alpha(&self) -> bool {
        self.layout.alpha
    }

    /// The color space of the samples, `Colorspace.GRAY` or `Colorspace.RGB`.
    #[getter]
    fn colorspace<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        py.import("pylopdf._pixmap")?
            .getattr("Colorspace")?
            .getattr(self.layout.colorspace())
    }

    /// Bytes per row: width × channels.
    #[getter]
    fn stride(&self) -> u32 {
        self.width * self.layout.channels()
    }

    /// The horizontal and vertical resolution in dots per inch.
    ///
    /// A rendered page states the resolution it was rendered at, and other
    /// Pixmaps 96 unless told otherwise. PNG and JPEG output records it.
    #[getter]
    fn resolution(&self) -> Resolution {
        self.resolution
    }

    /// Return a Pixmap with a copy of the pixels of `array`.
    ///
    /// `array` supports the buffer protocol with unsigned bytes, such as a
    /// `uint8` NumPy array, shaped `(height, width)` for gray or
    /// `(height, width, channels)` with 1 to 4 channels: gray, gray and
    /// alpha, RGB, or RGB and alpha. Alpha is straight, not premultiplied.
    ///
    /// `resolution` is an `(x, y)` tuple of dots per inch from 1 through
    /// 65,535. It defaults to 96, or to the resolution of a Pixmap passed as
    /// `array`, whose pixels the new Pixmap then shares without a copy.
    #[staticmethod]
    #[pyo3(signature = (array, /, *, resolution=None))]
    fn from_array(
        py: Python<'_>,
        array: &Bound<'_, PyAny>,
        resolution: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let resolution = resolution.map(extract_resolution).transpose()?;
        if let Ok(pixmap) = array.cast::<Self>() {
            let pixmap = pixmap.get();
            return Ok(Self {
                width: pixmap.width,
                height: pixmap.height,
                layout: pixmap.layout,
                data: Arc::clone(&pixmap.data),
                resolution: resolution.unwrap_or(pixmap.resolution),
            });
        }
        let buffer = PyBuffer::<u8>::get(array).map_err(|_| {
            PyTypeError::new_err(
                "from_array needs a buffer of unsigned bytes, such as a uint8 NumPy array",
            )
        })?;
        let (height, width, channels) = match *buffer.shape() {
            [height, width] => (height, width, 1),
            [height, width, channels] => (height, width, channels),
            _ => {
                return Err(PyValueError::new_err(
                    "from_array needs an array shaped (height, width) or (height, width, channels)",
                ));
            }
        };
        let layout = Layout::of_channels(channels).ok_or_else(|| {
            PyValueError::new_err(format!("from_array needs 1 to 4 channels, not {channels}"))
        })?;
        if width == 0 || height == 0 || width > MAX_SIDE || height > MAX_SIDE {
            return Err(PyValueError::new_err(format!(
                "a Pixmap is 1 to {MAX_SIDE} pixels a side, not {width}x{height}"
            )));
        }
        let data = buffer.to_vec(py)?;
        Ok(Self {
            width: width as u32,
            height: height as u32,
            layout,
            data: Arc::new(data),
            resolution: resolution.unwrap_or(DEFAULT_RESOLUTION),
        })
    }

    /// Encode the pixels as PNG or JPEG.
    ///
    /// `quality` (1 through 100) applies to JPEG, which has no alpha.
    /// `max_size` caps the encoded bytes and defaults to 64 MiB; `None` opts
    /// out.
    #[pyo3(
        signature = (format="png", *, quality=DEFAULT_QUALITY, max_size=Some(DEFAULT_MAX_OUTPUT_SIZE)),
        text_signature = "($self, format='png', /, *, quality=95, max_size=67108864)"
    )]
    fn to_bytes(
        &self,
        py: Python<'_>,
        format: &str,
        #[pyo3(from_py_with = extract_quality)] quality: u8,
        #[pyo3(from_py_with = extract_max_size)] max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        let encoding = Encoding::parse(format, quality, self.layout)?;
        py.detach(|| self.encode(encoding, max_size))
    }

    /// Encode the pixels and failure-atomically replace `path`.
    ///
    /// `format` is `"png"` or `"jpeg"`, else the extension of `path` names
    /// it: `.png`, `.jpg`, or `.jpeg`.
    #[pyo3(
        signature = (path, /, *, format=None, quality=DEFAULT_QUALITY),
        text_signature = "($self, path, /, *, format=None, quality=95)"
    )]
    fn save(
        &self,
        py: Python<'_>,
        path: PathBuf,
        format: Option<&str>,
        #[pyo3(from_py_with = extract_quality)] quality: u8,
    ) -> PyResult<()> {
        let format = match format {
            Some(format) => format,
            None => format_of(&path).ok_or_else(|| {
                PyValueError::new_err(format!(
                    "cannot tell the image format of {}; pass format='png' or format='jpeg'",
                    path.display()
                ))
            })?,
        };
        let encoding = Encoding::parse(format, quality, self.layout)?;
        py.detach(|| {
            let target = resolve_final_symlink(&path).map_err(|error| save_error(&path, error))?;
            let permissions = fs::metadata(&target)
                .ok()
                .filter(|metadata| metadata.is_file())
                .map(|metadata| metadata.permissions());
            let (temporary, mut output) =
                temporary_sibling(&target).map_err(|error| save_error(&path, error))?;

            // Restrict the sibling before writing any image data. Keep the
            // final rename: ReplaceFileW can remove the original on error.
            #[cfg(windows)]
            let prepared = if permissions.is_some() {
                crate::windows_permissions::copy_dacl(&target, &temporary)
                    .map_err(|error| save_error(&path, error))
            } else {
                Ok(())
            };
            #[cfg(not(windows))]
            let prepared: PyResult<()> = Ok(());
            let result = prepared.and_then(|()| {
                write_image(
                    &mut output,
                    self.width,
                    self.height,
                    self.layout,
                    &self.data,
                    encoding,
                    self.resolution,
                )
                .map_err(|error| save_error(&path, error))
            });
            drop(output);
            let result = result.and_then(|()| {
                if let Some(permissions) = permissions {
                    fs::set_permissions(&temporary, permissions)
                        .map_err(|error| save_error(&path, error))?;
                }
                fs::rename(&temporary, &target).map_err(|error| save_error(&path, error))
            });
            if result.is_err() {
                let _ = fs::remove_file(&temporary);
            }
            result
        })
    }

    fn __repr__(&self) -> String {
        let colorspace = if self.layout.gray { "gray" } else { "rgb" };
        let alpha = if self.layout.alpha { " alpha" } else { "" };
        format!(
            "<Pixmap {}x{} {colorspace}{alpha}>",
            self.width, self.height
        )
    }

    /// Expose immutable pixel storage without copying, shaped `(height,
    /// width)` for one channel and `(height, width, channels)` otherwise.
    unsafe fn __getbuffer__(
        slf: Bound<'_, Self>,
        view: *mut ffi::Py_buffer,
        flags: c_int,
    ) -> PyResult<()> {
        if view.is_null() {
            return Err(PyBufferError::new_err("buffer view is null"));
        }
        if (flags & ffi::PyBUF_WRITABLE) == ffi::PyBUF_WRITABLE {
            return Err(PyBufferError::new_err("Pixmap buffer is read-only"));
        }

        let owner = slf.clone().into_any();
        let pixmap = slf.get();
        let data = &pixmap.data;
        let length = isize::try_from(data.len())
            .map_err(|_| PyBufferError::new_err("Pixmap buffer is too large"))?;
        let channels = pixmap.layout.channels() as isize;
        let width = pixmap.width as isize;
        let height = pixmap.height as isize;
        let ndim: usize = if channels == 1 { 2 } else { 3 };
        // Shape and strides live in one allocation that `internal` owns
        // until `__releasebuffer__`.
        let mut dimensions = Box::new([height, width, channels, width * channels, channels, 1]);
        let dimensions_ptr = dimensions.as_mut_ptr();
        let shape_ptr = if (flags & ffi::PyBUF_ND) == ffi::PyBUF_ND {
            dimensions_ptr
        } else {
            ptr::null_mut()
        };
        let strides_ptr = if (flags & ffi::PyBUF_STRIDES) == ffi::PyBUF_STRIDES {
            // SAFETY: the array holds six entries; strides start at index 3.
            unsafe { dimensions_ptr.add(3) }
        } else {
            ptr::null_mut()
        };
        // SAFETY: `view` was checked for null. The frozen Pixmap cannot move or
        // mutate `data`, and the transferred owner reference keeps it alive.
        // Shape and strides point into the boxed array that `internal` owns.
        unsafe {
            (*view).obj = owner.into_ptr();
            (*view).buf = data.as_ptr().cast_mut().cast::<c_void>();
            (*view).len = length;
            (*view).readonly = 1;
            (*view).itemsize = 1;
            (*view).format = if (flags & ffi::PyBUF_FORMAT) == ffi::PyBUF_FORMAT {
                CString::new("B")
                    .expect("the static buffer format contains no null bytes")
                    .into_raw()
            } else {
                ptr::null_mut()
            };
            (*view).ndim = if shape_ptr.is_null() {
                1
            } else {
                ndim as c_int
            };
            (*view).shape = shape_ptr;
            (*view).strides = if shape_ptr.is_null() {
                ptr::null_mut()
            } else {
                strides_ptr
            };
            (*view).suboffsets = ptr::null_mut();
            (*view).internal = Box::into_raw(dimensions).cast::<c_void>();
        }
        Ok(())
    }

    /// Release the format string and dimensions allocated for one buffer view.
    unsafe fn __releasebuffer__(&self, view: *mut ffi::Py_buffer) {
        if !view.is_null() {
            let format = unsafe { (*view).format };
            if !format.is_null() {
                // SAFETY: `__getbuffer__` created every non-null format pointer
                // with `CString::into_raw`, once for this exact buffer view.
                drop(unsafe { CString::from_raw(format) });
            }
            let internal = unsafe { (*view).internal };
            if !internal.is_null() {
                // SAFETY: `__getbuffer__` stored a boxed `[isize; 6]` here,
                // once for this exact buffer view.
                drop(unsafe { Box::from_raw(internal.cast::<[isize; 6]>()) });
            }
        }
    }
}
