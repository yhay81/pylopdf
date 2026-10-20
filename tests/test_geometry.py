"""Independent affine examples and PDF round trips for roadmap F4."""

from __future__ import annotations

import copy
import math
import operator
import pickle
from collections.abc import Callable
from typing import get_type_hints

import pytest

import pylopdf
from pylopdf import Matrix, Point


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((), (0, 0)),
        ((3, 4), (3, 4)),
        (((3, 4),), (3, 4)),
        (([3, 4],), (3, 4)),
        ((Point(3, 4),), (3, 4)),
    ],
)
def test_point_constructors(args: tuple, expected: tuple[float, float]) -> None:
    point = Point(*args)
    assert point == expected
    assert all(isinstance(value, float) for value in point)
    assert Point(x=3, y=4) == Point(3, y=4) == Point(3, 4)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((), (0, 0, 0, 0, 0, 0)),
        ((0,), (1, 0, 0, 1, 0, 0)),
        ((90,), (0, 1, -1, 0, 0, 0)),
        ((-90,), (0, -1, 1, 0, 0, 0)),
        ((450,), (0, 1, -1, 0, 0, 0)),
        ((180,), (-1, 0, 0, -1, 0, 0)),
        ((2, 3), (2, 0, 0, 3, 0, 0)),
        ((2, 3, 0), (2, 0, 0, 3, 0, 0)),
        ((2, 3, 1), (1, 3, 2, 1, 0, 0)),
        ((1, 2, 3, 4, 5, 6), (1, 2, 3, 4, 5, 6)),
        (((1, 2, 3, 4, 5, 6),), (1, 2, 3, 4, 5, 6)),
        (([1, 2, 3, 4, 5, 6],), (1, 2, 3, 4, 5, 6)),
        ((Matrix(2, 3),), (2, 0, 0, 3, 0, 0)),
    ],
)
def test_matrix_constructors(args: tuple, expected: tuple[float, ...]) -> None:
    matrix = Matrix(*args)
    assert matrix == expected
    assert all(isinstance(value, float) for value in matrix)
    assert Matrix(a=1, b=2, c=3, d=4, e=5, f=6) == Matrix(1, 2, c=3, d=4, e=5, f=6)


@pytest.mark.parametrize("angle", [30, 45, 123.5, -400.5, 1e300])
def test_rotation_uses_degrees_and_bounded_angles(angle: float) -> None:
    radians = math.radians(angle % 360)
    assert Matrix(angle) == pytest.approx(
        (math.cos(radians), math.sin(radians), -math.sin(radians), math.cos(radians), 0, 0)
    )
    assert Point(1, 0) * Matrix(90) == (0, 1)


@pytest.mark.parametrize("value", [Point(3, 4), Matrix(1, 2, 3, 4, 5, 6)])
def test_values_keep_named_tuple_contracts(value: Point | Matrix) -> None:
    assert isinstance(value, tuple)
    assert len(value) == len(value._fields)
    assert value[0] == next(iter(value))
    assert value[:] == tuple(value)
    assert hash(value) == hash(tuple(value))
    assert {value: "value"}[type(value)(value)] == "value"
    assert type(value).__module__ == "pylopdf"
    assert repr(value).startswith(type(value).__name__ + "(")
    for restored in (copy.copy(value), copy.deepcopy(value), pickle.loads(pickle.dumps(value))):  # noqa: S301 - locally serialized values.
        assert restored == value
        assert type(restored) is type(value)
    assert value._asdict() == dict(zip(value._fields, value, strict=True))
    assert get_type_hints(type(value)) == dict.fromkeys(value._fields, float)
    with pytest.raises(AttributeError):
        setattr(value, value._fields[0], 99)
    with pytest.raises(TypeError):
        operator.setitem(value, 0, 99)  # ty: ignore[no-matching-overload]


@pytest.mark.parametrize("point", [Point(3, -4), Point(0, 0)])
def test_point_arithmetic(point: Point) -> None:
    x, y = point
    assert point + (2, 3) == Point(x + 2, y + 3)  # noqa: RUF005 - geometric addition, not concatenation.
    assert point + 2 == Point(x + 2, y + 2)
    assert point - [2, 3] == Point(x - 2, y - 3)
    assert point - 2 == Point(x - 2, y - 2)
    assert point * 2 == Point(x * 2, y * 2)
    assert point / 2 == Point(x / 2, y / 2)
    assert -point == Point(-x, -y)
    assert +point == point
    assert bool(point) == (x != 0 or y != 0)
    assert point.norm() == abs(point) == math.hypot(x, y)


def test_point_directions() -> None:
    assert Point().unit == Point().abs_unit == (0, 0)
    assert Point(-3, 4).unit == pytest.approx((-0.6, 0.8))
    assert Point(-3, -4).abs_unit == pytest.approx((0.6, 0.8))
    assert abs(Point(1.7e308, 1.7e308).unit) == pytest.approx(1)
    assert Point(1e-300, 0).unit == (1, 0)


