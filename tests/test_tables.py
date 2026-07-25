"""Tests for deterministic bordered-table detection."""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def _bordered_table_pdf() -> bytes:
    """Build a two-by-two table from stroked rules and positioned text."""
    stream = (
        "q 0 G 1 w\n"
        "40 260 m 300 260 l\n"
        "40 220 m 300 220 l\n"
        "40 180 m 300 180 l\n"
        "40 180 m 40 260 l\n"
        "170 180 m 170 260 l\n"
        "300 180 m 300 260 l\n"
        "S Q\n"
        "BT /F1 12 Tf 50 235 Td (Name) Tj ET\n"
        "BT /F1 12 Tf 180 235 Td (Value) Tj ET\n"
        "BT /F1 12 Tf 50 195 Td (Alpha) Tj ET\n"
        "BT /F1 12 Tf 180 195 Td (42) Tj ET"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 340 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )


def _filled_rule_table_pdf() -> bytes:
    """Build the same grid from thin filled rectangles."""
    stream = (
        "q 0 g\n"
        "40 179 260 2 re f\n"
        "40 219 260 2 re f\n"
        "40 259 260 2 re f\n"
        "39 180 2 80 re f\n"
        "169 180 2 80 re f\n"
        "299 180 2 80 re f\n"
        "Q\n"
        "BT /F1 12 Tf 50 235 Td (Name) Tj ET\n"
        "BT /F1 12 Tf 180 235 Td (Value) Tj ET\n"
        "BT /F1 12 Tf 50 195 Td (Alpha) Tj ET\n"
        "BT /F1 12 Tf 180 195 Td (42) Tj ET"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 340 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )


def _borderless_table_pdf(*, rows: int = 3) -> bytes:
    """Build aligned, independently positioned text cells without borders."""
    values = [("Name", "Value"), ("Alpha", "42"), ("Beta", "7")][:rows]
    stream = "\n".join(
        (f"BT /F1 12 Tf 40 {240 - row * 30} Td ({left}) Tj ET\nBT /F1 12 Tf 180 {240 - row * 30} Td ({right}) Tj ET")
        for row, (left, right) in enumerate(values)
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 340 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )


def _sparse_row_grid_pdf() -> bytes:
    """Build a grid whose data rows omit every internal horizontal rule."""
    rules = (
        "q 0 G 1 w\n"
        "40 260 m 300 260 l\n"
        "40 230 m 300 230 l\n"
        "40 130 m 300 130 l\n"
        "40 130 m 40 260 l\n"
        "140 130 m 140 260 l\n"
        "240 130 m 240 260 l\n"
        "300 130 m 300 260 l\n"
        "S Q"
    )
    cells = [("Name", "Qty", "Total")] + [(f"Item{row}", str(row), str(row * 10)) for row in range(1, 6)]
    text = "\n".join(
        " ".join(
            f"BT /F1 12 Tf {x} {baseline} Td ({value}) Tj ET" for x, value in zip((50, 150, 250), values, strict=True)
        )
        for baseline, values in zip((240, 215, 196, 177, 158, 139), cells, strict=True)
    )
    stream = f"{rules}\n{text}"
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 340 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )


def _multiline_merged_header_pdf() -> bytes:
    """Build a merged header whose dense lines occupy different columns."""
    stream = (
        "q 0 G 1 w\n"
        "40 260 m 300 260 l\n"
        "40 200 m 300 200 l\n"
        "40 160 m 300 160 l\n"
        "40 120 m 300 120 l\n"
        "40 120 m 40 260 l\n"
        "300 120 m 300 260 l\n"
        "140 120 m 140 200 l\n"
        "240 120 m 240 200 l\n"
        "S Q\n"
        "BT /F1 10 Tf 50 248 Td (First) Tj ET\n"
        "BT /F1 10 Tf 150 248 Td (summary) Tj ET\n"
        "BT /F1 10 Tf 50 228 Td (Second) Tj ET\n"
        "BT /F1 10 Tf 250 228 Td (summary) Tj ET\n"
        "BT /F1 10 Tf 150 208 Td (Third) Tj ET\n"
        "BT /F1 10 Tf 250 208 Td (summary) Tj ET\n"
        "BT /F1 12 Tf 50 175 Td (Alpha) Tj ET\n"
        "BT /F1 12 Tf 150 175 Td (1) Tj ET\n"
        "BT /F1 12 Tf 250 175 Td (10) Tj ET\n"
        "BT /F1 12 Tf 50 135 Td (Beta) Tj ET\n"
        "BT /F1 12 Tf 150 135 Td (2) Tj ET\n"
        "BT /F1 12 Tf 250 135 Td (20) Tj ET"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 340 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )


