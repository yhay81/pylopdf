"""Rectangles and quads with pymupdf's value semantics.

The expected values were observed with pymupdf 1.28.2. MuPDF transforms in
single precision, so these examples use exactly representable results.
"""

from __future__ import annotations

import copy
import math
import operator
import pickle
from collections.abc import Callable
from typing import get_type_hints

import pytest

import pylopdf
from pylopdf import (
    EMPTY_IRECT,
    EMPTY_QUAD,
    EMPTY_RECT,
    INFINITE_IRECT,
    INFINITE_QUAD,
    INFINITE_RECT,
    IRect,
    Matrix,
    Point,
    Quad,
    Rect,
)

LIMIT_LOW = -2147483648.0
LIMIT_HIGH = 2147483520.0
UNIT_QUAD = Quad(Point(1, 2), Point(3, 2), Point(1, 4), Point(3, 4))


def _flat(quad: Quad) -> list[float]:
    return [coordinate for corner in quad for coordinate in corner]


@pytest.mark.parametrize(
    "args",
    [
        (1, 2, 3, 4),
        ((1, 2, 3, 4),),
        ([1, 2, 3, 4],),
        (Point(1, 2), Point(3, 4)),
        ((1, 2), (3, 4)),
        (Point(1, 2), 3, 4),
        (1, 2, Point(3, 4)),
        (Rect(1, 2, 3, 4),),
        (IRect(1, 2, 3, 4),),
    ],
)
def test_rect_constructors(args: tuple) -> None:
    rect = Rect(*args)
    assert rect == (1, 2, 3, 4)
    assert all(isinstance(value, float) for value in rect)
    assert Rect() == (0, 0, 0, 0)
    assert Rect(x0=1, y0=2, x1=3, y1=4) == Rect(1, 2, x1=3, y1=4) == rect


def test_irect_rounds_fractional_edges_outward() -> None:
    assert IRect(1.7, 2.2, 3.5, 4.9) == (1, 2, 4, 5)
    assert IRect(-1.7, -2.2, 3.5, 4.9) == (-2, -3, 4, 5)
    assert IRect(1.0005, 2, 3.0005, 3.9995) == (1, 2, 4, 4)
    assert IRect(3, 4, 1, 2) == (3, 4, 1, 2)
    assert all(type(value) is int for value in IRect(1.5, 2, 3, 4))
    assert IRect(Point(1.5, 2.5), Point(3.5, 4.5)) == (1, 2, 4, 5)


@pytest.mark.parametrize("value", [Rect(1, 2, 3, 4), IRect(1, 2, 3, 4), UNIT_QUAD])
def test_values_keep_named_tuple_contracts(value: Rect | IRect | Quad) -> None:
    assert isinstance(value, tuple)
    assert len(value) == 4
    assert value[:] == tuple(value)
    assert hash(value) == hash(tuple(value))
    assert type(value).__module__ == "pylopdf"
    for restored in (copy.copy(value), copy.deepcopy(value), pickle.loads(pickle.dumps(value))):  # noqa: S301 - locally serialized values.
        assert restored == value
        assert type(restored) is type(value)
    with pytest.raises(AttributeError):
        setattr(value, value._fields[0], 99)
    with pytest.raises(TypeError):
        operator.setitem(value, 0, 99)  # ty: ignore[no-matching-overload]


def test_field_types() -> None:
    assert get_type_hints(Rect) == dict.fromkeys(("x0", "y0", "x1", "y1"), float)
    assert get_type_hints(IRect) == dict.fromkeys(("x0", "y0", "x1", "y1"), int)
    assert get_type_hints(Quad) == dict.fromkeys(("ul", "ur", "ll", "lr"), Point)
    assert Rect(1, 2, 3, 4) == IRect(1, 2, 3, 4)
    assert not isinstance(IRect(), Rect)


def test_size_and_validity() -> None:
    assert Rect(1, 2, 4, 6).width == 3
    assert Rect(1, 2, 4, 6).height == 4
    assert Rect(3, 4, 1, 2).width == Rect(3, 4, 1, 2).height == 0
    assert type(IRect(1, 2, 4, 6).width) is int
    assert IRect(3, 4, 1, 2).width == 0
    assert Rect(3, 4, 1, 2).is_empty
    assert not Rect(3, 4, 1, 2).is_valid
    assert Rect(1, 2, 1, 4).is_empty
    assert Rect(1, 2, 1, 4).is_valid
    assert not Rect(1, 2, 3, 4).is_empty


