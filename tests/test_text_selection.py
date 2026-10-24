"""The text that a viewer selects between two points."""

from __future__ import annotations

import pytest
from test_text_options import build_text_pdf

import pylopdf


def _page() -> pylopdf.Page:
    return pylopdf.open(
        build_text_pdf(
            "BT /F1 12 Tf 14 TL 72 700 Td (The quick brown fox) Tj T* (jumps over the) Tj T* (lazy dog) Tj ET"
        )
    ).pages[0]


def _start(word: pylopdf.Word) -> tuple[float, float]:
    """Return a point on the first character of a word."""
    box = word.bbox
    return (box.x0 + 1, (box.y0 + box.y1) / 2)


def _end(word: pylopdf.Word) -> tuple[float, float]:
    """Return a point on the last character of a word."""
    box = word.bbox
    return (box.x1 - 1, (box.y0 + box.y1) / 2)


def test_selection_runs_in_reading_order_between_two_points() -> None:
    page = _page()
    words = {word.text: word for word in page.words()}

    assert page.selection(_start(words["quick"]), _end(words["the"])) == "quick brown fox\njumps over the"
    # The order of the points does not matter.
    assert page.selection(_end(words["the"]), _start(words["quick"])) == "quick brown fox\njumps over the"
    assert page.selection(_start(words["brown"]), _start(words["brown"])) == "b"
    # A point off the text selects from the nearest character.
    x, y = _end(words["dog"])
    assert page.selection((0, 0), (x + 20, y + 20)) == page.text().removesuffix("\n")


def test_selection_keeps_to_a_clip() -> None:
    page = _page()

    words = {word.text: word for word in page.words()}
    clip = (0, 0, 612, words["lazy"].bbox.y0)

    assert page.selection((0, 0), _end(words["the"]), clip=clip) == "The quick brown fox\njumps over the"
    # The nearest character to a point outside the clip is inside it.
    assert page.selection((0, 0), (612, 792), clip=clip) == "The quick brown fox"


def test_selection_of_a_page_without_text_is_empty() -> None:
    document = pylopdf.Document()

    assert document.pages.new().selection((0, 0), (100, 100)) == ""


@pytest.mark.parametrize("point", [(1,), ("a", 2), (float("nan"), 3), None])
def test_selection_points_are_validated(point: object) -> None:
    with pytest.raises(ValueError, match="start must be"):
        _page().selection(point, (1, 2))  # ty: ignore[invalid-argument-type]
