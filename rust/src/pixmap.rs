//! Rendered straight-alpha RGBA8 pixel map.
//!
//! `np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 4)`
//! can be consumed by NumPy or PIL.
//!
//! The buffer protocol is intentionally absent. `Py_buffer` entered the stable
//! ABI in Python 3.11 and is unavailable to abi3-py310, so `samples` performs
//! one copy. Reconsider after raising the abi3 floor or adding cp314t builds.

use pyo3::prelude::*;

use crate::document::PdfError;

/// Pixel map for a rendered page.
///
/// Data is row-major RGBA8 with straight, non-premultiplied alpha.
#[pyclass(module = "pylopdf.pylopdf_core")]
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
}
