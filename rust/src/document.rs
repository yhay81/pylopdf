use pyo3::prelude::*;
use lopdf::Document as LopdfDocument;

#[pyclass]
pub struct _Document(pub LopdfDocument);

#[pymethods]
impl _Document {
}