def test_constants() -> None:
    assert INFINITE_RECT() == (LIMIT_LOW, LIMIT_LOW, LIMIT_HIGH, LIMIT_HIGH)
    assert EMPTY_RECT() == (LIMIT_HIGH, LIMIT_HIGH, LIMIT_LOW, LIMIT_LOW)
    assert INFINITE_IRECT() == INFINITE_RECT()
    assert EMPTY_IRECT() == EMPTY_RECT()
    assert type(INFINITE_IRECT()) is IRect
    assert INFINITE_QUAD() == INFINITE_RECT().quad
    assert EMPTY_QUAD() == EMPTY_RECT().quad
    assert INFINITE_RECT().is_infinite
    assert INFINITE_IRECT().is_infinite
    assert INFINITE_QUAD().is_infinite
    assert not INFINITE_RECT().is_empty
    assert INFINITE_RECT().is_valid
    assert EMPTY_RECT().is_empty
    assert not EMPTY_RECT().is_valid
    assert not Rect(LIMIT_LOW, LIMIT_LOW, LIMIT_HIGH, LIMIT_HIGH + 1).is_infinite
    # pymupdf measures a quad by its edges, so the empty quad is not empty.
    assert not EMPTY_QUAD().is_empty


def test_rect_corners_keep_the_stored_order() -> None:
    rect = Rect(1, 2, 3, 4)
    assert (rect.tl, rect.tr, rect.bl, rect.br) == ((1, 2), (3, 2), (1, 4), (3, 4))
    assert (rect.top_left, rect.top_right, rect.bottom_left, rect.bottom_right) == (rect.tl, rect.tr, rect.bl, rect.br)
    assert isinstance(rect.tl, Point)
    assert Rect(3, 4, 1, 2).top_left == (3, 4)
    assert rect.quad == UNIT_QUAD
    assert Rect(3, 4, 1, 2).quad == ((3, 4), (1, 4), (3, 2), (1, 2))
    assert IRect(1, 2, 3, 4).quad == UNIT_QUAD
    assert IRect(1, 2, 3, 4).rect == rect
    assert type(IRect(1, 2, 3, 4).rect) is Rect
    assert Rect(3, 4, 1, 2).normalized() == rect
    assert IRect(3, 4, 1, 2).normalized() == (1, 2, 3, 4)


def test_rect_arithmetic() -> None:
    rect = Rect(1, 2, 3, 4)
    assert rect + 1 == (2, 3, 4, 5)
    assert rect + (1, 2, 3, 4) == (2, 4, 6, 8)  # noqa: RUF005 - geometric addition, not concatenation.
    assert rect + IRect(1, 1, 1, 1) == (2, 3, 4, 5)
    assert rect - 1 == (0, 1, 2, 3)
    assert rect - rect == (0, 0, 0, 0)
    assert rect * 2 == (2, 4, 6, 8)
    assert Rect(3, 4, 1, 2) * 2 == (6, 8, 2, 4)
    assert rect / 2 == (0.5, 1, 1.5, 2)
    assert -rect == (-1, -2, -3, -4)
    assert +rect is rect
    assert type(rect + 1) is Rect


def test_irect_arithmetic_rounds_the_result_outward() -> None:
    irect = IRect(1, 2, 3, 4)
    assert irect + 1 == (2, 3, 4, 5)
    assert irect + 0.5 == (1, 2, 4, 5)
    assert irect - 1 == (0, 1, 2, 3)
    assert irect * 1.5 == (1, 3, 5, 6)
    assert irect / 2 == (0, 1, 2, 2)
    assert irect * Matrix(90) == (-4, 1, -2, 3)
    assert irect * (1, 0, 0, 1, 0.5, 0.5) == (1, 2, 4, 5)
    assert -irect == (-1, -2, -3, -4)
    assert all(type(value) is IRect for value in (irect + 0.5, irect * 1.5, irect / 2, -irect))


