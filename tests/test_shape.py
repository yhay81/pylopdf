"""pymupdf's Shape: vector paths and text committed to a page in one step."""

from __future__ import annotations

import base64
import gc
import re
import struct
import warnings
from pathlib import Path

import pytest

import pylopdf
from pylopdf import pylopdf_core

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
WHITE = (255, 255, 255)
A4 = (595.28, 841.89)

# Generated 64x64 grayscale linear-gradient JPEG, as in test_image_compression.
PHOTO_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAME"
    "BgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/wAALCABAAEABAREA"
    "/8QAFgABAQEAAAAAAAAAAAAAAAAAAAgJ/8QAGhAAAgMBAQAAAAAAAAAAAAAAABcBZKFT"
    "kf/aAAgBAQAAPwDD5axwkLWOEhaxwkLWOEhaxwkLWOEhaxwkLWOElAravgW1fAtq+Bb"
    "V8C2r4FtXwLavgW1fCgFtXwLavgW1fAtq+BbV8C2r4FtXwLavhQC2r4FtXwLavgW1fA"
    "tq+BbV8C2r4FtXwoBbV8C2r4FtXwLavgW1fAtq+BbV8C2r4UAtq+BbV8C2r4FtXwLav"
    "gW1fAtq+BbV8KAW1fAtq+BbV8C2r4FtXwLavgW1fAtq+FALeOEeBbxwjwLeOEeBbxwj"
    "wLeOEeBbxwjwLeOEeBbxwjw//9k="
)


@pytest.fixture(scope="module")
def regular() -> pylopdf.Font:
    return pylopdf.Font(fontfile=NOTO_SANS_JP)


@pytest.fixture(scope="module")
def bold() -> pylopdf.Font:
    # The repository bundles regular faces only; a patched OS/2 weight class
    # gives a face that declares bold, as tests/test_font.py does.
    data = bytearray(NOTO_SANS_JP.read_bytes())
    table_count = struct.unpack_from(">H", data, 4)[0]
    for index in range(table_count):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        if tag == b"OS/2":
            struct.pack_into(">H", data, offset + 4, 700)
    return pylopdf.Font(fontbuffer=bytes(data))


def _new_page(width: float = 300, height: float = 200) -> tuple[pylopdf.Document, pylopdf.Page]:
    doc = pylopdf.Document()
    return doc, doc.new_page(width=width, height=height)


def _pixel(page: pylopdf.Page, x: float, y: float) -> tuple[int, int, int]:
    pix = page.get_pixmap(background=WHITE)
    offset = int(y) * pix.stride + int(x) * 4
    red, green, blue = pix.samples[offset : offset + 3]
    return red, green, blue


def _font_files(doc: pylopdf.Document) -> int:
    return len(re.findall(rb"/FontFile[23]? ", doc.tobytes()))


def _rounded(values: tuple[float, ...] | None) -> tuple[float, ...] | None:
    return None if values is None else tuple(round(value, 4) for value in values)


