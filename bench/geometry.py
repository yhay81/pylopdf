"""Measure affine value operations and compare a deterministic pymupdf oracle.

Run ``uv run --group bench python bench/geometry.py``. The optional benchmark
group supplies pymupdf; it is never a runtime dependency of pylopdf.
"""

from __future__ import annotations

import math
import platform
import random
import statistics
import timeit
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pymupdf

import pylopdf

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "bench" / "results" / "geometry-latest.md"
_DEGENERATE_AREA = 1e-3


class _PreviousRect(NamedTuple):
    """The four-field Rect representation before the geometry types, without validation."""

    x0: float
    y0: float
    x1: float
    y1: float


class _PreviousPoint(NamedTuple):
    """The two-field Point representation before the geometry types, without validation."""

    x: float
    y: float


def _check_oracle() -> int:
    """Compare small integer transforms without importing implementation code."""
    generator = random.Random(20261020)  # noqa: S311 - deterministic geometry, not security.
    checked = 0
    for _ in range(1000):
        values = [generator.randint(-10, 10) for _ in range(6)]
        coordinates = [generator.randint(-100, 100) for _ in range(2)]
        other = [generator.randint(-10, 10) for _ in range(6)]
        matrix, reference = pylopdf.Matrix(values), pymupdf.Matrix(values)
        point, refpoint = pylopdf.Point(coordinates), pymupdf.Point(coordinates)
        pairs = [(point * matrix, refpoint * reference), (matrix * other, reference * other)]
        if values[0] * values[3] != values[1] * values[2]:
            pairs.extend([(~matrix, ~reference), (point / matrix, refpoint / reference)])
        for actual, expected in pairs:
            if not all(math.isclose(a, b, rel_tol=2e-5, abs_tol=2e-4) for a, b in zip(actual, expected, strict=True)):
                msg = f"geometry differs: {actual!r} != {expected!r}"
                raise RuntimeError(msg)
            checked += 1
    return checked + _check_rect_oracle()


def _close(actual: object, expected: object) -> bool:
    """Compare flat or nested coordinates within the float32 oracle's tolerance."""
    flat_actual = [float(v) for item in actual for v in (item if isinstance(item, tuple) else (item,))]  # type: ignore[attr-defined]
    flat_expected = [float(v) for item in expected for v in (item if hasattr(item, "__len__") else (item,))]  # type: ignore[attr-defined]
    return len(flat_actual) == len(flat_expected) and all(
        math.isclose(a, b, rel_tol=2e-5, abs_tol=2e-4) for a, b in zip(flat_actual, flat_expected, strict=True)
    )


def _signed_area(quad: pylopdf.Quad) -> float:
    """Return the shoelace area of the outline ``ul, ur, lr, ll``."""
    outline = (quad.ul, quad.ur, quad.lr, quad.ll)
    return sum(a.x * b.y - b.x * a.y for a, b in zip(outline, outline[1:] + outline[:1], strict=True)) / 2


def _check_rect_oracle() -> int:
    """Compare rectangle and quad operations on seeded small-integer inputs."""
    generator = random.Random(20261021)  # noqa: S311 - deterministic geometry, not security.
    checked = 0
    for _ in range(1000):
        coords = [generator.randint(-20, 20) for _ in range(4)]
        other = [generator.randint(-20, 20) for _ in range(4)]
        point = [generator.randint(-25, 25) for _ in range(2)]
        values = [generator.randint(-4, 4) for _ in range(4)] + [generator.randint(-10, 10) for _ in range(2)]
        angle = generator.choice([0, 30, 45, 90, 180, 270])
        rect, ref = pylopdf.Rect(coords), pymupdf.Rect(coords)
        matrices = [(pylopdf.Matrix(values), pymupdf.Matrix(values)), (pylopdf.Matrix(angle), pymupdf.Matrix(angle))]
        pairs: list[tuple[object, object]] = [
            (rect | pylopdf.Rect(other), ref | pymupdf.Rect(other)),
            (rect & pylopdf.Rect(other), ref & pymupdf.Rect(other)),
            (rect | pylopdf.Point(point), ref | pymupdf.Point(point)),
            ((rect.width, rect.height, abs(rect), rect.get_area()), (ref.width, ref.height, abs(ref), ref.get_area())),
            ((rect + 0.5).round(), (ref + 0.5).round()),
            (pylopdf.IRect(rect * 0.5), pymupdf.IRect(ref * 0.5)),
            (rect.quad, ref.quad),
        ]
        flags = [
            (pylopdf.Point(point) in rect, pymupdf.Point(point) in ref),
            (pylopdf.Rect(other) in rect, pymupdf.Rect(other) in ref),
            (rect.intersects(other), ref.intersects(other)),
            ((rect.is_empty, rect.is_valid), (ref.is_empty, ref.is_valid)),
        ]
        for matrix, refmatrix in matrices:
            quad, refquad = rect.quad * matrix, ref.quad * refmatrix
            pairs.extend([(rect * matrix, ref * refmatrix), (quad, refquad), (quad.rect, refquad.rect)])
            pairs.append(((quad.width, quad.height, abs(quad)), (refquad.width, refquad.height, abs(refquad))))
            flags.append((quad.is_empty, refquad.is_empty))
            # Collinear corners sit on the convexity boundary, where the
            # float32 oracle decides by rounding noise.
            if not quad.is_empty and abs(_signed_area(quad)) > _DEGENERATE_AREA:
                flags.append((quad.is_convex, refquad.is_convex))
                flags.append((quad.is_rectangular, refquad.is_rectangular))
                if quad.is_convex:
                    flags.append((pylopdf.Point(point) in quad, pymupdf.Point(point) in refquad))
        for index, (actual, expected) in enumerate(pairs):
            if not _close(actual, expected):
                case = f"{coords}, {other}, {point}, {values}, {angle}"
                msg = f"geometry {index} differs for {case}: {actual!r} != {expected!r}"
                raise RuntimeError(msg)
            checked += 1
        for actual, expected in flags:
            if actual != expected:
                msg = f"geometry differs for {coords}, {other}, {point}, {values}, {angle}: {actual!r} != {expected!r}"
                raise RuntimeError(msg)
            checked += 1
    return checked