@pytest.mark.parametrize(
    ("rect", "matrix", "expected"),
    [
        (Rect(1, 2, 3, 4), Matrix(90), (-4, 1, -2, 3)),
        (Rect(1, 2, 3, 4), (1, 0, 0, 1, 5, 5), (6, 7, 8, 9)),
        (Rect(1, 2, 3, 4), Matrix(-1, 1), (-3, 2, -1, 4)),
        (Rect(1, 2, 3, 4), Matrix(1, -1), (1, -4, 3, -2)),
        (Rect(1, 2, 3, 4), Matrix(0, -1, 1, 0, 0, 0), (2, -3, 4, -1)),
        (Rect(1, 2, 3, 4), Matrix(1, 0, 0.5, 1, 0, 0), (2, 2, 5, 4)),
        (Rect(1, 2, 3, 4), Matrix(), (0, 0, 0, 0)),
        (Rect(1, 2, 3, 4), Matrix(0, 1, 0, 0, 0, 0), (0, 1, 0, 3)),
        # An invalid rectangle keeps its exchanged edges along the same axes.
        (Rect(3, 2, 1, 4), Matrix(1, 1), (3, 2, 1, 4)),
        (Rect(3, 2, 1, 4), Matrix(90), (-4, 3, -2, 1)),
        (Rect(1, 4, 3, 2), Matrix(90), (-2, 1, -4, 3)),
        (Rect(1, 1, 1, 5), Matrix(90), (-5, 1, -1, 1)),
        # Other matrices exchange every corner of an invalid result.
        (Rect(3, 2, 1, 4), Matrix(1, 0, 0.5, 1, 0, 0), (5, 4, 2, 2)),
        (Rect(3, 4, 1, 2), Matrix(1, 0, 0.5, 1, 0, 0), (5, 4, 2, 2)),
    ],
)
def test_rect_transformation(rect: Rect, matrix: Matrix, expected: tuple[float, ...]) -> None:
    assert rect * matrix == expected


def test_general_rotation_returns_the_bounding_box() -> None:
    half = math.sqrt(0.5)
    expected = (-3 * half, 3 * half, half, 7 * half)
    assert Rect(1, 2, 3, 4) * Matrix(45) == pytest.approx(expected, abs=1e-7)
    assert Rect(3, 4, 1, 2) * Matrix(45) == pytest.approx((half, 7 * half, -3 * half, 3 * half), abs=1e-7)
    assert (Rect(1, 2, 3, 4) * Matrix(45)).quad != (Rect(1, 2, 3, 4).quad * Matrix(45))


def test_infinite_rect_stays_infinite_under_any_matrix() -> None:
    for matrix in (Matrix(2, 2), Matrix(90), Matrix(30), Matrix(1, 0, 0, 1, 5, 5)):
        assert (INFINITE_RECT() * matrix).is_infinite
    assert not (INFINITE_RECT() * 2).is_infinite
    assert not (INFINITE_RECT() + 1).is_infinite
    assert EMPTY_RECT() * Matrix(2, 2) == (2 * LIMIT_HIGH, 2 * LIMIT_HIGH, 2 * LIMIT_LOW, 2 * LIMIT_LOW)


def test_rect_division_by_matrix() -> None:
    assert Rect(1, 2, 3, 4) / Matrix(2, 2) == (0.5, 1, 1.5, 2)
    assert Rect(1, 2, 3, 4) * Matrix(90) / Matrix(90) == (1, 2, 3, 4)
    # A bounding box only grows under a general rotation and its inverse.
    assert Rect(1, 2, 3, 4) in Rect(1, 2, 3, 4) * Matrix(30) / Matrix(30)
    with pytest.raises(ZeroDivisionError, match="not invertible"):
        _ = Rect(1, 2, 3, 4) / Matrix()
    with pytest.raises(ZeroDivisionError):
        _ = Rect(1, 2, 3, 4) / 0


def test_union() -> None:
    rect = Rect(1, 2, 3, 4)
    assert rect | Point(10, 10) == (1, 2, 10, 10)
    assert rect | (10, 10) == (1, 2, 10, 10)
    assert rect | Rect(0, 0, 2, 2) == (0, 0, 3, 4)
    assert rect | (0, 0, 2, 2) == (0, 0, 3, 4)
    # A point extends every rectangle, even an empty or invalid one.
    assert Rect() | Point(5, 5) == (0, 0, 5, 5)
    assert EMPTY_RECT() | Point(1, 2) == (1, 2, 1, 2)
    assert Rect(3, 4, 1, 2) | Point(5, 5) == (3, 4, 5, 5)
    # An empty rectangle contributes nothing to a union of rectangles.
    assert Rect() | Rect(1, 1, 2, 2) == (1, 1, 2, 2)
    assert Rect(0, 0, 0, 5) | Rect(1, 1, 2, 2) == (1, 1, 2, 2)
    assert rect | Rect(3, 4, 1, 2) == rect
    assert rect | Rect() == rect
    assert EMPTY_RECT() | rect == rect
    assert (INFINITE_RECT() | rect).is_infinite
    assert (rect | INFINITE_RECT()).is_infinite
    assert (INFINITE_RECT() | Point(1e12, 1e12)).is_infinite
    assert (INFINITE_RECT() | EMPTY_RECT()).is_infinite
    assert IRect(1, 2, 3, 4) | Point(10.5, 10.5) == (1, 2, 11, 11)
    assert IRect(1, 2, 3, 4) | Rect(0.5, 0.5, 1, 1) == (0, 0, 3, 4)


