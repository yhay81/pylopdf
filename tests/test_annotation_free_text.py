"""FreeText annotations: text, fonts, alignment, rotation, and callouts."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Border, Color, Font, FreeText, LineEnding, Point, Rect

WHITE = (255, 255, 255)


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * pixmap.channels
    red, green, blue = bytes(pixmap)[offset : offset + 3]
    return red, green, blue


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_free_text_reads_upright_on_every_page_rotation(rotation: int) -> None:
    page = pylopdf.Document().pages.new((300, 300))
    page.rotation = rotation

    annotation = page.annotations.add(
        FreeText((20, 20, 200, 80), "Upright words", size=14, color=Color(1, 1, 0), border=Border(1))
    )

    assert annotation.type is pylopdf.AnnotationType.FREE_TEXT
    assert annotation.rect == pytest.approx(Rect(20, 20, 200, 80), abs=1e-3)
    assert annotation.contents == "Upright words"
    assert annotation.rotation == 0
    words = [(word.text, word.bbox) for word in page.words()]
    assert [text for text, _ in words] == ["Upright", "words"]
    first = words[0][1]
    # The words run left to right along the top of the box.
    assert first.x0 >= 20
    assert first.x0 < words[1][1].x0
    assert first.y0 >= 20
    assert first.y1 <= 50
    pixmap = page.pixmap(dpi=72)
    assert _pixel(pixmap, 150, 70) == (255, 255, 0)
    assert _pixel(pixmap, 20, 50) == (0, 0, 0)


def test_free_text_turns_and_aligns() -> None:
    page = pylopdf.Document().pages.new((300, 300))

    turned = page.annotations.add(FreeText((20, 20, 60, 280), "Down the side", rotate=90))
    centered = page.annotations.add(FreeText((100, 20, 280, 60), "Mid", align="center", size=20))

    assert turned.rotation == 90
    assert turned.obj["Rotate"] == 270
    assert centered.obj["Q"] == 1
    lines = [(line.text, line.direction) for block in page.layout().blocks for line in block.lines]
    assert ("Down the side", pytest.approx((0, 1), abs=1e-6)) in [(text, tuple(d)) for text, d in lines]
    (mid,) = [word for word in page.words() if word.text == "Mid"]
    assert abs((mid.bbox.x0 + mid.bbox.x1) / 2 - 190) < 3


def test_callouts_point_at_what_the_text_explains() -> None:
    page = pylopdf.Document().pages.new((300, 300))

    annotation = page.annotations.add(
        FreeText(
            (150, 150, 280, 200),
            "Look",
            callout=[Point(40, 250), (100, 220), (150, 175)],
            callout_ending=LineEnding.CLOSED_ARROW,
            border=Border(1),
        )
    )

    assert annotation.intent == "FreeTextCallout"
    # The rectangle holds the box, the line, and its ending.
    assert annotation.rect.contains(Rect(35, 150, 280, 255))
    assert len(annotation.obj["CL"]) == 6
    assert annotation.obj["LE"] == "ClosedArrow"
    pixmap = page.pixmap(dpi=72)
    assert _pixel(pixmap, 70, 235) != WHITE
    assert _pixel(pixmap, 100, 220) != WHITE


def test_free_text_survives_save_and_reopen() -> None:
    page = pylopdf.Document().pages.new((300, 300))
    page.annotations.add(
        FreeText(
            (20, 20, 200, 80),
            "Saved",
            font=Font.standard("Courier-Bold"),
            size=13,
            text_color=Color(0, 0, 1),
            opacity=0.5,
        )
    )

    (annotation,) = pylopdf.open(page.document.to_bytes()).pages[0].annotations

    assert annotation.contents == "Saved"
    assert annotation.obj["DA"].text == "/CoBo 13 Tf 0 0 1 rg"
    assert annotation.opacity == pytest.approx(0.5)
    assert "Saved" in pylopdf.open(page.document.to_bytes()).pages[0].text()


def test_free_text_validates_its_fields() -> None:
    text = FreeText((0, 0, 100, 50), "x")
    assert (text.size, text.align, text.rotate, text.callout, text.color) == (11.0, "left", 0, None, None)
    assert text.border == Border(0)
    assert FreeText((0, 0, 100, 50), "x", rotate=-90).rotate == 270
    for kwargs, error in [
        ({"size": 0}, ValueError),
        ({"align": "justify"}, ValueError),
        ({"rotate": 45}, ValueError),
        ({"callout": [(0, 0)]}, ValueError),
        ({"font": "Helvetica"}, TypeError),
        ({"text_color": (0, 0, 0)}, TypeError),
        ({"callout_ending": "wave"}, ValueError),
    ]:
        with pytest.raises(error):
            FreeText((0, 0, 100, 50), "x", **cast("dict[str, Any]", kwargs))
    with pytest.raises(TypeError, match="text must be a str"):
        FreeText((0, 0, 100, 50), cast("Any", None))
    with pytest.raises(pylopdf.LimitError) as raised:
        FreeText((0, 0, 100, 50), "x" * (1024 * 1024))
    assert raised.value.code == "annotation_input_size"


def test_text_that_does_not_fit_changes_nothing() -> None:
    page = pylopdf.Document().pages.new((300, 300))

    with pytest.raises(pylopdf.PdfError, match="does not fit"):
        page.annotations.add(FreeText((20, 20, 60, 30), "far too much text for so small a box", size=20))
    assert "Annots" not in page.obj


def test_the_core_repeats_the_free_text_checks() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 100))
    core = doc._doc  # noqa: SLF001
    base: dict[str, Any] = {"subtype": "FreeText", "rect": (0.0, 0.0, 90.0, 40.0), "contents": "x"}
    for spec, message in [
        ({"font_size": 0.0}, "font size"),
        ({"font_name": "Comic"}, "unknown Standard 14 font"),
        ({"align": 3}, "aligns left"),
        ({"rotate": 45}, "multiple of 90"),
        ({"callout": [(0.0, 0.0)]}, "2 or 3 finite points"),
        ({"callout_ending": "ClosedArrow"}, "needs a callout line"),
        ({"text_color": (2.0, 0.0, 0.0)}, "components from 0 to 1"),
    ]:
        with pytest.raises(pylopdf.PdfError, match=message):
            core.add_annotation(1, {**base, **spec})
    with pytest.raises(pylopdf.PdfError, match="belong to free text"):
        core.add_annotation(1, {"subtype": "Square", "rect": (0.0, 0.0, 9.0, 9.0), "font_size": 9.0})
    assert "Annots" not in doc.pages[0].obj


def test_rendering_draws_a_missing_free_text_appearance() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [4 0 R] >>",
                4: (
                    "<< /Type /Annot /Subtype /FreeText /Rect [10 10 190 90] /Contents (Drawn later) "
                    "/DA (/TiRo 18 Tf 1 0 0 rg) /C [0 0 1] >>"
                ),
            }
        )
    )
    before = doc.to_bytes()

    pixmap = doc.pages[0].pixmap(dpi=72)

    assert _pixel(pixmap, 100, 80) == (0, 0, 255)
    assert doc.pages[0].text().strip() == "Drawn later"
    assert doc.to_bytes() == before


@pytest.mark.parametrize(
    "default_appearance",
    ["(0 g /HeBo 0 Tf)", "(1 0 0 1 k)", "(garbage Tf rg)", "<FFFE>", "7"],
)
def test_odd_default_appearances_still_draw(default_appearance: str) -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [4 0 R] >>",
                4: (
                    "<< /Type /Annot /Subtype /FreeText /Rect [10 10 190 90] /Contents (Odd) "
                    f"/DA {default_appearance} >>"
                ),
            }
        )
    )

    assert doc.pages[0].text().strip() == "Odd"
