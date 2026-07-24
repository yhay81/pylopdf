"""Tests for positioned text extraction and search_for."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_nonembedded_cjk_pdf, build_pdf, build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


@pytest.fixture
def usrguide() -> pylopdf.Page:
    return pylopdf.open(ASSETS / "usrguide.pdf")[0]


def test_words_have_positions_and_numbering(usrguide: pylopdf.Page) -> None:
    words = usrguide.get_text("words")
    assert len(words) > 100
    page_rect = usrguide.rect
    for x0, y0, x1, y1, text, block_no, line_no, word_no in words:
        assert x0 < x1
        assert y0 < y1
        assert -1 <= x0 <= page_rect.x1 + 1
        assert -1 <= y0 <= page_rect.y1 + 1
        assert text
        assert block_no >= 0
        assert line_no >= 0
        assert word_no >= 0
    texts = [w[4] for w in words]
    assert "authors" in texts


def test_words_numbering_is_consistent(usrguide: pylopdf.Page) -> None:
    """Keep word numbers consecutive per line and line numbers monotonic."""
    words = usrguide.get_text("words")
    seen: dict[tuple[int, int], int] = {}
    for *_, block_no, line_no, word_no in words:
        key = (block_no, line_no)
        expected = seen.get(key, 0)
        assert word_no == expected
        seen[key] = expected + 1


def test_blocks_structure(usrguide: pylopdf.Page) -> None:
    blocks = usrguide.get_text("blocks")
    assert 1 < len(blocks) < 30
    numbers = [b[5] for b in blocks]
    assert numbers == list(range(len(blocks)))
    assert all(b[6] == 0 for b in blocks)
    joined = "\n".join(b[4] for b in blocks)
    assert "for authors" in joined
    assert "Introduction" in joined


def test_dict_structure(usrguide: pylopdf.Page) -> None:
    d = usrguide.get_text("dict")
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


def test_invalid_option_raises(usrguide: pylopdf.Page) -> None:
    with pytest.raises(ValueError, match="option"):
        usrguide.get_text("html")  # type: ignore[call-overload]


def test_document_level_get_page_text_modes() -> None:
    doc = pylopdf.open(stream=build_pdf(["Alpha beta", "Gamma"]))
    words = doc.get_page_text(0, "words")
    assert [w[4] for w in words] == ["Alpha", "beta"]
    assert doc.get_page_text(1, "words")[0][4] == "Gamma"


def test_search_for_basic(usrguide: pylopdf.Page) -> None:
    hits = usrguide.search_for("authors")
    assert len(hits) >= 1
    rect = hits[0]
    assert isinstance(rect, pylopdf.Rect)
    assert rect.x0 < rect.x1
    assert rect.y0 < rect.y1


def test_search_for_is_case_insensitive(usrguide: pylopdf.Page) -> None:
    assert usrguide.search_for("AUTHORS") == usrguide.search_for("authors")


def test_search_for_multiword(usrguide: pylopdf.Page) -> None:
    """Search across words joined by a synthesized space."""
    hits = usrguide.search_for("for authors")
    assert len(hits) == 1
    only_for = usrguide.search_for("for")[0]
    assert hits[0].x0 <= only_for.x1


def test_search_for_no_match(usrguide: pylopdf.Page) -> None:
    assert usrguide.search_for("zzzz-not-in-document") == []


def test_search_for_empty_raises(usrguide: pylopdf.Page) -> None:
    with pytest.raises(ValueError, match="needle"):
        usrguide.search_for("")


def test_search_for_cjk() -> None:
    doc = pylopdf.open(ASSETS / "mhlw-doc.pdf")
    hits = doc[0].search_for("裁判例")
    assert len(hits) >= 3
    for rect in hits:
        assert rect.x0 < rect.x1
        assert rect.y0 < rect.y1


def test_search_for_nonembedded_cjk() -> None:
    """Search non-embedded CJK text whose Unicode comes from the CMap."""
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(None)
    assert len(doc[0].search_for("日本語")) == 1


def test_layout_reflects_edits() -> None:
    """Reflect the new document state in layout extraction after select."""
    doc = pylopdf.open(stream=build_pdf(["First page", "Second page"]))
    doc.select([1])
    words = doc.get_page_text(0, "words")
    assert [w[4] for w in words] == ["Second", "page"]


def test_cached_text_page_reflects_drawing_edit() -> None:
    """Invalidate reusable interpretation after a non-structural page edit."""
    doc = pylopdf.open(stream=build_pdf(["Before"]))
    page = doc[0]
    assert "Before" in page.get_text("text")
    assert page.search_for("After") == []

    page.insert_text((72, 120), "After")

    assert "After" in page.get_text("text")
    assert len(page.search_for("After")) == 1


def test_dict_reports_transformed_baseline_direction() -> None:
    """Preserve baseline geometry without mistaking rotation for vertical WMode."""
    stream = "BT /F1 24 Tf 0 1 -1 0 100 200 Tm (Up) Tj ET"
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
    line = pylopdf.open(stream=pdf)[0].get_text("dict")["blocks"][0]["lines"][0]
    direction_x, direction_y = line["dir"]
    assert direction_x == pytest.approx(0.0, abs=1e-9)
    assert abs(direction_y) == pytest.approx(1.0)
    assert line["wmode"] == 0


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

    page = pylopdf.open(stream=pdf)[0]
    expected = [
        "A heading spanning both columns",
        "Left one",
        "Left two",
        "Right one",
        "Right two",
        "A footer spanning both columns",
    ]
    assert page.get_text().splitlines() == expected
    assert [word[4] for word in page.get_text("words")] == [word for line in expected for word in line.split()]


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

    page = pylopdf.open(stream=pdf)[0]
    assert page.get_text() == "Header 1\n"
    layout = page.get_text("dict")
    assert len(layout["blocks"][0]["lines"]) == 1
