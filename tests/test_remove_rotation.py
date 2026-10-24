"""``Page.remove_rotation``: ``/Rotate 0`` with the page showing what it showed."""

from __future__ import annotations

from typing import Any

import pytest

import pylopdf
from pylopdf import Color, Destination, Highlight, Ink, LineAnnotation, Matrix, Note, OutlineEntry, Rect, Square

RED = Color(1, 0, 0)


def _page(rotation: int) -> pylopdf.Document:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 200))
    page.boxes.crop = (10, 20, 290, 190)
    with page.draw() as canvas:
        canvas.rect((20, 30, 120, 80), fill=RED)
        canvas.text("Turned text", (30, 120), size=14)
    page.annotations.add(Square((150, 30, 220, 90), color=Color(0, 0, 1)))
    page.annotations.add(Highlight([(30, 105, 120, 125)]))
    page.annotations.add(Ink([[(160, 120), (200, 150), (240, 130)]]))
    page.annotations.add(LineAnnotation((40, 150), (100, 170)))
    page.links.add((150, 120, 250, 160), Destination(0, point=(50, 60), zoom=2.0))
    page.rotation = rotation
    doc.outline = [OutlineEntry(1, "Top", Destination(0, point=(100, 40)))]
    return doc


def _snapshot(doc: pylopdf.Document) -> dict[str, Any]:
    page = doc.pages[0]
    return {
        "rect": page.rect,
        "words": [(word.text, tuple(round(value, 2) for value in word.bbox)) for word in page.words()],
        "annotations": [
            (annotation.type, tuple(round(value, 2) for value in annotation.rect)) for annotation in page.annotations
        ],
        "links": [(tuple(round(value, 2) for value in link.rect), link.target) for link in page.links],
        "outline": [item.destination for item in doc.outline],
    }


def _pixels(doc: pylopdf.Document) -> bytes:
    return bytes(doc.pages[0].pixmap())


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_the_page_shows_and_reads_what_it_showed(rotation: int) -> None:
    doc = _page(rotation)
    before = _snapshot(doc)
    pixels = _pixels(doc)

    matrix = doc.pages[0].remove_rotation()

    assert isinstance(matrix, Matrix)
    assert doc.pages[0].rotation == 0
    assert _snapshot(doc) == before
    after = _pixels(doc)
    assert len(after) == len(pixels)
    differing = sum(1 for old, new in zip(pixels, after, strict=True) if abs(old - new) > 8)
    assert differing <= len(pixels) // 1000

    reopened = pylopdf.open(doc.to_bytes())
    assert _snapshot(reopened) == before
    assert reopened.pages[0].rotation == 0


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_boxes_map_into_the_upright_page(rotation: int) -> None:
    doc = _page(rotation)
    page = doc.pages[0]
    page.boxes.trim = (30, 40, 270, 180)
    width, height = page.rect.width, page.rect.height

    page.remove_rotation()

    boxes = page.boxes
    assert boxes.crop == Rect(0, 0, width, height)
    assert boxes.media.width == pytest.approx(300 if rotation == 180 else 200)
    assert boxes.trim.width == pytest.approx(240 if rotation == 180 else 140)


def test_the_matrix_maps_the_old_user_space() -> None:
    doc = _page(90)
    page = doc.pages[0]
    before = page.user_to_display

    matrix = page.remove_rotation()

    assert matrix * page.user_to_display == pytest.approx(before)


def test_an_upright_page_is_left_alone() -> None:
    doc = pylopdf.open(_page(0).to_bytes())

    assert doc.pages[0].remove_rotation() == Matrix.IDENTITY
    assert not doc.is_dirty


def test_inherited_rotation_and_boxes_are_resolved() -> None:
    doc = pylopdf.Document()
    doc.pages.new((300, 200))
    doc.pages[0].obj["Parent"]["Rotate"] = 90
    doc.pages[0].obj["Parent"]["MediaBox"] = [0, 0, 300, 200]
    for key in ("MediaBox", "Rotate"):
        if key in doc.pages[0].obj:
            del doc.pages[0].obj[key]
    assert doc.pages[0].rotation == 90

    doc.pages[0].remove_rotation()

    assert doc.pages[0].rotation == 0
    assert doc.pages[0].rect == Rect(0, 0, 200, 300)


def test_no_rotate_annotations_keep_their_corner_upright() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 200))
    note = page.annotations.add(Note((50, 60)))
    page.rotation = 90
    page = doc.pages[0]
    rect = page.annotations[0].rect
    # The upper-left corner of /Rect in user space displays at the top right
    # of the turned rectangle.
    corner = (rect.x1, rect.y0)
    del note

    page.remove_rotation()

    moved = page.annotations[0].rect
    assert (moved.width, moved.height) == (20, 20)
    assert (moved.x0, moved.y0) == pytest.approx(corner)


def test_destinations_elsewhere_follow_the_page() -> None:
    doc = _page(90)
    second = doc.pages.new((300, 200))
    second.links.add((10, 10, 50, 50), Destination(0, point=(70, 80)))
    raw = doc.pages[1].obj["Annots"][0]
    raw["Dest"] = [doc.pages[0].obj, pylopdf.Name("FitH"), 150]
    fit_before = doc.pages[1].links[0].target

    doc.pages[0].remove_rotation()

    assert doc.pages[1].links[0].target == fit_before
    dest = doc.pages[1].obj["Annots"][0]["Dest"]
    assert dest[1] == "FitV"


def test_widgets_and_free_text_keep_their_turn() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 200))
    page.widgets.add(pylopdf.TextField("name", (20, 20, 120, 40), value="Hello"))
    page.rotation = 270
    before = _pixels(doc)

    doc.pages[0].remove_rotation()

    assert doc.pages[0].widgets[0].obj["MK"]["R"] == 90
    after = _pixels(doc)
    differing = sum(1 for old, new in zip(before, after, strict=True) if abs(old - new) > 8)
    assert differing <= len(before) // 1000
