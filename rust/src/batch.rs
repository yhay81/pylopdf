//! Drawing batches: text, vector shapes, and images recorded for several
//! pages, with text and shapes generated as one krilla document.
//!
//! Generating every canvas of a batch in one document lets krilla embed one
//! subset of each font for the whole batch, where `insert_text` embeds one
//! per call. Text is shaped and laid out while it is recorded, with the
//! functions that `insert_text` and `insert_textbox` use, so a missing glyph
//! raises at the call that drew it and measurements match the drawing.
//! Images stay on lopdf's image path, as for `insert_image`: they are decoded
//! when recorded, identical images are stored once, and an image splits the
//! canvas's text and shapes into separately generated pages so that drawing
//! order survives. `_Document::commit_batch` imports the generated pages once
//! and places one Form XObject per canvas.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::Arc;

use krilla::Document;
use krilla::color::rgb;
use krilla::geom::{Path, PathBuilder, Rect};
use krilla::page::PageSettings;
use krilla::paint::Stroke;
use krilla::text::Font;
use lopdf::ObjectId;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::bundled_font_cache::FontBytes;
use crate::document::{
    _Document, DEFAULT_MAX_IMAGE_INPUT_SIZE, DEFAULT_MAX_IMAGE_PIXELS, PdfError, limit_err,
    read_image_input, validate_image_input,
};
use crate::draw::{self, ImageParts};
use crate::font::_Font;
use crate::generate::{self, GlyphRun};
use crate::layout::MAX_GENERATED_TEXT_LINES;
use crate::pixmap::Pixmap;

/// Canvases one batch may draw on.
const MAX_BATCH_CANVASES: usize = 4096;
/// Path points one batch may record across all of its shapes.
const MAX_BATCH_PATH_POINTS: usize = 1_048_576;
/// Default bound on the UTF-8 text that one batch records.
const DEFAULT_MAX_BATCH_TEXT_SIZE: usize = 64 * 1024 * 1024;
/// Default bound on the image data that one batch stores until its commit.
const DEFAULT_MAX_BATCH_IMAGE_SIZE: usize = 512 * 1024 * 1024;
/// Bezier control-point distance for a quarter circle of unit radius.
const CIRCLE_KAPPA: f64 = 0.552_284_749_830_793_4;

/// Where a canvas is drawn when its batch is committed.
#[derive(Clone, Copy)]
pub(crate) enum CanvasTarget {
    /// An existing page, held by object so that later page edits cannot
    /// redirect the drawing to another page.
    Page { id: ObjectId, overlay: bool },
    /// A blank page appended in canvas order.
    NewPage,
}

/// The recorded drawing of one page, in its top-left-origin display space.
pub(crate) struct Canvas {
    pub(crate) target: CanvasTarget,
    pub(crate) size: (f64, f64),
    ops: Vec<DrawOp>,
}

impl Canvas {
    pub(crate) fn is_empty(&self) -> bool {
        self.ops.is_empty()
    }
}

/// One recorded drawing operation, generated in recording order.
enum DrawOp {
    Text {
        font: usize,
        size: f32,
        color: (f64, f64, f64),
        runs: Vec<GlyphRun>,
    },
    Path {
        path: Path,
        fill: Option<(f64, f64, f64)>,
        stroke: Option<((f64, f64, f64), f32)>,
    },
    Image(ImagePlacement),
}

/// One recorded image placed into a display rectangle of its canvas.
#[derive(Clone, Copy)]
pub(crate) struct ImagePlacement {
    /// Index of the stored image in the batch.
    pub(crate) image: usize,
    pub(crate) rect: [f64; 4],
    pub(crate) rotation: i64,
    pub(crate) keep_proportion: bool,
}

/// One decoded image, stored once however many canvases draw it.
pub(crate) struct BatchImage {
    pub(crate) parts: ImageParts,
    fingerprint: u64,
}

