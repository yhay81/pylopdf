use pyo3::prelude::*;
mod batch;
mod bundled_font_cache;
mod content_guard;
mod content_scan;
mod document;
mod draw;
mod extract;
mod font;
mod form;
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
mod postscript_scan;
mod prune;
mod recovery;
mod render_cache;
mod renumber;
mod text_replace;
mod unallocated;
use batch::_Batch;
use document::{_Document, LimitError, PasswordError, PdfError};
use font::_Font;
use ocr_engine::{_OcrEngine, OcrError};
use pixmap::Pixmap;

#[pymodule(gil_used = false)]
fn pylopdf_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<_Batch>()?;
    m.add_class::<_Document>()?;
    m.add_class::<_Font>()?;
    m.add_class::<_OcrEngine>()?;
    m.add_class::<Pixmap>()?;
    m.add("PdfError", m.py().get_type::<PdfError>())?;
    m.add("LimitError", m.py().get_type::<LimitError>())?;
    m.add("PasswordError", m.py().get_type::<PasswordError>())?;
    m.add("OcrError", m.py().get_type::<OcrError>())?;
    Ok(())
}
