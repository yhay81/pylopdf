"""Arbitrary affine render matrices in Page.pixmap and Document.render."""

from __future__ import annotations

from pathlib import Path

import pytest

import pylopdf
from pylopdf import Color, Matrix

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
RED = Color(1, 0, 0)
BLUE = Color(0, 0, 1)


def _shapes_page(size: tuple[float, float] = (120, 80)) -> pylopdf.Page:
    """Return a page of axis-aligned rectangles on whole points, every edge on a pixel boundary."""
    page = pylopdf.Document().pages.new(size)
    with page.draw() as canvas:
        canvas.rect((10, 10, 50, 30), fill=RED)
        canvas.rect((60, 20, 110, 70), fill=BLUE)
        canvas.rect((0, 0, 5, 5), fill=RED)
    return page


def _rows(pixmap: pylopdf.Pixmap) -> list[list[bytes]]:
    """Return the pixels of ``pixmap`` as rows of per-pixel byte strings."""
    data, channels = bytes(pixmap), pixmap.channels
    return [
        [data[(y * pixmap.width + x) * channels : (y * pixmap.width + x + 1) * channels] for x in range(pixmap.width)]
        for y in range(pixmap.height)
    ]


def _clockwise(rows: list[list[bytes]]) -> list[list[bytes]]:
    return [list(row) for row in zip(*rows[::-1], strict=True)]


def _turned(rows: list[list[bytes]], quarter_turns: int) -> list[list[bytes]]:
    for _ in range(quarter_turns % 4):
        rows = _clockwise(rows)
    return rows


# Each matrix with what it does to the upright raster.
RIGHT_ANGLES = [
    pytest.param(Matrix(90), lambda rows: _turned(rows, 1), id="90"),
    pytest.param(Matrix(180), lambda rows: _turned(rows, 2), id="180"),
    pytest.param(Matrix(270), lambda rows: _turned(rows, 3), id="270"),
    pytest.param(Matrix(-1, 0, 0, 1, 0, 0), lambda rows: [row[::-1] for row in rows], id="flip-x"),
    pytest.param(Matrix(1, 0, 0, -1, 0, 0), lambda rows: rows[::-1], id="flip-y"),
]


@pytest.mark.parametrize("dpi", [36, 72, 100, 150])
@pytest.mark.parametrize("name", ["usrguide.pdf", "patent-us223898.pdf", "pdfium-smask-blend.pdf"])
def test_a_diagonal_matrix_renders_what_the_equivalent_dpi_renders(name: str, dpi: int) -> None:
    page = pylopdf.open(REAL_WORLD / name).pages[0]
    scale = dpi / 72

    by_dpi = page.pixmap(dpi=dpi)
    by_matrix = page.pixmap(matrix=Matrix(scale, scale))

    assert (by_matrix.width, by_matrix.height) == (by_dpi.width, by_dpi.height)
    assert bytes(by_matrix) == bytes(by_dpi)
    clip = (30.5, 40.25, 300, 200)
    assert bytes(page.pixmap(matrix=Matrix(scale, scale), clip=clip)) == bytes(page.pixmap(dpi=dpi, clip=clip))


@pytest.mark.parametrize(("matrix", "turn"), RIGHT_ANGLES)
def test_right_angles_and_flips_turn_the_upright_pixels(matrix: Matrix, turn: object) -> None:
    page = _shapes_page()
    upright = page.pixmap(dpi=144)

    turned = page.pixmap(matrix=matrix * Matrix(2, 2))

    assert _rows(turned) == turn(_rows(upright))  # ty: ignore[call-non-callable]


@pytest.mark.parametrize(("matrix", "turn"), RIGHT_ANGLES)
def test_a_scanned_page_turns_pixel_for_pixel_on_a_fractional_page_size(matrix: Matrix, turn: object) -> None:
    # An A4 page is 595.28 by 841.89 points: the turned raster keeps the
    # page's top-left corner on a pixel corner, as the upright raster does.
    page = pylopdf.open(REAL_WORLD / "patent-us223898.pdf").pages[0]
    upright = page.pixmap(dpi=24)

    turned = page.pixmap(matrix=matrix * Matrix(1 / 3, 1 / 3))

    assert _rows(turned) == turn(_rows(upright))  # ty: ignore[call-non-callable]


