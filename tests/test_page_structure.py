"""doc.pages: a sequence that adds, copies, deletes, selects, and moves pages."""

from __future__ import annotations

from operator import delitem
from typing import TYPE_CHECKING

import pytest
from conftest import build_pdf

import pylopdf

if TYPE_CHECKING:
    from collections.abc import Callable


def _doc(count: int = 6) -> pylopdf.Document:
    return pylopdf.open(build_pdf([f"Page {index}" for index in range(count)]))


def _texts(doc: pylopdf.Document) -> list[str]:
    return [page.get_text().strip() for page in doc.pages]


def _numbers(doc: pylopdf.Document) -> list[int]:
    return [int(text.removeprefix("Page ")) for text in _texts(doc)]


def test_pages_is_a_sequence_of_page_views() -> None:
    doc = _doc(4)
    pages = doc.pages

    assert len(pages) == 4
    assert pages[0] == pages[0]
    assert pages[-1] == pages[3]
    assert pages[-1].index == 3
    assert pages[0].document is doc
    assert [page.index for page in pages] == [0, 1, 2, 3]
    assert [page.index for page in pages[1:3]] == [1, 2]
    assert [page.index for page in pages[::-2]] == [3, 1]
    assert [page.index for page in reversed(pages)] == [3, 2, 1, 0]
    assert pages[2] in pages
    assert pages.index(pages[2]) == 2
    assert pylopdf.Document().pages[:] == []
    assert hash(pages[1]) == hash(pages[1])
    assert len({pages[1], pages[1], pages[2]}) == 2
    assert repr(pages).startswith("<Pages of <pylopdf.Document")


@pytest.mark.parametrize("index", [4, -5])
def test_page_indexes_out_of_range_raise_index_error(index: int) -> None:
    with pytest.raises(IndexError, match="out of range for 4 pages"):
        _ = _doc(4).pages[index]


@pytest.mark.parametrize("index", [1.0, "1", True, None])
def test_page_indexes_must_be_integers(index: object) -> None:
    with pytest.raises(TypeError, match="integers"):
        _ = _doc(2).pages[index]  # ty: ignore[invalid-argument-type]


def test_documents_point_old_syntax_at_pages() -> None:
    doc = _doc(2)

    with pytest.raises(TypeError, match=r"use doc\.pages\[index\]"):
        _ = doc[0]  # ty: ignore[not-subscriptable]
    with pytest.raises(TypeError, match=r"iterate over doc\.pages"):
        list(doc)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        len(doc)  # ty: ignore[invalid-argument-type]
    with pytest.raises(AttributeError, match=r"len\(doc\.pages\)"):
        _ = doc.page_count  # ty: ignore[unresolved-attribute]


def test_structural_edits_make_earlier_views_stale() -> None:
    doc = _doc(3)
    page = doc.pages[1]

    del doc.pages[0]

    assert page not in doc.pages
    assert page != doc.pages[0]
    with pytest.raises(pylopdf.StalePageError, match=r"doc\.pages\[1\]"):
        page.get_text()


def test_changing_the_pages_during_iteration_raises() -> None:
    doc = _doc(3)
    pages = iter(doc.pages)
    assert next(pages).index == 0

    del doc.pages[2]

    with pytest.raises(RuntimeError, match="changed during iteration"):
        next(pages)


@pytest.mark.parametrize(
    ("edit", "remaining"),
    [
        (lambda pages: delitem(pages, 1), [0, 2, 3, 4, 5]),
        (lambda pages: delitem(pages, -1), [0, 1, 2, 3, 4]),
        (lambda pages: delitem(pages, slice(1, 4)), [0, 4, 5]),
        (lambda pages: delitem(pages, slice(4, None)), [0, 1, 2, 3]),
        (lambda pages: delitem(pages, slice(-3, -1)), [0, 1, 2, 5]),
        (lambda pages: delitem(pages, slice(None, None, 2)), [1, 3, 5]),
        (lambda pages: delitem(pages, slice(5, None, -2)), [0, 2, 4]),
        (lambda pages: delitem(pages, slice(3, 1)), [0, 1, 2, 3, 4, 5]),
    ],
)
def test_del_takes_an_index_or_a_slice(edit: Callable[[pylopdf.Pages], None], remaining: list[int]) -> None:
    doc = _doc()

    edit(doc.pages)

    assert _numbers(doc) == remaining


def test_deleting_no_pages_changes_nothing() -> None:
    doc = _doc(2)
    page = doc.pages[0]
    before = doc.tobytes()

    del doc.pages[1:1]

    assert doc.tobytes() == before
    assert page.get_text().strip() == "Page 0"


@pytest.mark.parametrize(("index", "error"), [(6, IndexError), (-7, IndexError), (1.0, TypeError)])
def test_del_refuses_bad_indexes_before_changing(index: object, error: type[Exception]) -> None:
    doc = _doc()
    before = doc.tobytes()

    with pytest.raises(error):
        del doc.pages[index]  # ty: ignore[invalid-argument-type]

    assert doc.tobytes() == before


