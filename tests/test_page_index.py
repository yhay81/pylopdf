"""Per-page access reuses one page index until the document changes.

``len(doc)``, ``doc[i]``, and per-page reads once walked the complete page tree
on every call, so touching every page cost O(N^2). Rotation, page-box, drawing,
and annotation edits cannot change the page tree and keep the index; every
other edit drops it, and the next access rebuilds it. Reads after every kind of
edit must therefore match a document that never cached anything, including in
malformed files whose page objects alias other structures, and failed walks
must keep failing.
"""

from __future__ import annotations

import struct
import time
import zlib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import build_pdf, build_raw_pdf, outline_entries, outline_tuples

import pylopdf
from pylopdf.pylopdf_core import _Document

if TYPE_CHECKING:
    from collections.abc import Callable

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
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
    return pylopdf.open(build_pdf(["Other"]))


def _png() -> bytes:
    """Encode a 1x1 red PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


PNG = _png()
# Edits only read the stamp, so one source serves every placement.
STAMP = pylopdf.open(build_pdf(["Stamp"]))


def _snapshot(doc: pylopdf.Document) -> tuple[int, list[tuple[object, ...]], list[tuple[int, str, int | None]]]:
    """Return every page-index-backed read: count, per-page facts, and the TOC.

    Text extraction serializes the document for its renderer, which drops the
    index, so the other reads cover every page first.
    """
    count = len(doc.pages)
    views = list(doc.pages)
    facts = [
        (
            page.boxes.media.x1,
            page.rotation,
            page.rect,
            [(annot.type, annot.rect, annot.xref) for annot in page.annots()],
            [(link.rect, link.target) for link in page.links],
        )
        for page in views
    ]
    toc = outline_tuples(doc)
    texts = [page.text().strip() for page in views]
    pages: list[tuple[object, ...]] = [
        (width, rotation, rect, text, annots, links)
        for (width, rotation, rect, annots, links), text in zip(facts, texts, strict=True)
    ]
    return count, pages, toc


def _delete_pages(doc: pylopdf.Document, index: int | slice) -> None:
    del doc.pages[index]


UNCHANGED: list[int | str] = list(range(PAGES))
# Each edit maps to its expected page order: original page numbers or special pages.
EDITS: dict[str, tuple[Callable[[pylopdf.Document], object], list[int | str]]] = {
    "delete_pages": (lambda doc: _delete_pages(doc, slice(1, 4, 2)), [0, 2, 4]),
    "delete_page": (lambda doc: _delete_pages(doc, -1), [0, 1, 2, 3]),
    "select": (lambda doc: doc.pages.select([4, 0, 2, 0]), [4, 0, 2, 0]),
    "insert_pdf": (lambda doc: doc.pages.insert(2, _other().pages), [0, 1, "other", 2, 3, 4]),
    "insert_pdf_append": (lambda doc: doc.pages.extend(_other().pages), [*UNCHANGED, "other"]),
    "new_page": (lambda doc: doc.pages.new((50, 60), index=1), [0, "new", 1, 2, 3, 4]),
    "copy_page": (lambda doc: doc.pages.insert(0, doc.pages[3]), [3, *UNCHANGED]),
    "outline": (lambda doc: setattr(doc, "outline", outline_entries([(1, "Last", 4), (1, "First", 0)])), UNCHANGED),
    "set_rotation": (lambda doc: setattr(doc.pages[2], "rotation", 270), UNCHANGED),
    "set_mediabox": (lambda doc: setattr(doc.pages[1].boxes, "media", (0, 0, 300, 400)), [0, "resized", 2, 3, 4]),
    "insert_text": (lambda doc: doc.pages[0].insert_text((10, 20), "Stamp"), UNCHANGED),
    "insert_embedded_text": (lambda doc: doc.pages[4].insert_text((10, 20), "日本", fontfile=NOTO_SANS_JP), UNCHANGED),
    "insert_textbox": (lambda doc: doc.pages[1].insert_textbox((5, 5, 95, 60), "Box text"), UNCHANGED),
    "insert_image": (lambda doc: doc.pages[2].insert_image((10, 10, 40, 40), stream=PNG), UNCHANGED),
    "show_pdf_page": (lambda doc: doc.pages[2].show_pdf_page((0, 0, 50, 50), STAMP), UNCHANGED),
    "show_pdf_page_self": (lambda doc: doc.pages[0].show_pdf_page((0, 0, 50, 50), doc, 3), UNCHANGED),
    "replace_text": (lambda doc: doc.pages[4].replace_text("Page", "Leaf"), UNCHANGED),
    "add_link_annot": (
        lambda doc: doc.pages[3].links.add((1, 1, 20, 20), "https://example.com/"),
        UNCHANGED,
    ),
    "add_highlight_annot": (lambda doc: doc.pages[1].add_highlight_annot((10, 10, 50, 20)), UNCHANGED),
    "pruning_to_bytes": (lambda doc: doc.to_bytes(), UNCHANGED),
    "attachments.add": (lambda doc: doc.attachments.add("notes.txt", b"notes"), UNCHANGED),
}


@pytest.mark.parametrize("edit", EDITS)
def test_page_reads_after_an_edit_match_a_fresh_document(edit: str) -> None:
    doc = pylopdf.open(_nested_pdf())
    # Warm the index through every per-page read before editing.
    assert [width for width, *_ in _snapshot(doc)[1]] == _widths(UNCHANGED)
    apply, order = EDITS[edit]

    apply(doc)

    # Read the edited document first: saving drops the index itself.
    cached = _snapshot(doc)
    assert cached == _snapshot(pylopdf.open(doc.to_bytes()))
    assert [width for width, *_ in cached[1]] == _widths(order)


def test_structural_edits_still_invalidate_page_views() -> None:
    doc = pylopdf.open(_nested_pdf())
    views = list(doc.pages)

    # A non-structural edit keeps views valid, and they see its result.
    doc.pages[3].rotation = 180
    assert [view.rotation for view in views] == [0, 90, 90, 180, 0]

    doc.pages.select([4, 3, 2, 1, 0])
    for view in views:
        with pytest.raises(pylopdf.StalePageError):
            _ = view.rotation
    assert [page.boxes.media.x1 for page in doc.pages] == _widths([4, 3, 2, 1, 0])
    assert [page.rotation for page in doc.pages] == [0, 180, 90, 90, 0]


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
    doc = pylopdf.open(build_raw_pdf(objects))

    for _ in range(2):
        with pytest.raises(pylopdf.PdfError, match=message):
            _ = len(doc.pages)
        with pytest.raises(pylopdf.PdfError, match=message):
            _ = doc.pages[0]
        with pytest.raises(pylopdf.PdfError, match=message):
            _ = doc.complexity


def test_index_follows_a_prune_that_changes_the_walk_outcome() -> None:
    """The page-tree edge bound depends on the object count, which pruning lowers."""
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R null null null] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: "<< /Unreferenced true >>",
            }
        )
    )
    # Four Kids edges fit the bound of four objects.
    assert len(doc.pages) == 1
    assert doc.pages[0].boxes.media.x1 == 100

    saved = doc.to_bytes()

    for current in (doc, pylopdf.open(saved)):
        with pytest.raises(pylopdf.PdfError, match="3-edge object-count safety limit"):
            _ = len(current.pages)


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
    doc = pylopdf.open(_flat_pdf(pages))

    start = time.perf_counter()
    views = list(doc.pages)
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
    doc = pylopdf.open(_flat_pdf(pages))

    start = time.perf_counter()
    for page in doc.pages:
        page.rotation = page.rotation + 90
        page.boxes.crop = (0, 0, 100, 150 + page.index % 2)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"editing {pages} pages took {elapsed:.2f}s"
    rotations = [90 * (page % 4 + 1) % 360 for page in range(pages)]
    assert [page.rotation for page in doc.pages] == rotations
    assert [page.boxes.crop.y1 for page in doc.pages] == [150 + page % 2 for page in range(pages)]
    fresh = pylopdf.open(doc.to_bytes())
    assert [page.rotation for page in fresh.pages] == rotations


def test_other_page_entries_drop_the_page_index() -> None:
    """Only rotation and page-box entries keep the index; ``/Type`` retypes the page."""
    core = _Document.load_bytes(_flat_pdf(3))
    assert core.page_count() == 3

    core.set_page_box(1, "Type", 0, 0, 1, 1)

    assert core.page_count() == 2
    assert _Document.load_bytes(core.save_bytes()).page_count() == 2


def _text_stream(text: str) -> str:
    content = f"BT /F1 12 Tf 10 60 Td ({text}) Tj ET"
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"


FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
INHERITED = "/MediaBox [0 0 100 100] /Resources << /Font << /F1 9 0 R >> >>"

# Each malformed file aliases page objects with other structures the page-tree
# walk reads; object 9 is the Helvetica font that every page inherits.
ALIASED: dict[str, dict[int, str | bytes]] = {
    # The catalog is also the first page, so page replacements must keep /Pages.
    "catalog-page": {
        1: "<< /Type /Page /Pages 2 0 R /Parent 2 0 R /Contents 3 0 R >>",
        2: f"<< /Type /Pages /Kids [1 0 R 4 0 R] /Count 2 {INHERITED} >>",
        3: _text_stream("Text one"),
        4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
        5: _text_stream("Text two"),
        6: "(filler)",
        7: "(filler)",
        8: "(filler)",
        9: FONT,
    },
    # The first page's /Annots and the second page's /Contents are the /Kids array.
    "kids-array-alias": {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids 3 0 R /Count 2 {INHERITED} >>",
        3: "[4 0 R 6 0 R]",
        4: "<< /Type /Page /Parent 2 0 R /Annots 3 0 R /Contents 5 0 R >>",
        5: _text_stream("Text one"),
        6: "<< /Type /Page /Parent 2 0 R /Contents 3 0 R >>",
        7: "(filler)",
        8: "(filler)",
        9: FONT,
    },
    # The second page object is a reference to the first page's dictionary, so
    # both pages share its indirect /Annots array.
    "page-reference": {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 {INHERITED} >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Annots 6 0 R >>",
        4: _text_stream("Text one"),
        5: "3 0 R",
        6: "[7 0 R]",
        7: "<< /Type /Annot /Subtype /Text /Rect [0 0 10 10] /Contents (Existing) >>",
        8: "(filler)",
        9: FONT,
    },
    # A /Kids entry names an absent object, which a new object could complete.
    "missing-kid": {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [3 0 R 10 0 R 5 0 R] /Count 2 {INHERITED} >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        4: _text_stream("Text one"),
        5: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R >>",
        6: _text_stream("Text two"),
        7: "(filler)",
        8: "(filler)",
        9: FONT,
    },
}


def _aliased_pdf(name: str) -> bytes:
    if name == "small-xref-size":
        # The trailer /Size names objects 0-2 only, but the table and file hold
        # 1-9. lopdf raises its next object number past the highest loaded
        # object, and edits check that the number is free anyway.
        objects = dict(ALIASED["missing-kid"])
        objects[2] = f"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 {INHERITED} >>"
        return build_raw_pdf(objects).replace(b"/Size 10", b"/Size 3")
    return build_raw_pdf(ALIASED[name])


def _text_outcome(page: pylopdf.Page) -> str:
    """Return a page's text or its error, so malformed files compare either way."""
    try:
        return page.text().strip()
    except pylopdf.PdfError as error:
        return f"{type(error).__name__}: {error}"