impl BatchImage {
    fn new(parts: ImageParts) -> Self {
        let mut hasher = DefaultHasher::new();
        parts.width.hash(&mut hasher);
        parts.height.hash(&mut hasher);
        parts.color_space.hash(&mut hasher);
        parts.filter.hash(&mut hasher);
        parts.data.hash(&mut hasher);
        parts.alpha.hash(&mut hasher);
        Self {
            fingerprint: hasher.finish(),
            parts,
        }
    }

    fn same_as(&self, other: &Self) -> bool {
        let (left, right) = (&self.parts, &other.parts);
        self.fingerprint == other.fingerprint
            && (left.width, left.height, left.color_space, left.filter)
                == (right.width, right.height, right.color_space, right.filter)
            && left.data == right.data
            && left.alpha == right.alpha
    }

    /// Bytes that the batch keeps for this image until its commit.
    fn stored_size(&self) -> usize {
        self.parts
            .data
            .len()
            .saturating_add(self.parts.alpha.as_ref().map_or(0, Vec::len))
    }
}

/// One layer of a canvas, drawn in order at commit.
#[derive(Clone, Copy)]
pub(crate) enum CanvasPart {
    /// A run of text and shapes, generated as one page of the batch PDF.
    Drawing(usize),
    /// A stored image placed by lopdf.
    Image(ImagePlacement),
}

/// A generated batch: the PDF of every run of text and shapes, and the
/// layers of each canvas in drawing order.
pub(crate) struct GeneratedBatch {
    /// `None` when no canvas records text or shapes.
    pub(crate) pdf: Option<Vec<u8>>,
    /// The layers of each canvas, indexed like the canvases.
    pub(crate) parts: Vec<Vec<CanvasPart>>,
}

/// Text and shapes recorded for several pages before one generation pass.
#[pyclass(module = "pylopdf.pylopdf_core")]
pub struct _Batch {
    canvases: Vec<Canvas>,
    /// Distinct faces drawn, by shared bytes and collection index.
    fonts: Vec<(FontBytes, u32)>,
    text_size: usize,
    max_text_size: Option<usize>,
    path_points: usize,
    images: Vec<BatchImage>,
    image_size: usize,
    max_image_size: Option<usize>,
}

impl _Batch {
    /// The recorded canvases, in creation order.
    pub(crate) fn canvases(&self) -> &[Canvas] {
        &self.canvases
    }

    fn canvas_mut(&mut self, canvas: usize) -> PyResult<&mut Canvas> {
        self.canvases
            .get_mut(canvas)
            .ok_or_else(|| PyValueError::new_err(format!("unknown canvas {canvas}")))
    }