def test_intersection() -> None:
    rect = Rect(1, 2, 3, 4)
    assert rect & Rect(2, 3, 5, 5) == (2, 3, 3, 4)
    assert rect & (2, 3, 5, 5) == (2, 3, 3, 4)
    # Disjoint rectangles give an invalid, empty result.
    assert rect & Rect(5, 5, 6, 6) == (5, 5, 3, 4)
    assert (rect & Rect(5, 5, 6, 6)).is_empty
    # An empty operand is the result; the infinite one yields the other.
    assert Rect() & rect == (0, 0, 0, 0)
    assert Rect(3, 4, 1, 2) & rect == (3, 4, 1, 2)
    assert rect & Rect(3, 4, 1, 2) == (3, 4, 1, 2)
    assert Rect(1, 1, 1, 5) & Rect(0, 0, 3, 3) == (1, 1, 1, 5)
    assert INFINITE_RECT() & rect == rect
    assert INFINITE_RECT() & EMPTY_RECT() == EMPTY_RECT()
    # With both operands empty, the right one is the result.
    assert Rect(14, 4, 17, -20) & Rect(4, 5, 0, 20) == (4, 5, 0, 20)
    assert EMPTY_RECT() & Rect() == Rect()
    assert IRect(1, 2, 3, 4) & Rect(1.5, 2.5, 2.5, 3.5) == (1, 2, 3, 4)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (Point(2, 3), True),
        (Point(1, 2), True),
        # Points use half-open intervals, so the far edges are outside.
        (Point(3, 4), False),
        (Point(3, 3), False),
        ((2, 3), True),
        # Rectangles include their edges, and an empty one inside counts.
        (Rect(1, 2, 3, 4), True),
        (Rect(1, 2, 2, 3), True),
        (Rect(2, 3, 2, 3), True),
        (IRect(1, 2, 2, 3), True),
        ((1, 2, 3, 4), True),
        (Rect(), False),
        (Rect(5, 5, 5, 5), False),
        (Rect(2.5, 3.5, 2, 3), False),
        (UNIT_QUAD, True),
        ((UNIT_QUAD * Matrix(1, 0, 0, 1, 1, 0)), False),
        # A number tests the coordinates, as for a tuple.
        (2, True),
        (5, False),
        ("x", False),
        ((1, 2, 3), False),
    ],
)
def test_rect_containment(item: object, *, expected: bool) -> None:
    assert (item in Rect(1, 2, 3, 4)) is expected
    assert Rect(1, 2, 3, 4).contains(item) is expected
    assert (item in IRect(1, 2, 3, 4)) is expected


def test_containment_in_degenerate_and_infinite_rects() -> None:
    assert Rect(1, 1, 1, 1) in Rect(1, 1, 1, 1)
    assert Point(1, 1) not in Rect(1, 1, 1, 1)
    assert Point(2, 3) not in Rect(3, 4, 1, 2)
    assert Rect(1, 2, 3, 4) not in Rect(3, 4, 1, 2)
    assert Rect(1, 2, 3, 4) in INFINITE_RECT()
    assert INFINITE_RECT() in INFINITE_RECT()
    assert Point(1e12, 0) not in INFINITE_RECT()


def test_intersects_requires_two_finite_areas() -> None:
    rect = Rect(1, 2, 3, 4)
    assert rect.intersects(Rect(2, 3, 5, 6))
    assert rect.intersects((2, 3, 5, 6))
    assert IRect(1, 2, 3, 4).intersects(IRect(2, 3, 5, 6))
    assert not rect.intersects(Rect(3, 4, 5, 6))
    assert not rect.intersects(Rect(3, 2, 5, 4))
    assert not rect.intersects(Rect())
    assert not rect.intersects(Rect(3, 4, 1, 2))
    assert not Rect(3, 4, 1, 2).intersects(rect)
    assert not rect.intersects(INFINITE_RECT())
    with pytest.raises(pylopdf.PdfError):
        rect.intersects(Point(2, 3))


