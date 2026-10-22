"""Canvas: drawing on a page in paint order, written as one Form XObject."""

from __future__ import annotations

import enum
import math
import os
import warnings as _warnings
from collections.abc import Iterable
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path as FilePath
from typing import TYPE_CHECKING, Literal, cast

from pylopdf._content import (
    BlendMode,
    ClosePath,
    Color,
    CurveTo,
    FillRule,
    LineCap,
    LineJoin,
    LineTo,
    MoveTo,
    Path,
    Segment,
    Stroke,
)
from pylopdf._fonts import Font, _regular_instance, _require_winansi, _resolve_generation_font
from pylopdf._geometry import Matrix, Point, Rect
from pylopdf._hints import hinted_getattr
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    _DEFAULT_MAX_IMAGE_INPUT_SIZE,
    _DEFAULT_MAX_IMAGE_PIXELS,
    _FLOAT32_MAX,
    _validate_optional_positive_int,
)
from pylopdf.pylopdf_core import Pixmap, _Canvas, _Font

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Sequence
    from types import TracebackType
    from typing import Self, TypeAlias

    from pylopdf._create import Builder
    from pylopdf._page import Page
    from pylopdf.pylopdf_core import _Document

    PathLike: TypeAlias = "PathBuilder | Path | Iterable[Segment]"
    _Rgba: TypeAlias = tuple[float, float, float, float]
    _CoreStroke: TypeAlias = tuple[_Rgba, float, int, int, float, list[float], float]

#: The control-point factor that approximates a quarter circle with a cubic curve.
_KAPPA = 4 * (math.sqrt(2) - 1) / 3
#: The height of a wavy line's turning points in units of its breadth.
_WAVE_HEIGHT = 2.4142135623765633
#: The control-point factor of a curve pulled toward a corner, as pymupdf's ``draw_curve``.
_KAPPA_CURVE = 0.55228474983
_ALIGNMENTS = ("left", "center", "right", "justify")
#: The deepest bookmark level, which the core keeps in 16 bits.
_MAX_BOOKMARK_LEVEL = 0xFFFF
_BLACK = Color(0.0, 0.0, 0.0)


def _number(value: object, name: str) -> float:
    """Return a finite number within PDF real range."""
    if isinstance(value, (bool, str, bytes)) or not hasattr(value, "__float__"):
        msg = f"{name} must be a number: {value!r}"
        raise TypeError(msg)
    number = float(cast("float", value))
    if not (math.isfinite(number) and abs(number) <= _FLOAT32_MAX):
        msg = f"{name} must be a finite number within PDF real range: {value!r}"
        raise ValueError(msg)
    return number


def _point(value: Sequence[float], name: str) -> Point:
    """Return a point from a :class:`Point` or a pair of numbers."""
    try:
        point = Point(*value)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a point (x, y): {value!r}"
        raise ValueError(msg) from exc
    _number(point.x, name)
    _number(point.y, name)
    return point


def _rect(value: Sequence[float], name: str) -> Rect:
    """Return a rectangle with a positive size."""
    try:
        rect = Rect(*value)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a rectangle (x0, y0, x1, y1): {value!r}"
        raise ValueError(msg) from exc
    for coordinate in rect:
        _number(coordinate, name)
    if not (rect.x1 > rect.x0 and rect.y1 > rect.y0):
        msg = f"{name} must have x0 < x1 and y0 < y1: {value!r}"
        raise ValueError(msg)
    return rect


def _unit(value: object, name: str) -> float:
    """Return a number from 0 to 1."""
    number = _number(value, name)
    if not 0 <= number <= 1:
        msg = f"{name} must be a number from 0 to 1: {value!r}"
        raise ValueError(msg)
    return number


def _color(value: object, name: str) -> _Rgba:
    """Return the components and alpha of a :class:`Color`."""
    if not isinstance(value, Color):
        msg = f"{name} must be a pylopdf.Color, not {type(value).__name__}"
        raise TypeError(msg)
    return (
        _unit(value.red, f"{name}.red"),
        _unit(value.green, f"{name}.green"),
        _unit(value.blue, f"{name}.blue"),
        _unit(value.alpha, f"{name}.alpha"),
    )


def _stroke(value: Stroke | Color | None) -> _CoreStroke | None:
    """Return a stroke as the core takes it, from a :class:`Stroke` or a :class:`Color`."""
    if value is None:
        return None
    stroke = Stroke(paint=value) if isinstance(value, Color) else value
    if not isinstance(stroke, Stroke):
        msg = f"stroke must be a pylopdf.Stroke or Color, not {type(value).__name__}"
        raise TypeError(msg)
    if not isinstance(stroke.paint, Color):
        msg = "a canvas strokes with a Color; pattern paints cannot be drawn yet"
        raise TypeError(msg)
    width = _number(stroke.width, "stroke.width")
    if not width > 0:
        msg = f"stroke.width must be positive: {stroke.width!r}"
        raise ValueError(msg)
    miter_limit = _number(stroke.miter_limit, "stroke.miter_limit")
    if miter_limit < 1:
        msg = f"stroke.miter_limit must be at least 1: {stroke.miter_limit!r}"
        raise ValueError(msg)
    dashes = [_number(length, "stroke.dashes") for length in stroke.dashes]
    if any(length < 0 for length in dashes) or (dashes and not any(dashes)):
        msg = f"stroke.dashes must be non-negative lengths, not all zero: {stroke.dashes!r}"
        raise ValueError(msg)
    return (
        _color(stroke.paint, "stroke.paint"),
        width,
        list(LineCap).index(LineCap(stroke.cap)),
        list(LineJoin).index(LineJoin(stroke.join)),
        miter_limit,
        dashes,
        _number(stroke.dash_offset, "stroke.dash_offset"),
    )


