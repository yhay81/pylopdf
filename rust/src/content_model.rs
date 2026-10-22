//! The content model: every text run, path, and image that hayro paints on a
//! page, in paint order and display space.
//!
//! `ContentCollector` is a hayro `Device`. It records what each paint callback
//! receives together with the clips, transparency groups, and marked content
//! that enclose it, and interns fonts and graphics contexts so that items share
//! them. Glyph boxes come from the outlines that hayro computes, and from
//! interpreting Type 3 glyph procedures. One element budget bounds glyphs, path
//! and clip segments, and items, and every collection grows fallibly, so a
//! refusal returns no partial result.

use std::collections::HashMap;

use hayro::hayro_interpret::font::{Glyph, GlyphRun, OutlineGlyph, Type3Glyph};
use hayro::hayro_interpret::hayro_cmap::BfString;
use hayro::hayro_interpret::pattern::Pattern;
use hayro::hayro_interpret::shading::ShadingType;
use hayro::hayro_interpret::{
    BlendMode, CacheKey, ClipPath, Device, DrawMode, DrawProps, FillRule, Image, ImageDrawProps,
    InterpreterCache, InterpreterSettings, MaskType, Paint, SoftMask, StrokeProps, interpret_page,
};
use hayro::hayro_syntax::object::{Array, Stream};
use hayro::hayro_syntax::page::Page;
use hayro::hayro_syntax::{Filter, Pdf};
use kurbo::{Affine, BezPath, Cap, Join, PathEl, Point, Rect, Shape};

use crate::extract::{
    DEFAULT_IMAGE_RESOLUTION, extraction_context, image_color_space, jpeg_resolution,
};

type BBox = (f64, f64, f64, f64);
type Coefficients = [f64; 6];

/// A paint: kind 0 is a color (red, green, blue, alpha in sRGB), kind 1 a
/// shading pattern (shading type, or 0 for a shading that hayro could not
/// read, the six matrix coefficients, and opacity), and kind 2 a tiling
/// pattern (the four pattern-space bbox coordinates, x step, y step, and the
/// six matrix coefficients).
pub(crate) type PaintTuple = (u8, Vec<f64>);
/// A stroke: paint, width, cap, join, miter limit, dash lengths, dash offset.
pub(crate) type StrokeTuple = (PaintTuple, f64, u8, u8, f64, Vec<f64>, f64);
/// A soft mask: the object number of its group, and whether it reads alpha.
pub(crate) type SoftMaskTuple = (u32, bool);
/// What every item carries: its graphics context, blend mode, and soft mask.
pub(crate) type ItemContextTuple = (u32, u8, Option<SoftMaskTuple>);
/// Glyph IDs, Unicode, matrices (six coefficients per glyph), advances, and
/// ink boxes.
pub(crate) type GlyphsTuple = (
    Vec<Option<u32>>,
    Vec<Option<String>>,
    Vec<f64>,
    Vec<Option<f64>>,
    Vec<Option<BBox>>,
);
/// A text run: glyphs, font index, render mode, fill paint, stroke, bbox, and
/// item context.
pub(crate) type TextRunTuple = (
    GlyphsTuple,
    u32,
    u8,
    Option<PaintTuple>,
    Option<StrokeTuple>,
    BBox,
    ItemContextTuple,
);
/// A path: segment kinds, coordinates, bbox, fill paint, even-odd fill rule,
/// stroke, and item context.
pub(crate) type PathTuple = (
    Vec<u8>,
    Vec<f64>,
    BBox,
    Option<PaintTuple>,
    bool,
    Option<StrokeTuple>,
    ItemContextTuple,
);
/// An image's pixel width and height, bits per component, color components,
/// and color space name.
pub(crate) type ImageFormatTuple = (u32, u32, u8, u8, String);
/// An image: xref, format, stencil flag, mask kind, encoded size, resolution,
/// unit-square matrix, bbox, and item context.
pub(crate) type ImageTuple = (
    u32,
    ImageFormatTuple,
    bool,
    u8,
    usize,
    (u32, u32),
    Coefficients,
    BBox,
    ItemContextTuple,
);
/// A font: PostScript name, Type 3 flag, weight, italic, serif, monospace.
pub(crate) type FontTuple = (String, bool, Option<u32>, bool, bool, bool);
/// A clip: segment kinds, coordinates, even-odd fill rule, and bbox.
pub(crate) type ClipTuple = (Vec<u8>, Vec<f64>, bool, BBox);
/// A transparency group: opacity, blend mode, and soft mask.
pub(crate) type GroupTuple = (f64, u8, Option<SoftMaskTuple>);
/// A marked-content sequence: tag and MCID.
pub(crate) type MarkTuple = (String, Option<i32>);
/// A graphics context: clip, group, and marked-content indexes, outermost
/// first.
pub(crate) type StateTuple = (Vec<u32>, Vec<u32>, Vec<u32>);

/// Segment kinds, as the PDF path operators name them.
const MOVE: u8 = b'm';
const LINE: u8 = b'l';
const CURVE: u8 = b'c';
const CLOSE: u8 = b'h';

/// Text render modes the Device API distinguishes.
const RENDER_FILL: u8 = 0;
const RENDER_STROKE: u8 = 1;
const RENDER_FILL_STROKE: u8 = 2;
const RENDER_INVISIBLE: u8 = 3;

