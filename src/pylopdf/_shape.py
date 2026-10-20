"""Shape: vector paths and text drawn on one page in one step."""

from __future__ import annotations

import math
import os
import warnings as _warnings
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pylopdf._fonts import (
    _regular_instance,
    _require_winansi,
    _resolve_font_source,
    _resolve_generation_font,
    _validate_font_choice,
)
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    _FLOAT32_MAX,
    _insert_text_lines,
    _validate_generated_text_lines,
    _validate_lineheight,
    _validate_optional_positive_int,
    _validate_rect,
    _validate_textbox_options,
    _validate_utf8_text_input,
)
from pylopdf._types import TEXT_ALIGN_LEFT, Point, Rect
from pylopdf.pylopdf_core import LimitError, PdfError, _Font, _Shape

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from types import TracebackType
    from typing import Any, Self

    from pylopdf._document import Document
    from pylopdf._fonts import Font
    from pylopdf._page import Page
    from pylopdf._types import FontLanguage


#: Path points that one :class:`Shape` may hold until it is committed.
_MAX_SHAPE_PATH_POINTS = 1_048_576
#: pymupdf's tolerance for zero lengths in Shape geometry.
_SHAPE_EPSILON = 1e-5
#: The control-point factor of pymupdf's ``draw_curve``.
_SHAPE_KAPPA = 0.55228474983
#: The height of ``draw_squiggle``'s helper points in units of its breadth.
_SQUIGGLE_HEIGHT = 2.4142135623765633
_SHAPE_CAPS_AND_JOINS = (0, 1, 2)


def _shape_number(value: object, name: str) -> float:
    """Return a finite number within PDF real range."""
    if isinstance(value, (bool, str, bytes)) or not hasattr(value, "__float__"):
        msg = f"{name} must be a number: {value!r}"
        raise PdfError(msg)
    number = float(cast("float", value))
    if not (math.isfinite(number) and abs(number) <= _FLOAT32_MAX):
        msg = f"{name} must be a finite number within PDF real range: {value!r}"
        raise PdfError(msg)
    return number


def _path_point_limit() -> LimitError:
    """Return the refusal for a shape that would hold too many path points."""
    code = "path_point_count"
    return LimitError(code, f"a shape exceeds the {_MAX_SHAPE_PATH_POINTS}-point path safety limit")


def _distance(a: Point, b: Point) -> float:
    """Return the distance between two points as pymupdf computes it."""
    dx, dy = a.x - b.x, a.y - b.y
    return math.sqrt(dx * dx + dy * dy)


def _shape_point(value: object, name: str) -> Point:
    """Return a display-coordinate point ``(x, y)``."""
    try:
        x, y = cast("Sequence[object]", value)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a point (x, y): {value!r}"
        raise PdfError(msg) from exc
    return Point(_shape_number(x, name), _shape_number(y, name))


def _shape_rect(value: object, name: str) -> Rect:
    """Return a display-coordinate rectangle ``(x0, y0, x1, y1)``."""
    try:
        x0, y0, x1, y1 = cast("Sequence[object]", value)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a rectangle (x0, y0, x1, y1): {value!r}"
        raise PdfError(msg) from exc
    return Rect(*(_shape_number(v, name) for v in (x0, y0, x1, y1)))


def _shape_quad(value: object, name: str) -> tuple[Point, Point, Point, Point]:
    """Return pymupdf's quad corners ``(ul, ur, ll, lr)`` of a rectangle or quad."""
    try:
        items = list(cast("Iterable[object]", value))
    except TypeError as exc:
        msg = f"{name} must be a rectangle or four points: {value!r}"
        raise PdfError(msg) from exc
    if len(items) != 4:  # noqa: PLR2004 - a rectangle or a quad has four items.
        msg = f"{name} must be a rectangle or four points: {value!r}"
        raise PdfError(msg)
    if hasattr(items[0], "__float__"):
        x0, y0, x1, y1 = _shape_rect(items, name)
        return Point(x0, y0), Point(x1, y0), Point(x0, y1), Point(x1, y1)
    ul, ur, ll, lr = (_shape_point(item, name) for item in items)
    return ul, ur, ll, lr