    fn add_canvas(&mut self, target: CanvasTarget, size: (f64, f64)) -> PyResult<usize> {
        if self.canvases.len() >= MAX_BATCH_CANVASES {
            return Err(limit_err(
                "batch_page_count",
                format!("a drawing batch exceeds the {MAX_BATCH_CANVASES}-page safety limit"),
            ));
        }
        self.canvases.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the drawing batch: {error}"))
        })?;
        self.canvases.push(Canvas {
            target,
            size,
            ops: Vec::new(),
        });
        Ok(self.canvases.len() - 1)
    }

    /// Refuse text that would take the batch past its text budget.
    fn check_text_budget(&self, size: usize) -> PyResult<usize> {
        let total = self.text_size.checked_add(size);
        match (total, self.max_text_size) {
            (Some(total), Some(limit)) if total <= limit => Ok(total),
            (Some(total), None) => Ok(total),
            (_, limit) => Err(limit_err(
                "text_input_size",
                format!(
                    "text input exceeds the {}-byte UTF-8 limit of the drawing batch",
                    limit.unwrap_or(usize::MAX)
                ),
            )),
        }
    }

    /// Return the index of a face in this batch, adding it on first use.
    fn font_slot(&mut self, font: &_Font) -> PyResult<usize> {
        let (data, index) = font.source();
        if let Some(slot) = self
            .fonts
            .iter()
            .position(|(known, known_index)| Arc::ptr_eq(known, &data) && *known_index == index)
        {
            return Ok(slot);
        }
        self.fonts.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the drawing batch fonts: {error}"))
        })?;
        self.fonts.push((data, index));
        Ok(self.fonts.len() - 1)
    }

    fn push_op(&mut self, canvas: usize, op: DrawOp) -> PyResult<()> {
        let ops = &mut self.canvas_mut(canvas)?.ops;
        ops.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the canvas drawing: {error}"))
        })?;
        ops.push(op);
        Ok(())
    }

    /// Record a path after charging its points to the batch.
    fn push_path(
        &mut self,
        canvas: usize,
        points: usize,
        build: impl FnOnce(&mut PathBuilder),
        fill: Option<(f64, f64, f64)>,
        stroke: Option<((f64, f64, f64), f64)>,
    ) -> PyResult<()> {
        self.canvas_mut(canvas)?;
        let total = self
            .path_points
            .checked_add(points)
            .filter(|total| *total <= MAX_BATCH_PATH_POINTS)
            .ok_or_else(|| {
                limit_err(
                    "path_point_count",
                    format!(
                        "a drawing batch exceeds the {MAX_BATCH_PATH_POINTS}-point path safety limit"
                    ),
                )
            })?;
        let stroke = stroke
            .map(|(color, width)| {
                generate::finite_f32(width, "line width").map(|width| (color, width))
            })
            .transpose()
            .map_err(PdfError::new_err)?;
        let mut builder = PathBuilder::new();
        build(&mut builder);
        let path = builder
            .finish()
            .ok_or_else(|| PdfError::new_err("shape coordinates do not form a drawable path"))?;
        self.push_op(canvas, DrawOp::Path { path, fill, stroke })?;
        self.path_points = total;
        Ok(())
    }

    /// Store a decoded image, or find an identical one, and record its placement.
    fn push_image(
        &mut self,
        canvas: usize,
        parts: ImageParts,
        rect: (f64, f64, f64, f64),
        rotation: i64,
        keep_proportion: bool,
    ) -> PyResult<()> {
        self.canvas_mut(canvas)?;
        let image = BatchImage::new(parts);
        let slot = match self.images.iter().position(|known| known.same_as(&image)) {
            Some(slot) => slot,
            None => {
                let total = self
                    .image_size
                    .checked_add(image.stored_size())
                    .filter(|total| self.max_image_size.is_none_or(|limit| *total <= limit))
                    .ok_or_else(|| {
                        limit_err(
                            "image_input_size",
                            format!(
                                "image data exceeds the {}-byte limit of the drawing batch",
                                self.max_image_size.unwrap_or(usize::MAX)
                            ),
                        )
                    })?;
                self.images.try_reserve(1).map_err(|error| {
                    PdfError::new_err(format!("failed to grow the drawing batch images: {error}"))
                })?;
                self.images.push(image);
                self.image_size = total;
                self.images.len() - 1
            }
        };
        self.push_op(
            canvas,
            DrawOp::Image(ImagePlacement {
                image: slot,
                rect: [rect.0, rect.1, rect.2, rect.3],
                rotation,
                keep_proportion,
            }),
        )
    }

    /// Hand the stored images to the commit, which embeds each once.
    pub(crate) fn take_images(&mut self) -> Vec<BatchImage> {
        std::mem::take(&mut self.images)
    }

    /// Generate the text and shapes of every canvas as one krilla document.
    ///
    /// Each run of text and shapes between two images of a canvas becomes
    /// one page, so that the commit can layer images between them.
    pub(crate) fn generate(&self) -> Result<GeneratedBatch, String> {
        let mut fonts = Vec::new();
        fonts
            .try_reserve_exact(self.fonts.len())
            .map_err(|error| format!("failed to allocate batch fonts: {error}"))?;
        for (data, index) in &self.fonts {
            fonts.push(
                Font::new(Arc::clone(data).into(), *index)
                    .ok_or_else(|| format!("font data or collection index {index} is invalid"))?,
            );
        }
        let mut document = Document::new();
        let mut pages = 0;
        let mut all_parts = Vec::new();
        all_parts
            .try_reserve_exact(self.canvases.len())
            .map_err(|error| format!("failed to allocate batch canvas layers: {error}"))?;
        for canvas in &self.canvases {
            let mut parts = Vec::new();
            let mut start = 0;
            while start < canvas.ops.len() {
                parts
                    .try_reserve(1)
                    .map_err(|error| format!("failed to allocate canvas layers: {error}"))?;
                if let DrawOp::Image(placement) = &canvas.ops[start] {
                    parts.push(CanvasPart::Image(*placement));
                    start += 1;
                    continue;
                }
                let end = canvas.ops[start..]
                    .iter()
                    .position(|op| matches!(op, DrawOp::Image(_)))
                    .map_or(canvas.ops.len(), |offset| start + offset);
                draw_page(&mut document, canvas.size, &canvas.ops[start..end], &fonts)?;
                parts.push(CanvasPart::Drawing(pages));
                pages += 1;
                start = end;
            }
            all_parts.push(parts);
        }
        let pdf = if pages == 0 {
            None
        } else {
            Some(
                document
                    .finish()
                    .map_err(|error| format!("krilla drawing generation failed: {error}"))?,
            )
        };
        Ok(GeneratedBatch {
            pdf,
            parts: all_parts,
        })
    }
}

