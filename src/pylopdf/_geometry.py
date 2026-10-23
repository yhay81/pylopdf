"""Immutable affine geometry in top-left display coordinates."""

from __future__ import annotations

import math
import sys
from collections.abc import Callable, Sequence
from fractions import Fraction
from typing import TYPE_CHECKING, Any, NamedTuple, Self, TypeGuard, overload

from pylopdf._hints import hinted_getattr
from pylopdf.pylopdf_core import PdfError

if TYPE_CHECKING:
    from typing import ClassVar

_RECTILINEAR_TOLERANCE = 1e-5
_SHEAR_ARGUMENTS = 3
_POINT_COORDINATES = 2
_RECT_COORDINATES = 4
_QUAD_POINTS = 4
# pymupdf's EPSILON, which decides Quad emptiness and right angles.
_EPSILON = 1e-5
# MuPDF treats a transform as axis-preserving when its other two
# coefficients are below single precision's epsilon.
_FLT_EPSILON = 2.0**-23
# The coordinate bounds of MuPDF's infinite rectangle: the smallest 32-bit
# integer and the largest single-precision value below 2**31.
_INFINITE_MIN = -2147483648.0
_INFINITE_MAX = 2147483520.0
# Rect.round() tolerates this much float noise at each edge and clamps to
# the range in which single precision still represents every integer.
_ROUND_TOLERANCE = 0.001
_ROUND_LIMIT = 16777216
_UNIT_SCALES = {"px": 1.0, "in": 1 / 72, "cm": 2.54 / 72, "mm": 25.4 / 72}


def _number(value: object) -> float:
    """Read a finite coordinate without accepting text as a number."""
    if not isinstance(value, (int, float)):
        msg = "geometry coordinates must be real numbers"
        raise TypeError(msg)
    try:
        result = float(value)
    except OverflowError as error:
        msg = "geometry coordinates must be finite"
        raise PdfError(msg) from error
    if not math.isfinite(result):
        msg = "geometry coordinates must be finite"
        raise PdfError(msg)
    return result