def _aliased_snapshot(doc: pylopdf.Document) -> tuple[object, ...]:
    """Like ``_snapshot``, tolerating renderer reads that a malformed tree breaks.

    hayro, which extracts text, resolves some of these trees to fewer pages
    than the page-tree walk; that disagreement predates the page index.
    """
    views = list(doc.pages)
    facts = [
        (
            page.boxes.media.x1,
            page.rotation,
            page.rect,
            [(annot.type, annot.rect, annot.xref) for annot in page.annots()],
            [(link.rect, link.target) for link in page.links],
        )
        for page in views
    ]
    toc = outline_tuples(doc)
    texts = [_text_outcome(page) for page in views]
    return len(views), facts, toc, texts


PAGE_EDITS: dict[str, Callable[[pylopdf.Page], object]] = {
    "insert_text": lambda page: page.insert_text((10, 20), "Stamp"),
    "insert_textbox": lambda page: page.insert_textbox((5, 5, 95, 60), "Box text"),
    "insert_image": lambda page: page.insert_image((5, 5, 30, 30), stream=PNG),
    "show_pdf_page": lambda page: page.show_pdf_page((0, 0, 40, 40), STAMP),
    "replace_text": lambda page: page.replace_text("Text", "Done"),
    "add_link_annot": lambda page: page.links.add((1, 1, 20, 20), "https://example.com/"),
    "add_highlight_annot": lambda page: page.add_highlight_annot((5, 5, 40, 15)),
}


