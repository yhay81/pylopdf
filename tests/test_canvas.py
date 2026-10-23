"""The canvas: drawing in paint order, read back through the content model."""

from __future__ import annotations

import base64
import gc
import math
import re
import struct
import warnings
from pathlib import Path
from typing import Any

import pytest

import pylopdf
from pylopdf import (
    BlendMode,
    ClosePath,
    Color,
    CurveTo,
    FillRule,
    LineCap,
    LineJoin,
    LineTo,
    Matrix,
    MoveTo,
    PathBuilder,
    Stroke,
    TextFit,
    TextRenderMode,
)

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
RED = Color(1.0, 0.0, 0.0)
BLUE = Color(0.0, 0.0, 1.0)
BLACK = Color(0.0, 0.0, 0.0)
WHITE = Color(1.0, 1.0, 1.0)
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
    return pylopdf.Font.from_file(NOTO_SANS_JP)


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
    return pylopdf.Font.from_bytes(bytes(data))


def _new_page(width: float = 300, height: float = 200) -> tuple[pylopdf.Document, pylopdf.Page]:
    doc = pylopdf.Document()
    return doc, doc.pages.new((width, height))


def _pixel(page: pylopdf.Page, x: float, y: float) -> tuple[int, int, int]:
    pixmap = page.pixmap()
    offset = int(y) * pixmap.stride + int(x) * 3
    samples = bytes(pixmap)
    return samples[offset], samples[offset + 1], samples[offset + 2]


def _assert_color(paint: object, expected: object) -> None:
    assert isinstance(paint, Color)
    assert isinstance(expected, Color)
    for actual, wanted in zip(
        (paint.red, paint.green, paint.blue, paint.alpha),
        (expected.red, expected.green, expected.blue, expected.alpha),
        strict=True,
    ):
        assert actual == pytest.approx(wanted, abs=1 / 255)


def _assert_rect(actual: pylopdf.Rect, expected: tuple[float, float, float, float], tolerance: float = 0.01) -> None:
    assert tuple(actual) == pytest.approx(expected, abs=tolerance)


def _items(page: pylopdf.Page) -> list[Any]:
    return list(page.content())


def _font_files(doc: pylopdf.Document) -> int:
    return len(re.findall(rb"/FontFile[23]? ", doc.to_bytes()))


def test_shapes_draw_paths_in_display_space() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.rect((10, 20, 110, 70), fill=RED)
        canvas.circle((200, 100), 30, stroke=BLUE)
        canvas.ellipse((20, 120, 80, 150), fill=BLUE, stroke=RED)
        canvas.polygon([(150, 150), (200, 190), (100, 190)], fill=BLACK)
        canvas.line((0, 0), (300, 200))
        canvas.bezier((0, 200), (100, 100), (200, 100), (300, 200))
    rect, circle, ellipse, triangle, line, curve = _items(page)
    assert isinstance(rect, pylopdf.Path)
    _assert_rect(rect.bbox, (10, 20, 110, 70))
    _assert_color(rect.fill, RED)
    assert rect.stroke is None
    _assert_rect(circle.bbox, (170, 70, 230, 130), 0.05)
    assert circle.fill is None
    _assert_color(circle.stroke.paint, BLUE)
    _assert_color(ellipse.fill, BLUE)
    _assert_color(ellipse.stroke.paint, RED)
    _assert_rect(triangle.bbox, (100, 150, 200, 190))
    # A line or curve without a stroke is drawn with a 1-point black line.
    for item in (line, curve):
        assert item.fill is None
        _assert_color(item.stroke.paint, BLACK)
        assert item.stroke.width == pytest.approx(1)
    assert _pixel(page, 60, 45) == (255, 0, 0)


def test_a_path_without_fill_or_stroke_is_stroked_black() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.rect((10, 10, 50, 50))
    (item,) = _items(page)
    assert item.fill is None
    _assert_color(item.stroke.paint, BLACK)


def test_stroke_style_and_alpha_round_trip() -> None:
    _doc, page = _new_page()
    stroke = Stroke(
        paint=Color(0.0, 0.5, 0.0, 0.25),
        width=4,
        cap=LineCap.ROUND,
        join=LineJoin.BEVEL,
        miter_limit=4,
        dashes=(6, 2),
        dash_offset=1,
    )
    with page.draw() as canvas:
        canvas.path(
            PathBuilder().move_to((20, 20)).line_to((120, 20)).line_to((120, 120)),
            fill=Color(1, 0, 0, 0.5),
            stroke=stroke,
            fill_rule=FillRule.EVEN_ODD,
        )
    (item,) = _items(page)
    _assert_color(item.fill, Color(1, 0, 0, 0.5))
    assert item.fill_rule is FillRule.EVEN_ODD
    _assert_color(item.stroke.paint, Color(0.0, 0.5, 0.0, 0.25))
    assert item.stroke.width == pytest.approx(4)
    assert item.stroke.cap is LineCap.ROUND
    assert item.stroke.join is LineJoin.BEVEL
    assert item.stroke.miter_limit == pytest.approx(4)
    assert item.stroke.dashes == pytest.approx((6, 2))
    assert item.stroke.dash_offset == pytest.approx(1)