def _components(value: object, size: int) -> tuple[float, ...]:
    """Read exactly ``size`` coordinates, without consuming arbitrary iterables."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        msg = f"expected a sequence of {size} geometry coordinates"
        raise TypeError(msg)
    if len(value) != size:
        msg = f"expected {size} geometry coordinates"
        raise PdfError(msg)
    return tuple(_number(value[index]) for index in range(size))


def _named_components(args: tuple[object, ...], kwargs: dict[str, float], names: tuple[str, ...]) -> tuple[float, ...]:
    """Bind coordinate keywords while rejecting missing or duplicate arguments."""
    if len(args) > len(names) or any(name not in names for name in kwargs):
        msg = f"expected coordinates {', '.join(names)}"
        raise TypeError(msg)
    if any(name in kwargs for name in names[: len(args)]):
        msg = "geometry coordinate specified more than once"
        raise TypeError(msg)
    if len(args) + len(kwargs) != len(names):
        msg = f"expected all coordinates: {', '.join(names)}"
        raise TypeError(msg)
    return tuple(_number(value) for value in args) + tuple(_number(kwargs[name]) for name in names[len(args) :])


def _unit_scale(unit: str, measure: str) -> float:
    """Return the length scale of a pymupdf measurement unit."""
    if unit not in _UNIT_SCALES:
        msg = f"{measure} unit must be 'px', 'in', 'cm', or 'mm'"
        raise PdfError(msg)
    return _UNIT_SCALES[unit]


def _arithmetic_components(value: object, size: int) -> tuple[float, ...]:
    """Expand a scalar or read the coordinates of an addition/subtraction operand."""
    if isinstance(value, (int, float)):
        return (_number(value),) * size
    return _components(value, size)


class _MatrixTuple(NamedTuple):
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float


class Matrix(_MatrixTuple):
    """An immutable affine matrix ``(a, b, c, d, e, f)``.

    Construct the zero matrix with no arguments, scaling with ``(sx, sy)``,
    rotation with an angle in degrees, shear with ``(sx, sy, 1)``, or an
    explicit matrix with six coordinates or a six-item sequence.
    ``Matrix(sx, sy, 0)`` also constructs scaling. Coordinate keywords
    ``a`` through ``f`` may be used when all six coordinates are supplied.

    ``left * right`` applies ``left`` first. ``~matrix`` returns the inverse,
    or the zero matrix for a singular matrix. Division by a singular matrix
    raises ``ZeroDivisionError``. Coordinates must be finite.
    """

    __slots__ = ()

    if TYPE_CHECKING:
        #: The identity matrix ``(1, 0, 0, 1, 0, 0)``.
        IDENTITY: ClassVar[Matrix]

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    @overload
    def __new__(cls) -> Self: ...

    @overload
    def __new__(cls, angle_or_matrix: float | Sequence[float], /) -> Self: ...

    @overload
    def __new__(cls, sx: float, sy: float, mode: float = ..., /) -> Self: ...

    @overload
    def __new__(cls, a: float, b: float, c: float, d: float, e: float, f: float) -> Self: ...

    def __new__(cls, *args: float | Sequence[float], **kwargs: float) -> Self:
        """Construct a matrix using pymupdf's coordinate and transform forms."""
        if kwargs:
            values = _named_components(args, kwargs, cls._fields)
        elif not args:
            values = (0.0,) * 6
        elif len(args) == 1:
            value = args[0]
            if isinstance(value, (int, float)):
                # Reducing first keeps even very large finite angles bounded.
                angle = _number(value) % 360
                sine = round(math.sin(math.radians(angle)), 8)
                cosine = round(math.cos(math.radians(angle)), 8)
                values = (cosine, sine, -sine, cosine, 0.0, 0.0)
            else:
                values = _components(value, 6)
        elif len(args) in (2, 3):
            sx, sy = _number(args[0]), _number(args[1])
            mode = _number(args[2]) if len(args) == _SHEAR_ARGUMENTS else 0.0
            if mode not in (0, 1):
                msg = "Matrix's third argument must be 0 (scale) or 1 (shear)"
                raise PdfError(msg)
            values = (sx, 0.0, 0.0, sy, 0.0, 0.0) if mode == 0 else (1.0, sy, sx, 1.0, 0.0, 0.0)
        else:
            values = _components(args, 6)
        return _MatrixTuple.__new__(cls, *values)

    def __add__(self, other: object) -> Matrix:
        """Add a scalar or six coordinates component by component."""
        return Matrix(tuple(a + b for a, b in zip(self, _arithmetic_components(other, 6), strict=True)))

    def __radd__(self, other: object) -> Matrix:
        """Reject tuple concatenation when geometry is on the right."""
        msg = "place the matrix on the left of addition"
        raise TypeError(msg)

    def __sub__(self, other: object) -> Matrix:
        """Subtract a scalar or six coordinates component by component."""
        return Matrix(tuple(a - b for a, b in zip(self, _arithmetic_components(other, 6), strict=True)))

    def __mul__(self, other: object) -> Matrix:
        """Scale all six coordinates or concatenate another affine matrix."""
        if isinstance(other, (int, float)):
            scale = _number(other)
            return Matrix(tuple(value * scale for value in self))
        a, b, c, d, e, f = _components(other, 6)
        return Matrix(
            self.a * a + self.b * c,
            self.a * b + self.b * d,
            self.c * a + self.d * c,
            self.c * b + self.d * d,
            self.e * a + self.f * c + e,
            self.e * b + self.f * d + f,
        )

    def __rmul__(self, other: object) -> Matrix:
        """Reject tuple repetition; place the matrix on the left of a scalar."""
        msg = "use matrix * scalar, not scalar * matrix"
        raise TypeError(msg)

    def __truediv__(self, other: object) -> Matrix:
        """Divide coordinates by a scalar or concatenate an inverse matrix."""
        if isinstance(other, (int, float)):
            divisor = _number(other)
            return Matrix(tuple(value / divisor for value in self))
        inverse = ~Matrix(_components(other, 6))
        if not inverse:
            msg = "matrix not invertible"
            raise ZeroDivisionError(msg)
        return self * inverse

    def __invert__(self) -> Matrix:
        """Return the inverse, or the zero matrix when the determinant is zero."""
        determinant = self.a * self.d - self.b * self.c
        if abs(determinant) >= sys.float_info.min and math.isfinite(determinant):
            ia, ib, ic, id_ = (value / determinant for value in (self.d, -self.b, -self.c, self.a))
        else:
            # Four finite floats have bounded binary ratios. Only this rare
            # path needs exact products: subnormal determinants lose relative
            # precision, and rescaling can turn an exactly singular matrix
            # into a tiny nonzero determinant by rounding.
            a, b, c, d = (Fraction(value) for value in self[:4])
            determinant = a * d - b * c
            if determinant == 0:
                return Matrix()
            try:
                ia, ib, ic, id_ = (float(value / determinant) for value in (d, -b, -c, a))
            except OverflowError as error:
                msg = "inverse matrix coordinates must be finite"
                raise PdfError(msg) from error
        return Matrix(
            ia,
            ib,
            ic,
            id_,
            -(self.e * ia + self.f * ic),
            -(self.e * ib + self.f * id_),
        )

    def __neg__(self) -> Matrix:
        """Negate all six coordinates."""
        return self * -1

    def __pos__(self) -> Matrix:
        """Return this immutable value."""
        return self

    def __abs__(self) -> float:
        """Return the Euclidean norm of all six coordinates."""
        return math.hypot(*self)

    def __bool__(self) -> bool:
        """Return whether any coordinate is nonzero."""
        return any(self)

    def norm(self) -> float:
        """Return the Euclidean norm of all six coordinates."""
        return abs(self)

    @property
    def is_rectilinear(self) -> bool:
        """Return whether axis-aligned rectangles remain axis-aligned."""
        return (abs(self.b) < _RECTILINEAR_TOLERANCE and abs(self.c) < _RECTILINEAR_TOLERANCE) or (
            abs(self.a) < _RECTILINEAR_TOLERANCE and abs(self.d) < _RECTILINEAR_TOLERANCE
        )

    @staticmethod
    def planish(p1: Sequence[float], p2: Sequence[float]) -> Matrix:
        """Return the matrix that moves ``p1`` to the origin and ``p2`` onto the positive x axis.

        The matrix only translates and rotates, so lengths are kept. Equal
        points give the zero matrix.
        """
        start, end = _point_argument(p1), _point_argument(p2)
        length = abs(end - start)
        if length == 0:
            return Matrix()
        cosine, sine = (end.x - start.x) / length, (end.y - start.y) / length
        return Matrix(1, 0, 0, 1, -start.x, -start.y) * Matrix(cosine, -sine, sine, cosine, 0, 0)


class _PointTuple(NamedTuple):
    x: float
    y: float


