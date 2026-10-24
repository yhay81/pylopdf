"""Page.recolor and Document.recolor convert what pages draw to gray, RGB, or CMYK."""

from __future__ import annotations

import zlib

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Color, Colorspace

HELVETICA = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"


def _stream(content: str | bytes, dictionary: str = "") -> bytes:
    data = content.encode("latin-1") if isinstance(content, str) else content
    return b"<< %s /Length %d >>\nstream\n%s\nendstream" % (dictionary.encode(), len(data), data)


def _image() -> bytes:
    pixels = bytes([255, 0, 0, 0, 0, 255])
    return _stream(
        zlib.compress(pixels),
        "/Type /XObject /Subtype /Image /Width 2 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8"
        " /Filter /FlateDecode",
    )


def _document(pages: int = 1) -> bytes:
    content = (
        "1 0 0 rg 10 10 50 50 re f 0 0 1 RG 4 w 100 10 m 200 10 l S"
        " /CS0 cs 0 1 0 sc 10 100 50 50 re f"
        " 0.2 0.4 0.6 0.1 k 100 100 50 50 re f"
        " q 40 0 0 20 200 200 cm /Im Do Q"
        " q 20 0 0 10 10 200 cm BI /W 1 /H 1 /BPC 8 /CS /RGB ID \xff\x80\x00 EI Q"
        " /Sh sh"
    )
    shading = (
        "<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 250 300 250]"
        " /Function << /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >> >>"
    )
    kids = " ".join(f"{3 if index == 0 else 8 + index} 0 R" for index in range(pages))
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} /MediaBox [0 0 300 300] >>",
        3: (
            "<< /Type /Page /Parent 2 0 R /Contents 5 0 R /Resources << /Font << /F1 4 0 R >>"
            " /ColorSpace << /CS0 [/CalRGB << /WhitePoint [0.95 1 1.09] >>] >>"
            " /XObject << /Im 6 0 R >> /Shading << /Sh 7 0 R >> >> >>"
        ),
        4: HELVETICA,
        5: _stream(content),
        6: _image(),
        7: shading,
        8: "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    }
    for index in range(1, pages):
        objects[8 + index] = "<< /Type /Page /Parent 2 0 R /Contents 5 0 R /Resources << /XObject << /Im 6 0 R >> >> >>"
    return build_raw_pdf(objects)


def _is_gray(color: Color) -> bool:
    return abs(color.red - color.green) < 0.02 and abs(color.green - color.blue) < 0.02


def test_recolor_to_gray_converts_every_colour() -> None:
    doc = pylopdf.open(_document())
    page = doc.pages[0]

    page.recolor(Colorspace.GRAY)

    content = page.content()
    for path in content.paths:
        for paint in (path.fill, path.stroke.paint if path.stroke else None):
            if isinstance(paint, Color):
                assert _is_gray(paint), paint
    pixmap = page.pixmap(dpi=36)
    samples = bytes(pixmap)
    for index in range(0, len(samples), 3):
        red, green, blue = samples[index : index + 3]
        assert abs(red - green) <= 3, (index, red, green, blue)
        assert abs(green - blue) <= 3, (index, red, green, blue)
    for image in content.images:
        assert image.colorspace in ("DeviceGray", "gray") or image.components == 1
    reopened = pylopdf.open(doc.to_bytes())
    assert bytes(reopened.pages[0].pixmap(dpi=36)) == bytes(pixmap)


def test_recolor_to_cmyk_writes_cmyk_operators() -> None:
    doc = pylopdf.open(_document())
    page = doc.pages[0]

    page.recolor("cmyk")

    operators = {operation.operator for operation in page.operations}
    assert "k" in operators
    assert "rg" not in operators
    assert "RG" not in operators
    # Red stays red: CMYK (0, 1, 1, 0).
    (first,) = [operation for operation in page.operations if operation.operator == "k"][:1]
    assert first.operands == pytest.approx((0, 1, 1, 0))
    image = next(iter(page.content().images))
    assert image.components == 4


def test_page_recolor_keeps_other_pages() -> None:
    doc = pylopdf.open(_document(pages=2))
    before = bytes(doc.pages[1].pixmap(dpi=36))

    doc.pages[0].recolor("gray")

    assert bytes(doc.pages[1].pixmap(dpi=36)) == before
    assert doc.pages[0].content().images[0].xref != doc.pages[1].content().images[0].xref


def test_document_recolor_converts_shared_objects_once() -> None:
    doc = pylopdf.open(_document(pages=2))

    doc.recolor("gray")

    first, second = (page.content().images[0].xref for page in doc.pages)
    assert first == second
    samples = bytes(doc.pages[1].pixmap(dpi=36))
    assert all(
        abs(samples[index] - samples[index + 1]) <= 3 and abs(samples[index + 1] - samples[index + 2]) <= 3
        for index in range(0, len(samples), 3)
    )


def test_recolor_refuses_unknown_spaces() -> None:
    doc = pylopdf.open(_document())
    with pytest.raises(ValueError, match="cmyk"):
        doc.pages[0].recolor("lab")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="'gray' or 'rgb'"):
        doc.pages[0].pixmap(colorspace="cmyk")  # ty: ignore[invalid-argument-type]


def test_a_page_already_in_the_target_space_is_unchanged() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((100, 100))
    with page.draw() as canvas:
        canvas.rect((10, 10, 50, 50), fill=Color(0.5, 0.5, 0.5))
    contents = page.obj["Contents"]
    page.recolor("rgb")
    assert page.obj["Contents"] == contents