# End points and bounds that pymupdf 1.28.2 returns for the same calls.
# draw_circle and draw_sector leave pymupdf's Shape.rect unchanged.
PYMUPDF_DRAWING = [
    ("draw_line", ((10, 20), (200, 120)), {}, (200, 120), (10, 20, 200, 120)),
    ("draw_polyline", ([(10, 20), (50, 80), (120, 30), (160, 90)],), {}, (160, 90), (10, 20, 160, 90)),
    ("draw_bezier", ((10, 20), (40, 200), (180, 0), (220, 150)), {}, (220, 150), (10, 0, 220, 200)),
    (
        "draw_curve",
        ((10, 20), (100, 200), (220, 30)),
        {},
        (220, 30),
        (10, 20, 220, 30 + (200 - 30) * 0.55228474983),
    ),
    ("draw_oval", ((20, 30, 220, 130),), {}, (20, 80), (20, 30, 220, 130)),
    ("draw_oval", (((20, 30), (220, 40), (10, 130), (230, 150)),), {}, (15, 80), (10, 30, 230, 150)),
    ("draw_circle", ((150, 100), 30), {}, (120, 99.99999999999999), None),
    ("draw_sector", ((150, 100), (200, 100), 90), {}, (150, 50), None),
    (
        "draw_sector",
        ((150, 100), (200, 130), -135),
        {"fullSector": False},
        (93.4314575050762, 114.14213562373097),
        None,
    ),
    ("draw_rect", ((20, 30, 220, 130),), {}, (20, 30), (20, 30, 220, 130)),
    ("draw_rect", ((20, 30, 220, 130),), {"radius": 0.2}, (20, 50), (20, 30, 220, 130)),
    ("draw_rect", ((20, 30, 220, 130),), {"radius": (0.1, 0.3)}, (20, 60), (20, 30, 220, 130)),
    ("draw_quad", (((20, 30), (220, 40), (10, 130), (230, 150)),), {}, (20, 30), (10, 30, 230, 150)),
    ("draw_zigzag", ((10, 20), (200, 120)), {"breadth": 5}, (200, 120), None),
    ("draw_squiggle", ((10, 20), (200, 120)), {"breadth": 4}, (200, 120), None),
]


@pytest.mark.parametrize(("method", "args", "kwargs", "end", "rect"), PYMUPDF_DRAWING)
def test_draw_methods_return_pymupdfs_end_points_and_bounds(
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    end: tuple[float, float],
    rect: tuple[float, float, float, float] | None,
) -> None:
    _, page = _new_page()
    shape = page.new_shape()
    point = getattr(shape, method)(*args, **kwargs)
    assert isinstance(point, pylopdf.Point)
    assert point == pytest.approx(end, abs=1e-9)
    assert shape.last_point == point
    if method in ("draw_circle", "draw_sector"):
        assert shape.rect is None
    elif rect is not None:
        assert shape.rect == pytest.approx(rect, abs=1e-9)
    shape.finish()
    shape.commit()
    assert len(page.get_drawings()) == 1


def test_zigzag_and_squiggle_bounds_match_pymupdf_within_float_precision() -> None:
    # pymupdf maps the turning points through MuPDF's single-precision matrices.
    _, page = _new_page()
    shape = page.new_shape()
    shape.draw_zigzag((10, 20), (200, 120), breadth=5)
    assert shape.rect == pytest.approx((10, 17.954545974731445, 200, 122.04544830322266), abs=1e-5)
    shape.finish()
    shape.draw_squiggle((10, 20), (200, 120), breadth=4)
    assert shape.rect == pytest.approx((10, 16.19029094321455, 200, 123.8097080033859), abs=1e-5)
    shape.finish()
    shape.commit()


def test_horizontal_angle_resolves_the_quadrant() -> None:
    # pymupdf 1.28.2 returns these angles.
    assert pylopdf.Shape.horizontal_angle((1, 2), (-3, 5)) == pytest.approx(2.498091544796509)
    assert pylopdf.Shape.horizontal_angle((0, 0), (1, 0)) == 0
    assert pylopdf.Shape.horizontal_angle((0, 0), (0, -1)) == pytest.approx(-1.5707963267948966)
    assert pylopdf.Shape.horizontal_angle((0, 0), (-1, -1)) == pytest.approx(-2.356194490192345)


def test_connected_segments_continue_one_subpath() -> None:
    _, page = _new_page()
    shape = page.new_shape()
    end = shape.draw_line((10, 10), (100, 10))
    shape.draw_line(end, (100, 100))
    shape.draw_line((150, 150), (200, 150))
    shape.finish(closePath=False)
    shape.commit()
    (drawing,) = page.get_drawings()
    assert [item[0] for item in drawing["items"]] == ["l", "l", "l"]
    first, second, third = drawing["items"]
    assert first[2] == second[1]
    assert third[1] != second[2]
    assert drawing["rect"] == pytest.approx((10, 10, 200, 150))


