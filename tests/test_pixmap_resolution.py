"""Pixmap.resolution and the resolution that PNG and JPEG output states."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

import pylopdf
from pylopdf import Matrix, Pixmap

METRES_PER_INCH = 0.0254


def _page(size: tuple[float, float] = (100, 50)) -> pylopdf.Page:
    return pylopdf.Document().pages.new(size)


def _png_resolution(data: bytes) -> tuple[int, int, int]:
    """Return the pixels per unit and the unit of a PNG's ``pHYs`` chunk."""
    position = 8
    while position < len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        kind = data[position + 4 : position + 8]
        if kind == b"pHYs":
            return struct.unpack(">IIB", data[position + 8 : position + 17])
        if kind == b"IDAT":
            break
        position += 12 + length
    msg = "no pHYs chunk before the image data"
    raise AssertionError(msg)


def _jpeg_density(data: bytes) -> tuple[int, int, int]:
    """Return the unit and the densities of a JPEG's JFIF header."""
    assert data[:4] == b"\xff\xd8\xff\xe0"
    assert data[6:11] == b"JFIF\x00"
    unit = data[13]
    x, y = struct.unpack(">HH", data[14:18])
    return unit, x, y


def _dpi_of_png(data: bytes) -> tuple[int, int]:
    x, y, unit = _png_resolution(data)
    assert unit == 1
    return (round(x * METRES_PER_INCH), round(y * METRES_PER_INCH))


@pytest.mark.parametrize(
    ("options", "resolution"),
    [
        ({}, (72, 72)),
        ({"dpi": 150}, (150, 150)),
        ({"dpi": 100.4}, (100, 100)),
        ({"matrix": Matrix(2, 3)}, (144, 216)),
        # A quarter turn gives the raster's x axis the page's y resolution.
        ({"matrix": Matrix(90) * Matrix(2, 3)}, (144, 216)),
        ({"matrix": Matrix(2, 3) * Matrix(90)}, (216, 144)),
        ({"matrix": Matrix(45) * Matrix(2, 2)}, (144, 144)),
        ({"matrix": Matrix(-2, 0, 0, 2, 0, 0)}, (144, 144)),
    ],
)
def test_a_rendered_page_states_its_resolution(options: dict[str, object], resolution: tuple[int, int]) -> None:
    pixmap = _page().pixmap(**options)  # ty: ignore[invalid-argument-type]

    assert pixmap.resolution == resolution
    assert _dpi_of_png(pixmap.to_bytes()) == resolution
    assert _jpeg_density(pixmap.to_bytes("jpeg")) == (1, *resolution)


def test_png_states_pixels_per_metre() -> None:
    data = _page().pixmap(dpi=300).to_bytes()

    assert _png_resolution(data) == (11811, 11811, 1)


@pytest.mark.parametrize("resolution", [1, 72, 96, 150, 299, 300, 600, 1200, 2400, 65535])
def test_every_resolution_round_trips_through_png(resolution: int) -> None:
    pixmap = Pixmap.from_array(memoryview(bytes(12)).cast("B", (2, 2, 3)), resolution=(resolution, 7))

    assert _dpi_of_png(pixmap.to_bytes()) == (resolution, 7)


def test_saved_files_state_the_resolution(tmp_path: Path) -> None:
    pixmap = _page().pixmap(dpi=200)
    pixmap.save(tmp_path / "page.png")
    pixmap.save(tmp_path / "page.jpg")

    assert _dpi_of_png((tmp_path / "page.png").read_bytes()) == (200, 200)
    assert _jpeg_density((tmp_path / "page.jpg").read_bytes()) == (1, 200, 200)


def test_render_states_the_resolution_of_each_page() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 50))
    doc.pages.new((60, 80))

    for data in doc.render(dpi=120, workers=2):
        assert _dpi_of_png(data) == (120, 120)
    for data in doc.render(matrix=Matrix(2, 1), format="jpeg", max_size=None):
        assert _jpeg_density(data) == (1, 144, 72)


def test_a_resolution_beyond_jpeg_densities_is_clamped() -> None:
    page = _page((1, 1))

    assert page.pixmap(matrix=Matrix(1000, 1000)).resolution == (65535, 65535)
    assert page.pixmap(matrix=Matrix(0.001, 0.001) * Matrix(1000, 1000)).resolution == (72, 72)


def test_from_array_defaults_to_96_dpi() -> None:
    pixmap = Pixmap.from_array(memoryview(bytes(4)).cast("B", (2, 2)))

    assert pixmap.resolution == (96, 96)
    assert _dpi_of_png(pixmap.to_bytes()) == (96, 96)


def test_from_array_of_a_pixmap_keeps_its_pixels_and_resolution() -> None:
    rendered = _page().pixmap(dpi=144)

    same = Pixmap.from_array(rendered)
    retagged = Pixmap.from_array(rendered, resolution=(300, 600))

    assert same.resolution == (144, 144)
    assert retagged.resolution == (300, 600)
    assert rendered.resolution == (144, 144)
    assert (retagged.width, retagged.height, retagged.channels) == (rendered.width, rendered.height, 3)
    assert bytes(retagged) == bytes(rendered)
    assert _dpi_of_png(retagged.to_bytes()) == (300, 600)


@pytest.mark.parametrize(
    ("resolution", "error"),
    [
        ((0, 96), ValueError),
        ((96, 65536), ValueError),
        ((96,), ValueError),
        ((96, 96, 96), ValueError),
        ((96.0, 96), TypeError),
        ((True, 96), TypeError),
        ([96, 96], TypeError),
        (96, TypeError),
    ],
)
def test_from_array_refuses_other_resolutions(resolution: object, error: type[Exception]) -> None:
    with pytest.raises(error, match="resolution must be a tuple of two integers from 1 through 65535"):
        Pixmap.from_array(memoryview(bytes(4)).cast("B", (2, 2)), resolution=resolution)  # ty: ignore[invalid-argument-type]


def test_an_image_object_pixmap_states_96_dpi() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((100, 100))
    with page.draw() as canvas:
        canvas.image((0, 0, 50, 50), Pixmap.from_array(memoryview(bytes(12)).cast("B", (2, 2, 3))))
    (image,) = page.resources.images

    assert image.to_pixmap().resolution == (96, 96)