/// Image mask kinds.
const MASK_NONE: u8 = 0;
const MASK_SOFT: u8 = 1;
const MASK_EXPLICIT: u8 = 2;
const MASK_COLOR_KEY: u8 = 3;

/// Glyph outlines kept per interpretation, so that a page reusing many
/// distinct glyphs cannot grow the cache without bound.
const MAX_CACHED_GLYPHS: usize = 65_536;

/// Why an interpretation stopped.
pub(crate) enum ContentError {
    /// More glyphs, segments, and items than the budget allows.
    ElementCount(usize),
    /// A collection could not grow.
    Allocation(String),
}

/// One painted item, in the tuple form that crosses into Python.
pub(crate) enum ContentItem {
    Text(TextRunTuple),
    Path(PathTuple),
    Image(ImageTuple),
}

/// A page's content: items and the fonts and contexts they index.
pub(crate) struct PageContent {
    pub(crate) items: Vec<ContentItem>,
    pub(crate) fonts: Vec<FontTuple>,
    pub(crate) clips: Vec<ClipTuple>,
    pub(crate) groups: Vec<GroupTuple>,
    pub(crate) marks: Vec<MarkTuple>,
    pub(crate) states: Vec<StateTuple>,
}

fn push<T>(values: &mut Vec<T>, value: T, label: &str) -> Result<(), ContentError> {
    if values.len() == values.capacity() {
        values.try_reserve(1).map_err(|error| {
            ContentError::Allocation(format!("failed to grow {label}: {error}"))
        })?;
    }
    values.push(value);
    Ok(())
}

fn with_capacity<T>(capacity: usize, label: &str) -> Result<Vec<T>, ContentError> {
    let mut values = Vec::new();
    values.try_reserve_exact(capacity).map_err(|error| {
        ContentError::Allocation(format!("failed to allocate {label}: {error}"))
    })?;
    Ok(values)
}

fn copy_values(values: &[f64], label: &str) -> Result<Vec<f64>, ContentError> {
    let mut copy = with_capacity(values.len(), label)?;
    copy.extend_from_slice(values);
    Ok(copy)
}

fn bbox_tuple(rect: Rect) -> BBox {
    (rect.x0, rect.y0, rect.x1, rect.y1)
}

fn rect_is_finite(rect: Rect) -> bool {
    rect.x0.is_finite() && rect.y0.is_finite() && rect.x1.is_finite() && rect.y1.is_finite()
}

fn affine_is_finite(transform: Affine) -> bool {
    transform.as_coeffs().iter().all(|value| value.is_finite())
}

/// The bounding box of a rectangle's four corners under `transform`.
fn transformed_rect(rect: Rect, transform: Affine) -> Rect {
    let corners = [
        transform * Point::new(rect.x0, rect.y0),
        transform * Point::new(rect.x1, rect.y0),
        transform * Point::new(rect.x0, rect.y1),
        transform * Point::new(rect.x1, rect.y1),
    ];
    let mut bbox = Rect::from_points(corners[0], corners[1]);
    bbox = bbox.union_pt(corners[2]);
    bbox.union_pt(corners[3])
}

/// Whether `transform` keeps rectangles axis-aligned, so that mapping a
/// box's corners gives the exact box of the mapped shape.
fn is_axis_aligned(transform: Affine) -> bool {
    let [a, b, c, d, _, _] = transform.as_coeffs();
    (b == 0.0 && c == 0.0) || (a == 0.0 && d == 0.0)
}

/// The exact bounding box of a path under `transform`, or `None` for a path
/// without segments.
fn path_bbox(path: &BezPath, transform: Affine) -> Option<Rect> {
    path.segments()
        .map(|segment| (transform * segment).bounding_box())
        .reduce(|bbox, segment| bbox.union(segment))
}

/// The largest scale that `transform` applies, used for stroke widths and
/// dashes in display space.
fn transform_scale(transform: Affine) -> f64 {
    let [a, b, c, d, _, _] = transform.as_coeffs();
    a.hypot(b).max(c.hypot(d))
}

/// Path segments in display space, as PDF operators and their coordinates.
struct Segments {
    kinds: Vec<u8>,
    coords: Vec<f64>,
}

impl Segments {
    fn equals(&self, other: &Self) -> bool {
        self.kinds == other.kinds && self.coords == other.coords
    }

    fn count(&self) -> usize {
        self.kinds.len()
    }
}

/// Map a path's elements through `transform`. Quadratic curves become the
/// cubic curves that draw them exactly. Returns `None` when a coordinate is
/// not finite.
fn segments(path: &BezPath, transform: Affine) -> Result<Option<Segments>, ContentError> {
    let elements = path.elements();
    let coordinate_count = elements
        .iter()
        .map(|element| match element {
            PathEl::MoveTo(_) | PathEl::LineTo(_) => 2,
            PathEl::QuadTo(..) | PathEl::CurveTo(..) => 6,
            PathEl::ClosePath => 0,
        })
        .sum();
    let mut kinds = with_capacity(elements.len(), "path segment kinds")?;
    let mut coords: Vec<f64> = with_capacity(coordinate_count, "path coordinates")?;
    let mut current = Point::ZERO;
    let mut start = Point::ZERO;
    for element in elements {
        match *element {
            PathEl::MoveTo(point) => {
                let point = transform * point;
                kinds.push(MOVE);
                coords.extend_from_slice(&[point.x, point.y]);
                current = point;
                start = point;
            }
            PathEl::LineTo(point) => {
                let point = transform * point;
                kinds.push(LINE);
                coords.extend_from_slice(&[point.x, point.y]);
                current = point;
            }
            PathEl::QuadTo(control, point) => {
                let control = transform * control;
                let point = transform * point;
                let first = current + (control - current) * (2.0 / 3.0);
                let second = point + (control - point) * (2.0 / 3.0);
                kinds.push(CURVE);
                coords.extend_from_slice(&[first.x, first.y, second.x, second.y, point.x, point.y]);
                current = point;
            }
            PathEl::CurveTo(first, second, point) => {
                let (first, second, point) =
                    (transform * first, transform * second, transform * point);
                kinds.push(CURVE);
                coords.extend_from_slice(&[first.x, first.y, second.x, second.y, point.x, point.y]);
                current = point;
            }
            PathEl::ClosePath => {
                kinds.push(CLOSE);
                current = start;
            }
        }
    }
    if !coords.iter().all(|value| value.is_finite()) {
        return Ok(None);
    }
    Ok(Some(Segments { kinds, coords }))
}