def test_finish_applies_pymupdfs_styles() -> None:
    _, page = _new_page()
    shape = page.new_shape()
    points = [(10, 20), (50, 80), (120, 30), (160, 90)]
    shape.draw_polyline(points)
    shape.finish(color=(0.5,), width=3, lineCap=1, lineJoin=2, closePath=False)
    shape.draw_polyline(points)
    shape.finish(color=(0, 1, 0, 0), fill=(0, 0, 0, 0.2), width=2, lineJoin=1)
    shape.draw_polyline(points)
    shape.finish(color=(1, 0, 0), width=2, dashes="[3 4] 1", stroke_opacity=0.5)
    shape.draw_polyline(points)
    shape.finish(color=None, fill=(1, 1, 0), fill_opacity=0.25, even_odd=True)
    shape.commit()
    gray, cmyk, dashed, filled = page.get_drawings()
    cmyk_color, cmyk_fill = cmyk["color"], cmyk["fill"]
    assert cmyk_color is not None
    assert cmyk_fill is not None
    assert gray["type"] == "s"
    assert gray["color"] == pytest.approx((0.502, 0.502, 0.502), abs=1e-3)
    assert (gray["width"], gray["lineCap"], gray["lineJoin"], gray["closePath"]) == (3, (1, 1, 1), 2, False)
    assert cmyk["type"] == "fs"
    # The renderer converts CMYK magenta and a 20 % black to RGB.
    assert cmyk_color[0] > 0.8 > cmyk_color[2] > cmyk_color[1] == 0
    assert min(cmyk_fill) > 0.7
    assert max(cmyk_fill) - min(cmyk_fill) < 0.03
    assert (cmyk["width"], cmyk["lineJoin"], cmyk["closePath"]) == (2, 1, True)
    assert dashed["dashes"] == "[3 4] 1"
    assert dashed["stroke_opacity"] == pytest.approx(0.5, abs=1e-2)
    assert filled["type"] == "f"
    assert filled["even_odd"] is True
    assert filled["fill"] == (1, 1, 0)
    assert filled["fill_opacity"] == pytest.approx(0.25, abs=1e-2)


def test_finish_without_drawing_is_a_no_op_and_refuses_styles_that_draw_nothing() -> None:
    _, page = _new_page()
    shape = page.new_shape()
    shape.finish(color=None)
    shape.draw_line((10, 10), (100, 100))
    with pytest.raises(pylopdf.PdfError, match="draws nothing"):
        shape.finish(color=None)
    with pytest.raises(pylopdf.PdfError, match="draws nothing"):
        shape.finish(width=0)
    shape.finish(width=0, fill=(0, 0, 1))
    shape.commit()
    assert page.get_drawings()[0]["type"] == "f"


def test_commit_refuses_drawing_that_finish_did_not_style() -> None:
    doc, page = _new_page()
    before = doc.tobytes()
    shape = page.new_shape()
    shape.draw_rect((10, 10, 50, 50))
    shape.insert_text((10, 80), "text")
    with pytest.raises(pylopdf.PdfError, match=r"finish\(\)"):
        shape.commit()
    assert doc.tobytes() == before
    shape.finish(color=(1, 0, 0))
    shape.commit()
    assert page.get_text().strip() == "text"


def test_text_is_drawn_above_the_paths_of_its_shape(regular: pylopdf.Font) -> None:
    _, page = _new_page()
    shape = page.new_shape()
    # Text inserted first still covers the rectangle finished after it.
    shape.insert_text((50, 110), "■", font=regular, fontsize=60, color=(1, 0, 0))
    shape.draw_rect((40, 40, 130, 130))
    shape.finish(color=None, fill=(0, 0, 1))
    shape.commit()
    assert _pixel(page, 80, 80) == (255, 0, 0)
    assert _pixel(page, 125, 45) == (0, 0, 255)


