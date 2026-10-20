"""Whole-document page batches on documents beyond the fixed 4,096-entry floor."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import build_pdf

import pylopdf
from pylopdf.pylopdf_core import LimitError, _Document

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# Two internal 4,096-entry chunks, while staying fast to build and interpret.
LARGE_PAGE_COUNT = 4_200


@pytest.fixture(scope="module")
def large_pdf() -> bytes:
    return build_pdf([f"Page {index}" for index in range(LARGE_PAGE_COUNT)])


def _zeros(limit: int, consumed: list[int]) -> Iterator[int]:
    for _ in range(limit):
        consumed[0] += 1
        yield 0


def test_get_text_extracts_every_page_across_chunks(large_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=large_pdf)
    expected = "".join(f"Page {index}\n" for index in range(LARGE_PAGE_COUNT))

    assert doc.get_text() == expected
    assert doc.get_text(range(LARGE_PAGE_COUNT)) == expected
    assert doc.get_text([4199, 4096, 4095, 0]) == "Page 4199\nPage 4096\nPage 4095\nPage 0\n"


def test_get_text_output_budget_spans_every_chunk(large_pdf: bytes) -> None:
    page_text = "Page 0\n"
    # Plain output may use twice max_text_size. One 4,096-entry chunk fits,
    # so only a call-wide budget rejects the complete repeated batch.
    budget_entries = 4_150
    limits = pylopdf.DocumentLimits(max_text_size=budget_entries * len(page_text) // 2)
    doc = pylopdf.Document(stream=large_pdf, limits=limits)

    assert doc.get_text([0] * budget_entries) == page_text * budget_entries
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.get_text([0] * LARGE_PAGE_COUNT)
    assert caught.value.code == "text_size"


def test_to_markdown_converts_every_page(large_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=large_pdf)
    assert doc.to_markdown() == "\n\n".join(f"Page {index}" for index in range(LARGE_PAGE_COUNT))
    assert doc.to_markdown([4199, 0]) == "Page 4199\n\nPage 0"


def test_render_pages_renders_every_page_across_chunks(large_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=large_pdf)
    scale = 0.05

    for workers in (1, 2):
        rendered = doc.render_pages(matrix=pylopdf.Matrix(scale, scale), alpha=True, workers=workers)
        assert len(rendered) == LARGE_PAGE_COUNT
        for index in (0, 4095, 4096, LARGE_PAGE_COUNT - 1):
            assert (
                rendered[index] == doc.get_page_pixmap(index, matrix=pylopdf.Matrix(scale, scale), alpha=True).tobytes()
            )


def test_select_reorders_every_page(large_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=large_pdf)
    doc.select(range(LARGE_PAGE_COUNT - 1, -1, -1))

    assert doc.page_count == LARGE_PAGE_COUNT
    assert doc.get_page_text(0) == f"Page {LARGE_PAGE_COUNT - 1}\n"
    assert doc.get_page_text(LARGE_PAGE_COUNT - 1) == "Page 0\n"


def test_select_accepts_page_count_duplicates_without_growing(large_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=large_pdf)
    doc.select([0] * LARGE_PAGE_COUNT)

    assert doc.page_count == LARGE_PAGE_COUNT
    assert doc.get_text([LARGE_PAGE_COUNT - 1]) == "Page 0\n"


def test_delete_pages_accepts_more_than_the_floor(large_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=large_pdf)
    doc.delete_pages(range(4_150))

    assert doc.page_count == LARGE_PAGE_COUNT - 4_150
    assert doc.get_page_text(0) == "Page 4150\n"


def test_insert_pdf_imports_a_complete_large_document(large_pdf: bytes) -> None:
    source = pylopdf.Document(stream=large_pdf)
    appended = pylopdf.Document()
    appended.insert_pdf(source)
    assert appended.page_count == LARGE_PAGE_COUNT
    assert appended.get_text([0, LARGE_PAGE_COUNT - 1]) == f"Page 0\nPage {LARGE_PAGE_COUNT - 1}\n"

    reversed_doc = pylopdf.Document()
    reversed_doc.insert_pdf(source, from_page=LARGE_PAGE_COUNT - 1, to_page=0)
    assert reversed_doc.page_count == LARGE_PAGE_COUNT
    assert reversed_doc.get_page_text(0) == f"Page {LARGE_PAGE_COUNT - 1}\n"


@pytest.mark.parametrize(
    "operation",
    [
        lambda doc, pages: doc.get_text(pages),
        lambda doc, pages: doc.to_markdown(pages),
        lambda doc, pages: doc.render_pages(pages, matrix=pylopdf.Matrix(0.05, 0.05), alpha=True),
        lambda doc, pages: doc.select(pages),
        lambda doc, pages: doc.delete_pages(pages),
    ],
    ids=["get_text", "to_markdown", "render_pages", "select", "delete_pages"],
)
def test_explicit_entries_stop_after_the_page_count(
    large_pdf: bytes,
    operation: Callable[[pylopdf.Document, Iterator[int]], object],
) -> None:
    doc = pylopdf.Document(stream=large_pdf)
    page = doc[LARGE_PAGE_COUNT - 1]
    consumed = [0]

    with pytest.raises(pylopdf.LimitError, match=f"more than {LARGE_PAGE_COUNT} entries") as caught:
        operation(doc, _zeros(LARGE_PAGE_COUNT + 800, consumed))

    assert caught.value.code == "page_entry_count"
    assert consumed[0] == LARGE_PAGE_COUNT + 1
    assert doc.page_count == LARGE_PAGE_COUNT
    assert page.get_text() == f"Page {LARGE_PAGE_COUNT - 1}\n"


def test_core_page_batches_are_bounded_by_the_page_count(large_pdf: bytes) -> None:
    doc = _Document.load_bytes(large_pdf)
    every_page = list(range(1, LARGE_PAGE_COUNT + 1))
    too_many = [1] * (LARGE_PAGE_COUNT + 1)

    assert doc.extract_text([1] * LARGE_PAGE_COUNT) == "Page 0\n" * LARGE_PAGE_COUNT
    calls: list[Callable[[], object]] = [
        lambda: doc.extract_text(too_many),
        lambda: doc.render_pages_png(too_many, (0.05, 0.05), True, None, 1, None),
        lambda: doc.select(too_many),
        lambda: doc.delete_pages(too_many),
        lambda: _Document().merge_pages(doc, too_many, None),
    ]
    for call in calls:
        with pytest.raises(LimitError) as caught:
            call()
        assert caught.value.args[0] == "page_entry_count"
        assert doc.page_count() == LARGE_PAGE_COUNT

    merged = _Document()
    merged.merge_pages(doc, every_page, None)
    assert merged.page_count() == LARGE_PAGE_COUNT
    merged.merge(doc)
    assert merged.page_count() == 2 * LARGE_PAGE_COUNT


def test_large_manual_toc_round_trips_through_page_edits(large_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=large_pdf)
    outline: list[tuple[int, str, int]] = []
    for index in range(LARGE_PAGE_COUNT):
        outline.append((1, f"Chapter {index}", index))
        outline.append((2, f"Section {index}.1", index))
    doc.set_outline(outline)

    assert len(outline) > 4_096
    assert doc.get_outline() == outline
    assert pylopdf.Document(stream=doc.tobytes()).get_outline() == outline

    doc.delete_pages(range(LARGE_PAGE_COUNT // 2))
    retained = [(level, title, page - LARGE_PAGE_COUNT // 2) for level, title, page in outline[LARGE_PAGE_COUNT:]]
    assert doc.get_outline() == retained