fn paint_tuple(paint: &Paint<'_>) -> Result<PaintTuple, ContentError> {
    match paint {
        Paint::Color(color) => {
            let [red, green, blue, alpha] = color.to_rgba().components();
            let values = [red, green, blue, alpha].map(f64::from);
            Ok((0, copy_values(&values, "paint values")?))
        }
        Paint::Pattern(pattern) => match pattern.as_ref() {
            Pattern::Shading(shading) => {
                let shading_type = match shading.shading.shading_type.as_ref() {
                    ShadingType::FunctionBased { .. } => 1.0,
                    ShadingType::RadialAxial { axial: true, .. } => 2.0,
                    ShadingType::RadialAxial { .. } => 3.0,
                    ShadingType::TriangleMesh { .. } => 4.0,
                    ShadingType::CoonsPatchMesh { .. } => 6.0,
                    ShadingType::TensorProductPatchMesh { .. } => 7.0,
                    ShadingType::Dummy => 0.0,
                };
                let [a, b, c, d, e, f] = shading.matrix.as_coeffs();
                let values = [shading_type, a, b, c, d, e, f, f64::from(shading.opacity)];
                Ok((1, copy_values(&values, "paint values")?))
            }
            Pattern::Tiling(tiling) => {
                let [a, b, c, d, e, f] = tiling.matrix.as_coeffs();
                let bbox = tiling.bbox;
                let values = [
                    bbox.x0,
                    bbox.y0,
                    bbox.x1,
                    bbox.y1,
                    f64::from(tiling.x_step),
                    f64::from(tiling.y_step),
                    a,
                    b,
                    c,
                    d,
                    e,
                    f,
                ];
                Ok((2, copy_values(&values, "paint values")?))
            }
        },
    }
}

/// Whether every value of a paint is finite, so that Python can hold its
/// matrix and box.
fn paint_is_finite(paint: &PaintTuple) -> bool {
    paint.1.iter().all(|value| value.is_finite())
}

fn blend_code(mode: BlendMode) -> u8 {
    match mode {
        BlendMode::Normal => 0,
        BlendMode::Multiply => 1,
        BlendMode::Screen => 2,
        BlendMode::Overlay => 3,
        BlendMode::Darken => 4,
        BlendMode::Lighten => 5,
        BlendMode::ColorDodge => 6,
        BlendMode::ColorBurn => 7,
        BlendMode::HardLight => 8,
        BlendMode::SoftLight => 9,
        BlendMode::Difference => 10,
        BlendMode::Exclusion => 11,
        BlendMode::Hue => 12,
        BlendMode::Saturation => 13,
        BlendMode::Color => 14,
        BlendMode::Luminosity => 15,
    }
}

fn soft_mask_tuple(mask: Option<&SoftMask<'_>>) -> Option<SoftMaskTuple> {
    mask.map(|mask| {
        (
            u32::try_from(mask.id().obj_number).unwrap_or(0),
            mask.mask_type() == MaskType::Alpha,
        )
    })
}

/// A stroke's style in display space.
struct StrokeRecord {
    paint: PaintTuple,
    width: f64,
    cap: u8,
    join: u8,
    miter_limit: f64,
    dashes: Vec<f64>,
    dash_offset: f64,
}

impl StrokeRecord {
    fn new(
        paint: &Paint<'_>,
        stroke: &StrokeProps,
        transform: Affine,
    ) -> Result<Self, ContentError> {
        let scale = transform_scale(transform);
        let mut dashes = with_capacity(stroke.dash_array.len(), "dash lengths")?;
        dashes.extend(
            stroke
                .dash_array
                .iter()
                .map(|value| f64::from(*value) * scale),
        );
        Ok(Self {
            paint: paint_tuple(paint)?,
            width: f64::from(stroke.line_width) * scale,
            cap: match stroke.line_cap {
                Cap::Butt => 0,
                Cap::Round => 1,
                Cap::Square => 2,
            },
            join: match stroke.line_join {
                Join::Miter => 0,
                Join::Round => 1,
                Join::Bevel => 2,
            },
            miter_limit: f64::from(stroke.miter_limit),
            dashes,
            dash_offset: f64::from(stroke.dash_offset) * scale,
        })
    }

    fn is_finite(&self) -> bool {
        paint_is_finite(&self.paint)
            && self.width.is_finite()
            && self.miter_limit.is_finite()
            && self.dash_offset.is_finite()
            && self.dashes.iter().all(|value| value.is_finite())
    }

