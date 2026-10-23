//! Shapes: the vector paths and text that pymupdf's `Shape` records for one
//! page, placed as one Form XObject when the shape is committed.
//!
//! A commit keeps pymupdf's drawing order: every finished path in the order
//! of its `finish()`, then every text item in insertion order. krilla draws
//! the paths and the text in embedded OpenType fonts and embeds one subset of
//! each face for the whole commit. Standard 14 text is not embedded, so it
//! becomes content operators, as `insert_text` writes it.
//!
//! The drawing is generated as krilla documents: one for the paths and the
//! faces drawn as given, and one for each variable face drawn at a weight
//! away from its default location, because `GenerationFace::name_subset`
//! renames every font of a document after the instance it embeds. Each
//! document holds the pages of its runs of consecutive items, which share the
//! document's subsets. When the drawing has more than one such page, or
//! Standard 14 text, a composed Form draws the pieces in order.

use std::ops::Range;
use std::sync::Arc;

use krilla::Document;
use krilla::geom::{Path, PathBuilder};
use krilla::num::NormalizedF32;
use krilla::page::PageSettings;
use krilla::paint::{Fill, FillRule, LineCap, LineJoin, Stroke, StrokeDash};
use krilla::surface::Surface;
use pyo3::prelude::*;

use crate::document::{
    DEFAULT_MAX_GENERATED_TEXT_SIZE, PdfError, generated_text_err, limit_err,
    validate_generated_text_input, validate_generated_text_line_count,
};
use crate::draw::{self, DeviceColor};
use crate::font::{_Font, DrawFace};
use crate::generate::{self, GenerationFace, GlyphRun};
use crate::layout::{LinePlacement, MAX_GENERATED_TEXT_LINES, TextBoxLayout};

/// Path points that one shape may hold until it is committed.
const MAX_SHAPE_PATH_POINTS: usize = 1_048_576;

/// How a finished path is stroked.
struct StrokeStyle {
    color: DeviceColor,
    width: f32,
    cap: LineCap,
    join: LineJoin,
    dash: Option<StrokeDash>,
    opacity: NormalizedF32,
}

/// How a finished path is filled.
struct FillStyle {
    color: DeviceColor,
    rule: FillRule,
    opacity: NormalizedF32,
}

/// One path that `finish()` styled.
struct ShapePath {
    path: Path,
    fill: Option<FillStyle>,
    stroke: Option<StrokeStyle>,
}

/// Standard 14 text, laid out when it was recorded.
enum StandardContent {
    Lines {
        point: (f64, f64),
        lines: Vec<Vec<u8>>,
        leading: f64,
    },
    Textbox {
        rect: [f64; 4],
        layout: TextBoxLayout,
        align: u8,
    },
}

/// One recorded text item.
enum ShapeText {
    /// Shaped lines in an embedded face, by slot in `_Shape::faces`.
    Embedded {
        face: usize,
        size: f32,
        color: DeviceColor,
        runs: Vec<GlyphRun>,
    },
    /// Text in a Standard 14 font, by PostScript name.
    Standard {
        font: &'static str,
        size: f64,
        color: DeviceColor,
        content: StandardContent,
    },
}

/// A piece of a committed shape, in drawing order.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Segment {
    /// A zero-based page of a zero-based generated krilla document.
    Drawing { document: usize, page: usize },
    /// A recorded Standard 14 text item, by index.
    Standard(usize),
}

/// What one generated krilla page draws: the paths, and a run of
/// consecutive text items.
struct PagePlan {
    paths: bool,
    texts: Range<usize>,
}

/// One krilla document of a commit.
struct DocumentPlan {
    /// The face slot whose instance names every font of the document, or
    /// `None` for the document of the paths and the faces drawn as given.
    instance: Option<usize>,
    pages: Vec<PagePlan>,
}

/// The pieces of a commit: drawing order and the krilla documents to generate.
pub(crate) struct ShapePlan {
    pub(crate) segments: Vec<Segment>,
    documents: Vec<DocumentPlan>,
}

impl ShapePlan {
    /// The number of pages of each generated document, in document order.
    pub(crate) fn page_counts(&self) -> impl Iterator<Item = usize> + '_ {
        self.documents.iter().map(|document| document.pages.len())
    }
}

