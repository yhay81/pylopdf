"""Optional-content membership: ``LayerMembership`` and the ``layer`` properties of handles."""

from __future__ import annotations

from typing import cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Layer, LayerExpression, LayerMembership, StaleObjectError, VisibilityPolicy

_CONTENT = (
    "/OC /M1 BDC BT /F1 12 Tf 20 170 Td (Both) Tj ET EMC "
    "/OC /L1 BDC BT /F1 12 Tf 20 150 Td (Notes) Tj ET EMC "
    "q 50 0 0 50 100 100 cm /Im1 Do Q"
)


def _pdf(*, image_oc: str = "", annots: str = "[10 0 R 11 0 R]", extra: dict[int, bytes | str] | None = None) -> bytes:
    objects: dict[int, bytes | str] = {
        1: ("<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [5 0 R 6 0 R] /D << /OFF [6 0 R] >> >> >>"),
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Annots {annots} "
            "/Resources << /Font << /F1 7 0 R >> /XObject << /Im1 8 0 R >> "
            "/Properties << /L1 5 0 R /M1 12 0 R /X1 << /MCID 3 >> /M2 9 0 R "
            "/M3 << /Type /OCMD /OCGs [6 0 R] >> >> >> >>"
        ),
        4: f"<< /Length {len(_CONTENT)} >>\nstream\n{_CONTENT}\nendstream",
        5: "<< /Type /OCG /Name (Notes) >>",
        6: "<< /Type /OCG /Name (Hidden) >>",
        7: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        8: (
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8 "
            + image_oc.encode()
            + b" /Length 3 >>\nstream\n\xff\x00\x00\nendstream"
        ),
        9: "<< /Type /OCMD /OCGs 6 0 R /VE [/Not 6 0 R] >>",
        10: "<< /Type /Annot /Subtype /Square /Rect [10 10 40 40] /OC 5 0 R >>",
        11: "<< /Type /Annot /Subtype /Link /Rect [50 10 80 40] /A << /S /URI /URI (https://example.com) >> >>",
        12: "<< /Type /OCMD /OCGs [5 0 R 6 0 R] /P /AllOn >>",
    }
    objects.update(extra or {})
    return build_raw_pdf(objects)


def _red(page: pylopdf.Page) -> bool:
    """Return whether the image shows at (125, 75)."""
    rows = cast("list[list[list[int]]]", memoryview(page.pixmap()).tolist())
    return rows[75][125] == [255, 0, 0]


def test_page_resources_name_groups_and_memberships() -> None:
    doc = pylopdf.open(_pdf())
    notes, hidden = doc.layers

    layers = doc.pages[0].resources.layers

    assert list(layers) == ["L1", "M1", "M2", "M3"]
    assert layers["L1"] == notes
    assert layers["M1"] == LayerMembership((notes, hidden), policy=VisibilityPolicy.ALL_ON)
    assert layers["M2"] == LayerMembership((hidden,), expression=LayerExpression("not", (hidden,)))
    assert layers["M3"] == LayerMembership((hidden,))


def test_membership_policies_decide_visibility_in_extraction() -> None:
    doc = pylopdf.open(_pdf())
    page = doc.pages[0]

    assert page.text().split() == ["Notes"]
    doc.layers[1].is_visible = True
    assert page.text().split() == ["Both", "Notes"]


def test_an_image_layer_changes_rendering() -> None:
    doc = pylopdf.open(_pdf())
    image = doc.images[8]
    notes, hidden = doc.layers

    assert image.layer is None
    assert _red(doc.pages[0])

    image.layer = hidden
    assert image.layer == hidden
    assert not _red(doc.pages[0])

    image.layer = LayerMembership((notes, hidden), policy=VisibilityPolicy.ANY_ON)
    assert _red(doc.pages[0])
    membership = doc.objects[8]["OC"]
    assert (membership["Type"], [item.xref for item in membership["OCGs"]]) == ("OCMD", [5, 6])
    assert "P" not in membership

    del image.layer
    assert "OC" not in doc.objects[8]
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.images[8].layer is None


