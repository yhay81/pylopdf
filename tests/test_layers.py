"""Optional-content groups: ``doc.layers`` and visibility in rendering and extraction."""

from __future__ import annotations

from typing import cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import LimitError, StaleObjectError

_CONTENT = (
    "BT /F1 12 Tf 20 170 Td (Alpha) Tj ET "
    "/OC /L1 BDC BT /F1 12 Tf 20 150 Td (Bravo) Tj ET EMC "
    "/OC /L2 BDC 0 0 1 rg 20 20 50 50 re f BT /F1 12 Tf 20 100 Td (Charlie) Tj ET EMC"
)


def _layered_pdf(*, properties: str | None = None, extra: dict[int, bytes | str] | None = None) -> bytes:
    """Return a page whose content shows text in groups 5 (Notes) and 6 (Hidden)."""
    if properties is None:
        properties = "<< /OCGs [5 0 R 6 0 R] /D << /Order [5 0 R 6 0 R] /OFF [6 0 R] >> >>"
    objects: dict[int, bytes | str] = {
        1: f"<< /Type /Catalog /Pages 2 0 R /OCProperties {properties} >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
            "/Resources << /Font << /F1 7 0 R >> /Properties << /L1 5 0 R /L2 6 0 R >> >> >>"
        ),
        4: f"<< /Length {len(_CONTENT)} >>\nstream\n{_CONTENT}\nendstream",
        5: "<< /Type /OCG /Name (Notes) >>",
        6: "<< /Type /OCG /Name (Hidden) /Intent [/View /Design] >>",
        7: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    objects.update(extra or {})
    return build_raw_pdf(objects)


def _blue(page: pylopdf.Page) -> bool:
    """Return whether the square that group 6 paints shows at (45, 155)."""
    rows = cast("list[list[list[int]]]", memoryview(page.pixmap()).tolist())
    return rows[155][45] == [0, 0, 255]


def test_layers_list_groups_with_names_intents_and_visibility() -> None:
    doc = pylopdf.open(_layered_pdf())

    notes, hidden = doc.layers
    assert len(doc.layers) == 2
    assert (notes.xref, notes.name, notes.intent, notes.is_visible) == (5, "Notes", ("View",), True)
    assert (hidden.xref, hidden.name, hidden.intent, hidden.is_visible) == (6, "Hidden", ("View", "Design"), False)
    assert doc.layers[-1] == hidden
    assert doc.layers[0:1] == [notes]
    assert repr(notes) == "Layer(xref=5, name='Notes')"
    assert notes.obj["Type"] == pylopdf.Name("OCG")


def test_visibility_follows_rendering_and_extraction() -> None:
    doc = pylopdf.open(_layered_pdf())
    page = doc.pages[0]
    notes, hidden = doc.layers

    assert page.text().split() == ["Alpha", "Bravo"]
    assert not _blue(page)

    hidden.is_visible = True
    notes.is_visible = False

    assert (notes.is_visible, hidden.is_visible) == (False, True)
    assert page.text().split() == ["Alpha", "Charlie"]
    assert _blue(page)
    assert [word.text for word in page.words()] == ["Alpha", "Charlie"]
    off = doc.catalog["OCProperties"]["D"]["OFF"]
    on = doc.catalog["OCProperties"]["D"].get("ON")
    assert [item.xref for item in off] == [5]
    assert on is None

    reopened = pylopdf.open(doc.to_bytes())
    assert [layer.is_visible for layer in reopened.layers] == [False, True]
    assert reopened.pages[0].text().split() == ["Alpha", "Charlie"]


def test_assigning_the_current_state_changes_nothing() -> None:
    doc = pylopdf.open(_layered_pdf())

    doc.layers[0].is_visible = True
    doc.layers[1].is_visible = False

    assert not doc.is_dirty


