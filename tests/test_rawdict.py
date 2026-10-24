"""Glyph-level extraction contracts using source-defined PDF geometry."""

from __future__ import annotations

import dataclasses
import math
import warnings
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from conftest import build_nonembedded_cjk_pdf, build_pdf, build_raw_pdf

import pylopdf

_REAL_WORLD = Path(__file__).parent / "assets" / "real_world"


def _courier_pdf(
    stream: str,
    *,
    width: int = 300,
    height: int = 200,
    page_attributes: str = "",
    to_unicode: str | None = None,
) -> bytes:
    """Use Courier's specified 600-unit widths for independent advance checks."""
    mapping = " /ToUnicode 6 0 R" if to_unicode is not None else ""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 {width} {height}] "
            "/Resources << /Font << /F1 3 0 R >> >> >>"
        ),
        3: f"<< /Type /Font /Subtype /Type1 /BaseFont /Courier{mapping} >>",
        4: f"<< /Type /Page /Parent 2 0 R /Contents 5 0 R {page_attributes} >>",
        5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    }
    if to_unicode is not None:
        objects[6] = f"<< /Length {len(to_unicode)} >>\nstream\n{to_unicode}\nendstream"
    return build_raw_pdf(objects)


def _ligature_pdf() -> bytes:
    """Map one painted Courier glyph to two Unicode characters without shaping."""
    cmap = """/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
2 beginbfchar
<41> <00660069>
<42> <0042>
endbfchar
endcmap
end
end"""
    return _courier_pdf("BT /F1 20 Tf 40 140 Td (AB) Tj ET", to_unicode=cmap)


