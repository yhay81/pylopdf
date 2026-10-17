"""Named destinations at the scale of large LaTeX/hyperref manuals.

hyperref writes a named destination for every page, section, equation, figure,
citation, and listing line, so a long manual's `/Names/Dests` tree easily holds
more than 4,096 entries. TOC, link, page-cleanup, and import paths share one
large-manual bound, so every well-formed tree that resolves can also be cleaned
and imported.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_NAMED_DEST_ENTRIES = 65_536
MAX_NAMED_DEST_NODES = 65_536
MAX_NAMED_DEST_EDGES = 131_072
MAX_NAMED_DEST_TEXT_BYTES = 16 * 1024 * 1024
MIB = 1024 * 1024
MANUAL_PAGES = 3
# pdfTeX keeps at most six names per leaf and six kids per intermediate node.
PDFTEX_FANOUT = 6

Shape = Literal["entries", "nodes", "edges", "text"]
SHAPE_CODES: dict[Shape, str] = {
    "entries": "named_destination_count",
    "nodes": "named_destination_count",
    "edges": "named_destination_count",
    "text": "named_destination_text_size",
}


def _name(index: int) -> str:
    """Return an 18-byte listings anchor; sorted names keep the tree ordered."""
    return f"lstnumber.-{index // 64:04d}.{index % 64 + 1:02d}"


def _deepest(count: int, page: int, pages: int = MANUAL_PAGES) -> int:
    """Return the last destination index that targets ``page``."""
    return count - 1 - (count - 1 - page) % pages


def _name_tree(count: int, first_id: int, pages: int = MANUAL_PAGES) -> tuple[dict[int, str | bytes], str]:
    """Build a pdfTeX-shaped name tree whose destination ``i`` targets page ``i % pages``."""
    objects: dict[int, str | bytes] = {}
    level: list[tuple[int, str, str]] = []
    for start in range(0, count, PDFTEX_FANOUT):
        indices = range(start, min(start + PDFTEX_FANOUT, count))
        pairs = " ".join(f"({_name(i)}) [{3 + i % pages} 0 R /XYZ 0 200 null]" for i in indices)
        limits = (_name(indices[0]), _name(indices[-1]))
        objects[first_id + len(objects)] = f"<< /Names [{pairs}] /Limits [({limits[0]}) ({limits[1]})] >>"
        level.append((first_id + len(objects) - 1, *limits))
    while len(level) > PDFTEX_FANOUT:
        parents: list[tuple[int, str, str]] = []
        for start in range(0, len(level), PDFTEX_FANOUT):
            group = level[start : start + PDFTEX_FANOUT]
            kids = " ".join(f"{node} 0 R" for node, _, _ in group)
            limits = (group[0][1], group[-1][2])
            objects[first_id + len(objects)] = f"<< /Kids [{kids}] /Limits [({limits[0]}) ({limits[1]})] >>"
            parents.append((first_id + len(objects) - 1, *limits))
        level = parents
    return objects, " ".join(f"{node} 0 R" for node, _, _ in level)


def _manual(count: int, pages: int = MANUAL_PAGES) -> bytes:
    """Build a manual whose links and outline name the deepest destinations.

    Every page links to the deepest destination of each of the first three
    pages, and every outline item names its own page's deepest destination.
    At the entry limit, the pdfTeX-shaped tree also exceeds the former
    4,096-node, 8,192-edge, 1 MiB key-text, and 65,536-object copy budgets.
    """
    deepest = [_name(_deepest(count, page, pages)) for page in range(pages)]
    outline = 3 + pages
    kids = " ".join(f"{3 + page} 0 R" for page in range(pages))
    objects: dict[int, str | bytes] = {
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} /MediaBox [0 0 200 200] >>",
        outline: f"<< /Type /Outlines /First {outline + 1} 0 R /Last {outline + pages} 0 R /Count {pages} >>",
    }
    for page in range(pages):
        links = " ".join(
            f"<< /Type /Annot /Subtype /Link /Rect [{20 * other + 1} 1 {20 * other + 10} 10] "
            f"/A << /S /GoTo /D ({deepest[other]}) >> >>"
            for other in range(min(pages, MANUAL_PAGES))
        )
        objects[3 + page] = f"<< /Type /Page /Parent 2 0 R /Annots [{links}] >>"
        previous = f" /Prev {outline + page} 0 R" if page > 0 else ""
        following = f" /Next {outline + page + 2} 0 R" if page + 1 < pages else ""
        objects[outline + 1 + page] = (
            f"<< /Title (Page {page}) /Parent {outline} 0 R /A << /S /GoTo /D ({deepest[page]}) >>"
            f"{previous}{following} >>"
        )
    tree, root_kids = _name_tree(count, outline + pages + 1, pages)
    objects.update(tree)
    objects[1] = (
        f"<< /Type /Catalog /Pages 2 0 R /Names << /Dests << /Kids [{root_kids}] >> >> /Outlines {outline} 0 R >>"
    )
    return build_raw_pdf(objects)


@pytest.fixture(scope="module")
def manual() -> bytes:
    return _manual(MAX_NAMED_DEST_ENTRIES)


def _link_targets(doc: pylopdf.Document) -> list[list[tuple[int, str | None]]]:
    return [[(link["page"], link.get("nameddest")) for link in page.get_links()] for page in doc]


def _links(doc: pylopdf.Document) -> list[list[pylopdf.LinkInfo]]:
    return [page.get_links() for page in doc]


def test_manual_resolves_toc_and_links_at_the_entry_limit(manual: bytes) -> None:
    doc = pylopdf.Document(stream=manual)
    deepest = [_name(_deepest(MAX_NAMED_DEST_ENTRIES, page)) for page in range(MANUAL_PAGES)]

    assert doc.get_toc() == [[1, f"Page {page}", page + 1] for page in range(MANUAL_PAGES)]
    assert _link_targets(doc) == [[(page, deepest[page]) for page in range(MANUAL_PAGES)]] * MANUAL_PAGES


@pytest.mark.parametrize("operation", ["delete", "select"])
def test_manual_cleanup_rewrites_the_complete_tree(manual: bytes, operation: Literal["delete", "select"]) -> None:
    doc = pylopdf.Document(stream=manual)
    retained = [_name(_deepest(MAX_NAMED_DEST_ENTRIES, page)) for page in (0, 2)]
    removed = _name(_deepest(MAX_NAMED_DEST_ENTRIES, 1))
    # Warm the cached link indexes; the edit must drop them.
    assert [len(links) for links in _link_targets(doc)] == [MANUAL_PAGES] * MANUAL_PAGES

    if operation == "delete":
        doc.delete_pages([1])
    else:
        doc.select([0, 2])

    saved = doc.tobytes()
    assert removed.encode() not in saved
    assert _name(1).encode() not in saved
    assert all(_name(index).encode() in saved for index in (0, 2, MAX_NAMED_DEST_ENTRIES - 1))
    for current in (doc, pylopdf.Document(stream=saved)):
        assert current.get_toc() == [[1, "Page 0", 1], [1, "Page 2", 2]]
        assert _link_targets(current) == [[(0, retained[0]), (1, retained[1])]] * 2


def test_manual_links_import_through_insert_pdf(manual: bytes) -> None:
    source = pylopdf.Document(stream=manual)
    target = pylopdf.Document()
    target.new_page(width=200, height=200)

    target.insert_pdf(source)

    # Imported links become explicit destinations on the shifted pages.
    expected: list[list[tuple[int, str | None]]] = [[]]
    expected += [[(page + 1, None) for page in range(MANUAL_PAGES)]] * MANUAL_PAGES
    for merged in (target, pylopdf.Document(stream=target.tobytes())):
        assert _link_targets(merged) == expected


def test_reading_every_page_of_a_large_manual_indexes_it_once() -> None:
    """Link reads reuse one name-tree index and one page index until an edit.

    Rebuilding both for every call made this loop take about 13 s locally,
    against about 0.03 s now; the generous bound only catches that regression.
    """
    pages = 1_000
    doc = pylopdf.Document(stream=_manual(MAX_NAMED_DEST_ENTRIES, pages))
    views = list(doc)
    deepest = [_name(_deepest(MAX_NAMED_DEST_ENTRIES, page, pages)) for page in range(MANUAL_PAGES)]

    start = time.perf_counter()
    targets = [[(link["page"], link.get("nameddest")) for link in page.get_links()] for page in views]
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"reading the links of {pages} pages took {elapsed:.2f}s"
    assert targets == [[(page, deepest[page]) for page in range(MANUAL_PAGES)]] * pages


EDITS: dict[str, Callable[[pylopdf.Document], object]] = {
    "delete_pages": lambda doc: doc.delete_pages([0]),
    "select": lambda doc: doc.select([2, 0, 1]),
    "insert_pdf": lambda doc: doc.insert_pdf(pylopdf.Document(stream=_manual(256)), start_at=0),
    "new_page": lambda doc: doc.new_page(0, width=200, height=200),
    "copy_page": lambda doc: doc.copy_page(2, 0),
    "set_toc": lambda doc: doc.set_toc([[1, "Last page", 3]]),
    "add_link_annot": lambda doc: doc[1].add_link_annot((1, 20, 10, 30), "https://example.com/"),
    "embfile_add": lambda doc: doc.embfile_add("notes.txt", b"notes"),
    "set_rotation": lambda doc: doc[2].set_rotation(90),
}


@pytest.mark.parametrize("edit", EDITS)
def test_cached_link_reads_follow_every_edit(edit: str) -> None:
    """Warm link reads after an edit match a document that never cached them."""
    doc = pylopdf.Document(stream=_manual(256))
    assert all(link["page"] >= 0 for links in _links(doc) for link in links)

    EDITS[edit](doc)

    assert _links(doc) == _links(pylopdf.Document(stream=doc.tobytes()))


def test_cached_link_reads_keep_stale_page_views_stale() -> None:
    doc = pylopdf.Document(stream=_manual(256))
    page = doc[2]
    before = [link["page"] for link in page.get_links()]

    doc.delete_pages([0])

    with pytest.raises(pylopdf.StalePageError):
        page.get_links()
    # Links to the deleted page are removed, and the rest shift down by one.
    assert [link["page"] for link in doc[1].get_links()] == [target - 1 for target in before if target > 0]


@pytest.mark.parametrize("shape", SHAPE_CODES)
def test_refused_name_tree_index_is_never_cached(shape: Shape) -> None:
    """Every named lookup re-raises instead of trusting a partial index."""
    doc = _limited_document(shape, over=True)
    named = doc[0]

    # A page without named links never walks the tree.
    assert doc[1].get_links() == []
    for _ in range(2):
        with pytest.raises(pylopdf.LimitError) as caught:
            named.get_links()
        assert caught.value.code == SHAPE_CODES[shape]


def test_name_tree_index_keeps_first_occurrences_of_unsorted_keys() -> None:
    """Unsorted and repeated keys resolve like a first-occurrence lookup.

    A key whose tree value is not a destination still shadows the legacy
    catalog ``/Dests`` entry of the same name.
    """
    names = ["zeta", "alpha", "", "twice", "legacy", "shadowed", "missing"]
    links = " ".join(
        f"<< /Type /Annot /Subtype /Link /Rect [{index} 1 {index + 1} 2] /Dest ({name}) >>"
        for index, name in enumerate(names)
    )
    doc = pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: (
                    "<< /Type /Catalog /Pages 2 0 R /Names << /Dests << /Kids [5 0 R 6 0 R] >> >> "
                    "/Dests << /legacy [4 0 R /Fit] /shadowed [4 0 R /Fit] >> >>"
                ),
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: f"<< /Type /Page /Parent 2 0 R /Annots [{links}] >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
                5: "<< /Names [(zeta) [4 0 R /Fit] (alpha) [3 0 R /Fit] () [4 0 R /Fit] (twice) [4 0 R /Fit]] >>",
                6: "<< /Names [(twice) [3 0 R /Fit] () [3 0 R /Fit] (shadowed) << /S /GoTo >>] >>",
            }
        )
    )

    for _ in range(2):
        assert [(link["page"], link["nameddest"]) for link in doc[0].get_links()] == [
            (1, "zeta"),
            (0, "alpha"),
            (1, ""),
            (1, "twice"),
            (1, "legacy"),
            (-1, "shadowed"),
            (-1, "missing"),
        ]


def _limited_tree(shape: Shape, *, over: bool) -> tuple[str, dict[int, str]]:
    """Return a tree exactly at, or one unit beyond, one limit with ``section`` last.

    Each tree approaches only the limit named by ``shape``; the others stay far
    below theirs, so every case isolates one boundary.
    """
    extra = int(over)
    section = "(section) [4 0 R /Fit]"
    if shape == "entries":
        return f"<< /Names [{'(k) 0 ' * (MAX_NAMED_DEST_ENTRIES - 1 + extra)}{section}] >>", {}
    if shape == "nodes":
        empty_kids = "<< >> " * (MAX_NAMED_DEST_NODES - 2 + extra)
        return f"<< /Kids [{empty_kids}<< /Names [{section}] >>] >>", {}
    if shape == "edges":
        return f"<< /Kids [{'8 0 R ' * (MAX_NAMED_DEST_EDGES + extra)}] >>", {8: f"<< /Names [{section}] >>"}
    # Fifteen reused 1 MiB keys, one shorter key, and `section` fill the budget.
    keys = "8 0 R 0 " * 15 + "9 0 R 0 "
    filler = {8: f"({'x' * MIB})", 9: f"({'y' * (MIB - len('section') + extra)})"}
    return f"<< /Names [{keys}{section}] >>", filler


def _limited_document(shape: Shape, *, over: bool, navigation: bool = True) -> pylopdf.Document:
    """Open two pages whose name tree is at object 5.

    With ``navigation``, a first-page link and the outline name ``section``, so
    reads and imports index the tree. Without it, page cleanup rewrites the tree
    without any named lookup.
    """
    tree, extra = _limited_tree(shape, over=over)
    link = "/Annots [<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /Dest (section) >>] " if navigation else ""
    outline = "/Outlines 6 0 R " if navigation else ""
    objects: dict[int, str | bytes] = {
        1: f"<< /Type /Catalog /Pages 2 0 R /Names << /Dests 5 0 R >> {outline}>>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
        3: f"<< /Type /Page /Parent 2 0 R {link}>>",
        4: "<< /Type /Page /Parent 2 0 R >>",
        5: tree,
        6: "<< /Type /Outlines /First 7 0 R /Last 7 0 R /Count 1 >>",
        7: "<< /Title (Section) /Parent 6 0 R /Dest (section) >>",
        **extra,
    }
    return pylopdf.Document(stream=build_raw_pdf(objects))


@pytest.mark.parametrize("shape", SHAPE_CODES)
def test_reads_and_imports_accept_each_limit(shape: Shape) -> None:
    source = _limited_document(shape, over=False)
    target = pylopdf.Document()

    target.insert_pdf(source)

    assert [link["page"] for link in source[0].get_links()] == [1]
    assert source.get_toc() == [[1, "Section", 2]]
    assert [link["page"] for link in target[0].get_links()] == [1]


@pytest.mark.parametrize("shape", SHAPE_CODES)
def test_reads_and_imports_refuse_beyond_each_limit(shape: Shape) -> None:
    source = _limited_document(shape, over=True)
    target = pylopdf.Document(stream=build_pdf(["Unchanged"]))
    source_before = source.tobytes()
    target_before = target.tobytes()
    operations: list[Callable[[], object]] = [
        source[0].get_links,
        source.get_toc,
        lambda: target.insert_pdf(source),
    ]

    for operation in operations:
        with pytest.raises(pylopdf.LimitError) as caught:
            operation()
        assert caught.value.code == SHAPE_CODES[shape]

    assert source.tobytes() == source_before
    assert target.tobytes() == target_before
    assert "Unchanged" in target[0].get_text()


def _edit(doc: pylopdf.Document, operation: Literal["delete", "select"]) -> None:
    if operation == "delete":
        doc.delete_pages([1])
    else:
        doc.select([0])


@pytest.mark.parametrize("shape", SHAPE_CODES)
@pytest.mark.parametrize("operation", ["delete", "select"])
def test_cleanup_shares_each_limit(shape: Shape, operation: Literal["delete", "select"]) -> None:
    accepted = _limited_document(shape, over=False, navigation=False)
    _edit(accepted, operation)
    assert accepted.page_count == 1

    refused = _limited_document(shape, over=True, navigation=False)
    page = refused[0]
    before = refused.tobytes()
    with pytest.raises(pylopdf.LimitError) as caught:
        _edit(refused, operation)
    assert caught.value.code == SHAPE_CODES[shape]
    assert refused.tobytes() == before
    assert refused.page_count == 2
    assert page.number == 0


def _outline_document(items: int) -> pylopdf.Document:
    """Open outline items that share one 1 MiB named destination on the first page."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Names << /Dests << /Names [6 0 R [3 0 R /Fit]] >> >> /Outlines 5 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
        3: "<< /Type /Page /Parent 2 0 R >>",
        4: "<< /Type /Page /Parent 2 0 R >>",
        5: f"<< /Type /Outlines /First 7 0 R /Last {6 + items} 0 R /Count {items} >>",
        6: f"({'n' * MIB})",
    }
    for item in range(items):
        following = f" /Next {8 + item} 0 R" if item + 1 < items else ""
        objects[7 + item] = f"<< /Title (Item {item}) /Parent 5 0 R /Dest 6 0 R{following} >>"
    return pylopdf.Document(stream=build_raw_pdf(objects))