class Point(_PointTuple):
    """An immutable point ``(x, y)`` in display coordinates.

    Accept two coordinates, a two-item sequence, or no arguments for the
    origin. ``+`` and ``-`` add or subtract coordinates; ``*`` and ``/``
    scale by a number or apply a matrix and its inverse. Coordinates must
    be finite. Tuple indexing, unpacking, equality, and hashing remain.
    """

    __slots__ = ()

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    @overload
    def __new__(cls) -> Self: ...

    @overload
    def __new__(cls, point: Sequence[float], /) -> Self: ...

    @overload
    def __new__(cls, x: float, y: float) -> Self: ...

    def __new__(cls, *args: float | Sequence[float], **kwargs: float) -> Self:
        """Construct the origin or a point from two finite coordinates."""
        if kwargs:
            values = _named_components(args, kwargs, cls._fields)
        elif not args:
            values = (0.0, 0.0)
        elif len(args) == 1:
            values = _components(args[0], 2)
        elif len(args) == _POINT_COORDINATES:
            # Drawing and extraction already pass two numbers; avoid a
            # temporary component tuple on this common return-value path.
            return _PointTuple.__new__(cls, _number(args[0]), _number(args[1]))
        else:
            values = _components(args, 2)
        return _PointTuple.__new__(cls, *values)

    def __add__(self, other: object) -> Point:
        """Add a scalar to both coordinates, or add another point."""
        x, y = _arithmetic_components(other, 2)
        return Point(self.x + x, self.y + y)

    def __radd__(self, other: object) -> Point:
        """Reject tuple concatenation when geometry is on the right."""
        msg = "place the point on the left of addition"
        raise TypeError(msg)

    def __sub__(self, other: object) -> Point:
        """Subtract a scalar from both coordinates, or subtract another point."""
        x, y = _arithmetic_components(other, 2)
        return Point(self.x - x, self.y - y)

    def __mul__(self, other: object) -> Point:
        """Scale the point or apply a six-coordinate affine matrix."""
        if isinstance(other, (int, float)):
            scale = _number(other)
            return Point(self.x * scale, self.y * scale)
        a, b, c, d, e, f = _components(other, 6)
        return Point(self.x * a + self.y * c + e, self.x * b + self.y * d + f)

    def __rmul__(self, other: object) -> Point:
        """Reject tuple repetition; place the point on the left of a scalar."""
        msg = "use point * scalar, not scalar * point"
        raise TypeError(msg)

    def __truediv__(self, other: object) -> Point:
        """Divide coordinates by a scalar or apply an inverse matrix."""
        if isinstance(other, (int, float)):
            divisor = _number(other)
            return Point(self.x / divisor, self.y / divisor)
        inverse = ~Matrix(_components(other, 6))
        if not inverse:
            msg = "matrix not invertible"
            raise ZeroDivisionError(msg)
        return self * inverse

    def __neg__(self) -> Point:
        """Negate both coordinates."""
        return Point(-self.x, -self.y)

    def __pos__(self) -> Point:
        """Return this immutable value."""
        return self

    def __abs__(self) -> float:
        """Return the distance from the origin."""
        return math.hypot(self.x, self.y)

    def __bool__(self) -> bool:
        """Return whether this point differs from the origin."""
        return self.x != 0 or self.y != 0

    def norm(self) -> float:
        """Return the distance from the origin."""
        return abs(self)

    @property
    def unit(self) -> Point:
        """Return the direction vector of length one, or the origin for zero."""
        scale = max(abs(self.x), abs(self.y))
        if scale == 0:
            return self
        direction = self / scale
        return direction / abs(direction)

    @property
    def abs_unit(self) -> Point:
        """Return the direction vector with nonnegative coordinates."""
        direction = self.unit
        return Point(abs(direction.x), abs(direction.y))

    def distance_to(self, x: Sequence[float], unit: str = "px") -> float:
        """Measure distance to a point or normalized rectangle.

        Units are ``px`` (PDF points), ``in``, ``cm``, and ``mm``.
        A point inside or on a rectangle has distance zero.
        """
        scale = _unit_scale(unit, "distance")
        if not isinstance(x, Sequence) or isinstance(x, (str, bytes, bytearray)):
            msg = "distance requires a point or rectangle"
            raise TypeError(msg)
        if len(x) == _POINT_COORDINATES:
            distance = abs(self - x)
        else:
            x0, y0, x1, y1 = _components(x, 4)
            left, right = min(x0, x1), max(x0, x1)
            top, bottom = min(y0, y1), max(y0, y1)
            distance = math.hypot(max(left - self.x, 0, self.x - right), max(top - self.y, 0, self.y - bottom))
        return distance * scale


def _is_number(value: object) -> TypeGuard[float]:
    return isinstance(value, (int, float))


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _is_point_like(value: object) -> TypeGuard[Sequence[float]]:
    """Return whether ``value`` has the shape of a point: two numbers."""
    return _is_sequence(value) and len(value) == _POINT_COORDINATES and all(_is_number(item) for item in value)


def _is_quad_like(value: object) -> TypeGuard[Sequence[Sequence[float]]]:
    """Return whether ``value`` has the shape of a quad: four point shapes."""
    return _is_sequence(value) and len(value) == _QUAD_POINTS and all(_is_point_like(item) for item in value)


def _rect_components(value: object) -> tuple[float, float, float, float]:
    """Read the four coordinates of a rectangle operand."""
    x0, y0, x1, y1 = _components(value, _RECT_COORDINATES)
    return x0, y0, x1, y1


def _matrix(value: object) -> Matrix:
    """Read a six-coordinate matrix operand."""
    return value if isinstance(value, Matrix) else Matrix(_components(value, 6))