def test_drawing_keeps_paint_order() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.rect((10, 10, 60, 60), fill=RED)
        canvas.text("first", (10, 90))
        canvas.rect((70, 10, 120, 60), fill=BLUE)
        canvas.text("second", (10, 120), font=pylopdf.Font.from_file(NOTO_SANS_JP))
        canvas.rect((130, 10, 180, 60), fill=BLACK)
    kinds = [type(item).__name__ for item in _items(page)]
    assert kinds == ["Path", "TextRun", "Path", "TextRun", "Path"]
    assert page.text() == "first\nsecond\n"


def test_text_in_standard_and_embedded_fonts() -> None:
    _doc, page = _new_page(400, 200)
    font = pylopdf.Font.from_file(NOTO_SANS_JP)
    with page.draw() as canvas:
        canvas.text("Helvetica text", (20, 40), size=12, fill=RED)
        canvas.text("日本語の文章", (20, 80), font=font, size=16, fill=BLUE)
        canvas.text("two\nlines", (20, 120), size=10, line_height=2)
    runs = page.content().text_runs
    assert "NotoSansJP-Regular" in runs[1].font.name
    _assert_color(runs[0].fill, RED)
    _assert_color(runs[1].fill, BLUE)
    assert runs[0].glyphs[0].origin.y == pytest.approx(40, abs=0.01)
    assert runs[1].glyphs[0].origin.y == pytest.approx(80, abs=0.01)
    assert runs[1].glyphs[0].size == pytest.approx(16, abs=0.01)
    two, lines = runs[2], runs[3]
    assert lines.glyphs[0].origin.y - two.glyphs[0].origin.y == pytest.approx(20, abs=0.01)
    assert page.text() == "Helvetica text\n日本語の文章\ntwo\nlines\n"


def test_non_winansi_text_without_a_font_uses_an_installed_extra() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.text("こんにちは", (20, 40))
    assert page.text() == "こんにちは\n"


@pytest.mark.parametrize("embedded", [False, True])
def test_text_can_be_stroked(embedded: bool) -> None:  # noqa: FBT001 - parametrized.
    _doc, page = _new_page()
    font = pylopdf.Font.from_file(NOTO_SANS_JP) if embedded else None
    with page.draw() as canvas:
        canvas.text("Outline", (20, 40), font=font, size=20, fill=None, stroke=Stroke(paint=RED, width=0.5))
        canvas.text("Both", (20, 80), font=font, size=20, fill=BLUE, stroke=RED)
    outline, both = page.content().text_runs
    assert outline.stroke is not None
    assert both.stroke is not None
    assert outline.render_mode is TextRenderMode.STROKE
    assert outline.fill is None
    _assert_color(outline.stroke.paint, RED)
    assert outline.stroke.width == pytest.approx(0.5)
    assert both.render_mode is TextRenderMode.FILL_STROKE
    _assert_color(both.fill, BLUE)
    _assert_color(both.stroke.paint, RED)


@pytest.mark.parametrize("embedded", [False, True])
def test_translucent_text(embedded: bool) -> None:  # noqa: FBT001 - parametrized.
    _doc, page = _new_page()
    font = pylopdf.Font.from_file(NOTO_SANS_JP) if embedded else None
    with page.draw() as canvas:
        canvas.text("faint", (20, 40), font=font, fill=Color(0, 0, 0, 0.5))
    (run,) = page.content().text_runs
    _assert_color(run.fill, Color(0, 0, 0, 0.5))


def test_text_needs_a_fill_or_a_stroke() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas, pytest.raises(ValueError, match="fill or a stroke"):
        canvas.text("nothing", (10, 10), fill=None)


def test_standard_14_text_must_be_winansi() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas, pytest.raises(ValueError, match="WinAnsi"):
        canvas.text("日本", (10, 10), font=pylopdf.Font.standard("Helvetica"))


@pytest.mark.parametrize("embedded", [False, True])
def test_textbox_fits_or_draws_nothing(embedded: bool) -> None:  # noqa: FBT001 - parametrized.
    _doc, page = _new_page()
    font = pylopdf.Font.from_file(NOTO_SANS_JP) if embedded else None
    with page.draw() as canvas:
        fit = canvas.textbox((10, 10, 150, 100), "A short paragraph that wraps onto lines.", font=font, size=10)
        overflow = canvas.textbox((10, 120, 60, 130), "Far too much text for this box", font=font, size=10)
        empty = canvas.textbox((10, 140, 60, 160), "", font=font)
    assert isinstance(fit, TextFit)
    assert fit.fits
    assert fit.spare_height > 0
    assert not overflow.fits
    assert overflow.spare_height < 0
    assert empty == TextFit(spare_height=20)
    text = page.text()
    assert "A short paragraph" in text
    assert "Far too much" not in text
    assert all(run.bbox.y1 <= 100 for run in page.content().text_runs)


def test_textbox_alignment() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.textbox((0, 10, 300, 40), "left", align="left")
        canvas.textbox((0, 50, 300, 80), "center", align="center")
        canvas.textbox((0, 90, 300, 120), "right", align="right")
    left, center, right = page.content().text_runs
    assert left.bbox.x0 < 5
    assert abs((center.bbox.x0 + center.bbox.x1) / 2 - 150) < 2
    assert 295 < right.bbox.x1 <= 300
    with page.draw() as canvas, pytest.raises(ValueError, match="align"):
        canvas.textbox((0, 0, 100, 100), "x", align="middle")  # ty: ignore[invalid-argument-type]


_TURNS = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}


def _direction(glyph: pylopdf.Glyph) -> tuple[float, float]:
    """Return the unit baseline direction of a glyph in display space."""
    a, b = glyph.matrix.a, glyph.matrix.b
    length = math.hypot(a, b)
    return a / length, b / length


