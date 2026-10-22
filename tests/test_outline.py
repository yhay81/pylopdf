"""Tests for ``Document.outline``: item handles, entry records, and targets."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf
from pylopdf import Color, Destination, Launch, NamedAction, OutlineEntry, RemoteDestination
from pylopdf.pylopdf_core import LimitError, _Document

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
MAX_TOC_ENTRIES = 65_536
MAX_TOC_TEXT_BYTES = 16 * 1024 * 1024


def _build_outline_fixture(outline_objects: dict[int, str | bytes], *, pages: str = "[3 0 R]") -> bytes:
    """Build one page with a caller-provided outline graph rooted at object 4."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R >>",
        2: f"<< /Type /Pages /Kids {pages} /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    }
    objects.update(outline_objects)
    return build_raw_pdf(objects)


def _simple(entries: list[OutlineEntry]) -> list[tuple[int, str, int | None]]:
    return [(entry.level, entry.title, entry.page) for entry in entries]


BOOK = [
    OutlineEntry(1, "第 1 章", Destination(0)),
    OutlineEntry(2, "1.1 節", Destination(1)),
    OutlineEntry(1, "第 2 章", Destination(2)),
]


# --- writing and reading whole outlines --------------------------------------


def test_outline_round_trips_through_saving(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    assert list(doc.outline) == []
    assert doc.outline.entries() == []
    doc.outline = BOOK
    assert doc.outline.entries() == BOOK
    assert _simple(doc.outline.entries()) == [(1, "第 1 章", 0), (2, "1.1 節", 1), (1, "第 2 章", 2)]
    # CJK titles survive as UTF-16BE text strings.
    assert pylopdf.open(doc.to_bytes()).outline.entries() == BOOK


def test_outline_deep_nesting(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    entries = [
        OutlineEntry(1, "A", Destination(0)),
        OutlineEntry(2, "B", Destination(0)),
        OutlineEntry(3, "C", Destination(1)),
        OutlineEntry(1, "D", Destination(2)),
    ]
    doc.outline = entries
    reloaded = pylopdf.open(doc.to_bytes())
    assert reloaded.outline.entries() == entries
    assert [item.level for item in reloaded.outline.walk()] == [1, 2, 3, 1]
    assert [item.title for item in reloaded.outline[0].children[0].children] == ["C"]


def test_assigning_replaces_and_deleting_removes(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.outline = [OutlineEntry(1, "old", Destination(0)), OutlineEntry(1, "old2", Destination(1))]
    doc.outline = (entry for entry in [OutlineEntry(1, "new", Destination(2))])
    assert _simple(doc.outline.entries()) == [(1, "new", 2)]
    assert b"old2" not in doc.to_bytes()
    del doc.outline
    assert doc.outline.entries() == []
    assert "Outlines" not in doc.catalog
    doc.outline = BOOK
    doc.outline = []
    assert pylopdf.open(doc.to_bytes()).outline.entries() == []
    del doc.outline  # Removing an absent outline changes nothing.


def test_removed_outline_names_raise_migration_hints() -> None:
    doc = pylopdf.Document()
    for name in ("get_toc", "set_toc"):
        with pytest.raises(AttributeError, match=r"doc\.outline"):
            getattr(doc, name)


@pytest.mark.parametrize(
    ("entries", "error", "match"),
    [
        ([OutlineEntry(2, "A")], ValueError, "level"),  # The first level must be 1.
        ([OutlineEntry(1, "A"), OutlineEntry(3, "B")], ValueError, "level"),
        ([OutlineEntry(1, "A", Destination(3))], ValueError, "out of range"),
        ([(1, "A", 0)], TypeError, "OutlineEntry"),
        ("A", TypeError, "iterable of OutlineEntry"),
    ],
)
def test_assigning_refuses_invalid_outlines(
    three_page_pdf: bytes, entries: object, error: type[Exception], match: str
) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.outline = BOOK
    with pytest.raises(error, match=match):
        doc.outline = entries  # ty: ignore[invalid-assignment]
    assert doc.outline.entries() == BOOK


@pytest.mark.parametrize(
    ("make", "error"),
    [
        (lambda: OutlineEntry(0, "A"), ValueError),
        (lambda: OutlineEntry(65, "A"), ValueError),
        (lambda: OutlineEntry(True, "A"), TypeError),
        (lambda: OutlineEntry(1, b"A"), TypeError),  # ty: ignore[invalid-argument-type]
        (lambda: OutlineEntry(1, "A", 3), TypeError),  # ty: ignore[invalid-argument-type]
        (lambda: OutlineEntry(1, "A", color=(1, 0, 0)), TypeError),  # ty: ignore[invalid-argument-type]
        (lambda: OutlineEntry(1, "A", color=Color(1, 0, 0, 0.5)), ValueError),
        (lambda: OutlineEntry(1, "A", color=Color(2, 0, 0)), ValueError),
        (lambda: OutlineEntry(1, "A", bold=1), TypeError),  # ty: ignore[invalid-argument-type]
        (lambda: Destination(-1), ValueError),
        (lambda: Destination(0, zoom=-1), ValueError),
        (lambda: Destination(0, zoom=float("nan")), ValueError),
        (lambda: Destination(0, (1, float("inf"))), ValueError),
        (lambda: RemoteDestination(""), ValueError),
        (lambda: Launch(b"a.pdf"), TypeError),  # ty: ignore[invalid-argument-type]
        (lambda: NamedAction(""), ValueError),
    ],
)
def test_records_validate_on_construction(make: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        make()  # ty: ignore[call-non-callable]


def test_outline_survives_page_ops(three_page_pdf: bytes) -> None:
    """Keep outline entries readable after select while target pages remain."""
    doc = pylopdf.open(three_page_pdf)
    doc.outline = [OutlineEntry(1, "first", Destination(0)), OutlineEntry(1, "third", Destination(2))]
    doc.pages.select([0, 2])
    assert _simple(doc.outline.entries()) == [(1, "first", 0), (1, "third", 1)]  # Page numbers are compacted.


@pytest.mark.parametrize("name", ["usrguide.pdf", "bill-hr815.pdf", "f1040.pdf"])
def test_real_world_outline_readable(name: str) -> None:
    doc = pylopdf.open(REAL_WORLD / name)
    entries = doc.outline.entries()
    items = list(doc.outline.walk())
    assert len(items) == len(entries)
    for entry, item in zip(entries, items, strict=True):
        assert entry.level >= 1
        assert isinstance(entry.title, str)
        assert entry.page is None or 0 <= entry.page < len(doc.pages)
        assert item.to_entry() == entry
    # A document's own outline writes back unchanged.
    doc.outline = entries
    assert doc.outline.entries() == entries


# --- targets ---------------------------------------------------------------------


def test_every_target_kind_round_trips(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    targets = [
        None,
        Destination(1),
        Destination(2, (10, 20)),
        Destination(0, zoom=1.5),
        Destination(1, (30, 40), 2.0),
        "https://example.com/a?b=c",
        RemoteDestination("other.pdf", 4),
        RemoteDestination("other.pdf", name="chapter-2"),
        Launch("notes.txt"),
        NamedAction("NextPage"),
    ]
    doc.outline = [OutlineEntry(1, str(index), target) for index, target in enumerate(targets)]
    for document in (doc, pylopdf.open(doc.to_bytes())):
        assert [entry.destination for entry in document.outline.entries()] == targets
        assert [item.destination for item in document.outline] == targets


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_destination_points_use_display_coordinates(rotation: int) -> None:
    doc = pylopdf.open(build_pdf(["a"]))
    doc.pages[0].rotation = rotation
    doc.outline = [OutlineEntry(1, "spot", Destination(0, (12.5, 30)))]
    (entry,) = doc.outline.entries()
    assert entry.destination == Destination(0, (12.5, 30))


def test_destinations_read_from_explicit_arrays_and_names() -> None:
    doc = pylopdf.open(
        _build_outline_fixture(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R /Names << /Dests 8 0 R >> >>",
                4: "<< /First 5 0 R /Last 7 0 R >>",
                5: "<< /Title (xyz) /Dest [3 0 R /XYZ 10 90 3] /Next 6 0 R >>",
                6: "<< /Title (named) /Dest (intro) /Next 7 0 R >>",
                7: "<< /Title (fith) /A << /S /GoTo /D [3 0 R /FitH 50] >> >>",
                8: "<< /Names [(intro) [3 0 R /Fit]] >>",
            }
        )
    )
    assert not doc.is_repaired
    assert [entry.destination for entry in doc.outline.entries()] == [
        Destination(0, (10, 10), 3.0),
        Destination(0),
        Destination(0, (0, 50)),
    ]


# --- item handles ----------------------------------------------------------------


def test_items_are_handles_on_the_tree(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.outline = BOOK
    outline = doc.outline
    assert len(outline) == 2
    first, second = outline
    assert outline[0] == first
    assert outline[-1] == second
    assert outline[:1] == [first]
    assert first != second
    assert len({first, outline[0], second}) == 2
    assert (first.level, first.title, first.page) == (1, "第 1 章", 0)
    (child,) = first.children
    assert (child.level, child.title, child.destination) == (2, "1.1 節", Destination(1))
    assert child.children == ()
    assert second.children == ()
    assert list(outline.walk()) == [first, child, second]
    assert repr(first) == f"OutlineItem(xref={first.xref}, title='第 1 章')"
    assert first.obj["Title"].text == "第 1 章"
    assert repr(outline).startswith("Outline([OutlineItem(")


def test_item_objects_keep_the_handle_generation() -> None:
    data = _build_outline_fixture(
        {
            4: "<< /First 5 0 R /Last 5 1 R >>",
            5: "<< /Title (Original) /Parent 4 0 R /Next 5 1 R >>",
            6: "<< /Title (Higher generation) /Parent 4 0 R >>",
        }
    )
    # A mismatched xref entry can load two generations of one object number.
    data = data.replace(b"6 0 obj", b"5 1 obj", 1)
    doc = pylopdf.open(data)
    first, second = doc.outline
    assert first.xref == second.xref
    assert first.obj.generation == 0
    assert second.obj.generation == 1
    assert first.obj["Title"].text == first.title == "Original"
    first.obj["Title"] = "Renamed"
    assert first.title == "Renamed"
    assert second.title == "Higher generation"


def test_editing_an_item_in_place(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.outline = BOOK
    page = doc.pages[0]
    item = doc.outline[0].children[0]
    item.title = "Renamed ✓"
    item.color = Color(0, 0.5, 1)
    item.bold = True
    item.italic = True
    item.italic = False
    item.destination = "https://example.com"
    assert item.to_entry() == OutlineEntry(2, "Renamed ✓", "https://example.com", color=Color(0, 0.5, 1), bold=True)
    item.color = None
    item.destination = Destination(2, (5, 5))
    item.destination = None
    assert item.to_entry() == OutlineEntry(2, "Renamed ✓", bold=True)
    reloaded = pylopdf.open(doc.to_bytes())
    assert reloaded.outline.entries()[1] == OutlineEntry(2, "Renamed ✓", bold=True)
    # In-place edits keep page views valid.
    assert page.text().strip() == "Page one"


def test_item_edits_validate_before_any_change(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.outline = BOOK
    item = doc.outline[0]
    with pytest.raises(TypeError):
        item.title = 5  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match="out of range"):
        item.destination = Destination(3)
    with pytest.raises(TypeError):
        item.destination = 2  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match="no transparency"):
        item.color = Color(1, 0, 0, 0)
    with pytest.raises(TypeError):
        item.bold = None  # ty: ignore[invalid-assignment]
    with pytest.raises(LimitError) as caught:
        item.title = "é" * (MAX_TOC_TEXT_BYTES // 2)
    assert caught.value.code == "toc_input_size"
    with pytest.raises(pylopdf.PdfError, match="no open state"):
        item.children[0].is_open = True
    assert doc.outline.entries() == BOOK


@pytest.mark.parametrize("property_name", ["title", "destination"])
def test_item_text_edits_preserve_the_aggregate_read_limit(three_page_pdf: bytes, property_name: str) -> None:
    doc = pylopdf.open(three_page_pdf)
    original = [OutlineEntry(1, "A" * (MAX_TOC_TEXT_BYTES // 2)), OutlineEntry(1, "B")]
    doc.outline = original
    item = doc.outline[1]
    with pytest.raises(LimitError) as caught:
        setattr(item, property_name, "C" * (MAX_TOC_TEXT_BYTES // 2 + 1))
    assert caught.value.code == "toc_input_size"
    assert doc.outline.entries() == original


@pytest.mark.parametrize("color", ["[2 0 0]", "[-1 0 0]"])
def test_invalid_source_colors_read_as_the_viewer_default(color: str) -> None:
    doc = pylopdf.open(
        _build_outline_fixture(
            {
                4: "<< /First 5 0 R >>",
                5: f"<< /Title (A) /Dest [3 0 R /Fit] /C {color} >>",
            }
        )
    )
    expected = OutlineEntry(1, "A", Destination(0))
    assert doc.outline[0].color is None
    assert doc.outline[0].to_entry() == expected
    assert doc.outline.entries() == [expected]
    doc.outline = doc.outline.entries()
    assert pylopdf.open(doc.to_bytes()).outline.entries() == [expected]


def test_open_states_keep_counts_consistent(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.outline = [
        OutlineEntry(1, "A", Destination(0), is_open=True),
        OutlineEntry(2, "A1", Destination(0), is_open=False),
        OutlineEntry(3, "A1a", Destination(0)),
        OutlineEntry(3, "A1b", Destination(0)),
        OutlineEntry(2, "A2", Destination(1)),
        OutlineEntry(1, "B", Destination(2), is_open=False),
        OutlineEntry(2, "B1", Destination(2)),
    ]

    def counts() -> list[object]:
        return [doc.catalog["Outlines"]["Count"], *(item.obj.get("Count") for item in doc.outline.walk())]

    # ISO 32000-2 Table 151: an open item counts its visible descendants, a
    # closed one the negative of those that opening it would show.
    assert counts() == [4, 2, -2, None, None, None, -1, None]
    a1 = doc.outline[0].children[0]
    a1.is_open = True
    assert a1.is_open
    assert counts() == [6, 4, 2, None, None, None, -1, None]
    doc.outline[0].is_open = False
    assert counts() == [2, -4, 2, None, None, None, -1, None]
    assert [entry.is_open for entry in doc.outline.entries()] == [False, True, False, False, False, False, False]
    assert pylopdf.open(doc.to_bytes()).outline.entries() == doc.outline.entries()


def test_handles_go_stale_when_the_outline_is_replaced(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.outline = BOOK
    item = doc.outline[0]
    doc.outline = BOOK
    with pytest.raises(pylopdf.StaleObjectError):
        _ = item.title
    with pytest.raises(pylopdf.StaleObjectError):
        item.title = "x"
    assert repr(item).startswith("<stale OutlineItem")
    renumbered = doc.outline[0]
    doc.to_bytes(renumber=True)
    with pytest.raises(pylopdf.StaleObjectError, match="renumbered"):
        _ = renumbered.title
    doc.close()
    with pytest.raises(pylopdf.DocumentClosedError):
        _ = doc.outline


def test_direct_items_appear_in_entries_but_not_as_handles() -> None:
    doc = pylopdf.open(
        _build_outline_fixture(
            {
                4: "<< /First 5 0 R >>",
                5: "<< /Title (indirect) /Dest [3 0 R /Fit] /Next << /Title (direct) /Dest [3 0 R /Fit] >> >>",
            }
        )
    )
    assert _simple(doc.outline.entries()) == [(1, "indirect", 0), (1, "direct", 0)]
    assert [item.title for item in doc.outline.walk()] == ["indirect"]
    assert [item.title for item in doc.outline] == ["indirect"]


# --- read limits -----------------------------------------------------------------


def test_next_cycle_is_visited_once() -> None:
    doc = pylopdf.open(
        _build_outline_fixture(
            {
                4: "<< /First 5 0 R >>",
                5: "<< /Title (A) /Dest [3 0 R /Fit] /Next 5 0 R >>",
            }
        )
    )
    assert _simple(doc.outline.entries()) == [(1, "A", 0)]
    assert [item.title for item in doc.outline] == ["A"]


def test_unresolved_destinations_read_as_none() -> None:
    objects: dict[int, str | bytes] = {
        4: "<< /First 5 0 R >>",
        5: "<< /Title (cycle) /Dest 6 0 R /Next 7 0 R >>",
        6: "<< /D 6 0 R >>",
        7: "<< /Title (deep) /Dest 8 0 R /Next 41 0 R >>",
        41: "<< /Title (missing page) /Dest [99 0 R /Fit] >>",
    }
    for object_id in range(8, 40):
        objects[object_id] = f"<< /D {object_id + 1} 0 R >>"
    objects[40] = "[3 0 R /Fit]"
    doc = pylopdf.open(_build_outline_fixture(objects))
    assert _simple(doc.outline.entries()) == [(1, "cycle", None), (1, "deep", None), (1, "missing page", None)]


def test_reads_refuse_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    for object_id in range(5, 69):
        objects[object_id] = f"<< /First {object_id + 1} 0 R >>"
    objects[69] = "<< >>"
    doc = pylopdf.open(_build_outline_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
        doc.outline.entries()


def _outline_chain(count: int, entry: str = "") -> dict[int, str | bytes]:
    """Return ``count`` sibling outline items linked from object 5."""
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    last = 4 + count
    for object_id in range(5, last + 1):
        next_entry = f" /Next {object_id + 1} 0 R" if object_id < last else ""
        objects[object_id] = f"<<{entry}{next_entry} >>"
    return objects


def test_reads_entries_beyond_the_former_4096_limit() -> None:
    doc = pylopdf.open(_build_outline_fixture(_outline_chain(4_097, " /Title (A) /Dest [3 0 R /Fit]")))
    assert _simple(doc.outline.entries()) == [(1, "A", 0)] * 4_097
    assert len(doc.outline) == 4_097


def test_reads_refuse_excessive_nodes() -> None:
    doc = pylopdf.open(_build_outline_fixture(_outline_chain(MAX_TOC_ENTRIES + 1)))
    with pytest.raises(pylopdf.LimitError, match="65536-node safety limit") as caught:
        doc.outline.entries()
    assert caught.value.code == "toc_entry_count"
    with pytest.raises(pylopdf.LimitError, match="65536-entry safety limit") as caught:
        len(doc.outline)
    assert caught.value.code == "toc_entry_count"


def test_reads_refuse_excessive_edges() -> None:
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    last = 4 + MAX_TOC_ENTRIES
    for object_id in range(5, last + 1):
        next_id = object_id + 1 if object_id < last else 5
        objects[object_id] = f"<< /First 5 0 R /Next {next_id} 0 R >>"
    doc = pylopdf.open(_build_outline_fixture(objects))
    with pytest.raises(pylopdf.LimitError, match="131072-edge safety limit") as caught:
        doc.outline.entries()
    assert caught.value.code == "toc_entry_count"


def test_reads_refuse_excessive_title_bytes() -> None:
    title = "x" * (1024 * 1024)
    objects: dict[int, str | bytes] = {4: "<< /First 5 0 R >>"}
    for object_id in range(5, 22):
        next_entry = f" /Next {object_id + 1} 0 R" if object_id < 21 else ""
        objects[object_id] = f"<< /Title ({title}) /Dest [3 0 R /Fit]{next_entry} >>"
    doc = pylopdf.open(_build_outline_fixture(objects))
    with pytest.raises(pylopdf.LimitError, match="TOC source text exceeds the 16777216-byte safety limit") as caught:
        doc.outline.entries()
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


# --- write limits ----------------------------------------------------------------


def test_assigning_refuses_excessive_entries(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    with pytest.raises(pylopdf.LimitError, match="65536 items") as caught:
        doc.outline = [OutlineEntry(1, "A", Destination(0))] * (MAX_TOC_ENTRIES + 1)
    assert caught.value.code == "toc_entry_count"
    assert doc.outline.entries() == []

    core = _Document.load_bytes(three_page_pdf)
    target = ("goto", 1, None, None, None, None)
    with pytest.raises(LimitError, match="65536 outline items") as core_caught:
        core.write_outline([(1, "A", target, None, 0, False)] * (MAX_TOC_ENTRIES + 1))
    assert core_caught.value.args[0] == "toc_entry_count"
    assert core.outline_entries() == []


def test_write_limits_are_atomic(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    original = [OutlineEntry(1, "original", Destination(0))]
    doc.outline = original

    with pytest.raises(pylopdf.LimitError, match="16777216-byte encoded-text safety limit") as caught:
        doc.outline = [OutlineEntry(1, "é" * (MAX_TOC_TEXT_BYTES // 2), Destination(0))]
    assert caught.value.code == "toc_input_size"
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.outline = [OutlineEntry(1, "uri", "x" * MAX_TOC_TEXT_BYTES)]
    assert caught.value.code == "toc_input_size"
    assert doc.outline.entries() == original

    core = doc._doc  # noqa: SLF001 - the core repeats every check.
    target = ("goto", 1, None, None, None, None)
    with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
        core.write_outline([(level, str(level), target, None, 0, False) for level in range(1, 66)])
    for bad in (
        ("goto", 4, None, None, None, None),
        ("goto", 1, (float("nan"), 0.0), None, None, None),
        ("uri", None, None, None, "", None),
        ("teleport", None, None, None, None, None),
    ):
        with pytest.raises(pylopdf.PdfError):
            core.write_outline([(1, "bad", bad, None, 0, False)])
    with pytest.raises(pylopdf.PdfError, match="from 0 to 1"):
        core.write_outline([(1, "bad", target, (2.0, 0.0, 0.0), 0, False)])
    assert doc.outline.entries() == original