def test_reading_an_image_layer_from_a_direct_membership() -> None:
    doc = pylopdf.open(_pdf(image_oc="/OC << /OCGs [6 0 R] /P /AllOff >>"))

    assert doc.images[8].layer == LayerMembership((doc.layers[1],), policy=VisibilityPolicy.ALL_OFF)
    assert _red(doc.pages[0])


def test_annotation_and_link_layers_are_read_and_written() -> None:
    doc = pylopdf.open(_pdf())
    notes, hidden = doc.layers
    page = doc.pages[0]
    square = page.annotations[0]
    link = page.links[0]

    assert square.layer == notes
    assert link.layer is None

    expression = LayerExpression("and", (notes, LayerExpression("not", (hidden,))))
    link.layer = LayerMembership((), expression=expression)
    square.layer = hidden

    reopened = pylopdf.open(doc.to_bytes())
    page = reopened.pages[0]
    notes, hidden = reopened.layers
    assert page.annotations[0].layer == hidden
    assert page.links[0].layer == LayerMembership(
        (), expression=LayerExpression("and", (notes, LayerExpression("not", (hidden,))))
    )
    del page.annotations[0].layer
    assert page.annotations[0].layer is None


def test_direct_annotations_become_objects_when_their_layer_changes() -> None:
    annots = "[<< /Type /Annot /Subtype /Square /Rect [10 10 40 40] >>]"
    doc = pylopdf.open(_pdf(annots=annots))
    square = doc.pages[0].annotations[0]

    assert (square.xref, square.layer) == (0, None)
    square.layer = doc.layers[0]

    assert square.xref != 0
    assert square.layer == doc.layers[0]


def test_widget_layers_are_read_and_written() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((200, 200))
    widget = page.widgets.add(pylopdf.TextField("name", (10, 10, 100, 30)))
    layer = doc.layers.add("Form")

    widget.layer = layer

    assert pylopdf.open(doc.to_bytes()).pages[0].widgets[0].layer is not None
    assert widget.layer == layer


def test_malformed_expressions_read_as_none() -> None:
    extra: dict[int, bytes | str] = {9: "<< /Type /OCMD /OCGs [6 0 R] /VE [/Not 5 0 R 6 0 R] >>"}
    doc = pylopdf.open(_pdf(extra=extra))

    assert doc.pages[0].resources.layers["M2"] == LayerMembership((doc.layers[1],))


def test_values_are_validated() -> None:
    doc = pylopdf.open(_pdf())
    notes = doc.layers[0]
    other = pylopdf.open(_pdf()).layers[0]
    image = doc.images[8]

    with pytest.raises(TypeError):
        image.layer = "Notes"  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match="this document"):
        image.layer = other
    with pytest.raises(StaleObjectError):
        image.layer = Layer(doc, 99, 0)
    with pytest.raises(TypeError):
        LayerMembership(notes)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        LayerMembership(("x",))  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="VisibilityPolicy"):
        LayerMembership((), policy="all")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="operator"):
        LayerExpression("xor", (notes,))  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="one operand"):
        LayerExpression("not", (notes, notes))
    with pytest.raises(ValueError, match="operands"):
        LayerExpression("and", ())
    with pytest.raises(TypeError):
        LayerExpression("and", ("x",))  # ty: ignore[invalid-argument-type]
    assert image.layer is None
    assert not doc.is_dirty


def test_deep_expressions_are_bounded() -> None:
    deep = "[/Not " * 34 + "5 0 R" + "]" * 34
    extra: dict[int, bytes | str] = {9: f"<< /Type /OCMD /VE {deep} >>"}
    doc = pylopdf.open(_pdf(extra=extra))

    with pytest.raises(pylopdf.LimitError) as caught:
        _ = doc.pages[0].resources.layers
    assert caught.value.code == "layer_count"
