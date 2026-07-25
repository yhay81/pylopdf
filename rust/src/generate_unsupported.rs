//! Explicit fallbacks for the Pyodide build's embedded-font generation API.
//!
//! Pyodide 0.28.3 pins a Rust/Emscripten pair older than krilla supports.
//! Keeping these functions preserves pylopdf's Python API shape while making
//! the one unavailable capability fail clearly instead of disappearing.

const UNSUPPORTED: &str = "embedded-font PDF generation is unavailable in this Pyodide build; use a Standard 14 font or generate the PDF before uploading it";

#[allow(clippy::too_many_arguments)]
pub fn embedded_text_page(
    _page_size: (f64, f64),
    _point: (f64, f64),
    _lines: &[String],
    _font_data: Vec<u8>,
    _font_index: u32,
    _font_size: f64,
    _color: (f64, f64, f64),
) -> Result<Vec<u8>, String> {
    Err(UNSUPPORTED.to_owned())
}

#[allow(clippy::too_many_arguments)]
pub fn embedded_textbox_page(
    _page_size: (f64, f64),
    _rect: [f64; 4],
    _text: &str,
    _font_data: Vec<u8>,
    _font_index: u32,
    _font_size: f64,
    _line_height: f64,
    _align: u8,
    _color: (f64, f64, f64),
) -> Result<(Option<Vec<u8>>, f64), String> {
    Err(UNSUPPORTED.to_owned())
}

#[allow(clippy::too_many_arguments)]
pub fn embedded_widget_text_page(
    _page_size: (f64, f64),
    _rect: [f64; 4],
    _text: &str,
    _font_data: Vec<u8>,
    _font_index: u32,
    _multiline: bool,
    _align: u8,
    _color: (f64, f64, f64),
) -> Result<Vec<u8>, String> {
    Err(UNSUPPORTED.to_owned())
}