def _inverse(value: object) -> Matrix:
    """Return the inverse of a matrix operand, refusing a singular one."""
    inverse = ~_matrix(value)
    if not inverse:
        msg = "matrix not invertible"
        raise ZeroDivisionError(msg)
    return inverse


def _rect_arguments(  # noqa: PLR0911 - one return per constructor form.
    args: tuple[object, ...], kwargs: dict[str, float]
) -> tuple[float, float, float, float]:
    """Bind pymupdf's rectangle constructor forms to four coordinates."""
    if kwargs:
        x0, y0, x1, y1 = _named_components(args, kwargs, ("x0", "y0", "x1", "y1"))
        return x0, y0, x1, y1
    if not args:
        return 0.0, 0.0, 0.0, 0.0
    if len(args) == 1:
        return _rect_components(args[0])
    if len(args) == 2:  # noqa: PLR2004 - two corner points.
        top_left, bottom_right = (_components(arg, _POINT_COORDINATES) for arg in args)
        return top_left[0], top_left[1], bottom_right[0], bottom_right[1]
    if len(args) == 3:  # noqa: PLR2004 - a corner point and two coordinates.
        if _is_sequence(args[0]):
            x0, y0 = _components(args[0], _POINT_COORDINATES)
            return x0, y0, _number(args[1]), _number(args[2])
        x1, y1 = _components(args[2], _POINT_COORDINATES)
        return _number(args[0]), _number(args[1]), x1, y1
    return _rect_components(args)