def test_matrix_arithmetic() -> None:
    matrix = Matrix(1, 2, 3, 4, 5, 6)
    assert matrix + 2 == (3, 4, 5, 6, 7, 8)
    assert matrix - 2 == (-1, 0, 1, 2, 3, 4)
    assert matrix + (6, 5, 4, 3, 2, 1) == (7,) * 6  # noqa: RUF005 - geometric addition, not concatenation.
    assert matrix - [1, 1, 2, 2, 3, 3] == (0, 1, 1, 2, 2, 3)
    assert matrix * 2 == (2, 4, 6, 8, 10, 12)
    assert matrix / 2 == (0.5, 1, 1.5, 2, 2.5, 3)
    assert -matrix == (-1, -2, -3, -4, -5, -6)
    assert +matrix == matrix
    assert matrix.norm() == abs(matrix) == math.sqrt(91)
    assert not Matrix()
    assert Matrix(0, 0, 0, 0, 0, 1)


def test_composition_applies_the_left_matrix_first() -> None:
    scale = Matrix(2, 3)
    translate = Matrix(1, 0, 0, 1, 10, -20)
    point = Point(4, 5)
    assert point * scale * translate == (18, -5)
    assert point * (scale * translate) == (18, -5)
    assert point * (translate * scale) == (28, -45)
    assert point * tuple(translate) == (14, -15)
    assert scale * tuple(translate) == scale * translate
    assert point * Matrix(2, 3, 1) == (14, 17)


@pytest.mark.parametrize(
    "matrix",
    [Matrix(2, 3), Matrix(90), Matrix(37), Matrix(1, 2, 3, 4, 5, 6), Matrix(1, 0, 0, 1, -300, 200)],
)
def test_inverse_and_division_round_trips(matrix: Matrix) -> None:
    identity = Matrix(1, 1)
    assert matrix * ~matrix == pytest.approx(identity, abs=1e-10)
    assert ~matrix * matrix == pytest.approx(identity, abs=1e-10)
    assert matrix / matrix == pytest.approx(identity, abs=1e-10)
    assert identity / tuple(matrix) == pytest.approx(~matrix)
    for point in [Point(), Point(23, -17), Point(-1000, 5000)]:
        assert (point * matrix) / matrix == pytest.approx(point, abs=1e-10)
        assert point / tuple(matrix) == pytest.approx(point * ~matrix)


@pytest.mark.parametrize("scale", [1e-200, 1e200])
def test_inverse_does_not_overflow_or_underflow_the_determinant(scale: float) -> None:
    matrix = Matrix(scale, scale)
    assert ~matrix == pytest.approx(Matrix(1 / scale, 1 / scale))
    assert matrix * ~matrix == pytest.approx(Matrix(1, 1))


def test_inverse_preserves_axes_with_very_different_scales() -> None:
    matrix = Matrix(1e200, 1e-200)
    assert ~matrix == Matrix(1e-200, 1e200)
    assert matrix * ~matrix == Matrix(1, 1)


@pytest.mark.parametrize(
    "matrix",
    [
        Matrix(),
        Matrix(1, 2, 2, 4, 5, 6),
        Matrix(0, 0, 0, 0, 10, 20),
        Matrix(1, 5, 5, 25, 0, 0),
        Matrix(3, 4, 15, 20, 0, 0),
        Matrix(1e200, 1e200, 1e200, 1e200, 0, 0),
        Matrix(1e-200, 1e-200, 1e-200, 1e-200, 0, 0),
    ],
)
def test_singular_inverse_is_zero_and_division_raises(matrix: Matrix) -> None:
    assert ~matrix == Matrix()
    for value in (Point(1, 2), Matrix(1, 1)):
        with pytest.raises(ZeroDivisionError, match="not invertible"):
            operator.truediv(value, matrix)


@pytest.mark.parametrize("value", [Point(1, 2), Matrix(1, 1)])
def test_zero_division_and_reverse_repetition_are_rejected(value: Point | Matrix) -> None:
    with pytest.raises(ZeroDivisionError):
        operator.truediv(value, 0)
    with pytest.raises(TypeError):
        operator.mul(2, value)
    with pytest.raises(TypeError):
        operator.add(tuple(value), value)