def test_find_bordered_table() -> None:
    page = pylopdf.open(stream=_bordered_table_pdf())[0]

    finder = page.find_tables()

    assert isinstance(finder, pylopdf.TableFinder)
    assert finder.page is page
    assert len(finder) == 1
    assert list(finder) == finder.tables
    assert finder[:] == finder.tables
    table = finder[0]
    assert isinstance(table, pylopdf.Table)
    assert table.page is page
    assert table.bbox == pytest.approx(pylopdf.Rect(40, 40, 300, 120))
    assert table.row_count == 2
    assert table.col_count == 2
    assert len(table.cells) == 4
    assert finder.cells == table.cells
    assert finder.strategy == "lines"
    assert finder.clip is None
    assert table.strategy == "lines"
    assert table.confidence == 1.0
    assert table.diagnostics == pylopdf.TableDiagnostics("lines", 1.0, None, None, None)
    assert table.extract() == [["Name", "Value"], ["Alpha", "42"]]
    assert table.to_markdown() == "| Name | Value |\n| --- | --- |\n| Alpha | 42 |"


def test_find_tables_clip_is_conservative_and_uses_display_coordinates() -> None:
    """Return complete tables inside a region without synthesizing partial grids."""
    document = pylopdf.open(stream=_bordered_table_pdf())
    page = document[0]
    page.set_rotation(90)

    table_bbox = pylopdf.Rect(180, 40, 260, 300)
    finder = page.find_tables(clip=table_bbox)

    assert finder.clip == table_bbox
    assert len(finder) == 1
    assert finder[0].bbox == pytest.approx(table_bbox)
    assert page.find_tables(clip=(180, 40, 240, 300)).tables == []
    assert page.find_tables(clip=(0, 0, 100, 100)).tables == []


@pytest.mark.parametrize(
    "clip",
    [
        (0, 0, 0, 10),
        (0, 0, float("inf"), 10),
        (0, 0, 10),
    ],
)
def test_find_tables_rejects_invalid_clip(clip: tuple[float, ...]) -> None:
    page = pylopdf.open(stream=_bordered_table_pdf())[0]
    with pytest.raises(ValueError, match="clip"):
        page.find_tables(clip=clip)


def test_find_tables_returns_empty_for_plain_text() -> None:
    page = pylopdf.open(stream=build_pdf(["Not a table"]))[0]
    finder = page.find_tables()
    assert len(finder) == 0
    assert finder.tables == []
    assert finder.cells == []


def test_table_cache_is_invalidated_after_page_insertion() -> None:
    doc = pylopdf.open(stream=_bordered_table_pdf())
    assert len(doc[0].find_tables()) == 1

    doc.insert_pdf(pylopdf.open(stream=build_pdf(["Not a table"])), start_at=0)

    assert doc[0].find_tables().tables == []
    assert len(doc[1].find_tables()) == 1


def test_rectangular_merged_cell_is_reconstructed() -> None:
    """Represent a missing internal divider as a merged header cell."""
    pdf = _bordered_table_pdf().replace(b"170 180 m 170 260 l", b"170 180 m 170 220 l")
    table = pylopdf.open(stream=pdf)[0].find_tables()[0]

    assert table.cells[0] == pytest.approx(pylopdf.Rect(40, 40, 300, 80))
    assert table.cells[1] is None
    assert table.extract() == [["Name Value", None], ["Alpha", "42"]]
    assert table.to_markdown() == ("| Name Value | Name Value |\n| --- | --- |\n| Alpha | 42 |")
    assert table.to_markdown(fill_empty=False) == ("| Name Value |  |\n| --- | --- |\n| Alpha | 42 |")


def test_broken_outer_grid_is_not_reported_as_a_table() -> None:
    """Reject a missing exterior border instead of inventing a table."""
    pdf = _bordered_table_pdf().replace(b"40 180 m 40 260 l", b"40 180 m 40 220 l")
    assert pylopdf.open(stream=pdf)[0].find_tables().tables == []


def test_row_spanning_cell_is_reconstructed() -> None:
    """Represent a missing internal horizontal divider as a row span."""
    pdf = _bordered_table_pdf().replace(b"40 220 m 300 220 l", b"80 220 m 300 220 l")
    table = pylopdf.open(stream=pdf)[0].find_tables()[0]

    assert table.cells[0] == pytest.approx(pylopdf.Rect(40, 40, 170, 120))
    assert table.cells[2] is None
    assert table.extract() == [["Name\nAlpha", "Value"], [None, "42"]]


