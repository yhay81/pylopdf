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
    assert table.extract() == [["Name", "Value"], ["Alpha", "42"]]
    assert table.to_markdown() == "| Name | Value |\n| --- | --- |\n| Alpha | 42 |"


def test_find_tables_returns_empty_for_plain_text() -> None:
    page = pylopdf.open(stream=build_pdf(["Not a table"]))[0]
    finder = page.find_tables()
    assert len(finder) == 0
    assert finder.tables == []
    assert finder.cells == []


def test_incomplete_grid_is_not_reported_as_a_table() -> None:
    """Require every cell edge instead of guessing across a broken grid."""
    pdf = _bordered_table_pdf().replace(b"170 180 m 170 260 l", b"170 180 m 170 220 l")
    assert pylopdf.open(stream=pdf)[0].find_tables().tables == []