def _ink(page: pylopdf.Page) -> tuple[int, int, int, int]:
    """Return the pixel box of everything darker than white."""
    pixmap = page.pixmap()
    data = bytes(pixmap)
    xs: list[int] = []
    ys: list[int] = []
    for y in range(pixmap.height):
        row = data[y * pixmap.stride : y * pixmap.stride + pixmap.width * 3]
        dark = [x for x in range(pixmap.width) if min(row[x * 3 : x * 3 + 3]) < 128]
        if dark:
            xs.extend((dark[0], dark[-1]))
            ys.append(y)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("rotate", [0, 90, 180, 270, -90, 450])
def test_text_turns_clockwise_about_its_origin(rotate: int, embedded: bool, regular: pylopdf.Font) -> None:  # noqa: FBT001 - parametrized.
    _doc, page = _new_page(300, 300)
    font = regular if embedded else None
    with page.draw() as canvas:
        canvas.text("Turned\nsecond", (150, 150), font=font, size=12, line_height=2, rotate=rotate)
    first, second = page.content().text_runs
    cos, sin = _TURNS[rotate % 360]
    origin = first.glyphs[0].origin
    assert (origin.x, origin.y) == pytest.approx((150, 150), abs=0.01)
    for glyph in (*first.glyphs, *second.glyphs):
        assert _direction(glyph) == pytest.approx((cos, sin), abs=1e-6)
        assert glyph.size == pytest.approx(12, abs=0.01)
    # The second line is 24 points further along the turned downward axis.
    below = second.glyphs[0].origin
    assert (below.x, below.y) == pytest.approx((150 - 24 * sin, 150 + 24 * cos), abs=0.01)
    assert page.text() == "Turned\nsecond\n"


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_turned_text_renders_along_its_direction(rotate: int, embedded: bool, regular: pylopdf.Font) -> None:  # noqa: FBT001 - parametrized.
    _doc, page = _new_page(200, 200)
    font = regular if embedded else None
    with page.draw() as canvas:
        canvas.text("MMMMMM", (100, 100), font=font, size=20, rotate=rotate)
    x0, y0, x1, y1 = _ink(page)
    cos, sin = _TURNS[rotate]
    # The ink runs away from the origin along the baseline, and the glyphs
    # stand on the side opposite the turned downward axis.
    if cos:
        assert x1 - x0 > 2 * (y1 - y0)
        assert (x0 >= 99) if cos > 0 else (x1 <= 101)
        assert (y1 <= 101) if cos > 0 else (y0 >= 99)
    else:
        assert y1 - y0 > 2 * (x1 - x0)
        assert (y0 >= 99) if sin > 0 else (y1 <= 101)
        assert (x0 >= 99) if sin > 0 else (x1 <= 101)


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_turned_textbox_matches_an_upright_box_of_the_turned_size(
    rotate: int,
    embedded: bool,  # noqa: FBT001 - parametrized.
    regular: pylopdf.Font,
) -> None:
    text = "A paragraph long enough to wrap onto several lines of a narrow box."
    font = regular if embedded else None
    rect = (40, 20, 100, 180)
    width, height = (60, 160) if rotate == 180 else (160, 60)
    _doc, upright = _new_page(300, 300)
    with upright.draw() as canvas:
        expected = canvas.textbox((0, 0, width, height), text, font=font, size=10, align="center")
    _doc, page = _new_page(300, 300)
    with page.draw() as canvas:
        fit = canvas.textbox(rect, text, font=font, size=10, align="center", rotate=rotate)
    assert fit.spare_height == pytest.approx(expected.spare_height)
    assert fit.fits
    # Each glyph is the upright box's glyph, moved by the turn that maps
    # the upright box onto ``rect``.
    x0, y0, x1, y1 = rect
    a, b, c, d, e, f = {
        90: (0, 1, -1, 0, x1, y0),
        180: (-1, 0, 0, -1, x1, y1),
        270: (0, -1, 1, 0, x0, y1),
    }[rotate]
    upright_glyphs = [glyph for run in upright.content().text_runs for glyph in run.glyphs]
    glyphs = [glyph for run in page.content().text_runs for glyph in run.glyphs]
    assert len(glyphs) == len(upright_glyphs) > 0
    for glyph, source in zip(glyphs, upright_glyphs, strict=True):
        x, y = source.origin
        assert (glyph.origin.x, glyph.origin.y) == pytest.approx((a * x + c * y + e, b * x + d * y + f), abs=0.01)
        assert _direction(glyph) == pytest.approx((a, b), abs=1e-6)
    for run in page.content().text_runs:
        assert run.bbox.x0 >= x0 - 0.5
        assert run.bbox.x1 <= x1 + 0.5
        assert run.bbox.y0 >= y0 - 0.5
        assert run.bbox.y1 <= y1 + 0.5
    assert page.text().split() == text.split()


def test_turned_textbox_overflow_and_empty_text_measure_across_the_lines() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        text = "Far too much text for this turned box, which wraps onto lines"
        overflow = canvas.textbox((10, 10, 25, 190), text, size=10, rotate=90)
        empty = canvas.textbox((10, 10, 40, 190), "", rotate=270)
    assert not overflow.fits
    assert empty == TextFit(spare_height=30)
    assert _items(page) == []