@pytest.mark.parametrize("edit", PAGE_EDITS)
@pytest.mark.parametrize("name", [*ALIASED, "small-xref-size"])
def test_edits_on_every_page_of_an_aliased_tree_match_a_fresh_document(name: str, edit: str) -> None:
    doc = pylopdf.open(_aliased_pdf(name))
    pages = len(doc.pages)
    objects = doc.complexity["object_count"]

    for number in range(pages):
        _aliased_snapshot(doc)
        PAGE_EDITS[edit](doc.pages[number])
        # Serialize without the options that change the document.
        saved = doc.to_bytes(compress=False, prune=False)
        assert _aliased_snapshot(doc) == _aliased_snapshot(pylopdf.open(saved))

    # No edit replaced an existing object or changed the page tree.
    assert len(doc.pages) == pages
    assert doc.complexity["object_count"] > objects


@pytest.mark.parametrize("edit", PAGE_EDITS)
def test_edits_on_every_page_of_a_nested_tree_match_a_fresh_document(edit: str) -> None:
    doc = pylopdf.open(_nested_pdf())
    _snapshot(doc)

    for page in doc.pages:
        PAGE_EDITS[edit](page)

    cached = _snapshot(doc)
    assert cached == _snapshot(pylopdf.open(doc.to_bytes()))
    assert [width for width, *_ in cached[1]] == _widths(UNCHANGED)