/// One generated krilla document of a commit.
pub(crate) struct GeneratedDocument {
    pub(crate) pdf: Vec<u8>,
    /// The face whose instance names the document's fonts, if any.
    pub(crate) instance: Option<usize>,
}

/// The paths and text recorded for one page before a commit.
#[pyclass(module = "pylopdf.pylopdf_core")]
pub struct _Shape {
    paths: Vec<ShapePath>,
    texts: Vec<ShapeText>,
    /// Distinct embedded faces: shared bytes, collection index, and weight.
    faces: Vec<GenerationFace>,
    path_points: usize,
}

impl _Shape {
    pub(crate) fn is_empty(&self) -> bool {
        self.paths.is_empty() && self.texts.is_empty()
    }

    /// Return the face in a slot.
    pub(crate) fn face(&self, slot: usize) -> Option<&GenerationFace> {
        self.faces.get(slot)
    }

    /// Return the slot of an embedded face, adding it on first use.
    fn face_slot(&mut self, face: &GenerationFace) -> PyResult<usize> {
        let (data, index) = face.source();
        if let Some(slot) = self.faces.iter().position(|known| {
            let (known_data, known_index) = known.source();
            Arc::ptr_eq(&known_data, &data)
                && known_index == index
                && known.weight() == face.weight()
        }) {
            return Ok(slot);
        }
        self.faces.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the shape's fonts: {error}"))
        })?;
        self.faces.push(face.clone());
        Ok(self.faces.len() - 1)
    }

    fn push_text(&mut self, text: ShapeText) -> PyResult<()> {
        self.texts.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the shape's text: {error}"))
        })?;
        self.texts.push(text);
        Ok(())
    }

    /// Split the recorded drawing into the pieces that a commit draws in
    /// order: pages of krilla documents for the paths and runs of embedded
    /// text, and the Standard 14 text items between them.
    pub(crate) fn plan(&self) -> Result<ShapePlan, String> {
        fn grow<T>(items: &mut Vec<T>, label: &str) -> Result<(), String> {
            items
                .try_reserve(1)
                .map_err(|error| format!("failed to grow the shape's {label}: {error}"))
        }

        let mut segments: Vec<Segment> = Vec::new();
        let mut documents: Vec<DocumentPlan> = Vec::new();
        // The document that each slot draws in, created on first use; the
        // shared document is `None`'s entry.
        let mut shared: Option<usize> = None;
        let mut instance_documents: Vec<Option<usize>> = Vec::new();
        instance_documents
            .try_reserve_exact(self.faces.len())
            .map_err(|error| format!("failed to allocate the shape's documents: {error}"))?;
        instance_documents.resize(self.faces.len(), None);
        // The segment that the next item may extend.
        let mut open: Option<(usize, usize)> = None;
        if !self.paths.is_empty() {
            grow(&mut documents, "documents")?;
            documents.push(DocumentPlan {
                instance: None,
                pages: Vec::new(),
            });
            shared = Some(0);
            grow(&mut documents[0].pages, "pages")?;
            documents[0].pages.push(PagePlan {
                paths: true,
                texts: 0..0,
            });
            grow(&mut segments, "segments")?;
            segments.push(Segment::Drawing {
                document: 0,
                page: 0,
            });
            open = Some((0, 0));
        }
        for (index, text) in self.texts.iter().enumerate() {
            let ShapeText::Embedded { face, .. } = text else {
                grow(&mut segments, "segments")?;
                segments.push(Segment::Standard(index));
                open = None;
                continue;
            };
            let named = self.faces[*face].instance_postscript_name().is_some();
            let known = if named {
                instance_documents[*face]
            } else {
                shared
            };
            let document = match known {
                Some(document) => document,
                None => {
                    grow(&mut documents, "documents")?;
                    documents.push(DocumentPlan {
                        instance: named.then_some(*face),
                        pages: Vec::new(),
                    });
                    let document = documents.len() - 1;
                    if named {
                        instance_documents[*face] = Some(document);
                    } else {
                        shared = Some(document);
                    }
                    document
                }
            };
            match open {
                Some((open_document, page)) if open_document == document => {
                    let texts = &mut documents[document].pages[page].texts;
                    if texts.start == texts.end {
                        texts.start = index;
                    }
                    texts.end = index + 1;
                }
                _ => {
                    let pages = &mut documents[document].pages;
                    grow(pages, "pages")?;
                    pages.push(PagePlan {
                        paths: false,
                        texts: index..index + 1,
                    });
                    let page = pages.len() - 1;
                    grow(&mut segments, "segments")?;
                    segments.push(Segment::Drawing { document, page });
                    open = Some((document, page));
                }
            }
        }
        Ok(ShapePlan {
            segments,
            documents,
        })
    }

    /// Generate the krilla documents of `plan`, whose pages have the display
    /// `size`, in document order.
    pub(crate) fn generate(
        &self,
        plan: &ShapePlan,
        size: (f64, f64),
    ) -> Result<Vec<GeneratedDocument>, String> {
        let width = generate::finite_f32(size.0, "page width")?;
        let height = generate::finite_f32(size.1, "page height")?;
        let mut fonts = Vec::new();
        fonts
            .try_reserve_exact(self.faces.len())
            .map_err(|error| format!("failed to allocate the shape's fonts: {error}"))?;
        for face in &self.faces {
            fonts.push(face.embedding_font()?);
        }
        let mut generated = Vec::new();
        generated
            .try_reserve_exact(plan.documents.len())
            .map_err(|error| format!("failed to allocate the shape's documents: {error}"))?;
        for document_plan in &plan.documents {
            let mut document = Document::new();
            for page_plan in &document_plan.pages {
                let settings = PageSettings::from_wh(width, height).ok_or_else(|| {
                    "page dimensions are outside krilla's supported range".to_owned()
                })?;
                let mut page = document.start_page_with(settings);
                let mut surface = page.surface();
                if page_plan.paths {
                    for path in &self.paths {
                        draw_path(&mut surface, path);
                    }
                }
                for text in &self.texts[page_plan.texts.clone()] {
                    if let ShapeText::Embedded {
                        face,
                        size,
                        color,
                        runs,
                    } = text
                    {
                        // A stroke would also outline the glyphs, so text never
                        // keeps one from a preceding path.
                        surface.set_stroke(None);
                        surface.set_fill(Some(generate::solid_fill(*color)));
                        generate::draw_runs(&mut surface, runs, &fonts[*face], *size);
                    }
                }
                surface.finish();
                page.finish();
            }
            generated.push(GeneratedDocument {
                pdf: document
                    .finish()
                    .map_err(|error| format!("krilla shape generation failed: {error}"))?,
                instance: document_plan.instance,
            });
        }
        Ok(generated)
    }

    /// Return the PostScript name of a recorded Standard 14 text item.
    pub(crate) fn standard_font(&self, index: usize) -> Result<&'static str, String> {
        match self.texts.get(index) {
            Some(ShapeText::Standard { font, .. }) => Ok(font),
            _ => Err("a shape segment is not Standard 14 text".to_owned()),
        }
    }

    /// Build the content operators of a recorded Standard 14 text item for an
    /// unrotated page with `crop`, selecting the font resource `name`.
    pub(crate) fn standard_ops(
        &self,
        index: usize,
        crop: [f64; 4],
        name: &str,
    ) -> Result<Vec<u8>, String> {
        let Some(ShapeText::Standard {
            size,
            color,
            content,
            ..
        }) = self.texts.get(index)
        else {
            return Err("a shape segment is not Standard 14 text".to_owned());
        };
        match content {
            StandardContent::Lines {
                point,
                lines,
                leading,
            } => Ok(draw::text_ops(
                crop, 0, *point, lines, name, *size, *leading, *color,
            )),
            StandardContent::Textbox {
                rect,
                layout,
                align,
            } => draw::textbox_text_ops(crop, 0, *rect, layout, *align, name, *size, *color),
        }
    }
}