def test_turned_text_survives_a_save(regular: pylopdf.Font) -> None:
    doc, page = _new_page(300, 300)
    with page.draw() as canvas:
        canvas.text("down", (150, 20), font=regular, size=12, rotate=90)
        canvas.textbox((20, 20, 60, 280), "up the box", size=10, rotate=270)
    before = [(run.text, run.glyphs[0].origin, _direction(run.glyphs[0])) for run in page.content().text_runs]
    reopened = pylopdf.open(doc.to_bytes()).pages[0]
    after = [(run.text, run.glyphs[0].origin, _direction(run.glyphs[0])) for run in reopened.content().text_runs]
    assert after == before
    assert reopened.text() == page.text()


def test_turned_text_needs_no_scope() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        scopes = [canvas.transform(Matrix(1, 0, 0, 1, 1, 0)) for _ in range(16)]
        for scope in scopes:
            scope.__enter__()
        canvas.text("deep", (10, 40), rotate=90)
        canvas.textbox((50, 10, 80, 190), "deep box", rotate=270)
        for scope in reversed(scopes):
            scope.__exit__(None, None, None)
    upright, boxed = page.content().text_runs
    assert _direction(upright.glyphs[0]) == pytest.approx((0, 1), abs=1e-6)
    assert upright.glyphs[0].origin.x == pytest.approx(26, abs=0.01)
    assert _direction(boxed.glyphs[0]) == pytest.approx((0, -1), abs=1e-6)


def test_turned_text_in_a_new_document(regular: pylopdf.Font) -> None:
    builder = pylopdf.create()
    with builder.page((200, 200)) as canvas:
        canvas.text("Turned", (100, 100), font=regular, size=12, rotate=90)
        canvas.textbox((20, 20, 60, 180), "Boxed lines", font=regular, size=10, rotate=270)
    page = builder.finish().pages[0]
    turned, boxed = page.content().text_runs
    assert (turned.glyphs[0].origin.x, turned.glyphs[0].origin.y) == pytest.approx((100, 100), abs=0.01)
    assert _direction(turned.glyphs[0]) == pytest.approx((0, 1), abs=1e-6)
    assert _direction(boxed.glyphs[0]) == pytest.approx((0, -1), abs=1e-6)
    assert boxed.bbox.x0 >= 20
    assert boxed.bbox.x1 <= 60
    assert page.text() == "Turned\nBoxed lines\n"


@pytest.mark.parametrize("rotate", [45, 1, True, 90.0, "90"])
def test_text_turns_only_by_right_angles(rotate: object) -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        with pytest.raises(ValueError, match="multiple of 90"):
            canvas.text("x", (10, 10), rotate=rotate)  # ty: ignore[invalid-argument-type]
        with pytest.raises(ValueError, match="multiple of 90"):
            canvas.textbox((10, 10, 50, 50), "x", rotate=rotate)  # ty: ignore[invalid-argument-type]
    assert _items(page) == []


def test_transform_scopes_compose_inner_first() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        with canvas.transform(Matrix(1, 0, 0, 1, 100, 50)), canvas.transform(Matrix(2, 0, 0, 2, 0, 0)):
            canvas.rect((10, 10, 20, 20), fill=RED)
        canvas.rect((10, 10, 20, 20), fill=BLUE)
    scaled, plain = _items(page)
    # The inner scale applies first, then the outer translation.
    _assert_rect(scaled.bbox, (120, 70, 140, 90))
    _assert_rect(plain.bbox, (10, 10, 20, 20))


def test_clip_scope_lists_the_clip_in_the_context() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        with canvas.clip(PathBuilder().rect((0, 0, 100, 100))):
            canvas.circle((100, 100), 50, fill=RED)
        canvas.rect((200, 10, 250, 60), fill=BLUE)
    clipped, free = _items(page)
    # The canvas's Form clips everything to the page; the scope adds one clip.
    assert clipped.context.clips[:-1] == free.context.clips
    _assert_rect(clipped.context.clips[-1].bbox, (0, 0, 100, 100))
    _assert_rect(clipped.context.clip_bbox, (0, 0, 100, 100))
    assert _pixel(page, 90, 90) == (255, 0, 0)
    assert _pixel(page, 110, 110) == (255, 255, 255)


def test_opacity_scopes_are_transparency_groups() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        with canvas.opacity(0.5):
            canvas.rect((10, 10, 60, 60), fill=BLACK)
            with canvas.opacity(0.5):
                canvas.rect((70, 10, 120, 60), fill=BLACK)
            canvas.text("inside", (10, 90))
        with canvas.opacity(0.3):
            pass
        canvas.rect((130, 10, 180, 60), fill=BLACK)
    outer, nested, text, opaque = _items(page)
    assert outer.context.opacity == pytest.approx(0.5, abs=1 / 255)
    assert nested.context.opacity == pytest.approx(0.25, abs=1 / 255)
    assert text.context.opacity == pytest.approx(0.5, abs=1 / 255)
    assert opaque.context.groups == ()
    gray = _pixel(page, 35, 35)
    assert 120 <= gray[0] <= 135
    assert gray[0] == gray[1] == gray[2]


def test_opacity_inside_a_transform_keeps_the_group_on_the_page() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas, canvas.transform(Matrix(0.5, 0, 0, 0.5, 150, 100)), canvas.opacity(0.5):
        canvas.rect((0, 0, 300, 200), fill=BLACK)
    (item,) = _items(page)
    _assert_rect(item.bbox, (150, 100, 300, 200))
    assert item.context.opacity == pytest.approx(0.5, abs=1 / 255)
    assert _pixel(page, 250, 150)[0] < 200