    fn into_tuple(self) -> StrokeTuple {
        (
            self.paint,
            self.width,
            self.cap,
            self.join,
            self.miter_limit,
            self.dashes,
            self.dash_offset,
        )
    }
}

/// The context index, blend mode, and soft mask of one item.
#[derive(Clone, Copy, PartialEq)]
struct ItemContext {
    state: u32,
    blend: u8,
    soft_mask: Option<SoftMaskTuple>,
}

impl ItemContext {
    fn into_tuple(self) -> ItemContextTuple {
        (self.state, self.blend, self.soft_mask)
    }
}

struct TextRunRecord {
    ids: Vec<Option<u32>>,
    texts: Vec<Option<String>>,
    matrices: Vec<f64>,
    advances: Vec<Option<f64>>,
    boxes: Vec<Option<BBox>>,
    font: u32,
    render_mode: u8,
    fill: Option<PaintTuple>,
    stroke: Option<StrokeRecord>,
    bbox: Rect,
    context: ItemContext,
}

struct PathRecord {
    segments: Segments,
    bbox: Rect,
    fill: Option<PaintTuple>,
    even_odd: bool,
    stroke: Option<StrokeRecord>,
    context: ItemContext,
}

struct ImageRecord {
    xref: u32,
    format: ImageFormatTuple,
    is_stencil: bool,
    mask: u8,
    encoded_size: usize,
    resolution: (u32, u32),
    matrix: Coefficients,
    bbox: Rect,
    context: ItemContext,
}

enum Record {
    Text(TextRunRecord),
    Path(PathRecord),
    Image(ImageRecord),
}

impl Record {
    fn bbox(&self) -> Rect {
        match self {
            Self::Text(run) => run.bbox,
            Self::Path(path) => path.bbox,
            Self::Image(image) => image.bbox,
        }
    }

    fn into_item(self) -> ContentItem {
        match self {
            Self::Text(run) => ContentItem::Text((
                (run.ids, run.texts, run.matrices, run.advances, run.boxes),
                run.font,
                run.render_mode,
                run.fill,
                run.stroke.map(StrokeRecord::into_tuple),
                bbox_tuple(run.bbox),
                run.context.into_tuple(),
            )),
            Self::Path(path) => ContentItem::Path((
                path.segments.kinds,
                path.segments.coords,
                bbox_tuple(path.bbox),
                path.fill,
                path.even_odd,
                path.stroke.map(StrokeRecord::into_tuple),
                path.context.into_tuple(),
            )),
            Self::Image(image) => ContentItem::Image((
                image.xref,
                image.format,
                image.is_stencil,
                image.mask,
                image.encoded_size,
                image.resolution,
                image.matrix,
                bbox_tuple(image.bbox),
                image.context.into_tuple(),
            )),
        }
    }
}

/// The paint callback that a stroke may complete: hayro paints `B` and the
/// fill-and-stroke text render mode as a fill followed by a stroke of the
/// same geometry.
#[derive(Clone, Copy, PartialEq)]
enum Pending {
    Path,
    Text,
}

/// A glyph outline in hayro's 1000-unit glyph space, and its exact box.
struct GlyphInk {
    bbox: Rect,
    path: BezPath,
}

/// A Device that only measures where a Type 3 glyph procedure paints.
struct InkBounds {
    bbox: Option<Rect>,
}

impl InkBounds {
    fn add(&mut self, rect: Rect) {
        if rect_is_finite(rect) {
            self.bbox = Some(self.bbox.map_or(rect, |bbox| bbox.union(rect)));
        }
    }
}

impl<'a> Device<'a> for InkBounds {
    fn draw_path(&mut self, path: &BezPath, props: DrawProps<'a>, mode: &DrawMode) {
        let Some(bbox) = path_bbox(path, props.transform) else {
            return;
        };
        let half_width = match mode {
            DrawMode::Stroke(stroke) | DrawMode::FillAndStroke(_, stroke) => {
                f64::from(stroke.line_width) * transform_scale(props.transform) / 2.0
            }
            DrawMode::Fill(_) => 0.0,
            DrawMode::Invisible => return,
        };
        self.add(bbox.inflate(half_width, half_width));
    }
    fn push_clip_path(&mut self, _: &ClipPath) {}
    fn push_transparency_group(&mut self, _: f32, _: Option<SoftMask<'a>>, _: BlendMode) {}
    fn draw_glyph_run(&mut self, run: &GlyphRun<'_, 'a>, props: DrawProps<'a>, mode: &DrawMode) {
        if matches!(mode, DrawMode::Invisible) {
            return;
        }
        for glyph in run.glyphs() {
            match &**glyph {
                Glyph::Outline(outline) => {
                    if let Some(bbox) =
                        path_bbox(&outline.outline(), props.transform * glyph.transform())
                    {
                        self.add(bbox);
                    }
                }
                Glyph::Type3(type3) => {
                    type3.interpret(self, props.transform, glyph.transform(), &props.paint);
                }
            }
        }
    }
    fn draw_image(&mut self, image: Image<'a, '_>, props: ImageDrawProps<'a>) {
        let size = Rect::new(
            0.0,
            0.0,
            f64::from(image.width()),
            f64::from(image.height()),
        );
        self.add(transformed_rect(size, props.transform));
    }
    fn pop_clip(&mut self) {}
    fn pop_transparency_group(&mut self) {}
}

struct ContentCollector {
    records: Vec<Record>,
    fonts: Vec<FontTuple>,
    font_indexes: HashMap<u128, u32>,
    type3_font: Option<u32>,
    clips: Vec<ClipTuple>,
    groups: Vec<GroupTuple>,
    marks: Vec<MarkTuple>,
    states: Vec<StateTuple>,
    clip_stack: Vec<u32>,
    group_stack: Vec<u32>,
    mark_stack: Vec<u32>,
    /// The interned state of the current stacks, or `None` after a change.
    state: Option<u32>,
    pending: Option<Pending>,
    outlines: HashMap<u128, Option<GlyphInk>>,
    type3_boxes: HashMap<u128, Option<Rect>>,
    elements: usize,
    max_elements: Option<usize>,
    error: Option<ContentError>,
}

impl ContentCollector {
    fn new(max_elements: Option<usize>) -> Self {
        Self {
            records: Vec::new(),
            fonts: Vec::new(),
            font_indexes: HashMap::new(),
            type3_font: None,
            clips: Vec::new(),
            groups: Vec::new(),
            marks: Vec::new(),
            states: Vec::new(),
            clip_stack: Vec::new(),
            group_stack: Vec::new(),
            mark_stack: Vec::new(),
            state: None,
            pending: None,
            outlines: HashMap::new(),
            type3_boxes: HashMap::new(),
            elements: 0,
            max_elements,
            error: None,
        }
    }

