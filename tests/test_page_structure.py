"""pymupdf's argument forms for deleting, copying, and inserting pages."""

from __future__ import annotations

import pytest
from conftest import build_pdf

import pylopdf


def _doc(count: int = 6) -> pylopdf.Document:
    return pylopdf.open(stream=build_pdf([f"Page {index}" for index in range(count)]))


def _texts(doc: pylopdf.Document) -> list[str]:
    return [page.get_text().strip() for page in doc]


def test_delete_page_defaults_to_the_last_page() -> None:
    doc = _doc(3)

    doc.delete_page()

    assert _texts(doc) == ["Page 0", "Page 1"]


@pytest.mark.parametrize(
    ("call", "remaining"),
    [
        (lambda doc: doc.delete_pages(1), [0, 2, 3, 4, 5]),
        (lambda doc: doc.delete_pages(-1), [0, 1, 2, 3, 4]),
        (lambda doc: doc.delete_pages(1, 3), [0, 4, 5]),
        (lambda doc: doc.delete_pages(3, 1), [0, 4, 5]),
        (lambda doc: doc.delete_pages(from_page=4), [0, 1, 2, 3]),
        (lambda doc: doc.delete_pages(from_page=0, to_page=1), [2, 3, 4, 5]),
        (lambda doc: doc.delete_pages(from_page=-3, to_page=-2), [0, 1, 2, 5]),
        (lambda doc: doc.delete_pages([5, 0, 0, 2]), [1, 3, 4]),
        (lambda doc: doc.delete_pages(range(2, 4)), [0, 1, 4, 5]),
        (lambda doc: doc.delete_pages(()), [0, 1, 2, 3, 4, 5]),
    ],
)
def test_delete_pages_accepts_pymupdf_forms(call: object, remaining: list[int]) -> None:
    doc = _doc()

    call(doc)  # ty: ignore[call-non-callable]

    assert _texts(doc) == [f"Page {index}" for index in remaining]


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda doc: doc.delete_pages(6), ValueError),
        (lambda doc: doc.delete_pages(-7), ValueError),
        (lambda doc: doc.delete_pages(0, 6), ValueError),
        (lambda doc: doc.delete_pages(-1, 2), ValueError),
        (lambda doc: doc.delete_pages([0, -1]), ValueError),
        (lambda doc: doc.delete_pages([0, 6]), ValueError),
        (lambda doc: doc.delete_pages(from_page=4, to_page=2), ValueError),
        # As in pymupdf, from_page defaults to the last page.
        (lambda doc: doc.delete_pages(to_page=1), ValueError),
        (lambda doc: doc.delete_pages(1, from_page=2), TypeError),
        (lambda doc: doc.delete_pages(0, 1, 2), TypeError),
        (lambda doc: doc.delete_pages(), TypeError),
        (lambda doc: doc.delete_pages([0.5]), TypeError),
        (lambda doc: doc.delete_pages(1.0), TypeError),
        (lambda doc: doc.delete_pages(0, 1.0), TypeError),
    ],
)
def test_delete_pages_refuses_bad_numbers_before_changing(call: object, error: type[Exception]) -> None:
    doc = _doc()
    before = doc.tobytes()

    with pytest.raises(error):
        call(doc)  # ty: ignore[call-non-callable]

    assert doc.tobytes() == before


def test_fullcopy_page_follows_pymupdf_ranges() -> None:
    doc = _doc(3)

    doc.fullcopy_page(0)
    doc.fullcopy_page(2, 0)

    assert _texts(doc) == ["Page 2", "Page 0", "Page 1", "Page 2", "Page 0"]
    for pno, to in ((-1, -1), (5, -1), (0, 5), (0, -2)):
        with pytest.raises(ValueError, match="bad page number"):
            doc.fullcopy_page(pno, to)
    assert not hasattr(doc, "copy_page")


def test_fullcopy_page_is_independent_of_the_original() -> None:
    doc = _doc(1)
    doc.fullcopy_page(0)

    doc[1].insert_text((20, 40), "Only on the copy")
    doc[1].set_rotation(90)

    assert "Only on the copy" not in doc[0].get_text()
    assert doc[0].rotation == 0
    reopened = pylopdf.open(stream=doc.tobytes())
    assert "Only on the copy" in reopened[1].get_text()


def test_insert_pdf_takes_docsrc_and_keyword_only_ranges() -> None:
    target = _doc(1)
    source = _doc(3)

    target.insert_pdf(docsrc=source, from_page=2, to_page=0, start_at=0)

    assert _texts(target) == ["Page 2", "Page 1", "Page 0", "Page 0"]
    with pytest.raises(TypeError):
        target.insert_pdf(source, 0, 1)  # ty: ignore[too-many-positional-arguments]


def test_select_takes_page_numbers_positionally() -> None:
    doc = _doc(3)

    doc.select([2, 0])

    assert _texts(doc) == ["Page 2", "Page 0"]
    with pytest.raises(TypeError):
        doc.select(page_numbers=[0])  # ty: ignore[positional-only-parameter-as-kwarg]