def test_blend_scope_and_alpha_inside_it() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.rect((10, 10, 100, 100), fill=Color(1, 1, 0))
        with canvas.blend(BlendMode.MULTIPLY):
            canvas.rect((50, 50, 150, 150), fill=Color(0, 1, 1, 0.5))
        canvas.rect((200, 10, 250, 60), fill=RED)
    _below, blended, after = _items(page)
    # A translucent fill keeps the blend mode of its scope.
    assert blended.blend_mode is BlendMode.MULTIPLY
    _assert_color(blended.fill, Color(0, 1, 1, 0.5))
    assert after.blend_mode is BlendMode.NORMAL
    with page.draw() as canvas, pytest.raises(ValueError, match="BlendMode"):
        canvas.blend("burn")


def _png(width: int = 4, height: int = 2) -> bytes:
    _doc, page = _new_page(width, height)
    with page.draw() as canvas:
        canvas.rect((0, 0, width / 2, height), fill=RED)
    return page.pixmap().to_bytes()


def test_images_are_stored_once() -> None:
    doc, page = _new_page()
    png = _png()
    with page.draw() as canvas:
        canvas.image((10, 10, 50, 30), png)
        canvas.image((60, 10, 100, 30), png)
        canvas.image((10, 50, 50, 130), png, rotate=90)
    first, second, rotated = page.content().images
    assert first.xref == second.xref == rotated.xref
    _assert_rect(first.bbox, (10, 10, 50, 30))
    _assert_rect(second.bbox, (60, 10, 100, 30))
    # Rotated a quarter turn, the 2:1 image fills the 1:2 rectangle.
    _assert_rect(rotated.bbox, (10, 50, 50, 130))
    assert len(page.resources.images) == 1
    # A later canvas places the stored image again.
    with page.draw() as canvas:
        canvas.image((200, 10, 240, 30), png)
    assert len({image.xref for image in page.content().images}) == 1
    assert _pixel(page, 15, 20) == (255, 0, 0)
    assert len(doc.pages) == 1


def test_image_sources(tmp_path: Path) -> None:
    _doc, page = _new_page()
    png = _png()
    path = tmp_path / "image.png"
    path.write_bytes(png)
    pixmap = pylopdf.open(_one_page_pdf()).pages[0].pixmap()
    with page.draw() as canvas:
        canvas.image((10, 10, 50, 30), path)
        canvas.image((60, 10, 100, 30), str(path))
        canvas.image((110, 10, 150, 100), pixmap, keep_aspect=False)
        canvas.image((160, 10, 200, 100), memoryview(png))
    images = page.content().images
    assert len(images) == 4
    _assert_rect(images[2].bbox, (110, 10, 150, 100))
    with page.draw() as canvas:
        with pytest.raises(ValueError, match="multiple of 90"):
            canvas.image((10, 10, 50, 30), png, rotate=45)
        with pytest.raises(TypeError, match="source"):
            canvas.image((10, 10, 50, 30), 42)  # ty: ignore[invalid-argument-type]
        with pytest.raises(pylopdf.LimitError) as excinfo:
            canvas.image((10, 10, 50, 30), png, max_size=8)
        assert excinfo.value.code == "image_input_size"


def _one_page_pdf(text: str = "Source page") -> bytes:
    doc, page = _new_page(200, 100)
    with page.draw() as canvas:
        canvas.text(text, (20, 50), size=14)
        canvas.rect((0, 0, 200, 100), stroke=RED)
    return doc.to_bytes()


def test_pages_of_other_documents_are_stored_once() -> None:
    source = pylopdf.open(_one_page_pdf())
    doc, page = _new_page(400, 300)
    with page.draw() as canvas:
        canvas.page((0, 0, 200, 100), source.pages[0])
        canvas.page((200, 100, 400, 200), source.pages[0])
    assert page.text() == "Source page\nSource page\n"
    runs = page.content().text_runs
    assert runs[0].bbox.x0 < 200 < runs[1].bbox.x0
    # Drawing the unchanged page again reuses the Form of the first canvas.
    count = len(doc.objects)
    with page.draw() as canvas:
        canvas.page((0, 200, 200, 300), source.pages[0])
    assert len(doc.objects) - count < 10
    assert page.text().count("Source page") == 3


def test_a_page_is_drawn_as_it_was_recorded() -> None:
    doc = pylopdf.open(_one_page_pdf("Before"))
    target = doc.pages.new((300, 200))
    source = doc.pages[0]
    with target.draw() as canvas:
        canvas.page((0, 0, 150, 75), source)
        canvas.rect((200, 10, 250, 60), fill=RED)
    assert "Before" in target.text()
    # Placing a page on itself draws its earlier content.
    with source.draw() as canvas:
        canvas.page((100, 50, 200, 100), source)
    assert source.text().count("Before") == 2