def test_text_keeps_insertion_order_across_standard_14_and_embedded_fonts(regular: pylopdf.Font) -> None:
    _, page = _new_page()
    shape = page.new_shape()
    # ZapfDingbats "n" and the embedded "■" both fill the same square.
    shape.insert_text((50, 110), "■", font=regular, fontsize=60, color=(1, 0, 0))
    shape.insert_text((50, 110), "n", fontname="zadb", fontsize=60, color=(0, 1, 0))
    shape.insert_text((150, 110), "n", fontname="zadb", fontsize=60, color=(0, 1, 0))
    shape.insert_text((150, 110), "■", font=regular, fontsize=60, color=(1, 0, 0))
    shape.draw_line((0, 150), (300, 150))
    shape.finish(color=(0, 0, 0))
    shape.commit()
    assert _pixel(page, 75, 85) == (0, 255, 0)
    assert _pixel(page, 175, 85) == (255, 0, 0)
    assert _pixel(page, 150, 150) == (0, 0, 0)


def test_one_commit_embeds_each_font_once(regular: pylopdf.Font) -> None:
    doc, page = _new_page(400, 300)
    with page.new_shape() as shape:
        for row in range(6):
            shape.insert_text((20, 30 + 20 * row), f"行 {row} の本文", font=regular, fontsize=12)
            shape.insert_text((250, 30 + 20 * row), f"Standard {row}", fontsize=10)
        shape.insert_textbox((20, 160, 380, 280), "折り返す日本語の文章です。" * 5, font=regular, fontsize=10)
    assert _font_files(doc) == 1
    words = [word[4] for word in page.get_text("words")]
    assert "Standard" in words
    assert "の本文" in words

    separate, separate_page = _new_page(400, 300)
    for row in range(3):
        separate_page.insert_text((20, 30 + 20 * row), f"行 {row}", font=regular, fontsize=12)
    assert _font_files(separate) == 3


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_shapes_use_display_coordinates_on_rotated_pages(rotation: int, regular: pylopdf.Font) -> None:
    _, page = _new_page(400, 300)
    page.set_rotation(rotation)
    width, height = page.rect.width, page.rect.height
    with page.new_shape() as shape:
        shape.draw_rect((20, 30, 120, 80))
        shape.finish(color=(0, 0, 1), width=2)
        shape.insert_text((40, 60), "見出し", font=regular, fontsize=16)
        shape.insert_text((40, 120), "Standard", fontsize=12)
    (drawing,) = page.get_drawings()
    assert drawing["rect"] == pytest.approx((20, 30, 120, 80), abs=0.01)
    words = {word[4]: word for word in page.get_text("words")}
    assert words["見出し"][0] == pytest.approx(40, abs=0.5)
    assert words["Standard"][0] == pytest.approx(40, abs=0.5)
    assert words["見出し"][1] < 60 < words["見出し"][3]
    assert words["Standard"][1] < 120 < words["Standard"][3]
    assert (width, height) == ((400, 300) if rotation in (0, 180) else (300, 400))


def test_overlay_false_draws_below_existing_content() -> None:
    _, page = _new_page()
    page.draw_rect((40, 40, 100, 100), color=None, fill=(0, 0, 1))
    shape = page.new_shape()
    shape.draw_rect((70, 70, 130, 130))
    shape.finish(color=None, fill=(1, 0, 0))
    shape.commit(False)
    assert _pixel(page, 85, 85) == (0, 0, 255)
    assert _pixel(page, 120, 120) == (255, 0, 0)


def _draw_then_fail(page: pylopdf.Page) -> None:
    with page.new_shape() as shape:
        shape.draw_circle((100, 100), 20)
        shape.finish(fill=(1, 0, 0))
        msg = "stop"
        raise RuntimeError(msg)


def test_a_context_manager_commits_or_draws_nothing_after_an_exception() -> None:
    doc, page = _new_page()
    with page.new_shape() as shape:
        shape.draw_circle((100, 100), 20)
        shape.finish(fill=(0, 1, 0))
    assert len(page.get_drawings()) == 1

    before = doc.tobytes()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError, match="stop"):
            _draw_then_fail(page)
        gc.collect()
    assert doc.tobytes() == before
    assert not [warning for warning in caught if issubclass(warning.category, ResourceWarning)]


