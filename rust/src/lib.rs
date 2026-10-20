use pyo3::prelude::*;
mod acroform_import;
mod bundled_font_cache;
mod content;
mod content_guard;
mod content_scan;
mod document;
mod draw;
mod extract;
mod font;
mod form;
mod garbage;
mod generate;
mod generated_pdf;
mod graph_guard;
mod image_compression;
mod layout;
mod object_recovery;
mod object_stream_work;
mod ocr;
#[cfg(not(target_os = "emscripten"))]
mod ocr_engine;
#[cfg(target_os = "emscripten")]
#[path = "ocr_engine_emscripten.rs"]
mod ocr_engine;
mod page_cleanup;
mod pixmap;
mod placement_reuse;
mod postscript_scan;
mod prune;
mod read_depth;
mod recovery;
mod render_cache;
mod renumber;
mod shape;
mod structure_prune;
mod text_replace;
mod unallocated;
use document::{
    _Document, _decode_text_string, _encode_text_string, LimitError, PasswordError, PdfError,
    StaleObjectError,
};
use font::_Font;
use ocr_engine::{_OcrEngine, OcrError};
use pixmap::Pixmap;
use shape::_Shape;

#[pymodule(gil_used = false)]
fn pylopdf_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<_Document>()?;
    m.add_class::<_Font>()?;
    m.add_class::<_OcrEngine>()?;
    m.add_class::<Pixmap>()?;
    m.add_class::<_Shape>()?;
    m.add("PdfError", m.py().get_type::<PdfError>())?;
    m.add("LimitError", m.py().get_type::<LimitError>())?;
    m.add("PasswordError", m.py().get_type::<PasswordError>())?;
    m.add("OcrError", m.py().get_type::<OcrError>())?;
    m.add("StaleObjectError", m.py().get_type::<StaleObjectError>())?;
    m.add_function(wrap_pyfunction!(_decode_text_string, m)?)?;
    m.add_function(wrap_pyfunction!(_encode_text_string, m)?)?;
    Ok(())
}
