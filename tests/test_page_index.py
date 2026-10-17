"""Per-page access reuses one page index until the document changes.

``len(doc)``, ``doc[i]``, and per-page reads once walked the complete page tree
on every call, so touching every page cost O(N^2). Every edit except rotation
and page-box changes drops the index, and the next access rebuilds it, so reads
after every kind of edit must match a document that never cached anything, and
failed walks must keep failing.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf
from pylopdf.pylopdf_core import _Document

if TYPE_CHECKING:
    from collections.abc import Callable

PAGES = 5
# Page i is 100 + 10 * i units wide, so widths identify pages after edits.
SPECIAL_WIDTHS = {"new": 50.0, "other": 612.0, "resized": 300.0}


def _nested_pdf() -> bytes:
    """Build a two-level page tree whose pages differ in size, rotation, and text."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 5 /Resources << /Font << /F1 5 0 R >> >> >>",
        3: "<< /Type /Pages /Parent 2 0 R /Kids [6 0 R 8 0 R] /Count 2 >>",
        4: "<< /Type /Pages /Parent 2 0 R /Kids [10 0 R 12 0 R 14 0 R] /Count 3 /Rotate 90 >>",
        5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page in range(PAGES):
        parent = 3 if page < 2 else 4
        # Pages 2 and 3 inherit their rotation from the intermediate node.
        rotate = "" if page in (2, 3) else f" /Rotate {90 * page % 360}"
        stream = f"BT /F1 12 Tf 10 100 Td (Page {page}) Tj ET"
        objects[6 + 2 * page] = (
            f"<< /Type /Page /Parent {parent} 0 R /MediaBox [0 0 {100 + 10 * page} 200]{rotate} "
            f"/Contents {7 + 2 * page} 0 R >>"
        )
        objects[7 + 2 * page] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    return build_raw_pdf(objects)


def _widths(pages: list[int | str]) -> list[float]:
    return [SPECIAL_WIDTHS[page] if isinstance(page, str) else float(100 + 10 * page) for page in pages]


def _other() -> pylopdf.Document:
    return pylopdf.Document(stream=build_pdf(["Other"]))


def _snapshot(doc: pylopdf.Document) -> tuple[int, list[tuple[object, ...]], list[list[int | str]]]:
    """Return every page-index-backed read: count, per-page facts, and the TOC."""
    pages: list[tuple[object, ...]] = [
        (page.mediabox.x1, page.rotation, page.rect, page.get_text().strip(), page.annots()) for page in doc
    ]
    return len(doc), pages, doc.get_toc()


UNCHANGED: list[int | str] = list(range(PAGES))
# Each edit maps to its expected page order: original page numbers or special pages.
EDITS: dict[str, tuple[Callable[[pylopdf.Document], object], list[int | str]]] = {
    "delete_pages": (lambda doc: doc.delete_pages([1, 3]), [0, 2, 4]),
    "delete_page": (lambda doc: doc.delete_page(-1), [0, 1, 2, 3]),
    "select": (lambda doc: doc.select([4, 0, 2, 0]), [4, 0, 2, 0]),
    "insert_pdf": (lambda doc: doc.insert_pdf(_other(), start_at=2), [0, 1, "other", 2, 3, 4]),
    "insert_pdf_append": (lambda doc: doc.insert_pdf(_other()), [*UNCHANGED, "other"]),
    "new_page": (lambda doc: doc.new_page(1, width=50, height=60), [0, "new", 1, 2, 3, 4]),
    "copy_page": (lambda doc: doc.copy_page(3, 0), [3, *UNCHANGED]),
    "set_toc": (lambda doc: doc.set_toc([[1, "Last", 5], [1, "First", 1]]), UNCHANGED),
    "set_rotation": (lambda doc: doc[2].set_rotation(270), UNCHANGED),
    "set_mediabox": (lambda doc: doc[1].set_mediabox((0, 0, 300, 400)), [0, "resized", 2, 3, 4]),
    "insert_text": (lambda doc: doc[0].insert_text((10, 20), "Stamp"), UNCHANGED),
    "add_link_annot": (lambda doc: doc[3].add_link_annot((1, 1, 20, 20), "https://example.com/"), UNCHANGED),
    "garbage_tobytes": (lambda doc: doc.tobytes(garbage=True), UNCHANGED),
    "embfile_add": (lambda doc: doc.embfile_add("notes.txt", b"notes"), UNCHANGED),
}


@pytest.mark.parametrize("edit", EDITS)
def test_page_reads_after_an_edit_match_a_fresh_document(edit: str) -> None:
    doc = pylopdf.Document(stream=_nested_pdf())
    # Warm the index through every per-page read before editing.
    assert [width for width, *_ in _snapshot(doc)[1]] == _widths(UNCHANGED)
    apply, order = EDITS[edit]

    apply(doc)

    # Read the edited document first: saving drops the index itself.
    cached = _snapshot(doc)
    assert cached == _snapshot(pylopdf.Document(stream=doc.tobytes()))
    assert [width for width, *_ in cached[1]] == _widths(order)