/// Draw one finished path with its fill and stroke.
fn draw_path(surface: &mut Surface<'_>, path: &ShapePath) {
    surface.set_fill(path.fill.as_ref().map(|fill| Fill {
        paint: generate::device_paint(fill.color),
        opacity: fill.opacity,
        rule: fill.rule,
    }));
    surface.set_stroke(path.stroke.as_ref().map(|stroke| Stroke {
        paint: generate::device_paint(stroke.color),
        width: stroke.width,
        line_cap: stroke.cap,
        line_join: stroke.join,
        opacity: stroke.opacity,
        dash: stroke.dash.clone(),
        ..Stroke::default()
    }));
    surface.draw_path(&path.path);
}

/// Convert 1, 3, or 4 channels from 0 to 1 to a gray, RGB, or CMYK colour.
fn device_color(channels: &[f64]) -> PyResult<DeviceColor> {
    if !channels
        .iter()
        .all(|channel| channel.is_finite() && (0.0..=1.0).contains(channel))
    {
        return Err(PdfError::new_err(
            "colour channels must be numbers from 0 to 1",
        ));
    }
    match *channels {
        [gray] => Ok(DeviceColor::Gray(gray)),
        [red, green, blue] => Ok(DeviceColor::Rgb(red, green, blue)),
        [cyan, magenta, yellow, black] => Ok(DeviceColor::Cmyk(cyan, magenta, yellow, black)),
        _ => Err(PdfError::new_err(
            "a colour has 1 (gray), 3 (RGB), or 4 (CMYK) channels",
        )),
    }
}