def test_a_rotation_covers_the_bounding_box_of_the_turned_page() -> None:
    page = _shapes_page()

    quarter = page.pixmap(matrix=Matrix(90))
    assert (quarter.width, quarter.height) == (80, 120)
    diagonal = page.pixmap(matrix=Matrix(45))
    # The turned 120 by 80 page spans x from -56.57 to 84.85 and y from 0 to
    # 141.42: the whole pixels on the grid of the page's top-left corner.
    assert (diagonal.width, diagonal.height) == (140, 141)
    sheared = page.pixmap(matrix=Matrix(1, 0, 0.5, 1, 0, 0))
    assert (sheared.width, sheared.height) == (160, 80)
    # The corners outside the turned page show the background.
    assert bytes(page.pixmap(matrix=Matrix(45), alpha=True))[:4] == b"\x00\x00\x00\x00"


def test_a_translation_moves_no_pixels() -> None:
    page = _shapes_page()
    turned = Matrix(90) * Matrix(2, 2)

    assert bytes(page.pixmap(matrix=turned * Matrix(1, 0, 0, 1, 7.5, -3))) == bytes(page.pixmap(matrix=turned))


@pytest.mark.parametrize(("matrix", "turn"), RIGHT_ANGLES)
def test_a_clip_maps_through_the_matrix(matrix: Matrix, turn: object) -> None:
    page = _shapes_page()
    clip = (10, 10, 50, 30)
    upright = page.pixmap(dpi=144, clip=clip)

    turned = page.pixmap(matrix=matrix * Matrix(2, 2), clip=clip)

    assert _rows(turned) == turn(_rows(upright))  # ty: ignore[call-non-callable]
    assert set(bytes(turned)) == {0, 255}


def test_a_clip_of_a_rotated_page_is_a_region_of_the_whole_raster() -> None:
    page = _shapes_page()
    whole = _rows(page.pixmap(matrix=Matrix(30)))

    clipped = page.pixmap(matrix=Matrix(30), clip=(55, 15, 115, 75))

    rows = _rows(clipped)
    origin = next(
        (x, y)
        for y in range(len(whole) - len(rows) + 1)
        for x in range(len(whole[0]) - len(rows[0]) + 1)
        if all(whole[y + dy][x : x + len(rows[0])] == row for dy, row in enumerate(rows))
    )
    assert origin is not None


def test_render_takes_the_same_matrices() -> None:
    doc = pylopdf.Document()
    doc.pages.new((120, 80))
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.rect((10, 10, 50, 30), fill=RED)
    matrix = Matrix(90) * Matrix(1.5, 1.5)

    assert doc.render(matrix=matrix) == [page.pixmap(matrix=matrix).to_bytes()]
    assert doc.render(matrix=matrix, workers=2, pages=[0, 0]) == [page.pixmap(matrix=matrix).to_bytes()] * 2


@pytest.mark.parametrize("matrix", [(1, 0, 0, 0, 0, 0), Matrix(2, 4, 1, 2, 0, 0), Matrix(0, 0, 0, 0, 5, 5)])
def test_a_singular_matrix_raises(matrix: object) -> None:
    page = _shapes_page()

    with pytest.raises(ValueError, match="matrix must be invertible"):
        page.pixmap(matrix=matrix)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="matrix must be invertible"):
        page.document.render(matrix=matrix)  # ty: ignore[invalid-argument-type]


def test_a_turned_raster_keeps_the_size_limits() -> None:
    page = _shapes_page((1000, 10))

    with pytest.raises(pylopdf.PdfError, match="65535-pixel limit per side"):
        page.pixmap(matrix=Matrix(70, 1) * Matrix(90))
