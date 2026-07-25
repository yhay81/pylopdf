//! Explicit WebAssembly stub for the native RTen OCR engine.
//!
//! The independently distributed OCR model package and native inference are
//! outside the Emscripten compatibility contract. Keep the Python exception
//! and class shape available without linking the unused inference runtime.

use pyo3::prelude::*;

use crate::document::PdfError;
use crate::pixmap::Pixmap;

pyo3::create_exception!(
    pylopdf,
    OcrError,
    PdfError,
    "OCR model loading or inference failed."
);

type OcrTuple = (f32, f32, f32, f32, String, f32);

fn unavailable() -> PyErr {
    OcrError::new_err(
        "native OCR inference is unavailable on Emscripten; run OCR outside Wasm and use Page.insert_ocr_text_layer()",
    )
}

/// API-compatible placeholder that reports the documented runtime boundary.
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
        Err(unavailable())
    }

    #[pyo3(signature = (_pixmap, *, tile_size=1408, overlap=192, min_confidence=0.5, rotation=0))]
    fn recognize_pixmap(
        &self,
        _pixmap: PyRef<'_, Pixmap>,
        tile_size: usize,
        overlap: usize,
        min_confidence: f32,
        rotation: usize,
    ) -> PyResult<Vec<OcrTuple>> {
        let _ = (tile_size, overlap, min_confidence, rotation);
        Err(unavailable())
    }

    fn __repr__(&self) -> &'static str {
        "<pylopdf.OcrEngine unavailable on Emscripten>"
    }
}