fn opacity(value: f64) -> PyResult<NormalizedF32> {
    NormalizedF32::new(value as f32)
        .filter(|_| value.is_finite())
        .ok_or_else(|| PdfError::new_err("opacity must be a number from 0 to 1"))
}

fn line_cap(value: u8) -> PyResult<LineCap> {
    match value {
        0 => Ok(LineCap::Butt),
        1 => Ok(LineCap::Round),
        2 => Ok(LineCap::Square),
        _ => Err(PdfError::new_err("lineCap must be 0, 1, or 2")),
    }
}

fn line_join(value: u8) -> PyResult<LineJoin> {
    match value {
        0 => Ok(LineJoin::Miter),
        1 => Ok(LineJoin::Round),
        2 => Ok(LineJoin::Bevel),
        _ => Err(PdfError::new_err("lineJoin must be 0, 1, or 2")),
    }
}

fn coordinate(value: f64) -> PyResult<f32> {
    generate::finite_f32(value, "shape coordinate").map_err(PdfError::new_err)
}

/// Path operators and the coordinates each consumes.
fn operand_count(operator: char) -> Option<usize> {
    match operator {
        'm' | 'l' => Some(2),
        'c' => Some(6),
        'h' => Some(0),
        _ => None,
    }
}

/// A stroke's colour, width, line cap, line join, dash array and phase, and
/// opacity, as Python passes them.
type StrokeArgs = (Vec<f64>, f64, u8, u8, Option<(Vec<f64>, f64)>, f64);
/// A fill's colour, even-odd rule, and opacity, as Python passes them.
type FillArgs = (Vec<f64>, bool, f64);

#[pymethods]
impl _Shape {
    #[new]
    fn new() -> Self {
        Self {
            paths: Vec::new(),
            texts: Vec::new(),
            faces: Vec::new(),
            path_points: 0,
        }
    }

    /// Whether nothing is recorded.
    #[pyo3(name = "is_empty")]
    fn py_is_empty(&self) -> bool {
        self.is_empty()
    }

    /// Discard everything recorded.
    fn clear(&mut self) {
        self.paths = Vec::new();
        self.texts = Vec::new();
        self.faces = Vec::new();
        self.path_points = 0;
    }

