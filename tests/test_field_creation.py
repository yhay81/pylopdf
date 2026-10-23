"""Creating and removing form fields with page.widgets.add, del page.widgets[i], and del doc.form[name]."""

from __future__ import annotations

from typing import Any, cast

import pytest
from test_acroform import _build_form_pdf

import pylopdf
from pylopdf import (
    Border,
    CheckBox,
    Color,
    ComboBox,
    FieldChoice,
    FieldFlags,
    FieldType,
    Font,
    ListBox,
    PushButton,
    RadioButton,
    Rect,
    SignatureField,
    TextField,
)


def _page(rotation: int = 0) -> pylopdf.Page:
    page = pylopdf.Document().pages.new((300, 300))
    page.rotation = rotation
    return page


def test_every_kind_of_field_is_created_with_its_widget() -> None:
    page = _page()
    doc = page.document

    text = page.widgets.add(TextField("name", (20, 20, 200, 40), value="Ada", label="Your name"))
    page.widgets.add(CheckBox("agree", (20, 60, 35, 75), checked=True))
    page.widgets.add(RadioButton("plan", (20, 100, 35, 115), on_state="Basic", selected=True))
    page.widgets.add(RadioButton("plan", (60, 100, 75, 115), on_state="Pro"))
    page.widgets.add(ComboBox("level", (20, 140, 200, 160), choices=["low", FieldChoice(value="hi", label="High")]))
    page.widgets.add(ListBox("items", (20, 170, 200, 220), choices=["a", "b"], value="b"))
    button = page.widgets.add(PushButton("go", (220, 20, 280, 40), caption="Go"))
    page.widgets.add(SignatureField("sig", (20, 240, 200, 280)))

    assert text.rect == Rect(20, 20, 200, 40)
    assert text.field is not None
    assert text.field.label == "Your name"
    kinds = {name: field.type for name, field in doc.form.items()}
    assert kinds == {
        "agree": FieldType.CHECKBOX,
        "go": FieldType.PUSHBUTTON,
        "items": FieldType.LISTBOX,
        "level": FieldType.COMBOBOX,
        "name": FieldType.TEXT,
        "plan": FieldType.RADIO,
        "sig": FieldType.SIGNATURE,
    }
    values = {name: field.value for name, field in doc.form.items()}
    assert values == {
        "agree": "Yes",
        "go": None,
        "items": "b",
        "level": None,
        "name": "Ada",
        "plan": "Basic",
        "sig": None,
    }
    assert len(doc.form["plan"].widgets) == 2
    assert doc.form["level"].choices == (FieldChoice(value="low", label="low"), FieldChoice(value="hi", label="High"))
    assert button.caption == "Go"
    assert "Ada" in page.text()
    assert "Go" in page.text()
    assert doc.form.need_appearances is False
    reopened = pylopdf.open(doc.to_bytes())
    assert {name: field.value for name, field in reopened.form.items()} == values


def test_created_fields_are_filled_by_pylopdf() -> None:
    page = _page(90)
    doc = page.document
    page.widgets.add(TextField("name", (20, 20, 200, 40), font=Font.standard("Courier"), font_size=10))
    page.widgets.add(CheckBox("agree", (20, 60, 35, 75)))
    page.widgets.add(RadioButton("plan", (20, 100, 35, 115), on_state="Basic"))
    page.widgets.add(RadioButton("plan", (60, 100, 75, 115), on_state="Pro"))

    doc.form["name"].value = "Grace"
    doc.form["agree"].value = True
    doc.form["plan"].value = "Pro"

    assert (doc.form["name"].value, doc.form["agree"].value, doc.form["plan"].value) == ("Grace", "Yes", "Pro")
    assert [widget.obj["AS"] for widget in doc.form["plan"].widgets] == ["Off", "Pro"]
    assert "Grace" in page.text()
    name = page.widgets[0]
    assert name.obj["MK"]["R"] == 90
    assert name.font is not None
    assert name.font.name == "Courier"


def test_a_second_widget_joins_the_field_and_splits_a_merged_one() -> None:
    page = _page()
    doc = page.document
    first = page.widgets.add(TextField("name", (20, 20, 200, 40), value="Ada"))
    assert first.field is not None
    assert first.field.xref == first.xref

    second = page.widgets.add(TextField("name", (20, 50, 200, 70)))

    field = doc.form["name"]
    assert field.widgets == [first, second]
    assert field.xref not in (first.xref, second.xref)
    assert field.value == "Ada"
    assert page.text().count("Ada") == 2
    with pytest.raises(pylopdf.PdfError, match="already has a text field"):
        page.widgets.add(CheckBox("name", (20, 80, 30, 90)))