    /// Count `count` more elements against the budget.
    fn charge(&mut self, count: usize) -> Result<(), ContentError> {
        let elements = self.elements.saturating_add(count);
        if let Some(limit) = self.max_elements
            && elements > limit
        {
            return Err(ContentError::ElementCount(limit));
        }
        self.elements = elements;
        Ok(())
    }

    /// Run one callback, keeping its first error and ignoring every later
    /// callback.
    fn run(&mut self, callback: impl FnOnce(&mut Self) -> Result<(), ContentError>) {
        if self.error.is_none()
            && let Err(error) = callback(self)
        {
            self.error = Some(error);
        }
    }

    fn intern_state(&mut self) -> Result<u32, ContentError> {
        if let Some(state) = self.state {
            return Ok(state);
        }
        let mut clips = with_capacity(self.clip_stack.len(), "context clips")?;
        clips.extend_from_slice(&self.clip_stack);
        let mut groups = with_capacity(self.group_stack.len(), "context groups")?;
        groups.extend_from_slice(&self.group_stack);
        let mut marks = with_capacity(self.mark_stack.len(), "context marked content")?;
        marks.extend_from_slice(&self.mark_stack);
        self.charge(1 + clips.len() + groups.len() + marks.len())?;
        let index = index_of(self.states.len())?;
        push(
            &mut self.states,
            (clips, groups, marks),
            "graphics contexts",
        )?;
        self.state = Some(index);
        Ok(index)
    }

    fn item_context(
        &mut self,
        blend: BlendMode,
        soft_mask: Option<&SoftMask<'_>>,
    ) -> Result<ItemContext, ContentError> {
        Ok(ItemContext {
            state: self.intern_state()?,
            blend: blend_code(blend),
            soft_mask: soft_mask_tuple(soft_mask),
        })
    }

    fn outline_font(&mut self, glyph: &OutlineGlyph) -> Result<u32, ContentError> {
        let key = glyph.font_cache_key();
        if let Some(index) = self.font_indexes.get(&key) {
            return Ok(*index);
        }
        let font = match glyph.font_data() {
            Some(data) => (
                data.postscript_name.unwrap_or_default(),
                false,
                data.weight,
                data.is_italic,
                data.is_serif,
                data.is_monospace,
            ),
            None => (String::new(), false, None, false, false, false),
        };
        let index = index_of(self.fonts.len())?;
        self.font_indexes.try_reserve(1).map_err(|error| {
            ContentError::Allocation(format!("failed to grow the font index: {error}"))
        })?;
        push(&mut self.fonts, font, "fonts")?;
        self.font_indexes.insert(key, index);
        Ok(index)
    }

    fn type3_font(&mut self) -> Result<u32, ContentError> {
        if let Some(index) = self.type3_font {
            return Ok(index);
        }
        let index = index_of(self.fonts.len())?;
        push(
            &mut self.fonts,
            (String::new(), true, None, false, false, false),
            "fonts",
        )?;
        self.type3_font = Some(index);
        Ok(index)
    }

    /// The display-space ink box of an outline glyph.
    fn outline_box(
        &mut self,
        glyph: &OutlineGlyph,
        transform: Affine,
    ) -> Result<Option<Rect>, ContentError> {
        let key = glyph.identifier().cache_key();
        if !self.outlines.contains_key(&key) {
            let path = glyph.outline();
            let ink = path_bbox(&path, Affine::IDENTITY).map(|bbox| GlyphInk { bbox, path });
            if self.outlines.len() >= MAX_CACHED_GLYPHS {
                return Ok(ink.and_then(|ink| path_bbox(&ink.path, transform)));
            }
            self.outlines.try_reserve(1).map_err(|error| {
                ContentError::Allocation(format!("failed to grow the outline cache: {error}"))
            })?;
            self.outlines.insert(key, ink);
        }
        let Some(Some(ink)) = self.outlines.get(&key) else {
            return Ok(None);
        };
        if is_axis_aligned(transform) {
            return Ok(Some(transformed_rect(ink.bbox, transform)));
        }
        Ok(path_bbox(&ink.path, transform))
    }