def test_base_state_off_hides_listed_groups_until_on_lists_them() -> None:
    properties = "<< /OCGs [5 0 R 6 0 R] /D << /BaseState /OFF /ON [5 0 R] >> >>"
    doc = pylopdf.open(_layered_pdf(properties=properties))
    notes, hidden = doc.layers

    assert (notes.is_visible, hidden.is_visible) == (True, False)
    assert doc.pages[0].text().split() == ["Alpha", "Bravo"]

    hidden.is_visible = True
    notes.is_visible = False

    config = doc.catalog["OCProperties"]["D"]
    assert [item.xref for item in config["ON"]] == [6]
    assert "OFF" not in config
    assert doc.pages[0].text().split() == ["Alpha", "Charlie"]


def test_off_wins_over_on_as_hayro_reads_them() -> None:
    properties = "<< /OCGs [5 0 R 6 0 R] /D << /ON [6 0 R] /OFF [6 0 R] >> >>"
    doc = pylopdf.open(_layered_pdf(properties=properties))

    assert doc.layers[1].is_visible is False
    assert doc.pages[0].text().split() == ["Alpha", "Bravo"]


def test_showing_a_group_hides_the_others_of_its_radio_group() -> None:
    properties = "<< /OCGs [5 0 R 6 0 R] /D << /OFF [6 0 R] /RBGroups [[5 0 R 6 0 R]] >> >>"
    doc = pylopdf.open(_layered_pdf(properties=properties))
    notes, hidden = doc.layers

    hidden.is_visible = True

    assert (notes.is_visible, hidden.is_visible) == (False, True)
    assert doc.pages[0].text().split() == ["Alpha", "Charlie"]


def test_indirect_properties_configuration_and_arrays_are_edited_in_place() -> None:
    properties = "8 0 R"
    extra: dict[int, bytes | str] = {
        8: "<< /OCGs 9 0 R /D 10 0 R >>",
        9: "[5 0 R 6 0 R]",
        10: "<< /OFF 11 0 R >>",
        11: "[6 0 R]",
    }
    doc = pylopdf.open(_layered_pdf(properties=properties, extra=extra))

    doc.layers[1].is_visible = True

    assert doc.objects[8]["D"].xref == 10
    assert "OFF" not in doc.objects[10]
    assert [item.xref for item in doc.objects[11]] == [6]
    assert doc.pages[0].text().split() == ["Alpha", "Bravo", "Charlie"]


def test_add_creates_optional_content_properties() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 200))

    shown = doc.layers.add("Shown")
    hidden = doc.layers.add("Ébauche", visible=False, intent=("Design",))

    assert doc.layers[:] == [shown, hidden]
    assert (hidden.name, hidden.intent, hidden.is_visible) == ("Ébauche", ("Design",), False)
    properties = doc.catalog["OCProperties"]
    assert [item.xref for item in properties["OCGs"]] == [shown.xref, hidden.xref]
    assert [item.xref for item in properties["D"]["Order"]] == [shown.xref, hidden.xref]
    assert [item.xref for item in properties["D"]["OFF"]] == [hidden.xref]
    assert "Intent" not in shown.obj
    assert page.text() == ""

    reopened = pylopdf.open(doc.to_bytes())
    assert [(layer.name, layer.intent, layer.is_visible) for layer in reopened.layers] == [
        ("Shown", ("View",), True),
        ("Ébauche", ("Design",), False),
    ]


def test_add_appends_to_an_existing_order_only_when_there_is_one() -> None:
    properties = "<< /OCGs [5 0 R 6 0 R] /D << >> >>"
    doc = pylopdf.open(_layered_pdf(properties=properties))

    layer = doc.layers.add("Third")

    assert "Order" not in doc.catalog["OCProperties"]["D"]
    assert doc.layers[-1] == layer
    assert layer.is_visible


def test_name_and_intent_are_assignable() -> None:
    doc = pylopdf.open(_layered_pdf())
    notes = doc.layers[0]

    notes.name = "注記"
    notes.intent = ("View", "Design")
    assert (notes.name, notes.intent) == ("注記", ("View", "Design"))
    notes.intent = ["View"]
    assert "Intent" not in notes.obj

    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.layers[0].name == "注記"


