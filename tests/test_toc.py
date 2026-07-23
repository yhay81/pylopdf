"""Tests for get_toc and set_toc."""

from __future__ import annotations

from pathlib import Path

import pytest

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"


def test_toc_roundtrip(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    assert doc.get_toc() == []
    toc: list[list[int | str]] = [[1, "第 1 章", 1], [2, "1.1 節", 2], [1, "第 2 章", 3]]
    doc.set_toc(toc)
    assert doc.get_toc() == toc
    # Preserve entries after save/reload; CJK titles use UTF-16BE.
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.get_toc() == toc


def test_toc_deep_nesting(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    toc: list[list[int | str]] = [[1, "A", 1], [2, "B", 1], [3, "C", 2], [1, "D", 3]]
    doc.set_toc(toc)
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.get_toc() == toc


def test_set_toc_replaces_existing(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.set_toc([[1, "old", 1], [1, "old2", 2]])
    doc.set_toc([[1, "new", 3]])
    assert doc.get_toc() == [[1, "new", 3]]
    assert pylopdf.Document(stream=doc.tobytes()).get_toc() == [[1, "new", 3]]


def test_set_toc_empty_removes(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.set_toc([[1, "A", 1]])
    doc.set_toc([])
    assert doc.get_toc() == []
    assert pylopdf.Document(stream=doc.tobytes()).get_toc() == []


@pytest.mark.parametrize(
    ("toc", "match"),
    [
        ([[2, "A", 1]], "level"),  # The first level must be 1.
        ([[1, "A", 1], [3, "B", 1]], "level"),  # Level jumps greater than 1 are invalid.
        ([[1, "A", 0]], "out of range"),  # TOC page numbers are one-based.
        ([[1, "A", 4]], "out of range"),
        ([[1, "A"]], "3 elements"),
    ],
)
def test_set_toc_invalid(three_page_pdf: bytes, toc: list[list[int | str]], match: str) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(ValueError, match=match):
        doc.set_toc(toc)


def test_toc_survives_page_ops(three_page_pdf: bytes) -> None:
    """Keep TOC entries readable after select while target pages remain."""
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.set_toc([[1, "first", 1], [1, "third", 3]])
    doc.select([0, 2])
    toc = doc.get_toc()
    assert [entry[1] for entry in toc] == ["first", "third"]
    assert [entry[2] for entry in toc] == [1, 2]  # Page numbers are compacted.


def test_real_world_toc_readable() -> None:
    """Read consistent existing outlines from real-world PDFs."""
    for name in ["usrguide.pdf", "bill-hr815.pdf", "f1040.pdf"]:
        doc = pylopdf.open(REAL_WORLD / name)
        for level, title, page in doc.get_toc():
            assert isinstance(level, int)
            assert isinstance(title, str)
            assert isinstance(page, int)
            assert level >= 1
            assert 1 <= page <= doc.page_count