    /// Record a path that `finish()` styled.
    ///
    /// `operators` holds one character per path operator: `m` and `l` take
    /// two coordinates, `c` six, and `h` closes the current subpath. A path
    /// starts with `m`. `close` closes the last subpath.
    fn add_path(
        &mut self,
        operators: &str,
        coordinates: Vec<f64>,
        close: bool,
        fill: Option<FillArgs>,
        stroke: Option<StrokeArgs>,
    ) -> PyResult<()> {
        if fill.is_none() && stroke.is_none() {
            return Err(PdfError::new_err(
                "a path needs a fill, or a stroke colour with a positive width",
            ));
        }
        let mut points = 0usize;
        let mut expected = 0usize;
        for operator in operators.chars() {
            let count = operand_count(operator)
                .ok_or_else(|| PdfError::new_err(format!("unknown path operator {operator:?}")))?;
            expected += count;
            points += count / 2;
        }
        if expected != coordinates.len() || !operators.starts_with('m') {
            return Err(PdfError::new_err(
                "path operators and coordinates do not match",
            ));
        }
        let total = self
            .path_points
            .checked_add(points)
            .filter(|total| *total <= MAX_SHAPE_PATH_POINTS)
            .ok_or_else(|| {
                limit_err(
                    "path_point_count",
                    format!("a shape exceeds the {MAX_SHAPE_PATH_POINTS}-point path safety limit"),
                )
            })?;
        let fill = fill
            .map(|(color, even_odd, alpha)| -> PyResult<FillStyle> {
                Ok(FillStyle {
                    color: device_color(&color)?,
                    rule: if even_odd {
                        FillRule::EvenOdd
                    } else {
                        FillRule::NonZero
                    },
                    opacity: opacity(alpha)?,
                })
            })
            .transpose()?;
        let stroke = stroke
            .map(
                |(color, width, cap, join, dash, alpha)| -> PyResult<StrokeStyle> {
                    if !(width.is_finite() && width > 0.0) {
                        return Err(PdfError::new_err("width must be a positive number"));
                    }
                    let dash = dash
                        .map(|(array, phase)| -> PyResult<StrokeDash> {
                            if !array.iter().all(|value| value.is_finite() && *value >= 0.0)
                                || array.iter().all(|value| *value == 0.0)
                            {
                                return Err(PdfError::new_err(
                                    "dash lengths must be non-negative numbers, not all zero",
                                ));
                            }
                            Ok(StrokeDash {
                                array: array
                                    .iter()
                                    .map(|value| coordinate(*value))
                                    .collect::<PyResult<_>>()?,
                                offset: coordinate(phase)?,
                            })
                        })
                        .transpose()?;
                    Ok(StrokeStyle {
                        color: device_color(&color)?,
                        width: coordinate(width)?,
                        cap: line_cap(cap)?,
                        join: line_join(join)?,
                        dash,
                        opacity: opacity(alpha)?,
                    })
                },
            )
            .transpose()?;

        let mut builder = PathBuilder::new();
        let mut values = coordinates.iter();
        let mut next = || -> PyResult<f32> {
            coordinate(
                *values
                    .next()
                    .ok_or_else(|| PdfError::new_err("path coordinates ended early"))?,
            )
        };
        for operator in operators.chars() {
            match operator {
                'm' => {
                    let (x, y) = (next()?, next()?);
                    builder.move_to(x, y);
                }
                'l' => {
                    let (x, y) = (next()?, next()?);
                    builder.line_to(x, y);
                }
                'c' => {
                    let (x1, y1, x2, y2, x, y) =
                        (next()?, next()?, next()?, next()?, next()?, next()?);
                    builder.cubic_to(x1, y1, x2, y2, x, y);
                }
                _ => builder.close(),
            }
        }
        if close {
            builder.close();
        }
        let path = builder
            .finish()
            .ok_or_else(|| PdfError::new_err("the path has no segment to draw"))?;
        self.paths.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the shape's paths: {error}"))
        })?;
        self.paths.push(ShapePath { path, fill, stroke });
        self.path_points = total;
        Ok(())
    }

    /// Record lines placed as `insert_text` places them: the first baseline
    /// starts at `point` and later lines follow at `lineheight` times the
    /// font size, or at the font's default line height. Lines whose baseline
    /// would fall below a page `page_height` points high are left out, as
    /// `LineMetrics::lines_on_page` decides, although every line is checked
    /// against the font. Return the number of lines recorded.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        point,
        lines,
        font,
        fontsize,
        lineheight,
        color,
        page_height,
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_text(
        &mut self,
        py: Python<'_>,
        point: (f64, f64),
        lines: Vec<String>,
        font: &Bound<'_, _Font>,
        fontsize: f64,
        lineheight: Option<f64>,
        color: Vec<f64>,
        page_height: f64,
        max_text_size: Option<usize>,
    ) -> PyResult<usize> {
        validate_generated_text_line_count(lines.len(), max_text_size)?;
        validate_generated_text_input(lines.iter().map(String::as_bytes), max_text_size)?;
        let color = device_color(&color)?;
        let face = font.get();
        let metrics = face.line_metrics();
        let leading = metrics
            .leading(fontsize, lineheight)
            .map_err(PdfError::new_err)?;
        let drawn = metrics.lines_on_page(lines.len(), page_height - point.1, fontsize, lineheight);
        let text = match face.draw_face() {
            DrawFace::Embedded(face) => {
                let size =
                    generate::finite_f32(fontsize, "font size").map_err(PdfError::new_err)?;
                let mut runs = py
                    .detach(|| generate::text_runs(face.shaping_font(), point, &lines, leading))
                    .map_err(PdfError::new_err)?;
                runs.truncate(drawn);
                ShapeText::Embedded {
                    face: self.face_slot(face)?,
                    size,
                    color,
                    runs,
                }
            }
            DrawFace::Standard(name) => {
                let mut encoded = Vec::new();
                encoded.try_reserve_exact(lines.len()).map_err(|error| {
                    PdfError::new_err(format!("failed to allocate Standard 14 lines: {error}"))
                })?;
                for line in &lines {
                    encoded.push(draw::encode_cp1252(line).map_err(PdfError::new_err)?);
                }
                encoded.truncate(drawn);
                ShapeText::Standard {
                    font: name,
                    size: fontsize,
                    color,
                    content: StandardContent::Lines {
                        point,
                        lines: encoded,
                        leading,
                    },
                }
            }
        };
        self.push_text(text)?;
        Ok(drawn)
    }

    /// Lay out text inside a display rectangle as `insert_textbox` does and
    /// return the spare height. Nothing is recorded when the text does not
    /// fit, and the spare height is then negative.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        rect,
        text,
        font,
        fontsize,
        lineheight,
        align,
        color,
        max_text_size=Some(DEFAULT_MAX_GENERATED_TEXT_SIZE)
    ))]
    fn insert_textbox(
        &mut self,
        py: Python<'_>,
        rect: (f64, f64, f64, f64),
        text: &str,
        font: &Bound<'_, _Font>,
        fontsize: f64,
        lineheight: Option<f64>,
        align: u8,
        color: Vec<f64>,
        max_text_size: Option<usize>,
    ) -> PyResult<f64> {
        validate_generated_text_line_count(text.split('\n').count(), max_text_size)?;
        validate_generated_text_input(std::iter::once(text.as_bytes()), max_text_size)?;
        let color = device_color(&color)?;
        let rect = [rect.0, rect.1, rect.2, rect.3];
        let max_lines = max_text_size.map(|_| MAX_GENERATED_TEXT_LINES);
        let face = font.get();
        let placement = LinePlacement::textbox(face.line_metrics(), lineheight);
        match face.draw_face() {
            DrawFace::Embedded(face) => {
                let size =
                    generate::finite_f32(fontsize, "font size").map_err(PdfError::new_err)?;
                let (runs, spare_height) = py
                    .detach(|| {
                        generate::textbox_runs(
                            face.shaping_font(),
                            rect,
                            text,
                            fontsize,
                            placement,
                            align,
                            max_lines,
                        )
                    })
                    .map_err(generated_text_err)?;
                if let Some(runs) = runs {
                    let face = self.face_slot(face)?;
                    self.push_text(ShapeText::Embedded {
                        face,
                        size,
                        color,
                        runs,
                    })?;
                }
                Ok(spare_height)
            }
            DrawFace::Standard(name) => {
                let layout = py
                    .detach(|| {
                        draw::standard_textbox_layout(
                            text,
                            (rect[2] - rect[0], rect[3] - rect[1]),
                            name,
                            fontsize,
                            placement,
                            align == 3,
                            max_lines,
                        )
                    })
                    .map_err(generated_text_err)?;
                let spare_height = layout.spare_height;
                if layout.fits() {
                    self.push_text(ShapeText::Standard {
                        font: name,
                        size: fontsize,
                        color,
                        content: StandardContent::Textbox {
                            rect,
                            layout,
                            align,
                        },
                    })?;
                }
                Ok(spare_height)
            }
        }
    }
}