class _Rectangle:
    """Value-returning members shared by :class:`Rect` and :class:`IRect`.

    Containment, union, intersection, and transformation follow MuPDF's
    conventions: a rectangle with ``x0 >= x1`` or ``y0 >= y1`` is empty, one
    with ``x0 > x1`` or ``y0 > y1`` is invalid, and the infinite rectangle
    spans MuPDF's 32-bit coordinate range.
    """

    __slots__ = ()

    if TYPE_CHECKING:
        x0: float
        y0: float
        x1: float
        y1: float

        def __new__(cls, *args: Any, **kwargs: Any) -> Self: ...  # noqa: ANN401 - every subclass binds them.

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def _new(self, x0: float, y0: float, x1: float, y1: float) -> Self:
        return type(self)(x0, y0, x1, y1)

    @property
    def width(self) -> float:
        """Return ``x1 - x0``, or 0 when that is negative."""
        return max(self.x1 - self.x0, 0)

    @property
    def height(self) -> float:
        """Return ``y1 - y0``, or 0 when that is negative."""
        return max(self.y1 - self.y0, 0)

    @property
    def is_empty(self) -> bool:
        """Return whether the rectangle encloses no area."""
        return self.x0 >= self.x1 or self.y0 >= self.y1

    @property
    def is_valid(self) -> bool:
        """Return whether ``x0 <= x1`` and ``y0 <= y1``."""
        return self.x0 <= self.x1 and self.y0 <= self.y1

    @property
    def is_infinite(self) -> bool:
        """Return whether this is MuPDF's infinite rectangle."""
        return (self.x0, self.y0, self.x1, self.y1) == (_INFINITE_MIN, _INFINITE_MIN, _INFINITE_MAX, _INFINITE_MAX)

    @property
    def top_left(self) -> Point:
        """Return ``(x0, y0)``."""
        return Point(self.x0, self.y0)

    @property
    def top_right(self) -> Point:
        """Return ``(x1, y0)``."""
        return Point(self.x1, self.y0)

    @property
    def bottom_left(self) -> Point:
        """Return ``(x0, y1)``."""
        return Point(self.x0, self.y1)

    @property
    def bottom_right(self) -> Point:
        """Return ``(x1, y1)``."""
        return Point(self.x1, self.y1)

    tl = top_left
    tr = top_right
    bl = bottom_left
    br = bottom_right

    @property
    def quad(self) -> Quad:
        """Return the corners as a :class:`Quad`, in the rectangle's own order."""
        return Quad(self.top_left, self.top_right, self.bottom_left, self.bottom_right)

    def __add__(self, other: object) -> Self:
        """Add a scalar to every coordinate, or add another rectangle."""
        x0, y0, x1, y1 = _arithmetic_components(other, _RECT_COORDINATES)
        return self._new(self.x0 + x0, self.y0 + y0, self.x1 + x1, self.y1 + y1)

    def __radd__(self, other: object) -> Self:
        """Reject tuple concatenation when geometry is on the right."""
        msg = "place the rectangle on the left of addition"
        raise TypeError(msg)

    def __sub__(self, other: object) -> Self:
        """Subtract a scalar from every coordinate, or subtract another rectangle."""
        x0, y0, x1, y1 = _arithmetic_components(other, _RECT_COORDINATES)
        return self._new(self.x0 - x0, self.y0 - y0, self.x1 - x1, self.y1 - y1)

    def __mul__(self, other: object) -> Self:
        """Scale every coordinate, or return the bounding box of a transformed rectangle.

        The infinite rectangle stays infinite. An axis-preserving matrix maps
        each edge directly, so an invalid rectangle stays invalid along the
        same axes; any other matrix returns the bounding box of the four
        transformed corners, with its corners exchanged when the source was
        invalid.
        """
        if _is_number(other):
            scale = _number(other)
            return self._new(self.x0 * scale, self.y0 * scale, self.x1 * scale, self.y1 * scale)
        a, b, c, d, e, f = _matrix(other)
        if self.is_infinite:
            return self
        if abs(b) < _FLT_EPSILON and abs(c) < _FLT_EPSILON:
            xs = sorted((a * self.x0 + e, a * self.x1 + e))
            ys = sorted((d * self.y0 + f, d * self.y1 + f))
            if self.x0 > self.x1:
                xs.reverse()
            if self.y0 > self.y1:
                ys.reverse()
            return self._new(xs[0], ys[0], xs[1], ys[1])
        if abs(a) < _FLT_EPSILON and abs(d) < _FLT_EPSILON:
            # The new x axis comes from the old y axis and the reverse.
            xs = sorted((c * self.y0 + e, c * self.y1 + e))
            ys = sorted((b * self.x0 + f, b * self.x1 + f))
            if self.y0 > self.y1:
                xs.reverse()
            if self.x0 > self.x1:
                ys.reverse()
            return self._new(xs[0], ys[0], xs[1], ys[1])
        corners = [corner * Matrix(a, b, c, d, e, f) for corner in self.quad]
        left, right = min(p.x for p in corners), max(p.x for p in corners)
        top, bottom = min(p.y for p in corners), max(p.y for p in corners)
        if self.is_valid:
            return self._new(left, top, right, bottom)
        return self._new(right, bottom, left, top)

    def __rmul__(self, other: object) -> Self:
        """Reject tuple repetition; place the rectangle on the left of a scalar."""
        msg = "use rect * scalar, not scalar * rect"
        raise TypeError(msg)

    def __truediv__(self, other: object) -> Self:
        """Divide coordinates by a scalar, or transform by an inverse matrix."""
        if _is_number(other):
            divisor = _number(other)
            return self._new(self.x0 / divisor, self.y0 / divisor, self.x1 / divisor, self.y1 / divisor)
        return self * _inverse(other)

    def __neg__(self) -> Self:
        """Negate every coordinate."""
        return self._new(-self.x0, -self.y0, -self.x1, -self.y1)

    def __pos__(self) -> Self:
        """Return this immutable value."""
        return self

    def __or__(self, other: object) -> Self:
        """Return the smallest rectangle that also includes a point or rectangle.

        The infinite rectangle absorbs everything. An empty rectangle
        contributes nothing to a union with another rectangle, and an
        empty operand on the left yields the right operand.
        """
        if _is_point_like(other):
            if self.is_infinite:
                return self
            x, y = _components(other, _POINT_COORDINATES)
            return self._new(min(self.x0, x), min(self.y0, y), max(self.x1, x), max(self.y1, y))
        other_rect = Rect(_rect_components(other))
        if self.is_infinite or other_rect.is_empty:
            return self
        if other_rect.is_infinite or self.is_empty:
            return self._new(*other_rect)
        return self._new(
            min(self.x0, other_rect.x0),
            min(self.y0, other_rect.y0),
            max(self.x1, other_rect.x1),
            max(self.y1, other_rect.y1),
        )

    def __and__(self, other: object) -> Self:
        """Return the intersection with another rectangle.

        An empty operand is returned as the result, the right one when
        both are empty, and the infinite rectangle yields the other operand. Disjoint rectangles produce an
        invalid, empty result.
        """
        other_rect = Rect(_rect_components(other))
        if other_rect.is_empty or self.is_infinite:
            return self._new(*other_rect)
        if self.is_empty or other_rect.is_infinite:
            return self
        return self._new(
            max(self.x0, other_rect.x0),
            max(self.y0, other_rect.y0),
            min(self.x1, other_rect.x1),
            min(self.y1, other_rect.y1),
        )

    def __contains__(self, x: object) -> bool:
        """Test a point, rectangle, quad, or coordinate value.

        A point is inside when ``x0 <= x < x1`` and ``y0 <= y < y1``. A
        valid rectangle, or the bounding box of a quad, is inside when it
        lies within the edges, which count as inside. A number tests the
        coordinates, as for a tuple; any other value is not contained.
        """
        if _is_number(x):
            return x in (self.x0, self.y0, self.x1, self.y1)
        if _is_point_like(x):
            px, py = _components(x, _POINT_COORDINATES)
            return self.x0 <= px < self.x1 and self.y0 <= py < self.y1
        if _is_quad_like(x):
            inner = Quad(x).rect
        elif _is_sequence(x) and len(x) == _RECT_COORDINATES and all(_is_number(item) for item in x):
            inner = Rect(_rect_components(x))
        else:
            return False
        return (
            inner.is_valid
            and self.x0 <= inner.x0
            and self.y0 <= inner.y0
            and inner.x1 <= self.x1
            and inner.y1 <= self.y1
        )

    def contains(self, x: object) -> bool:
        """Return ``x in self``."""
        return x in self

    def intersects(self, r: Sequence[float]) -> bool:
        """Return whether both rectangles are finite and share some area."""
        other = Rect(_rect_components(r))
        if self.is_empty or self.is_infinite or other.is_empty or other.is_infinite:
            return False
        return not (Rect(self.x0, self.y0, self.x1, self.y1) & other).is_empty

    def get_area(self, unit: str = "px") -> float:
        """Return ``width * height`` in ``px`` (PDF points), ``in``, ``cm``, or ``mm`` squared."""
        scale = _unit_scale(unit, "area")
        return float(self.width * self.height) * scale * scale

    def norm(self) -> float:
        """Return the Euclidean norm of the four coordinates."""
        return math.hypot(self.x0, self.y0, self.x1, self.y1)

    def normalized(self) -> Self:
        """Return the rectangle with ``x0 <= x1`` and ``y0 <= y1``."""
        return self._new(
            min(self.x0, self.x1),
            min(self.y0, self.y1),
            max(self.x0, self.x1),
            max(self.y0, self.y1),
        )

    def morph(self, p: Sequence[float], m: Sequence[float]) -> Quad:
        """Transform the corners by ``m`` around the fixed point ``p``."""
        if self.is_infinite:
            return Quad.INFINITE
        return self.quad.morph(p, m)

    def torect(self, r: Sequence[float]) -> Matrix:
        """Return the matrix that maps this rectangle onto ``r``."""
        target = Rect(_rect_components(r))
        if self.is_empty or self.is_infinite or target.is_empty or target.is_infinite:
            msg = "rectangles must be finite and not empty"
            raise PdfError(msg)
        return (
            Matrix(1, 0, 0, 1, -self.x0, -self.y0)
            * Matrix(target.width / self.width, target.height / self.height)
            * Matrix(1, 0, 0, 1, target.x0, target.y0)
        )


