"""Tests for get_toc and set_toc."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"


def _build_outline_fixture(outline_objects: dict[int, str | bytes]) -> bytes:
    """Build one page with a caller-provided outline graph rooted at object 4."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    }
    objects.update(outline_objects)
    return build_raw_pdf(objects)


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


def test_toc_next_cycle_is_visited_once() -> None:
    doc = pylopdf.open(
        stream=_build_outline_fixture(
            {
                4: "<< /First 5 0 R >>",
                5: "<< /Title (A) /Dest [3 0 R /Fit] /Next 5 0 R >>",
            }
        )
    )
    assert doc.get_toc() == [[1, "A", 1]]


def test_toc_rejects_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    for object_id in range(5, 69):
        objects[object_id] = f"<< /First {object_id + 1} 0 R >>"
    objects[69] = "<< >>"
    doc = pylopdf.open(stream=_build_outline_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
        doc.get_toc()


def test_toc_rejects_excessive_nodes() -> None:
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    for object_id in range(5, 4102):
        next_entry = f" /Next {object_id + 1} 0 R" if object_id < 4101 else ""
        objects[object_id] = f"<<{next_entry} >>"
    doc = pylopdf.open(stream=_build_outline_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="4096-node safety limit"):
        doc.get_toc()


def test_toc_rejects_excessive_edges() -> None:
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    for object_id in range(5, 4101):
        next_id = object_id + 1 if object_id < 4100 else 5
        objects[object_id] = f"<< /First 5 0 R /Next {next_id} 0 R >>"
    doc = pylopdf.open(stream=_build_outline_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
        doc.get_toc()


def test_toc_rejects_excessive_title_bytes() -> None:
    title = "x" * 1024
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    for object_id in range(5, 1030):
        next_entry = f" /Next {object_id + 1} 0 R" if object_id < 1029 else ""
        objects[object_id] = f"<< /Title ({title}) /Dest [3 0 R /Fit]{next_entry} >>"
    doc = pylopdf.open(stream=_build_outline_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="TOC source text exceeds the 1048576-byte safety limit"):
        doc.get_toc()


def test_set_toc_rejects_excessive_entries(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=three_page_pdf)
    with pytest.raises(ValueError, match="4096 entries"):
        doc.set_toc([[1, "A", 1]] * 4097)


def test_set_toc_limits_are_atomic(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=three_page_pdf)
    original: list[list[int | str]] = [[1, "original", 1]]
    doc.set_toc(original)

    with pytest.raises(pylopdf.LimitError, match="1048576-byte encoded-text safety limit") as caught:
        doc.set_toc([[1, "é" * 524288, 1]])
    assert caught.value.code == "toc_input_size"
    assert doc.get_toc() == original

    too_deep: list[list[int | str]] = [[level, str(level), 1] for level in range(1, 66)]
    with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
        doc.set_toc(too_deep)
    assert doc.get_toc() == original
