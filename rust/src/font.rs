//! Faces that Python loads once and reuses for generation and measurement.
//!
//! `pylopdf.Font` wraps a `_Font`, which holds either an OpenType face or a
//! Standard 14 font. An OpenType face keeps a `generate::GenerationFace`:
//! its shared bytes and the HarfRust font that shapes them, at the instance
//! of a variable face that `with_weight` selected. `text_width` measures
//! with `generate::shaped_line_width`, the sum that places every glyph
//! generation draws, and `text_height` wraps text with the layout and line
//! placement of the canvas's `textbox`. A Standard 14 font is not embedded;
//! it measures with the Adobe AFM widths that Standard 14 text layout uses.
//!
//! The metrics of single characters come from the same sources: read-fonts'
//! glyph metrics of an OpenType face, at the instance HarfRust shapes, and
//! the AFM metrics of a Standard 14 font, whose font box is the URW box that
//! its ascender and descender come from. `glyph_name` and `glyph_text` read
//! the Adobe Glyph List through read-fonts' `agl` module.

use std::sync::Arc;

use harfrust::ShaperFont;
use pdf_base14_metrics::{Base14Font, CharacterMetric};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use read_fonts::TableProvider;
use read_fonts::ps::agl;
use read_fonts::tables::head::MacStyle;
use read_fonts::tables::os2::SelectionFlags;
use read_fonts::types::NameId;

use crate::document::{
    DEFAULT_MAX_FONT_INPUT_SIZE, DEFAULT_MAX_GENERATED_TEXT_SIZE, PdfError, generated_text_err,
    load_generation_font, validate_font_input, validate_generated_text_input,
    validate_generated_text_line_count,
};
use crate::draw;
use crate::generate::{self, GenerationFace, ShapingFont, WeightAxis};
use crate::layout::{LineMetrics, LinePlacement, MAX_GENERATED_TEXT_LINES};

/// The OS/2 weight class of a face that has no OS/2 table: Regular.
const DEFAULT_WEIGHT_CLASS: u16 = 400;
/// The weight class of a bold Standard 14 font.
const BOLD_WEIGHT_CLASS: u16 = 700;
/// The lightest weight class that counts as bold.
const MIN_BOLD_WEIGHT_CLASS: u16 = 600;
/// Standard 14 metrics are given in thousandths of the em.
const STANDARD_UNITS_PER_EM: u16 = 1000;

/// An embedded OpenType face prepared for shaping and measurement.
struct OpenTypeFace {
    generation: GenerationFace,
    weight_axis: Option<WeightAxis>,
    full_name: Option<String>,
    family: Option<String>,
    weight: u16,
    bold: bool,
    italic: bool,
    serif: bool,
    line_metrics: LineMetrics,
}

