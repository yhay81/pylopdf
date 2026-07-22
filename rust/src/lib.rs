use pyo3::prelude::*;
mod document;
mod draw;
mod extract;
mod pixmap;
use document::{_Document, PasswordError, PdfError};
use pixmap::Pixmap;

#[pymodule]
fn pylopdf_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<_Document>()?;
    m.add_class::<Pixmap>()?;
    m.add("PdfError", m.py().get_type::<PdfError>())?;
    m.add("PasswordError", m.py().get_type::<PasswordError>())?;
    Ok(())
}
