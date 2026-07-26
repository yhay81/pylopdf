//! Rendered straight-alpha RGBA8 pixel map.
//!
//! `np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 4)`
//! can be consumed by NumPy or PIL.
//!
//! Version-specific builds such as cp314t expose a read-only, zero-copy buffer.
//! The abi3-py310 wheel cannot use `Py_buffer`, which entered the stable ABI in
//! Python 3.11, so `samples` remains the portable one-copy fallback.

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyInt};
#[cfg(any(not(Py_LIMITED_API), Py_3_11))]
use pyo3::{exceptions::PyBufferError, ffi};
#[cfg(any(not(Py_LIMITED_API), Py_3_11))]
use std::{
    ffi::{CString, c_int, c_void},
    ptr,
};
use std::{
    fs::{self, File, OpenOptions},
    io,
    path::{Path, PathBuf},
    sync::Arc,
};

use crate::document::{LimitError, PdfError};

const TEMPORARY_PATH_ATTEMPTS: usize = 100;
const MAX_SYMLINK_DEPTH: usize = 40;
const DEFAULT_MAX_PNG_OUTPUT_SIZE: usize = 64 * 1024 * 1024;

fn save_error(path: &Path, error: impl std::fmt::Display) -> PyErr {
    PdfError::new_err(format!("failed to save PNG to {}: {error}", path.display()))
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

/// Pixel map for a rendered page.
///
/// Data is row-major RGBA8 with straight, non-premultiplied alpha.
#[pyclass(frozen, module = "pylopdf.pylopdf_core")]
pub struct Pixmap {
    pub(crate) width: u32,
    pub(crate) height: u32,
    /// Row-major straight-alpha RGBA8 data.
    pub(crate) data: Arc<Vec<u8>>,
}

impl Pixmap {
    fn encode_png(&self, max_size: Option<usize>) -> PyResult<Vec<u8>> {
        match crate::extract::encode_png_bounded(
            self.width,
            self.height,
            png::ColorType::Rgba,
            &self.data,
            png::Compression::Fast,
            max_size,
        ) {
            Ok(png) => Ok(png),
            Err(crate::extract::PngEncodeError::OutputLimit) => {
                let limit = max_size.expect("PNG output can exceed only a configured limit");
                Err(LimitError::new_err((
                    "pixmap_output_size",
                    format!("encoded Pixmap PNG exceeds the {limit}-byte output limit"),
                )))
            }
            Err(crate::extract::PngEncodeError::Encoding(error)) => {
                Err(PdfError::new_err(format!("failed to encode PNG: {error}")))
            }
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

    /// Components per pixel, always 4 for RGBA.
    #[getter]
    fn n(&self) -> u32 {
        4
    }

    /// Bytes per row: width × 4.
    #[getter]
    fn stride(&self) -> u32 {
        self.width * 4
    }

    /// Return row-major RGBA8 pixel data as bytes.
    #[getter]
    fn samples<'py>(&self, py: Python<'py>) -> Bound<'py, pyo3::types::PyBytes> {
        pyo3::types::PyBytes::new(py, &self.data)
    }

    /// Encode and return PNG bytes.
    ///
    /// Fast compression matches `render_page` and prioritizes speed over size.
    /// Recompress externally when a smaller PNG is required.
    #[pyo3(
        signature = (*, max_size=Some(DEFAULT_MAX_PNG_OUTPUT_SIZE)),
        text_signature = "($self, /, *, max_size=67108864)"
    )]
    fn tobytes(
        &self,
        py: Python<'_>,
        #[pyo3(from_py_with = extract_max_size)] max_size: Option<usize>,
    ) -> PyResult<Vec<u8>> {
        py.detach(|| self.encode_png(max_size))
    }

    /// Encode PNG data and failure-atomically replace a filesystem path.
    fn save(&self, py: Python<'_>, path: PathBuf) -> PyResult<()> {
        let is_png = path
            .extension()
            .and_then(|extension| extension.to_str())
            .is_some_and(|extension| extension.eq_ignore_ascii_case("png"));
        if !is_png {
            return Err(PdfError::new_err(
                "Pixmap.save requires a path with a .png extension",
            ));
        }
        py.detach(|| {
            let target = resolve_final_symlink(&path).map_err(|error| save_error(&path, error))?;
            let permissions = fs::metadata(&target)
                .ok()
                .filter(|metadata| metadata.is_file())
                .map(|metadata| metadata.permissions());
            let (temporary, mut output) =
                temporary_sibling(&target).map_err(|error| save_error(&path, error))?;

            let result = crate::extract::write_png(
                &mut output,
                self.width,
                self.height,
                png::ColorType::Rgba,
                &self.data,
                png::Compression::Fast,
            )
            .map_err(|error| save_error(&path, error));
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
        format!("<Pixmap {}x{} rgba>", self.width, self.height)
    }

    /// Expose immutable pixel storage without copying on version-specific builds.
    #[cfg(any(not(Py_LIMITED_API), Py_3_11))]
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
        let data = &slf.get().data;
        let length = isize::try_from(data.len())
            .map_err(|_| PyBufferError::new_err("Pixmap buffer is too large"))?;
        // SAFETY: `view` was checked for null. The frozen Pixmap cannot move or
        // mutate `data`, and the transferred owner reference keeps it alive.
        // Shape and stride point into the caller-owned Py_buffer itself.
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
            (*view).ndim = 1;
            (*view).shape = if (flags & ffi::PyBUF_ND) == ffi::PyBUF_ND {
                &raw mut (*view).len
            } else {
                ptr::null_mut()
            };
            (*view).strides = if (flags & ffi::PyBUF_STRIDES) == ffi::PyBUF_STRIDES {
                &raw mut (*view).itemsize
            } else {
                ptr::null_mut()
            };
            (*view).suboffsets = ptr::null_mut();
            (*view).internal = ptr::null_mut();
        }
        Ok(())
    }

    /// Release the optional format string allocated for one buffer view.
    #[cfg(any(not(Py_LIMITED_API), Py_3_11))]
    unsafe fn __releasebuffer__(&self, view: *mut ffi::Py_buffer) {
        if !view.is_null() {
            let format = unsafe { (*view).format };
            if !format.is_null() {
                // SAFETY: `__getbuffer__` created every non-null format pointer
                // with `CString::into_raw`, once for this exact buffer view.
                drop(unsafe { CString::from_raw(format) });
            }
        }
    }
}