def _shape_color(value: object, name: str) -> tuple[float, ...] | None:
    """Return a 1-, 3-, or 4-component colour from 0 to 1, or ``None``."""
    if value is None:
        return None
    channels = (value,) if hasattr(value, "__float__") and not isinstance(value, (bool, str)) else value
    try:
        values = tuple(float(channel) for channel in cast("Iterable[Any]", channels))
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a gray, RGB, or CMYK colour with components from 0 to 1: {value!r}"
        raise PdfError(msg) from exc
    if len(values) not in (1, 3, 4) or not all(0.0 <= channel <= 1.0 for channel in values):
        msg = f"{name} must be a gray, RGB, or CMYK colour with components from 0 to 1: {value!r}"
        raise PdfError(msg)
    return values


def _shape_unit(value: object, name: str) -> float:
    """Return a number from 0 to 1."""
    number = _shape_number(value, name)
    if not 0.0 <= number <= 1.0:
        msg = f"{name} must be a number from 0 to 1: {value!r}"
        raise PdfError(msg)
    return number


def _shape_line_style(value: object, name: str) -> int:
    """Return a line cap or join style: 0, 1, or 2."""
    if isinstance(value, bool) or value not in _SHAPE_CAPS_AND_JOINS:
        msg = f"{name} must be 0, 1, or 2: {value!r}"
        raise PdfError(msg)
    return cast("int", value)