def test_underlay_draws_below_existing_content() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.rect((10, 10, 100, 100), fill=RED)
    with page.draw(underlay=True) as canvas:
        canvas.rect((50, 50, 150, 150), fill=BLUE)
    under, over = _items(page)
    _assert_color(under.fill, BLUE)
    _assert_color(over.fill, RED)
    assert _pixel(page, 75, 75) == (255, 0, 0)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_paths_and_text_use_display_space_at_every_rotation(rotation: int, regular: pylopdf.Font) -> None:
    _doc, page = _new_page(400, 300)
    page.rotation = rotation
    with page.draw() as canvas:
        canvas.rect((20, 30, 120, 80), stroke=Stroke(paint=BLUE, width=2))
        canvas.text("見出し", (40, 60), font=regular, size=16)
        canvas.text("Standard", (40, 120), size=12)
    (path,) = page.content().paths
    _assert_rect(path.bbox, (20, 30, 120, 80))
    words = {word.text: word for word in page.words()}
    assert words["見出し"].bbox.x0 == pytest.approx(40, abs=0.5)
    assert words["Standard"].bbox.x0 == pytest.approx(40, abs=0.5)
    assert words["見出し"].bbox.y0 < 60 < words["見出し"].bbox.y1
    assert words["Standard"].bbox.y0 < 120 < words["Standard"].bbox.y1
    assert (page.rect.width, page.rect.height) == ((400, 300) if rotation in (0, 180) else (300, 400))


def test_rotated_pages_use_display_space() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 300))
    page.rotation = 90
    assert tuple(page.rect) == (0, 0, 300, 200)
    with page.draw() as canvas:
        canvas.rect((10, 20, 110, 70), fill=RED)
        canvas.text("rotated", (20, 150))
    rect, text = _items(page)
    _assert_rect(rect.bbox, (10, 20, 110, 70))
    assert text.glyphs[0].origin.y == pytest.approx(150, abs=0.01)
    assert text.glyphs[0].matrix.a > 0
    assert page.text() == "rotated\n"


def test_an_exception_discards_the_drawing() -> None:
    doc, page = _new_page()
    before = doc.to_bytes()

    def draw_then_fail() -> None:
        with page.draw() as canvas:
            canvas.rect((10, 10, 50, 50), fill=RED)
            msg = "stop"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="stop"):
        draw_then_fail()
    assert doc.to_bytes() == before
    assert _items(page) == []


def test_a_canvas_on_a_stale_page_cannot_write() -> None:
    doc, page = _new_page()
    canvas = page.draw()
    canvas.line((10, 10), (100, 10))
    doc.pages.new()
    with pytest.raises(pylopdf.StalePageError), canvas:
        pass
    assert len(doc.pages[0].content().paths) == 0
    with pytest.raises(pylopdf.StalePageError):
        page.draw()


def test_an_empty_canvas_changes_nothing() -> None:
    doc, page = _new_page()
    before = doc.to_bytes()
    with page.draw():
        pass
    assert doc.to_bytes() == before


def test_a_canvas_is_written_once() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.rect((10, 10, 50, 50), fill=RED)
    with pytest.raises(ValueError, match="already written"):
        canvas.rect((10, 10, 50, 50), fill=RED)
    with pytest.raises(ValueError, match="already written"), canvas:
        pass


def test_an_unwritten_canvas_warns() -> None:
    _doc, page = _new_page()
    canvas = page.draw()
    canvas.rect((10, 10, 50, 50), fill=RED)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del canvas
        gc.collect()
    assert any(issubclass(warning.category, ResourceWarning) for warning in caught)
    assert _items(page) == []


def test_scopes_close_in_reverse_order() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        outer = canvas.transform(Matrix(1, 0, 0, 1, 10, 0))
        inner = canvas.opacity(0.5)
        outer.__enter__()
        inner.__enter__()
        with pytest.raises(pylopdf.PdfError, match="reverse order"):
            outer.__exit__(None, None, None)
        inner.__exit__(None, None, None)
        outer.__exit__(None, None, None)
        canvas.rect((0, 0, 10, 10), fill=RED)
    (item,) = _items(page)
    _assert_rect(item.bbox, (0, 0, 10, 10))


def test_a_scope_left_open_refuses_to_write() -> None:
    _doc, page = _new_page()
    canvas = page.draw()
    canvas.clip(PathBuilder().rect((0, 0, 10, 10))).__enter__()
    canvas.rect((0, 0, 10, 10), fill=RED)
    with pytest.raises(pylopdf.PdfError, match="still open"):
        canvas.__exit__(None, None, None)
    assert _items(page) == []


def test_scope_depth_limit() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        scopes = [canvas.transform(Matrix(1, 0, 0, 1, 1, 0)) for _ in range(17)]
        for scope in scopes[:16]:
            scope.__enter__()
        with pytest.raises(pylopdf.LimitError) as excinfo:
            scopes[16].__enter__()
        assert excinfo.value.code == "canvas_scope_depth"
        for scope in reversed(scopes[:16]):
            scope.__exit__(None, None, None)


def test_path_point_limit() -> None:
    _doc, page = _new_page()
    builder = PathBuilder().move_to((0, 0))
    for index in range(1_048_576):
        builder.line_to((index % 300, 1))
    with page.draw() as canvas, pytest.raises(pylopdf.LimitError) as excinfo:
        canvas.path(builder)
    assert excinfo.value.code == "path_point_count"