class _RectTuple(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float


class Rect(_Rectangle, _RectTuple):
    """An immutable rectangle ``(x0, y0, x1, y1)`` in display coordinates.

    Accept four coordinates, a four-item sequence such as another
    rectangle, two corner points, a top-left point and ``x1, y1``,
    ``x0, y0`` and a bottom-right point, or no arguments for the zero
    rectangle. ``+`` and ``-`` add or subtract a scalar or rectangle; ``*``
    and ``/`` scale or transform by a matrix and its inverse; ``|``
    includes a point or rectangle; ``&`` intersects; ``in`` tests
    containment. ``width`` and ``height`` are never negative. Coordinates
    must be finite. Tuple indexing, unpacking, equality, and hashing remain.
    """

    __slots__ = ()

    if TYPE_CHECKING:
        #: MuPDF's empty rectangle, the infinite one with its corners exchanged.
        EMPTY: ClassVar[Rect]
        #: MuPDF's infinite rectangle, from -2147483648 to 2147483520 on both axes.
        INFINITE: ClassVar[Rect]

    @overload
    def __new__(cls) -> Self: ...

    @overload
    def __new__(cls, rect: Sequence[float], /) -> Self: ...

    @overload
    def __new__(cls, top_left: Sequence[float], bottom_right: Sequence[float], /) -> Self: ...

    @overload
    def __new__(cls, top_left: Sequence[float], x1: float, y1: float, /) -> Self: ...

    @overload
    def __new__(cls, x0: float, y0: float, bottom_right: Sequence[float], /) -> Self: ...

    @overload
    def __new__(cls, x0: float, y0: float, x1: float, y1: float) -> Self: ...

    def __new__(cls, *args: float | Sequence[float], **kwargs: float) -> Self:
        """Construct a rectangle from pymupdf's coordinate and corner forms."""
        if len(args) == _RECT_COORDINATES and not kwargs:
            # Extraction returns many rectangles of four floats. A finite sum
            # proves every coordinate finite; anything else, including a sum
            # that overflows, takes the validating path.
            x0, y0, x1, y1 = args
            if (
                type(x0) is float
                and type(y0) is float
                and type(x1) is float
                and type(y1) is float
                and math.isfinite(x0 + y0 + x1 + y1)
            ):
                return _RectTuple.__new__(cls, x0, y0, x1, y1)
            return _RectTuple.__new__(cls, *(_number(value) for value in args))
        return _RectTuple.__new__(cls, *_rect_arguments(args, kwargs))

    def __abs__(self) -> float:
        """Return the area, or 0 for an empty or infinite rectangle."""
        if self.is_empty or self.is_infinite:
            return 0.0
        return self.width * self.height

    def __bool__(self) -> bool:
        """Return whether any coordinate is nonzero."""
        return any(self)

    def round(self) -> IRect:
        """Return the smallest integer rectangle that covers this one.

        Each edge tolerates 0.001 of floating-point noise before it moves
        outward, and coordinates are clamped to ``-2**24`` through ``2**24``.
        """
        x0, y0 = (min(max(value + _ROUND_TOLERANCE, -_ROUND_LIMIT), _ROUND_LIMIT) for value in (self.x0, self.y0))
        x1, y1 = (min(max(value - _ROUND_TOLERANCE, -_ROUND_LIMIT), _ROUND_LIMIT) for value in (self.x1, self.y1))
        return IRect(math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))

    @property
    def irect(self) -> IRect:
        """Return :meth:`round`."""
        return self.round()


def _integer(value: object, rounding: Callable[[float], int]) -> int:
    """Read an integer coordinate, rounding a finite float outward."""
    if isinstance(value, int):
        return value
    return rounding(_number(value))


class _IRectTuple(NamedTuple):
    x0: int
    y0: int
    x1: int
    y1: int