def _fill(value: Color | None) -> _Rgba | None:
    """Return a fill as the core takes it."""
    return None if value is None else _color(value, "fill")


def _segment_ops(segment: object) -> tuple[str, tuple[float, ...]]:
    """Return the path operator of one segment and its coordinates."""
    if isinstance(segment, MoveTo):
        return "m", tuple(segment.to)
    if isinstance(segment, LineTo):
        return "l", tuple(segment.to)
    if isinstance(segment, CurveTo):
        return "c", (*segment.c1, *segment.c2, *segment.to)
    if isinstance(segment, ClosePath):
        return "h", ()
    msg = f"a path segment must be MoveTo, LineTo, CurveTo, or ClosePath, not {type(segment).__name__}"
    raise TypeError(msg)


def _segments(path: object) -> tuple[str, list[float]]:
    """Return the operators and coordinates of a path, a builder, or segments."""
    if isinstance(path, (PathBuilder, Path)):
        segments: Iterable[object] = path.segments
    elif isinstance(path, Iterable):
        segments = path
    else:
        msg = f"path must be a PathBuilder, a Path, or segments, not {type(path).__name__}"
        raise TypeError(msg)
    operators: list[str] = []
    coordinates: list[float] = []
    for segment in segments:
        operator, values = _segment_ops(segment)
        operators.append(operator)
        coordinates.extend(values)
    if not operators:
        msg = "the path has no segments"
        raise ValueError(msg)
    if operators[0] != "m":
        msg = "a path starts with MoveTo"
        raise ValueError(msg)
    return "".join(operators), coordinates


def _canvas_font(text: str, font: Font | None) -> _Font:
    """Return the face that draws ``text``.

    Without ``font``, WinAnsi text is drawn in Helvetica and other text in
    the face of an installed font extra for its script, at the regular weight.
    """
    if font is not None:
        if not isinstance(font, Font):
            msg = f"font must be a pylopdf.Font, not {type(font).__name__}"
            raise TypeError(msg)
        core_font = font._font
    else:
        base_font, source, package_font = _resolve_generation_font(text, "helv", None, 0, None)
        if isinstance(source, FilePath):
            core_font = _Font.from_file(os.fspath(source), 0, _DEFAULT_MAX_FONT_INPUT_SIZE, package_font)
        elif isinstance(source, bytes):
            core_font = _Font.from_bytes(source, 0, _DEFAULT_MAX_FONT_INPUT_SIZE)
        else:
            core_font = _Font.standard(str(base_font))
        if package_font is not None:
            core_font = _regular_instance(core_font)
    if core_font.standard_name is not None:
        _require_winansi(text)
    return core_font


def _size(value: object) -> float:
    """Return a positive font size."""
    size = _number(value, "size")
    if not size > 0:
        msg = f"size must be positive: {value!r}"
        raise ValueError(msg)
    return size


def _line_height(value: object) -> float | None:
    """Return a positive line-height multiplier, or ``None`` for the font's default."""
    if value is None:
        return None
    line_height = _number(value, "line_height")
    if not line_height > 0:
        msg = f"line_height must be positive: {value!r}"
        raise ValueError(msg)
    return line_height


def _text_paint(
    fill: Color | None,
    stroke: Stroke | Color | None,
) -> tuple[_Rgba | None, _CoreStroke | None]:
    """Return the fill and stroke of text, which needs at least one."""
    if fill is None and stroke is None:
        msg = "text needs a fill or a stroke"
        raise ValueError(msg)
    return _fill(fill), _stroke(stroke)


class TagRole(enum.StrEnum):
    """A structure type of a tagged new document (ISO 32000-1 §14.8.4), by its PDF name.

    :attr:`ARTIFACT` marks content that is not part of the document's
    logical structure, such as page headers, footers, and decoration.
    """

    PART = "Part"
    ARTICLE = "Art"
    SECTION = "Sect"
    DIV = "Div"
    BLOCK_QUOTE = "BlockQuote"
    CAPTION = "Caption"
    TOC = "TOC"
    TOC_ITEM = "TOCI"
    INDEX = "Index"
    NON_STRUCT = "NonStruct"
    PARAGRAPH = "P"
    HEADING_1 = "H1"
    HEADING_2 = "H2"
    HEADING_3 = "H3"
    HEADING_4 = "H4"
    HEADING_5 = "H5"
    HEADING_6 = "H6"
    LIST = "L"
    LIST_ITEM = "LI"
    LABEL = "Lbl"
    LIST_BODY = "LBody"
    TABLE = "Table"
    TABLE_ROW = "TR"
    TABLE_HEADER = "TH"
    TABLE_DATA = "TD"
    TABLE_HEAD = "THead"
    TABLE_BODY = "TBody"
    TABLE_FOOT = "TFoot"
    SPAN = "Span"
    QUOTE = "Quote"
    NOTE = "Note"
    REFERENCE = "Reference"
    BIB_ENTRY = "BibEntry"
    CODE = "Code"
    FIGURE = "Figure"
    FORMULA = "Formula"
    ARTIFACT = "Artifact"