    /// The display-space ink box of a Type 3 glyph. The procedure is measured
    /// once in glyph space; under a transform that rotates by other than a
    /// right angle, the box encloses the rotated glyph-space box.
    fn type3_box<'a>(
        &mut self,
        glyph: &Type3Glyph<'a>,
        paint: &Paint<'a>,
        transform: Affine,
    ) -> Result<Option<Rect>, ContentError> {
        let key = glyph.cache_key();
        let bbox = match self.type3_boxes.get(&key) {
            Some(bbox) => *bbox,
            None => {
                let mut ink = InkBounds { bbox: None };
                glyph.interpret(&mut ink, Affine::IDENTITY, Affine::IDENTITY, paint);
                if self.type3_boxes.len() < MAX_CACHED_GLYPHS {
                    self.type3_boxes.try_reserve(1).map_err(|error| {
                        ContentError::Allocation(format!(
                            "failed to grow the Type 3 cache: {error}"
                        ))
                    })?;
                    self.type3_boxes.insert(key, ink.bbox);
                }
                ink.bbox
            }
        };
        Ok(bbox.map(|bbox| transformed_rect(bbox, transform)))
    }

    fn record_path(
        &mut self,
        path: &BezPath,
        props: &DrawProps<'_>,
        fill: Option<FillRule>,
        stroke: Option<&StrokeProps>,
        pending: Option<Pending>,
    ) -> Result<(), ContentError> {
        let Some(segments) = segments(path, props.transform)? else {
            return Ok(());
        };
        let Some(bbox) = path_bbox(path, props.transform).filter(|bbox| rect_is_finite(*bbox))
        else {
            return Ok(());
        };
        let stroke = stroke
            .map(|stroke| StrokeRecord::new(&props.paint, stroke, props.transform))
            .transpose()?;
        let fill_paint = fill.map(|_| paint_tuple(&props.paint)).transpose()?;
        if !stroke.as_ref().is_none_or(StrokeRecord::is_finite)
            || !fill_paint.as_ref().is_none_or(paint_is_finite)
        {
            return Ok(());
        }
        let context = self.item_context(props.blend_mode, props.soft_mask.as_ref())?;
        if fill.is_none()
            && pending == Some(Pending::Path)
            && let Some(Record::Path(previous)) = self.records.last_mut()
            && previous.stroke.is_none()
            && previous.context == context
            && previous.segments.equals(&segments)
        {
            previous.stroke = stroke;
            return Ok(());
        }
        self.charge(1 + segments.count())?;
        let fill_only = stroke.is_none();
        let record = PathRecord {
            segments,
            bbox,
            fill: fill_paint,
            even_odd: fill == Some(FillRule::EvenOdd),
            stroke,
            context,
        };
        push(&mut self.records, Record::Path(record), "content items")?;
        if fill_only {
            self.pending = Some(Pending::Path);
        }
        Ok(())
    }