@pytest.mark.parametrize(
    ("matrix", "expected"),
    [
        (Matrix(), True),
        (Matrix(2, -3), True),
        (Matrix(90), True),
        (Matrix(180), True),
        (Matrix(270), True),
        (Matrix(45), False),
        (Matrix(1, 2, 1), False),
        (Matrix(1, 1e-6, 0, 1, 40, 20), True),
        (Matrix(1, 1e-4, 0, 1, 40, 20), False),
    ],
)
def test_rectilinearity(matrix: Matrix, *, expected: bool) -> None:
    assert matrix.is_rectilinear is expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ((6, 8), 5),
        ((0, 0, 10, 10), 0),
        ((10, 10, 0, 0), 0),
        ((6, 0, 10, 10), 3),
        ((6, 8, 10, 10), 5),
        ((0, 0, 0, 0), 5),
        (pylopdf.Rect(3, 4, 3, 4), 0),
    ],
)
def test_distance_to_point_or_rectangle(target: tuple[float, ...], expected: float) -> None:
    assert Point(3, 4).distance_to(target) == expected


@pytest.mark.parametrize(("unit", "expected"), [("px", 72), ("in", 1), ("cm", 2.54), ("mm", 25.4)])
def test_distance_units(unit: str, expected: float) -> None:
    assert Point().distance_to((72, 0), unit) == pytest.approx(expected)
    with pytest.raises(pylopdf.PdfError, match="unit"):
        Point().distance_to((72, 0), "invalid")


@pytest.mark.parametrize("coordinate", [math.nan, math.inf, -math.inf, 10**400])
def test_nonfinite_coordinates_and_operands_are_refused(coordinate: float) -> None:
    for construct in (
        lambda: Point(coordinate, 1),
        lambda: Matrix(coordinate),
        lambda: Matrix(coordinate, 1),
        lambda: Matrix(1, 0, 0, 1, coordinate, 0),
        lambda: Point(1, 2) * coordinate,
        lambda: Matrix(1, 1) + coordinate,
        lambda: Point(1, 2).distance_to((coordinate, 0)),
    ):
        with pytest.raises(pylopdf.PdfError, match="finite"):
            construct()


@pytest.mark.parametrize(
    "construct",
    [
        lambda: Point("12"),  # ty: ignore[invalid-argument-type]
        lambda: Point([1]),
        lambda: Point([1, 2, 3]),
        lambda: Point(iter([1, 2])),  # ty: ignore[invalid-argument-type]
        lambda: Point(1, x=2, y=3),  # ty: ignore[no-matching-overload]
        lambda: Point(x=1),  # ty: ignore[no-matching-overload]
        lambda: Matrix("123456"),  # ty: ignore[invalid-argument-type]
        lambda: Matrix([1] * 7),
        lambda: Matrix(iter(range(6))),  # ty: ignore[invalid-argument-type]
        lambda: Matrix(1, 2, 2),
        lambda: Matrix(a=1),  # ty: ignore[no-matching-overload]
        lambda: Point(1, 2) * (3, 4),
        lambda: Point(1, 2) / (3, 4),
        lambda: operator.add(Matrix(1, 1), (1, 2)),
        lambda: Matrix(1, 1) * (1, 2),
        lambda: Matrix(1, 1) / (1, 2),
    ],
)
def test_malformed_inputs_fail_without_partial_values(construct: Callable[[], object]) -> None:
    with pytest.raises((TypeError, pylopdf.PdfError)):
        construct()


def test_finite_input_that_overflows_an_operation_is_refused() -> None:
    with pytest.raises(pylopdf.PdfError, match="finite"):
        _ = Point(1e308, 1) * 10
    with pytest.raises(pylopdf.PdfError, match="finite"):
        _ = Matrix(1e308, 1) * 10
    with pytest.raises(pylopdf.PdfError, match="finite"):
        _ = ~Matrix(5e-324, 5e-324)


@pytest.mark.parametrize("name", ["transform"])
def test_point_has_no_mutating_alias(name: str) -> None:
    assert not hasattr(Point(), name)


@pytest.mark.parametrize("name", ["concat", "invert", "prerotate", "prescale", "preshear", "pretranslate"])
def test_matrix_has_no_mutating_alias(name: str) -> None:
    assert not hasattr(Matrix(), name)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_transformed_points_work_with_drawing_and_save_reopen(rotation: int) -> None:
    with pylopdf.Document() as document:
        page = document.new_page(width=200, height=200)
        page.set_rotation(rotation)
        transform = Matrix(2, 2) * Matrix(1, 0, 0, 1, 20, 30)
        start, end = Point(10, 10) * transform, Point(40, 10) * transform
        assert page.draw_line(start, end, color=(1, 0, 0), width=2) == end
        page.insert_text(Point(20, 40) * transform, "Affine", fontsize=12)
        before = page.get_pixmap().samples
        data = document.tobytes()
    with pylopdf.Document(stream=data) as reopened:
        assert reopened[0].get_text().strip() == "Affine"
        assert reopened[0].get_pixmap().samples == before
        lines = reopened[0].get_drawings()
        assert lines[0]["items"][0] == ("l", start, end)