def test_an_uncommitted_shape_warns_when_it_is_collected() -> None:
    _, page = _new_page()
    shape = page.new_shape()
    shape.draw_line((10, 10), (20, 20))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del shape
        gc.collect()
    assert [str(warning.message) for warning in caught if issubclass(warning.category, ResourceWarning)] == [
        "a Shape was discarded with drawing that commit() never wrote"
    ]

    committed = page.new_shape()
    committed.draw_line((10, 10), (20, 20))
    committed.finish()
    committed.commit()
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        del committed
        gc.collect()


def test_a_shape_can_commit_again_after_it_is_emptied() -> None:
    _, page = _new_page()
    shape = pylopdf.Shape(page)
    assert (shape.page, shape.doc) == (page, page.parent)
    shape.draw_line((10, 10), (100, 10))
    shape.finish()
    shape.commit()
    assert (shape.last_point, shape.rect) == (None, None)
    shape.commit()
    shape.draw_line((10, 50), (100, 50))
    shape.finish()
    shape.commit()
    assert len(page.get_drawings()) == 2


def test_a_shape_on_a_stale_page_cannot_commit() -> None:
    doc, page = _new_page()
    shape = page.new_shape()
    shape.draw_line((10, 10), (100, 10))
    shape.finish()
    doc.new_page()
    with pytest.raises(pylopdf.StalePageError):
        shape.commit()
    shape._discard()  # noqa: SLF001 - drops drawing a refusal left behind.


@pytest.mark.parametrize("fontname", ["helv", "tiro", "cobo"])
def test_insert_text_places_lines_as_page_insert_text_does(fontname: str, regular: pylopdf.Font) -> None:
    _, expected = _new_page()
    expected.insert_text((20, 40), "First line\nSecond", fontname=fontname, fontsize=14, color=(0, 0, 1))
    expected.insert_text((20, 120), "日本語\n二行目", font=regular, fontsize=14)
    _, page = _new_page()
    shape = page.new_shape()
    assert shape.insert_text((20, 40), "First line\nSecond", fontname=fontname, fontsize=14, color=(0, 0, 1)) == 2
    assert shape.insert_text((20, 120), ["日本語", "二行目"], font=regular, fontsize=14) == 2
    assert shape.insert_text((20, 180), "") == 0
    shape.commit()
    assert page.get_text("words") == expected.get_text("words")


def test_insert_textbox_wraps_as_page_insert_textbox_does(regular: pylopdf.Font) -> None:
    text = "Wrapped text for a box. " * 4
    _, expected = _new_page()
    expected_spare = expected.insert_textbox((20, 20, 200, 120), text, fontsize=11, align=1, expandtabs=1)
    expected_jp = expected.insert_textbox((20, 120, 200, 190), "折り返す文章です。" * 3, font=regular, fontsize=10)
    _, page = _new_page()
    shape = page.new_shape()
    assert shape.insert_textbox((20, 20, 200, 120), text, fontsize=11, align=1) == pytest.approx(expected_spare)
    assert shape.insert_textbox((20, 120, 200, 190), "折り返す文章です。" * 3, font=regular, fontsize=10) == (
        pytest.approx(expected_jp)
    )
    shape.commit()
    assert page.get_text("words") == expected.get_text("words")


def test_an_overflowing_textbox_adds_nothing() -> None:
    doc, page = _new_page()
    before = doc.tobytes()
    shape = page.new_shape()
    assert shape.insert_textbox((20, 20, 60, 30), "Too much text " * 20) < 0
    assert shape.insert_textbox((20, 20, 60, 30), "") == 10
    shape.commit()
    assert doc.tobytes() == before


