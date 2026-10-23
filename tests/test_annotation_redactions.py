"""Redaction annotations, which mark areas for page.redact to remove."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Color, Font, Rect, Redaction

WHITE = (255, 255, 255)


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * pixmap.channels
    red, green, blue = bytes(pixmap)[offset : offset + 3]
    return red, green, blue


@pytest.mark.parametrize("rotation", [0, 90])
def test_redactions_outline_and_cross_out_their_areas(rotation: int) -> None:
    page = pylopdf.Document().pages.new((300, 300))
    page.rotation = rotation

    redaction = page.annotations.add(Redaction([(20, 20, 120, 60), (20, 100, 120, 140)], overlay_text="X"))

    assert redaction.type is pylopdf.AnnotationType.REDACT
    assert redaction.rect == pytest.approx(Rect(20, 20, 120, 140), abs=1e-3)
    assert len(redaction.obj["QuadPoints"]) == 16
    pixmap = page.pixmap(dpi=72)
    # The outline and the diagonals are red; the areas stay visible.
    assert _pixel(pixmap, 70, 20)[0] > 200
    assert _pixel(pixmap, 70, 40)[0] > 200
    assert _pixel(pixmap, 70, 30) == WHITE
    assert _pixel(pixmap, 70, 80) == WHITE


def test_redaction_entries_survive_save_and_reopen() -> None:
    page = pylopdf.Document().pages.new((300, 300))
    page.annotations.add(
        Redaction(
            (20, 20, 120, 60),
            fill=Color(1, 1, 1),
            overlay_text="Removed",
            font=Font.standard("Times-Roman"),
            size=9,
            text_color=Color(1, 0, 0),
            align="right",
            contents="reason",
        )
    )

    (redaction,) = pylopdf.open(page.document.to_bytes()).pages[0].annotations

    assert redaction.obj["OverlayText"].text == "Removed"
    assert redaction.obj["DA"].text == "/TiRo 9 Tf 1 0 0 rg"
    assert redaction.obj["Q"] == 2
    assert redaction.interior_color == Color(1, 1, 1)
    assert redaction.color == Color(1, 0, 0)
    assert redaction.contents == "reason"


def test_redaction_specifications_validate() -> None:
    redaction = Redaction((0, 0, 10, 10))
    assert (redaction.fill, redaction.overlay_text, redaction.align) == (Color(0, 0, 0), None, "left")
    assert Redaction((0, 0, 10, 10), fill=None).fill is None
    with pytest.raises(TypeError, match="Standard 14"):
        Redaction((0, 0, 10, 10), font=cast("Any", "Helvetica"))
    with pytest.raises(TypeError, match="overlay_text"):
        Redaction((0, 0, 10, 10), overlay_text=cast("Any", 5))
    with pytest.raises(ValueError, match="align"):
        Redaction((0, 0, 10, 10), align=cast("Any", "justify"))
    with pytest.raises(pylopdf.LimitError) as raised:
        Redaction((0, 0, 10, 10), overlay_text="x" * (1024 * 1024))
    assert raised.value.code == "annotation_input_size"
    doc = pylopdf.Document()
    doc.pages.new((100, 100))
    core = doc._doc  # noqa: SLF001
    with pytest.raises(pylopdf.PdfError, match="overlay text to redactions"):
        core.add_annotation(1, {"subtype": "Square", "rect": (0.0, 0.0, 9.0, 9.0), "overlay_text": "x"})
    with pytest.raises(pylopdf.PdfError, match="rectangles place text markup"):
        core.add_annotation(1, {"subtype": "Redact", "rect": (0.0, 0.0, 9.0, 9.0)})


def test_rendering_draws_redactions_without_an_appearance() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [4 0 R] >>",
                4: "<< /Type /Annot /Subtype /Redact /Rect [10 10 190 90] /C [0 0 1] /Border [0 0 4] >>",
            }
        )
    )

    pixmap = doc.pages[0].pixmap(dpi=72)

    assert _pixel(pixmap, 100, 11) == (0, 0, 255)
    assert _pixel(pixmap, 100, 50) == (0, 0, 255)
    assert _pixel(pixmap, 100, 25) == WHITE