def _chars(layout: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        char
        for block in layout["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]


def _raw_line_texts(layout: dict[str, Any]) -> list[str]:
    return [
        "".join(char["c"] for span in line["spans"] for char in span["chars"])
        for block in layout["blocks"]
        for line in block["lines"]
    ]


def test_rawdict_schema_and_horizontal_japanese_geometry() -> None:
    doc = pylopdf.open(build_nonembedded_cjk_pdf("日本語"))
    doc.fallback_fonts = {}
    page = doc.pages[0]
    layout: dict[str, Any] = page.layout(chars=True).to_dict()
    assert set(layout) == {"width", "height", "blocks"}
    assert (layout["width"], layout["height"]) == (612.0, 792.0)
    block = layout["blocks"][0]
    assert set(block) == {"number", "type", "bbox", "lines"}
    assert (block["number"], block["type"]) == (0, 0)
    line = block["lines"][0]
    assert set(line) == {"bbox", "dir", "wmode", "spans"}
    assert line["dir"] == (1.0, 0.0)
    assert line["wmode"] == 0
    span = line["spans"][0]
    assert set(span) == {
        "bbox",
        "origin",
        "size",
        "font",
        "flags",
        "char_flags",
        "color",
        "alpha",
        "ascender",
        "descender",
        "chars",
    }
    assert span["size"] == pytest.approx(24)
    assert [char["c"] for char in span["chars"]] == list("日本語")
    # The fixture's PDF origin is (72, 720), with /DW 1000 at 24 pt.
    for index, char in enumerate(span["chars"]):
        assert set(char) == {"c", "bbox", "origin", "synthetic"}
        x = 72 + 24 * index
        assert char["origin"] == pytest.approx((x, 72))
        assert char["bbox"] == pytest.approx((x, 52.8, x + 24, 76.8))
    document_layout: dict[str, Any] = doc.pages[0].layout(chars=True).to_dict()
    assert document_layout == layout
    assert page.text() == "日本語\n"


def test_rawdict_dimension_text_preserves_each_letter_digit_and_unit() -> None:
    dimension = "DN100 2400 mm"
    stream = f"BT /F1 20 Tf 40 140 Td ({dimension}) Tj ET"
    page = pylopdf.open(_courier_pdf(stream)).pages[0]
    chars = _chars(page.layout(chars=True).to_dict())
    assert [char["c"] for char in chars] == list(dimension)
    # Courier's 600-unit width at 20 pt applies to letters, digits, and spaces.
    for index, char in enumerate(chars):
        x = 40 + 12 * index
        assert char["origin"] == (x, 60)
        assert char["bbox"] == (x, 44, x + 12, 64)
    assert page.text() == f"{dimension}\n"
    assert [word.text for word in page.words()] == ["DN100", "2400", "mm"]


def test_rawdict_tj_adjustments_use_actual_glyph_positions() -> None:
    # TJ subtracts 100/1000 * 20 = 2 pt after W and adds 200/1000 * 20 = 4 pt
    # after i. These origins cannot come from evenly splitting the span bbox.
    stream = "BT /F1 20 Tf 40 140 Td [(W) 100 (i) -200 (8)] TJ ET"
    page = pylopdf.open(_courier_pdf(stream)).pages[0]
    layout = page.layout(chars=True).to_dict()
    span = layout["blocks"][0]["lines"][0]["spans"][0]
    assert span["bbox"] == (40, 44, 78, 64)
    # The 4-point gap after i holds an inferred space.
    chars = [char for char in span["chars"] if not char["synthetic"]]
    assert [char["c"] for char in span["chars"]] == ["W", "i", " ", "8"]
    for char, x in zip(chars, (40, 50, 66), strict=True):
        assert char["origin"] == (x, 60)
        assert char["bbox"] == (x, 44, x + 12, 64)


@pytest.mark.parametrize(
    ("matrix", "direction", "step", "bbox_offset"),
    [
        ("0 1 -1 0", (0.0, -1.0), (0.0, -12.0), (-16.0, -12.0, 4.0, 0.0)),
        (
            "0.7071067812 0.7071067812 -0.7071067812 0.7071067812",
            (math.sqrt(0.5), -math.sqrt(0.5)),
            (12 * math.sqrt(0.5), -12 * math.sqrt(0.5)),
            (-16 * math.sqrt(0.5), -28 * math.sqrt(0.5), 16 * math.sqrt(0.5), 4 * math.sqrt(0.5)),
        ),
    ],
    ids=["90-degrees", "45-degrees"],
)
def test_rawdict_rotated_text_keeps_glyph_origins_and_advance_boxes(
    matrix: str,
    direction: tuple[float, float],
    step: tuple[float, float],
    bbox_offset: tuple[float, float, float, float],
) -> None:
    stream = f"BT /F1 20 Tf {matrix} 100 200 Tm (ABC) Tj ET"
    page = pylopdf.open(_courier_pdf(stream, height=300)).pages[0]
    before_text = page.text()
    before_dict = page.layout().to_dict()
    layout = page.layout(chars=True).to_dict()
    for block in layout["blocks"]:
        for line in block["lines"]:
            assert line["dir"] == pytest.approx(direction)
            assert line["wmode"] == 0
    assert _raw_line_texts(layout) == [
        "".join(span["text"] for span in line["spans"]) for block in before_dict["blocks"] for line in block["lines"]
    ]
    # Arbitrary-angle clustering follows dict; source character identity pins
    # each glyph's geometry independently of that existing reading order.
    chars = sorted(_chars(layout), key=lambda char: char["c"])
    assert [char["c"] for char in chars] == list("ABC")
    # Rotate Courier's 12 pt advance and the documented 0.8/0.2 size envelope.
    for index, char in enumerate(chars):
        x, y = 100 + index * step[0], 100 + index * step[1]
        assert char["origin"] == pytest.approx((x, y), abs=1e-6)
        expected = (x + bbox_offset[0], y + bbox_offset[1], x + bbox_offset[2], y + bbox_offset[3])
        assert char["bbox"] == pytest.approx(expected, abs=1e-6)
    assert page.text() == before_text
    assert page.layout().to_dict() == before_dict


@pytest.mark.parametrize(
    ("rotation", "size", "origin", "direction", "bbox"),
    [
        (0, (280, 180), (20, 30), (1, 0), (20, 14, 32, 34)),
        (90, (180, 280), (150, 20), (0, 1), (146, 20, 166, 32)),
        (180, (280, 180), (260, 150), (-1, 0), (248, 146, 260, 166)),
        (270, (180, 280), (30, 260), (0, -1), (14, 248, 34, 260)),
    ],
)
def test_rawdict_resolves_page_rotation_and_crop_offsets(
    rotation: int,
    size: tuple[int, int],
    origin: tuple[int, int],
    direction: tuple[int, int],
    bbox: tuple[int, int, int, int],
) -> None:
    stream = "BT /F1 20 Tf 40 180 Td (AB) Tj ET"
    attributes = f"/CropBox [20 30 300 210] /Rotate {rotation}"
    page = pylopdf.open(_courier_pdf(stream, width=320, height=240, page_attributes=attributes)).pages[0]
    layout = page.layout(chars=True).to_dict()
    assert (layout["width"], layout["height"]) == size
    assert layout["blocks"][0]["lines"][0]["dir"] == pytest.approx(direction)
    first, second = _chars(layout)
    assert first["origin"] == pytest.approx(origin)
    assert first["bbox"] == pytest.approx(bbox)
    assert second["origin"] == pytest.approx((origin[0] + 12 * direction[0], origin[1] + 12 * direction[1]))


def test_rawdict_vertical_cjk_preserves_per_glyph_vertical_positions() -> None:
    pdf = build_nonembedded_cjk_pdf("日本語").replace(b"/90ms-RKSJ-H", b"/90ms-RKSJ-V")
    doc = pylopdf.open(pdf)
    doc.fallback_fonts = {}
    layout = doc.pages[0].layout(chars=True).to_dict()
    line = layout["blocks"][0]["lines"][0]
    assert line["dir"] == (0.0, 1.0)
    assert line["wmode"] == 1
    assert [char["c"] for char in _chars(layout)] == list("日本語")
    # Default vertical metrics shift by half /DW and 880 units, then advance 1000.
    for index, char in enumerate(_chars(layout)):
        y = 72 + 0.88 * 24 + 24 * index
        assert char["origin"] == pytest.approx((60, y))
        assert char["bbox"][1] == pytest.approx(y - 0.8 * 24)
        assert char["bbox"][3] == pytest.approx(y + 0.2 * 24)
        assert char["bbox"][0] == pytest.approx(60)
        assert char["bbox"][2] > 60


def test_rawdict_retains_a_multi_unicode_mapping_as_one_glyph() -> None:
    doc = pylopdf.open(_ligature_pdf(), limits=pylopdf.DocumentLimits(max_text_glyphs=2, max_text_size=3))
    page = doc.pages[0]
    first, second = _chars(page.layout(chars=True).to_dict())
    assert (first["c"], second["c"]) == ("fi", "B")
    assert first["origin"] == (40, 60)
    assert second["origin"] == (52, 60)
    assert first["bbox"] == (40, 44, 52, 64)
    assert second["bbox"] == (52, 44, 64, 64)
    assert page.text() == "fiB\n"
    assert page.layout().to_dict()["blocks"][0]["lines"][0]["spans"][0]["text"] == "fiB"


@pytest.mark.parametrize(
    ("limits", "code"),
    [
        (pylopdf.DocumentLimits(max_text_glyphs=1), "text_glyph_count"),
        (pylopdf.DocumentLimits(max_text_size=2), "text_size"),
    ],
)
def test_rawdict_applies_glyph_and_unicode_payload_limits(limits: pylopdf.DocumentLimits, code: str) -> None:
    doc = pylopdf.open(_ligature_pdf(), limits=limits)
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.pages[0].layout(chars=True).to_dict()
    assert caught.value.code == code


@pytest.mark.parametrize("painted_space", [False, True])
def test_rawdict_marks_inferred_spaces_as_synthetic(*, painted_space: bool) -> None:
    stream = (
        "BT /F1 20 Tf 40 140 Td (A B) Tj ET" if painted_space else "BT /F1 20 Tf 40 140 Td (A) Tj 24 0 Td (B) Tj ET"
    )
    page = pylopdf.open(_courier_pdf(stream)).pages[0]
    chars = _chars(page.layout(chars=True).to_dict())
    assert [char["c"] for char in chars] == list("A B")
    assert [char["synthetic"] for char in chars] == [False, not painted_space, False]
    # An inferred space spans the gap between the advance of A and B.
    assert chars[1]["bbox"] == (52, 44, 64, 64)
    assert chars[0]["origin"] == (40, 60)
    assert chars[-1]["origin"] == (64, 60)
    assert page.text() == "A B\n"
    assert page.layout().to_dict()["blocks"][0]["lines"][0]["spans"][0]["text"] == "A B"


@pytest.mark.parametrize("second_text", ["AB", "CD"])
def test_rawdict_keeps_overprinted_glyphs_in_separate_paint_runs(second_text: str) -> None:
    stream = f"BT /F1 20 Tf 40 140 Td (AB) Tj ET BT /F1 20 Tf 40 140 Td ({second_text}) Tj ET"
    page = pylopdf.open(_courier_pdf(stream)).pages[0]
    layout = page.layout(chars=True).to_dict()
    assert _raw_line_texts(layout) == ["AB", second_text]
    chars = _chars(layout)
    assert len(chars) == 4
    assert [char["origin"] for char in chars] == [(40, 60), (52, 60), (40, 60), (52, 60)]
    assert chars[0]["bbox"] == chars[2]["bbox"] == (40, 44, 52, 64)
    assert page.text() == f"AB\n{second_text}\n"


def test_rawdict_empty_page_has_dimensions_and_no_blocks() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((321, 123))
    assert page.layout(chars=True).to_dict() == {"width": 321.0, "height": 123.0, "blocks": []}
    assert page.text() == ""


def test_rawdict_unmapped_glyphs_reuse_the_existing_warning_contract() -> None:
    page = pylopdf.open(_REAL_WORLD / "pdfium-type3.pdf").pages[0]
    with pytest.warns(pylopdf.PylopdfWarning, match="no Unicode mapping") as caught:
        layout = page.layout(chars=True).to_dict()
    assert layout["blocks"] == []
    assert sum("no Unicode mapping" in str(item.message) for item in caught) == 1
    with warnings.catch_warnings(record=True) as repeated:
        warnings.simplefilter("always")
        assert page.layout(chars=True).to_dict() == layout
        assert page.text() == ""
        assert page.layout().to_dict()["blocks"] == []
    assert not repeated


def test_rawdict_shares_admission_with_other_text_and_table_views() -> None:
    doc = pylopdf.open(build_pdf(["AB", "CD", "E"]), limits=pylopdf.DocumentLimits(max_text_glyphs=3))
    page = doc.pages[0]
    assert _raw_line_texts(page.layout(chars=True).to_dict()) == ["AB"]
    assert page.text() == "AB\n"
    assert page.words()[0].text == "AB"
    assert page.layout().to_dict()["blocks"][0]["lines"][0]["spans"][0]["text"] == "AB"
    assert page.tables() == []
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.pages[1].layout(chars=True).to_dict()
    assert caught.value.code == "text_glyph_count"
    # Failed admission leaves the final one-glyph page available.
    assert _raw_line_texts(doc.pages[2].layout(chars=True).to_dict()) == ["E"]


def test_rawdict_reinterprets_evicted_pages_without_charging_budget_twice() -> None:
    # Ten pages exceed the eight-page interpretation cache while exactly
    # exhausting both document-wide admission budgets.
    source = list("ABCDEFGHIJ")
    doc = pylopdf.open(build_pdf(source), limits=pylopdf.DocumentLimits(max_text_glyphs=10, max_text_size=10))
    first = doc.pages[0].layout(chars=True).to_dict()
    for index, expected in enumerate(source):
        assert _raw_line_texts(doc.pages[index].layout(chars=True).to_dict()) == [expected]
    assert doc.pages[0].layout(chars=True).to_dict() == first


def test_rawdict_results_do_not_mutate_the_cached_interpretation() -> None:
    page = pylopdf.open(_courier_pdf("BT /F1 20 Tf 40 140 Td (AB) Tj ET")).pages[0]
    layout = page.layout(chars=True).to_dict()
    chars = _chars(layout)
    chars[0]["c"] = "Changed"
    chars[0]["bbox"] = (0, 0, 1, 1)
    layout["blocks"].clear()
    assert _raw_line_texts(page.layout(chars=True).to_dict()) == ["AB"]
    assert _chars(page.layout(chars=True).to_dict())[0]["bbox"] == (40, 44, 52, 64)
    assert page.text() == "AB\n"


def test_rawdict_cache_reflects_a_page_edit() -> None:
    doc = pylopdf.open(build_pdf(["ABC"]))
    page = doc.pages[0]
    assert _raw_line_texts(page.layout(chars=True).to_dict()) == ["ABC"]
    with page.draw() as canvas:
        canvas.text("D", (72, 120))
    layout = page.layout(chars=True).to_dict()
    assert _raw_line_texts(layout) == ["ABC", "D"]
    assert _chars(layout)[-1]["origin"] == (72, 120)
    assert page.text() == "ABC\nD\n"


def test_rawdict_preserves_existing_layout_metadata_and_span_boundaries() -> None:
    stream = "BT /F1 20 Tf 40 140 Td (AB) Tj /F1 10 Tf (CD) Tj ET"
    page = pylopdf.open(_courier_pdf(stream)).pages[0]
    before = page.layout().to_dict()
    raw = page.layout(chars=True).to_dict()
    assert page.layout().to_dict() == before
    assert raw["width"] == before["width"]
    assert raw["height"] == before["height"]
    raw_line = raw["blocks"][0]["lines"][0]
    text_line = before["blocks"][0]["lines"][0]
    assert raw_line["bbox"] == text_line["bbox"]
    assert raw_line["dir"] == text_line["dir"]
    assert raw_line["wmode"] == text_line["wmode"]
    assert len(raw_line["spans"]) == len(text_line["spans"]) == 2
    for raw_span, text_span in zip(raw_line["spans"], text_line["spans"], strict=True):
        assert {key: value for key, value in raw_span.items() if key != "chars"} == {
            key: value for key, value in text_span.items() if key != "text"
        }
        assert "".join(char["c"] for char in raw_span["chars"]) == text_span["text"]


def test_layout_records_describe_glyph_values() -> None:
    contracts = {
        pylopdf.Char: ("text", "bbox", "origin", "is_synthetic"),
        pylopdf.Span: (
            "text",
            "bbox",
            "origin",
            "size",
            "font",
            "is_bold",
            "is_italic",
            "is_serif",
            "is_monospaced",
            "is_superscript",
            "is_underlined",
            "is_struck",
            "color",
            "render_mode",
            "is_clipped",
            "ascender",
            "descender",
            "chars",
            "quad",
        ),
        pylopdf.Line: ("text", "bbox", "direction", "is_vertical", "spans", "quad"),
        pylopdf.Block: ("text", "bbox", "lines"),
        pylopdf.Layout: ("width", "height", "blocks"),
    }
    for record, fields in contracts.items():
        assert record.__name__ in pylopdf.__all__
        assert dataclasses.is_dataclass(record)
        assert tuple(field.name for field in dataclasses.fields(record)) == fields
    hints = get_type_hints(pylopdf.Char)
    assert hints == {
        "text": str,
        "bbox": pylopdf.Rect,
        "origin": pylopdf.Point,
        "is_synthetic": bool,
    }