class IRect(_Rectangle, _IRectTuple):
    """An immutable integer rectangle ``(x0, y0, x1, y1)``.

    Accept the same arguments as :class:`Rect`. Fractional coordinates
    move outward: ``x0`` and ``y0`` round down and ``x1`` and ``y1`` round
    up, also for the results of arithmetic. The operators and members are
    those of :class:`Rect`, apart from ``abs()``, truth testing, and
    ``round()``, which pymupdf's IRect does not define.
    """

    __slots__ = ()

    if TYPE_CHECKING:
        #: :attr:`Rect.EMPTY` as an integer rectangle.
        EMPTY: ClassVar[IRect]
        #: :attr:`Rect.INFINITE` as an integer rectangle.
        INFINITE: ClassVar[IRect]

    @overload
    def __new__(cls) -> Self: ...

    @overload
    def __new__(cls, rect: Sequence[float], /) -> Self: ...

    @overload
    def __new__(cls, top_left: Sequence[float], bottom_right: Sequence[float], /) -> Self: ...

    @overload
    def __new__(cls, top_left: Sequence[float], x1: float, y1: float, /) -> Self: ...

    @overload
    def __new__(cls, x0: float, y0: float, bottom_right: Sequence[float], /) -> Self: ...

    @overload
    def __new__(cls, x0: float, y0: float, x1: float, y1: float) -> Self: ...

    def __new__(cls, *args: float | Sequence[float], **kwargs: float) -> Self:
        """Construct an integer rectangle, rounding fractional edges outward."""
        if len(args) == _RECT_COORDINATES and not kwargs:
            x0, y0, x1, y1 = args
        elif len(args) == 1 and isinstance(args[0], IRect) and not kwargs:
            return _IRectTuple.__new__(cls, *args[0])
        else:
            x0, y0, x1, y1 = _rect_arguments(args, kwargs)
        return _IRectTuple.__new__(
            cls,
            _integer(x0, math.floor),
            _integer(y0, math.floor),
            _integer(x1, math.ceil),
            _integer(y1, math.ceil),
        )

    @property
    def width(self) -> int:
        """Return ``x1 - x0``, or 0 when that is negative."""
        return int(max(self.x1 - self.x0, 0))

    @property
    def height(self) -> int:
        """Return ``y1 - y0``, or 0 when that is negative."""
        return int(max(self.y1 - self.y0, 0))

    @property
    def rect(self) -> Rect:
        """Return the same rectangle as a :class:`Rect`."""
        return Rect(self)


def _cross(origin: Point, first: Point, second: Point) -> float:
    """Return the z component of ``(first - origin) x (second - origin)``."""
    return (first.x - origin.x) * (second.y - origin.y) - (first.y - origin.y) * (second.x - origin.x)


def _in_triangle(point: Point, a: Point, b: Point, c: Point) -> bool:
    """Test a point against a nondegenerate triangle, edges included."""
    area = _cross(a, b, c)
    if area == 0:
        return False
    first, second, third = _cross(a, b, point), _cross(b, c, point), _cross(c, a, point)
    if area < 0:
        first, second, third = -first, -second, -third
    return first >= 0 and second >= 0 and third >= 0


def _point_argument(value: object) -> Point:
    return value if isinstance(value, Point) else Point(_components(value, _POINT_COORDINATES))


class _QuadTuple(NamedTuple):
    ul: Point
    ur: Point
    ll: Point
    lr: Point