/// What a `_Font` draws with.
enum Face {
    OpenType(Box<OpenTypeFace>),
    /// A Standard 14 font and its PostScript name.
    Standard(Base14Font, &'static str),
}

/// One face prepared for measurement and drawing.
#[pyclass(frozen, module = "pylopdf.pylopdf_core")]
pub struct _Font {
    face: Face,
}

/// The code points below which a Microsoft symbol or Mac Roman `cmap`
/// subtable maps characters through a conversion that `Charmap::unicodes`
/// does not list, so `chars` tests each of them.
const CONVERTED_CODE_POINTS: u32 = 0x1_0000;

/// The PostScript names of the Standard 14 fonts.
const STANDARD_NAMES: [&str; 14] = [
    "Courier",
    "Courier-Oblique",
    "Courier-Bold",
    "Courier-BoldOblique",
    "Helvetica",
    "Helvetica-Oblique",
    "Helvetica-Bold",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Italic",
    "Times-Bold",
    "Times-BoldItalic",
    "Symbol",
    "ZapfDingbats",
];

/// Read a name record, preferring English.
fn name_record(font: &ShapingFont, id: NameId) -> Option<String> {
    font.names()
        .get(id)
        .and_then(|name| name.english_or_first())
        .map(|localized| localized.name().to_string())
        .filter(|text| !text.is_empty())
}

/// Classify a face as serif from its PANOSE serif style, then its family name.
fn is_serif_face(font: &ShapingFont, family: Option<&str>) -> bool {
    // PANOSE Latin Text (family kind 2) records the serif style in byte 1:
    // 2-10 are serif styles and 11-13 sans serif ones.
    if let Ok(os2) = font.tables().os2()
        && let [2, serif_style, ..] = *os2.panose_10()
    {
        match serif_style {
            2..=10 => return true,
            11..=13 => return false,
            _ => {}
        }
    }
    family.is_some_and(|family| {
        let lowered = family.to_ascii_lowercase();
        lowered.contains("serif") && !lowered.contains("sans")
            || ["mincho", "ming", "song", "naskh", "times"]
                .iter()
                .any(|marker| lowered.contains(marker))
    })
}

impl _Font {
    /// Open an OpenType face and read the facts Python exposes.
    ///
    /// krilla must accept the face as well as HarfRust, so a font that
    /// generation could not embed fails here rather than at its first use.
    fn open(generation: GenerationFace) -> PyResult<Self> {
        let shaping = generation.shaping_font();
        generation.embedding_font().map_err(|_| {
            PdfError::new_err(format!(
                "font data or collection index {} cannot be embedded",
                generation.source().1
            ))
        })?;
        let line_metrics = generate::font_line_metrics(shaping).map_err(PdfError::new_err)?;
        let tables = shaping.tables();
        let os2 = tables.os2().ok();
        let selection = os2.as_ref().map(|os2| os2.fs_selection());
        let mac_style = tables.head().ok().map(|head| head.mac_style());
        // OS/2 and head describe the default instance of a variable face; an
        // instance that `with_weight` selected weighs its axis value.
        let (weight, bold) = match generation.weight() {
            Some(weight) => (weight, weight >= MIN_BOLD_WEIGHT_CLASS),
            None => {
                let weight = os2
                    .as_ref()
                    .map_or(DEFAULT_WEIGHT_CLASS, |os2| os2.us_weight_class());
                let bold = weight >= MIN_BOLD_WEIGHT_CLASS
                    || selection.is_some_and(|flags| flags.contains(SelectionFlags::BOLD))
                    || mac_style.is_some_and(|style| style.contains(MacStyle::BOLD));
                (weight, bold)
            }
        };
        let italic = selection.is_some_and(|flags| {
            flags.intersects(SelectionFlags::ITALIC | SelectionFlags::OBLIQUE)
        }) || mac_style.is_some_and(|style| style.contains(MacStyle::ITALIC));
        let family = name_record(shaping, NameId::TYPOGRAPHIC_FAMILY_NAME)
            .or_else(|| name_record(shaping, NameId::FAMILY_NAME));
        // Name a face as FreeType and pymupdf do: family, then style. An
        // instance away from the default location takes its own style.
        let style = generation
            .instance_style_name()
            .map(str::to_owned)
            .or_else(|| name_record(shaping, NameId::TYPOGRAPHIC_SUBFAMILY_NAME))
            .or_else(|| name_record(shaping, NameId::SUBFAMILY_NAME));
        let full_name = match (&family, style) {
            (Some(family), Some(style)) => Some(format!("{family} {style}")),
            _ => name_record(shaping, NameId::FULL_NAME)
                .or_else(|| name_record(shaping, NameId::POSTSCRIPT_NAME)),
        };
        let serif = is_serif_face(shaping, family.as_deref());
        let weight_axis = generate::weight_axis(shaping);
        Ok(Self {
            face: Face::OpenType(Box::new(OpenTypeFace {
                generation,
                weight_axis,
                full_name,
                family,
                weight,
                bold,
                italic,
                serif,
                line_metrics,
            })),
        })
    }

