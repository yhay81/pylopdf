"""Page.replace_text edits text in the font that draws it, keeping the text after it in place."""

from __future__ import annotations

from pathlib import Path

import pytest
from redaction_corpus import COURIER, cid_codes, cid_font, form, page, to_unicode, type3_font

import pylopdf
from pylopdf import Font

NOTO_SANS_JP = (
    Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
)


def _chars(doc_page: pylopdf.Page) -> list[tuple[str, float, float]]:
    return [
        (char.text, round(char.origin.x, 3), round(char.origin.y, 3))
        for block in doc_page.layout(chars=True).blocks
        for line in block.lines
        for span in line.spans
        for char in span.chars
    ]


def _painted(doc_page: pylopdf.Page) -> str:
    return "".join(glyph.text or "" for run in doc_page.content().text_runs for glyph in run.glyphs)


def test_simple_font_text_is_replaced_and_the_rest_stays() -> None:
    doc = pylopdf.open(page("BT /F1 12 Tf 20 250 Td (Dear Ada, see you) Tj 0 -20 Td (Ada again) Tj ET"))
    doc_page = doc.pages[0]
    before = _chars(doc_page)

    assert doc_page.replace_text("Ada", "Al") == 2

    # The comma stays where it was, after the gap that the shorter name leaves.
    assert _painted(doc_page) == "Dear Al, see youAl again"
    after = _chars(doc_page)
    # ", see you" keeps its positions, and the second line keeps its start.
    tail = [entry for entry in before if entry[0] in ", seyou" and entry[2] == before[0][2]][-8:]
    assert [entry for entry in after if entry[2] == before[0][2]][-8:] == tail
    reopened = pylopdf.open(doc.to_bytes())
    assert "Al again" in reopened.pages[0].text()


def test_a_replacement_spans_kerned_strings() -> None:
    content = "BT /F1 12 Tf 20 250 Td [(Hel) -40 (lo w) 120 (orld) -30 (!)] TJ ET"
    doc = pylopdf.open(page(content))
    doc_page = doc.pages[0]
    before = _chars(doc_page)

    assert doc_page.replace_text("lo wor", "p") == 1

    assert _painted(doc_page) == "Helpld!"
    assert _chars(doc_page)[-3:] == before[-3:]


def test_text_in_other_fonts_and_courier() -> None:
    content = "BT /F2 12 Tf 20 250 Td (monospaced text) Tj ET"
    doc = pylopdf.open(page(content, "<< /Font << /F2 6 0 R >> >>", COURIER))
    doc_page = doc.pages[0]
    before = _chars(doc_page)

    assert doc_page.replace_text("mono", "duo") == 1

    assert _painted(doc_page) == "duospaced text"
    assert _chars(doc_page)[-4:] == before[-4:]


@pytest.mark.parametrize("vertical", [False, True], ids=["horizontal", "vertical"])
def test_cid_fonts_are_re_encoded_through_their_cmap(*, vertical: bool) -> None:
    alphabet = "機密情報公開文書"
    move = "150 290 Td" if vertical else "20 250 Td"
    content = f"BT /F1 20 Tf {move} {cid_codes('機密文書情報', alphabet)} Tj ET"
    doc = pylopdf.open(page(content, "<< /Font << /F1 6 0 R >> >>", cid_font(vertical=vertical), to_unicode(alphabet)))
    doc_page = doc.pages[0]
    before = _chars(doc_page)

    assert doc_page.replace_text("機密", "公開") == 1

    assert _painted(doc_page) == "公開文書情報"
    assert _chars(doc_page)[2:] == before[2:]


def test_type3_fonts_are_re_encoded() -> None:
    font, cmap, glyph = type3_font()
    doc = pylopdf.open(
        page("BT /F1 20 Tf 20 250 Td (abc def) Tj ET", "<< /Font << /F1 6 0 R >> >>", font, "<< >>", cmap, glyph)
    )
    doc_page = doc.pages[0]
    before = _chars(doc_page)

    assert doc_page.replace_text("abc", "xy") == 1

    assert _painted(doc_page) == "xydef"
    assert _chars(doc_page)[-3:] == before[-3:]