def test_text_colours_may_be_gray_rgb_or_cmyk(regular: pylopdf.Font) -> None:
    _, page = _new_page()
    with page.new_shape() as shape:
        shape.insert_text((20, 80), "■", font=regular, fontsize=60, color=(0.5,))
        shape.insert_text((100, 80), "n", fontname="zadb", fontsize=60, color=(0, 1, 1, 0))
        shape.insert_text((180, 80), "■", font=regular, fontsize=60, color=1)
    assert _pixel(page, 45, 55) == (128, 128, 128)
    assert _pixel(page, 125, 55)[0] > 200 > max(_pixel(page, 125, 55)[1:])
    assert _pixel(page, 205, 55) == WHITE


def test_measured_width_equals_the_drawn_width(regular: pylopdf.Font) -> None:
    _, page = _new_page(400, 200)
    text = "AVATAR 見出し、本文。"
    with page.new_shape() as shape:
        shape.insert_text((20, 100), text, font=regular, fontsize=18)
    chars = [
        char
        for block in page.get_text("rawdict")["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]
    assert chars[-1]["bbox"][2] - 20 == pytest.approx(regular.text_length(text, 18), abs=0.01)


def test_page_drawing_methods_draw_one_finished_path_each() -> None:
    _, page = _new_page()
    assert page.draw_line((10, 10), (100, 10), color=(1, 0, 0), width=2, dashes="[2 2] 0") == (100, 10)
    assert page.draw_polyline([(10, 20), (60, 40), (110, 20)], closePath=True, fill=(0, 1, 0)) == (110, 20)
    assert page.draw_bezier((10, 50), (30, 90), (60, 10), (90, 50)) == (90, 50)
    assert page.draw_curve((100, 50), (130, 90), (160, 50)) == (160, 50)
    assert page.draw_rect((10, 100, 60, 140), radius=0.25, fill=(0, 0, 1)) == (10, 110)
    assert page.draw_quad(((70, 100), (120, 100), (70, 140), (120, 140))) == (70, 100)
    assert page.draw_circle((160, 120), 20, fill=(1, 1, 0)) == pytest.approx((140, 120))
    assert page.draw_oval((190, 100, 260, 140)) == (190, 120)
    assert page.draw_sector((60, 180), (90, 180), 90, fill=(1, 0, 1)) == pytest.approx((60, 150))
    assert page.draw_squiggle((100, 180), (200, 180), breadth=3) == (200, 180)
    assert page.draw_zigzag((200, 180), (290, 180), breadth=3) == (290, 180)
    drawings = page.get_drawings()
    assert len(drawings) == 11
    assert drawings[0]["color"] == (1, 0, 0)
    assert drawings[2]["color"] == (0, 0, 0)
    assert drawings[2]["width"] == 1


def test_page_drawing_options_are_keyword_only_and_omit_morph_and_oc() -> None:
    _, page = _new_page()
    with pytest.raises(TypeError):
        page.draw_line((10, 10), (20, 20), (1, 0, 0))  # ty: ignore[too-many-positional-arguments]
    with pytest.raises(TypeError):
        page.draw_rect((10, 10, 20, 20), morph=None)  # ty: ignore[unknown-argument]
    with pytest.raises(TypeError):
        page.draw_circle((10, 10), 5, oc=0)  # ty: ignore[unknown-argument]
    shape = page.new_shape()
    shape.draw_line((10, 10), (20, 20))
    with pytest.raises(TypeError):
        shape.finish(1)  # ty: ignore[too-many-positional-arguments]
    with pytest.raises(TypeError):
        shape.finish(morph=None)  # ty: ignore[unknown-argument]
    with pytest.raises(TypeError):
        shape.insert_text((10, 10), "x", fill=(0, 0, 0))  # ty: ignore[unknown-argument]
    shape._discard()  # noqa: SLF001 - drops drawing a refusal left behind.
    assert page.get_drawings() == []


@pytest.mark.parametrize(
    ("finish", "message"),
    [
        ({"color": (1, 0)}, "colour"),
        ({"color": (2, 0, 0)}, "colour"),
        ({"fill": "red"}, "colour"),
        ({"width": -1}, "width"),
        ({"lineCap": 3}, "lineCap"),
        ({"lineJoin": True}, "lineJoin"),
        ({"dashes": "3 4"}, "dash"),
        ({"dashes": "[0 0] 0"}, "dash"),
        ({"dashes": "[-1 2] 0"}, "dash"),
        ({"stroke_opacity": 1.5}, "stroke_opacity"),
        ({"fill": (1, 0, 0), "fill_opacity": -0.1}, "fill_opacity"),
    ],
)
def test_finish_rejects_unsupported_values(finish: dict[str, object], message: str) -> None:
    _, page = _new_page()
    shape = page.new_shape()
    shape.draw_line((10, 10), (20, 20))
    with pytest.raises(pylopdf.PdfError, match=message):
        shape.finish(**finish)  # ty: ignore[invalid-argument-type]
    shape._discard()  # noqa: SLF001 - drops drawing a refusal left behind.


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "message"),
    [
        ("draw_line", ((10, 10), (20,)), {}, "p2"),
        ("draw_line", ((10, float("nan")), (20, 20)), {}, "p1"),
        ("draw_polyline", ([(10, 10)],), {}, "at least two"),
        ("draw_polyline", (5,), {}, "iterable"),
        ("draw_circle", ((10, 10), 0), {}, "radius"),
        ("draw_sector", ((10, 10), (10, 10), 90), {}, "radius"),
        ("draw_rect", ((10, 10, 20, 20),), {"radius": 0.6}, "radius"),
        ("draw_rect", ((10, 10, 20, 20),), {"radius": (0.2,)}, "radius"),
        ("draw_oval", ((10, 10, 20),), {}, "rectangle or four points"),
        ("draw_zigzag", ((10, 10), (12, 10)), {}, "too close"),
        ("draw_squiggle", ((10, 10), (100, 10)), {"breadth": 0}, "breadth"),
    ],
)
def test_draw_methods_reject_unsupported_values(
    method: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    message: str,
) -> None:
    _, page = _new_page()
    shape = page.new_shape()
    with pytest.raises(pylopdf.PdfError, match=message):
        getattr(shape, method)(*args, **kwargs)
    assert shape.last_point is None