    /// The face that generation embeds, or `None` for a Standard 14 font,
    /// which is drawn without embedding.
    pub(crate) fn face(&self) -> Option<GenerationFace> {
        match &self.face {
            Face::OpenType(face) => Some(face.generation.clone()),
            Face::Standard(..) => None,
        }
    }

    /// The font program and face index of an OpenType face, shared rather
    /// than copied, or `None` for a Standard 14 font.
    pub(crate) fn program(&self) -> Option<(Arc<Vec<u8>>, u32)> {
        match &self.face {
            Face::OpenType(face) => Some(face.generation.program()),
            Face::Standard(..) => None,
        }
    }

    /// The ascender and descender that text and textboxes place lines with:
    /// what FreeType reads for an OpenType face, and the bounding box of the
    /// URW font that MuPDF draws a Standard 14 font with.
    pub(crate) fn line_metrics(&self) -> LineMetrics {
        match &self.face {
            Face::OpenType(face) => face.line_metrics,
            Face::Standard(font, _) => draw::standard_line_metrics(*font),
        }
    }

    /// What drawing needs from this face.
    pub(crate) fn draw_face(&self) -> DrawFace<'_> {
        match &self.face {
            Face::OpenType(face) => DrawFace::Embedded(&face.generation),
            Face::Standard(_, name) => DrawFace::Standard(name),
        }
    }

    /// The glyph that an OpenType face draws for `character` alone, or the
    /// AFM metrics of a Standard 14 font's glyph, refused when the face has
    /// no glyph for it, as `has_glyph` decides.
    fn char_glyph(&self, character: char) -> PyResult<CharGlyph<'_>> {
        let missing = || {
            PdfError::new_err(format!(
                "the font has no glyph for {character:?} (U+{:04X})",
                u32::from(character)
            ))
        };
        match &self.face {
            Face::OpenType(face) => {
                let font = face.generation.shaping_font();
                font.charmap()
                    .map_unicode(character)
                    .map(|glyph| CharGlyph::OpenType(font, glyph))
                    .ok_or_else(missing)
            }
            Face::Standard(font, _) => draw::standard_char_metric(*font, character)
                .map(CharGlyph::Standard)
                .ok_or_else(missing),
        }
    }
}

/// The glyph of one character: an OpenType glyph of a face at its instance,
/// or the AFM metrics of a Standard 14 glyph.
enum CharGlyph<'a> {
    OpenType(&'a ShapingFont, read_fonts::types::GlyphId),
    Standard(&'static CharacterMetric<'static>),
}

impl CharGlyph<'_> {
    /// The advance in fractions of the em. An OpenType glyph takes the
    /// advance that HarfRust gives it, which rounds the variation deltas of
    /// an instance as shaping and drawing do; `shaper` is a `ShaperFont` of
    /// the glyph's font, made once per call.
    fn advance(&self, shaper: Option<&ShaperFont<'_, '_>>) -> f64 {
        match self {
            Self::OpenType(font, glyph) => {
                let advance = match shaper {
                    Some(shaper) => shaper.glyph_h_advance(*glyph),
                    None => ShaperFont::new(font).glyph_h_advance(*glyph),
                };
                f64::from(advance) / f64::from(font.units_per_em())
            }
            Self::Standard(metric) => f64::from(metric.width_x) / 1000.0,
        }
    }

    /// The ink box as `(x0, y0, x1, y1)` in fractions of the em, y upward,
    /// empty at the origin for a glyph without ink.
    fn bbox(&self) -> (f64, f64, f64, f64) {
        match self {
            Self::OpenType(font, glyph) => {
                let em = f64::from(font.units_per_em());
                font.glyph_metrics()
                    .extents(*glyph)
                    .filter(|extents| extents.width > 0.0 || extents.height > 0.0)
                    .map_or((0.0, 0.0, 0.0, 0.0), |extents| {
                        let left = f64::from(extents.x_bearing);
                        let top = f64::from(extents.y_bearing);
                        (
                            left / em,
                            (top - f64::from(extents.height)) / em,
                            (left + f64::from(extents.width)) / em,
                            top / em,
                        )
                    })
            }
            Self::Standard(metric) => metric.bbox.as_ref().map_or((0.0, 0.0, 0.0, 0.0), |bbox| {
                (
                    f64::from(bbox.llx) / 1000.0,
                    f64::from(bbox.lly) / 1000.0,
                    f64::from(bbox.urx) / 1000.0,
                    f64::from(bbox.ury) / 1000.0,
                )
            }),
        }
    }
}

