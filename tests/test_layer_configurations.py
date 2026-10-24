"""Optional-content configurations: ``doc.layers.configurations``."""

from __future__ import annotations

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import LayerBaseState, LayerOrder, LimitError, StaleObjectError

_CONTENT = "/OC /L1 BDC BT /F1 12 Tf 20 150 Td (Bravo) Tj ET EMC /OC /L2 BDC BT /F1 12 Tf 20 100 Td (Charlie) Tj ET EMC"

_DEFAULT = "<< /Name (Default) /Order [5 0 R [(Group) 6 0 R]] /OFF [6 0 R] /RBGroups [[5 0 R 6 0 R]] /Locked [5 0 R] >>"
_ALTERNATE = "<< /Name (Print) /Creator (Tool) /BaseState /OFF /ON [6 0 R] >>"


def _pdf(properties: str | None = None, extra: dict[int, bytes | str] | None = None) -> bytes:
    if properties is None:
        properties = f"<< /OCGs [5 0 R 6 0 R] /D {_DEFAULT} /Configs [{_ALTERNATE} 8 0 R] >>"
    objects: dict[int, bytes | str] = {
        1: f"<< /Type /Catalog /Pages 2 0 R /OCProperties {properties} >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
            "/Resources << /Font << /F1 7 0 R >> /Properties << /L1 5 0 R /L2 6 0 R >> >> >>"
        ),
        4: f"<< /Length {len(_CONTENT)} >>\nstream\n{_CONTENT}\nendstream",
        5: "<< /Type /OCG /Name (Notes) >>",
        6: "<< /Type /OCG /Name (Hidden) >>",
        7: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        8: "<< /Name (Indirect) /OFF [5 0 R] /Order [5 0 R [6 0 R]] >>",
    }
    objects.update(extra or {})
    return build_raw_pdf(objects)


def test_configurations_read_every_entry() -> None:
    doc = pylopdf.open(_pdf())
    notes, hidden = doc.layers
    default, alternate, indirect = doc.layers.configurations

    assert len(doc.layers.configurations) == 3
    assert (default.index, default.name, default.creator, default.base_state) == (0, "Default", None, LayerBaseState.ON)
    assert default.on == ()
    assert default.off == (hidden,)
    assert default.locked == (notes,)
    assert default.radio_groups == ((notes, hidden),)
    assert default.order == (notes, LayerOrder("Group", (hidden,)))
    assert (alternate.name, alternate.creator, alternate.base_state) == ("Print", "Tool", LayerBaseState.OFF)
    assert alternate.on == (hidden,)
    assert indirect.order == (notes, LayerOrder(None, (hidden,)))
    assert repr(alternate) == "LayerConfiguration(index=1, name='Print')"


def test_applying_a_configuration_changes_rendering_and_extraction() -> None:
    doc = pylopdf.open(_pdf())
    page = doc.pages[0]
    notes, hidden = doc.layers

    assert page.text().split() == ["Bravo"]
    doc.layers.configurations[1].apply()

    default = doc.layers.configurations[0]
    assert (default.name, default.base_state, default.on) == ("Print", LayerBaseState.OFF, (hidden,))
    assert default.order == (notes, LayerOrder("Group", (hidden,)))
    assert (notes.is_visible, hidden.is_visible) == (False, True)
    assert page.text().split() == ["Charlie"]

    doc.layers.configurations[2].apply()
    assert doc.layers.configurations[0].order == (notes, LayerOrder(None, (hidden,)))
    assert page.text().split() == ["Charlie"]

    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].text().split() == ["Charlie"]
    assert reopened.layers.configurations[0].name == "Indirect"


def test_applying_the_default_changes_nothing() -> None:
    doc = pylopdf.open(_pdf())

    doc.layers.configurations[0].apply()

    assert not doc.is_dirty


def test_entries_are_assignable_and_round_trip() -> None:
    doc = pylopdf.open(_pdf())
    notes, hidden = doc.layers
    default = doc.layers.configurations[0]

    default.name = "Écran"
    default.creator = "pylopdf"
    default.base_state = "OFF"
    default.on = [notes]
    default.off = ()
    default.locked = []
    default.radio_groups = []
    default.order = [LayerOrder("Top", (notes, LayerOrder(None, (hidden,)))), hidden]

    assert doc.pages[0].text().split() == ["Bravo"]
    reopened = pylopdf.open(doc.to_bytes())
    notes, hidden = reopened.layers
    default = reopened.layers.configurations[0]
    assert (default.name, default.creator, default.base_state) == ("Écran", "pylopdf", LayerBaseState.OFF)
    assert (default.on, default.off, default.locked, default.radio_groups) == ((notes,), (), (), ())
    assert default.order == (LayerOrder("Top", (notes, LayerOrder(None, (hidden,)))), hidden)
    config = reopened.catalog["OCProperties"]["D"]
    assert "OFF" not in config
    assert "RBGroups" not in config

    default.name = None
    default.order = []
    assert default.name is None
    assert "Order" not in reopened.catalog["OCProperties"]["D"]


