"""Remove objects unreachable from the trailer after edits and garbage saves."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def _stream(text: str) -> str:
    content = f"BT /F1 20 Tf 20 100 Td ({text}) Tj ET"
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"


def _reachability_pdf() -> bytes:
    """Cover each reference path that pruning follows and each it must not."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /PylopdfProbe 5 0 R /Missing 99 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] /Resources << /Font << /F1 11 0 R >> >> >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        # Stream dictionaries are followed, but stream content is never parsed.
        4: _stream("Body text 8 0 R").replace(" >>", " /PylopdfProbe 12 0 R >>", 1),
        # A reference-valued indirect object is followed like any other value.
        5: "6 0 R",
        6: f"<< /Nested {'[' * 64}<< /Deep 7 0 R >>{']' * 64} >>",
        7: "(KEPT-NESTED)",
        8: "(DROPPED-CONTENT-ONLY)",
        9: "<< /Next 10 0 R /Marker (DROPPED-CYCLE) >>",
        10: "<< /Next 9 0 R >>",
        11: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        12: "(KEPT-STREAM-DICTIONARY)",
        13: "<< /Title (KEPT-INFO) >>",
        14: "(DROPPED-ORPHAN)",
    }
    # The trailer follows the xref table, so adding /Info moves no recorded offset.
    return build_raw_pdf(objects).replace(b"/Root 1 0 R >>", b"/Root 1 0 R /Info 13 0 R >>", 1)


def test_garbage_save_keeps_exactly_the_objects_reachable_from_the_trailer() -> None:
    doc = pylopdf.Document(stream=_reachability_pdf())
    assert doc.complexity["object_count"] == 14

    saved = doc.tobytes(garbage=True)

    for kept in (b"KEPT-NESTED", b"KEPT-STREAM-DICTIONARY", b"KEPT-INFO"):
        assert kept in saved
    for dropped in (b"DROPPED-CONTENT-ONLY", b"DROPPED-CYCLE", b"DROPPED-ORPHAN"):
        assert dropped not in saved
    reloaded = pylopdf.Document(stream=saved)
    for current in (doc, reloaded):
        assert current.complexity["object_count"] == 10
        assert current.metadata["title"] == "KEPT-INFO"
        assert "Body text" in current.get_page_text(0)


def _structural_edit_pdf() -> bytes:
    """Two pages, a one-entry outline, and one object nothing references."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 7 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] "
        "/Resources << /Font << /F1 9 0 R >> >> >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
        4: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R >>",
        5: _stream("KEPT-PAGE-CONTENT"),
        6: _stream("SECOND-PAGE-CONTENT"),
        7: "<< /Type /Outlines /First 8 0 R /Last 8 0 R /Count 1 >>",
        8: "<< /Title (OLD-OUTLINE-TITLE) /Parent 7 0 R /Dest [3 0 R /Fit] >>",
        9: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        10: "(ORPHAN-OBJECT)",
    }
    return build_raw_pdf(objects)


def _show_stamp(doc: pylopdf.Document) -> None:
    doc[0].show_pdf_page((0, 0, 50, 50), pylopdf.Document(stream=build_pdf(["Stamp"])))


@pytest.mark.parametrize(
    ("edit", "dropped"),
    [
        (lambda doc: doc.delete_pages([1]), [b"SECOND-PAGE-CONTENT"]),
        (lambda doc: doc.select([0]), [b"SECOND-PAGE-CONTENT"]),
        (lambda doc: doc.new_page(), []),
        (lambda doc: doc.copy_page(0), []),
        (lambda doc: doc.set_toc([[1, "New outline", 1]]), [b"OLD-OUTLINE-TITLE"]),
        (lambda doc: doc.set_toc([]), [b"OLD-OUTLINE-TITLE"]),
        (lambda doc: doc.insert_pdf(pylopdf.Document(stream=build_pdf(["Inserted"]))), []),
        (_show_stamp, []),
    ],
    ids=["delete_pages", "select", "new_page", "copy_page", "set_toc", "clear_toc", "insert_pdf", "show_pdf_page"],
)
def test_structural_edits_drop_unreachable_objects(
    edit: Callable[[pylopdf.Document], object],
    dropped: list[bytes],
) -> None:
    doc = pylopdf.Document(stream=_structural_edit_pdf())

    edit(doc)

    saved = doc.tobytes()
    assert b"KEPT-PAGE-CONTENT" in saved
    for marker in [b"ORPHAN-OBJECT", *dropped]:
        assert marker not in saved
    assert "KEPT-PAGE-CONTENT" in pylopdf.Document(stream=saved).get_page_text(0)


def test_structural_edits_on_tens_of_thousands_of_objects_stay_fast() -> None:
    """Pruning is linear in object count.

    lopdf's own `prune_objects` scans a list of visited IDs for every reference
    and every object. These three prunes over about 68,000 objects took about
    7 s locally with it; the generous bound only catches that regression.
    """
    page_count = 30_000
    doc = pylopdf.Document(stream=build_pdf([f"P{index}" for index in range(page_count)]))
    toc: list[list[int | str]] = [[1, f"Entry {index}", index + 1] for index in range(4096)]

    start = time.perf_counter()
    doc.set_toc(toc)
    doc.delete_pages([0])
    saved = doc.tobytes(garbage=True)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"set_toc, delete_pages, and a garbage save took {elapsed:.2f}s"
    assert doc.page_count == page_count - 1
    assert doc.get_toc()[0] == [1, "Entry 1", 1]
    assert pylopdf.Document(stream=saved).page_count == page_count - 1
