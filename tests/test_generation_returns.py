"""pymupdf's return values of insert_image and show_pdf_page, and Standard 14 names."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf, drawn_images

import pylopdf


def _png() -> bytes:
    pixmap = pylopdf.open(build_pdf(["Pixels"])).pages[0].get_pixmap(matrix=pylopdf.Matrix(0.2, 0.2), alpha=True)
    return pixmap.tobytes()


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
    pixmap = page.get_pixmap(matrix=pylopdf.Matrix(0.1, 0.1), alpha=True)
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
    assert "Stamp" in pylopdf.open(target.to_bytes()).pages[1].get_text()


def test_base14_tables_match_pymupdf() -> None:
    assert pylopdf.Base14_fontnames == (
        "Courier",
        "Courier-Oblique",
        "Courier-Bold",
        "Courier-BoldOblique",
        "Helvetica",
        "Helvetica-Oblique",
        "Helvetica-Bold",
        "Helvetica-BoldOblique",
        "Times-Roman",
        "Times-Italic",
        "Times-Bold",
        "Times-BoldItalic",
        "Symbol",
        "ZapfDingbats",
    )
    assert pylopdf.Base14_fontdict["helv"] == "Helvetica"
    assert pylopdf.Base14_fontdict["times-bolditalic"] == "Times-BoldItalic"
    assert pylopdf.Base14_fontdict["zadb"] == "ZapfDingbats"
    assert len(pylopdf.Base14_fontdict) == 28
    with pytest.raises(TypeError):
        pylopdf.Base14_fontdict["new"] = "Helvetica"  # ty: ignore[invalid-assignment]


@pytest.mark.parametrize(
    ("fontname", "base_font"),
    [
        ("Helvetica", "Helvetica"),
        ("helv", "Helvetica"),
        ("Times-Roman", "Times-Roman"),
        ("TIMES-BOLD", "Times-Bold"),
        ("cobo", "Courier-Bold"),
        ("Courier-BoldOblique", "Courier-BoldOblique"),
    ],
)
def test_full_standard_14_names_draw_like_their_aliases(fontname: str, base_font: str) -> None:
    doc = pylopdf.Document()
    page = doc.pages.new()

    page.insert_text((40, 60), "Named", fontname=fontname)
    page.insert_textbox((40, 100, 300, 160), "Boxed", fontname=fontname)
    shape = page.new_shape()
    shape.insert_text((40, 200), "Shaped", fontname=fontname)
    shape.commit()

    data = doc.to_bytes()
    assert f"/BaseFont/{base_font}".encode() in data
    text = pylopdf.open(data).pages[0].get_text()
    assert all(word in text for word in ("Named", "Boxed", "Shaped"))


def test_symbol_fonts_draw_with_their_built_in_encoding() -> None:
    page = pylopdf.Document().pages.new()

    assert page.insert_textbox((40, 40, 300, 100), "abc", fontname="Symbol") > 0
    assert page.insert_text((40, 140), "abc", fontname="ZapfDingbats") == 1


def test_unknown_fontname_raises() -> None:
    page = pylopdf.Document().pages.new()

    with pytest.raises(pylopdf.PdfError, match="Standard 14 name or alias"):
        page.insert_text((40, 60), "x", fontname="Arial")
    with pytest.raises(ValueError, match="Standard 14 name or alias"):
        page.insert_textbox((40, 60, 200, 120), "x", fontname="figo")
