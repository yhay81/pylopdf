"""Remove objects unreachable from the trailer after edits and garbage saves."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from conftest import build_pdf, build_raw_pdf, outline_entries, outline_tuples

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
    doc = pylopdf.open(_reachability_pdf())
    assert doc.complexity["object_count"] == 14

    saved = doc.to_bytes()

    for kept in (b"KEPT-NESTED", b"KEPT-STREAM-DICTIONARY", b"KEPT-INFO"):
        assert kept in saved
    for dropped in (b"DROPPED-CONTENT-ONLY", b"DROPPED-CYCLE", b"DROPPED-ORPHAN"):
        assert dropped not in saved
    reloaded = pylopdf.open(saved)
    for current in (doc, reloaded):
        assert current.complexity["object_count"] == 10
        assert current.metadata["title"] == "KEPT-INFO"
        assert "Body text" in current.pages[0].text()


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


def _delete_page(doc: pylopdf.Document, index: int) -> None:
    del doc.pages[index]


@pytest.mark.parametrize(
    ("edit", "dropped"),
    [
        (lambda doc: _delete_page(doc, 1), [b"SECOND-PAGE-CONTENT"]),
        (lambda doc: doc.pages.select([0]), [b"SECOND-PAGE-CONTENT"]),
        (lambda doc: doc.pages.new(), []),
        (lambda doc: doc.pages.extend(doc.pages[0]), []),
        (lambda doc: setattr(doc, "outline", outline_entries([(1, "New outline", 0)])), [b"OLD-OUTLINE-TITLE"]),
        (lambda doc: setattr(doc, "outline", outline_entries([])), [b"OLD-OUTLINE-TITLE"]),
        (lambda doc: doc.pages.extend(pylopdf.open(build_pdf(["Inserted"])).pages), []),
    ],
    ids=["delete_pages", "select", "new_page", "copy_page", "outline", "clear_outline", "insert_pdf"],
)
def test_structural_edits_drop_unreachable_objects(
    edit: Callable[[pylopdf.Document], object],
    dropped: list[bytes],
) -> None:
    doc = pylopdf.open(_structural_edit_pdf())

    edit(doc)

    saved = doc.to_bytes()
    assert b"KEPT-PAGE-CONTENT" in saved
    for marker in [b"ORPHAN-OBJECT", *dropped]:
        assert marker not in saved
    assert "KEPT-PAGE-CONTENT" in pylopdf.open(saved).pages[0].text()


def _source_with_unreachable_objects() -> pylopdf.Document:
    """One page plus an outline, an annotation, an attachment, and an orphan."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 6 0 R /Names << /EmbeddedFiles << /Names [(a.txt) 8 0 R] >> >> >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Annots [7 0 R] /Resources << /Font << /F1 5 0 R >> >> >>",
        4: _stream("SOURCE-PAGE-TEXT"),
        5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /PylopdfProbe (KEPT-SOURCE-FONT) >>",
        6: "<< /Type /Outlines /First 10 0 R /Last 10 0 R /Count 1 >>",
        7: "<< /Type /Annot /Subtype /Text /Rect [0 0 10 10] /Contents (DROPPED-SOURCE-ANNOT) >>",
        8: "<< /Type /Filespec /F (a.txt) /EF << /F 9 0 R >> >>",
        9: "<< /Length 23 >>\nstream\nDROPPED-SOURCE-ATTACHED\nendstream",
        10: "<< /Title (DROPPED-SOURCE-OUTLINE) /Parent 6 0 R /Dest [3 0 R /Fit] >>",
        11: "(DROPPED-SOURCE-ORPHAN)",
    }
    return pylopdf.open(build_raw_pdf(objects))


def test_show_pdf_page_moves_only_the_objects_its_form_reaches() -> None:
    """Placement imports the source objects its Form XObject reaches, not the rest.

    The source page's own content stream, annotations, outline, attachments,
    and orphans stay behind. Placement no longer sweeps the target either:
    objects it already could not reach stay until a pruning save, as after
    every other drawing edit.
    """
    doc = pylopdf.open(_structural_edit_pdf())
    objects = doc.complexity["object_count"]

    doc.pages[0].show_pdf_page((0, 0, 50, 50), _source_with_unreachable_objects())

    saved = doc.to_bytes(prune=False)
    # The Form copies the source content, and its resources keep the font.
    for kept in (b"SOURCE-PAGE-TEXT", b"KEPT-SOURCE-FONT", b"KEPT-PAGE-CONTENT", b"ORPHAN-OBJECT"):
        assert kept in saved
    for dropped in (b"SOURCE-ANNOT", b"SOURCE-ATTACHED", b"SOURCE-OUTLINE", b"SOURCE-ORPHAN"):
        assert b"DROPPED-" + dropped not in saved
    # The font, the Form XObject, the new content stream, and the q/Q pair
    # that isolates the page's existing content.
    assert doc.complexity["object_count"] == objects + 5
    assert list(doc.attachments) == []
    assert [entry.title for entry in doc.outline.entries()] == ["OLD-OUTLINE-TITLE"]
    assert b"ORPHAN-OBJECT" not in doc.to_bytes()
    assert "SOURCE-PAGE-TEXT" in pylopdf.open(saved).pages[0].text()


def test_structural_edits_on_tens_of_thousands_of_objects_stay_fast() -> None:
    """Pruning is linear in object count.

    lopdf's own `prune_objects` scans a list of visited IDs for every reference
    and every object. These three prunes over about 68,000 objects took about
    7 s locally with it; the generous bound only catches that regression.
    """
    page_count = 30_000
    doc = pylopdf.open(build_pdf([f"P{index}" for index in range(page_count)]))
    outline = [(1, f"Entry {index}", index) for index in range(4096)]

    start = time.perf_counter()
    doc.outline = outline_entries(outline)
    del doc.pages[0:1]
    saved = doc.to_bytes()
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"writing the outline, deleting a page, and a pruning save took {elapsed:.2f}s"
    assert len(doc.pages) == page_count - 1
    assert outline_tuples(doc)[:2] == [(1, "Entry 0", None), (1, "Entry 1", 0)]
    assert len(pylopdf.open(saved).pages) == page_count - 1
