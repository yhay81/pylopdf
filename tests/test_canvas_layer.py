"""``Canvas.layer``: drawing optional content on an open document's page."""

from __future__ import annotations

from typing import cast

import pytest

import pylopdf
from pylopdf import Color, LayerMembership, StaleObjectError, VisibilityPolicy

RED = Color(1, 0, 0)


def _pixel(page: pylopdf.Page, x: int, y: int) -> list[int]:
    rows = cast("list[list[list[int]]]", memoryview(page.pixmap()).tolist())
    return rows[y][x]


def _drawn(*, visible: bool) -> tuple[pylopdf.Document, pylopdf.Layer]:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 200))
    layer = doc.layers.add("Overlay", visible=visible)
    with page.draw() as canvas:
        canvas.text("Base", (20, 40))
        with canvas.layer(layer):
            canvas.rect((100, 100, 150, 150), fill=RED)
            canvas.text("Layered", (20, 80))
    return doc, layer


def test_a_layer_scope_hides_and_shows_what_it_draws() -> None:
    doc, layer = _drawn(visible=False)
    page = doc.pages[0]

    assert page.text().split() == ["Base"]
    assert _pixel(page, 125, 125) == [255, 255, 255]

    layer.is_visible = True
    assert page.text().split() == ["Base", "Layered"]
    assert _pixel(page, 125, 125) == [255, 0, 0]

    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].text().split() == ["Base", "Layered"]
    reopened.layers[0].is_visible = False
    assert reopened.pages[0].text().split() == ["Base"]


def test_the_scope_names_the_group_in_the_form_resources() -> None:
    doc, layer = _drawn(visible=True)
    form = doc.pages[0].resources.images  # the canvas Form holds no image
    assert form == ()

    page = doc.pages[0].obj
    xobjects = page["Resources"]["XObject"]
    canvas_form = next(iter(xobjects.values()))
    properties = canvas_form["Resources"]["Properties"]
    assert [(name, value.xref) for name, value in properties.items()] == [("PyloL0", layer.xref)]
    assert b"/OC /PyloL0 BDC" in canvas_form.read()


def test_membership_scopes_write_a_membership_dictionary() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 200))
    notes = doc.layers.add("Notes")
    draft = doc.layers.add("Draft", visible=False)
    with page.draw() as canvas:
        with canvas.layer(LayerMembership((notes, draft), policy=VisibilityPolicy.ALL_ON)):
            canvas.text("Both", (20, 40))
        with canvas.layer(LayerMembership((notes, draft), policy=VisibilityPolicy.ANY_ON)):
            canvas.text("Either", (20, 80))

    assert page.text().split() == ["Either"]
    draft.is_visible = True
    assert page.text().split() == ["Both", "Either"]


def test_layers_nest_with_other_scopes() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 200))
    outer = doc.layers.add("Outer")
    inner = doc.layers.add("Inner")
    with page.draw() as canvas:
        with canvas.layer(outer), canvas.opacity(0.5), canvas.layer(inner):
            canvas.text("Nested", (20, 40))
        with canvas.transform(pylopdf.Matrix(1, 0, 0, 1, 0, 50)), canvas.layer(inner):
            canvas.text("Moved", (20, 40))

    assert page.text().split() == ["Nested", "Moved"]
    inner.is_visible = False
    assert page.text() == ""
    inner.is_visible = True
    outer.is_visible = False
    assert page.text().split() == ["Moved"]


def test_layers_of_other_documents_and_new_documents_are_refused() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 200))
    other = pylopdf.Document()
    foreign = other.layers.add("Foreign")

    with page.draw() as canvas:
        with pytest.raises(ValueError, match="this document"):
            canvas.layer(foreign)
        with pytest.raises(TypeError):
            canvas.layer(None)  # ty: ignore[invalid-argument-type]
        with pytest.raises(TypeError):
            canvas.layer("Foreign")  # ty: ignore[invalid-argument-type]

    builder = pylopdf.create()
    with builder.page((100, 100)) as canvas, pytest.raises(ValueError, match="krilla"):
        canvas.layer(foreign)


def test_renumbering_before_the_commit_refuses_it() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 200))
    layer = doc.layers.add("Moved")

    canvas = page.draw()
    with canvas.layer(layer):
        canvas.rect((10, 10, 20, 20), fill=RED)
    doc.to_bytes(renumber=True)
    with pytest.raises(StaleObjectError):
        canvas.__exit__(None, None, None)
    assert "XObject" not in (doc.pages[0].obj.get("Resources") or {})


def test_layers_and_tags_nest() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    layer = doc.layers.add("Notes")
    with page.draw() as canvas, canvas.tag(pylopdf.TagRole.PARAGRAPH), canvas.layer(layer):
        canvas.text("Layered", (20, 40))

    root = doc.catalog["StructTreeRoot"]
    paragraph = root["K"][0]
    content = paragraph["K"][0]["Stm"].read()
    assert content.index(b"/OC /PyloL0 BDC") < content.index(b"/P <</MCID 0>> BDC")
    layer.is_visible = False
    assert page.text() == ""