def test_area_norm_and_truth() -> None:
    assert abs(Rect(1, 2, 3, 4)) == 4
    assert abs(Rect(3, 4, 1, 2)) == 0
    assert abs(INFINITE_RECT()) == 0
    assert Rect(1, 2, 3, 4).get_area() == 4
    assert IRect(1, 2, 3, 4).get_area() == 4
    assert type(IRect(1, 2, 3, 4).get_area()) is float
    assert Rect(1, 2, 1, 4).get_area() == 0
    assert INFINITE_RECT().get_area() == (LIMIT_HIGH - LIMIT_LOW) ** 2
    square_inch = Rect(0, 0, 72, 72)
    assert square_inch.get_area("px") == 5184
    assert square_inch.get_area("in") == pytest.approx(1)
    assert square_inch.get_area("cm") == pytest.approx(2.54**2)
    assert square_inch.get_area("mm") == pytest.approx(25.4**2)
    with pytest.raises(pylopdf.PdfError, match="unit"):
        square_inch.get_area("IN")
    assert Rect(1, 2, 3, 4).norm() == Rect(-1, -2, -3, -4).norm() == math.sqrt(30)
    assert not Rect()
    assert Rect(0, 0, 0, 1)
    assert Rect(1, 1, 1, 1)
    # pymupdf's IRect has neither abs() nor geometric truth testing.
    assert bool(IRect()) is True
    with pytest.raises(TypeError):
        abs(IRect(1, 2, 3, 4))  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("rect", "expected"),
    [
        (Rect(0.5, 1.5, 2.5, 3.2), (0, 1, 3, 4)),
        (Rect(-0.5, -1.5, 2.0001, 3.9999), (-1, -2, 2, 4)),
        (Rect(1.0005, 2.0009, 3.0005, 3.9995), (1, 2, 3, 4)),
        (Rect(1.002, 2, 3.002, 4), (1, 2, 4, 4)),
        (Rect(3.5, 4.5, 1.2, 2.2), (3, 4, 2, 3)),
        (INFINITE_RECT(), (-16777216, -16777216, 16777216, 16777216)),
    ],
)
def test_round_tolerates_float_noise_and_clamps(rect: Rect, expected: tuple[int, ...]) -> None:
    assert rect.round() == expected
    assert rect.irect == expected
    assert type(rect.round()) is IRect


def test_torect_maps_one_rectangle_onto_another() -> None:
    matrix = Rect(1, 2, 3, 4).torect(Rect(0, 0, 10, 10))
    assert matrix == (5, 0, 0, 5, -5, -10)
    assert Rect(1, 2, 3, 4) * matrix == (0, 0, 10, 10)
    assert IRect(1, 2, 3, 4).torect(IRect(0, 0, 4, 4)) == (2, 0, 0, 2, -2, -4)
    assert Rect(1, 2, 3, 4).torect((0, 0, 4, 4)) == (2, 0, 0, 2, -2, -4)
    for source, target in [
        (Rect(1, 1, 1, 1), Rect(0, 0, 10, 10)),
        (Rect(1, 2, 3, 4), Rect(3, 4, 1, 2)),
        (INFINITE_RECT(), Rect(0, 0, 1, 1)),
    ]:
        with pytest.raises(pylopdf.PdfError, match="finite and not empty"):
            source.torect(target)


def test_morph_turns_corners_around_a_fixed_point() -> None:
    assert Rect(1, 2, 3, 4).morph(Point(2, 3), Matrix(90)) == ((3, 2), (3, 4), (1, 2), (1, 4))
    assert Rect(1, 2, 3, 4).morph((2, 3), Matrix(2, 2)) == ((0, 1), (4, 1), (0, 5), (4, 5))
    assert Rect(3, 4, 1, 2).morph(Point(2, 3), Matrix(2, 2)) == ((4, 5), (0, 5), (4, 1), (0, 1))
    assert Rect(1, 2, 3, 4).morph(Point(2, 3), Matrix()) == (Point(2, 3),) * 4
    assert IRect(1, 2, 3, 4).morph(Point(2, 3), Matrix(90)) == ((3, 2), (3, 4), (1, 2), (1, 4))
    assert UNIT_QUAD.morph(Point(2, 3), (0, 1, -1, 0, 0, 0)) == ((3, 2), (3, 4), (1, 2), (1, 4))
    assert INFINITE_RECT().morph(Point(), Matrix(90)) == INFINITE_QUAD()
    assert INFINITE_QUAD().morph(Point(1, 1), Matrix(2, 2)) == INFINITE_QUAD()