@dataclass(frozen=True, slots=True, kw_only=True)
class TextFit:
    """How text fits a :meth:`Canvas.textbox`."""

    spare_height: float
    """The height left below the last line, or, when negative, the height
    that the text lacks."""

    @property
    def fits(self) -> bool:
        """Return whether the text fits and was drawn."""
        return self.spare_height >= 0


class PathBuilder:
    """Geometry for :meth:`Canvas.path` and :meth:`Canvas.clip`, built from segments.

    Every method adds segments in display coordinates and returns the
    builder, so calls chain: ``PathBuilder().move_to(a).line_to(b).close()``.
    The constructors (:meth:`rect`, :meth:`ellipse`, :meth:`circle`,
    :meth:`polygon`, :meth:`sector`, :meth:`zigzag`, and :meth:`wave`) each
    add complete subpaths.
    """

    __slots__ = ("_current", "_segments", "_start")

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self) -> None:
        """Start an empty path."""
        self._segments: list[Segment] = []
        self._current: Point | None = None
        self._start: Point | None = None

    @property
    def segments(self) -> tuple[Segment, ...]:
        """Return the segments added so far."""
        return tuple(self._segments)

    @property
    def current_point(self) -> Point | None:
        """Return the end of the last segment, or ``None`` before the first."""
        return self._current

    def move_to(self, point: Sequence[float]) -> Self:
        """Start a subpath at ``point``."""
        to = _point(point, "point")
        self._segments.append(MoveTo(to))
        self._current = self._start = to
        return self

    def _require_current(self) -> Point:
        if self._current is None:
            msg = "start the path with move_to()"
            raise ValueError(msg)
        return self._current

    def line_to(self, point: Sequence[float]) -> Self:
        """Add a line to ``point``."""
        self._require_current()
        to = _point(point, "point")
        self._segments.append(LineTo(to))
        self._current = to
        return self

    def curve_to(self, control1: Sequence[float], control2: Sequence[float], point: Sequence[float]) -> Self:
        """Add a cubic Bézier curve to ``point``."""
        self._require_current()
        to = _point(point, "point")
        self._segments.append(CurveTo(_point(control1, "control1"), _point(control2, "control2"), to))
        self._current = to
        return self

    def quad_to(self, control: Sequence[float], point: Sequence[float]) -> Self:
        """Add a quadratic Bézier curve to ``point``, as the equal cubic curve."""
        start = self._require_current()
        c = _point(control, "control")
        to = _point(point, "point")
        control1 = Point(start.x + 2 / 3 * (c.x - start.x), start.y + 2 / 3 * (c.y - start.y))
        control2 = Point(to.x + 2 / 3 * (c.x - to.x), to.y + 2 / 3 * (c.y - to.y))
        return self.curve_to(control1, control2, to)

    def close(self) -> Self:
        """Close the current subpath with a line to its start."""
        self._require_current()
        self._segments.append(ClosePath())
        self._current = self._start
        return self

    def rect(self, rect: Sequence[float], *, radius: float = 0) -> Self:
        """Add a rectangle, with corners rounded by ``radius`` points."""
        r = _rect(rect, "rect")
        rounding = _number(radius, "radius")
        if rounding < 0:
            msg = f"radius must not be negative: {radius!r}"
            raise ValueError(msg)
        rounding = min(rounding, (r.x1 - r.x0) / 2, (r.y1 - r.y0) / 2)
        if rounding == 0:
            self.move_to((r.x0, r.y0)).line_to((r.x1, r.y0)).line_to((r.x1, r.y1)).line_to((r.x0, r.y1))
            return self.close()
        k = rounding * (1 - _KAPPA)
        self.move_to((r.x0 + rounding, r.y0))
        self.line_to((r.x1 - rounding, r.y0))
        self.curve_to((r.x1 - k, r.y0), (r.x1, r.y0 + k), (r.x1, r.y0 + rounding))
        self.line_to((r.x1, r.y1 - rounding))
        self.curve_to((r.x1, r.y1 - k), (r.x1 - k, r.y1), (r.x1 - rounding, r.y1))
        self.line_to((r.x0 + rounding, r.y1))
        self.curve_to((r.x0 + k, r.y1), (r.x0, r.y1 - k), (r.x0, r.y1 - rounding))
        self.line_to((r.x0, r.y0 + rounding))
        self.curve_to((r.x0, r.y0 + k), (r.x0 + k, r.y0), (r.x0 + rounding, r.y0))
        return self.close()

    def ellipse(self, rect: Sequence[float]) -> Self:
        """Add the ellipse inside ``rect``."""
        r = _rect(rect, "rect")
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        rx, ry = (r.x1 - r.x0) / 2, (r.y1 - r.y0) / 2
        kx, ky = rx * _KAPPA, ry * _KAPPA
        self.move_to((r.x1, cy))
        self.curve_to((r.x1, cy + ky), (cx + kx, r.y1), (cx, r.y1))
        self.curve_to((cx - kx, r.y1), (r.x0, cy + ky), (r.x0, cy))
        self.curve_to((r.x0, cy - ky), (cx - kx, r.y0), (cx, r.y0))
        self.curve_to((cx + kx, r.y0), (r.x1, cy - ky), (r.x1, cy))
        return self.close()

    def circle(self, center: Sequence[float], radius: float) -> Self:
        """Add a circle."""
        c = _point(center, "center")
        size = _number(radius, "radius")
        if not size > 0:
            msg = f"radius must be positive: {radius!r}"
            raise ValueError(msg)
        return self.ellipse((c.x - size, c.y - size, c.x + size, c.y + size))

    def polygon(self, points: Iterable[Sequence[float]], *, close: bool = True) -> Self:
        """Add straight segments through ``points``, closed unless ``close`` is false."""
        corners = [_point(point, "points") for point in points]
        if len(corners) < 2:  # noqa: PLR2004 - a segment needs two points.
            msg = "points must contain at least two points"
            raise ValueError(msg)
        self.move_to(corners[0])
        for corner in corners[1:]:
            self.line_to(corner)
        return self.close() if close else self

    def sector(
        self,
        center: Sequence[float],
        radius: float,
        start: float,
        sweep: float,
        *,
        pie: bool = True,
    ) -> Self:
        """Add an arc of ``sweep`` degrees around ``center``, from ``start`` degrees.

        Angles run clockwise on the page from the positive x axis. With
        ``pie``, lines join the arc's ends to the centre, which closes it.
        """
        c = _point(center, "center")
        size = _number(radius, "radius")
        if not size > 0:
            msg = f"radius must be positive: {radius!r}"
            raise ValueError(msg)
        first = math.radians(_number(start, "start"))
        total = math.radians(_number(sweep, "sweep"))
        if total == 0:
            msg = "sweep must not be zero"
            raise ValueError(msg)
        total = max(-2 * math.pi, min(2 * math.pi, total))
        steps = max(1, math.ceil(abs(total) / (math.pi / 2) - 1e-9))
        step = total / steps
        factor = 4 / 3 * math.tan(step / 4)

        def at(angle: float) -> Point:
            return Point(c.x + size * math.cos(angle), c.y + size * math.sin(angle))

        begin = at(first)
        if pie:
            self.move_to(c).line_to(begin)
        else:
            self.move_to(begin)
        angle = first
        for _ in range(steps):
            end = angle + step
            p0, p3 = at(angle), at(end)
            control1 = Point(p0.x - factor * size * math.sin(angle), p0.y + factor * size * math.cos(angle))
            control2 = Point(p3.x + factor * size * math.sin(end), p3.y - factor * size * math.cos(end))
            self.curve_to(control1, control2, p3)
            angle = end
        return self.close() if pie else self

    @staticmethod
    def _turns(start: Point, end: Point, breadth: float, height: float, *, on_line: bool) -> list[Point]:
        """Return the turning points of a wave from ``start`` to ``end``."""
        if not breadth > 0:
            msg = f"breadth must be positive: {breadth!r}"
            raise ValueError(msg)
        length = math.dist(start, end)
        count = 4 * round(length / (4 * breadth))
        if count < 4:  # noqa: PLR2004 - one full phase has four steps.
            msg = "the points are too close for one wave of this breadth"
            raise ValueError(msg)
        step = length / count
        ux, uy = (end.x - start.x) / length, (end.y - start.y) / length
        points = []
        for i in range(1, count):
            if i % 4 == 1:
                offset = -height
            elif i % 4 == 3:  # noqa: PLR2004 - the second turn of each phase.
                offset = height
            elif on_line:
                offset = 0.0
            else:
                continue
            u, v = i * step, offset * step
            points.append(Point(start.x + u * ux - v * uy, start.y + u * uy + v * ux))
        return points

    def zigzag(self, start: Sequence[float], end: Sequence[float], *, breadth: float = 2) -> Self:
        """Add a zigzag line from ``start`` to ``end`` whose teeth are ``breadth`` points wide."""
        first = _point(start, "start")
        last = _point(end, "end")
        turns = self._turns(first, last, _number(breadth, "breadth"), 1.0, on_line=False)
        return self.polygon([first, *turns, last], close=False)

    def wave(self, start: Sequence[float], end: Sequence[float], *, breadth: float = 2) -> Self:
        """Add a wavy line from ``start`` to ``end`` whose waves are ``breadth`` points wide."""
        first = _point(start, "start")
        last = _point(end, "end")
        points = [first, *self._turns(first, last, _number(breadth, "breadth"), _WAVE_HEIGHT, on_line=True), last]
        self.move_to(first)
        for i in range(0, len(points) - 2, 2):
            begin, corner, finish = points[i], points[i + 1], points[i + 2]
            self.curve_to(
                (begin.x + (corner.x - begin.x) * _KAPPA_CURVE, begin.y + (corner.y - begin.y) * _KAPPA_CURVE),
                (finish.x + (corner.x - finish.x) * _KAPPA_CURVE, finish.y + (corner.y - finish.y) * _KAPPA_CURVE),
                finish,
            )
        return self

    def __repr__(self) -> str:
        """Return the segment count."""
        return f"PathBuilder({len(self._segments)} segments)"