    fn record_text<'a>(
        &mut self,
        run: &GlyphRun<'_, 'a>,
        props: &DrawProps<'a>,
        mode: &DrawMode,
        pending: Option<Pending>,
    ) -> Result<(), ContentError> {
        let glyphs = run.glyphs();
        let Some(first) = glyphs.first() else {
            return Ok(());
        };
        let mut matrices = with_capacity(glyphs.len().saturating_mul(6), "glyph matrices")?;
        for glyph in glyphs {
            let matrix = props.transform * glyph.transform() * Affine::scale(1000.0);
            matrices.extend_from_slice(&matrix.as_coeffs());
        }
        if !matrices.iter().all(|value| value.is_finite()) {
            return Ok(());
        }
        let (render_mode, fill, stroke) = match mode {
            DrawMode::Fill(_) => (RENDER_FILL, Some(paint_tuple(&props.paint)?), None),
            DrawMode::Stroke(stroke) => (
                RENDER_STROKE,
                None,
                Some(StrokeRecord::new(&props.paint, stroke, props.transform)?),
            ),
            DrawMode::FillAndStroke(_, stroke) => (
                RENDER_FILL_STROKE,
                Some(paint_tuple(&props.paint)?),
                Some(StrokeRecord::new(&props.paint, stroke, props.transform)?),
            ),
            DrawMode::Invisible => (RENDER_INVISIBLE, None, None),
        };
        if !stroke.as_ref().is_none_or(StrokeRecord::is_finite)
            || !fill.as_ref().is_none_or(paint_is_finite)
        {
            return Ok(());
        }
        let context = self.item_context(props.blend_mode, props.soft_mask.as_ref())?;
        if render_mode == RENDER_STROKE
            && pending == Some(Pending::Text)
            && let Some(Record::Text(previous)) = self.records.last_mut()
            && previous.render_mode == RENDER_FILL
            && previous.context == context
            && previous.matrices == matrices
        {
            previous.render_mode = RENDER_FILL_STROKE;
            previous.stroke = stroke;
            return Ok(());
        }
        self.charge(1 + glyphs.len())?;
        let font = match &**first {
            Glyph::Outline(outline) => self.outline_font(outline)?,
            Glyph::Type3(_) => self.type3_font()?,
        };
        let mut ids = with_capacity(glyphs.len(), "glyph IDs")?;
        let mut texts = with_capacity(glyphs.len(), "glyph text")?;
        let mut advances = with_capacity(glyphs.len(), "glyph advances")?;
        let mut boxes = with_capacity(glyphs.len(), "glyph boxes")?;
        let mut ink: Option<Rect> = None;
        let mut extent: Option<Rect> = None;
        for glyph in glyphs {
            let combined = props.transform * glyph.transform();
            let origin = combined * Point::ZERO;
            let (id, width, bbox) = match &**glyph {
                Glyph::Outline(outline) => (
                    Some(outline.glyph_id().to_u32()),
                    outline.advance_width().map(f64::from),
                    self.outline_box(outline, combined)?,
                ),
                Glyph::Type3(type3) => (None, None, self.type3_box(type3, &props.paint, combined)?),
            };
            // Advances are in hayro's 1000-unit glyph space, like outlines.
            let end = width
                .map(|width| combined * Point::new(width, 0.0))
                .filter(|end| end.x.is_finite() && end.y.is_finite());
            let advance = end.map(|end| (end - origin).hypot());
            let bbox = bbox.filter(|bbox| rect_is_finite(*bbox));
            let reach = Rect::from_points(origin, end.unwrap_or(origin));
            extent = Some(extent.map_or(reach, |extent| extent.union(reach)));
            if let Some(bbox) = bbox {
                ink = Some(ink.map_or(bbox, |ink| ink.union(bbox)));
            }
            ids.push(id);
            texts.push(glyph_text(glyph)?);
            advances.push(advance);
            boxes.push(bbox.map(bbox_tuple));
        }
        let Some(bbox) = ink.or(extent) else {
            return Ok(());
        };
        let record = TextRunRecord {
            ids,
            texts,
            matrices,
            advances,
            boxes,
            font,
            render_mode,
            fill,
            stroke,
            bbox,
            context,
        };
        push(&mut self.records, Record::Text(record), "content items")?;
        if render_mode == RENDER_FILL {
            self.pending = Some(Pending::Text);
        }
        Ok(())
    }

    fn record_image(
        &mut self,
        image: &Image<'_, '_>,
        props: &ImageDrawProps<'_>,
    ) -> Result<(), ContentError> {
        let (width, height) = (image.width(), image.height());
        let matrix =
            props.transform * Affine::scale_non_uniform(f64::from(width), f64::from(height));
        let bbox = transformed_rect(Rect::new(0.0, 0.0, 1.0, 1.0), matrix);
        if !affine_is_finite(matrix) || !rect_is_finite(bbox) {
            return Ok(());
        }
        let context = self.item_context(props.blend_mode, props.soft_mask.as_ref())?;
        self.charge(1)?;
        let default_resolution = (DEFAULT_IMAGE_RESOLUTION, DEFAULT_IMAGE_RESOLUTION);
        let record = match image {
            // hayro does not expose the stream of an image mask.
            Image::Stencil(_) => ImageRecord {
                xref: 0,
                format: (width, height, 1, 1, String::new()),
                is_stencil: true,
                mask: MASK_NONE,
                encoded_size: 0,
                resolution: default_resolution,
                matrix: matrix.as_coeffs(),
                bbox,
                context,
            },
            Image::Raster(raster) => {
                let stream = raster.stream();
                let dict = stream.dict();
                let (components, color_space) = image_color_space(dict);
                let bits = dict
                    .get::<u8>(b"BitsPerComponent")
                    .or_else(|| dict.get::<u8>(b"BPC"))
                    .unwrap_or(0);
                let mask = if dict.contains_key(b"SMask") {
                    MASK_SOFT
                } else if dict.get::<Stream<'_>>(b"Mask").is_some() {
                    MASK_EXPLICIT
                } else if dict.get::<Array<'_>>(b"Mask").is_some() {
                    MASK_COLOR_KEY
                } else {
                    MASK_NONE
                };
                let raw = stream.raw_data();
                let resolution = if stream.filters().last() == Some(&Filter::DctDecode) {
                    jpeg_resolution(&raw)
                } else {
                    default_resolution
                };
                ImageRecord {
                    xref: u32::try_from(stream.obj_id().obj_number).unwrap_or(0),
                    format: (width, height, bits, components, color_space),
                    is_stencil: false,
                    mask,
                    encoded_size: raw.len(),
                    resolution,
                    matrix: matrix.as_coeffs(),
                    bbox,
                    context,
                }
            }
        };
        push(&mut self.records, Record::Image(record), "content items")
    }

    fn push_clip(&mut self, clip: &ClipPath) -> Result<(), ContentError> {
        let Some(segments) = segments(&clip.path, Affine::IDENTITY)? else {
            // A clip that no finite path describes leaves nothing visible.
            return self.push_clip_entry(empty_segments(), false, Rect::ZERO);
        };
        let bbox = path_bbox(&clip.path, Affine::IDENTITY)
            .filter(|bbox| rect_is_finite(*bbox))
            .unwrap_or(Rect::ZERO);
        self.push_clip_entry(segments, clip.fill == FillRule::EvenOdd, bbox)
    }

    fn push_clip_entry(
        &mut self,
        segments: Segments,
        even_odd: bool,
        bbox: Rect,
    ) -> Result<(), ContentError> {
        self.charge(1 + segments.count())?;
        let index = index_of(self.clips.len())?;
        push(
            &mut self.clips,
            (segments.kinds, segments.coords, even_odd, bbox_tuple(bbox)),
            "clips",
        )?;
        push(&mut self.clip_stack, index, "the clip stack")?;
        self.state = None;
        Ok(())
    }

    fn push_group(
        &mut self,
        opacity: f32,
        mask: Option<&SoftMask<'_>>,
        blend: BlendMode,
    ) -> Result<(), ContentError> {
        self.charge(1)?;
        let index = index_of(self.groups.len())?;
        push(
            &mut self.groups,
            (f64::from(opacity), blend_code(blend), soft_mask_tuple(mask)),
            "transparency groups",
        )?;
        push(&mut self.group_stack, index, "the group stack")?;
        self.state = None;
        Ok(())
    }

    fn begin_mark(&mut self, tag: &[u8], mcid: Option<i32>) -> Result<(), ContentError> {
        self.charge(1)?;
        let mut name = String::new();
        let text = String::from_utf8_lossy(tag);
        name.try_reserve_exact(text.len()).map_err(|error| {
            ContentError::Allocation(format!("failed to allocate a marked-content tag: {error}"))
        })?;
        name.push_str(&text);
        let index = index_of(self.marks.len())?;
        push(&mut self.marks, (name, mcid), "marked content")?;
        push(&mut self.mark_stack, index, "the marked-content stack")?;
        self.state = None;
        Ok(())
    }

    fn finish(self, clip: Option<BBox>) -> Result<PageContent, ContentError> {
        if let Some(error) = self.error {
            return Err(error);
        }
        let mut items = with_capacity(self.records.len(), "content items")?;
        for record in self.records {
            if let Some((x0, y0, x1, y1)) = clip {
                let bbox = record.bbox();
                if bbox.x0 > x1 || bbox.x1 < x0 || bbox.y0 > y1 || bbox.y1 < y0 {
                    continue;
                }
            }
            items.push(record.into_item());
        }
        Ok(PageContent {
            items,
            fonts: self.fonts,
            clips: self.clips,
            groups: self.groups,
            marks: self.marks,
            states: self.states,
        })
    }
}

