"""The canvas: drawing in paint order, read back through the content model."""

from __future__ import annotations

import gc
import math
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


def _new_page(width: float = 300, height: float = 200) -> tuple[pylopdf.Document, pylopdf.Page]:
    doc = pylopdf.Document()
    return doc, doc.pages.new((width, height))


def _pixel(page: pylopdf.Page, x: float, y: float) -> tuple[int, int, int]:
    pixmap = page.get_pixmap()
    offset = int(y) * pixmap.stride + int(x) * 3
    samples = pixmap.samples
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
        canvas.text("second", (10, 120), font=pylopdf.Font(fontfile=NOTO_SANS_JP))
        canvas.rect((130, 10, 180, 60), fill=BLACK)
    kinds = [type(item).__name__ for item in _items(page)]
    assert kinds == ["Path", "TextRun", "Path", "TextRun", "Path"]
    assert page.text() == "first\nsecond\n"


def test_text_in_standard_and_embedded_fonts() -> None:
    _doc, page = _new_page(400, 200)
    font = pylopdf.Font(fontfile=NOTO_SANS_JP)
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
    font = pylopdf.Font(fontfile=NOTO_SANS_JP) if embedded else None
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
    font = pylopdf.Font(fontfile=NOTO_SANS_JP) if embedded else None
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
        canvas.text("日本", (10, 10), font=pylopdf.Font("helv"))


@pytest.mark.parametrize("embedded", [False, True])
def test_textbox_fits_or_draws_nothing(embedded: bool) -> None:  # noqa: FBT001 - parametrized.
    _doc, page = _new_page()
    font = pylopdf.Font(fontfile=NOTO_SANS_JP) if embedded else None
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
    return page.get_pixmap().tobytes()


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
    pixmap = pylopdf.open(_one_page_pdf()).pages[0].get_pixmap()
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


def test_rotated_pages_use_display_space() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 300))
    page.set_rotation(90)
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
    page.set_mediabox((0, 0, 100, 100))
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
