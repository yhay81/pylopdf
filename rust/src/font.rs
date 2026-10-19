//! OpenType faces that Python loads once and reuses for generation.
//!
//! `pylopdf.Font` wraps a `_Font`: shared font bytes plus the HarfRust font
//! that shapes them. `text_length` measures with
//! `generate::shaped_line_width`, the sum that places every glyph generation
//! draws, so a measured width is the drawn width. Drawing passes the same
//! bytes and collection index to the generation paths that `fontbuffer` uses.

use std::sync::Arc;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use read_fonts::TableProvider;
use read_fonts::tables::head::MacStyle;
use read_fonts::tables::os2::SelectionFlags;
use read_fonts::types::NameId;

use crate::bundled_font_cache::FontBytes;
use crate::document::{
    DEFAULT_MAX_FONT_INPUT_SIZE, DEFAULT_MAX_GENERATED_TEXT_SIZE, PdfError, load_generation_font,
    validate_font_input, validate_generated_text_input,
};
use crate::generate::{self, ShapingFont};

/// The OS/2 weight class of a face that has no OS/2 table: Regular.
const DEFAULT_WEIGHT_CLASS: u16 = 400;

/// One OpenType face prepared for shaping, measurement, and embedding.
#[pyclass(frozen, module = "pylopdf.pylopdf_core")]
pub struct _Font {
    data: FontBytes,
    index: u32,
    shaping: ShapingFont,
    postscript_name: Option<String>,
    family: Option<String>,
    weight: u16,
    italic: bool,
    ascent: f64,
    descent: f64,
}

impl _Font {
    /// Open a face and read the facts Python exposes.
    ///
    /// krilla must accept the face as well as HarfRust, so a font that
    /// generation could not embed fails here rather than at its first use.
    fn open(data: FontBytes, index: u32) -> PyResult<Self> {
        let shaping = generate::open_shaping_font(&data, index).map_err(PdfError::new_err)?;
        krilla::text::Font::new(Arc::clone(&data).into(), index).ok_or_else(|| {
            PdfError::new_err(format!(
                "font data or collection index {index} cannot be embedded"
            ))
        })?;
        let (ascent, descent) =
            generate::font_vertical_metrics(&shaping).map_err(PdfError::new_err)?;
        let tables = shaping.tables();
        let os2 = tables.os2().ok();
        let weight = os2
            .as_ref()
            .map_or(DEFAULT_WEIGHT_CLASS, |os2| os2.us_weight_class());
        let italic = os2.as_ref().is_some_and(|os2| {
            os2.fs_selection()
                .intersects(SelectionFlags::ITALIC | SelectionFlags::OBLIQUE)
        }) || tables
            .head()
            .is_ok_and(|head| head.mac_style().contains(MacStyle::ITALIC));
        let names = shaping.names();
        let name = |id: NameId| {
            names
                .get(id)
                .english_or_first()
                .map(|localized| localized.name().to_string())
                .filter(|text| !text.is_empty())
        };
        let postscript_name = name(NameId::POSTSCRIPT_NAME);
        let family = name(NameId::TYPOGRAPHIC_FAMILY_NAME).or_else(|| name(NameId::FAMILY_NAME));
        Ok(Self {
            data,
            index,
            shaping,
            postscript_name,
            family,
            weight,
            italic,
            ascent,
            descent,
        })
    }

    /// The shared bytes and collection index that generation embeds.
    pub(crate) fn source(&self) -> (FontBytes, u32) {
        (Arc::clone(&self.data), self.index)
    }
}

#[pymethods]
impl _Font {
    /// Open a face from bounded in-memory OpenType data.
    #[staticmethod]
    #[pyo3(signature = (data, index, max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE)))]
    fn from_bytes(
        py: Python<'_>,
        data: Vec<u8>,
        index: u32,
        max_font_size: Option<usize>,
    ) -> PyResult<Self> {
        validate_font_input(Some(&data), max_font_size)?;
        py.detach(|| Self::open(Arc::new(data), index))
    }

    /// Open a face from a bounded font file.
    ///
    /// `package_font` names the `(language, kind)` of a face Python selected
    /// from an installed font package, which shares its bytes through
    /// `bundled_font_cache`; other paths are read once for this face.
    #[staticmethod]
    #[pyo3(signature = (
        path,
        index,
        max_font_size=Some(DEFAULT_MAX_FONT_INPUT_SIZE),
        package_font=None
    ))]
    fn from_file(
        py: Python<'_>,
        path: &str,
        index: u32,
        max_font_size: Option<usize>,
        package_font: Option<(String, String)>,
    ) -> PyResult<Self> {
        let data = load_generation_font(py, path, package_font, max_font_size)?;
        py.detach(|| Self::open(data, index))
    }

    /// The PostScript name, or `None` when the face has none.
    #[getter]
    fn name(&self) -> Option<&str> {
        self.postscript_name.as_deref()
    }

    /// The typographic family name, falling back to the legacy family name.
    #[getter]
    fn family(&self) -> Option<&str> {
        self.family.as_deref()
    }

    /// The OS/2 weight class, 400 when the face has no OS/2 table.
    #[getter]
    fn weight(&self) -> u16 {
        self.weight
    }

    /// Whether OS/2 or `head` marks the face italic or oblique.
    #[getter]
    fn italic(&self) -> bool {
        self.italic
    }

    #[getter]
    fn units_per_em(&self) -> u16 {
        self.shaping.units_per_em()
    }

    /// Ascent as a positive fraction of the em.
    #[getter]
    fn ascender(&self) -> f64 {
        self.ascent
    }

    /// Descent as a negative fraction of the em.
    #[getter]
    fn descender(&self) -> f64 {
        -self.descent
    }

    /// Measure one line as generation draws it, in PDF units.
    #[pyo3(signature = (text, fontsize, max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)))]
    fn text_length(
        &self,
        py: Python<'_>,
        text: &str,
        fontsize: f64,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        if text.contains(['\n', '\r']) {
            return Err(PyValueError::new_err(
                "text_length measures one line; split text at line breaks first",
            ));
        }
        if !(fontsize.is_finite() && fontsize > 0.0) {
            return Err(PyValueError::new_err(format!(
                "fontsize must be a positive number: {fontsize}"
            )));
        }
        py.detach(|| generate::shaped_line_width(&self.shaping, text, fontsize))
            .map_err(PdfError::new_err)
    }

    /// Return whether the character map assigns a glyph to `codepoint`.
    fn has_glyph(&self, codepoint: u32) -> bool {
        self.shaping
            .charmap()
            .map_unicode(codepoint)
            .is_some_and(|glyph| glyph.to_u32() != 0)
    }
}