def test_draw_repaints_path_items_with_their_context() -> None:
    _source_doc, source = _new_page()
    with source.draw() as canvas:
        with canvas.clip(PathBuilder().rect((0, 0, 60, 60))), canvas.opacity(0.5):
            canvas.circle((50, 50), 30, fill=RED, stroke=Stroke(paint=BLUE, width=3))
        with canvas.blend(BlendMode.SCREEN):
            canvas.rect((100, 100, 150, 150), fill=BLUE)
    original = list(source.content().paths)
    _doc, page = _new_page()
    with page.draw() as canvas:
        for item in original:
            canvas.draw(item)
    copied = list(page.content().paths)
    assert len(copied) == len(original)
    for before, after in zip(original, copied, strict=True):
        _assert_rect(after.bbox, tuple(before.bbox), 0.05)
        assert after.blend_mode is before.blend_mode
        assert after.context.opacity == pytest.approx(before.context.opacity, abs=1 / 255)
        assert len(after.context.clips) == len(before.context.clips)
        _assert_color(after.fill, before.fill)
    with page.draw() as canvas, pytest.raises(TypeError, match="Path items"):
        canvas.draw(page.content().paths[0].context)  # ty: ignore[invalid-argument-type]


def test_the_page_must_keep_its_size_while_the_canvas_is_open() -> None:
    _doc, page = _new_page()
    canvas = page.draw()
    canvas.rect((0, 0, 10, 10), fill=RED)
    page.boxes.media = (0, 0, 100, 100)
    with pytest.raises(pylopdf.PdfError, match="changed size"), canvas:
        pass


def test_path_builder_constructors() -> None:
    builder = PathBuilder().move_to((0, 0)).line_to((10, 0)).quad_to((20, 0), (20, 10)).close()
    segments = builder.segments
    assert isinstance(segments[0], MoveTo)
    assert isinstance(segments[1], LineTo)
    assert isinstance(segments[2], CurveTo)
    assert segments[2].c1 == pytest.approx((10 + 20 / 3, 0))
    assert isinstance(segments[3], ClosePath)
    assert builder.current_point == (0, 0)
    with pytest.raises(ValueError, match="move_to"):
        PathBuilder().line_to((1, 1))

    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.path(PathBuilder().sector((100, 100), 50, 0, 90), fill=RED)
        canvas.path(PathBuilder().sector((100, 100), 50, 180, 360, pie=False), stroke=BLUE)
        canvas.path(PathBuilder().zigzag((10, 150), (110, 150), breadth=5))
        canvas.path(PathBuilder().wave((10, 180), (110, 180), breadth=5))
        canvas.path(PathBuilder().rect((200, 10, 280, 60), radius=10), fill=BLUE)
    sector, circle, zigzag, wave, rounded = _items(page)
    # Angles run clockwise on the page: a quarter from 0 to 90 degrees is
    # the lower right quarter.
    _assert_rect(sector.bbox, (100, 100, 150, 150), 0.05)
    _assert_rect(circle.bbox, (50, 50, 150, 150), 0.1)
    assert zigzag.bbox.y0 < 150 < zigzag.bbox.y1
    assert wave.bbox.y0 < 180 < wave.bbox.y1
    _assert_rect(rounded.bbox, (200, 10, 280, 60))
    assert _pixel(page, 201, 11) == (255, 255, 255)
    assert _pixel(page, 240, 35) == (0, 0, 255)


def test_invalid_arguments() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        with pytest.raises(TypeError, match="Color"):
            canvas.rect((0, 0, 10, 10), fill=(1, 0, 0))  # ty: ignore[invalid-argument-type]
        with pytest.raises(ValueError, match="from 0 to 1"):
            canvas.rect((0, 0, 10, 10), fill=Color(2, 0, 0))
        with pytest.raises(ValueError, match="x0 < x1"):
            canvas.rect((10, 0, 0, 10), fill=RED)
        with pytest.raises(ValueError, match="no segments"):
            canvas.path([])
        with pytest.raises(ValueError, match="MoveTo"):
            canvas.path([LineTo(pylopdf.Point(1, 1))])
        with pytest.raises(ValueError, match="positive"):
            canvas.line((0, 0), (1, 1), stroke=Stroke(paint=RED, width=0))
        with pytest.raises(ValueError, match="from 0 to 1"):
            canvas.opacity(1.5)
        with pytest.raises(ValueError, match="matrix"):
            canvas.transform((1, 0, 0, 1, math.inf, 0))
    assert _items(page) == []


def test_small_transform_scales_keep_their_precision() -> None:
    _doc, page = _new_page()
    with page.draw() as canvas, canvas.transform(Matrix(0.0001, 0, 0, 0.0001, 0, 0)):
        canvas.rect((0, 0, 100_000, 50_000), fill=RED)
    (item,) = _items(page)
    _assert_rect(item.bbox, (0, 0, 10, 5))


def test_one_canvas_embeds_each_font_once(regular: pylopdf.Font) -> None:
    doc, page = _new_page(400, 300)
    with page.draw() as canvas:
        for row in range(6):
            canvas.text(f"行 {row} の本文", (20, 30 + 20 * row), font=regular, size=12)
            canvas.text(f"Standard {row}", (250, 30 + 20 * row), size=10)
        canvas.textbox((20, 160, 380, 280), "折り返す日本語の文章です。" * 5, font=regular, size=10)
    assert _font_files(doc) == 1
    words = [word.text for word in page.words()]
    assert "Standard" in words
    assert "の本文" in words

    separate, separate_page = _new_page(400, 300)
    for row in range(3):
        with separate_page.draw() as canvas:
            canvas.text(f"行 {row}", (20, 30 + 20 * row), font=regular, size=12)
    assert _font_files(separate) == 3