class Quad(_QuadTuple):
    """An immutable quadrilateral of four corner points ``(ul, ur, ll, lr)``.

    Accept four points, a four-item sequence of points such as another
    quad, the coordinate keywords ``ul``, ``ur``, ``ll``, and ``lr``
    together, or no arguments for four origin points. A rectangle's quad
    is ``rect.quad``. ``+`` and ``-`` add or subtract a scalar or another
    quad's corners; ``*`` and ``/`` scale or transform every corner by a
    matrix and its inverse; ``in`` tests containment. Tuple indexing,
    unpacking, equality, and hashing remain.
    """

    __slots__ = ()

    if TYPE_CHECKING:
        #: The quad of :attr:`Rect.EMPTY`.
        EMPTY: ClassVar[Quad]
        #: The quad of :attr:`Rect.INFINITE`.
        INFINITE: ClassVar[Quad]

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    @overload
    def __new__(cls) -> Self: ...

    @overload
    def __new__(cls, quad: Sequence[Sequence[float]], /) -> Self: ...

    @overload
    def __new__(cls, ul: Sequence[float], ur: Sequence[float], ll: Sequence[float], lr: Sequence[float]) -> Self: ...

    def __new__(cls, *args: Sequence[float] | Sequence[Sequence[float]], **kwargs: Sequence[float]) -> Self:
        """Construct a quad from four corner points."""
        if kwargs:
            names = cls._fields
            if len(args) + len(kwargs) != _QUAD_POINTS or any(name not in names for name in kwargs):
                msg = f"expected all corners: {', '.join(names)}"
                raise TypeError(msg)
            if any(name in kwargs for name in names[: len(args)]):
                msg = "quad corner specified more than once"
                raise TypeError(msg)
            corners: Sequence[object] = (*args, *(kwargs[name] for name in names[len(args) :]))
        elif not args:
            return _QuadTuple.__new__(cls, Point(), Point(), Point(), Point())
        elif len(args) == 1:
            if not _is_sequence(args[0]):
                msg = "expected a sequence of four quad corners"
                raise TypeError(msg)
            corners = args[0]
        else:
            corners = args
        if len(corners) != _QUAD_POINTS:
            msg = "expected four quad corners"
            raise PdfError(msg)
        return _QuadTuple.__new__(cls, *(_point_argument(corner) for corner in corners))

    @property
    def rect(self) -> Rect:
        """Return the smallest rectangle that contains the four corners."""
        xs = [corner.x for corner in self]
        ys = [corner.y for corner in self]
        return Rect(min(xs), min(ys), max(xs), max(ys))

    @property
    def width(self) -> float:
        """Return the longer of the top and bottom edges."""
        return max(abs(self.ur - self.ul), abs(self.lr - self.ll))

    @property
    def height(self) -> float:
        """Return the longer of the left and right edges."""
        return max(abs(self.ll - self.ul), abs(self.lr - self.ur))

    @property
    def is_empty(self) -> bool:
        """Return whether ``width`` or ``height`` is below 1e-5."""
        return self.width < _EPSILON or self.height < _EPSILON

    @property
    def is_infinite(self) -> bool:
        """Return whether the bounding rectangle is infinite."""
        return self.rect.is_infinite

    @property
    def is_convex(self) -> bool:
        """Return whether the outline ``ul, ur, lr, ll`` encloses a convex area.

        Each diagonal must separate, or touch, the two corners off it, so
        degenerate quads whose corners are collinear count as convex.
        """
        across_first = _cross(self.ul, self.lr, self.ur) * _cross(self.ul, self.lr, self.ll)
        across_second = _cross(self.ur, self.ll, self.ul) * _cross(self.ur, self.ll, self.lr)
        return across_first <= 0 and across_second <= 0

    @property
    def is_rectangular(self) -> bool:
        """Return whether every corner is a right angle, turning as ``Rect.quad`` does.

        The sine of each corner's angle must be within 1e-5 of 1, so a
        mirror image of a rectangle's quad, or a quad with a zero-length
        edge, is not rectangular.
        """
        outline = (self.ul, self.ur, self.lr, self.ll)
        for index, corner in enumerate(outline):
            previous, following = outline[index - 1], outline[(index + 1) % _QUAD_POINTS]
            lengths = abs(following - corner) * abs(previous - corner)
            if lengths == 0 or abs(_cross(corner, following, previous) / lengths - 1) > _EPSILON:
                return False
        return True

    def __add__(self, other: object) -> Quad:
        """Add a scalar to every coordinate, or add another quad's corners."""
        if _is_number(other):
            return Quad(*(corner + other for corner in self))
        if not _is_quad_like(other):
            msg = "quad addition requires a number or four corner points"
            raise TypeError(msg)
        corners = Quad(other)
        return Quad(*(mine + theirs for mine, theirs in zip(self, corners, strict=True)))

    def __radd__(self, other: object) -> Quad:
        """Reject tuple concatenation when geometry is on the right."""
        msg = "place the quad on the left of addition"
        raise TypeError(msg)

    def __sub__(self, other: object) -> Quad:
        """Subtract a scalar from every coordinate, or subtract another quad's corners."""
        if _is_number(other):
            return Quad(*(corner - other for corner in self))
        if not _is_quad_like(other):
            msg = "quad subtraction requires a number or four corner points"
            raise TypeError(msg)
        return self + -Quad(other)

    def __mul__(self, other: object) -> Quad:
        """Scale every corner, or transform every corner by a matrix."""
        if _is_number(other):
            scale = _number(other)
            return Quad(*(corner * scale for corner in self))
        matrix = _matrix(other)
        return Quad(*(corner * matrix for corner in self))

    def __rmul__(self, other: object) -> Quad:
        """Reject tuple repetition; place the quad on the left of a scalar."""
        msg = "use quad * scalar, not scalar * quad"
        raise TypeError(msg)

    def __truediv__(self, other: object) -> Quad:
        """Divide every corner by a scalar, or transform by an inverse matrix."""
        if _is_number(other):
            divisor = _number(other)
            return Quad(*(corner / divisor for corner in self))
        return self * _inverse(other)

    def __neg__(self) -> Quad:
        """Negate every corner."""
        return Quad(*(-corner for corner in self))

    def __pos__(self) -> Quad:
        """Return this immutable value."""
        return self

    def __abs__(self) -> float:
        """Return ``|ur - ul| * |ll - ul|``, the area of a rectangular quad, or 0 when empty."""
        if self.is_empty:
            return 0.0
        return abs(self.ur - self.ul) * abs(self.ll - self.ul)

    def __bool__(self) -> bool:
        """Return whether the quad is not empty."""
        return not self.is_empty

    def __contains__(self, x: object) -> bool:
        """Test a point, rectangle, or quad.

        A point is inside when it lies in triangle ``ul, ur, lr`` or
        ``ul, lr, ll``, edges included; a degenerate triangle contains
        nothing. A rectangle or quad is inside when all four of its corners
        are. Numbers and other values are not contained.
        """
        if _is_point_like(x):
            point = _point_argument(x)
            return _in_triangle(point, self.ul, self.ur, self.lr) or _in_triangle(point, self.ul, self.lr, self.ll)
        if _is_quad_like(x):
            corners: Sequence[Point] = Quad(x)
        elif _is_sequence(x) and len(x) == _RECT_COORDINATES and all(_is_number(item) for item in x):
            corners = Rect(_rect_components(x)).quad
        else:
            return False
        return all(corner in self for corner in corners)

    def morph(self, p: Sequence[float], m: Sequence[float]) -> Quad:
        """Transform the corners by ``m`` around the fixed point ``p``."""
        if self.is_infinite:
            return self
        x, y = _components(p, _POINT_COORDINATES)
        return self * (Matrix(1, 0, 0, 1, -x, -y) * _matrix(m) * Matrix(1, 0, 0, 1, x, y))


Matrix.IDENTITY = Matrix(1, 0, 0, 1, 0, 0)
Rect.EMPTY = Rect(_INFINITE_MAX, _INFINITE_MAX, _INFINITE_MIN, _INFINITE_MIN)
Rect.INFINITE = Rect(_INFINITE_MIN, _INFINITE_MIN, _INFINITE_MAX, _INFINITE_MAX)
IRect.EMPTY = IRect(Rect.EMPTY)
IRect.INFINITE = IRect(Rect.INFINITE)
Quad.EMPTY = Rect.EMPTY.quad
Quad.INFINITE = Rect.INFINITE.quad

# Reprs, pickles, and type hints use the public package.
for _public in (
    Point,
    Matrix,
    Rect,
    IRect,
    Quad,
):
    _public.__module__ = "pylopdf"
