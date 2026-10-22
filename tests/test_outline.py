"""Tests for get_outline and set_outline."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf.pylopdf_core import LimitError, _Document

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
MAX_TOC_ENTRIES = 65_536
MAX_TOC_TEXT_BYTES = 16 * 1024 * 1024


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
    doc = pylopdf.open(three_page_pdf)
    assert doc.get_outline() == []
    outline = [(1, "第 1 章", 0), (2, "1.1 節", 1), (1, "第 2 章", 2)]
    doc.set_outline(outline)
    entries = doc.get_outline()
    assert entries == outline
    assert all(isinstance(entry, pylopdf.OutlineEntry) for entry in entries)
    assert entries[1].page == 1
    # Preserve entries after save/reload; CJK titles use UTF-16BE.
    reloaded = pylopdf.open(doc.to_bytes())
    assert reloaded.get_outline() == outline


def test_toc_deep_nesting(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    outline = [(1, "A", 0), (2, "B", 0), (3, "C", 1), (1, "D", 2)]
    doc.set_outline(outline)
    reloaded = pylopdf.open(doc.to_bytes())
    assert reloaded.get_outline() == outline


def test_set_outline_replaces_existing(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.set_outline([(1, "old", 0), (1, "old2", 1)])
    doc.set_outline([pylopdf.OutlineEntry(1, "new", 2)])
    assert doc.get_outline() == [(1, "new", 2)]
    assert pylopdf.open(doc.to_bytes()).get_outline() == [(1, "new", 2)]


def test_set_outline_empty_removes(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.set_outline([(1, "A", 0)])
    doc.set_outline([])
    assert doc.get_outline() == []
    assert pylopdf.open(doc.to_bytes()).get_outline() == []


def test_outline_replaces_the_one_based_toc_methods() -> None:
    doc = pylopdf.Document()
    assert not hasattr(doc, "get_toc")
    assert not hasattr(doc, "set_toc")


@pytest.mark.parametrize(
    ("entries", "match"),
    [
        ([(2, "A", 0)], "level"),  # The first level must be 1.
        ([(1, "A", 0), (3, "B", 0)], "level"),  # Level jumps greater than 1 are invalid.
        ([(1, "A", -1)], "out of range"),  # Outline page numbers are zero-based.
        ([(1, "A", 3)], "out of range"),
        ([(1, "A")], "level, title, page"),
    ],
)
def test_set_outline_invalid(three_page_pdf: bytes, entries: list[tuple[int | str, ...]], match: str) -> None:
    doc = pylopdf.open(three_page_pdf)
    with pytest.raises(ValueError, match=match):
        doc.set_outline(entries)


def test_outline_survives_page_ops(three_page_pdf: bytes) -> None:
    """Keep outline entries readable after select while target pages remain."""
    doc = pylopdf.open(three_page_pdf)
    doc.set_outline([(1, "first", 0), (1, "third", 2)])
    doc.pages.select([0, 2])
    outline = doc.get_outline()
    assert [entry.title for entry in outline] == ["first", "third"]
    assert [entry.page for entry in outline] == [0, 1]  # Page numbers are compacted.


def test_real_world_outline_readable() -> None:
    """Read consistent existing outlines from real-world PDFs."""
    for name in ["usrguide.pdf", "bill-hr815.pdf", "f1040.pdf"]:
        doc = pylopdf.open(REAL_WORLD / name)
        for level, title, page in doc.get_outline():
            assert isinstance(level, int)
            assert isinstance(title, str)
            assert isinstance(page, int)
            assert level >= 1
            assert 0 <= page < len(doc.pages)


def test_toc_next_cycle_is_visited_once() -> None:
    doc = pylopdf.open(
        _build_outline_fixture(
            {
                4: "<< /First 5 0 R >>",
                5: "<< /Title (A) /Dest [3 0 R /Fit] /Next 5 0 R >>",
            }
        )
    )
    assert doc.get_outline() == [(1, "A", 0)]


def test_toc_destination_reference_cycle_is_visited_once() -> None:
    doc = pylopdf.open(
        _build_outline_fixture(
            {
                4: "<< /First 5 0 R >>",
                5: "<< /Title (A) /Dest 6 0 R >>",
                6: "<< /D 6 0 R >>",
            }
        )
    )
    assert doc.get_outline() == []


def test_toc_rejects_excessive_destination_depth() -> None:
    objects: dict[int, str | bytes] = {
        4: "<< /First 5 0 R >>",
        5: "<< /Title (A) /Dest 6 0 R >>",
    }
    for object_id in range(6, 38):
        objects[object_id] = f"<< /D {object_id + 1} 0 R >>"
    objects[38] = "[3 0 R /Fit]"
    doc = pylopdf.open(_build_outline_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        doc.get_outline()


def test_toc_rejects_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    for object_id in range(5, 69):
        objects[object_id] = f"<< /First {object_id + 1} 0 R >>"
    objects[69] = "<< >>"
    doc = pylopdf.open(_build_outline_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
        doc.get_outline()


def _outline_chain(count: int, entry: str = "") -> dict[int, str | bytes]:
    """Return ``count`` sibling outline items linked from object 5."""
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    last = 4 + count
    for object_id in range(5, last + 1):
        next_entry = f" /Next {object_id + 1} 0 R" if object_id < last else ""
        objects[object_id] = f"<<{entry}{next_entry} >>"
    return objects


def test_toc_reads_entries_beyond_the_former_4096_limit() -> None:
    doc = pylopdf.open(_build_outline_fixture(_outline_chain(4_097, " /Title (A) /Dest [3 0 R /Fit]")))
    assert doc.get_outline() == [(1, "A", 0)] * 4_097


def test_toc_rejects_excessive_nodes() -> None:
    doc = pylopdf.open(_build_outline_fixture(_outline_chain(MAX_TOC_ENTRIES + 1)))
    with pytest.raises(pylopdf.LimitError, match="65536-node safety limit") as caught:
        doc.get_outline()
    assert caught.value.code == "toc_entry_count"


def test_toc_rejects_excessive_edges() -> None:
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    last = 4 + MAX_TOC_ENTRIES
    for object_id in range(5, last + 1):
        next_id = object_id + 1 if object_id < last else 5
        objects[object_id] = f"<< /First 5 0 R /Next {next_id} 0 R >>"
    doc = pylopdf.open(_build_outline_fixture(objects))
    with pytest.raises(pylopdf.LimitError, match="131072-edge safety limit") as caught:
        doc.get_outline()
    assert caught.value.code == "toc_entry_count"


def test_toc_rejects_excessive_title_bytes() -> None:
    title = "x" * (1024 * 1024)
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    for object_id in range(5, 22):
        next_entry = f" /Next {object_id + 1} 0 R" if object_id < 21 else ""
        objects[object_id] = f"<< /Title ({title}) /Dest [3 0 R /Fit]{next_entry} >>"
    doc = pylopdf.open(_build_outline_fixture(objects))
    with pytest.raises(pylopdf.LimitError, match="TOC source text exceeds the 16777216-byte safety limit") as caught:
        doc.get_outline()
    assert caught.value.code == "toc_text_size"


def test_page_cleanup_shares_the_toc_node_limit() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
        4: "<< /First 6 0 R >>",
        5: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    }
    last = 5 + MAX_TOC_ENTRIES + 1
    for object_id in range(6, last + 1):
        next_entry = f" /Next {object_id + 1} 0 R" if object_id < last else ""
        objects[object_id] = f"<< /Title (A) /Dest [5 0 R /Fit]{next_entry} >>"
    doc = pylopdf.open(build_raw_pdf(objects))
    page = doc.pages[0]

    def delete() -> None:
        del doc.pages[1]

    for operation in (delete, lambda: doc.pages.select([0])):
        with pytest.raises(pylopdf.LimitError, match="65536-node safety limit") as caught:
            operation()
        assert caught.value.code == "toc_entry_count"
        assert len(doc.pages) == 2
        assert page.index == 0
        assert page.rect.width == 100


def test_set_outline_rejects_excessive_entries(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    with pytest.raises(pylopdf.LimitError, match="65536 entries") as caught:
        doc.set_outline([(1, "A", 0)] * (MAX_TOC_ENTRIES + 1))
    assert caught.value.code == "toc_entry_count"
    assert doc.get_outline() == []

    core = _Document.load_bytes(three_page_pdf)
    with pytest.raises(LimitError, match="65536 TOC entries") as core_caught:
        core.set_toc([(1, "A", 1)] * (MAX_TOC_ENTRIES + 1))
    assert core_caught.value.args[0] == "toc_entry_count"
    assert core.get_toc() == []


def test_set_outline_limits_are_atomic(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    original = [(1, "original", 0)]
    doc.set_outline(original)

    with pytest.raises(pylopdf.LimitError, match="16777216-byte encoded-text safety limit") as caught:
        doc.set_outline([(1, "é" * (MAX_TOC_TEXT_BYTES // 2), 0)])
    assert caught.value.code == "toc_input_size"
    assert doc.get_outline() == original

    too_deep = [(level, str(level), 0) for level in range(1, 66)]
    with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
        doc.set_outline(too_deep)
    assert doc.get_outline() == original