def test_an_import_into_a_missing_kid_number_matches_a_fresh_walk() -> None:
    """A Form import can fill the object number that a dangling /Kids entry names.

    Renumbering moves source object 1, the Form's font, to the target's next
    number, 10. The index records the dangling entry and drops itself instead
    of assuming that the new object is not a page; the font is skipped, as a
    fresh walk finds.
    """
    target = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [7 0 R 10 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
                3: "(filler)",
                4: "(filler)",
                5: "(filler)",
                6: "(filler)",
                7: "<< /Type /Page /Parent 2 0 R /Contents 8 0 R /Resources << /Font << /F1 9 0 R >> >> >>",
                8: _text_stream("Target"),
                9: FONT,
            }
        )
    )
    source_objects: dict[int, str | bytes] = {
        1: FONT,
        2: "<< /Type /Catalog /Pages 3 0 R >>",
        3: "<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        4: "<< /Type /Page /Parent 3 0 R /MediaBox [0 0 100 100] /Resources << /Font << /F1 1 0 R >> >> "
        "/Contents 5 0 R >>",
        5: _text_stream("Source"),
    }
    source = pylopdf.open(build_raw_pdf(source_objects).replace(b"/Root 1 0 R", b"/Root 2 0 R"))
    assert len(target.pages) == 1

    target.pages[0].show_pdf_page((0, 0, 50, 50), source)

    fresh = pylopdf.open(target.to_bytes())
    assert _snapshot(target) == _snapshot(fresh)
    assert len(fresh.pages) == 1
    assert sorted(fresh.pages[0].text().split()) == ["Source", "Target"]