def test_installed_fonts_are_embedded_once_per_canvas() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    doc, page = _new_page()
    with page.draw() as canvas:
        canvas.text("日本語の見出し", (20, 50), size=14)
        canvas.text("二行目の本文", (20, 80), size=12)
    assert [word.text for word in page.words()] == ["日本語の見出し", "二行目の本文"]
    assert _font_files(doc) == 1


def test_weight_instances_of_one_variable_face_keep_their_names() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    # Noto Sans Hebrew is variable, and its default instance is Thin.
    doc, page = _new_page(400, 300)
    regular = pylopdf.Font.installed(language="he")
    bold = pylopdf.Font.installed(language="he", weight=700)
    thin = pylopdf.Font.installed(language="he", weight=100)
    with page.draw() as canvas:
        canvas.line((20, 20), (380, 20))
        canvas.text("שלום", (20, 60), font=regular, size=20)
        canvas.text("שלום", (20, 100), font=bold, size=20)
        canvas.text("Standard", (20, 140), size=12)
        canvas.text("עולם", (20, 180), font=bold, size=20)
        canvas.text("שלום", (20, 220), font=thin, size=20)
        canvas.text("עולם", (20, 260), font=regular, size=20)
    spans = [span for block in page.layout().blocks for line in block.lines for span in line.spans]
    by_line = {round(span.origin.y): span.font for span in spans if span.text.strip() != "Standard"}
    assert by_line == {
        60: "NotoSansHebrew-Regular",
        100: "NotoSansHebrew-Bold",
        180: "NotoSansHebrew-Bold",
        220: "NotoSansHebrew-Thin",
        260: "NotoSansHebrew-Regular",
    }
    # Each instance is embedded once, however many lines use it.
    assert _font_files(doc) == 3
    # An installed extra chosen for the text is drawn at the regular weight,
    # and a new canvas embeds it again.
    with page.draw() as canvas:
        canvas.text("עולם", (200, 60), size=20)
    spans = [span for block in page.layout().blocks for line in block.lines for span in line.spans]
    assert {span.font for span in spans if span.origin.x >= 200} == {"NotoSansHebrew-Regular"}
    assert _font_files(doc) == 4


def test_report_pages_with_measured_labels_frames_and_photo_marks(regular: pylopdf.Font, bold: pylopdf.Font) -> None:
    """Build A4 report pages: framed notes sized to their text, measured labels, and marks over a photo."""
    doc = pylopdf.Document()
    accent = Color(0.83, 0.26, 0.05)
    frame = Stroke(paint=Color(0.72, 0.78, 0.82), width=0.5)
    note = "調査の結果、外壁の北面に幅0.3mmのひび割れを確認した。" * 4
    for number in range(2):
        page = doc.pages.new(A4)
        height = regular.text_height(note, 10.5, width=484)
        with page.draw() as canvas:
            canvas.rect((50, 150, 545, 160 + height + 10), radius=4, fill=Color(0.95, 0.96, 0.97), stroke=frame)
            canvas.line((56, 90), (540, 90), stroke=Stroke(paint=BLACK, width=1.6))
            canvas.circle((80, 140), 7, fill=accent, stroke=Stroke(paint=WHITE, width=1.2))
            canvas.polygon([(520, 130), (535, 140), (520, 150)], fill=accent)
            canvas.text(f"現地調査報告書 {number + 1}", (56, 80), font=bold, size=18)
            end = 56 + bold.text_width("項目", 12)
            canvas.text("項目", (56, 120), font=bold, size=12)
            canvas.text("：外壁のひび割れ", (end, 120), font=regular, size=12)
            badge = bold.text_width("注意", 9) + 8
            canvas.rect((440, 112, 440 + badge, 126), radius=2, fill=accent)
            canvas.text("注意", (444, 123), font=bold, size=9, fill=WHITE)
            fit = canvas.textbox((56, 160, 540, 160 + height), note, font=regular, size=10.5)
        assert fit == TextFit(spare_height=0)
        glyphs = [glyph for run in page.content(paths=False, images=False).text_runs for glyph in run.glyphs]
        colon = next(glyph for glyph in glyphs if glyph.text == "：")
        # The value starts where the measured label ends.
        assert colon.origin.x == pytest.approx(end, abs=0.01)
        assert len(page.content().paths) == 5
        # The frame ends one padding below the note's last line.
        last = max(glyph.bbox.y1 for glyph in glyphs if glyph.bbox is not None)
        assert last <= 160 + height < 160 + height + 10

    # Each canvas embeds one subset of each face it uses.
    assert _font_files(doc) == 4

    # A record page: marks drawn after a photo lie on top of it.
    page = doc.pages.new(A4)
    with page.draw() as canvas:
        canvas.image((56, 420, 300, 600), PHOTO_JPEG, keep_aspect=False)
        canvas.rect((120, 470, 200, 540), stroke=Stroke(paint=RED, width=3))
        canvas.circle((90, 450), 12, fill=accent)
        canvas.text("1", (86, 455), font=pylopdf.Font.standard("Helvetica-Bold"), size=12, fill=WHITE)
        canvas.line((210, 560), (290, 590), stroke=Stroke(paint=RED, width=2))
    assert _pixel(page, 120, 505) == (255, 0, 0)
    assert _pixel(page, 98, 445) == (212, 66, 13)
    # Inside the stroked frame the photo still shows.
    red, green, blue = _pixel(page, 160, 505)
    assert red == green == blue < 250
    saved = pylopdf.open(doc.to_bytes())
    assert len(saved.pages) == 3
    assert "現地調査報告書" in saved.pages[0].text()
