use pyo3::prelude::*;
mod document;
use document::_Document;

#[pymodule]
fn pylopdf_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<_Document>()?;
    Ok(())
}
