//! Explicit fallback for offline OCR in the lean Pyodide build.
//!
//! RTen targets native inference and requires a newer Rust compiler than the
//! Rust/Emscripten pair pinned by Pyodide 0.28.3. Keeping this class preserves
//! pylopdf's Python API shape while failing before model loading or rendering.

use pyo3::prelude::*;

use crate::document::PdfError;
use crate::pixmap::Pixmap;

pyo3::create_exception!(
    pylopdf,
    OcrError,
    PdfError,
    "OCR model loading or inference failed."
);

const UNSUPPORTED: &str = "offline OCR is unavailable in this Pyodide build; run OCR before uploading the PDF or use a native pylopdf[ocr] installation";

/// Placeholder matching the native OCR class exported by the extension.
#[pyclass(frozen, module = "pylopdf.pylopdf_core")]
pub struct _OcrEngine;

#[pymethods]
impl _OcrEngine {
    #[new]
    fn new(
        _detector_path: &str,
        _recognizer_path: &str,
        _dictionary_path: &str,
        _threads: usize,
    ) -> PyResult<Self> {
        Err(OcrError::new_err(UNSUPPORTED))
    }

    #[pyo3(signature = (_pixmap, *, tile_size=1408, overlap=192, min_confidence=0.5))]
    fn recognize_pixmap(
        &self,
        _pixmap: PyRef<'_, Pixmap>,
        tile_size: usize,
        overlap: usize,
        min_confidence: f32,
    ) -> PyResult<Vec<(f32, f32, f32, f32, String, f32)>> {
        let _ = (tile_size, overlap, min_confidence);
        Err(OcrError::new_err(UNSUPPORTED))
    }

    fn __repr__(&self) -> &'static str {
        "<pylopdf.OcrEngine unavailable in Pyodide>"
    }
}
