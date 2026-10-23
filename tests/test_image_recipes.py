"""The NumPy and Pillow image-processing recipes of the ecosystem guide.

The recipes are the Python blocks of the guide's image-processing section,
run here as written. Expected values come from pymupdf 2.0's Pixmap methods
on the same pixels.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

numpy = pytest.importorskip("numpy", reason="interop group (numpy) is not installed")
pytest.importorskip("PIL", reason="interop group (Pillow) is not installed")

import pylopdf  # noqa: E402 - after the optional imports.
from pylopdf import Color, IRect, Pixmap, Quad  # noqa: E402

GUIDE = Path(__file__).parents[1] / "docs" / "en" / "ecosystem.md"
SECTION = "## Image processing with NumPy and Pillow"


def _recipes() -> dict[str, Any]:
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(SECTION)
    end = text.find("\n## ", start + len(SECTION))
    section = text[start : end if end != -1 else len(text)]
    blocks = re.findall(r"```python\n(.*?)```", section, flags=re.DOTALL)
    assert len(blocks) >= 6
    namespace: dict[str, Any] = {}
    exec(compile("\n".join(blocks), str(GUIDE), "exec"), namespace)  # noqa: S102 - the guide's own code.
    return namespace


R = _recipes()


def _rgb() -> Pixmap:
    """Return the 3-by-2 RGB pixmap that the pymupdf expectations were computed on."""
    rows = [[(10, 20, 30), (200, 100, 50), (0, 0, 0)], [(0, 0, 0), (0, 0, 0), (255, 255, 255)]]
    return Pixmap.from_array(numpy.array(rows, dtype=numpy.uint8), resolution=(150, 150))


def _gray() -> Pixmap:
    return Pixmap.from_array(numpy.array([[0, 10, 20], [30, 40, 50]], dtype=numpy.uint8))


def _rows(pix: Pixmap) -> list[Any]:
    return numpy.asarray(pix).tolist()


def test_recipes_keep_the_resolution_and_leave_the_source_unchanged() -> None:
    source = _rgb()
    before = bytes(source)

    assert R["invert"](source).resolution == (150, 150)
    assert bytes(source) == before


def test_pixel_reads_one_pixel() -> None:
    assert R["pixel"](_rgb(), 1, 0) == (200, 100, 50)
    page = pylopdf.Document().pages.new((20, 20))
    with page.draw() as canvas:
        canvas.rect((5, 5, 15, 15), fill=Color(1, 0, 0))
    rendered = page.pixmap(alpha=True)
    assert R["pixel"](rendered, 10, 10) == (255, 0, 0, 255)
    assert R["pixel"](rendered, 1, 1) == (0, 0, 0, 0)


def test_fill_paints_a_rectangle_as_set_rect_and_clear_with_do() -> None:
    filled = R["fill"](_rgb(), IRect(0, 0, 1, 1), (7, 7, 7))
    assert _rows(filled) == [[[7, 7, 7], [200, 100, 50], [0, 0, 0]], [[0, 0, 0], [0, 0, 0], [255, 255, 255]]]

    with_alpha = R["with_alpha"](_rgb(), 255)
    # A color without alpha leaves the alpha channel as it was.
    painted = R["fill"](with_alpha, (1, 0, 3, 2), (1, 2, 3))
    assert R["pixel"](painted, 2, 1) == (1, 2, 3, 255)
    assert R["pixel"](R["fill"](with_alpha, (0, 0, 1, 1), (9, 9, 9, 0)), 0, 0) == (9, 9, 9, 0)


def test_paste_copies_a_region_as_copy_does() -> None:
    white = Pixmap.from_array(numpy.full((2, 3, 3), 255, dtype=numpy.uint8))

    pasted = R["paste"](white, _rgb(), (0, 0, 2, 1))

    assert _rows(pasted) == [[[10, 20, 30], [200, 100, 50], [255, 255, 255]], [[255] * 3, [255] * 3, [255] * 3]]


def test_tint_matches_tint_with() -> None:
    tinted = R["tint"](_rgb(), 0x102030, 0xF0E0D0)

    assert _rows(tinted) == [
        [[25, 47, 67], [192, 107, 79], [16, 32, 48]],
        [[16, 32, 48], [16, 32, 48], [240, 224, 208]],
    ]
    assert _rows(R["tint"](_gray(), 0x000000, 0x00FF00)) == _rows(_gray())


def test_gamma_matches_gamma_with() -> None:
    assert _rows(R["gamma"](_rgb(), 2.0)) == [
        [[0, 1, 3], [156, 39, 9], [0, 0, 0]],
        [[0, 0, 0], [0, 0, 0], [255, 255, 255]],
    ]


def test_invert_matches_invert_irect() -> None:
    assert _rows(R["invert"](_rgb(), IRect(0, 0, 2, 1))) == [
        [[245, 235, 225], [55, 155, 205], [0, 0, 0]],
        [[0, 0, 0], [0, 0, 0], [255, 255, 255]],
    ]
    translucent = R["with_alpha"](_gray(), 128)
    assert R["pixel"](R["invert"](translucent), 0, 0) == (255, 128)


def test_alpha_is_added_replaced_and_removed() -> None:
    plane = numpy.array([[0, 64, 128], [192, 255, 255]], dtype=numpy.uint8)

    added = R["with_alpha"](_rgb(), plane)
    assert (added.channels, added.alpha) == (4, True)
    assert numpy.asarray(added)[..., 3].tolist() == plane.tolist()
    replaced = R["with_alpha"](added, 255)
    assert set(numpy.asarray(replaced)[..., 3].ravel().tolist()) == {255}
    removed = R["without_alpha"](added)
    assert (removed.channels, bytes(removed)) == (3, bytes(_rgb()))
    gray = R["with_alpha"](_gray(), 9)
    assert (gray.channels, R["pixel"](gray, 1, 0)) == (2, (10, 9))


def test_color_statistics_match_color_count_and_color_topusage() -> None:
    pix = _rgb()

    assert R["color_counts"](pix) == {b"\n\x14\x1e": 1, b"\xc8d2": 1, b"\x00\x00\x00": 3, b"\xff\xff\xff": 1}
    assert len(R["color_counts"](pix)) == 4
    assert R["top_color"](pix) == (0.5, b"\x00\x00\x00")
    assert not R["is_unicolor"](pix)
    assert R["is_unicolor"](R["fill"](pix, (0, 0, 3, 2), (1, 2, 3)))
    # Alpha does not make another color.
    assert R["is_unicolor"](R["with_alpha"](Pixmap.from_array(numpy.zeros((2, 2), numpy.uint8)), [[0, 255], [9, 9]]))


def test_monochrome_means_gray_black_and_white() -> None:
    black_and_white = Pixmap.from_array(numpy.array([[0, 255], [255, 0]], dtype=numpy.uint8))

    assert R["is_monochrome"](black_and_white)
    assert not R["is_monochrome"](_gray())
    assert not R["is_monochrome"](R["fill"](_rgb(), (0, 0, 3, 2), (0, 0, 0)))


def test_digest_size_and_rect_need_no_recipe() -> None:
    pix = _rgb()

    assert hashlib.md5(pix).digest() == hashlib.md5(bytes(pix)).digest()  # noqa: S324 - pymupdf's digest is MD5.
    assert memoryview(pix).nbytes == 18
    assert IRect(0, 0, pix.width, pix.height) == IRect(0, 0, 3, 2)


@pytest.mark.parametrize(
    ("turns", "expected"),
    [
        ("ROTATE_90", [[30, 0], [40, 10], [50, 20]]),
        ("ROTATE_180", [[50, 40, 30], [20, 10, 0]]),
        ("ROTATE_270", [[20, 50], [10, 40], [0, 30]]),
    ],
)
def test_turned_matches_flip_rotate(turns: str, expected: list[list[int]]) -> None:
    assert _rows(R["turned"](_gray(), R[turns])) == expected


def test_flipped_matches_flip_rotate() -> None:
    assert _rows(R["flipped"](_gray())) == [[20, 10, 0], [50, 40, 30]]
    assert _rows(R["flipped"](_gray(), left_right=False)) == [[30, 40, 50], [0, 10, 20]]


def test_a_turned_page_raster_equals_rendering_it_turned() -> None:
    page = pylopdf.Document().pages.new((30, 20))
    with page.draw() as canvas:
        canvas.rect((2, 3, 12, 9), fill=Color(0, 0, 1))

    assert bytes(R["turned"](page.pixmap(), R["ROTATE_90"])) == bytes(page.pixmap(matrix=pylopdf.Matrix(90)))


def test_shrunk_averages_blocks_as_shrink_does() -> None:
    array = numpy.zeros((4, 8, 3), dtype=numpy.uint8)
    array[...] = (100, 0, 0)
    array[0:2, 0:2] = (200, 0, 0)
    pix = Pixmap.from_array(array, resolution=(300, 300))

    shrunk = R["shrunk"](pix, 1)

    assert (shrunk.width, shrunk.height) == (4, 2)
    assert numpy.asarray(shrunk)[0, :2].tolist() == [[200, 0, 0], [100, 0, 0]]
    assert shrunk.resolution == (150, 150)


def test_warped_maps_a_quad_onto_the_output() -> None:
    page = pylopdf.Document().pages.new((40, 40))
    with page.draw() as canvas:
        canvas.rect((10, 10, 30, 30), fill=Color(1, 0, 0))
    pix = page.pixmap()

    whole = R["warped"](pix, Quad((0, 0), (40, 0), (0, 40), (40, 40)), 40, 40)
    assert numpy.abs(numpy.asarray(whole).astype(int) - numpy.asarray(pix)).max() <= 1

    inner = R["warped"](pix, Quad((10, 10), (30, 10), (10, 30), (30, 30)), 20, 20)
    assert (inner.width, inner.height) == (20, 20)
    assert R["is_unicolor"](inner)
    assert R["pixel"](inner, 10, 10) == (255, 0, 0)

    # A quad turned a quarter turn reads the source's columns as rows.
    turned = R["warped"](pix, Quad((40, 0), (40, 40), (0, 0), (0, 40)), 40, 40)
    expected = numpy.asarray(R["turned"](pix, R["ROTATE_90"])).astype(int)
    assert numpy.abs(numpy.asarray(turned).astype(int) - expected).mean() < 8


@pytest.mark.parametrize(("options", "mode"), [({}, "RGB"), ({"alpha": True}, "RGBA"), ({"colorspace": "gray"}, "L")])
def test_pillow_round_trips_every_layout(options: dict[str, object], mode: str) -> None:
    pix = pylopdf.Document().pages.new((20, 10)).pixmap(dpi=144, **options)  # ty: ignore[invalid-argument-type]

    image = R["to_image"](pix)

    assert image.mode == mode
    back = R["from_image"](image, pix)
    assert (bytes(back), back.channels, back.resolution) == (bytes(pix), pix.channels, (144, 144))
    assert R["to_image"](pix).convert("CMYK").mode == "CMYK"