def test_outline_cleanup_charges_destination_names_to_the_toc_budget() -> None:
    """Outline names are not limited by one page's 1 MiB link budget."""
    doc = _outline_document(2)
    expected = [[1, "Item 0", 1], [1, "Item 1", 1]]
    assert doc.get_toc() == expected

    doc.delete_pages([1])

    assert doc.get_toc() == expected


@pytest.mark.parametrize("operation", ["get_toc", "delete", "select"])
def test_outline_names_beyond_the_toc_budget_are_refused(operation: str) -> None:
    doc = _outline_document(17)
    before = doc.tobytes()
    call: Callable[[], object] = {
        "get_toc": doc.get_toc,
        "delete": lambda: doc.delete_pages([1]),
        "select": lambda: doc.select([0]),
    }[operation]

    with pytest.raises(pylopdf.LimitError, match="16777216-byte safety limit") as caught:
        call()

    assert caught.value.code == "toc_text_size"
    assert doc.tobytes() == before


def _legacy_document(count: int) -> pylopdf.Document:
    """Open a legacy catalog `/Dests` dictionary that alternates between both pages.

    The first page links to the last name that targets it.
    """
    entries = " ".join(f"/{_name(index)} [{3 + index % 2} 0 R /Fit]" for index in range(count))
    return pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: f"<< /Type /Catalog /Pages 2 0 R /Dests << {entries} >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [<< /Type /Annot /Subtype /Link "
                f"/Rect [1 1 10 10] /Dest /{_name(count - 2)} >>] >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
            }
        )
    )