def test_text_input_follows_page_insert_text_rules(regular: pylopdf.Font) -> None:
    _, page = _new_page()
    shape = page.new_shape()
    with pytest.raises(ValueError, match="WinAnsi"):
        shape.insert_text((10, 10), "日本語", font=pylopdf.Font("helv"))
    with pytest.raises(ValueError, match="font cannot be combined"):
        shape.insert_text((10, 10), "x", font=regular, fontindex=1)
    with pytest.raises(pylopdf.LimitError) as error:
        shape.insert_text((10, 10), "x" * 11, max_text_size=10)
    assert error.value.code == "text_input_size"
    with pytest.raises(pylopdf.PdfError, match="fontsize"):
        shape.insert_text((10, 10), "x", fontsize=0)
    with pytest.raises(TypeError):
        shape.insert_text((10, 10), 5)  # ty: ignore[invalid-argument-type]
    assert shape.insert_text((10, 10), "ok", max_text_size=None) == 1
    shape._discard()  # noqa: SLF001 - drops drawing a refusal left behind.


def test_installed_fonts_are_selected_for_unicode_text() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    doc, page = _new_page()
    with page.new_shape() as shape:
        shape.insert_text((20, 50), "日本語の見出し", fontsize=14)
        shape.insert_text((20, 80), "二行目の本文", fontsize=12)
    assert [word[4] for word in page.get_text("words")] == ["日本語の見出し", "二行目の本文"]
    assert _font_files(doc) == 1