/// What drawing needs from a `_Font`: the face that generation embeds, or the
/// PostScript name of a Standard 14 font, which is drawn without embedding.
pub(crate) enum DrawFace<'a> {
    Embedded(&'a GenerationFace),
    Standard(&'static str),
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
        py.detach(|| {
            Self::open(GenerationFace::new(Arc::new(data), index, None).map_err(PdfError::new_err)?)
        })
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
        py.detach(|| Self::open(GenerationFace::new(data, index, None).map_err(PdfError::new_err)?))
    }

    /// Return the instance at `weight` on this face's weight axis.
    ///
    /// The instance shares this face's bytes. A face without a weight axis,
    /// including a Standard 14 font, and a weight outside the axis raise:
    /// pylopdf draws only weights that the font defines.
    fn with_weight(&self, py: Python<'_>, weight: u16) -> PyResult<Self> {
        let Face::OpenType(face) = &self.face else {
            return Err(PdfError::new_err(
                "a Standard 14 font has no weight axis to select another weight on",
            ));
        };
        if face.weight_axis.is_none() {
            return Err(PdfError::new_err(
                "the face has no weight axis to select another weight on",
            ));
        }
        let (data, index) = face.generation.source();
        py.detach(|| {
            Self::open(GenerationFace::new(data, index, Some(weight)).map_err(PdfError::new_err)?)
        })
    }

    /// The `(min, default, max)` user-space values of the weight axis, or
    /// `None` for a face without one.
    #[getter]
    fn weight_range(&self) -> Option<(f32, f32, f32)> {
        match &self.face {
            Face::OpenType(face) => face
                .weight_axis
                .map(|axis| (axis.min, axis.default, axis.max)),
            Face::Standard(..) => None,
        }
    }

    /// A Standard 14 font, by its PostScript name.
    #[staticmethod]
    pub(crate) fn standard(name: &str) -> PyResult<Self> {
        let canonical = STANDARD_NAMES
            .into_iter()
            .find(|candidate| *candidate == name)
            .ok_or_else(|| PdfError::new_err(format!("unknown Standard 14 font: {name:?}")))?;
        let font = draw::base14_font(canonical)
            .ok_or_else(|| PdfError::new_err(format!("unknown Standard 14 font: {name:?}")))?;
        Ok(Self {
            face: Face::Standard(font, canonical),
        })
    }

    /// The PostScript name of a Standard 14 font, or `None` for OpenType.
    #[getter]
    pub(crate) fn standard_name(&self) -> Option<&'static str> {
        match self.face {
            Face::Standard(_, name) => Some(name),
            Face::OpenType(_) => None,
        }
    }

    /// The family and style of an OpenType face, or a Standard 14 PostScript name.
    #[getter]
    fn name(&self) -> Option<&str> {
        match &self.face {
            Face::OpenType(face) => face.full_name.as_deref(),
            Face::Standard(_, name) => Some(name),
        }
    }

    /// The typographic family name, falling back to the legacy family name.
    #[getter]
    fn family(&self) -> Option<&str> {
        match &self.face {
            Face::OpenType(face) => face.family.as_deref(),
            Face::Standard(_, name) => Some(match name.split('-').next().unwrap_or(name) {
                "Times" => "Times",
                family => family,
            }),
        }
    }

    /// The weight-axis value of a selected instance, else the OS/2 weight
    /// class; 400 or 700 for Standard 14 fonts.
    #[getter]
    fn weight(&self) -> u16 {
        match &self.face {
            Face::OpenType(face) => face.weight,
            Face::Standard(..) if self.bold() => BOLD_WEIGHT_CLASS,
            Face::Standard(..) => DEFAULT_WEIGHT_CLASS,
        }
    }

    /// Whether the face is bold by its weight class or style flags.
    #[getter]
    fn bold(&self) -> bool {
        match &self.face {
            Face::OpenType(face) => face.bold,
            Face::Standard(_, name) => name.contains("Bold"),
        }
    }

    /// Whether OS/2 or `head` marks the face italic or oblique.
    #[getter]
    fn italic(&self) -> bool {
        match &self.face {
            Face::OpenType(face) => face.italic,
            Face::Standard(_, name) => name.contains("Italic") || name.contains("Oblique"),
        }
    }

    /// Whether the face has serifs, by PANOSE classification or family name.
    #[getter]
    fn serif(&self) -> bool {
        match &self.face {
            Face::OpenType(face) => face.serif,
            Face::Standard(_, name) => name.starts_with("Times"),
        }
    }

    #[getter]
    fn units_per_em(&self) -> u16 {
        match &self.face {
            Face::OpenType(face) => face.generation.shaping_font().units_per_em(),
            Face::Standard(..) => STANDARD_UNITS_PER_EM,
        }
    }

    /// The ascender as a fraction of the em; see `line_metrics`.
    #[getter]
    fn ascender(&self) -> f64 {
        self.line_metrics().ascender
    }

    /// The descender as a fraction of the em, negative below the baseline;
    /// see `line_metrics`.
    #[getter]
    fn descender(&self) -> f64 {
        self.line_metrics().descender
    }

    /// Measure one line as drawing places it, in PDF units.
    #[pyo3(signature = (text, size, max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)))]
    fn text_width(
        &self,
        py: Python<'_>,
        text: &str,
        size: f64,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        if text.contains(['\n', '\r']) {
            return Err(PdfError::new_err(
                "text_width measures one line; split text at line breaks first",
            ));
        }
        positive("size", size)?;
        match &self.face {
            Face::OpenType(face) => py
                .detach(|| generate::shaped_line_width(face.generation.shaping_font(), text, size)),
            Face::Standard(font, _) => draw::standard_text_width(text, *font, size),
        }
        .map_err(PdfError::new_err)
    }

    /// Return the height that the canvas's `textbox` needs for `text`
    /// wrapped `width` wide: the distance from the top of the box to the
    /// descender of the last line. `text` has its line breaks normalized to
    /// `\n` and its tabs expanded, as the textbox takes it.
    #[pyo3(signature = (
        text,
        size,
        width,
        line_height=None,
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn text_height(
        &self,
        py: Python<'_>,
        text: &str,
        size: f64,
        width: f64,
        line_height: Option<f64>,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        positive("size", size)?;
        positive("width", width)?;
        if let Some(line_height) = line_height {
            positive("line_height", line_height)?;
        }
        let placement = LinePlacement::lines(self.line_metrics(), line_height);
        let max_lines = max_text_size.map(|_| MAX_GENERATED_TEXT_LINES);
        let layout = match &self.face {
            Face::OpenType(face) => py.detach(|| {
                generate::textbox_layout(
                    face.generation.shaping_font(),
                    text,
                    (width, 0.0),
                    size,
                    placement,
                    false,
                    max_lines,
                )
            }),
            Face::Standard(_, name) => py.detach(|| {
                draw::standard_textbox_layout(
                    text,
                    (width, 0.0),
                    name,
                    size,
                    placement,
                    false,
                    max_lines,
                )
            }),
        }
        .map_err(generated_text_err)?;
        Ok(layout.height)
    }

    /// The font bounding box as `(x0, y0, x1, y1)` in fractions of the em, y
    /// upward: the `head` box of an OpenType face, and the URW box that a
    /// Standard 14 font's ascender and descender come from.
    #[getter]
    fn bbox(&self) -> (f64, f64, f64, f64) {
        match &self.face {
            Face::OpenType(face) => {
                let metrics = face.generation.shaping_font().metrics();
                let em = f64::from(metrics.units_per_em);
                let bounds = metrics.bounds;
                (
                    bounds.x_min.to_f64() / em,
                    bounds.y_min.to_f64() / em,
                    bounds.x_max.to_f64() / em,
                    bounds.y_max.to_f64() / em,
                )
            }
            Face::Standard(font, _) => {
                let [x0, y0, x1, y1] = draw::standard_font_bbox(*font);
                (x0, y0, x1, y1)
            }
        }
    }

    /// The number of glyphs: the face's `maxp` count, or the glyphs of a
    /// Standard 14 font's AFM metrics.
    #[getter]
    fn glyph_count(&self) -> u32 {
        match &self.face {
            Face::OpenType(face) => face.generation.shaping_font().metrics().num_glyphs,
            Face::Standard(font, _) => {
                u32::try_from(font.metrics().character_metrics.len()).unwrap_or(u32::MAX)
            }
        }
    }

    /// Whether every glyph has the same advance: the `post` table's
    /// `isFixedPitch`, or the AFM `IsFixedPitch` of a Standard 14 font.
    #[getter]
    fn monospaced(&self) -> bool {
        match &self.face {
            Face::OpenType(face) => face
                .generation
                .shaping_font()
                .tables()
                .post()
                .is_ok_and(|post| post.is_fixed_pitch() != 0),
            Face::Standard(font, _) => font.metrics().is_fixed_pitch,
        }
    }

    /// Every character that `has_glyph` accepts, in code point order.
    fn chars(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| match &self.face {
            Face::OpenType(face) => {
                let charmap = face.generation.shaping_font().charmap();
                let mut codes: Vec<u32> = Vec::new();
                for (code, _) in charmap.unicodes() {
                    codes.try_reserve(1).map_err(|error| {
                        PdfError::new_err(format!("failed to list the font's characters: {error}"))
                    })?;
                    codes.push(code);
                }
                // A symbol or Mac Roman subtable maps characters through a
                // conversion that `unicodes` leaves out.
                if charmap.unicode_is_symbol() || charmap.unicode_is_mac_roman() {
                    for code in 0..CONVERTED_CODE_POINTS {
                        if charmap.map_unicode(code).is_some() {
                            codes.try_reserve(1).map_err(|error| {
                                PdfError::new_err(format!(
                                    "failed to list the font's characters: {error}"
                                ))
                            })?;
                            codes.push(code);
                        }
                    }
                }
                codes.sort_unstable();
                codes.dedup();
                let mut text = String::new();
                text.try_reserve(codes.len()).map_err(|error| {
                    PdfError::new_err(format!("failed to list the font's characters: {error}"))
                })?;
                text.extend(
                    codes
                        .into_iter()
                        .filter(|code| charmap.map_unicode(*code).is_some())
                        .filter_map(char::from_u32),
                );
                Ok(text)
            }
            Face::Standard(font, _) => Ok((0..=u8::MAX)
                .filter_map(|byte| pdf_base14_metrics::__WINANSI_CHAR_MAP[usize::from(byte)])
                .filter(|character| draw::standard_glyph_code(*font, *character).is_some())
                .collect::<std::collections::BTreeSet<char>>()
                .into_iter()
                .collect()),
        })
    }

    /// The advance of each character of `text` drawn alone, unshaped, at
    /// `size`, in PDF units.
    #[pyo3(signature = (text, size, max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)))]
    fn char_widths(
        &self,
        text: &str,
        size: f64,
        max_text_size: Option<usize>,
    ) -> PyResult<Vec<f64>> {
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        positive("size", size)?;
        let mut widths = Vec::new();
        widths
            .try_reserve_exact(text.chars().count())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate character widths: {error}"))
            })?;
        let shaper = match &self.face {
            Face::OpenType(face) => Some(ShaperFont::new(face.generation.shaping_font())),
            Face::Standard(..) => None,
        };
        for character in text.chars() {
            widths.push(self.char_glyph(character)?.advance(shaper.as_ref()) * size);
        }
        Ok(widths)
    }

    /// The ink box of the glyph for `codepoint` drawn alone, as
    /// `(x0, y0, x1, y1)` in fractions of the em, y upward.
    fn char_bbox(&self, codepoint: u32) -> PyResult<(f64, f64, f64, f64)> {
        let character = char::from_u32(codepoint)
            .ok_or_else(|| PdfError::new_err(format!("U+{codepoint:04X} is no character")))?;
        Ok(self.char_glyph(character)?.bbox())
    }

    /// The bytes an OpenType face was loaded from, a whole collection for a
    /// face of one, or `None` for a Standard 14 font.
    #[pyo3(name = "program")]
    fn program_bytes<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyBytes>> {
        self.program()
            .map(|(data, _)| PyBytes::new(py, data.as_slice()))
    }

    /// Return whether the face has a glyph for `codepoint`.
    ///
    /// A Standard 14 font has one when its encoding, WinAnsi for the text
    /// fonts, maps the character to a glyph of the font.
    fn has_glyph(&self, codepoint: u32) -> bool {
        match &self.face {
            Face::OpenType(face) => face
                .generation
                .shaping_font()
                .charmap()
                .map_unicode(codepoint)
                .is_some_and(|glyph| glyph.to_u32() != 0),
            Face::Standard(font, _) => char::from_u32(codepoint)
                .and_then(|character| draw::standard_glyph_code(*font, character))
                .is_some(),
        }
    }
}