def test_structural_edits_still_invalidate_page_views() -> None:
    doc = pylopdf.Document(stream=_nested_pdf())
    views = list(doc)

    # A non-structural edit keeps views valid, and they see its result.
    doc[3].set_rotation(180)
    assert [view.rotation for view in views] == [0, 90, 90, 180, 0]

    doc.select([4, 3, 2, 1, 0])
    for view in views:
        with pytest.raises(pylopdf.StalePageError):
            _ = view.rotation
    assert [page.mediabox.x1 for page in doc] == _widths([4, 3, 2, 1, 0])
    assert [page.rotation for page in doc] == [0, 180, 90, 90, 0]


@pytest.mark.parametrize(
    ("objects", "message"),
    [
        pytest.param(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [2 0 R] /Count 1 >>",
            },
            "cycle",
            id="cycle",
        ),
        pytest.param(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 3 0 R] /Count 2 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            },
            "reuses object",
            id="reused-page",
        ),
        pytest.param(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids 3 0 R /Count 1 >>",
                3: "3 0 R",
            },
            "Kids contains a reference cycle",
            id="kids-reference-cycle",
        ),
    ],
)
def test_failed_page_tree_walks_are_never_cached(objects: dict[int, str | bytes], message: str) -> None:
    doc = pylopdf.open(stream=build_raw_pdf(objects))

    for _ in range(2):
        with pytest.raises(pylopdf.PdfError, match=message):
            _ = len(doc)
        with pytest.raises(pylopdf.PdfError, match=message):
            _ = doc[0]
        with pytest.raises(pylopdf.PdfError, match=message):
            _ = doc.complexity


def test_index_follows_a_prune_that_changes_the_walk_outcome() -> None:
    """The page-tree edge bound depends on the object count, which pruning lowers."""
    doc = pylopdf.open(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R null null null] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: "<< /Unreferenced true >>",
            }
        )
    )
    # Four Kids edges fit the bound of four objects.
    assert len(doc) == 1
    assert doc[0].mediabox.x1 == 100

    saved = doc.tobytes(garbage=True)

    for current in (doc, pylopdf.open(stream=saved)):
        with pytest.raises(pylopdf.PdfError, match="3-edge object-count safety limit"):
            _ = len(current)


def _flat_pdf(pages: int) -> bytes:
    kids = " ".join(f"{3 + page} 0 R" for page in range(pages))
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} /MediaBox [0 0 200 300] >>",
    }
    for page in range(pages):
        objects[3 + page] = f"<< /Type /Page /Parent 2 0 R /Rotate {90 * (page % 4)} >>"
    return build_raw_pdf(objects)


def test_touching_every_page_walks_the_page_tree_once() -> None:
    """Page access and per-page reads reuse one page index until an edit.

    Walking the complete page tree for every ``doc[i]`` and every page read
    made this loop take about 18 s of CPU time locally, against about 0.02 s
    now; the generous bound only catches that regression.
    """
    pages = 5_000
    doc = pylopdf.Document(stream=_flat_pdf(pages))

    start = time.perf_counter()
    views = list(doc)
    rotations = [page.rotation for page in views]
    rects = [page.rect for page in views]
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"reading {pages} pages took {elapsed:.2f}s"
    assert rotations == [90 * (page % 4) for page in range(pages)]
    assert rects == [pylopdf.Rect(0, 0, *((300, 200) if page % 2 else (200, 300))) for page in range(pages)]


def test_rotating_and_cropping_every_page_keeps_the_page_index() -> None:
    """Rotation and page-box edits cannot change the page tree.

    They keep the index, so a loop over ``doc`` that rotates and crops every
    page stays linear. Walking the tree again for each ``doc[i]`` after an
    edit took about 14 s of CPU time locally, against about 0.01 s now.
    """
    pages = 5_000
    doc = pylopdf.Document(stream=_flat_pdf(pages))

    start = time.perf_counter()
    for page in doc:
        page.set_rotation(page.rotation + 90)
        page.set_cropbox((0, 0, 100, 150 + page.number % 2))
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"editing {pages} pages took {elapsed:.2f}s"
    rotations = [90 * (page % 4 + 1) % 360 for page in range(pages)]
    assert [page.rotation for page in doc] == rotations
    assert [page.cropbox.y1 for page in doc] == [150 + page % 2 for page in range(pages)]
    fresh = pylopdf.Document(stream=doc.tobytes())
    assert [page.rotation for page in fresh] == rotations


def test_other_page_entries_drop_the_page_index() -> None:
    """Only rotation and page-box entries keep the index; ``/Type`` retypes the page."""
    core = _Document.load_bytes(_flat_pdf(3))
    assert core.page_count() == 3

    core.set_page_box(1, "Type", 0, 0, 1, 1)

    assert core.page_count() == 2
    assert _Document.load_bytes(core.save_bytes()).page_count() == 2
