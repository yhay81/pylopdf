use pyo3::prelude::*;
mod document;
mod draw;
mod extract;
mod form;
#[cfg(feature = "generation")]
mod generate;
#[cfg(not(feature = "generation"))]
#[path = "generate_unsupported.rs"]
mod generate;
mod layout;
mod ocr;
#[cfg(feature = "ocr")]
mod ocr_engine;
#[cfg(not(feature = "ocr"))]
#[path = "ocr_engine_unsupported.rs"]
mod ocr_engine;
mod pixmap;
use document::{_Document, PasswordError, PdfError};
use ocr_engine::{_OcrEngine, OcrError};
use pixmap::Pixmap;

#[pymodule(gil_used = false)]
fn pylopdf_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<_Document>()?;
    m.add_class::<_OcrEngine>()?;
    m.add_class::<Pixmap>()?;
    m.add("PdfError", m.py().get_type::<PdfError>())?;
    m.add("PasswordError", m.py().get_type::<PasswordError>())?;
    m.add("OcrError", m.py().get_type::<OcrError>())?;
    Ok(())
}
