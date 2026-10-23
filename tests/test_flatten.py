"""doc.flatten: annotation and widget appearances drawn into page content."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import (
    AnnotationFlags,
    CheckBox,
    Color,
    ComboBox,
    FreeText,
    Highlight,
    Ink,
    LineAnnotation,
    Note,
    Square,
    Stamp,
    TextField,
)


def _differing(before: pylopdf.Pixmap, after: pylopdf.Pixmap, tolerance: int = 8) -> int:
    """Count the samples that differ by more than ``tolerance``."""
    assert (before.width, before.height) == (after.width, after.height)
    return sum(abs(a - b) > tolerance for a, b in zip(bytes(before), bytes(after), strict=True))


def _annotated() -> pylopdf.Document:
    doc = pylopdf.Document()
    page = doc.pages.new((400, 400))
    page.annotations.add(Square((20, 20, 120, 80), interior_color=Color(0, 0, 1)))
    page.annotations.add(LineAnnotation((20, 100), (180, 140), color=Color(0, 0.5, 0)))
    page.annotations.add(Ink([[(200, 20), (260, 60), (320, 20)]]))
    page.annotations.add(FreeText((200, 100, 380, 140), "Flattened text", border=pylopdf.Border(1)))
    page.annotations.add(Highlight((20, 160, 180, 180)))
    page.annotations.add(Stamp((200, 160, 380, 220)))
    page.annotations.add(Note((20, 200), contents="note")).popup_rect = (40, 200, 140, 260)
    page.links.add((300, 300, 380, 320), "https://example.test")
    page.widgets.add(TextField("name", (20, 300, 180, 324), value="Ada Lovelace"))
    page.widgets.add(CheckBox("agree", (200, 300, 216, 316), checked=True))
    page.widgets.add(ComboBox("level", (20, 340, 180, 364), choices=["low", "high"], value="high"))
    return doc


def test_flattening_keeps_what_rendering_shows() -> None:
    doc = _annotated()
    page = doc.pages[0]
    before = page.pixmap(dpi=72)

    doc.flatten()

    assert [annotation.type for annotation in page.annotations] == []
    assert len(page.links) == 1
    assert [item["Subtype"] for item in page.obj["Annots"]] == ["Link"]
    assert len(page.widgets) == 0
    assert len(doc.form) == 0
    assert "AcroForm" not in doc.catalog
    assert _differing(before, page.pixmap(dpi=72)) < 40
    assert "Ada Lovelace" in page.text()
    assert "Flattened text" in page.text()

    reopened = pylopdf.open(doc.to_bytes())
    assert _differing(before, reopened.pages[0].pixmap(dpi=72)) < 40
    assert "Ada Lovelace" in reopened.pages[0].text()


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_flattening_follows_page_rotation(rotation: int) -> None:
    doc = _annotated()
    page = doc.pages[0]
    page.rotation = rotation
    before = page.pixmap(dpi=72)

    doc.flatten()

    assert _differing(before, page.pixmap(dpi=72)) < 40


def test_annotations_and_widgets_flatten_apart() -> None:
    doc = _annotated()
    doc.flatten(annotations=False)
    page = doc.pages[0]
    assert len(page.annotations) == 7
    assert len(page.widgets) == 0
    assert "AcroForm" not in doc.catalog

    doc = _annotated()
    doc.flatten(widgets=False)
    page = doc.pages[0]
    assert len(page.annotations) == 0
    assert len(page.widgets) == 3
    assert doc.form["name"].value == "Ada Lovelace"
    assert [item["Subtype"] for item in page.obj["Annots"]] == ["Link", "Widget", "Widget", "Widget"]

    doc = _annotated()
    before = doc.to_bytes()
    doc.flatten(annotations=False, widgets=False)
    assert doc.to_bytes() == before


def test_hidden_annotations_are_removed_undrawn() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 200))
    square = page.annotations.add(Square((20, 20, 120, 120), interior_color=Color(1, 0, 0)))
    square.flags = AnnotationFlags.HIDDEN

    doc.flatten()

    assert "Annots" not in page.obj
    assert set(bytes(page.pixmap(dpi=72))) == {255}


def test_missing_appearances_are_drawn_as_rendering_draws_them() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [4 0 R 5 0 R] >>",
                4: "<< /Type /Annot /Subtype /Square /Rect [10 10 90 90] /C [1 0 0] /IC [0 0 1] >>",
                5: "<< /Type /Annot /Subtype /Square /Rect [110 10 190 90] /C [0 1 0] >>",
            }
        )
    )
    page = doc.pages[0]
    before = page.pixmap(dpi=72)
    assert _differing(before, pylopdf.Document().pages.new((200, 100)).pixmap(dpi=72)) > 1000

    doc.flatten()

    assert "Annots" not in page.obj
    assert _differing(before, page.pixmap(dpi=72)) == 0


def test_appearance_boxes_and_matrices_map_onto_the_rectangle() -> None:
    # The appearance's BBox is off the origin and turned by its Matrix; the
    # flattened drawing lands on /Rect as ISO 32000-2 §12.5.5 maps it, which
    # MuPDF and pdf.js follow. hayro 0.8 leaves the scale out of the
    # translation, so its rendering of the unflattened page differs.
    stream = b"0 0 1 rg 10 20 40 10 re f 1 0 0 rg 10 20 10 10 re f"
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [4 0 R] >>",
                4: "<< /Type /Annot /Subtype /Square /Rect [50 50 70 130] /AP << /N 5 0 R >> >>",
                5: (
                    f"<< /Type /XObject /Subtype /Form /BBox [10 20 50 30] /Matrix [0 1 -1 0 0 0] "
                    f"/Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream"
                ),
            }
        )
    )
    page = doc.pages[0]

    doc.flatten()

    pixmap = page.pixmap(dpi=72)
    samples = bytes(pixmap)

    def pixel(x: int, y: int) -> bytes:
        offset = y * pixmap.stride + x * pixmap.channels
        return samples[offset : offset + 3]

    # The turned box fills /Rect, 20 wide and 80 tall, red at its bottom.
    assert pixel(60, 80) == b"\x00\x00\xff"
    assert pixel(60, 140) == b"\xff\x00\x00"
    assert pixel(45, 100) == pixel(75, 100) == pixel(60, 65) == pixel(60, 155) == b"\xff\xff\xff"


def test_flattening_draws_shared_annotations_on_every_page() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots 5 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R /Annots 5 0 R >>",
                5: "[6 0 R]",
                6: "<< /Type /Annot /Subtype /Square /Rect [10 10 90 90] /C [1 0 0] /IC [1 0 0] >>",
            }
        )
    )
    before = [page.pixmap(dpi=72) for page in doc.pages]

    doc.flatten()

    for page, pixmap in zip(doc.pages, before, strict=True):
        assert "Annots" not in page.obj
        assert _differing(pixmap, page.pixmap(dpi=72)) == 0


def test_drawing_stops_where_rendering_stops() -> None:
    # hayro stops reading /Annots at an entry that is no dictionary, so the
    # square after it is not drawn before flattening, and not after it.
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R 4 0 R] >>",
                4: "<< /Type /Annot /Subtype /Square /Rect [10 10 90 90] /C [1 0 0] /IC [1 0 0] >>",
                5: "42",
            }
        )
    )
    page = doc.pages[0]
    before = page.pixmap(dpi=72)

    doc.flatten()

    assert _differing(before, page.pixmap(dpi=72)) == 0
    assert list(page.obj["Annots"]) == [42]


def test_flatten_takes_booleans() -> None:
    doc = pylopdf.Document()
    with pytest.raises(TypeError, match="annotations is a bool"):
        doc.flatten(annotations=cast("Any", 1))
    doc.close()
    with pytest.raises(pylopdf.DocumentClosedError):
        doc.flatten()