@pytest.mark.parametrize(
    "args",
    [
        (Point(1, 2), Point(3, 2), Point(1, 4), Point(3, 4)),
        ((1, 2), (3, 2), (1, 4), (3, 4)),
        ([(1, 2), (3, 2), (1, 4), (3, 4)],),
        (UNIT_QUAD,),
    ],
)
def test_quad_constructors(args: tuple) -> None:
    quad = Quad(*args)
    assert quad == UNIT_QUAD
    assert all(isinstance(corner, Point) for corner in quad)
    assert (quad.ul, quad.ur, quad.ll, quad.lr) == ((1, 2), (3, 2), (1, 4), (3, 4))
    assert Quad(ul=(1, 2), ur=(3, 2), ll=(1, 4), lr=(3, 4)) == quad
    assert Quad() == (Point(),) * 4


def test_quad_measurements() -> None:
    assert UNIT_QUAD.rect == (1, 2, 3, 4)
    assert type(UNIT_QUAD.rect) is Rect
    assert UNIT_QUAD.width == UNIT_QUAD.height == 2
    sheared = UNIT_QUAD * Matrix(1, 0, 0.5, 1, 0, 0)
    assert sheared.width == 2
    assert sheared.height == math.sqrt(5)
    assert abs(sheared) == 2 * math.sqrt(5)
    # Width and height take the longer of each pair of opposite edges,
    # while abs() multiplies the two edges that meet at ul.
    trapezoid = Quad(Point(0, 0), Point(3, 0), Point(1, 2), Point(2, 2))
    assert trapezoid.width == 3
    assert trapezoid.height == math.sqrt(5)
    kite = Quad(Point(0, 0), Point(2, 0), Point(1, 1), Point(1, 3))
    assert kite.height == math.sqrt(10)
    assert abs(kite) == 2 * math.sqrt(2)
    assert abs(UNIT_QUAD) == 4
    assert abs(Quad()) == 0
    assert abs(INFINITE_QUAD()) == (LIMIT_HIGH - LIMIT_LOW) ** 2


@pytest.mark.parametrize(
    ("quad", "rectangular", "convex", "empty"),
    [
        (UNIT_QUAD, True, True, False),
        (UNIT_QUAD * Matrix(30), True, True, False),
        (UNIT_QUAD * Matrix(1, 0, 0.5, 1, 0, 0), False, True, False),
        (Quad(), False, True, True),
        # The mirror image of a rectangle's quad turns the other way.
        (Quad(Point(0, 0), Point(2, 0), Point(0, -1), Point(2, -1)), False, True, False),
        (Quad(Point(2, 0), Point(0, 0), Point(2, 1), Point(0, 1)), False, True, False),
        # Corners in outline order make a bow tie.
        (Quad(Point(0, 0), Point(2, 0), Point(2, 2), Point(0, 2)), False, False, False),
        (Quad(Point(0, 0), Point(2, 0), Point(1, 1), Point(1, 3)), False, False, False),
        (Quad(Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)), False, True, False),
        (Quad(Point(0, 0), Point(2, 0), Point(0, 0), Point(2, 0)), False, True, True),
        (Quad(Point(0, 0), Point(2, 0), Point(0, 1e-6), Point(2, 1e-6)), True, True, True),
        (Quad(Point(0, 0), Point(2, 0), Point(0, 1e-5), Point(2, 1e-5)), True, True, False),
        # Each corner's sine may differ from 1 by 1e-5.
        (Quad(Point(0, 0), Point(2, 0), Point(0.0044, 1), Point(2.0044, 1)), True, True, False),
        (Quad(Point(0, 0), Point(2, 0), Point(0.0046, 1), Point(2.0046, 1)), False, True, False),
        (Quad(Point(0, 0), Point(2, 0), Point(0, 1), Point(2.004, 1)), True, True, False),
        (Quad(Point(0, 0), Point(2, 0), Point(0, 1), Point(2.01, 1)), False, True, False),
        (INFINITE_QUAD(), True, True, False),
    ],
)
def test_quad_shape(quad: Quad, *, rectangular: bool, convex: bool, empty: bool) -> None:
    assert quad.is_rectangular is rectangular
    assert quad.is_convex is convex
    assert quad.is_empty is empty
    assert bool(quad) is not empty