@pytest.mark.parametrize(
    ("index", "expected"),
    [(None, [0, 1, 2, "new"]), (0, ["new", 0, 1, 2]), (-1, [0, 1, "new", 2]), (9, [0, 1, 2, "new"])],
)
def test_new_inserts_a_blank_page_where_list_insert_would(index: int | None, expected: list[object]) -> None:
    doc = _doc(3)

    page = doc.pages.new((200, 300), index=index)

    assert page == doc.pages[page.index]
    assert page.rect == pylopdf.Rect(0, 0, 200, 300)
    assert [text.removeprefix("Page ") or "new" for text in _texts(doc)] == [str(item) for item in expected]


def test_new_defaults_to_a4_and_validates_the_size() -> None:
    doc = pylopdf.Document()

    assert doc.pages.new().rect == pylopdf.Rect(0, 0, 595, 842)
    for size in ((0, 100), (100, float("inf")), (100,), "A4", None):
        with pytest.raises(ValueError, match="size"):
            doc.pages.new(size)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        doc.pages.new((100, 100), 0)  # ty: ignore[too-many-positional-arguments]
    assert len(doc.pages) == 1


def test_insert_copies_pages_of_the_same_document() -> None:
    doc = _doc(3)

    doc.pages.extend(doc.pages[0])
    doc.pages.insert(0, doc.pages[2])
    doc.pages.insert(-1, [doc.pages[1], doc.pages[1]])

    assert _numbers(doc) == [2, 0, 1, 2, 0, 0, 0]


def test_a_copy_is_independent_of_the_original() -> None:
    doc = _doc(1)
    xref = doc.pages[0].xref
    doc.pages.extend(doc.pages[0])

    doc.pages[1].insert_text((20, 40), "Only on the copy")
    doc.pages[1].set_rotation(90)

    assert doc.pages[0].xref == xref
    assert doc.pages[1].xref != xref
    assert "Only on the copy" not in doc.pages[0].get_text()
    assert doc.pages[0].rotation == 0
    reopened = pylopdf.open(doc.tobytes())
    assert "Only on the copy" in reopened.pages[1].get_text()


def test_insert_imports_pages_of_another_document_in_any_order() -> None:
    target = _doc(1)
    source = _doc(4)

    target.pages.insert(0, source.pages[::-2])
    target.pages.extend(source.pages[0])
    target.pages.extend(source.pages)

    assert _numbers(target) == [3, 1, 0, 0, 0, 1, 2, 3]
    assert len(source.pages) == 4


def test_insert_refuses_mixed_or_repeated_sources_before_changing() -> None:
    target = _doc(1)
    first = _doc(2)
    second = _doc(2)
    before = target.tobytes()

    with pytest.raises(ValueError, match="one document"):
        target.pages.extend([first.pages[0], second.pages[0]])
    with pytest.raises(ValueError, match="once per call"):
        target.pages.extend([first.pages[0], first.pages[0]])
    with pytest.raises(TypeError, match="Page"):
        target.pages.extend([0, 1])  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match=r"iterate over doc\.pages"):
        target.pages.extend(first)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="insertion index"):
        target.pages.insert(0.5, first.pages)  # ty: ignore[invalid-argument-type]
    stale = first.pages[0]
    del first.pages[1]
    with pytest.raises(pylopdf.StalePageError):
        target.pages.extend(stale)

    assert target.tobytes() == before
    target.pages.extend([])
    assert target.tobytes() == before


def test_select_keeps_and_repeats_pages_in_order() -> None:
    doc = _doc(3)

    doc.pages.select([2, 0, 0, -1])

    assert _numbers(doc) == [2, 0, 0, 2]
    with pytest.raises(TypeError):
        doc.pages.select(indexes=[0])  # ty: ignore[positional-only-parameter-as-kwarg]
    with pytest.raises(IndexError):
        doc.pages.select([4])
    assert len(doc.pages) == 4


@pytest.mark.parametrize(
    ("index", "to", "expected"),
    [(0, 2, [1, 2, 0, 3]), (3, 0, [3, 0, 1, 2]), (-1, 1, [0, 3, 1, 2]), (1, -1, [0, 2, 3, 1]), (2, 2, [0, 1, 2, 3])],
)
def test_move_puts_the_page_at_the_destination_index(index: int, to: int, expected: list[int]) -> None:
    doc = _doc(4)
    xref = doc.pages[index].xref

    doc.pages.move(index, to)

    assert _numbers(doc) == expected
    assert doc.pages[to].xref == xref


def test_move_refuses_indexes_out_of_range() -> None:
    doc = _doc(2)
    with pytest.raises(IndexError):
        doc.pages.move(0, 2)
    assert _numbers(doc) == [0, 1]


def test_labeled_finds_pages_by_their_label() -> None:
    doc = _doc(4)
    assert doc.pages.labeled("1") == []
    doc.set_page_labels([{"startpage": 0, "style": "r"}, {"startpage": 2, "style": "D"}])

    assert doc.pages.labeled("ii") == [1]
    assert doc.pages.labeled("1") == [2]
    assert doc.pages.labeled("") == []
    with pytest.raises(TypeError, match="label"):
        doc.pages.labeled(1)  # ty: ignore[invalid-argument-type]
