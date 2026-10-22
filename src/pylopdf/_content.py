"""The content model: what hayro paints on a page, as immutable values.

``Page.content()`` returns a :class:`Content` sequence of :class:`TextRun`,
:class:`Path`, and :class:`Image` items in paint order and display space.
Every item carries the :class:`GraphicsContext` that encloses it, and items
painted under the same clips, transparency groups, and marked content share
one context object.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias, overload

from pylopdf._geometry import Matrix, Point, Rect

if TYPE_CHECKING:
    from collections.abc import Iterator


class BlendMode(enum.StrEnum):
    """A blend mode of ISO 32000-2 §11.3.5."""

    NORMAL = "normal"
    MULTIPLY = "multiply"
    SCREEN = "screen"
    OVERLAY = "overlay"
    DARKEN = "darken"
    LIGHTEN = "lighten"
    COLOR_DODGE = "color_dodge"
    COLOR_BURN = "color_burn"
    HARD_LIGHT = "hard_light"
    SOFT_LIGHT = "soft_light"
    DIFFERENCE = "difference"
    EXCLUSION = "exclusion"
    HUE = "hue"
    SATURATION = "saturation"
    COLOR = "color"
    LUMINOSITY = "luminosity"


class FillRule(enum.StrEnum):
    """The rule that decides which areas a path fills or clips."""

    NONZERO = "nonzero"
    EVEN_ODD = "even_odd"


class LineCap(enum.StrEnum):
    """The shape at the open ends of a stroke."""

    BUTT = "butt"
    ROUND = "round"
    SQUARE = "square"


class LineJoin(enum.StrEnum):
    """The shape at the corners of a stroke."""

    MITER = "miter"
    ROUND = "round"
    BEVEL = "bevel"


class TextRenderMode(enum.StrEnum):
    """How a text run paints its glyphs.

    hayro paints the clipping modes 4 to 6 as their painting part and adds
    their clip to the context of later items; text in mode 7, which only
    clips, paints nothing and is not an item.
    """

    FILL = "fill"
    STROKE = "stroke"
    FILL_STROKE = "fill_stroke"
    INVISIBLE = "invisible"


class ShadingType(enum.IntEnum):
    """A shading type of ISO 32000-2 §8.7.4.5.

    hayro reads free-form and lattice-form triangle meshes (types 4 and 5)
    into the same triangles, so both report :attr:`TRIANGLE_MESH`.
    """

    FUNCTION = 1
    AXIAL = 2
    RADIAL = 3
    TRIANGLE_MESH = 4
    COONS_PATCH_MESH = 6
    TENSOR_PATCH_MESH = 7


class SoftMaskType(enum.StrEnum):
    """What a soft mask reads from its group: alpha or luminosity."""

    ALPHA = "alpha"
    LUMINOSITY = "luminosity"


class ImageMask(enum.StrEnum):
    """The kind of mask that an image dictionary names."""

    SOFT = "soft"
    """``/SMask``: a grayscale soft-mask image."""
    EXPLICIT = "explicit"
    """``/Mask`` with a stencil-mask image."""
    COLOR_KEY = "color_key"
    """``/Mask`` with color ranges that are not painted."""


@dataclass(frozen=True, slots=True)
class MoveTo:
    """Start a new subpath at ``to``."""

    to: Point


@dataclass(frozen=True, slots=True)
class LineTo:
    """Draw a line to ``to``."""

    to: Point


@dataclass(frozen=True, slots=True)
class CurveTo:
    """Draw a cubic Bézier curve to ``to`` with two control points."""

    c1: Point
    c2: Point
    to: Point


@dataclass(frozen=True, slots=True)
class ClosePath:
    """Close the current subpath with a line to its start."""


Segment: TypeAlias = MoveTo | LineTo | CurveTo | ClosePath


@dataclass(frozen=True, slots=True)
class Color:
    """A solid paint, as the sRGB color and alpha that hayro converts it to.

    Components range from 0 to 1. The color space and its own components
    are not exposed yet.
    """

    red: float
    green: float
    blue: float
    alpha: float = 1.0

    @property
    def rgb(self) -> tuple[float, float, float]:
        """Return ``(red, green, blue)``."""
        return (self.red, self.green, self.blue)


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadingPattern:
    """A paint that a shading fills, from a shading pattern or ``sh``."""

    shading_type: ShadingType | None
    """The shading type, or ``None`` for a shading that hayro could not
    read, which paints nothing."""
    matrix: Matrix
    """The transform from shading space to display space."""
    opacity: float


@dataclass(frozen=True, slots=True, kw_only=True)
class TilingPattern:
    """A paint that repeats a pattern cell."""

    bbox: Rect
    """The pattern cell in pattern space."""
    x_step: float
    y_step: float
    matrix: Matrix
    """The transform from pattern space to display space."""


Paint: TypeAlias = Color | ShadingPattern | TilingPattern


@dataclass(frozen=True, slots=True, kw_only=True)
class Stroke:
    """How a path or text run is stroked, in display space.

    ``width`` and the dash lengths are scaled by the largest scale of the
    transform in force when the stroke was painted.
    """

    paint: Paint
    width: float = 1.0
    cap: LineCap = LineCap.BUTT
    join: LineJoin = LineJoin.MITER
    miter_limit: float = 10.0
    dashes: tuple[float, ...] = ()
    dash_offset: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class SoftMask:
    """A soft mask: the transparency group that masks what it encloses."""

    xref: int
    """The object number of the mask's group."""
    subtype: SoftMaskType