def test_quad_arithmetic() -> None:
    assert UNIT_QUAD + 1 == ((2, 3), (4, 3), (2, 5), (4, 5))
    assert UNIT_QUAD + UNIT_QUAD == UNIT_QUAD * 2
    assert UNIT_QUAD + ((1, 1),) * 4 == UNIT_QUAD + 1
    assert UNIT_QUAD - 1 == ((0, 1), (2, 1), (0, 3), (2, 3))
    assert Quad() == UNIT_QUAD - UNIT_QUAD
    assert UNIT_QUAD * 2 == ((2, 4), (6, 4), (2, 8), (6, 8))
    assert UNIT_QUAD / 2 == ((0.5, 1), (1.5, 1), (0.5, 2), (1.5, 2))
    assert UNIT_QUAD * (1, 0, 0, 1, 1, 1) == UNIT_QUAD + 1
    assert UNIT_QUAD / Matrix(2, 2) == UNIT_QUAD / 2
    assert _flat(UNIT_QUAD * Matrix(30) / Matrix(30)) == pytest.approx(_flat(UNIT_QUAD))
    assert -UNIT_QUAD == ((-1, -2), (-3, -2), (-1, -4), (-3, -4))
    assert +UNIT_QUAD is UNIT_QUAD
    with pytest.raises(ZeroDivisionError, match="not invertible"):
        _ = UNIT_QUAD / Matrix()
    # Transforming the quad keeps the corners; transforming the rect boxes them.
    rotated = UNIT_QUAD * Matrix(45)
    assert rotated.is_rectangular
    assert rotated.rect == pytest.approx(Rect(1, 2, 3, 4) * Matrix(45))
    assert not (INFINITE_QUAD() * Matrix(2, 2)).is_infinite


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (Point(2, 3), True),
        # Quads include their edges and corners.
        (Point(1, 2), True),
        (Point(3, 4), True),
        (Point(3, 3), True),
        ((2, 3), True),
        (Point(1, 1), False),
        (Rect(1, 2, 2, 3), True),
        (Rect(1, 2, 3, 4), True),
        (Rect(2, 3, 3, 4), True),
        ((1, 2, 3, 4), True),
        (Rect(0, 2, 3, 4), False),
        (UNIT_QUAD, True),
        (Rect(1, 2, 2, 3).quad, True),
        (UNIT_QUAD + 0.5, False),
        (2, False),
        ("x", False),
    ],
)
def test_quad_containment(item: object, *, expected: bool) -> None:
    assert (item in UNIT_QUAD) is expected


def test_quad_containment_follows_rotation_and_shape() -> None:
    rotated = UNIT_QUAD * Matrix(45)
    assert Point(0, 3.5) in rotated
    assert Point(2, 3) not in rotated
    assert Point(1.5, 1) in Quad(Point(0, 0), Point(3, 0), Point(1, 2), Point(2, 2))
    assert Point(0.2, 1.9) not in Quad(Point(0, 0), Point(3, 0), Point(1, 2), Point(2, 2))
    # A degenerate quad contains nothing.
    assert Point(1, 1) not in Quad(Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3))
    assert Point(1, 0) not in Quad(Point(0, 0), Point(2, 0), Point(0, 0), Point(2, 0))
    assert Point(0, 0) not in UNIT_QUAD * Matrix()
    # Every corner of a rectangle must be inside, not only two of them.
    kite = Quad(Point(0, 0), Point(2, 0), Point(1, 1), Point(1, 3))
    assert Rect(0.5, 0.2, 1.5, 0.4) in kite
    assert Rect(0, 0, 1, 1) not in kite


@pytest.mark.parametrize(
    "construct",
    [
        lambda: Rect(1, 2, 3),  # ty: ignore[no-matching-overload]
        lambda: Rect(1, 2, 3, 4, 5),  # ty: ignore[no-matching-overload]
        lambda: Rect("abcd"),  # ty: ignore[invalid-argument-type]
        lambda: Rect(iter([1, 2, 3, 4])),  # ty: ignore[invalid-argument-type]
        lambda: Rect(math.nan, 0, 1, 1),
        lambda: Rect(math.inf, 0, 1, 1),
        lambda: Rect(x0=1),  # ty: ignore[no-matching-overload]
        lambda: IRect(math.nan, 0, 1, 1),
        lambda: IRect("1234"),  # ty: ignore[invalid-argument-type]
        lambda: Rect(1, 2, 3, 4) + (1, 2),  # noqa: RUF005 - geometric addition, not concatenation.
        lambda: Rect(1, 2, 3, 4) + Point(1, 2),
        lambda: Rect(1, 2, 3, 4) * Rect(1, 2, 3, 4),
        lambda: Rect(1, 2, 3, 4) | (1, 2, 3),
        lambda: Rect(1, 2, 3, 4) & Point(1, 1),
        lambda: Rect(1, 2, 3, 4) & UNIT_QUAD,
        lambda: Rect(1, 2, 3, 4).torect(Point(1, 1)),
        lambda: Quad(Rect(1, 2, 3, 4)),  # ty: ignore[invalid-argument-type]
        lambda: Quad(1, 2, 3, 4, 5, 6, 7, 8),  # ty: ignore[no-matching-overload]
        lambda: Quad(Point(), Point(), Point()),  # ty: ignore[no-matching-overload]
        lambda: Quad(ul=Point()),  # ty: ignore[no-matching-overload]
        lambda: Quad((math.inf, 0), Point(), Point(), Point()),
        lambda: UNIT_QUAD + (1, 2),  # noqa: RUF005 - geometric addition, not concatenation.
        lambda: UNIT_QUAD * Rect(0, 0, 1, 1),
        lambda: operator.mul(2, Rect(1, 2, 3, 4)),
        lambda: operator.add((1, 2, 3, 4), Rect(1, 2, 3, 4)),
        lambda: operator.mul(2, UNIT_QUAD),
        lambda: operator.add(tuple(UNIT_QUAD), UNIT_QUAD),
    ],
)
def test_malformed_inputs_fail_without_partial_values(construct: Callable[[], object]) -> None:
    with pytest.raises((TypeError, pylopdf.PdfError)):
        construct()