def test_path_points_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    _, page = _new_page()
    monkeypatch.setattr(pylopdf, "_MAX_SHAPE_PATH_POINTS", 8)
    shape = page.new_shape()
    shape.draw_polyline([(i, i) for i in range(8)])
    with pytest.raises(pylopdf.LimitError) as error:
        shape.draw_line((0, 0), (1, 1))
    assert error.value.code == "path_point_count"
    shape._discard()  # noqa: SLF001 - drops drawing a refusal left behind.
    with pytest.raises(pylopdf.LimitError):
        shape.draw_polyline((i, i) for i in range(10))

    # The core repeats the bound for direct calls.
    record = pylopdf_core._Shape()  # noqa: SLF001 - the core boundary.
    points = 1_048_577
    with pytest.raises(pylopdf.LimitError) as core_error:
        record.add_path("m" + "l" * (points - 1), [0.0] * (2 * points), False, None, ([0.0], 1.0, 0, 0, None, 1.0))
    assert core_error.value.code == "path_point_count"
    with pytest.raises(pylopdf.PdfError, match="do not match"):
        record.add_path("ml", [0.0, 0.0, 1.0], False, None, ([0.0], 1.0, 0, 0, None, 1.0))
    with pytest.raises(pylopdf.PdfError, match="fill, or a stroke"):
        record.add_path("ml", [0.0, 0.0, 1.0, 1.0], False, None, None)


def test_site_survey_report_pages(regular: pylopdf.Font, bold: pylopdf.Font) -> None:
    """Build report pages the way the site-survey tool draws them."""
    doc = pylopdf.Document()
    accent = (0.83, 0.26, 0.05)
    for number in range(2):
        page = doc.new_page(width=A4[0], height=A4[1])
        with page.new_shape() as shape:
            shape.draw_rect((50, 50, 545, 792))
            shape.finish(color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
            shape.draw_line((56, 90), (540, 90))
            shape.finish(color=(0, 0, 0), width=1.6)
            shape.draw_circle((80, 140), 7)
            shape.finish(color=(1, 1, 1), fill=accent, width=1.2)
            shape.draw_polyline([(520, 130), (535, 140), (520, 150)])
            shape.finish(color=None, fill=accent)
            title = f"現地調査報告書 {number + 1}"
            shape.insert_text((56, 80), title, font=bold, fontsize=18)
            end = 56 + bold.text_length("項目", 12)
            shape.insert_text((56, 120), "項目", font=bold, fontsize=12)
            shape.insert_text((end, 120), "：外壁のひび割れ", font=regular, fontsize=12)
            spare = shape.insert_textbox(
                (56, 160, 540, 400),
                "調査の結果、外壁の北面に幅0.3mmのひび割れを確認した。" * 4,
                font=regular,
                fontsize=10.5,
            )
            assert spare > 0
        chars = [
            char
            for block in page.get_text("rawdict")["blocks"]
            for line in block["lines"]
            for span in line["spans"]
            for char in span["chars"]
        ]
        colon = next(char for char in chars if char["c"] == "：")
        # The value starts where the measured label ends.
        assert colon["origin"][0] == pytest.approx(end, abs=0.01)
        assert len(page.get_drawings()) == 4

    # Each commit embeds one subset of each face it uses.
    assert _font_files(doc) == 4

    # A survey record page: marks drawn after a photo lie on top of it.
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image((56, 420, 300, 600), stream=PHOTO_JPEG)
    with page.new_shape() as shape:
        shape.draw_rect((120, 470, 200, 540))
        shape.finish(color=(1, 0, 0), width=3)
        shape.draw_circle((90, 450), 12)
        shape.finish(color=(1, 1, 1), fill=accent)
        shape.insert_text((84, 455), "1", fontname="hebo", fontsize=12, color=(1, 1, 1))
        shape.draw_line((210, 560), (290, 590))
        shape.finish(color=(1, 0, 0), width=2)
    assert _pixel(page, 120, 505) == (255, 0, 0)
    assert _pixel(page, 98, 445) == (212, 66, 13)
    # Inside the stroked frame the photo still shows.
    red, green, blue = _pixel(page, 160, 505)
    assert red == green == blue < 250
    saved = pylopdf.open(stream=doc.tobytes())
    assert saved.page_count == 3
    assert "現地調査報告書" in saved[0].get_text()
