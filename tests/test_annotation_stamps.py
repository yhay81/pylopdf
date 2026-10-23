"""Stamp and Caret annotations."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Caret, Color, Point, Rect, Stamp, StampIcon

WHITE = (255, 255, 255)


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * pixmap.channels
    red, green, blue = bytes(pixmap)[offset : offset + 3]
    return red, green, blue


@pytest.mark.parametrize("icon", list(StampIcon))
def test_every_stamp_draws_its_label(icon: StampIcon) -> None:
    page = pylopdf.Document().pages.new((300, 100))

    stamp = page.annotations.add(Stamp((10, 10, 290, 70), icon=icon))

    assert stamp.type is pylopdf.AnnotationType.STAMP
    assert stamp.icon == "".join(part.capitalize() for part in icon.value.split("_"))
    assert stamp.rect == Rect(10, 10, 290, 70)
    label = icon.value.replace("_", " ").upper()
    assert page.text().strip() == label


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_stamps_read_upright_and_turn_as_displayed(rotation: int) -> None:
    page = pylopdf.Document().pages.new((300, 300))
    page.rotation = rotation

    upright = page.annotations.add(Stamp((10, 10, 200, 60), icon=StampIcon.FINAL))
    turned = page.annotations.add(Stamp((240, 20, 290, 280), icon=StampIcon.DRAFT, rotate=90))
    caret = page.annotations.add(Caret((10, 250)))

    assert (upright.rotation, turned.rotation, caret.rotation) == (0, 90, 0)
    lines = {line.text: tuple(line.direction) for block in page.layout().blocks for line in block.lines}
    assert lines["FINAL"] == pytest.approx((1, 0), abs=1e-6)
    assert lines["DRAFT"] == pytest.approx((0, 1), abs=1e-6)
    pixmap = page.pixmap(dpi=72)
    # The caret points up: its base and apex are filled, its top corners not.
    assert _pixel(pixmap, 20, 266) == (0, 0, 255)
    red, _, blue = _pixel(pixmap, 20, 254)
    assert blue == 255
    assert red < 128
    assert _pixel(pixmap, 13, 253) == WHITE
    assert _pixel(pixmap, 27, 253) == WHITE


def test_stamps_and_carets_survive_save_and_reopen() -> None:
    page = pylopdf.Document().pages.new((300, 200))
    page.annotations.add(Stamp((10, 10, 200, 60), icon=StampIcon.TOP_SECRET, color=Color(0, 0, 0), opacity=0.5))
    page.annotations.add(Caret(Point(250, 20), color=Color(1, 0, 0), contents="insert here"))

    stamp, caret = pylopdf.open(page.document.to_bytes()).pages[0].annotations

    assert stamp.icon == "TopSecret"
    assert stamp.color == Color(0, 0, 0)
    assert stamp.opacity == pytest.approx(0.5)
    assert caret.type is pylopdf.AnnotationType.CARET
    assert caret.contents == "insert here"
    assert caret.rect == Rect(250, 20, 270, 40)
    assert _pixel(pylopdf.open(page.document.to_bytes()).pages[0].pixmap(dpi=72), 260, 38) == (255, 0, 0)


def test_stamp_and_caret_specifications_validate() -> None:
    assert Stamp((0, 0, 10, 10)).icon is StampIcon.APPROVED
    assert Stamp((0, 0, 10, 10), icon=cast("Any", "sold")).icon is StampIcon.SOLD
    assert Stamp((0, 0, 10, 10), rotate=-90).rotate == 270
    with pytest.raises(ValueError, match="'stamped'"):
        Stamp((0, 0, 10, 10), icon=cast("Any", "stamped"))
    with pytest.raises(ValueError, match="multiple of 90"):
        Stamp((0, 0, 10, 10), rotate=30)
    with pytest.raises(TypeError, match="color must be a Color"):
        Caret((0, 0), color=cast("Any", "red"))
    with pytest.raises(pylopdf.LimitError):
        Caret((0, 0), contents="x" * (1024 * 1024))
    core = pylopdf.Document()
    core.pages.new((100, 100))
    with pytest.raises(pylopdf.PdfError, match="unknown stamp"):
        core._doc.add_annotation(1, {"subtype": "Stamp", "rect": (0.0, 0.0, 9.0, 9.0), "icon": "Rubber"})  # noqa: SLF001
    with pytest.raises(pylopdf.PdfError, match="does not turn"):
        core._doc.add_annotation(1, {"subtype": "Square", "rect": (0.0, 0.0, 9.0, 9.0), "rotate": 90})  # noqa: SLF001


def test_rendering_draws_missing_stamp_appearances() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Annots [4 0 R 5 0 R] >>",
                4: "<< /Type /Annot /Subtype /Stamp /Rect [10 10 200 90] /Name /Expired >>",
                5: "<< /Type /Annot /Subtype /Stamp /Rect [210 10 290 90] /Name /#53pecial >>",
            }
        )
    )

    assert doc.pages[0].text().split() == ["EXPIRED", "SPECIAL"]
