"""位置付きテキスト抽出（words / blocks / dict）と search_for のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_nonembedded_cjk_pdf, build_pdf

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
    """語番号は行内で 0 から連番、行番号はブロック内で単調。"""
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
    """語をまたぐ検索（合成空白を挟んだ一致）。"""
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
    """非埋め込み CJK でも検索できる（Unicode は CMap 由来のため）。"""
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(None)
    assert len(doc[0].search_for("日本語")) == 1


def test_layout_reflects_edits() -> None:
    """編集（select）後のレイアウト抽出が新しい状態を反映する。"""
    doc = pylopdf.open(stream=build_pdf(["First page", "Second page"]))
    doc.select([1])
    words = doc.get_page_text(0, "words")
    assert [w[4] for w in words] == ["Second", "page"]
