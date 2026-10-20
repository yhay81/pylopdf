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


class _PreviousPoint(NamedTuple):
    """The two-field Point representation before F4, without validation."""

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
    operations: list[tuple[str, Callable[[], object], Callable[[], object]]] = [
        ("Point(3, 4)", lambda: pylopdf.Point(3, 4), lambda: pymupdf.Point(3, 4)),
        ("Matrix(2, 3)", lambda: pylopdf.Matrix(2, 3), lambda: pymupdf.Matrix(2, 3)),
        ("point * matrix", lambda: point * matrix, lambda: refpoint * refmatrix),
        ("matrix * matrix", lambda: matrix * matrix, lambda: refmatrix * refmatrix),
        ("~matrix", lambda: ~matrix, lambda: ~refmatrix),
    ]
    lines = [
        "# F4 Point and Matrix measurements",
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
    lines.extend(
        [
            "",
            (
                f"The former two-field NamedTuple constructor takes {previous:.3f} us, "
                f"versus {current:.3f} us with finite-coordinate validation and overloaded constructors "
                f"({current / previous:.2f}x)."
            ),
            "This is a constructor cost, not a claim about whole-document performance.",
            "",
            (
                f"{checks:,} affine results match pymupdf {pymupdf.VersionBind} on 1,000 seeded small-integer "
                "matrices and points (relative tolerance 2e-5, absolute tolerance 2e-4). "
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