def _median_us(callback: Callable[[], object]) -> float:
    """Return a per-operation median over seven independent timing samples."""
    count = 20_000
    return statistics.median(timeit.repeat(callback, repeat=7, number=count)) * 1_000_000 / count


def main() -> None:
    """Record wins and losses for constructors, composition, and inversion."""
    checks = _check_oracle()
    point, matrix = pylopdf.Point(3, 4), pylopdf.Matrix(2, 0, 0, 3, 10, 20)
    refpoint, refmatrix = pymupdf.Point(3, 4), pymupdf.Matrix(2, 0, 0, 3, 10, 20)
    rect, other = pylopdf.Rect(1, 2, 30, 40), pylopdf.Rect(10, 20, 50, 60)
    refrect, refother = pymupdf.Rect(1, 2, 30, 40), pymupdf.Rect(10, 20, 50, 60)
    operations: list[tuple[str, Callable[[], object], Callable[[], object]]] = [
        ("Point(3, 4)", lambda: pylopdf.Point(3, 4), lambda: pymupdf.Point(3, 4)),
        ("Matrix(2, 3)", lambda: pylopdf.Matrix(2, 3), lambda: pymupdf.Matrix(2, 3)),
        ("point * matrix", lambda: point * matrix, lambda: refpoint * refmatrix),
        ("matrix * matrix", lambda: matrix * matrix, lambda: refmatrix * refmatrix),
        ("~matrix", lambda: ~matrix, lambda: ~refmatrix),
        ("Rect(1, 2, 3, 4)", lambda: pylopdf.Rect(1, 2, 3, 4), lambda: pymupdf.Rect(1, 2, 3, 4)),
        (
            "Rect(1.0, 2.0, 3.0, 4.0)",
            lambda: pylopdf.Rect(1.0, 2.0, 3.0, 4.0),
            lambda: pymupdf.Rect(1.0, 2.0, 3.0, 4.0),
        ),
        ("rect * matrix", lambda: rect * matrix, lambda: refrect * refmatrix),
        ("rect \\| rect", lambda: rect | other, lambda: refrect | refother),
        ("rect & rect", lambda: rect & other, lambda: refrect & refother),
        ("point in rect", lambda: point in rect, lambda: refpoint in refrect),
        ("rect.quad * matrix", lambda: rect.quad * matrix, lambda: refrect.quad * refmatrix),
    ]
    lines = [
        "# Geometry measurements",
        "",
        (
            f"Python {platform.python_version()}, {platform.system()} {platform.machine()}; "
            f"pylopdf {pylopdf.__version__}, pymupdf {pymupdf.VersionBind}."
        ),
        "",
        "Reproduce: `uv run --group bench python bench/geometry.py`.",
        "Per-operation median of seven samples, each with 20,000 operations.",
        "",
        "| Operation | pylopdf (us) | pymupdf (us) |",
        "| --- | ---: | ---: |",
    ]
    for label, ours, reference in operations:
        lines.append(f"| `{label}` | {_median_us(ours):.3f} | {_median_us(reference):.3f} |")
    previous = _median_us(lambda: _PreviousPoint(3, 4))
    current = _median_us(lambda: pylopdf.Point(3, 4))
    previous_rect = _median_us(lambda: _PreviousRect(1.0, 2.0, 3.0, 4.0))
    current_rect = _median_us(lambda: pylopdf.Rect(1.0, 2.0, 3.0, 4.0))
    lines.extend(
        [
            "",
            (
                f"The former two-field NamedTuple constructor takes {previous:.3f} us, "
                f"versus {current:.3f} us with finite-coordinate validation and overloaded constructors "
                f"({current / previous:.2f}x)."
            ),
            (
                f"For four floats, as extraction and search return them, the former four-field Rect "
                f"constructor takes {previous_rect:.3f} us, versus {current_rect:.3f} us with validation "
                f"({current_rect / previous_rect:.2f}x)."
            ),
            "This is a constructor cost, not a claim about whole-document performance.",
            "",
            (
                f"{checks:,} affine, rectangle, and quad results match pymupdf {pymupdf.VersionBind} on 2,000 "
                "seeded small-integer cases (relative tolerance 2e-5, absolute tolerance 2e-4). "
                "The oracle uses float32 on some paths; pylopdf retains Python float precision. "
                "Near-singular arbitrary floating-point matrices can therefore differ more. "
                "Independent regressions check inverse round trips and extreme scale ranges."
            ),
            "",
        ]
    )
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
