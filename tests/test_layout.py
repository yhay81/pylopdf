"""Tests for positioned text extraction and search."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_nonembedded_cjk_pdf, build_pdf, build_raw_pdf, build_visual_rtl_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def build_vertical_cjk_columns_pdf() -> bytes:
    """Build two Japanese vertical columns with horizontal page furniture."""

    def octal(text: str) -> str:
        return "".join(f"\\{byte:03o}" for byte in text.encode("cp932"))

    stream = (
        "BT /F2 18 Tf 40 750 Td (Heading) Tj ET\n"
        f"BT /F1 24 Tf 200 720 Td ({octal('右側列')}) Tj ET\n"
        f"BT /F1 24 Tf 100 720 Td ({octal('左側列')}) Tj ET\n"
        "BT /F2 18 Tf 40 20 Td (Footer) Tj ET"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R /F2 8 0 R >> >> >>"
            ),
            3: (
                "<< /Type /Font /Subtype /Type0 /BaseFont /MS-Mincho /Encoding /90ms-RKSJ-V /DescendantFonts [6 0 R] >>"
            ),
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            6: (
                "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /MS-Mincho "
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (Japan1) /Supplement 6 >> "
                "/FontDescriptor 7 0 R /DW 1000 >>"
            ),
            7: (
                "<< /Type /FontDescriptor /FontName /MS-Mincho /Flags 6 "
                "/FontBBox [0 -137 1000 859] /ItalicAngle 0 /Ascent 859 "
                "/Descent -140 /CapHeight 769 /StemV 78 >>"
            ),
            8: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        }
    )


@pytest.fixture
def usrguide() -> pylopdf.Page:
    return pylopdf.open(ASSETS / "usrguide.pdf").pages[0]


def test_words_have_positions_and_numbering(usrguide: pylopdf.Page) -> None:
    words = usrguide.words()
    assert len(words) > 100
    page_rect = usrguide.rect
    for word in words:
        x0, y0, x1, y1 = word.bbox
        assert x0 < x1
        assert y0 < y1
        assert -1 <= x0 <= page_rect.x1 + 1
        assert -1 <= y0 <= page_rect.y1 + 1
        assert word.text
        assert word.block >= 0
        assert word.line >= 0
        assert word.index >= 0
    texts = [w.text for w in words]
    assert "authors" in texts


def test_words_numbering_is_consistent(usrguide: pylopdf.Page) -> None:
    """Keep word numbers consecutive per line and line numbers monotonic."""
    words = usrguide.words()
    seen: dict[tuple[int, int], int] = {}
    for word in words:
        key = (word.block, word.line)
        expected = seen.get(key, 0)
        assert word.index == expected
        seen[key] = expected + 1


def test_blocks_structure(usrguide: pylopdf.Page) -> None:
    blocks = usrguide.layout().blocks
    assert 1 < len(blocks) < 30
    assert all(isinstance(block, pylopdf.Block) for block in blocks)
    assert all(block.text == "\n".join(line.text for line in block.lines) for block in blocks)
    joined = "\n".join(b.text for b in blocks)
    assert "for authors" in joined
    assert "Introduction" in joined


def test_dict_structure(usrguide: pylopdf.Page) -> None:
    d = usrguide.layout().to_dict()
    assert d["width"] == pytest.approx(usrguide.rect.width, abs=1.0)
    assert d["height"] == pytest.approx(usrguide.rect.height, abs=1.0)
    block = d["blocks"][0]
    assert block["type"] == 0
    assert len(block["bbox"]) == 4
    line = block["lines"][0]
    assert line["wmode"] == 0
    assert line["dir"] == pytest.approx((1.0, 0.0))
    span = line["spans"][0]
    assert set(span) == {"bbox", "origin", "size", "font", "flags", "text"}
    assert span["size"] > 1.0
    all_text = "".join(span["text"] for b in d["blocks"] for line in b["lines"] for span in line["spans"])
    assert "authors" in all_text


def test_layout_chars_must_be_a_boolean(usrguide: pylopdf.Page) -> None:
    with pytest.raises(TypeError, match="chars must be True or False"):
        usrguide.layout(chars=1)  # ty: ignore[invalid-argument-type]


def test_text_records_hold_checked_geometry_and_compare_as_constructed(usrguide: pylopdf.Page) -> None:
    """The fast record paths give the values that the public constructors would."""
    layout = usrguide.layout(chars=True)
    spans = [span for block in layout.blocks for line in block.lines for span in line.spans]
    chars = [char for span in spans for char in span.chars]
    boxes = [word.bbox for word in usrguide.words()] + [span.bbox for span in spans] + [char.bbox for char in chars]
    points = [span.origin for span in spans] + [char.origin for char in chars]

    assert chars
    assert {type(box) for box in boxes} == {pylopdf.Rect}
    assert {type(point) for point in points} == {pylopdf.Point}
    assert boxes == [pylopdf.Rect(*box) for box in boxes]
    assert points == [pylopdf.Point(*point) for point in points]
    rebuilt = [pylopdf.Char(text=char.text, bbox=char.bbox, origin=char.origin) for char in chars]
    assert rebuilt == chars
    assert {hash(char) for char in rebuilt} == {hash(char) for char in chars}
    assert repr(rebuilt[0]) == repr(chars[0])
    assert [span.text for span in usrguide.layout().blocks[0].lines[0].spans] == [
        span.text for span in layout.blocks[0].lines[0].spans
    ]


def test_document_level_get_page_text_modes() -> None:
    doc = pylopdf.open(build_pdf(["Alpha beta", "Gamma"]))
    words = doc.pages[0].words()
    assert [w.text for w in words] == ["Alpha", "beta"]
    assert doc.pages[1].words()[0].text == "Gamma"


def test_search_for_basic(usrguide: pylopdf.Page) -> None:
    hits = usrguide.search("authors")
    assert len(hits) >= 1
    rect = hits[0]
    assert isinstance(rect, pylopdf.Rect)
    assert rect.x0 < rect.x1
    assert rect.y0 < rect.y1


def test_search_for_is_case_insensitive(usrguide: pylopdf.Page) -> None:
    assert usrguide.search("AUTHORS") == usrguide.search("authors")


def test_search_for_multiword(usrguide: pylopdf.Page) -> None:
    """Search across words joined by a synthesized space."""
    hits = usrguide.search("for authors")
    assert len(hits) == 1
    only_for = usrguide.search("for")[0]
    assert hits[0].x0 <= only_for.x1


def test_search_for_no_match(usrguide: pylopdf.Page) -> None:
    assert usrguide.search("zzzz-not-in-document") == []


def test_search_for_empty_raises(usrguide: pylopdf.Page) -> None:
    with pytest.raises(ValueError, match="text must be"):
        usrguide.search("")


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_search_for_validates_hit_budget(usrguide: pylopdf.Page, value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="max_hits"):
        usrguide.search("authors", max_hits=value)  # ty: ignore[invalid-argument-type]


def test_search_for_bounds_utf8_input_before_core_copy(usrguide: pylopdf.Page) -> None:
    assert usrguide.search("é" * 2048) == []

    with pytest.raises(pylopdf.LimitError) as caught:
        usrguide.search("é" * 2049)

    assert caught.value.code == "search_input_size"


def test_search_for_bounds_hits_without_returning_partial_results() -> None:
    doc = pylopdf.Document()
    doc.pages.new((1_000_000, 100))
    page = doc.pages[0]
    page.insert_text((10, 50), "a" * 4097, max_text_size=4097)

    with pytest.raises(pylopdf.LimitError) as caught:
        page.search("a")

    assert caught.value.code == "search_hit_count"
    assert len(page.search("a", max_hits=4097)) == 4097
    assert len(page.search("a", max_hits=None)) == 4097


def test_search_for_cjk() -> None:
    doc = pylopdf.open(ASSETS / "mhlw-doc.pdf")
    hits = doc.pages[0].search("裁判例")
    assert len(hits) >= 3
    for rect in hits:
        assert rect.x0 < rect.x1
        assert rect.y0 < rect.y1


def test_search_for_nonembedded_cjk() -> None:
    """Search non-embedded CJK text whose Unicode comes from the CMap."""
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {}
    assert len(doc.pages[0].search("日本語")) == 1


def test_layout_reflects_edits() -> None:
    """Reflect the new document state in layout extraction after select."""
    doc = pylopdf.open(build_pdf(["First page", "Second page"]))
    doc.pages.select([1])
    words = doc.pages[0].words()
    assert [w.text for w in words] == ["Second", "page"]


def test_cached_text_page_reflects_drawing_edit() -> None:
    """Invalidate reusable interpretation after a non-structural page edit."""
    doc = pylopdf.open(build_pdf(["Before"]))
    page = doc.pages[0]
    assert "Before" in page.text()
    assert page.search("After") == []

    page.insert_text((72, 120), "After")

    assert "After" in page.text()
    assert len(page.search("After")) == 1


def test_dict_reports_transformed_baseline_direction() -> None:
    """Assemble a rotated baseline without mistaking it for vertical WMode."""
    stream = "BT /F1 24 Tf 0 1 -1 0 100 250 Tm (Vertical) Tj ET"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 300 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )
    page = pylopdf.open(pdf).pages[0]
    assert page.text() == "Vertical\n"
    assert [word.text for word in page.words()] == ["Vertical"]
    assert len(page.search("Vertical")) == 1

    line = page.layout().to_dict()["blocks"][0]["lines"][0]
    direction_x, direction_y = line["dir"]
    assert direction_x == pytest.approx(0.0, abs=1e-9)
    assert abs(direction_y) == pytest.approx(1.0)
    assert line["wmode"] == 0
    assert line["spans"][0]["text"] == "Vertical"


def test_vertical_cjk_writing_mode_and_search() -> None:
    """Infer vertical WMode from conservative CJK glyph geometry."""
    pdf = build_nonembedded_cjk_pdf().replace(b"/90ms-RKSJ-H", b"/90ms-RKSJ-V")
    page = pylopdf.open(pdf).pages[0]

    assert page.text() == "こんにちは日本語\n"
    assert [word.text for word in page.words()] == ["こんにちは日本語"]
    assert len(page.search("日本語")) == 1

    line = page.layout().to_dict()["blocks"][0]["lines"][0]
    assert line["dir"] == pytest.approx((0.0, 1.0))
    assert line["wmode"] == 1
    assert line["spans"][0]["text"] == "こんにちは日本語"


def test_horizontal_cjk_is_not_misclassified_as_vertical() -> None:
    """Keep ordinary CJK rows in horizontal writing mode."""
    page = pylopdf.open(build_nonembedded_cjk_pdf()).pages[0]
    line = page.layout().to_dict()["blocks"][0]["lines"][0]

    assert page.text() == "こんにちは日本語\n"
    assert line["dir"] == pytest.approx((1.0, 0.0))
    assert line["wmode"] == 0


def test_rtl_lines_use_logical_order_across_text_apis() -> None:
    """Restore pure RTL and one embedded token without guessing ambiguous lines."""
    hebrew_visual = "0001000200030004000500010003000200060007"
    arabic_visual = "0010001100120013001100120014000500120014001500160010"
    embedded_number_visual = "0001000200030004000500310032003300050001000300020006"
    embedded_latin_visual = "0001000200030004000500500044004600050001000300020006"
    embedded_separator_visual = "0001000200030004000500310032002D0033003100050001000300020006"
    embedded_descending_visual = "0001000200030004000500330032003100050001000300020006"
    ambiguous_visual = "00010002000300040005005000440046000500310032003300050001000300020006"
    stream = (
        f"BT /F1 20 Tf 40 330 Td <{hebrew_visual}> Tj ET\n"
        f"BT /F1 20 Tf 40 285 Td <{arabic_visual}> Tj ET\n"
        f"BT /F1 20 Tf 40 240 Td <{embedded_number_visual}> Tj ET\n"
        f"BT /F1 20 Tf 40 195 Td <{embedded_latin_visual}> Tj ET\n"
        f"BT /F1 20 Tf 40 150 Td <{embedded_separator_visual}> Tj ET\n"
        f"BT /F1 20 Tf 40 105 Td <{embedded_descending_visual}> Tj ET\n"
        f"BT /F1 20 Tf 40 60 Td <{ambiguous_visual}> Tj ET"
    )
    page = pylopdf.open(build_visual_rtl_pdf(stream, height=360)).pages[0]
    hebrew = "שְלום עולם"
    arabic = "مرحبا بالعالم"
    embedded_number = "שלום 123 עולם"
    embedded_latin = "שלום PDF עולם"
    embedded_separator = "שלום 12-31 עולם"
    embedded_descending = "שלום 321 עולם"
    untouched_ambiguous = "םלוע PDF 123 םולש"

    assert page.text().splitlines() == [
        hebrew,
        arabic,
        embedded_number,
        embedded_latin,
        embedded_separator,
        embedded_descending,
        untouched_ambiguous,
    ]
    assert page.document.text() == (
        f"{hebrew}\n{arabic}\n{embedded_number}\n{embedded_latin}\n{embedded_separator}\n"
        f"{embedded_descending}\n{untouched_ambiguous}\n"
    )

    words = page.words()
    assert [word.text for word in words] == [
        "שְלום",
        "עולם",
        "مرحبا",
        "بالعالم",
        "שלום",
        "123",
        "עולם",
        "שלום",
        "PDF",
        "עולם",
        "שלום",
        "12-31",
        "עולם",
        "שלום",
        "321",
        "עולם",
        "םלוע",
        "PDF",
        "123",
        "םולש",
    ]
    assert words[0].bbox.x0 > words[1].bbox.x0
    assert words[2].bbox.x0 > words[3].bbox.x0

    lines = [line for block in page.layout().to_dict()["blocks"] for line in block["lines"]]
    assert [span["text"] for line in lines for span in line["spans"]] == [
        hebrew,
        arabic,
        embedded_number,
        embedded_latin,
        embedded_separator,
        embedded_descending,
        untouched_ambiguous,
    ]
    assert len(page.search("שְלום")) == 1
    assert len(page.search("بالعالم")) == 1
    assert len(page.search("123")) == 2
    assert len(page.search("PDF")) == 2
    assert len(page.search("12-31")) == 1
    assert len(page.search("שלום")) == 4

    markdown = page.markdown()
    assert hebrew in markdown
    assert arabic in markdown
    assert embedded_number in markdown
    assert embedded_latin in markdown
    assert embedded_separator in markdown
    assert embedded_descending in markdown
    assert untouched_ambiguous in markdown


def test_rtl_edge_token_and_ltr_base_line_remain_in_producer_order() -> None:
    """Do not guess when a forward token is not bracketed by RTL text."""
    ltr_base = "00500044004600050001000300020006"
    rtl_edge = "00010002000300040005005000440046"
    stream = f"BT /F1 20 Tf 40 200 Td <{ltr_base}> Tj ET\nBT /F1 20 Tf 40 140 Td <{rtl_edge}> Tj ET"
    page = pylopdf.open(build_visual_rtl_pdf(stream)).pages[0]

    assert page.text().splitlines() == ["PDF םולש", "םלוע PDF"]


def test_vertical_cjk_inference_stops_at_candidate_boundary() -> None:
    """Disable optional inference as soon as the 4,097th candidate is seen."""
    text = "日" * 4097
    page = pylopdf.open(build_nonembedded_cjk_pdf(text)).pages[0]

    assert page.text().rstrip("\n") == text
    line = page.layout().to_dict()["blocks"][0]["lines"][0]
    assert line["wmode"] == 0


def test_vertical_cjk_columns_read_right_to_left_between_page_furniture() -> None:
    """Order vertical columns right-to-left between a heading and footer."""
    page = pylopdf.open(build_vertical_cjk_columns_pdf()).pages[0]

    assert page.text().splitlines() == ["Heading", "右側列", "左側列", "Footer"]
    layout = page.layout().to_dict()
    lines = [line for block in layout["blocks"] for line in block["lines"]]
    assert [line["wmode"] for line in lines] == [0, 1, 1, 0]
    markdown = page.markdown()
    assert markdown.index("Heading") < markdown.index("右側列")
    assert markdown.index("右側列") < markdown.index("左側列")
    assert markdown.index("左側列") < markdown.index("Footer")


def test_multicolumn_reading_order_follows_columns() -> None:
    """Read sustained columns top-to-bottom before moving left-to-right."""
    stream = (
        "BT /F1 18 Tf 20 270 Td (A heading spanning both columns) Tj ET\n"
        "BT /F1 12 Tf 40 230 Td (Left one) Tj ET\n"
        "BT /F1 12 Tf 40 210 Td (Left two) Tj ET\n"
        "BT /F1 12 Tf 200 230 Td (Right one) Tj ET\n"
        "BT /F1 12 Tf 200 210 Td (Right two) Tj ET\n"
        "BT /F1 18 Tf 20 30 Td (A footer spanning both columns) Tj ET"
    )
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 360 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )

    page = pylopdf.open(pdf).pages[0]
    expected = [
        "A heading spanning both columns",
        "Left one",
        "Left two",
        "Right one",
        "Right two",
        "A footer spanning both columns",
    ]
    assert page.text().splitlines() == expected
    assert [word.text for word in page.words()] == [word for line in expected for word in line.split()]


def test_narrow_multicolumn_gutter_follows_columns() -> None:
    """Use repeated narrow gutters without splitting ordinary word spaces."""
    rows = [
        ("Left column row 1", "Right column row 1"),
        ("Left column row 2", "Right column row 2"),
        ("Left column row 3", "Right column row 3"),
        ("Left column row 4", "Right column row 4"),
    ]
    stream = "BT /F1 14 Tf 20 270 Td (A heading spanning both columns) Tj ET\n"
    for offset, (left, right) in enumerate(rows):
        y = 230 - offset * 20
        stream += f"BT /F1 12 Tf 20 {y} Td ({left}) Tj ET\n"
        stream += f"BT /F1 12 Tf 148 {y} Td ({right}) Tj ET\n"
    stream += "BT /F1 14 Tf 20 30 Td (A footer spanning both columns) Tj ET"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 300 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )

    page = pylopdf.open(pdf).pages[0]
    expected = [
        "A heading spanning both columns",
        *(left for left, _ in rows),
        *(right for _, right in rows),
        "A footer spanning both columns",
    ]
    assert page.text().splitlines() == expected


def test_established_narrow_gutter_follows_small_horizontal_drift() -> None:
    """Keep continuation rows in columns when a scan gutter drifts slightly."""
    rows = [
        ("Left column row 1", "Right column row 1", 148),
        ("Left column row 2", "Right column row 2", 148),
        ("Left column row 3", "Right column row 3", 148),
        ("Left column row 4", "Right column row 4", 148),
        ("Left column row 5", "Right column row 5", 144),
        ("Left column row 6", "Right column row 6", 144),
    ]
    stream = ""
    for offset, (left, right, right_x) in enumerate(rows):
        y = 250 - offset * 20
        stream += f"BT /F1 12 Tf 20 {y} Td ({left}) Tj ET\n"
        stream += f"BT /F1 12 Tf {right_x} {y} Td ({right}) Tj ET\n"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 300 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )

    page = pylopdf.open(pdf).pages[0]
    assert page.text().splitlines() == [
        *(left for left, _, _ in rows),
        *(right for _, right, _ in rows),
    ]


def test_repeated_multiseparator_rows_stay_row_major() -> None:
    """Do not reinterpret a dense table-like layout as prose columns."""
    rows = [
        ("First field 0001", "Second field 0001", "Third field 0001"),
        ("First field 0002", "Second field 0002", "Third field 0002"),
        ("First field 0003", "Second field 0003", "Third field 0003"),
        ("First field 0004", "Second field 0004", "Third field 0004"),
    ]
    stream = ""
    for offset, row in enumerate(rows):
        y = 170 - offset * 20
        for x, text in zip((10, 73, 136), row, strict=True):
            stream += f"BT /F1 6 Tf {x} {y} Td ({text}) Tj ET\n"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 200 200] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )

    page = pylopdf.open(pdf).pages[0]
    assert page.text().splitlines() == [" ".join(row) for row in rows]


def test_wide_gap_table_rows_stay_row_major() -> None:
    """Do not split a spreadsheet's label column away from its cell grid."""
    rows = [
        ("Alpha", "11", "12", "13", "14"),
        ("Beta", "21", "22", "23", "24"),
        ("Gamma", "31", "32", "33", "34"),
        ("Delta", "41", "42", "43", "44"),
        ("Epsilon", "51", "52", "53", "54"),
        ("Zeta", "61", "62", "63", "64"),
    ]
    stream = ""
    for offset, row in enumerate(rows):
        y = 250 - offset * 20
        for x, text in zip((20, 120, 160, 200, 240), row, strict=True):
            stream += f"BT /F1 12 Tf {x} {y} Td ({text}) Tj ET\n"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 300 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )

    page = pylopdf.open(pdf).pages[0]
    assert page.text().splitlines() == [" ".join(row) for row in rows]


def test_isolated_wide_gap_stays_on_one_line() -> None:
    """Do not mistake an isolated header and page number for two columns."""
    stream = "BT /F1 12 Tf 40 260 Td (Header) Tj ET\nBT /F1 12 Tf 300 260 Td (1) Tj ET"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 360 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )

    page = pylopdf.open(pdf).pages[0]
    assert page.text() == "Header 1\n"
    layout = page.layout().to_dict()
    assert len(layout["blocks"][0]["lines"]) == 1
