"""Square and Circle annotations, borders, blend modes, and the appearances that the engine draws."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import BlendMode, Border, BorderStyle, Circle, Color, Rect, Square

RED = Color(1.0, 0.0, 0.0)
BLUE = Color(0.0, 0.0, 1.0)
YELLOW = Color(1.0, 1.0, 0.0)
WHITE = (255, 255, 255)


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * pixmap.channels
    red, green, blue = bytes(pixmap)[offset : offset + 3]
    return red, green, blue


def _page(rotation: int = 0) -> pylopdf.Page:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 200))
    page.rotation = rotation
    return page


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_squares_and_circles_render_on_their_display_rectangles(rotation: int) -> None:
    page = _page(rotation)

    square = page.annotations.add(Square((10, 10, 110, 60), color=BLUE, interior_color=YELLOW, border=Border(4)))
    circle = page.annotations.add(Circle((120, 10, 190, 90), color=None, interior_color=RED))

    assert square.type is pylopdf.AnnotationType.SQUARE
    assert circle.type is pylopdf.AnnotationType.CIRCLE
    assert tuple(square.rect) == pytest.approx((10, 10, 110, 60), abs=1e-3)
    assert tuple(circle.rect) == pytest.approx((120, 10, 190, 90), abs=1e-3)
    pixmap = page.pixmap(dpi=72)
    # The border lies inside the rectangle and the fill inside the border.
    assert _pixel(pixmap, 11, 11) == (0, 0, 255)
    assert _pixel(pixmap, 60, 35) == (255, 255, 0)
    assert _pixel(pixmap, 112, 35) == WHITE
    # The ellipse fills its centre, not the rectangle's corners.
    assert _pixel(pixmap, 155, 50) == (255, 0, 0)
    assert _pixel(pixmap, 122, 12) == WHITE


def test_shapes_survive_save_and_reopen() -> None:
    page = _page(90)
    page.annotations.add(
        Square(
            (20, 20, 120, 80),
            color=BLUE,
            interior_color=YELLOW,
            border=Border(2, dashes=(3, 1)),
            opacity=0.5,
            blend_mode=BlendMode.MULTIPLY,
            contents="Check this",
        )
    )

    reopened = pylopdf.open(page.document.to_bytes())

    (square,) = reopened.pages[0].annotations
    assert square.type is pylopdf.AnnotationType.SQUARE
    assert tuple(square.rect) == pytest.approx((20, 20, 120, 80), abs=1e-3)
    assert square.color == BLUE
    assert square.interior_color == YELLOW
    assert square.border == Border(2, dashes=(3, 1))
    assert square.border.style is BorderStyle.DASHED
    assert square.opacity == pytest.approx(0.5)
    assert square.blend_mode is BlendMode.MULTIPLY
    assert square.contents == "Check this"
    assert square.flags == pylopdf.AnnotationFlags.PRINT
    assert square.created is not None
    assert square.created.startswith("D:")
    assert square.modified == square.created
    obj = square.obj
    assert obj["P"] == reopened.pages[0].obj
    assert obj["BS"]["S"] == "D"
    assert obj["AP"]["N"]["Subtype"] == "Form"
    # Half-transparent yellow multiplies the white page.
    red, green, blue = _pixel(reopened.pages[0].pixmap(dpi=72), 70, 50)
    assert (red, green) == (255, 255)
    assert blue == pytest.approx(128, abs=1)


def test_a_shape_without_paints_still_adds_an_annotation() -> None:
    page = _page()

    square = page.annotations.add(Square((10, 10, 50, 50), color=None))

    assert square.color is None
    assert square.interior_color is None
    assert bytes(page.pixmap(dpi=72)) == b"\xff" * (300 * 200 * 3)


def test_a_zero_width_border_draws_only_the_fill() -> None:
    page = _page()

    page.annotations.add(Square((10, 10, 50, 50), color=BLUE, interior_color=YELLOW, border=Border(0)))

    pixmap = page.pixmap(dpi=72)
    assert _pixel(pixmap, 10, 10) == (255, 255, 0)
    assert _pixel(pixmap, 49, 49) == (255, 255, 0)


def test_borders_validate_their_fields() -> None:
    assert Border() == Border(1.0, style=BorderStyle.SOLID)
    assert Border(dashes=[2, 1]).style is BorderStyle.DASHED
    assert Border(2, style=cast("Any", "inset")).style is BorderStyle.INSET
    with pytest.raises(ValueError, match="not negative"):
        Border(-1)
    with pytest.raises(ValueError, match="finite"):
        Border(float("nan"))
    with pytest.raises(TypeError, match="number"):
        Border(True)
    with pytest.raises(ValueError, match="needs dash lengths"):
        Border(style=BorderStyle.DASHED)
    with pytest.raises(ValueError, match="needs dash lengths"):
        Border(style=BorderStyle.SOLID, dashes=(1,))
    with pytest.raises(ValueError, match="sum to 0"):
        Border(dashes=(0, 0))
    with pytest.raises(ValueError, match="sum to 0"):
        Border(dashes=(1, -1))
    with pytest.raises(ValueError, match="at most 32"):
        Border(dashes=(1,) * 33)
    with pytest.raises(TypeError, match="sequence"):
        Border(dashes=cast("Any", "12"))
    with pytest.raises(ValueError, match="'wavy'"):
        Border(style=cast("Any", "wavy"))


@pytest.mark.parametrize("kind", [Square, Circle])
def test_shape_specifications_validate_their_fields(kind: type[Square | Circle]) -> None:
    shape = kind((10, 10, 20, 20))
    assert shape.color == RED
    assert shape.interior_color is None
    assert shape.border == Border()
    assert (shape.opacity, shape.blend_mode, shape.contents) == (1.0, None, None)
    assert kind(Rect(10, 10, 20, 20), blend_mode=cast("Any", "screen")).blend_mode is BlendMode.SCREEN
    with pytest.raises(TypeError, match="color must be a Color"):
        kind((10, 10, 20, 20), color=cast("Any", (1, 0, 0)))
    with pytest.raises(ValueError, match="no transparency"):
        kind((10, 10, 20, 20), interior_color=Color(1, 0, 0, 0.5))
    with pytest.raises(TypeError, match="border must be a Border"):
        kind((10, 10, 20, 20), border=cast("Any", 2))
    with pytest.raises(ValueError, match="greater than 0"):
        kind((10, 10, 20, 20), opacity=0)
    with pytest.raises(TypeError, match="contents"):
        kind((10, 10, 20, 20), contents=cast("Any", b"x"))
    with pytest.raises(ValueError, match="'vivid'"):
        kind((10, 10, 20, 20), blend_mode=cast("Any", "vivid"))
    with pytest.raises(pylopdf.LimitError) as raised:
        kind((10, 10, 20, 20), contents="x" * (1024 * 1024))
    assert raised.value.code == "annotation_input_size"
    with pytest.raises((TypeError, ValueError)):
        kind(cast("Any", (10, 10, 20)))
    with pytest.raises(ValueError, match="finite"):
        kind((10, 10, float("inf"), 20))


def test_the_core_repeats_the_checks() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 100))
    core = doc._doc  # noqa: SLF001
    base: dict[str, Any] = {"subtype": "Square", "rect": (10, 10, 20, 20)}

    for spec, message in [
        ({"subtype": "Text"}, "does not create Text annotations"),
        ({"color": (2.0, 0.0, 0.0)}, "components from 0 to 1"),
        ({"border": (-1.0, "S", [])}, "not negative"),
        ({"border": (1.0, "X", [])}, "unknown annotation border style"),
        ({"border": (1.0, "D", [])}, "dashed border"),
        ({"border": (1.0, "S", [1.0])}, "dashed border"),
        ({"opacity": 0.0}, "greater than 0"),
        ({"blend_mode": "Vivid"}, "unknown blend mode"),
        ({"created": "2026"}, "PDF date string"),
        ({"rect": (10.0, 10.0, float("nan"), 20.0)}, "finite"),
    ]:
        with pytest.raises(pylopdf.PdfError, match=message):
            core.add_annotation(1, {**base, **spec})
    with pytest.raises(pylopdf.LimitError) as raised:
        core.add_annotation(1, {**base, "contents": "x" * (1024 * 1024)})
    assert raised.value.code == "annotation_input_size"
    assert len(doc.pages[0].annotations) == 0
    assert "Annots" not in doc.pages[0].obj


def _without_appearance(entries: str) -> pylopdf.Document:
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [4 0 R] >>",
                4: f"<< /Type /Annot {entries} >>",
            }
        )
    )


def test_rendering_draws_a_missing_shape_appearance_without_changing_the_document() -> None:
    doc = _without_appearance("/Subtype /Square /Rect [10 10 110 60] /C [0 0 1] /IC [1 1 0] /Border [0 0 4]")
    before = doc.to_bytes()

    pixmap = doc.pages[0].pixmap(dpi=72)

    assert _pixel(pixmap, 11, 89) == (0, 0, 255)
    assert _pixel(pixmap, 60, 65) == (255, 255, 0)
    assert "AP" not in doc.pages[0].annotations[0].obj
    assert doc.to_bytes() == before
    assert doc.pages[0].annotations[0].border == Border(4)


@pytest.mark.parametrize(
    ("entries", "inside"),
    [
        # /RD insets the shape; a CMYK colour converts to RGB.
        ("/Subtype /Circle /Rect [10 10 110 90] /IC [0 0 1 0] /RD [10 10 10 10]", (255, 255, 0)),
        # A gray colour, and /BS overrides /Border.
        ("/Subtype /Square /Rect [10 10 110 90] /IC [0.5] /BS << /W 0 >> /Border [0 0 9]", (128, 128, 128)),
    ],
)
def test_the_engine_reads_the_dictionary(entries: str, inside: tuple[int, int, int]) -> None:
    doc = _without_appearance(entries)

    pixmap = doc.pages[0].pixmap(dpi=72)

    assert _pixel(pixmap, 60, 50) == inside
    assert _pixel(pixmap, 12, 88) == (WHITE if "RD" in entries else inside)


@pytest.mark.parametrize(
    "entries",
    [
        # Unusable entries read as absent: no stroke or fill draws nothing.
        "/Subtype /Square /Rect [10 10 110 90] /C [] /IC (red)",
        "/Subtype /Square /Rect [10 10 110] /C [1 0 0]",
        "/Subtype /Circle /Rect [10 10 110 90] /C [1 0 0 0 0]",
        "/Subtype /Square /Rect [10 10 10 90] /IC [1 0 0]",
    ],
)
def test_the_engine_skips_unusable_dictionaries(entries: str) -> None:
    doc = _without_appearance(entries)

    assert bytes(doc.pages[0].pixmap(dpi=72)) == b"\xff" * (200 * 100 * 3)


def test_border_reads_follow_the_dictionary() -> None:
    doc = _without_appearance("/Subtype /Square /Rect [10 10 110 90] /BS << /S /D /D [] >>")
    assert doc.pages[0].annotations[0].border == Border(1, dashes=(3,))
    doc = _without_appearance("/Subtype /Square /Rect [10 10 110 90] /BS << /S /U /W 2 >> /BM /Screen")
    (annotation,) = doc.pages[0].annotations
    assert annotation.border == Border(2, style=BorderStyle.UNDERLINE)
    assert annotation.blend_mode is BlendMode.SCREEN
    doc = _without_appearance("/Subtype /Square /Rect [10 10 110 90] /Border [0 0 2 [4 2]] /BM /Vivid")
    (annotation,) = doc.pages[0].annotations
    assert annotation.border == Border(2, dashes=(4, 2))
    assert annotation.blend_mode is None
    doc = _without_appearance("/Subtype /Square /Rect [10 10 110 90] /Border [0 0]")
    assert doc.pages[0].annotations[0].border is None


def test_adding_a_shape_keeps_caches_of_unrelated_state_correct() -> None:
    page = _page()
    page.pixmap(dpi=72)
    page.annotations.add(Square((10, 10, 50, 50), interior_color=BLUE))

    assert _pixel(page.pixmap(dpi=72), 30, 30) == (0, 0, 255)
    assert [annotation.type for annotation in page.annotations] == [pylopdf.AnnotationType.SQUARE]
