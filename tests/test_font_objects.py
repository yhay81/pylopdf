"""Font and Form XObject inventories, font programs, and glyph widths."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from conftest import build_raw_pdf
from test_text_options import build_text_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"
HEBREW = Path(__file__).resolve().parent.parent / "fonts" / "pylopdf-fonts-he" / "src" / "pylopdf_fonts_he"


def test_page_fonts_of_a_type1_document_extract_their_programs() -> None:
    document = pylopdf.open(ASSETS / "usrguide.pdf")

    fonts = document.pages[0].resources.fonts

    assert fonts
    first = fonts[0]
    assert (first.subtype, first.is_embedded) == ("Type1", True)
    assert first.name.endswith("+SFRM1728")
    program = first.extract()
    assert program is not None
    assert program.format == "type1"
    assert program.data.startswith(b"%!")
    assert first == document.fonts[first.xref]
    assert first.obj["BaseFont"] == pylopdf.Name(first.name)
    assert len({font.xref for font in fonts}) == len(fonts)


def test_cff_programs_extract_as_cff() -> None:
    document = pylopdf.open(ASSETS / "f1040.pdf")

    programs = [font.extract() for font in document.pages[0].resources.fonts if font.is_embedded]

    assert {program.format for program in programs if program is not None} == {"cff"}
    assert None not in programs


def test_a_standard_font_that_is_not_embedded_has_afm_widths() -> None:
    document = pylopdf.open(build_text_pdf("BT /F1 20 Tf 72 700 Td (Ax) Tj ET"))

    (font,) = document.pages[0].resources.fonts

    assert (font.name, font.subtype, font.encoding, font.is_embedded) == ("Helvetica", "Type1", None, False)
    assert font.extract() is None
    widths = font.widths
    assert len(widths) == 256
    assert widths[ord("A")] == pytest.approx(0.667)
    assert widths[ord("x")] == pytest.approx(0.5)
    # The widths are the advances that extraction moves by.
    chars = document.pages[0].layout(chars=True).blocks[0].lines[0].spans[0].chars
    assert chars[1].origin.x - chars[0].origin.x == pytest.approx(20 * widths[ord("A")], abs=1e-3)


def test_an_embedded_opentype_font_is_a_type0_font_with_two_byte_widths() -> None:
    font = pylopdf.Font.from_file(HEBREW / "NotoSansHebrew-Variable.ttf")
    source = pylopdf.Document()
    with source.pages.new().draw() as canvas:
        canvas.text("Wide", (72, 100), font=font, size=20)
    document = pylopdf.open(source.to_bytes())

    (embedded,) = document.pages[0].resources.fonts

    assert (embedded.subtype, embedded.encoding, embedded.is_embedded) == ("Type0", "Identity-H", True)
    program = embedded.extract()
    assert program is not None
    assert program.format == "truetype"
    assert len(embedded.widths) == 65_536
    chars = document.pages[0].layout(chars=True).blocks[0].lines[0].spans[0].chars
    advances = [b.origin.x - a.origin.x for a, b in itertools.pairwise(chars)]
    # Each glyph moves the pen by the width of its code.
    widths = {round(width, 4) for width in embedded.widths}
    assert advances
    assert all(round(advance / 20, 4) in widths for advance in advances)


def test_a_type3_font_is_embedded_without_a_program() -> None:
    document = pylopdf.open(ASSETS / "pdfium-type3.pdf")

    fonts = [font for font in document.fonts.values() if font.subtype == "Type3"]

    assert fonts
    assert all(font.is_embedded and font.extract() is None for font in fonts)
    assert all(len(font.widths) == 256 for font in fonts)


def _forms_pdf() -> bytes:
    inner = "BT /F1 12 Tf 0 0 Td (inner) Tj ET"
    outer = "/Inner Do"
    page = "/Outer Do /Twice Do"
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
                "/Resources << /XObject << /Outer 5 0 R /Twice 5 0 R >> >> >>"
            ),
            4: f"<< /Length {len(page)} >>\nstream\n{page}\nendstream",
            5: (
                "<< /Type /XObject /Subtype /Form /BBox [0 0 200 100] /Matrix [1 0 0 1 50 60] "
                f"/Resources << /XObject << /Inner 6 0 R >> >> /Length {len(outer)} >>\nstream\n{outer}\nendstream"
            ),
            6: (
                "<< /Type /XObject /Subtype /Form /BBox [10 20 0 5] "
                f"/Resources << /Font << /F1 7 0 R >> >> /Length {len(inner)} >>\nstream\n{inner}\nendstream"
            ),
            7: "<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        }
    )


def test_page_forms_and_the_fonts_they_reach_are_listed_once_depth_first() -> None:
    document = pylopdf.open(_forms_pdf())
    page = document.pages[0]

    forms = page.resources.forms
    fonts = page.resources.fonts

    assert [form.xref for form in forms] == [5, 6]
    assert forms[0].bbox == pylopdf.Rect(0, 0, 200, 100)
    assert forms[0].matrix == pylopdf.Matrix(1, 0, 0, 1, 50, 60)
    assert forms[1].bbox == pylopdf.Rect(0, 5, 10, 20)
    assert forms[1].matrix == pylopdf.Matrix.IDENTITY
    assert [(font.xref, font.name, font.encoding) for font in fonts] == [(7, "Courier", "WinAnsiEncoding")]
    assert list(document.fonts) == [7]
    assert 7 in document.fonts
    assert 6 not in document.fonts
    with pytest.raises(KeyError):
        document.fonts[6]


def test_font_handles_go_stale_when_objects_are_renumbered() -> None:
    document = pylopdf.open(_forms_pdf())
    (font,) = document.pages[0].resources.fonts
    (outer, _) = document.pages[0].resources.forms

    document.to_bytes(renumber=True)

    with pytest.raises(pylopdf.StaleObjectError):
        _ = font.name
    with pytest.raises(pylopdf.StaleObjectError):
        _ = outer.bbox


def test_font_programs_extract_within_a_size_limit() -> None:
    document = pylopdf.open(ASSETS / "usrguide.pdf")
    font = document.pages[0].resources.fonts[0]

    with pytest.raises(pylopdf.LimitError) as error:
        font.extract(max_size=100)

    assert error.value.code == "font_program_size"
    assert font.extract(max_size=None) == font.extract()
    with pytest.raises(ValueError, match="max_size"):
        font.extract(max_size=0)


def test_a_page_that_lists_too_many_fonts_is_refused() -> None:
    count = 4_097
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << "
            + " ".join(f"/F{index} {index + 4} 0 R" for index in range(count))
            + " >> >> >>"
        ),
        4: "<< /Length 0 >>\nstream\n\nendstream",
    }
    for index in range(count):
        objects[index + 4] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    document = pylopdf.open(build_raw_pdf(objects))

    with pytest.raises(pylopdf.LimitError) as error:
        _ = document.pages[0].resources.fonts

    assert error.value.code == "font_inventory_count"
    assert len(document.fonts) == count