/// Draw one run of text and shapes as a page of the batch document.
fn draw_page(
    document: &mut Document,
    size: (f64, f64),
    ops: &[DrawOp],
    fonts: &[Font],
) -> Result<(), String> {
    let width = generate::finite_f32(size.0, "page width")?;
    let height = generate::finite_f32(size.1, "page height")?;
    let settings = PageSettings::from_wh(width, height)
        .ok_or_else(|| "page dimensions are outside krilla's supported range".to_owned())?;
    let mut page = document.start_page_with(settings);
    let mut surface = page.surface();
    for op in ops {
        match op {
            DrawOp::Text {
                font,
                size,
                color,
                runs,
            } => {
                // A stroke would also outline the glyphs, so text never
                // inherits one from a preceding shape.
                surface.set_stroke(None);
                surface.set_fill(Some(generate::solid_fill(*color)));
                generate::draw_runs(&mut surface, runs, &fonts[*font], *size);
            }
            DrawOp::Path { path, fill, stroke } => {
                surface.set_fill(fill.map(generate::solid_fill));
                surface.set_stroke(stroke.map(|(color, width)| {
                    Stroke {
                        paint: rgb::Color::new(
                            generate::channel_to_u8(color.0),
                            generate::channel_to_u8(color.1),
                            generate::channel_to_u8(color.2),
                        )
                        .into(),
                        width,
                        ..Stroke::default()
                    }
                }));
                surface.draw_path(path);
            }
            DrawOp::Image(_) => {}
        }
    }
    surface.finish();
    page.finish();
    Ok(())
}

/// Convert validated display coordinates for krilla's f32 path geometry.
fn point(x: f64, y: f64) -> PyResult<(f32, f32)> {
    Ok((
        generate::finite_f32(x, "shape x").map_err(PdfError::new_err)?,
        generate::finite_f32(y, "shape y").map_err(PdfError::new_err)?,
    ))
}