class _CanvasScope(AbstractContextManager[None]):
    """A canvas scope, opened when its block starts and closed when it ends."""

    __slots__ = ("_depth", "_open", "_record")

    def __init__(self, record: _Canvas, open_scope: Callable[[], int]) -> None:
        self._record = record
        self._open = open_scope
        self._depth: int | None = None

    def __enter__(self) -> None:
        if self._depth is not None:
            msg = "a canvas scope can be entered once"
            raise ValueError(msg)
        self._depth = self._open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._depth is not None:
            self._record.pop(self._depth)


class Canvas:
    """Drawing on one page in paint order, written as one Form XObject.

    Get one with :meth:`Page.draw`, or with :meth:`Builder.page` for a page
    of a new document, and use it in a ``with`` block: the drawing is
    written when the block ends normally and discarded after an exception.
    Everything is in display coordinates, the top-left-origin space of
    :attr:`Page.rect`, and drawn in the order of the calls. :meth:`tag` and
    :meth:`bookmark` belong to new documents.

    Paths take a ``fill`` :class:`Color` and a ``stroke``, a :class:`Stroke`
    or a :class:`Color` for a 1-point line; a path with neither is stroked
    with a 1-point black line. Text is drawn in a :class:`Font`: an OpenType
    face is shaped with HarfRust and embedded as a subset by krilla, once per
    canvas however many calls use it, and a Standard 14 font is written as
    text operators. The scopes :meth:`clip`, :meth:`transform`,
    :meth:`opacity`, and :meth:`blend` are context managers that apply to
    everything drawn inside them.

    A canvas holds at most 1,048,576 path points, of paths and clips
    together, and nests at most 16 scopes; more raise :class:`LimitError`
    with code ``path_point_count`` or ``canvas_scope_depth``. A canvas
    collected with drawing it never wrote emits a :class:`ResourceWarning`.
    """

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, page: Page, *, underlay: bool = False) -> None:
        """Start an empty canvas on ``page``; :meth:`Page.draw` is the usual way."""
        from pylopdf._page import Page  # noqa: PLC0415 - pylopdf._page imports this module.

        if not isinstance(page, Page):
            msg = f"page must be a pylopdf.Page, not {type(page).__name__}"
            raise TypeError(msg)
        width, height = page.parent._doc.page_display_size(page._page_number())
        self._page: Page | None = page
        self._builder: Builder | None = None
        self._overlay = not underlay
        self._record = _Canvas(width, height)
        self._closed = False

    @classmethod
    def _new_page(cls, builder: Builder, width: float, height: float) -> Canvas:
        """Start an empty canvas for a page of a new document."""
        canvas = cls.__new__(cls)
        canvas._page = None
        canvas._builder = builder
        canvas._overlay = True
        canvas._record = _Canvas(width, height, new_document=True)
        canvas._closed = False
        return canvas

    def _target(self) -> _Document | None:
        """Return the core document of a page canvas, or ``None`` for a new document's page."""
        return None if self._page is None else self._page.parent._doc

    def __enter__(self) -> Self:
        """Return the canvas; leaving the block writes it."""
        self._check_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Write the drawing after a normal exit; discard it after an exception."""
        if self._closed:
            return
        self._closed = True
        if exc_type is not None:
            self._record.clear()
            return
        if self._builder is not None:
            try:
                self._builder._core.add_page(self._record)
            finally:
                self._record.clear()
            return
        page = cast("Page", self._page)
        document = page.parent
        try:
            document._doc.commit_canvas(page._page_number(), self._record, self._overlay)
        finally:
            self._record.clear()
        document._emit_warnings()

    def __del__(self) -> None:
        """Warn when drawing is discarded without being written."""
        try:
            pending = not self._record.is_empty()
        except AttributeError:
            return
        if pending:
            with suppress(Exception):
                _warnings.warn(
                    "a Canvas was discarded with drawing that was never written; use it in a with block",
                    ResourceWarning,
                    stacklevel=2,
                    source=self,
                )

    def _check_open(self) -> None:
        if self._closed:
            msg = "the canvas was already written; start another one"
            raise ValueError(msg)

    def path(
        self,
        path: PathLike,
        *,
        fill: Color | None = None,
        stroke: Stroke | Color | None = None,
        fill_rule: FillRule = FillRule.NONZERO,
    ) -> None:
        """Draw a path: a :class:`PathBuilder`, a :class:`Path` item's geometry, or segments."""
        self._check_open()
        operators, coordinates = _segments(path)
        if fill is None and stroke is None:
            stroke = _BLACK
        filled = _fill(fill)
        self._record.add_path(
            operators,
            coordinates,
            None if filled is None else (filled, FillRule(fill_rule) is FillRule.EVEN_ODD),
            _stroke(stroke),
        )

    def line(self, start: Sequence[float], end: Sequence[float], *, stroke: Stroke | Color | None = None) -> None:
        """Draw a straight line, with a 1-point black stroke by default."""
        self.path(PathBuilder().move_to(start).line_to(end), stroke=_BLACK if stroke is None else stroke)

    def rect(
        self,
        rect: Sequence[float],
        *,
        radius: float = 0,
        fill: Color | None = None,
        stroke: Stroke | Color | None = None,
    ) -> None:
        """Draw a rectangle, with corners rounded by ``radius`` points."""
        self.path(PathBuilder().rect(rect, radius=radius), fill=fill, stroke=stroke)

    def circle(
        self,
        center: Sequence[float],
        radius: float,
        *,
        fill: Color | None = None,
        stroke: Stroke | Color | None = None,
    ) -> None:
        """Draw a circle."""
        self.path(PathBuilder().circle(center, radius), fill=fill, stroke=stroke)

    def ellipse(
        self,
        rect: Sequence[float],
        *,
        fill: Color | None = None,
        stroke: Stroke | Color | None = None,
    ) -> None:
        """Draw the ellipse inside ``rect``."""
        self.path(PathBuilder().ellipse(rect), fill=fill, stroke=stroke)

    def polygon(
        self,
        points: Iterable[Sequence[float]],
        *,
        close: bool = True,
        fill: Color | None = None,
        stroke: Stroke | Color | None = None,
        fill_rule: FillRule = FillRule.NONZERO,
    ) -> None:
        """Draw straight segments through ``points``, closed unless ``close`` is false."""
        self.path(PathBuilder().polygon(points, close=close), fill=fill, stroke=stroke, fill_rule=fill_rule)

    def bezier(
        self,
        start: Sequence[float],
        control1: Sequence[float],
        control2: Sequence[float],
        end: Sequence[float],
        *,
        stroke: Stroke | Color | None = None,
    ) -> None:
        """Draw a cubic Bézier curve, with a 1-point black stroke by default."""
        builder = PathBuilder().move_to(start).curve_to(control1, control2, end)
        self.path(builder, stroke=_BLACK if stroke is None else stroke)

    def text(  # noqa: PLR0913 - the text style options.
        self,
        text: str,
        at: Sequence[float],
        *,
        font: Font | None = None,
        size: float = 11,
        fill: Color | None = _BLACK,
        stroke: Stroke | Color | None = None,
        line_height: float | None = None,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> None:
        """Draw text whose first baseline starts at ``at``.

        Lines are split with :meth:`str.splitlines` and placed ``line_height``
        times ``size`` apart, by default at the font's own line height.
        ``font`` defaults to Helvetica for WinAnsi text and otherwise to the
        face of an installed font extra for the text's script. Text is
        filled, stroked, or both; at least one is needed. The text is limited
        to 1 MiB of UTF-8 by default (``max_text_size``, ``None`` for
        trusted input), refused with :class:`LimitError` code
        ``text_input_size``.
        """
        self._check_open()
        if not isinstance(text, str):
            msg = f"text must be a string: {text!r}"
            raise TypeError(msg)
        _validate_optional_positive_int("max_text_size", max_text_size)
        if max_text_size is not None and len(text) > max_text_size:
            # Each character takes at least one UTF-8 byte; the core counts exactly.
            text = text[: max_text_size + 1]
        lines = text.splitlines()
        if not any(lines):
            return
        origin = _point(at, "at")
        font_size = _size(size)
        filled, stroked = _text_paint(fill, stroke)
        core_font = _canvas_font("\n".join(lines), font)
        self._record.add_text(
            tuple(origin),
            lines,
            core_font,
            font_size,
            _line_height(line_height),
            filled,
            stroked,
            max_text_size,
        )

    def textbox(  # noqa: PLR0913 - the text style options.
        self,
        rect: Sequence[float],
        text: str,
        *,
        font: Font | None = None,
        size: float = 11,
        align: Literal["left", "center", "right", "justify"] = "left",
        line_height: float | None = None,
        fill: Color | None = _BLACK,
        stroke: Stroke | Color | None = None,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> TextFit:
        """Wrap text inside ``rect`` and report how it fits.

        Lines break at Unicode line-break opportunities (UAX #14) and, for a
        word wider than the box, between grapheme clusters. When the text
        does not fit, nothing is drawn and :attr:`TextFit.spare_height` is
        the negative shortfall. Tabs become single spaces. The font, paint,
        and size options follow :meth:`text`.
        """
        self._check_open()
        box = _rect(rect, "rect")
        if not isinstance(text, str):
            msg = f"text must be a string: {text!r}"
            raise TypeError(msg)
        if align not in _ALIGNMENTS:
            msg = f"align must be one of {', '.join(_ALIGNMENTS)}: {align!r}"
            raise ValueError(msg)
        _validate_optional_positive_int("max_text_size", max_text_size)
        if max_text_size is not None and len(text) > max_text_size:
            text = text[: max_text_size + 1]
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(1)
        if not normalized:
            return TextFit(spare_height=box.y1 - box.y0)
        font_size = _size(size)
        filled, stroked = _text_paint(fill, stroke)
        core_font = _canvas_font(normalized, font)
        spare = self._record.add_textbox(
            tuple(box),
            normalized,
            core_font,
            font_size,
            _line_height(line_height),
            _ALIGNMENTS.index(align),
            filled,
            stroked,
            max_text_size,
        )
        return TextFit(spare_height=spare)

    def image(  # noqa: PLR0913 - the image source and placement options.
        self,
        rect: Sequence[float],
        source: str | os.PathLike[str] | bytes | bytearray | memoryview | Pixmap,
        *,
        keep_aspect: bool = True,
        rotate: int = 0,
        max_size: int | None = _DEFAULT_MAX_IMAGE_INPUT_SIZE,
        max_pixels: int | None = _DEFAULT_MAX_IMAGE_PIXELS,
    ) -> None:
        """Draw a JPEG or PNG file, encoded bytes, or a :class:`Pixmap` into ``rect``.

        ``rotate`` turns the image clockwise by 0, 90, 180, or 270 degrees,
        and ``keep_aspect`` centres it in ``rect`` at its aspect ratio. An
        image drawn several times, or placed in this document before, is
        stored once. Encoded input is limited to 64 MiB and PNG input to
        64,000,000 pixels by default (``None`` for trusted input), refused
        with :class:`LimitError` code ``image_input_size`` or
        ``image_pixel_count``.
        """
        self._check_open()
        box = tuple(_rect(rect, "rect"))
        if isinstance(rotate, bool) or not isinstance(rotate, int) or rotate % 90 != 0:
            msg = f"rotate must be a multiple of 90: {rotate!r}"
            raise ValueError(msg)
        rotation = rotate % 360
        _validate_optional_positive_int("max_size", max_size)
        _validate_optional_positive_int("max_pixels", max_pixels)
        target = self._target()
        keep = bool(keep_aspect)
        if isinstance(source, Pixmap):
            self._record.add_pixmap(target, source, box, keep, rotation)
        elif isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
            self._record.add_image(target, data, box, keep, rotation, max_size, max_pixels)
        elif isinstance(source, (str, os.PathLike)):
            self._record.add_image_file(target, os.fspath(source), box, keep, rotation, max_size, max_pixels)
        else:
            msg = f"source must be a path, bytes, or a Pixmap, not {type(source).__name__}"
            raise TypeError(msg)

    def page(self, rect: Sequence[float], source: Page, *, keep_aspect: bool = True) -> None:
        """Draw another page, of any open document or this one, into ``rect``.

        The page is drawn as it was when this call was made, as vector
        content with its fonts and images. A page drawn several times, or
        placed in this document before while its document was unchanged, is
        stored once.
        """
        from pylopdf._page import Page  # noqa: PLC0415 - pylopdf._page imports this module.

        self._check_open()
        if not isinstance(source, Page):
            msg = f"source must be a pylopdf.Page, not {type(source).__name__}"
            raise TypeError(msg)
        if self._page is None:
            msg = "a new document cannot place other pages yet; draw them on the finished document's pages"
            raise ValueError(msg)
        box = tuple(_rect(rect, "rect"))
        self._record.add_page(
            self._page.parent._doc,
            source.parent._doc,
            source._page_number(),
            box,
            bool(keep_aspect),
        )

    def clip(self, path: PathLike, *, fill_rule: FillRule = FillRule.NONZERO) -> AbstractContextManager[None]:
        """Clip what the block draws to a path."""
        self._check_open()
        operators, coordinates = _segments(path)
        even_odd = FillRule(fill_rule) is FillRule.EVEN_ODD
        return _CanvasScope(self._record, lambda: self._record.push_clip(operators, coordinates, even_odd))

    def transform(self, matrix: Matrix | Sequence[float]) -> AbstractContextManager[None]:
        """Transform what the block draws by ``matrix``, applied before the enclosing transforms."""
        self._check_open()
        try:
            m = Matrix(*matrix)
        except (TypeError, ValueError) as exc:
            msg = f"matrix must be a Matrix or six numbers: {matrix!r}"
            raise ValueError(msg) from exc
        a, b, c, d, e, f = (_number(value, "matrix") for value in m)
        values = (a, b, c, d, e, f)
        return _CanvasScope(self._record, lambda: self._record.push_transform(values))

    def opacity(self, alpha: float) -> AbstractContextManager[None]:
        """Composite what the block draws as one group, then draw it at ``alpha``."""
        self._check_open()
        value = _unit(alpha, "alpha")
        return _CanvasScope(self._record, lambda: self._record.push_opacity(value))

    def blend(self, mode: BlendMode | str) -> AbstractContextManager[None]:
        """Blend what the block draws with the content below it."""
        self._check_open()
        try:
            blend_mode = BlendMode(mode)
        except ValueError as exc:
            msg = f"mode must be a BlendMode: {mode!r}"
            raise ValueError(msg) from exc
        code = list(BlendMode).index(blend_mode)
        return _CanvasScope(self._record, lambda: self._record.push_blend(code))

    def bookmark(self, title: str, *, level: int = 1, at: Sequence[float] = (0, 0)) -> None:
        """Add an outline entry of a new document that shows ``at`` on this page.

        Entries keep the order of the calls, page by page, and an entry at
        ``level`` 2 or more nests under the closest earlier entry one level
        up; a level can grow by one at a time. PDF/UA documents need an
        outline.
        """
        self._check_open()
        if self._builder is None:
            msg = "bookmarks belong to a new document; set an open document's outline instead"
            raise ValueError(msg)
        if not isinstance(title, str):
            msg = f"title must be a string: {title!r}"
            raise TypeError(msg)
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= _MAX_BOOKMARK_LEVEL:
            msg = f"level must be a positive integer: {level!r}"
            raise ValueError(msg)
        point = _point(at, "at")
        self._record.add_bookmark(title, level, tuple(point))

    def tag(
        self,
        role: TagRole | str,
        *,
        alt: str | None = None,
        lang: str | None = None,
        title: str | None = None,
        actual_text: str | None = None,
    ) -> AbstractContextManager[None]:
        """Make what the block draws a structure element of a tagged new document.

        Scopes nest into the structure tree, in the order of the calls, and
        each line of text inside one is a span of it. :attr:`TagRole.ARTIFACT`
        marks content outside the logical structure, as is everything drawn
        outside any tag scope. ``alt`` is the alternate description that
        figures and formulas need, ``title`` a heading's title, ``lang`` the
        element's BCP 47 language, and ``actual_text`` the text that replaces
        the element's content for extraction and assistive technology.
        Elements cannot span pages.
        """
        self._check_open()
        if self._builder is None:
            msg = "tag() builds the structure tree of a new document; tagging an open document is not supported yet"
            raise ValueError(msg)
        if not self._builder.tagged:
            msg = "tag() needs a tagged document: pass tagged=True, or a standard that requires tagging, to create()"
            raise ValueError(msg)
        try:
            tag_role = TagRole(role)
        except ValueError as exc:
            msg = f"role must be a TagRole: {role!r}"
            raise ValueError(msg) from exc
        texts = {"alt": alt, "lang": lang, "title": title, "actual_text": actual_text}
        for name, value in texts.items():
            if value is not None and not isinstance(value, str):
                msg = f"{name} must be a string or None: {value!r}"
                raise TypeError(msg)
        if lang is not None:
            from pylopdf._document import _is_language_tag  # noqa: PLC0415 - pylopdf._document imports this module.

            if not _is_language_tag(lang):
                msg = f"lang must be a BCP 47 language tag: {lang!r}"
                raise ValueError(msg)
        value = tag_role.value
        return _CanvasScope(
            self._record,
            lambda: self._record.push_tag(value, alt, lang, title, actual_text),
        )

    def draw(self, item: Path) -> None:
        """Paint a :class:`Path` item that :meth:`Page.content` returned.

        The item's clips, its groups' opacity and blend modes, and its own
        blend mode are applied; soft masks are not, and pattern paints cannot
        be drawn yet. Text runs and images follow in a later release.
        """
        self._check_open()
        if not isinstance(item, Path):
            msg = f"draw() paints Path items, not {type(item).__name__}"
            raise TypeError(msg)
        if item.fill is not None and not isinstance(item.fill, Color):
            msg = "a canvas fills with a Color; pattern paints cannot be drawn yet"
            raise TypeError(msg)
        with self._item_context(item):
            self.path(item.segments, fill=item.fill, stroke=item.stroke, fill_rule=item.fill_rule)

    @contextmanager
    def _item_context(self, item: Path) -> Generator[None]:
        """Apply an item's clips, groups, and blend mode around its painting."""
        scopes: list[AbstractContextManager[None]] = [
            self.clip(clip.segments, fill_rule=clip.fill_rule) for clip in item.context.clips
        ]
        for group in item.context.groups:
            if group.blend_mode is not BlendMode.NORMAL:
                scopes.append(self.blend(group.blend_mode))
            if group.opacity < 1:
                scopes.append(self.opacity(group.opacity))
        if item.blend_mode is not BlendMode.NORMAL:
            scopes.append(self.blend(item.blend_mode))
        entered: list[AbstractContextManager[None]] = []
        try:
            for scope in scopes:
                scope.__enter__()
                entered.append(scope)
            yield
        finally:
            for scope in reversed(entered):
                scope.__exit__(None, None, None)


# The package re-exports these classes; reprs, pickles, and documentation name them.
for _type in (Canvas, PathBuilder, TagRole, TextFit):
    _type.__module__ = "pylopdf"
del _type