def test_alternative_configurations_are_edited_in_place() -> None:
    doc = pylopdf.open(_pdf())
    notes, _ = doc.layers
    _, alternate, indirect = doc.layers.configurations

    alternate.off = [notes]
    indirect.name = "Renamed"

    assert [item.xref for item in doc.catalog["OCProperties"]["Configs"][0]["OFF"]] == [5]
    assert doc.objects[8]["Name"].text == "Renamed"
    assert doc.pages[0].text().split() == ["Bravo"]


def test_add_appends_a_configuration() -> None:
    doc = pylopdf.Document()
    doc.pages.new((200, 200))

    added = doc.layers.configurations.add("Print")
    assert (added.index, len(doc.layers.configurations)) == (1, 2)
    assert doc.layers.configurations[0].name is None

    layer = doc.layers.add("Notes")
    second = doc.layers.configurations.add("Off", creator="me", base_state=LayerBaseState.OFF, off=[layer])
    assert (second.index, second.creator, second.base_state, second.off) == (2, "me", LayerBaseState.OFF, (layer,))
    second.apply()
    assert not layer.is_visible

    reopened = pylopdf.open(doc.to_bytes())
    assert [config.name for config in reopened.layers.configurations] == ["Off", "Print", "Off"]


def test_documents_without_optional_content_have_no_configurations() -> None:
    doc = pylopdf.Document()

    assert list(doc.layers.configurations) == []
    with pytest.raises(StaleObjectError):
        _ = pylopdf.LayerConfiguration(doc, 0).name


def test_values_are_validated() -> None:
    doc = pylopdf.open(_pdf())
    notes, _ = doc.layers
    default = doc.layers.configurations[0]
    other = pylopdf.open(_pdf()).layers[0]

    with pytest.raises(TypeError):
        default.on = notes  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        default.on = ["Notes"]  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match="this document"):
        default.on = [other]
    with pytest.raises(ValueError, match="LayerBaseState"):
        default.base_state = "on"
    with pytest.raises(TypeError):
        default.order = [5]  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        LayerOrder(5)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        LayerOrder(None, ("x",))  # ty: ignore[invalid-argument-type]
    with pytest.raises(StaleObjectError):
        default.on = [pylopdf.Layer(doc, 99, 0)]
    assert repr(default) == "LayerConfiguration(index=0, name='Default')"
    doc.to_bytes(renumber=True)
    with pytest.raises(StaleObjectError):
        _ = default.name
    assert repr(default) == "<stale LayerConfiguration index=0>"


def test_order_nesting_and_cycles_are_bounded() -> None:
    deep = "[" * 34 + "5 0 R" + "]" * 34
    doc = pylopdf.open(_pdf(properties=f"<< /OCGs [5 0 R] /D << /Order {deep} >> >>"))
    with pytest.raises(LimitError) as caught:
        _ = doc.layers.configurations[0].order
    assert caught.value.code == "layer_count"

    cyclic = pylopdf.open(_pdf(properties="<< /OCGs [5 0 R] /D << /Order 9 0 R >> >>", extra={9: "[5 0 R 9 0 R]"}))
    assert cyclic.layers.configurations[0].order == (cyclic.layers[0], LayerOrder(None, (cyclic.layers[0],)))

    doc = pylopdf.open(_pdf())
    notes = doc.layers[0]
    tree: LayerOrder = LayerOrder(None, (notes,))
    for _ in range(32):
        tree = LayerOrder(None, (tree,))
    with pytest.raises(LimitError):
        doc.layers.configurations[0].order = [tree]
    with pytest.raises(LimitError) as caught:
        doc.layers.configurations[0].order = [notes] * 65_537
    assert caught.value.code == "layer_count"
    with pytest.raises(LimitError) as caught:
        doc.layers.configurations[0].order = [LayerOrder("x" * (1024 * 1024 + 1))]
    assert caught.value.code == "layer_input_size"


def test_malformed_configurations_read_as_absent() -> None:
    properties = "<< /OCGs [5 0 R] /D 99 0 R /Configs [7 0 R (text) << /ON 5 >>] >>"
    doc = pylopdf.open(_pdf(properties=properties))
    default, font, text, broken = doc.layers.configurations

    assert (default.name, default.order, default.base_state) == (None, (), LayerBaseState.ON)
    assert font.name is None
    with pytest.raises(StaleObjectError):
        _ = text.name
    assert broken.on == ()
