use pyo3::prelude::*;
mod document;
mod extract;
use document::{_Document, PasswordError, PdfError};

#[pymodule]
fn pylopdf_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<_Document>()?;
    m.add("PdfError", m.py().get_type::<PdfError>())?;
    m.add("PasswordError", m.py().get_type::<PasswordError>())?;
    Ok(())
}
