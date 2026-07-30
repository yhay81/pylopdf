"""Tests for Document.to_markdown and Page.to_markdown."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import build_raw_pdf

import pylopdf


def _table_pdf(
    *,
    bordered: bool = True,
    table_font_size: int = 12,
    include_cells: bool = True,
) -> bytes:
    """Build a three-by-two table between ordinary page text."""
    rules = (
        "q 0 G 1 w\n"
        "40 250 m 300 250 l\n"
        "40 210 m 300 210 l\n"
        "40 170 m 300 170 l\n"
        "40 130 m 300 130 l\n"
        "40 130 m 40 250 l\n"
        "170 130 m 170 250 l\n"
        "300 130 m 300 250 l\n"
        "S Q\n"
        if bordered
        else ""
    )
    values = [
        (50, 225, "Name"),
        (180, 225, "Value"),
        (50, 185, "Alpha"),
        (180, 185, "42"),
        (50, 145, "Beta"),
        (180, 145, "7"),
    ]
    cells = (
        "\n".join(f"BT /F1 {table_font_size} Tf {x} {y} Td ({text}) Tj ET" for x, y, text in values)
        if include_cells
        else ""
    )
    stream = (
        "BT /F1 18 Tf 40 310 Td (Section) Tj ET\n"
        "BT /F1 12 Tf 40 280 Td (Body text) Tj ET\n"
        f"{rules}{cells}\n"
        "BT /F1 12 Tf 40 60 Td (After table) Tj ET"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 340 340] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )


def _layout_pdf(pages: list[list[tuple]]) -> bytes:
    """Build pages from ``(size, baseline_y, text[, font])`` tuples.

    Fonts are F1 = Helvetica (default), F2 = Helvetica-Bold, and
    F3 = Helvetica-Oblique.
    """
    n = len(pages)
    kids = " ".join(f"{10 + 2 * i} 0 R" for i in range(n))
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{kids}] /Count {n} /MediaBox [0 0 612 792]"
            " /Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> >>"
        ),
        3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >>",
    }
    for i, items in enumerate(pages):
        ops = ""
        for item in items:
            size, y, text = item[0], item[1], item[2]
            font = item[3] if len(item) > 3 else "F1"
            escaped = text.replace(chr(92), chr(92) * 2).replace("(", chr(92) + "(").replace(")", chr(92) + ")")
            ops += f"BT /{font} {size} Tf 72 {y} Td ({escaped}) Tj ET\n"
        objects[10 + 2 * i] = f"<< /Type /Page /Parent 2 0 R /Contents {11 + 2 * i} 0 R >>"
        objects[11 + 2 * i] = f"<< /Length {len(ops)} >>\nstream\n{ops}endstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in range(1, size):
        if num in offsets:
            out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
        else:
            out += b"0000000000 65535 f \n"
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return bytes(out)


def test_heading_detected_by_size() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [
                    (24, 720, "Big Title"),
                    (12, 660, "Body line one"),
                    (12, 646, "body line two"),
                ]
            ]
        )
    )
    md = doc.to_markdown()
    assert md.startswith("# Big Title")
    # Join two body lines with a space inside one paragraph.
    assert "Body line one body line two" in md


def test_two_heading_levels() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [
                    (28, 720, "Title"),
                    (18, 660, "Section"),
                    (12, 600, "Body text here"),
                    (12, 586, "and more body"),
                ]
            ]
        )
    )
    md = doc.to_markdown()
    assert "# Title" in md
    assert "## Section" in md
    assert "### " not in md


def test_uniform_size_has_no_headings() -> None:
    doc = pylopdf.open(stream=_layout_pdf([[(12, 720, "Only body"), (12, 706, "same size")]]))
    assert "#" not in doc.to_markdown()


def test_bullets_and_numbers_normalize() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [
                    (12, 720, "Intro paragraph"),
                    (12, 680, "- first item"),
                    (12, 666, "- second item"),
                    (12, 626, "1) numbered"),
                ]
            ]
        )
    )
    md = doc.to_markdown()
    assert "- first item\n- second item" in md  # Adjacent items form one list.
    assert "1. numbered" in md


def test_dict_spans_have_font_and_flags_keys() -> None:
    # Embedded fonts exercise real bold/italic detection in test_interop.py.
    # Hayro exposes no metadata for Standard 14 Type1 substitutes, so the
    # current contract is flags=0 and an empty font name.
    doc = pylopdf.open(stream=_layout_pdf([[(12, 720, "Standard font words", "F2")]]))
    span = doc.get_page_text(0, "dict")["blocks"][0]["lines"][0]["spans"][0]
    assert span["flags"] == 0
    assert span["font"] == ""


def test_cjk_lines_join_without_space() -> None:
    # Use two Japanese OCR-layer fixture lines without building a CJK font PDF.
    doc = pylopdf.Document()
    doc.new_page(width=300, height=200)
    page = doc[0]
    page.insert_ocr_text_layer(
        [
            (50, 50, 200, 64, "日本語の折り返しは"),
            (50, 66, 200, 80, "空白なしで繋がる"),
        ]
    )
    md = doc.to_markdown()
    assert "日本語の折り返しは空白なしで繋がる" in md


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("ภาษาไทยไม่มี", "ช่องว่าง"),
        ("ພາສາລາວບໍ່ມີ", "ຊ່ອງຫວ່າງ"),
        ("ភាសាខ្មែរមិនមាន", "ចន្លោះ"),
        ("မြန်မာစာတွင်", "နေရာလွတ်"),
    ],
)
def test_no_space_scripts_join_wrapped_lines_without_ascii_gap(first: str, second: str) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=300, height=200)
    doc[0].insert_ocr_text_layer(
        [
            (50, 50, 200, 64, first),
            (50, 66, 200, 80, second),
        ]
    )

    markdown = doc.to_markdown()

    assert first + second in markdown
    assert first + " " + second not in markdown


def test_latin_lines_join_with_space() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=300, height=200)
    doc[0].insert_ocr_text_layer(
        [
            (50, 50, 200, 64, "Latin lines"),
            (50, 66, 200, 80, "join spaced"),
        ]
    )
    assert "Latin lines join spaced" in doc.to_markdown()


def test_page_to_markdown_and_page_selection() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [(12, 720, "First page")],
                [(12, 720, "Second page")],
            ]
        )
    )
    assert doc[1].to_markdown() == "Second page"
    assert doc.to_markdown(pages=[1, 0]) == "Second page\n\nFirst page"
    full = doc.to_markdown()
    assert "First page" in full
    assert "Second page" in full


def test_markdown_bounds_utf8_output_without_partial_result() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=300, height=200)
    doc.new_page(width=300, height=200)
    doc[0].insert_ocr_text_layer([(20, 20, 200, 40, "First page")])
    doc[1].insert_ocr_text_layer([(20, 20, 200, 40, "日本語の二ページ目")])
    expected = doc.to_markdown(max_size=None)
    exact_size = len(expected.encode())
    expected_page = doc[1].to_markdown(max_size=None)

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.to_markdown(max_size=exact_size - 1)
    assert caught.value.code == "markdown_output_size"
    assert doc.to_markdown(max_size=exact_size) == expected
    assert doc[1].to_markdown(max_size=len(expected_page.encode())) == expected_page


def test_markdown_bounds_page_entries_before_final_join(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = pylopdf.open(stream=_layout_pdf([[(12, 720, "Page body exceeds budget")]]))

    def fail_final_join(*_args: object, **_kwargs: object) -> str:
        raise AssertionError

    monkeypatch.setattr("pylopdf._markdown._join_entries", fail_final_join)
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.to_markdown(max_size=4)
    assert caught.value.code == "markdown_output_size"


def test_markdown_table_and_prose_share_exact_page_budget() -> None:
    doc = pylopdf.open(stream=_table_pdf())
    expected = doc.to_markdown(max_size=None)
    exact_size = len(expected.encode())

    assert doc.to_markdown(max_size=exact_size) == expected
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.to_markdown(max_size=exact_size - 1)
    assert caught.value.code == "markdown_output_size"


def test_markdown_headings_lists_and_paragraphs_share_exact_budget() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [
                    (24, 720, "Title"),
                    (12, 680, "Intro paragraph"),
                    (12, 640, "- first item"),
                    (12, 626, "- second item"),
                ]
            ]
        )
    )
    expected = doc.to_markdown(max_size=None)
    exact_size = len(expected.encode())

    assert doc.to_markdown(max_size=exact_size) == expected
    with pytest.raises(pylopdf.LimitError):
        doc.to_markdown(max_size=exact_size - 1)


def test_markdown_bounds_page_iterable_before_interpretation() -> None:
    doc = pylopdf.open(stream=_layout_pdf([[(12, 720, "Page")]]))
    consumed = 0

    def pages() -> Iterator[int]:
        nonlocal consumed
        for _ in range(5000):
            consumed += 1
            yield 0

    with pytest.raises(ValueError, match="4096 entries"):
        doc.to_markdown(pages=pages())
    assert consumed == 4097


@pytest.mark.parametrize("max_size", [True, 1.5, "1024"])
def test_markdown_rejects_non_integer_size_limits(max_size: object) -> None:
    doc = pylopdf.open(stream=_layout_pdf([[(12, 720, "Page")]]))
    with pytest.raises(TypeError, match="max_size"):
        doc.to_markdown(max_size=max_size)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_size", [0, -1])
def test_markdown_rejects_non_positive_size_limits(max_size: int) -> None:
    doc = pylopdf.open(stream=_layout_pdf([[(12, 720, "Page")]]))
    with pytest.raises(ValueError, match="max_size"):
        doc.to_markdown(max_size=max_size)


def test_empty_document() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    assert doc.to_markdown() == ""


def test_bordered_table_is_inserted_without_duplicate_cell_text() -> None:
    doc = pylopdf.open(stream=_table_pdf())

    markdown = doc.to_markdown()

    table = "| Name | Value |\n| --- | --- |\n| Alpha | 42 |\n| Beta | 7 |"
    assert table in markdown
    assert markdown.index("# Section") < markdown.index(table) < markdown.index("After table")
    assert markdown.count("Name") == 1
    assert markdown.count("Alpha") == 1


def test_table_conversion_can_be_disabled() -> None:
    doc = pylopdf.open(stream=_table_pdf())

    markdown = doc[0].to_markdown(table_strategy=None)

    assert "| --- |" not in markdown
    assert "Name" in markdown
    assert "Alpha" in markdown


def test_borderless_table_conversion_is_explicit() -> None:
    doc = pylopdf.open(stream=_table_pdf(bordered=False))

    default = doc.to_markdown()
    with_text_tables = doc.to_markdown(table_strategy="text")

    assert "| --- |" not in default
    assert "| Name | Value |\n| --- | --- |\n| Alpha | 42 |\n| Beta | 7 |" in with_text_tables
    assert with_text_tables.count("Name") == 1


def test_text_strategy_prefers_overlapping_bordered_table() -> None:
    doc = pylopdf.open(stream=_table_pdf())

    markdown = doc.to_markdown(table_strategy="text")

    assert markdown.count("| --- | --- |") == 1
    assert markdown.count("Alpha") == 1


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_table_markdown_preserves_page_reading_order(rotation: int) -> None:
    doc = pylopdf.open(stream=_table_pdf())
    doc[0].set_rotation(rotation)

    markdown = doc[0].to_markdown()

    assert markdown.index("# Section") < markdown.index("| Name | Value |") < markdown.index("After table")
    assert markdown.count("Alpha") == 1


def test_table_text_does_not_influence_heading_size_inference() -> None:
    doc = pylopdf.open(stream=_table_pdf(table_font_size=24))

    markdown = doc.to_markdown()

    assert markdown.startswith("# Section")


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_empty_bordered_table_uses_geometric_reading_order(rotation: int) -> None:
    doc = pylopdf.open(stream=_table_pdf(include_cells=False))
    doc[0].set_rotation(rotation)

    markdown = doc.to_markdown()

    table = "|  |  |\n| --- | --- |\n|  |  |\n|  |  |"
    assert markdown.index("Body text") < markdown.index(table) < markdown.index("After table")


@pytest.mark.parametrize("table_strategy", ["guess", 1, False])
def test_markdown_rejects_unknown_table_strategy(table_strategy: object) -> None:
    doc = pylopdf.open(stream=_table_pdf())
    with pytest.raises(ValueError, match="table_strategy"):
        doc.to_markdown(table_strategy=table_strategy)  # type: ignore[arg-type]