@dataclass(frozen=True, slots=True, kw_only=True)
class Clip:
    """A clipping path in display space."""

    segments: tuple[Segment, ...]
    fill_rule: FillRule
    bbox: Rect


@dataclass(frozen=True, slots=True, kw_only=True)
class TransparencyGroup:
    """A transparency group and what it applies to everything inside it."""

    opacity: float
    blend_mode: BlendMode
    soft_mask: SoftMask | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkedContent:
    """A marked-content sequence (``BMC`` or ``BDC``)."""

    tag: str
    mcid: int | None
    """The marked-content identifier of a ``BDC`` property list."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicsContext:
    """The clips, transparency groups, and marked content around an item.

    Each tuple lists its entries outermost first.
    """

    clips: tuple[Clip, ...]
    groups: tuple[TransparencyGroup, ...]
    marked_content: tuple[MarkedContent, ...]

    @property
    def opacity(self) -> float:
        """Return the product of the groups' opacities."""
        return math.prod(group.opacity for group in self.groups)

    @property
    def clip_bbox(self) -> Rect | None:
        """Return the intersection of the clips' boxes, or ``None`` without clips."""
        if not self.clips:
            return None
        x0, y0, x1, y1 = self.clips[0].bbox
        for clip in self.clips[1:]:
            cx0, cy0, cx1, cy1 = clip.bbox
            x0, y0, x1, y1 = max(x0, cx0), max(y0, cy0), min(x1, cx1), min(y1, cy1)
        return Rect(x0, y0, max(x0, x1), max(y0, y1))


@dataclass(frozen=True, slots=True, kw_only=True)
class FontRef:
    """The font that a text run uses, as hayro reads its font program.

    The font dictionary's object number, subtype, and embedding are not
    exposed yet. All Type 3 fonts share one reference.
    """

    name: str
    """The PostScript name of the font program, or ``""`` when hayro reads
    none, as for Type 1 and Type 3 fonts."""
    is_type3: bool
    weight: int | None
    is_italic: bool
    is_serif: bool
    is_monospace: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Glyph:
    """One glyph of a text run."""

    glyph_id: int | None
    """The glyph ID in the font program, or ``None`` for a Type 3 glyph."""
    text: str | None
    """The Unicode text, or ``None`` when the font maps the glyph to none."""
    matrix: Matrix
    """The transform from text space at a font size of 1 to display space;
    ``matrix.e`` and ``matrix.f`` are the origin."""
    advance: float | None
    """The display-space advance, or ``None`` when hayro does not know it,
    as for Type 3 glyphs."""
    bbox: Rect | None
    """The ink box: the exact box of the outline, or of what a Type 3
    procedure paints; ``None`` for a glyph without ink, such as a space."""

    @property
    def origin(self) -> Point:
        """Return the glyph origin on the baseline."""
        return Point(self.matrix.e, self.matrix.f)

    @property
    def size(self) -> float:
        """Return the display-space font size."""
        return math.hypot(self.matrix.c, self.matrix.d)


@dataclass(frozen=True, slots=True, kw_only=True)
class TextRun:
    """The glyphs that one text-showing operator paints."""

    glyphs: tuple[Glyph, ...]
    font: FontRef
    render_mode: TextRenderMode
    fill: Paint | None
    stroke: Stroke | None
    bbox: Rect
    """The union of the glyphs' ink boxes, or of their origins and advances
    when no glyph has ink."""
    context: GraphicsContext
    blend_mode: BlendMode
    soft_mask: SoftMask | None

    @property
    def text(self) -> str:
        """Return the Unicode text of the glyphs that map to one."""
        return "".join(glyph.text for glyph in self.glyphs if glyph.text is not None)

    @property
    def size(self) -> float:
        """Return the display-space font size of the first glyph."""
        return self.glyphs[0].size