def test_legacy_destinations_share_the_named_destination_limits() -> None:
    doc = _legacy_document(4_100)
    doc.delete_pages([1])
    saved = doc.tobytes()
    assert _name(4_096).encode() in saved
    assert _name(4_097).encode() not in saved
    assert [link["page"] for link in pylopdf.Document(stream=saved)[0].get_links()] == [0]

    refused = _legacy_document(MAX_NAMED_DEST_ENTRIES + 1)
    before = refused.tobytes()
    with pytest.raises(pylopdf.LimitError, match="65536-entry safety limit") as caught:
        refused.delete_pages([1])
    assert caught.value.code == "named_destination_count"
    assert refused.tobytes() == before


@pytest.mark.parametrize(
    ("budget", "error", "code"),
    [
        ("objects", "1048576-object safety limit", "named_destination_count"),
        ("text", "16777216-byte safety limit", "named_destination_text_size"),
    ],
)
def test_cleanup_bounds_copied_destination_values(budget: str, error: str, code: str) -> None:
    """Retained values share 1,048,576 copied objects (16 per entry) and 16 MiB of text."""
    if budget == "objects":
        value = f"[{'0 ' * (16 * MAX_NAMED_DEST_ENTRIES)}]"
    else:
        value = f"[({'v' * (MAX_NAMED_DEST_TEXT_BYTES + 1)})]"
    doc = pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: f"<< /Type /Catalog /Pages 2 0 R /Names << /Dests << /Names [(kept) {value}] >> >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
            }
        )
    )
    before = doc.tobytes()

    with pytest.raises(pylopdf.LimitError, match=error) as caught:
        doc.delete_pages([1])

    assert caught.value.code == code
    assert doc.tobytes() == before


def test_cleanup_rejects_deeply_nested_destination_values() -> None:
    nested = "[" * 33 + "]" * 33
    doc = pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: f"<< /Type /Catalog /Pages 2 0 R /Names << /Dests << /Names [(kept) {nested}] >> >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
            }
        )
    )
    before = doc.tobytes()

    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        doc.delete_pages([1])

    assert doc.tobytes() == before
