//! Rendered straight-alpha RGBA8 pixel map.
//!
//! `np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 4)`
//! can be consumed by NumPy or PIL.
//!
//! Version-specific builds such as cp314t expose a read-only, zero-copy buffer.
//! The abi3-py310 wheel cannot use `Py_buffer`, which entered the stable ABI in
//! Python 3.11, so `samples` remains the portable one-copy fallback.

use pyo3::prelude::*;
#[cfg(any(not(Py_LIMITED_API), Py_3_11))]
use pyo3::{exceptions::PyBufferError, ffi};
#[cfg(any(not(Py_LIMITED_API), Py_3_11))]
use std::{
    ffi::{CString, c_int, c_void},
    ptr,
};

use crate::document::PdfError;

/// Pixel map for a rendered page.
///
/// Data is row-major RGBA8 with straight, non-premultiplied alpha.
#[pyclass(frozen, module = "pylopdf.pylopdf_core")]
pub struct Pixmap {
    pub(crate) width: u32,
    pub(crate) height: u32,
    /// Row-major straight-alpha RGBA8 data.
    pub(crate) data: Vec<u8>,
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
    fn tobytes(&self, py: Python<'_>) -> PyResult<Vec<u8>> {
        py.detach(|| {
            crate::extract::encode_png(
                self.width,
                self.height,
                png::ColorType::Rgba,
                &self.data,
                png::Compression::Fast,
            )
            .ok_or_else(|| PdfError::new_err("failed to encode PNG"))
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