def test_find_table_with_filled_rectangle_rules() -> None:
    """Recognize generators that paint narrow rectangles instead of strokes."""
    table = pylopdf.open(stream=_filled_rule_table_pdf())[0].find_tables()[0]

    assert table.bbox == pytest.approx(pylopdf.Rect(40, 40, 300, 120))
    assert table.extract() == [["Name", "Value"], ["Alpha", "42"]]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_sparse_row_grid_infers_repeated_records(rotation: int) -> None:
    """Refine coarse row spans while preserving logical Markdown orientation."""
    document = pylopdf.open(stream=_sparse_row_grid_pdf())
    document[0].set_rotation(rotation)

    table = document[0].find_tables()[0]
    physical_shape = (6, 3) if rotation in (0, 180) else (3, 6)
    assert (table.row_count, table.col_count) == physical_shape
    assert table.confidence == 0.95

    markdown = document.to_markdown()
    assert "| Name | Qty | Total |" in markdown
    assert "| Item1 | 1 | 10 |" in markdown
    assert "| Item5 | 5 | 50 |" in markdown
    assert markdown.count("Item3") == 1


def test_sparse_row_inference_preserves_multiline_merged_header() -> None:
    """Reject dense multiline text when adjacent slot signatures disagree."""
    table = pylopdf.open(stream=_multiline_merged_header_pdf())[0].find_tables()[0]

    assert (table.row_count, table.col_count) == (3, 3)
    assert table.confidence == 1.0
    assert table.extract() == [
        ["First summary\nSecond summary\nThird summary", None, None],
        ["Alpha", "1", "10"],
        ["Beta", "2", "20"],
    ]


def test_compact_filled_decorations_are_not_table_rules() -> None:
    """Do not turn ordinary filled boxes into a grid."""
    stream = "20 20 30 30 re f\n80 80 40 40 re f"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
            4: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )
    assert pylopdf.open(stream=pdf)[0].find_tables().tables == []


def test_opt_in_text_strategy_finds_borderless_table() -> None:
    """Require explicit text strategy for sustained aligned rows."""
    page = pylopdf.open(stream=_borderless_table_pdf())[0]

    assert page.find_tables().tables == []
    table = page.find_tables(strategy="text")[0]
    assert (table.row_count, table.col_count) == (3, 2)
    assert table.strategy == "text"
    assert 0.0 <= table.confidence <= 1.0
    assert table.confidence == pytest.approx(0.9333333333333333)
    assert table.diagnostics.alignment_error_em == pytest.approx(0.0)
    assert table.diagnostics.minimum_gutter_em is not None
    assert table.diagnostics.minimum_gutter_em > 2.0
    assert table.diagnostics.row_gap_variation_em == pytest.approx(0.0)
    assert table.extract() == [["Name", "Value"], ["Alpha", "42"], ["Beta", "7"]]

    clipped = page.find_tables(strategy="text", clip=table.bbox)
    assert clipped.strategy == "text"
    assert clipped.clip == table.bbox
    assert clipped[0].extract() == table.extract()


def test_text_strategy_requires_three_rows() -> None:
    """Reject short aligned pairs that are likely ordinary page layout."""
    page = pylopdf.open(stream=_borderless_table_pdf(rows=2))[0]
    assert page.find_tables(strategy="text").tables == []


def test_text_table_confidence_tracks_alignment_evidence() -> None:
    """Lower the heuristic score when one accepted row aligns less precisely."""
    perfect_pdf = _borderless_table_pdf()
    shifted_pdf = perfect_pdf.replace(b"40 180 Td (Beta)", b"50 180 Td (Beta)")

    perfect = pylopdf.open(stream=perfect_pdf)[0].find_tables(strategy="text")[0]
    shifted = pylopdf.open(stream=shifted_pdf)[0].find_tables(strategy="text")[0]

    assert shifted.diagnostics.alignment_error_em is not None
    assert perfect.diagnostics.alignment_error_em is not None
    assert shifted.diagnostics.alignment_error_em > perfect.diagnostics.alignment_error_em
    assert shifted.confidence < perfect.confidence


def test_find_tables_rejects_unknown_strategy() -> None:
    page = pylopdf.open(stream=_borderless_table_pdf())[0]
    with pytest.raises(ValueError, match="strategy"):
        page.find_tables(strategy="guess")  # type: ignore[arg-type]