/// Return the glyph name of a character: its name in the Adobe Glyph List,
/// a name of the AGL rather than of the ITC Zapf Dingbats list where both
/// name it, else `uniXXXX`, or `uXXXXX` beyond the Basic Multilingual Plane,
/// as the AGL specification composes names.
#[pyfunction]
pub(crate) fn glyph_name(codepoint: u32) -> PyResult<String> {
    if char::from_u32(codepoint).is_none() {
        return Err(PdfError::new_err(format!(
            "U+{codepoint:04X} is no character"
        )));
    }
    let mut buffer = [0_u8; agl::MAX_NAME_LEN];
    let mut fallback = None;
    for index in 0.. {
        let Some(name) = agl::char_to_nth_name(codepoint, index, &mut buffer) else {
            break;
        };
        // The ITC Zapf Dingbats list names its glyphs `a1` through `a191`.
        let dingbat = name.len() > 1
            && name.starts_with('a')
            && name[1..].bytes().all(|byte| byte.is_ascii_digit());
        if !dingbat {
            return Ok(name.to_owned());
        }
        fallback.get_or_insert_with(|| name.to_owned());
    }
    Ok(fallback.unwrap_or_else(|| {
        if codepoint <= 0xFFFF {
            format!("uni{codepoint:04X}")
        } else {
            format!("u{codepoint:05X}")
        }
    }))
}

/// Return the text that a glyph name stands for by the Adobe Glyph List
/// specification: AGL and ITC Zapf Dingbats names, `uniXXXX` and `uXXXX`
/// sequences, and components joined by underscores, with a suffix after a
/// period ignored; empty for a name that stands for no character.
#[pyfunction]
pub(crate) fn glyph_text(name: &str) -> String {
    agl::name_to_chars(name).collect()
}

/// Refuse a measure that is not a positive finite number.
fn positive(name: &str, value: f64) -> PyResult<()> {
    if value.is_finite() && value > 0.0 {
        Ok(())
    } else {
        Err(PyValueError::new_err(format!(
            "{name} must be a positive number: {value}"
        )))
    }
}