@pytest.mark.parametrize(
    ("value", "names"),
    [
        (Rect(), ("include_point", "include_rect", "intersect", "normalize", "transform")),
        (IRect(), ("include_point", "include_rect", "intersect", "normalize", "transform")),
        (Quad(), ("transform",)),
    ],
)
def test_no_mutating_aliases(value: object, names: tuple[str, ...]) -> None:
    for name in names:
        assert not hasattr(value, name)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rectangles_drive_drawing_and_search_on_rotated_pages(rotation: int) -> None:
    with pylopdf.Document() as document:
        page = document.pages.new((300, 200))
        page.rotation = rotation
        box = (page.rect * Matrix(0.5, 0.5)) + (20, 20, 20, 20)
        assert box in page.rect
        with page.draw() as canvas:
            canvas.rect(box, stroke=pylopdf.Color(0, 0, 1))
            canvas.text("Geometry", box.tl + (10, 30), size=14)  # noqa: RUF005 - geometric addition.
        data = document.to_bytes()
    with pylopdf.open(data) as reopened:
        page = reopened.pages[0]
        (hit,) = page.search("Geometry")
        assert isinstance(hit, Rect)
        assert hit in box
        assert hit.intersects(box)
        assert (hit | box) == box
        assert (hit & box) == hit
        drawn = page.content().paths[0].bbox
        assert drawn == pytest.approx(box, abs=1e-3)


def test_identity_and_infinite_bounds() -> None:
    assert pylopdf.FZ_MIN_INF_RECT == -2147483648
    assert pylopdf.FZ_MAX_INF_RECT == 2147483520
    assert type(pylopdf.FZ_MIN_INF_RECT) is int
    assert INFINITE_RECT() == (pylopdf.FZ_MIN_INF_RECT,) * 2 + (pylopdf.FZ_MAX_INF_RECT,) * 2
    identity = pylopdf.Identity
    assert identity == pylopdf.IdentityMatrix() == Matrix(1, 1)
    assert isinstance(identity, Matrix)
    assert repr(identity).startswith("IdentityMatrix(")
    assert type(identity * Matrix(2, 2)) is Matrix
    assert pickle.loads(pickle.dumps(identity)) == identity  # noqa: S301 - locally serialized value.
    assert type(copy.deepcopy(identity)) is pylopdf.IdentityMatrix
    with pytest.raises(TypeError):
        pylopdf.IdentityMatrix(2, 2)  # ty: ignore[too-many-positional-arguments]


@pytest.mark.parametrize(
    ("start", "end"),
    [((1, 1), (4, 5)), (Point(0, 0), Point(-3, 0)), ((0, 0), (0, 3)), ((-2, 7), (5, -1))],
)
def test_planish_line_moves_a_line_onto_the_x_axis(start: tuple[float, float], end: tuple[float, float]) -> None:
    matrix = pylopdf.planish_line(start, end)
    assert Point(start) * matrix == pytest.approx((0, 0), abs=1e-12)
    assert Point(end) * matrix == pytest.approx((abs(Point(end) - start), 0), abs=1e-12)
    assert matrix.is_rectilinear or abs(matrix.a * matrix.d - matrix.b * matrix.c) == pytest.approx(1)


def test_planish_line_of_one_point_is_zero() -> None:
    assert pylopdf.planish_line((1, 1), Point(1, 1)) == Matrix()
    assert pylopdf.planish_line((1, 1), (4, 5)) == pytest.approx((0.6, -0.8, 0.8, 0.6, -1.4, 0.2))