def _shape_dashes(value: object) -> tuple[list[float], float] | None:
    """Parse pymupdf's PDF dash string, such as ``"[3 4] 0"``; ``None`` is solid."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        msg = f"dashes must be a PDF dash pattern such as '[3 4] 0': {value!r}"
        raise PdfError(msg)
    opening, _, rest = value.strip().partition("[")
    array, closing, phase = rest.partition("]")
    try:
        if opening or not closing:
            raise ValueError  # noqa: TRY301 - one message for every malformed pattern.
        lengths = [_shape_number(float(item), "dashes") for item in array.split()]
        offset = _shape_number(float(phase), "dashes")
    except (ValueError, PdfError) as exc:
        msg = f"dashes must be a PDF dash pattern such as '[3 4] 0': {value!r}"
        raise PdfError(msg) from exc
    if not lengths:
        return None
    if any(length < 0 for length in lengths) or not any(lengths):
        msg = f"dash lengths must be non-negative numbers, not all zero: {value!r}"
        raise PdfError(msg)
    return lengths, offset


class Shape:
    """Vector paths and text drawn on one page in one step, as pymupdf's ``Shape``.

    Create one with :meth:`Page.new_shape`. The ``draw_*`` methods add path
    segments in display coordinates and return pymupdf's end point;
    :meth:`finish` strokes and fills everything drawn since the previous
    ``finish``; :meth:`insert_text` and :meth:`insert_textbox` add text; and
    :meth:`commit` draws the whole shape on the page. As in pymupdf, the
    finished paths are drawn in order and all text above them, in insertion
    order. A shape can be committed again after more drawing.

    A commit places one Form XObject. Paths and text in embedded OpenType
    fonts are generated together, so each font is embedded once per commit
    however many lines use it. Coordinates follow pylopdf's display space with
    a top-left origin and the page rotation resolved, and colours are gray,
    RGB, or CMYK tuples from 0 to 1.

    ``with page.new_shape() as shape:`` commits when the block ends normally
    and draws nothing after an exception. A shape discarded with drawing that
    was never committed emits a :class:`ResourceWarning`. Where pymupdf
    silently drops or blackens drawing, pylopdf raises :class:`PdfError`:
    :meth:`commit` refuses drawing that :meth:`finish` did not style, and
    :meth:`finish` refuses a style that draws nothing. ``morph=`` and ``oc=``
    are not available. A shape holds at most 1,048,576 path points until it
    is committed; more raise :class:`LimitError` with code
    ``path_point_count``.
    """

    #: The page that :meth:`commit` draws on.
    page: Page
    #: The page's document.
    doc: Document
    #: The end point of the last ``draw_*`` call, or ``None`` after
    #: :meth:`finish` and :meth:`commit`.
    last_point: Point | None
    #: The rectangle that encloses every point that ``draw_*`` added since the
    #: last :meth:`commit`, or ``None``; as in pymupdf, ``draw_circle`` and
    #: ``draw_sector`` leave it unchanged.
    rect: Rect | None

    def __init__(self, page: Page) -> None:
        """Start an empty shape on ``page``."""
        from pylopdf._page import Page  # noqa: PLC0415 - pylopdf._page imports this module.

        if not isinstance(page, Page):
            msg = f"page must be a pylopdf.Page, not {type(page).__name__}"
            raise TypeError(msg)
        page._page_number()
        self.page = page
        self.doc = page.parent
        self.last_point = None
        self.rect = None
        self._record = _Shape()
        self._operators: list[str] = []
        self._coordinates: list[float] = []
        self._points = 0

    def __enter__(self) -> Self:
        """Return the shape; leaving the block commits it."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit after a normal exit; discard the drawing after an exception."""
        if exc_type is None:
            self.commit()
        else:
            self._discard()

    def __del__(self) -> None:
        """Warn when drawing is discarded without a commit."""
        try:
            pending = bool(self._operators) or not self._record.is_empty()
        except AttributeError:
            return
        if pending:
            with suppress(Exception):
                _warnings.warn(
                    "a Shape was discarded with drawing that commit() never wrote",
                    ResourceWarning,
                    stacklevel=2,
                    source=self,
                )

    def _discard(self) -> None:
        """Forget everything recorded since the last commit."""
        self._record.clear()
        self._operators.clear()
        self._coordinates.clear()
        self._points = 0
        self.last_point = None
        self.rect = None

    def _reserve_points(self, count: int) -> None:
        """Charge ``count`` path points against the shape's limit."""
        if self._points + count > _MAX_SHAPE_PATH_POINTS:
            raise _path_point_limit()
        self._points += count

    def _path(self, operator: str, *points: Point) -> None:
        """Append one path operator and its points."""
        self._reserve_points(len(points))
        self._operators.append(operator)
        for point in points:
            self._coordinates.extend(point)

    def _move_unless_at(self, point: Point) -> None:
        """Start a subpath at ``point`` unless the path already ends there."""
        if self.last_point != point:
            self._path("m", point)

    @staticmethod
    def horizontal_angle(C: Sequence[float], P: Sequence[float]) -> float:  # noqa: N803 - pymupdf's names.
        """Return the angle in radians between the horizontal and the line from ``C`` to ``P``."""
        c = _shape_point(C, "C")
        p = _shape_point(P, "P")
        sx, sy = p.x - c.x, p.y - c.y
        squared = sx * sx + sy * sy
        if squared < _SHAPE_EPSILON:
            sx = sy = 0.0
        else:
            length = math.sqrt(squared)
            sx, sy = sx / length, sy / length
        alfa = math.asin(abs(sy))
        if sx < 0:
            alfa = -(math.pi - alfa) if sy <= 0 else math.pi - alfa
        elif sy < 0:
            alfa = -alfa
        return alfa

    def updateRect(self, x: Sequence[float]) -> None:  # noqa: N802 - pymupdf's name.
        """Enlarge :attr:`rect` to enclose a point or a rectangle."""
        if len(x) == 2:  # noqa: PLR2004 - a point has two coordinates.
            point = _shape_point(x, "x")
            box = Rect(point.x, point.y, point.x, point.y)
        else:
            box = _shape_rect(x, "x")
        if self.rect is None:
            self.rect = box
        else:
            self.rect = Rect(
                min(self.rect.x0, box.x0),
                min(self.rect.y0, box.y0),
                max(self.rect.x1, box.x1),
                max(self.rect.y1, box.y1),
            )

    def draw_line(self, p1: Sequence[float], p2: Sequence[float]) -> Point:
        """Draw a line from ``p1`` to ``p2`` and return ``p2``."""
        start = _shape_point(p1, "p1")
        end = _shape_point(p2, "p2")
        if self.last_point != start:
            self._path("m", start)
            self.last_point = start
            self.updateRect(start)
        self._path("l", end)
        self.updateRect(end)
        self.last_point = end
        return end

    def draw_polyline(self, points: Iterable[Sequence[float]]) -> Point:
        """Draw connected straight segments through ``points`` and return the last point."""
        try:
            iterator = iter(points)
        except TypeError as exc:
            msg = f"points must be an iterable of points: {points!r}"
            raise PdfError(msg) from exc
        remaining = _MAX_SHAPE_PATH_POINTS - self._points
        polyline: list[Point] = []
        for item in iterator:
            if len(polyline) >= remaining:
                raise _path_point_limit()
            polyline.append(_shape_point(item, "points"))
        if len(polyline) < 2:  # noqa: PLR2004 - a segment needs two points.
            msg = "points must contain at least two points"
            raise PdfError(msg)
        first, *rest = polyline
        if self.last_point != first:
            self._path("m", first)
        self.updateRect(first)
        for point in rest:
            self._path("l", point)
            self.updateRect(point)
        self.last_point = polyline[-1]
        return self.last_point

    def draw_bezier(
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        p3: Sequence[float],
        p4: Sequence[float],
    ) -> Point:
        """Draw a cubic Bézier curve from ``p1`` to ``p4`` and return ``p4``."""
        points = [_shape_point(p, name) for p, name in ((p1, "p1"), (p2, "p2"), (p3, "p3"), (p4, "p4"))]
        start, control1, control2, end = points
        self._move_unless_at(start)
        self._path("c", control1, control2, end)
        for point in points:
            self.updateRect(point)
        self.last_point = end
        return end

    def draw_curve(self, p1: Sequence[float], p2: Sequence[float], p3: Sequence[float]) -> Point:
        """Draw a curve from ``p1`` to ``p3`` that ``p2`` pulls, and return ``p3``."""
        start = _shape_point(p1, "p1")
        corner = _shape_point(p2, "p2")
        end = _shape_point(p3, "p3")
        control1 = Point(
            start.x + (corner.x - start.x) * _SHAPE_KAPPA,
            start.y + (corner.y - start.y) * _SHAPE_KAPPA,
        )
        control2 = Point(
            end.x + (corner.x - end.x) * _SHAPE_KAPPA,
            end.y + (corner.y - end.y) * _SHAPE_KAPPA,
        )
        return self.draw_bezier(start, control1, control2, end)

    def draw_oval(self, tetra: Sequence[Any]) -> Point:
        """Draw the ellipse inside a rectangle or quad and return its left midpoint."""
        ul, ur, ll, lr = _shape_quad(tetra, "tetra")
        top = Point(ul.x + (ur.x - ul.x) * 0.5, ul.y + (ur.y - ul.y) * 0.5)
        right = Point(ur.x + (lr.x - ur.x) * 0.5, ur.y + (lr.y - ur.y) * 0.5)
        bottom = Point(ll.x + (lr.x - ll.x) * 0.5, ll.y + (lr.y - ll.y) * 0.5)
        left = Point(ul.x + (ll.x - ul.x) * 0.5, ul.y + (ll.y - ul.y) * 0.5)
        if self.last_point != left:
            self._path("m", left)
            self.last_point = left
        self.draw_curve(left, ll, bottom)
        self.draw_curve(bottom, lr, right)
        self.draw_curve(right, ur, top)
        self.draw_curve(top, ul, left)
        corners = (ul, ur, ll, lr)
        self.updateRect(
            (
                min(p.x for p in corners),
                min(p.y for p in corners),
                max(p.x for p in corners),
                max(p.y for p in corners),
            )
        )
        self.last_point = left
        return left

    def draw_circle(self, center: Sequence[float], radius: float) -> Point:
        """Draw a circle and return its leftmost point."""
        middle = _shape_point(center, "center")
        size = _shape_number(radius, "radius")
        if not size > _SHAPE_EPSILON:
            msg = f"radius must be positive: {radius!r}"
            raise PdfError(msg)
        return self.draw_sector(middle, Point(middle.x - size, middle.y), 360, fullSector=False)

    def draw_sector(
        self,
        center: Sequence[float],
        point: Sequence[float],
        beta: float,
        fullSector: bool = True,  # noqa: FBT001, FBT002, N803 - pymupdf's positional parameter.
    ) -> Point:
        """Draw an arc of ``beta`` degrees around ``center`` from ``point``.

        A positive angle runs counter-clockwise on the page. With
        ``fullSector`` the arc's ends are joined to the centre. Return the
        arc's end point.
        """
        c = _shape_point(center, "center")
        start = _shape_point(point, "point")
        betar = math.radians(-_shape_number(beta, "beta"))
        rad = _distance(start, c)
        if not rad > _SHAPE_EPSILON:
            msg = "radius must be positive: point is the centre"
            raise PdfError(msg)
        w360 = math.radians(math.copysign(360, betar)) * (-1)
        w90 = math.radians(math.copysign(90, betar))
        w45 = w90 / 2
        while abs(betar) > 2 * math.pi:
            betar += w360
        self._move_unless_at(start)
        self.last_point = start
        end = Point(0.0, 0.0)
        p = start
        alfa = self.horizontal_angle(c, start)
        while abs(betar) > abs(w90):
            end = Point(c.x + math.cos(alfa + w90) * rad, c.y + math.sin(alfa + w90) * rad)
            r = Point(
                c.x + math.cos(alfa + w45) * rad / math.cos(w45),
                c.y + math.sin(alfa + w45) * rad / math.cos(w45),
            )
            kappah = (1 - math.cos(w45)) * 4 / 3 / _distance(r, end)
            kappa = kappah * _distance(p, end)
            self._path(
                "c",
                Point(p.x + (r.x - p.x) * kappa, p.y + (r.y - p.y) * kappa),
                Point(end.x + (r.x - end.x) * kappa, end.y + (r.y - end.y) * kappa),
                end,
            )
            betar -= w90
            alfa += w90
            p = end
        if abs(betar) > 1e-3:  # noqa: PLR2004 - pymupdf's threshold for a remaining arc.
            beta2 = betar / 2
            end = Point(c.x + math.cos(alfa + betar) * rad, c.y + math.sin(alfa + betar) * rad)
            r = Point(
                c.x + math.cos(alfa + beta2) * rad / math.cos(beta2),
                c.y + math.sin(alfa + beta2) * rad / math.cos(beta2),
            )
            kappah = (1 - math.cos(beta2)) * 4 / 3 / _distance(r, end)
            kappa = kappah * _distance(p, end) / (1 - math.cos(betar))
            self._path(
                "c",
                Point(p.x + (r.x - p.x) * kappa, p.y + (r.y - p.y) * kappa),
                Point(end.x + (r.x - end.x) * kappa, end.y + (r.y - end.y) * kappa),
                end,
            )
        if fullSector:
            self._path("m", start)
            self._path("l", c)
            self._path("l", end)
        self.last_point = end
        return end

    def draw_rect(self, rect: Sequence[float], *, radius: float | Sequence[float] | None = None) -> Point:
        """Draw a rectangle and return its top-left corner.

        ``radius`` rounds the corners: a fraction above 0 and at most 0.5 of
        the shorter side, or a pair of fractions of the width and height.
        """
        r = _shape_rect(rect, "rect")
        width = max(r.x1 - r.x0, 0.0)
        height = max(r.y1 - r.y0, 0.0)
        top_left, top_right = Point(r.x0, r.y0), Point(r.x1, r.y0)
        bottom_left, bottom_right = Point(r.x0, r.y1), Point(r.x1, r.y1)
        if radius is None:
            self._path("m", bottom_left)
            self._path("l", Point(r.x0 + width, r.y1))
            self._path("l", Point(r.x0 + width, r.y1 - height))
            self._path("l", Point(r.x0, r.y1 - height))
            self._path("h")
            self.updateRect(r)
            self.last_point = top_left
            return top_left
        if isinstance(radius, (int, float)) and not isinstance(radius, bool):
            if not 0 < radius <= 0.5:  # noqa: PLR2004 - pymupdf's largest radius.
                msg = f"bad radius value {radius}."
                raise PdfError(msg)
            d = min(width, height) * radius
            px, py = d, d
        else:
            try:
                rx, ry = (_shape_number(v, "radius") for v in cast("Sequence[object]", radius))
            except (TypeError, ValueError) as exc:
                msg = f"bad radius value {radius}."
                raise PdfError(msg) from exc
            if min(rx, ry) <= 0 or max(rx, ry) > 0.5:  # noqa: PLR2004 - pymupdf's largest radius.
                msg = f"bad radius value {radius}."
                raise PdfError(msg)
            px, py = rx * width, ry * height
        point = self.draw_line(Point(top_left.x, top_left.y + py), Point(bottom_left.x, bottom_left.y - py))
        point = self.draw_curve(point, bottom_left, Point(bottom_left.x + px, bottom_left.y))
        point = self.draw_line(point, Point(bottom_right.x - px, bottom_right.y))
        point = self.draw_curve(point, bottom_right, Point(bottom_right.x, bottom_right.y - py))
        point = self.draw_line(point, Point(top_right.x, top_right.y + py))
        point = self.draw_curve(point, top_right, Point(top_right.x - px, top_right.y))
        point = self.draw_line(point, Point(top_left.x + px, top_left.y))
        self.last_point = self.draw_curve(point, top_left, Point(top_left.x, top_left.y + py))
        self.updateRect(r)
        return self.last_point

    def draw_quad(self, quad: Sequence[Any]) -> Point:
        """Draw the outline of a quad, or of a rectangle, and return its upper-left corner."""
        ul, ur, ll, lr = _shape_quad(quad, "quad")
        return self.draw_polyline([ul, ll, lr, ur, ul])

    @staticmethod
    def _wave_points(p1: Point, p2: Point, breadth: float, height: float, *, on_line: bool) -> list[Point]:
        """Return the turning points of a wave from ``p1`` to ``p2``.

        Points alternate ``height`` breadths on either side of the line, with
        the points on the line between them when ``on_line`` is set.
        """
        if not breadth > 0:
            msg = f"breadth must be positive: {breadth!r}"
            raise PdfError(msg)
        rad = _distance(p2, p1)
        count = 4 * round(rad / (4 * breadth))
        if count < 4:  # noqa: PLR2004 - one full phase has four steps.
            msg = "points too close"
            raise PdfError(msg)
        step = rad / count
        ux, uy = (p2.x - p1.x) / rad, (p2.y - p1.y) / rad
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
            points.append(Point(p1.x + u * ux - v * uy, p1.y + u * uy + v * ux))
        return points

    def draw_zigzag(
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        breadth: float = 2,
    ) -> Point:
        """Draw a zigzag line from ``p1`` to ``p2`` and return ``p2``."""
        start = _shape_point(p1, "p1")
        end = _shape_point(p2, "p2")
        turns = self._wave_points(start, end, _shape_number(breadth, "breadth"), 1.0, on_line=False)
        self.draw_polyline([start, *turns, end])
        return end

    def draw_squiggle(
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        breadth: float = 2,
    ) -> Point:
        """Draw a wavy line from ``p1`` to ``p2`` and return ``p2``."""
        start = _shape_point(p1, "p1")
        end = _shape_point(p2, "p2")
        turns = self._wave_points(start, end, _shape_number(breadth, "breadth"), _SQUIGGLE_HEIGHT, on_line=True)
        points = [start, *turns, end]
        for i in range(0, len(points) - 2, 2):
            self.draw_curve(points[i], points[i + 1], points[i + 2])
        return end

    def finish(  # noqa: PLR0913 - pymupdf's styling options.
        self,
        *,
        width: float = 1,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        dashes: str | None = None,
        even_odd: bool = False,
        closePath: bool = True,  # noqa: N803 - pymupdf's name.
        fill_opacity: float = 1,
        stroke_opacity: float = 1,
    ) -> None:
        """Stroke and fill everything drawn since the previous ``finish``.

        ``color`` strokes with ``width``, and ``fill`` fills with the
        non-zero rule, or the even-odd rule with ``even_odd``. ``closePath``
        joins the last point to the start of its subpath. ``dashes`` is a PDF
        dash pattern such as ``"[3 4] 0"``. Nothing drawn is a no-op, as in
        pymupdf; a style that would draw nothing, without ``fill`` and with
        ``color=None`` or ``width=0``, raises :class:`PdfError`.
        """
        if not self._operators:
            return
        line_width = _shape_number(width, "width")
        if line_width < 0:
            msg = f"width must not be negative: {width!r}"
            raise PdfError(msg)
        stroke_color = _shape_color(color, "color")
        fill_color = _shape_color(fill, "fill")
        stroke = None
        if stroke_color is not None and line_width > 0:
            stroke = (
                list(stroke_color),
                line_width,
                _shape_line_style(lineCap, "lineCap"),
                _shape_line_style(lineJoin, "lineJoin"),
                _shape_dashes(dashes),
                _shape_unit(stroke_opacity, "stroke_opacity"),
            )
        filled = None
        if fill_color is not None:
            filled = (list(fill_color), bool(even_odd), _shape_unit(fill_opacity, "fill_opacity"))
        if stroke is None and filled is None:
            msg = "finish() draws nothing: pass color with a positive width, or fill"
            raise PdfError(msg)
        self._record.add_path("".join(self._operators), self._coordinates, bool(closePath), filled, stroke)
        self._operators.clear()
        self._coordinates.clear()
        self.last_point = None

    def _font_for_text(  # noqa: PLR0913 - the text font sources.
        self,
        text: str,
        *,
        font: Font | None,
        fontname: str,
        fontfile: str | os.PathLike[str] | None,
        fontbuffer: bytes | None,
        fontindex: int,
        font_language: FontLanguage | None,
        max_font_size: int | None,
    ) -> _Font:
        """Resolve the font that :meth:`insert_text` and :meth:`insert_textbox` draw with."""
        if font is not None:
            core_font = _validate_font_choice(font, fontfile, fontbuffer, fontindex, font_language)
        else:
            font_data = _resolve_font_source(fontfile, fontbuffer, max_font_size)
            base_font, font_data, package_font = _resolve_generation_font(
                text,
                fontname,
                font_data,
                fontindex,
                font_language,
            )
            if isinstance(font_data, Path):
                core_font = _Font.from_file(os.fspath(font_data), fontindex, max_font_size, package_font)
            elif isinstance(font_data, bytes):
                core_font = _Font.from_bytes(font_data, fontindex, max_font_size)
            else:
                core_font = _Font.standard(cast("str", base_font))
            if package_font is not None:
                # As Page.insert_text does, an installed extra's face is drawn
                # at the regular weight.
                core_font = _regular_instance(core_font)
        if core_font.standard_name is not None:
            _require_winansi(text)
        return core_font

    def insert_text(  # noqa: PLR0913 - mirrors Page.insert_text.
        self,
        point: Sequence[float],
        text: str | Sequence[str],
        /,
        *,
        fontsize: float = 11,
        lineheight: float | None = None,
        font: Font | None = None,
        fontname: str = "helv",
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        font_language: FontLanguage | None = None,
        color: Sequence[float] | float | None = None,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> int:
        r"""Add text whose first baseline starts at ``point`` and return its line count.

        ``text`` is a string, split into lines with :meth:`str.splitlines`, or
        a list of lines used as given. Lines are split, placed, and left out
        below the page as :meth:`Page.insert_text` does, ``lineheight``
        included, and the returned count counts the lines added; the font
        options and limits are its own. ``color`` defaults to black. Text with
        nothing to draw adds nothing and returns 0.
        """
        _validate_optional_positive_int("max_font_size", max_font_size)
        lines = _insert_text_lines(text, max_text_size)
        if not lines or lines == [""]:
            return 0
        origin = _shape_point(point, "point")
        size = _shape_number(fontsize, "fontsize")
        if not size > 0:
            msg = f"fontsize must be a positive number: {fontsize!r}"
            raise PdfError(msg)
        resolved_lineheight = _validate_lineheight(lineheight)
        text_color = _shape_color(color, "color") or (0.0,)
        core_font = self._font_for_text(
            "\n".join(lines),
            font=font,
            fontname=fontname,
            fontfile=fontfile,
            fontbuffer=fontbuffer,
            fontindex=fontindex,
            font_language=font_language,
            max_font_size=max_font_size,
        )
        _, page_height = self.doc._doc.page_display_size(self.page._page_number())
        return self._record.insert_text(
            tuple(origin),
            lines,
            core_font,
            size,
            resolved_lineheight,
            list(text_color),
            page_height,
            max_text_size,
        )

    def insert_textbox(  # noqa: PLR0913 - mirrors Page.insert_textbox.
        self,
        rect: Sequence[float],
        text: str | Sequence[str],
        /,
        *,
        fontsize: float = 11,
        font: Font | None = None,
        fontname: str = "helv",
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        font_language: FontLanguage | None = None,
        color: Sequence[float] | float | None = None,
        align: int = TEXT_ALIGN_LEFT,
        expandtabs: int = 1,
        lineheight: float | None = None,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> float:
        """Wrap text inside a display rectangle and return the unused height.

        Layout follows :meth:`Page.insert_textbox`, with pymupdf's
        ``expandtabs=1``. A negative result means that the text does not
        fit, and then nothing is added. ``color`` defaults to black.
        """
        _validate_optional_positive_int("max_font_size", max_font_size)
        x0, y0, x1, y1 = _validate_rect(rect)
        resolved_fontsize, resolved_lineheight = _validate_textbox_options(fontsize, lineheight, align, expandtabs)
        joined = "\n".join(text) if isinstance(text, (list, tuple)) else text
        if not isinstance(joined, str):
            msg = f"text must be a string or a list of strings: {text!r}"
            raise TypeError(msg)
        text_color = _shape_color(color, "color") or (0.0,)
        _validate_utf8_text_input(joined, max_text_size)
        normalized = joined.replace("\r\n", "\n").replace("\r", "\n")
        _validate_generated_text_lines(normalized, max_text_size)
        _validate_utf8_text_input(normalized, max_text_size, expandtabs=expandtabs)
        normalized = normalized.expandtabs(expandtabs)
        if not normalized:
            return y1 - y0
        core_font = self._font_for_text(
            normalized,
            font=font,
            fontname=fontname,
            fontfile=fontfile,
            fontbuffer=fontbuffer,
            fontindex=fontindex,
            font_language=font_language,
            max_font_size=max_font_size,
        )
        return self._record.insert_textbox(
            (x0, y0, x1, y1),
            normalized,
            core_font,
            resolved_fontsize,
            resolved_lineheight,
            align,
            list(text_color),
            max_text_size,
        )

    def commit(self, overlay: bool = True) -> None:  # noqa: FBT001, FBT002 - pymupdf passes overlay positionally.
        """Draw the shape on the page, above existing content or, with ``overlay=False``, below it.

        The shape is then empty and can draw more. Drawing that
        :meth:`finish` has not styled raises :class:`PdfError`, and the page
        stays unchanged.
        """
        if self._operators:
            msg = "the shape has drawing that finish() has not styled; call finish() before commit()"
            raise PdfError(msg)
        if not self._record.is_empty():
            self.doc._doc.commit_shape(self.page._page_number(), self._record, bool(overlay))
        self._discard()


# The package re-exports this class; reprs, pickles, and documentation name it.
Shape.__module__ = "pylopdf"