fn empty_segments() -> Segments {
    Segments {
        kinds: Vec::new(),
        coords: Vec::new(),
    }
}

fn index_of(length: usize) -> Result<u32, ContentError> {
    u32::try_from(length)
        .map_err(|_| ContentError::Allocation("content model index overflowed".to_owned()))
}

/// A glyph's Unicode text, or `None` when hayro finds no mapping.
fn glyph_text(glyph: &Glyph<'_>) -> Result<Option<String>, ContentError> {
    Ok(match glyph.as_unicode() {
        None => None,
        Some(BfString::Char(character)) => {
            let mut text = String::new();
            text.try_reserve_exact(character.len_utf8())
                .map_err(|error| {
                    ContentError::Allocation(format!("failed to allocate glyph text: {error}"))
                })?;
            text.push(character);
            Some(text)
        }
        Some(BfString::String(text)) => Some(text),
    })
}

impl<'a> Device<'a> for ContentCollector {
    fn draw_path(&mut self, path: &BezPath, props: DrawProps<'a>, mode: &DrawMode) {
        let pending = self.pending.take();
        self.run(|this| match mode {
            DrawMode::Fill(rule) => this.record_path(path, &props, Some(*rule), None, None),
            DrawMode::Stroke(stroke) => this.record_path(path, &props, None, Some(stroke), pending),
            DrawMode::FillAndStroke(rule, stroke) => {
                this.record_path(path, &props, Some(*rule), Some(stroke), None)
            }
            DrawMode::Invisible => Ok(()),
        });
    }

    fn push_clip_path(&mut self, clip: &ClipPath) {
        self.pending = None;
        self.run(|this| this.push_clip(clip));
    }

    fn push_transparency_group(
        &mut self,
        opacity: f32,
        mask: Option<SoftMask<'a>>,
        blend: BlendMode,
    ) {
        self.pending = None;
        self.run(|this| this.push_group(opacity, mask.as_ref(), blend));
    }

    fn draw_glyph_run(&mut self, run: &GlyphRun<'_, 'a>, props: DrawProps<'a>, mode: &DrawMode) {
        let pending = self.pending.take();
        self.run(|this| this.record_text(run, &props, mode, pending));
    }

    fn draw_image(&mut self, image: Image<'a, '_>, props: ImageDrawProps<'a>) {
        self.pending = None;
        self.run(|this| this.record_image(&image, &props));
    }

    fn pop_clip(&mut self) {
        self.pending = None;
        self.clip_stack.pop();
        self.state = None;
    }

    fn pop_transparency_group(&mut self) {
        self.pending = None;
        self.group_stack.pop();
        self.state = None;
    }

    fn begin_marked_content(&mut self, tag: &[u8], mcid: Option<i32>) {
        self.pending = None;
        self.run(|this| this.begin_mark(tag, mcid));
    }

    fn end_marked_content(&mut self) {
        self.pending = None;
        if self.mark_stack.pop().is_some() {
            self.state = None;
        }
    }
}

/// Interpret one page and collect its content.
pub(crate) fn collect_page_content(
    pdf: &Pdf,
    page: &Page<'_>,
    settings: InterpreterSettings,
    clip: Option<BBox>,
    max_elements: Option<usize>,
) -> Result<PageContent, ContentError> {
    let cache = InterpreterCache::new();
    let mut context = extraction_context(pdf, page, &cache, settings);
    let mut collector = ContentCollector::new(max_elements);
    interpret_page(page, &mut context, &mut collector);
    collector.finish(clip)
}