def test_widgets_and_fields_are_removed() -> None:
    page = _page()
    doc = page.document
    page.widgets.add(TextField("name", (20, 20, 200, 40), value="Ada"))
    page.widgets.add(RadioButton("plan", (20, 100, 35, 115), on_state="Basic"))
    page.widgets.add(RadioButton("plan", (60, 100, 75, 115), on_state="Pro"))

    del page.widgets[1]
    assert len(doc.form["plan"].widgets) == 1
    del page.widgets[1]
    assert "plan" not in doc.form
    del doc.form["name"]
    assert len(doc.form) == 0
    assert len(page.widgets) == 0
    assert "Ada" not in page.text()
    with pytest.raises(KeyError):
        del doc.form["name"]


def test_removing_from_an_existing_form_keeps_the_others() -> None:
    doc = pylopdf.open(_build_form_pdf())
    before = set(doc.form)

    del doc.form["person.first"]
    del doc.pages[0].widgets[0]

    assert set(doc.form) == before - {"person.first", "customer"}
    assert "person" not in {name.split(".")[0] for name in doc.form}
    reopened = pylopdf.open(doc.to_bytes(prune=True))
    assert set(reopened.form) == set(doc.form)


def test_field_specifications_validate() -> None:
    page = _page()
    for spec, error in [
        (lambda: TextField("", (0, 0, 10, 10)), None),
        (lambda: TextField("a", (0, 0, 10, 10), max_length=-1), ValueError),
        (lambda: TextField("a", (0, 0, 10, 10), font=cast("Any", "Helvetica")), TypeError),
        (lambda: TextField("a", (0, 0, 10, 10), font_size=-1), ValueError),
        (lambda: TextField("a", (0, 0, 10, 10), align=cast("Any", "justify")), ValueError),
        (lambda: CheckBox("a", (0, 0, 10, 10), on_state="Off"), ValueError),
        (lambda: RadioButton("a", (0, 0, 10, 10), on_state=""), ValueError),
        (lambda: CheckBox("a", (0, 0, 10, 10), checked=cast("Any", 1)), TypeError),
        (lambda: ComboBox("a", (0, 0, 10, 10), choices=cast("Any", "abc")), TypeError),
        (lambda: ListBox("a", (0, 0, 10, 10), choices=["x"] * 4097), ValueError),
        (lambda: TextField(cast("Any", 5), (0, 0, 10, 10)), TypeError),
        (lambda: TextField("a", (0, 0, 10, 10), border=cast("Any", 1)), TypeError),
        (lambda: TextField("a", (0, 0, 10, 10), flags=cast("Any", True)), TypeError),
    ]:
        if error is None:
            spec()
            continue
        with pytest.raises(error):
            spec()
    with pytest.raises(pylopdf.PdfError, match="has a name"):
        page.widgets.add(TextField("", (0, 0, 10, 10)))
    with pytest.raises(pylopdf.PdfError, match="no dots"):
        page.widgets.add(TextField("a.b", (0, 0, 10, 10)))
    with pytest.raises(pylopdf.PdfError, match="bits that the kind sets"):
        page.widgets.add(TextField("a", (0, 0, 10, 10), flags=FieldFlags.RADIO))
    with pytest.raises(TypeError, match="field specification"):
        page.widgets.add(cast("Any", object()))
    with pytest.raises(pylopdf.LimitError):
        TextField("a", (0, 0, 10, 10), value="x" * (1024 * 1024 + 1))
    assert len(page.document.form) == 0
    assert "Annots" not in page.obj


def test_styled_fields_draw_their_style() -> None:
    page = _page()

    widget = page.widgets.add(
        TextField(
            "name",
            (20, 20, 200, 60),
            value="Ada",
            border=Border(2, dashes=(3, 1)),
            border_color=Color(0, 0, 1),
            background_color=Color(1, 1, 0),
            text_color=Color(1, 0, 0),
            align="center",
        )
    )

    assert widget.border == Border(2, dashes=(3, 1))
    assert widget.background_color == Color(1, 1, 0)
    assert widget.text_color == Color(1, 0, 0)
    assert widget.obj["Q"] == 1
    pixmap = page.pixmap(dpi=72)
    offset = 50 * pixmap.stride + 190 * pixmap.channels
    assert tuple(bytes(pixmap)[offset : offset + 3]) == (255, 255, 0)