@pytest.mark.parametrize(
    ("intent", "error"),
    [
        ("View", TypeError),
        ((), ValueError),
        (("",), ValueError),
        (("Two words",), ValueError),
        (("a/b",), ValueError),
        (("x" * 128,), ValueError),
        ((1,), TypeError),
        (("View",) * 65, ValueError),
    ],
)
def test_intents_are_pdf_names(intent: object, error: type[Exception]) -> None:
    doc = pylopdf.open(_layered_pdf())

    with pytest.raises(error):
        doc.layers[0].intent = intent  # ty: ignore[invalid-assignment]
    with pytest.raises(error):
        doc.layers.add("x", intent=intent)  # ty: ignore[invalid-argument-type]
    assert len(doc.layers) == 2


def test_bad_names_and_states_are_refused() -> None:
    doc = pylopdf.open(_layered_pdf())
    notes = doc.layers[0]

    with pytest.raises(TypeError):
        notes.name = 5  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        notes.is_visible = 1  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        doc.layers.add("x", visible=None)  # ty: ignore[invalid-argument-type]
    with pytest.raises(LimitError) as caught:
        doc.layers.add("é" * (512 * 1024 + 1))
    assert caught.value.code == "layer_input_size"
    with pytest.raises(LimitError) as caught:
        doc._doc.add_layer(  # noqa: SLF001 - the core repeats the check.
            "x" * (1024 * 1024 + 1), ["View"], True
        )
    assert caught.value.code == "layer_input_size"
    assert not doc.is_dirty


def test_malformed_properties_read_as_hayro_reads_them() -> None:
    extra: dict[int, bytes | str] = {8: "<< /Name (Not listed) >>"}
    properties = "<< /OCGs [5 0 R 5 0 R 99 0 R /Name 8 0 R 7 0 R] /D [1 2] >>"
    doc = pylopdf.open(_layered_pdf(properties=properties, extra=extra))

    assert [layer.xref for layer in doc.layers] == [5, 8, 7]
    assert all(layer.is_visible for layer in doc.layers)
    assert doc.pages[0].text().split() == ["Alpha", "Bravo", "Charlie"]
    assert pylopdf.open(_layered_pdf(properties="5")).layers[:] == []


def test_a_group_outside_ocgs_can_still_be_hidden() -> None:
    properties = "<< /OCGs [5 0 R] /D << >> >>"
    doc = pylopdf.open(_layered_pdf(properties=properties))
    hidden = pylopdf.Layer(doc, 6, 0)

    assert hidden.is_visible
    hidden.is_visible = False

    assert [layer.xref for layer in doc.layers] == [5, 6]
    assert doc.pages[0].text().split() == ["Alpha", "Bravo"]


def test_handles_go_stale_when_objects_are_renumbered_or_removed() -> None:
    doc = pylopdf.open(_layered_pdf())
    notes = doc.layers[0]
    gone = pylopdf.Layer(doc, 99, 0)

    with pytest.raises(StaleObjectError):
        _ = gone.name
    assert repr(gone) == "<stale Layer xref=99>"
    doc.to_bytes(renumber=True)
    with pytest.raises(StaleObjectError):
        _ = notes.is_visible


def test_too_many_groups_raise_a_limit_error() -> None:
    refs = " ".join(["5 0 R"] * 65_537)
    doc = pylopdf.open(_layered_pdf(properties=f"<< /OCGs [{refs}] >>"))

    with pytest.raises(LimitError) as caught:
        list(doc.layers)
    assert caught.value.code == "layer_count"


def test_long_group_names_raise_a_limit_error() -> None:
    extra: dict[int, bytes | str] = {5: "<< /Type /OCG /Name (" + "x" * (1024 * 1024 + 1) + ") >>"}
    doc = pylopdf.open(_layered_pdf(extra=extra))

    with pytest.raises(LimitError) as caught:
        _ = doc.layers[0].name
    assert caught.value.code == "layer_text_size"