@dataclass(frozen=True, slots=True, kw_only=True)
class Path:
    """A path that is filled, stroked, or both."""

    segments: tuple[Segment, ...]
    fill: Paint | None
    fill_rule: FillRule
    stroke: Stroke | None
    bbox: Rect
    """The exact box of the geometry; a stroke extends half its width
    beyond it."""
    context: GraphicsContext
    blend_mode: BlendMode
    soft_mask: SoftMask | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Image:
    """An image painted on the page."""

    xref: int
    """The image object number, or 0 for an inline image or an image mask."""
    width: int
    height: int
    bits: int
    """Bits per component."""
    colorspace: str
    """The color space name, or ``""`` for an image mask."""
    components: int
    is_stencil: bool
    """Whether the image is a stencil mask painted with the fill paint."""
    mask: ImageMask | None
    encoded_size: int
    resolution: tuple[int, int]
    """The resolution that a JPEG's JFIF header states, else 96 by 96."""
    matrix: Matrix
    """The transform from the unit square to display space."""
    bbox: Rect
    context: GraphicsContext
    blend_mode: BlendMode
    soft_mask: SoftMask | None


ContentItem: TypeAlias = TextRun | Path | Image


@dataclass(frozen=True, slots=True)
class Content(Sequence[ContentItem]):
    """The items that a page paints, in paint order."""

    items: tuple[ContentItem, ...]

    @overload
    def __getitem__(self, index: int) -> ContentItem: ...
    @overload
    def __getitem__(self, index: slice) -> tuple[ContentItem, ...]: ...
    def __getitem__(self, index: int | slice) -> ContentItem | tuple[ContentItem, ...]:
        """Return one item, or a tuple of items for a slice."""
        return self.items[index]

    def __len__(self) -> int:
        """Return the number of items."""
        return len(self.items)

    def __iter__(self) -> Iterator[ContentItem]:
        """Iterate over the items in paint order."""
        return iter(self.items)

    @property
    def text_runs(self) -> tuple[TextRun, ...]:
        """Return the text runs in paint order."""
        return tuple(item for item in self.items if isinstance(item, TextRun))

    @property
    def paths(self) -> tuple[Path, ...]:
        """Return the paths in paint order."""
        return tuple(item for item in self.items if isinstance(item, Path))

    @property
    def images(self) -> tuple[Image, ...]:
        """Return the images in paint order."""
        return tuple(item for item in self.items if isinstance(item, Image))


_BLEND_MODES = tuple(BlendMode)
_LINE_CAPS = (LineCap.BUTT, LineCap.ROUND, LineCap.SQUARE)
_LINE_JOINS = (LineJoin.MITER, LineJoin.ROUND, LineJoin.BEVEL)
_RENDER_MODES = tuple(TextRenderMode)
_IMAGE_MASKS = (None, ImageMask.SOFT, ImageMask.EXPLICIT, ImageMask.COLOR_KEY)
_CLOSE_PATH = ClosePath()
_TEXT_RUN = 0
_PATH = 1
_COLOR = 0
_SHADING = 1
_MOVE = ord("m")
_LINE = ord("l")
_CURVE = ord("c")


def _matrix(values: Sequence[float]) -> Matrix:
    """Build a matrix from six finite floats that the core already checked."""
    return Matrix._make(values)


# The core keeps only finite geometry, so points and boxes skip the checks of
# their constructors, which cost more than the rest of a path's record.
_new_tuple = tuple.__new__


def _point(x: float, y: float) -> Point:
    """Build a point from two finite floats that the core already checked."""
    return _new_tuple(Point, (x, y))


def _rect(values: Sequence[float]) -> Rect:
    """Build a rectangle from four finite floats that the core already checked."""
    return _new_tuple(Rect, values)


def _glyph(glyph_id: int | None, text: str | None, matrix: Matrix, advance: float | None, bbox: Rect | None) -> Glyph:
    """Build a :class:`Glyph` through its slots.

    A page can paint hundreds of thousands of glyphs, and the frozen
    dataclass's ``__init__`` sets each field through ``object.__setattr__``.
    """
    glyph = _new_object(Glyph)
    _set_glyph_id(glyph, glyph_id)
    _set_glyph_text(glyph, text)
    _set_glyph_matrix(glyph, matrix)
    _set_glyph_advance(glyph, advance)
    _set_glyph_bbox(glyph, bbox)
    return glyph