#[pymethods]
impl _Batch {
    #[new]
    #[pyo3(signature = (
        max_text_size=Some(DEFAULT_MAX_BATCH_TEXT_SIZE),
        max_image_size=Some(DEFAULT_MAX_BATCH_IMAGE_SIZE)
    ))]
    fn new(max_text_size: Option<usize>, max_image_size: Option<usize>) -> PyResult<Self> {
        for (name, value) in [
            ("max_text_size", max_text_size),
            ("max_image_size", max_image_size),
        ] {
            if value == Some(0) {
                return Err(PyValueError::new_err(format!(
                    "{name} must be a positive integer or None"
                )));
            }
        }
        Ok(Self {
            canvases: Vec::new(),
            fonts: Vec::new(),
            text_size: 0,
            max_text_size,
            path_points: 0,
            images: Vec::new(),
            image_size: 0,
            max_image_size,
        })
    }

    /// Return the canvas over an existing one-based page and its display size.
    ///
    /// A page has one canvas per batch; asking again with the same `overlay`
    /// returns it, and asking with the other one raises.
    fn add_page_canvas(
        &mut self,
        document: &Bound<'_, _Document>,
        page_number: u32,
        overlay: bool,
    ) -> PyResult<(usize, f64, f64)> {
        let (id, size) = document.borrow().canvas_target(page_number)?;
        for (index, canvas) in self.canvases.iter().enumerate() {
            if let CanvasTarget::Page {
                id: known,
                overlay: known_overlay,
            } = canvas.target
                && known == id
            {
                if known_overlay != overlay {
                    return Err(PyValueError::new_err(
                        "the page already has a canvas in this batch with the other overlay setting",
                    ));
                }
                return Ok((index, canvas.size.0, canvas.size.1));
            }
        }
        let index = self.add_canvas(CanvasTarget::Page { id, overlay }, size)?;
        Ok((index, size.0, size.1))
    }

    /// Add a canvas for a blank page that the commit appends, and return it
    /// with the size that the page's `MediaBox` stores.
    fn add_new_canvas(&mut self, width: f64, height: f64) -> PyResult<(usize, f64, f64)> {
        // The new page stores f32 reals, so the canvas keeps exactly that size.
        let width =
            f64::from(generate::finite_f32(width, "page width").map_err(PdfError::new_err)?);
        let height =
            f64::from(generate::finite_f32(height, "page height").map_err(PdfError::new_err)?);
        let index = self.add_canvas(CanvasTarget::NewPage, (width, height))?;
        Ok((index, width, height))
    }

    /// Record lines as `insert_text` places them and return the point after
    /// the last glyph of the last line.
    #[allow(clippy::too_many_arguments)]
    fn insert_text(
        &mut self,
        py: Python<'_>,
        canvas: usize,
        point: (f64, f64),
        lines: Vec<String>,
        font: &Bound<'_, _Font>,
        fontsize: f64,
        color: (f64, f64, f64),
    ) -> PyResult<(f64, f64)> {
        self.canvas_mut(canvas)?;
        if lines.len() > MAX_GENERATED_TEXT_LINES {
            return Err(limit_err(
                "text_line_count",
                format!("text input exceeds the {MAX_GENERATED_TEXT_LINES}-line safety limit"),
            ));
        }
        let text_size = self.check_text_budget(lines.iter().map(String::len).sum())?;
        let size = generate::finite_f32(fontsize, "font size").map_err(PdfError::new_err)?;
        let face = font.get();
        let runs = py
            .detach(|| generate::text_runs(face.shaping(), point, &lines, fontsize))
            .map_err(PdfError::new_err)?;
        let end = runs.last().map_or(point, |run| {
            (
                f64::from(run.origin.x) + generate::line_width(&run.glyphs, fontsize),
                f64::from(run.origin.y),
            )
        });
        let font = self.font_slot(face)?;
        self.push_op(
            canvas,
            DrawOp::Text {
                font,
                size,
                color,
                runs,
            },
        )?;
        self.text_size = text_size;
        Ok(end)
    }

    /// Record text laid out as `insert_textbox` lays it out and return the
    /// spare height; nothing is recorded when the text does not fit.
    #[allow(clippy::too_many_arguments)]
    fn insert_textbox(
        &mut self,
        py: Python<'_>,
        canvas: usize,
        rect: (f64, f64, f64, f64),
        text: &str,
        font: &Bound<'_, _Font>,
        fontsize: f64,
        line_height: f64,
        align: u8,
        color: (f64, f64, f64),
    ) -> PyResult<f64> {
        self.canvas_mut(canvas)?;
        if text.split('\n').count() > MAX_GENERATED_TEXT_LINES {
            return Err(limit_err(
                "text_line_count",
                format!("text input exceeds the {MAX_GENERATED_TEXT_LINES}-line safety limit"),
            ));
        }
        let text_size = self.check_text_budget(text.len())?;
        let size = generate::finite_f32(fontsize, "font size").map_err(PdfError::new_err)?;
        let face = font.get();
        let (runs, spare_height) = py
            .detach(|| {
                generate::textbox_runs(
                    face.shaping(),
                    [rect.0, rect.1, rect.2, rect.3],
                    text,
                    fontsize,
                    line_height,
                    align,
                    Some(MAX_GENERATED_TEXT_LINES),
                )
            })
            .map_err(crate::document::generated_text_err)?;
        let Some(runs) = runs else {
            return Ok(spare_height);
        };
        let font = self.font_slot(face)?;
        self.push_op(
            canvas,
            DrawOp::Text {
                font,
                size,
                color,
                runs,
            },
        )?;
        self.text_size = text_size;
        Ok(spare_height)
    }

    /// Record a straight stroked line.
    fn draw_line(
        &mut self,
        canvas: usize,
        start: (f64, f64),
        end: (f64, f64),
        color: (f64, f64, f64),
        width: f64,
    ) -> PyResult<()> {
        let start = point(start.0, start.1)?;
        let end = point(end.0, end.1)?;
        self.push_path(
            canvas,
            2,
            |builder| {
                builder.move_to(start.0, start.1);
                builder.line_to(end.0, end.1);
            },
            None,
            Some((color, width)),
        )
    }

    /// Record a rectangle, stroked with `color` and filled with `fill`.
    fn draw_rect(
        &mut self,
        canvas: usize,
        rect: (f64, f64, f64, f64),
        color: Option<(f64, f64, f64)>,
        fill: Option<(f64, f64, f64)>,
        width: f64,
    ) -> PyResult<()> {
        let (x0, y0) = point(rect.0, rect.1)?;
        let (x1, y1) = point(rect.2, rect.3)?;
        let rect = Rect::from_ltrb(x0, y0, x1, y1)
            .ok_or_else(|| PdfError::new_err("rect does not form a drawable rectangle"))?;
        self.push_path(
            canvas,
            4,
            |builder| builder.push_rect(rect),
            fill,
            color.map(|color| (color, width)),
        )
    }

    /// Record a circle as four cubic Bezier quarters.
    fn draw_circle(
        &mut self,
        canvas: usize,
        center: (f64, f64),
        radius: f64,
        color: Option<(f64, f64, f64)>,
        fill: Option<(f64, f64, f64)>,
        width: f64,
    ) -> PyResult<()> {
        let (cx, cy) = center;
        let k = radius * CIRCLE_KAPPA;
        let east = point(cx + radius, cy)?;
        let south = point(cx, cy + radius)?;
        let west = point(cx - radius, cy)?;
        let north = point(cx, cy - radius)?;
        let quarters = [
            [
                point(cx + radius, cy + k)?,
                point(cx + k, cy + radius)?,
                south,
            ],
            [
                point(cx - k, cy + radius)?,
                point(cx - radius, cy + k)?,
                west,
            ],
            [
                point(cx - radius, cy - k)?,
                point(cx - k, cy - radius)?,
                north,
            ],
            [
                point(cx + k, cy - radius)?,
                point(cx + radius, cy - k)?,
                east,
            ],
        ];
        self.push_path(
            canvas,
            13,
            |builder| {
                builder.move_to(east.0, east.1);
                for [first, second, end] in quarters {
                    builder.cubic_to(first.0, first.1, second.0, second.1, end.0, end.1);
                }
                builder.close();
            },
            fill,
            color.map(|color| (color, width)),
        )
    }

    /// Record connected straight segments, closed into a polygon if asked.
    fn draw_polyline(
        &mut self,
        canvas: usize,
        points: Vec<(f64, f64)>,
        closed: bool,
        color: Option<(f64, f64, f64)>,
        fill: Option<(f64, f64, f64)>,
        width: f64,
    ) -> PyResult<()> {
        let mut converted = Vec::new();
        converted.try_reserve_exact(points.len()).map_err(|error| {
            PdfError::new_err(format!("failed to allocate polyline points: {error}"))
        })?;
        for (x, y) in &points {
            converted.push(point(*x, *y)?);
        }
        let Some((&(x, y), rest)) = converted.split_first() else {
            return Err(PyValueError::new_err(
                "a polyline needs at least two points",
            ));
        };
        self.push_path(
            canvas,
            converted.len(),
            |builder| {
                builder.move_to(x, y);
                for &(x, y) in rest {
                    builder.line_to(x, y);
                }
                if closed {
                    builder.close();
                }
            },
            fill,
            color.map(|color| (color, width)),
        )
    }

    /// Decode bounded JPEG or PNG input and record it into a display rectangle.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        canvas,
        rect,
        data,
        rotation,
        keep_proportion,
        max_size=Some(DEFAULT_MAX_IMAGE_INPUT_SIZE),
        max_pixels=Some(DEFAULT_MAX_IMAGE_PIXELS)
    ))]
    fn insert_image(
        &mut self,
        py: Python<'_>,
        canvas: usize,
        rect: (f64, f64, f64, f64),
        data: Vec<u8>,
        rotation: i64,
        keep_proportion: bool,
        max_size: Option<usize>,
        max_pixels: Option<u64>,
    ) -> PyResult<()> {
        self.canvas_mut(canvas)?;
        validate_image_input(&data, max_size, max_pixels)?;
        let parts = py.detach(|| {
            draw::parse_image(data)
                .map_err(PdfError::new_err)?
                .ok_or_else(|| {
                    PdfError::new_err(
                        "unsupported image format (pass JPEG or PNG; convert other formats with Pillow or similar first)",
                    )
                })
        })?;
        self.push_image(canvas, parts, rect, rotation, keep_proportion)
    }

    /// Read bounded JPEG or PNG input from a path and record it.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        canvas,
        rect,
        path,
        rotation,
        keep_proportion,
        max_size=Some(DEFAULT_MAX_IMAGE_INPUT_SIZE),
        max_pixels=Some(DEFAULT_MAX_IMAGE_PIXELS)
    ))]
    fn insert_image_file(
        &mut self,
        py: Python<'_>,
        canvas: usize,
        rect: (f64, f64, f64, f64),
        path: &str,
        rotation: i64,
        keep_proportion: bool,
        max_size: Option<usize>,
        max_pixels: Option<u64>,
    ) -> PyResult<()> {
        if max_size == Some(0) {
            return Err(PyValueError::new_err(
                "max_size must be a positive integer or None",
            ));
        }
        self.canvas_mut(canvas)?;
        let data = py.detach(|| read_image_input(path, max_size))?;
        self.insert_image(
            py,
            canvas,
            rect,
            data,
            rotation,
            keep_proportion,
            max_size,
            max_pixels,
        )
    }

    /// Record a rendered RGBA8 Pixmap without a PNG round trip.
    fn insert_pixmap(
        &mut self,
        py: Python<'_>,
        canvas: usize,
        rect: (f64, f64, f64, f64),
        pixmap: PyRef<'_, Pixmap>,
        rotation: i64,
        keep_proportion: bool,
    ) -> PyResult<()> {
        self.canvas_mut(canvas)?;
        let (width, height) = (pixmap.width, pixmap.height);
        let data = Arc::clone(&pixmap.data);
        drop(pixmap);
        let parts = py
            .detach(|| draw::rgba_parts(width, height, &data))
            .map_err(PdfError::new_err)?;
        self.push_image(canvas, parts, rect, rotation, keep_proportion)
    }
}
