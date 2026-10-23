"""pymupdf's return values of insert_image and show_pdf_page, and Standard 14 font names."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import build_pdf, drawn_images

import pylopdf

if TYPE_CHECKING:
    from pylopdf import StandardFontName


def _png() -> bytes:
    pixmap = pylopdf.open(build_pdf(["Pixels"])).pages[0].pixmap(matrix=pylopdf.Matrix(0.2, 0.2), alpha=True)
    return pixmap.to_bytes()


def test_insert_image_returns_the_image_xref(tmp_path: Path) -> None:
    doc = pylopdf.open(build_pdf(["Target"]))
    page = doc.pages[0]
    data = _png()
    path = tmp_path / "image.png"
    path.write_bytes(data)

    xref = page.insert_image((10, 10, 110, 110), stream=data)

    image = doc.objects[xref]
    assert isinstance(image, pylopdf.Stream)
    assert image["Subtype"] == "Image"
    # Identical input places the same XObject and returns its xref.
    assert page.insert_image((120, 10, 220, 110), stream=data) == xref
    assert page.insert_image((10, 120, 110, 220), filename=path) == xref
    pixmap = page.pixmap(matrix=pylopdf.Matrix(0.1, 0.1), alpha=True)
    pixmap_xref = page.insert_image((120, 120, 220, 220), pixmap=pixmap)
    assert pixmap_xref != xref
    assert doc.objects[pixmap_xref]["Subtype"] == "Image"
    reopened = pylopdf.open(doc.to_bytes())
    assert len(drawn_images(reopened.pages[0])) == 4


def test_show_pdf_page_returns_the_form_xref() -> None:
    target = pylopdf.open(build_pdf(["Target", "Second"]))
    source = pylopdf.open(build_pdf(["Stamp"]))

    xref = target.pages[0].show_pdf_page((0, 0, 200, 100), source)

    form = target.objects[xref]
    assert isinstance(form, pylopdf.Stream)
    assert form["Subtype"] == "Form"
    assert target.pages[1].show_pdf_page((0, 0, 200, 100), source) == xref
    self_xref = target.pages[1].show_pdf_page((0, 100, 200, 200), target, 0)
    assert target.objects[self_xref]["Subtype"] == "Form"
    assert "Stamp" in pylopdf.open(target.to_bytes()).pages[1].text()


@pytest.mark.parametrize(
    "name",
    ["Helvetica", "Times-Roman", "Times-Bold", "Courier-Bold", "Courier-BoldOblique"],
)
def test_standard_14_fonts_draw_under_their_postscript_names(name: StandardFontName) -> None:
    doc = pylopdf.Document()
    page = doc.pages.new()
    font = pylopdf.Font.standard(name)

    with page.draw() as canvas:
        canvas.text("Named", (40, 60), font=font)
        assert canvas.textbox((40, 100, 300, 160), "Boxed", font=font).fits

    data = doc.to_bytes(compress=False)
    assert f"/BaseFont/{name}".encode() in data
    text = pylopdf.open(data).pages[0].text()
    assert all(word in text for word in ("Named", "Boxed"))


def test_symbol_fonts_draw_with_their_built_in_encoding() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new()

    with page.draw() as canvas:
        assert canvas.textbox((40, 40, 300, 100), "abc", font=pylopdf.Font.standard("Symbol")).fits
        canvas.text("abc", (40, 140), font=pylopdf.Font.standard("ZapfDingbats"))

    data = doc.to_bytes(compress=False)
    assert b"/BaseFont/Symbol" in data
    assert b"/BaseFont/ZapfDingbats" in data
    assert b"WinAnsiEncoding" not in data
