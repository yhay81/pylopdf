"""Immutable affine geometry in top-left display coordinates."""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from fractions import Fraction
from typing import NamedTuple, Self, overload

from pylopdf.pylopdf_core import PdfError

_RECTILINEAR_TOLERANCE = 1e-5
_SHEAR_ARGUMENTS = 3
_POINT_COORDINATES = 2


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
        scales = {"px": 1.0, "in": 1 / 72, "cm": 2.54 / 72, "mm": 25.4 / 72}
        if unit not in scales:
            msg = "distance unit must be 'px', 'in', 'cm', or 'mm'"
            raise PdfError(msg)
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
        return distance * scales[unit]


# Reprs, pickles, and type hints use the public package.
Point.__module__ = "pylopdf"
Matrix.__module__ = "pylopdf"