_new_object = object.__new__
_set_glyph_id = Glyph.glyph_id.__set__
_set_glyph_text = Glyph.text.__set__
_set_glyph_matrix = Glyph.matrix.__set__
_set_glyph_advance = Glyph.advance.__set__
_set_glyph_bbox = Glyph.bbox.__set__


def _segments(kinds: bytes, coords: list[float]) -> tuple[Segment, ...]:
    segments: list[Segment] = []
    position = 0
    for kind in kinds:
        if kind == _MOVE:
            segments.append(MoveTo(_point(coords[position], coords[position + 1])))
            position += 2
        elif kind == _LINE:
            segments.append(LineTo(_point(coords[position], coords[position + 1])))
            position += 2
        elif kind == _CURVE:
            x1, y1, x2, y2, x3, y3 = coords[position : position + 6]
            segments.append(CurveTo(_point(x1, y1), _point(x2, y2), _point(x3, y3)))
            position += 6
        else:
            segments.append(_CLOSE_PATH)
    return tuple(segments)


def _soft_mask(raw: tuple[int, bool] | None) -> SoftMask | None:
    if raw is None:
        return None
    xref, alpha = raw
    return SoftMask(xref=xref, subtype=SoftMaskType.ALPHA if alpha else SoftMaskType.LUMINOSITY)


class _Converter:
    """Turn the core's tuples into values, sharing equal paints."""

    def __init__(self, fonts: list[Any], states: _Contexts) -> None:
        self.fonts = [
            FontRef(
                name=name,
                is_type3=is_type3,
                weight=weight,
                is_italic=is_italic,
                is_serif=is_serif,
                is_monospace=is_monospace,
            )
            for name, is_type3, weight, is_italic, is_serif, is_monospace in fonts
        ]
        self.states = states
        self.paints: dict[tuple[int, tuple[float, ...]], Paint] = {}

    def paint(self, raw: tuple[int, list[float]]) -> Paint:
        kind, values = raw
        key = (kind, tuple(values))
        paint = self.paints.get(key)
        if paint is not None:
            return paint
        if kind == _COLOR:
            paint = Color(*values)
        elif kind == _SHADING:
            shading_type = int(values[0])
            paint = ShadingPattern(
                shading_type=ShadingType(shading_type) if shading_type else None,
                matrix=_matrix(values[1:7]),
                opacity=values[7],
            )
        else:
            paint = TilingPattern(
                bbox=_rect(values[0:4]),
                x_step=values[4],
                y_step=values[5],
                matrix=_matrix(values[6:12]),
            )
        self.paints[key] = paint
        return paint

    def stroke(self, raw: Any) -> Stroke | None:  # noqa: ANN401 - a nested core tuple.
        if raw is None:
            return None
        paint, width, cap, join, miter_limit, dashes, dash_offset = raw
        return Stroke(
            paint=self.paint(paint),
            width=width,
            cap=_LINE_CAPS[cap],
            join=_LINE_JOINS[join],
            miter_limit=miter_limit,
            dashes=tuple(dashes),
            dash_offset=dash_offset,
        )

    def text_run(self, raw: Any) -> TextRun:  # noqa: ANN401 - a nested core tuple.
        glyphs, font, render_mode, fill, stroke, bbox, (state, blend, soft_mask) = raw
        ids, texts, matrices, advances, boxes = glyphs
        return TextRun(
            glyphs=tuple(
                _glyph(
                    glyph_id,
                    text,
                    _new_tuple(Matrix, matrices[index * 6 : index * 6 + 6]),
                    advance,
                    None if box is None else _new_tuple(Rect, box),
                )
                for index, (glyph_id, text, advance, box) in enumerate(zip(ids, texts, advances, boxes, strict=True))
            ),
            font=self.fonts[font],
            render_mode=_RENDER_MODES[render_mode],
            fill=None if fill is None else self.paint(fill),
            stroke=self.stroke(stroke),
            bbox=_rect(bbox),
            context=self.states[state],
            blend_mode=_BLEND_MODES[blend],
            soft_mask=_soft_mask(soft_mask),
        )

    def path(self, raw: Any) -> Path:  # noqa: ANN401 - a nested core tuple.
        kinds, coords, bbox, fill, even_odd, stroke, (state, blend, soft_mask) = raw
        return Path(
            segments=_segments(kinds, coords),
            fill=None if fill is None else self.paint(fill),
            fill_rule=FillRule.EVEN_ODD if even_odd else FillRule.NONZERO,
            stroke=self.stroke(stroke),
            bbox=_rect(bbox),
            context=self.states[state],
            blend_mode=_BLEND_MODES[blend],
            soft_mask=_soft_mask(soft_mask),
        )

    def image(self, raw: Any) -> Image:  # noqa: ANN401 - a nested core tuple.
        (
            xref,
            (width, height, bits, components, colorspace),
            is_stencil,
            mask,
            encoded_size,
            resolution,
            matrix,
            bbox,
            (state, blend, soft_mask),
        ) = raw
        return Image(
            xref=xref,
            width=width,
            height=height,
            bits=bits,
            colorspace=colorspace,
            components=components,
            is_stencil=is_stencil,
            mask=_IMAGE_MASKS[mask],
            encoded_size=encoded_size,
            resolution=tuple(resolution),
            matrix=_matrix(matrix),
            bbox=_rect(bbox),
            context=self.states[state],
            blend_mode=_BLEND_MODES[blend],
            soft_mask=_soft_mask(soft_mask),
        )


