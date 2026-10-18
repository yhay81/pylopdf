"""Tests for importing krilla-generated PDFs without lopdf's rayon dispatch."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf.pylopdf_core import _Document

NOTO_SANS_JP = (
    Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
)

pytestmark = pytest.mark.skipif(
    sys.platform == "emscripten", reason="Emscripten keeps lopdf's loader, which already runs on the calling thread"
)


def pdf_writer_document(
    objects: dict[int, bytes],
    trailer: bytes,
    *,
    header: bytes = b"%PDF-1.7\n%\x80\x80\x80\x80\n\n",
    generations: dict[int, int] | None = None,
) -> bytes:
    """Lay out objects, the free list, and the trailer as pdf-writer writes them."""
    generations = generations or {}
    out = bytearray(header)
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} {generations.get(number, 0)} obj\n".encode() + objects[number] + b"\nendobj\n"
    xref_offset = len(out)
    size = max(objects) + 1
    free = [number for number in range(size) if number not in objects]
    out += f"xref\n0 {size}\n".encode()
    for number in range(size):
        if number in objects:
            out += f"{offsets[number]:010} {generations.get(number, 0):05} n\r\n".encode()
        else:
            following = next((candidate for candidate in free if candidate > number), 0)
            out += f"{following:010} {65535 if number == 0 else 0:05} f\r\n".encode()
    out += b"trailer\n" + trailer + f"\nstartxref\n{xref_offset}\n%%EOF".encode()
    return bytes(out)


def stream(dictionary: bytes, data: bytes) -> bytes:
    """Return a pdf-writer stream object whose /Length leads the dictionary."""
    return b"<</Length " + str(len(data)).encode() + dictionary + b">>\nstream\n" + data + b"\nendstream"


PAGE_TREE = {
    1: b"<</Type/Catalog/Pages 2 0 R>>",
    2: b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
    3: b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595.5 842]/Resources<<>>>>",
}
TRAILER = b"<</Size 4/Root 1 0 R>>"

ACCEPTED = {
    "compact pdf-writer syntax": pdf_writer_document(PAGE_TREE, TRAILER),
    "every direct object kind": pdf_writer_document(
        {
            **PAGE_TREE,
            4: (
                b"<</Null null/True true/False false/Integers[0 -7 +8 2147483647 -9007199254740993]"
                b"/Reals[0.5 -12.25 .5 5. +3.0 -0.0 0.000001 999999999999.9]"
                b"/Names[/ /A#20B /#2F#25 /Caf#C3#A9 /a.b_c-d]"
                b"/Strings[() (plain) (\\\\ \\( \\) \\n \\r \\t \\b \\f) (\\101\\0\\7777) (bal(an)ced)"
                b" (line\\\ncontinued) (raw\r\nends\rhere\n) (unknown \\q escape)]"
                b"/Hex[<> <48656C6C6F> <4 8 6 5 6c 6C 6F> <ABC> <FEFF00E9>]"
                b"/References[3 0 R 2  0  R 1 0R]/Nested[[[]<<>>[<</Deep[1 2 3]>>]]]"
                b"/Duplicate 1/Duplicate 2>>"
            ),
        },
        b"<</Size 5/Root 1 0 R/ID[<00FF><A1b2C3>]>>",
    ),
    "streams with binary data": pdf_writer_document(
        {
            **PAGE_TREE,
            4: stream(b"/Filter/FlateDecode", bytes(range(256)) + b"\nendstream\nendobj\n"),
            5: stream(b"", b""),
            6: b"<</Length 3>>\nstream\r\nabc\r\nendstream",
            7: b"<</Length 3>> \t stream \t\nabcendstream",
        },
        b"<</Size 8/Root 1 0 R>>",
    ),
    "free entries and generations": pdf_writer_document(
        {1: PAGE_TREE[1], 2: PAGE_TREE[2], 3: PAGE_TREE[3], 7: b"[1 2]", 9: b"(gap)"},
        b"<</Size 10/Root 1 0 R>>",
        generations={7: 3},
    ),
    "corrected trailer size": pdf_writer_document(PAGE_TREE, b"<</Size 40/Root 1 0 R>>"),
    "no binary mark": pdf_writer_document(PAGE_TREE, TRAILER, header=b"%PDF-2.0\n"),
    "plain second-line comment": pdf_writer_document(PAGE_TREE, TRAILER, header=b"%PDF-1.4\n%ASCII\n"),
    "empty binary mark": pdf_writer_document(PAGE_TREE, TRAILER, header=b"%PDF-1.7\r\n%\r\n"),
    "trailing header bytes": pdf_writer_document(PAGE_TREE, TRAILER, header=b"%PDF-1.7 \x80\n"),
    "space-separated layout": build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [ 3 0 R ] /Count 1 >>",
            3: "<< /Type /Page >>",
        }
    ),
}


def replace_once(data: bytes, old: bytes, new: bytes) -> bytes:
    assert data.count(old) == 1
    return data.replace(old, new)


SIMPLE = pdf_writer_document({**PAGE_TREE, 4: b"<</Value 1>>"}, b"<</Size 5/Root 1 0 R>>")
DECLINED = {
    "comment between tokens": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value 1 %note\n>>"),
    "comment in reference": replace_once(SIMPLE, b"/Pages 2 0 R", b"/Pages 2 %c\n0 R"),
    "exponent": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value 1e5>>"),
    "unknown keyword": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value nil>>"),
    "keyword continues": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value truex>>"),
    "reference continues": replace_once(SIMPLE, b"/Pages 2 0 R", b"/Pages 2 0 Rx"),
    "reference out of range": replace_once(SIMPLE, b"/Pages 2 0 R", b"/Pages 4294967296 0 R"),
    "generation out of range": replace_once(SIMPLE, b"/Pages 2 0 R", b"/Pages 2 65536 R"),
    "integer out of range": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value 9223372036854775808>>"),
    "lone sign": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value ->>"),
    "missing value": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value>>"),
    "bad name escape": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value /A#G0>>"),
    "unterminated string": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value (open>>"),
    "bad hex digit": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value <4G>>>"),
    "deep nesting": replace_once(SIMPLE, b"<</Value 1>>", b"<</Value " + b"[" * 40 + b"]" * 40 + b">>"),
    "missing endobj": replace_once(SIMPLE, b"<</Value 1>>\nendobj", b"<</Value 1>>\n"),
    "indirect stream length": pdf_writer_document(
        {**PAGE_TREE, 4: b"<</Length 5 0 R>>\nstream\nabc\nendstream", 5: b"3"}, b"<</Size 6/Root 1 0 R>>"
    ),
    "wrong stream length": pdf_writer_document(
        {**PAGE_TREE, 4: b"<</Length 9>>\nstream\nabc\nendstream"}, b"<</Size 5/Root 1 0 R>>"
    ),
    "object stream": pdf_writer_document(
        {**PAGE_TREE, 4: stream(b"/Type/ObjStm/N 1/First 4", b"5 0 6")}, b"<</Size 5/Root 1 0 R>>"
    ),
    "previous section": replace_once(SIMPLE, b"<</Size 5/Root 1 0 R>>", b"<</Size 5/Root 1 0 R/Prev 9>>"),
    "encryption": replace_once(SIMPLE, b"<</Size 5/Root 1 0 R>>", b"<</Size 5/Root 1 0 R/Encrypt 4 0 R>>"),
    "second section": replace_once(SIMPLE, b"\r\ntrailer\n", b"\r\n7 1\r\n0000000000 00000 f\r\ntrailer\n"),
    "duplicate object": replace_once(SIMPLE, b"4 0 obj\n<</Value 1>>", b"3 0 obj\n<</Value 1>>"),
    "leading bytes": b"junk" + SIMPLE,
    "missing startxref": SIMPLE.replace(b"startxref", b"startxrfe"),
    "not a cross-reference table": replace_once(SIMPLE, b"\nxref\n", b"\nxrfe\n"),
}


@pytest.mark.parametrize("data", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_sequential_parser_matches_lopdf(data: bytes) -> None:
    assert _Document.compare_generated_pdf_load(data) is True


@pytest.mark.parametrize("data", DECLINED.values(), ids=DECLINED.keys())
def test_sequential_parser_declines_other_syntax(data: bytes) -> None:
    assert _Document.compare_generated_pdf_load(data) is None


def _form_pdf() -> bytes:
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Annots [5 0 R 6 0 R 7 0 R 8 0 R] >>",
            4: "<< /Fields [5 0 R 6 0 R 7 0 R 8 0 R] /DA (/Helv 0 Tf 0 g) >>",
            5: "<< /FT /Tx /T (name) /Type /Annot /Subtype /Widget /Rect [50 700 250 720] /P 3 0 R /F 4 >>",
            6: (
                "<< /FT /Tx /Ff 4096 /T (notes) /Type /Annot /Subtype /Widget"
                " /Rect [50 600 250 680] /P 3 0 R /F 4 /MK << /BG [1 1 0.8] /BC [0 0 1] >> >>"
            ),
            7: (
                "<< /FT /Tx /Ff 16777216 /MaxLen 4 /Q 1 /T (code) /Type /Annot /Subtype /Widget"
                " /Rect [50 560 130 580] /P 3 0 R /F 4 >>"
            ),
            8: (
                "<< /FT /Ch /Ff 131072 /T (level) /Opt [<FEFF521D7D1A> <FEFF4E0A7D1A>] /Type /Annot /Subtype /Widget"
                " /Rect [300 700 500 720] /P 3 0 R /F 4 /MK << /R 90 >> >>"
            ),
        }
    )


def test_generated_imports_parse_on_the_calling_thread() -> None:
    sequential, fallback, mismatched = _Document.generated_pdf_loads()
    with pylopdf.Document() as doc:
        doc.new_page(width=595, height=842)
        doc.new_page(width=842, height=595)
        doc[1].set_rotation(90)
        page = doc[0]
        page.insert_text((50, 100), "日本語テキスト (かっこ) \\ 100%", fontsize=14, fontfile=NOTO_SANS_JP)
        page.insert_textbox(
            (50, 150, 300, 400),
            "折り返して配置する日本語の段落です。" * 3,
            fontsize=12,
            fontbuffer=NOTO_SANS_JP.read_bytes(),
            align=pylopdf.TEXT_ALIGN_JUSTIFY,
        )
        doc[1].insert_text((40, 60), "回転したページ", fontsize=20, fontfile=NOTO_SANS_JP, color=(0.2, 0.4, 0.6))
        output = doc.tobytes()
    with pylopdf.open(stream=_form_pdf()) as doc:
        doc.set_form_field("name", "山田 太郎", fontfile=NOTO_SANS_JP)
        doc.set_form_field("notes", "一行目\n二行目の長い説明文", fontfile=NOTO_SANS_JP)
        doc.set_form_field("code", "日本", fontfile=NOTO_SANS_JP)
        doc.set_form_field("level", "上級", fontfile=NOTO_SANS_JP)
        form_output = doc.tobytes()
    assert _Document.generated_pdf_loads() == (sequential + 7, fallback, mismatched)
    with pylopdf.open(stream=output) as reopened:
        assert "日本語テキスト" in reopened[0].get_text()
        assert "回転したページ" in reopened[1].get_text()
    with pylopdf.open(stream=form_output) as reopened:
        values = {field["name"]: field["value"] for field in reopened.get_form_fields()}
    assert values["code"] == "日本"
    assert values["level"] == "上級"


def test_standard_fonts_import_no_generated_pdf() -> None:
    loads = _Document.generated_pdf_loads()
    with pylopdf.Document() as doc:
        doc.new_page().insert_text((50, 100), "Latin text")
    assert _Document.generated_pdf_loads() == loads