def _indirect_annots_pdf(pages: int) -> bytes:
    """Give every page its own indirect /Annots array holding one shared link."""
    kids = " ".join(f"{4 + 2 * page} 0 R" for page in range(pages))
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} /MediaBox [0 0 200 300] >>",
        3: "<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /A << /S /URI /URI (https://example.com/first) >> >>",
    }
    for page in range(pages):
        objects[4 + 2 * page] = f"<< /Type /Page /Parent 2 0 R /Annots {5 + 2 * page} 0 R >>"
        objects[5 + 2 * page] = "[3 0 R]"
    return build_raw_pdf(objects)


def test_annotating_pages_that_share_an_indirect_array_keeps_them_separate() -> None:
    """Appends clone a shared array on write until the last page owns it alone."""
    doc = pylopdf.open(_indirect_annots_pdf(1))
    for _ in range(3):
        doc.pages.extend(doc.pages[0])
    assert [len(page.annots()) for page in doc.pages] == [1, 1, 1, 1]

    for number, page in enumerate(doc.pages):
        page.links.add((20, 20, 30, 30), f"https://example.com/{number}")

    uris = [[link.target for link in page.links] for page in doc.pages]
    expected = [["https://example.com/first", f"https://example.com/{number}"] for number in range(4)]
    assert uris == expected
    assert _snapshot(doc) == _snapshot(pylopdf.open(doc.to_bytes()))


TIMED_PAGES = 5_000


def _flat_text_pdf(pages: int) -> bytes:
    kids = " ".join(f"{4 + 2 * page} 0 R" for page in range(pages))
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} /MediaBox [0 0 100 100] "
        "/Resources << /Font << /F1 3 0 R >> >> >>",
        3: FONT,
    }
    for page in range(pages):
        objects[4 + 2 * page] = f"<< /Type /Page /Parent 2 0 R /Contents {5 + 2 * page} 0 R >>"
        objects[5 + 2 * page] = _text_stream(f"Text {page}")
    return build_raw_pdf(objects)


@pytest.mark.parametrize("edit", PAGE_EDITS)
def test_editing_every_page_keeps_the_page_index(edit: str) -> None:
    """Drawing and annotation edits keep the index, so editing every page is linear.

    Each edit used to drop the index, so every ``doc[i]`` after it walked the
    page tree again: about 4 s locally for each loop here, and 22 s for
    ``show_pdf_page``, which also swept every object of the target. Each loop
    now takes 0.1 to 0.25 s; the generous bound only catches that regression.
    """
    doc = pylopdf.open(_flat_text_pdf(TIMED_PAGES))

    start = time.perf_counter()
    for page in doc.pages:
        PAGE_EDITS[edit](page)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"{edit} on {TIMED_PAGES} pages took {elapsed:.2f}s"
    assert len(doc.pages) == TIMED_PAGES
    fresh = pylopdf.open(doc.to_bytes())
    assert len(fresh.pages) == TIMED_PAGES
    for number in (0, TIMED_PAGES // 2, TIMED_PAGES - 1):
        assert fresh.pages[number].text() == doc.pages[number].text()
        assert [(a.type, a.rect) for a in fresh.pages[number].annots()] == [
            (a.type, a.rect) for a in doc.pages[number].annots()
        ]


def test_annotating_every_page_with_its_own_indirect_array_is_linear() -> None:
    """An append to an indirect /Annots array no longer scans every page for sharers.

    Together with the page-tree walk, that scan took about 6 s locally for this
    loop, against about 0.05 s now.
    """
    doc = pylopdf.open(_indirect_annots_pdf(TIMED_PAGES))

    start = time.perf_counter()
    for page in doc.pages:
        page.links.add((20, 20, 30, 30), "https://example.com/second")
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"annotating {TIMED_PAGES} pages took {elapsed:.2f}s"
    assert {len(page.annots()) for page in doc.pages} == {2}