class _Contexts:
    """The core's graphics contexts, each built when an item first refers to it."""

    def __init__(self, clips: list[Any], groups: list[Any], marks: list[Any], states: list[Any]) -> None:
        self.raw = (clips, groups, marks)
        self.states = states
        self.clips: dict[int, Clip] = {}
        self.groups: dict[int, TransparencyGroup] = {}
        self.marks: dict[int, MarkedContent] = {}
        self.contexts: dict[int, GraphicsContext] = {}

    def clip(self, index: int) -> Clip:
        value = self.clips.get(index)
        if value is None:
            kinds, coords, even_odd, bbox = self.raw[0][index]
            value = self.clips[index] = Clip(
                segments=_segments(kinds, coords),
                fill_rule=FillRule.EVEN_ODD if even_odd else FillRule.NONZERO,
                bbox=_rect(bbox),
            )
        return value

    def group(self, index: int) -> TransparencyGroup:
        value = self.groups.get(index)
        if value is None:
            opacity, blend, soft_mask = self.raw[1][index]
            value = self.groups[index] = TransparencyGroup(
                opacity=opacity,
                blend_mode=_BLEND_MODES[blend],
                soft_mask=_soft_mask(soft_mask),
            )
        return value

    def mark(self, index: int) -> MarkedContent:
        value = self.marks.get(index)
        if value is None:
            tag, mcid = self.raw[2][index]
            value = self.marks[index] = MarkedContent(tag=tag, mcid=mcid)
        return value

    def __getitem__(self, index: int) -> GraphicsContext:
        value = self.contexts.get(index)
        if value is None:
            clip_indexes, group_indexes, mark_indexes = self.states[index]
            value = self.contexts[index] = GraphicsContext(
                clips=tuple(self.clip(clip) for clip in clip_indexes),
                groups=tuple(self.group(group) for group in group_indexes),
                marked_content=tuple(self.mark(mark) for mark in mark_indexes),
            )
        return value


def _content_from_core(raw: Any) -> Content:  # noqa: ANN401 - the core's nested tuples.
    """Build a :class:`Content` from ``_Document.page_content``."""
    items, fonts, clips, groups, marks, states = raw
    converter = _Converter(fonts, _Contexts(clips, groups, marks, states))
    converted: list[ContentItem] = []
    for kind, item in items:
        if kind == _TEXT_RUN:
            converted.append(converter.text_run(item))
        elif kind == _PATH:
            converted.append(converter.path(item))
        else:
            converted.append(converter.image(item))
    return Content(tuple(converted))


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (
    BlendMode,
    FillRule,
    LineCap,
    LineJoin,
    TextRenderMode,
    ShadingType,
    SoftMaskType,
    ImageMask,
    MoveTo,
    LineTo,
    CurveTo,
    ClosePath,
    Color,
    ShadingPattern,
    TilingPattern,
    Stroke,
    SoftMask,
    Clip,
    TransparencyGroup,
    MarkedContent,
    GraphicsContext,
    FontRef,
    Glyph,
    TextRun,
    Path,
    Image,
    Content,
):
    _public.__module__ = "pylopdf"