def test_text_that_the_font_cannot_draw_is_refused() -> None:
    alphabet = "機密情報"
    content = f"BT /F1 20 Tf 20 250 Td {cid_codes('機密', alphabet)} Tj ET"
    data = page(content, "<< /Font << /F1 6 0 R >> >>", cid_font(), to_unicode(alphabet))
    doc = pylopdf.open(data)
    with pytest.raises(pylopdf.PdfError, match="fallback_font"):
        doc.pages[0].replace_text("機密", "秘")
    assert doc.to_bytes() == pylopdf.open(data).to_bytes()


def test_a_fallback_font_draws_what_the_font_cannot() -> None:
    alphabet = "機密情報"
    content = f"BT /F1 20 Tf 20 250 Td {cid_codes('機密情報', alphabet)} Tj ET"
    doc = pylopdf.open(page(content, "<< /Font << /F1 6 0 R >> >>", cid_font(), to_unicode(alphabet)))
    doc_page = doc.pages[0]
    before = _chars(doc_page)

    assert doc_page.replace_text("機密", "秘", fallback_font=Font.from_file(NOTO_SANS_JP)) == 1

    chars = _chars(doc_page)
    assert "秘" in doc_page.text()
    assert [entry for entry in chars if entry[0] in "情報"] == before[2:]
    secret = next(entry for entry in chars if entry[0] == "秘")
    assert secret[1:] == pytest.approx(before[0][1:], abs=0.5)


def test_a_shared_form_is_copied_before_the_edit() -> None:
    data = page(
        "q 1 0 0 1 20 200 cm /Fm Do Q",
        "<< /XObject << /Fm 6 0 R >> /Font << /F1 4 0 R >> >>",
        form("BT /F1 12 Tf 0 0 Td (Shared words) Tj ET"),
        pages=2,
    )
    doc = pylopdf.open(data)

    assert doc.pages[0].replace_text("Shared", "Own") == 1

    assert doc.pages[0].text().split() == ["Own", "words"]
    assert doc.pages[1].text().split() == ["Shared", "words"]


def test_actual_text_around_the_old_glyphs_goes() -> None:
    content = "/Span << /ActualText (Hello) >> BDC BT /F1 12 Tf 20 250 Td (Hello) Tj ET EMC"
    doc = pylopdf.open(page(content))
    doc_page = doc.pages[0]

    assert doc_page.replace_text("Hello", "Howdy") == 1

    assert b"ActualText" not in doc_page.operations.to_bytes()
    assert doc_page.text().strip() == "Howdy"


def test_absent_text_and_inputs() -> None:
    data = page("BT /F1 12 Tf 20 250 Td (Hello) Tj ET")
    doc = pylopdf.open(data)
    assert doc.pages[0].replace_text("Bye", "Hi") == 0
    assert doc.to_bytes() == pylopdf.open(data).to_bytes()
    with pytest.raises(ValueError, match="at least 1"):
        doc.pages[0].replace_text("", "x")
    with pytest.raises(TypeError):
        doc.pages[0].replace_text(b"Hello", "x")  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.LimitError) as error:
        doc.pages[0].replace_text("Hello", "x" * 4092)
    assert error.value.code == "replacement_input_size"
    with pytest.raises(TypeError, match="OpenType"):
        doc.pages[0].replace_text("Hello", "x", fallback_font=Font.standard("Helvetica"))


def test_replacing_with_nothing_removes_the_text() -> None:
    doc = pylopdf.open(page("BT /F1 12 Tf 20 250 Td (keep drop keep) Tj ET"))
    doc_page = doc.pages[0]
    before = _chars(doc_page)

    assert doc_page.replace_text("drop ", "") == 1

    assert _painted(doc_page) == "keep keep"
    assert _chars(doc_page)[-4:] == before[-4:]
