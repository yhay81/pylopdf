"""Optional content groups and membership dictionaries with pymupdf's meaning (M5)."""

from __future__ import annotations

import pytest
from conftest import build_raw_pdf

import pylopdf

#: Text outside optional content, text in the hidden group, and a blue Form
#: XObject in the visible group on the right half of the page.
_CONTENT = (
    b"BT /F1 12 Tf 10 80 Td (Shown) Tj ET "
    b"/OC /L1 BDC BT /F1 12 Tf 10 60 Td (Secret) Tj ET EMC "
    b"q 1 0 0 1 100 0 cm /X Do Q"
)
_FORM = b"0 0 1 rg 0 0 100 100 re f"


def _stream(data: bytes, entries: str = "") -> bytes:
    return f"<< {entries} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def _layered_pdf(default_config: str = "/OFF [6 0 R] /Order [5 0 R 6 0 R]") -> bytes:
    return build_raw_pdf(
        {
            1: f"<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [5 0 R 6 0 R] /D << {default_config} >> >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Contents 9 0 R /Resources << "
                "/Font << /F1 4 0 R >> /Properties << /L0 5 0 R /L1 6 0 R /M 7 0 R /Other 10 0 R >> "
                "/XObject << /X 8 0 R >> >> >>"
            ),
            4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            5: "<< /Type /OCG /Name (Visible) /Intent /View /Usage << /CreatorInfo << /Subtype /Artwork >> >> >>",
            6: "<< /Type /OCG /Name (Hidden) >>",
            7: "<< /Type /OCMD /OCGs [5 0 R 6 0 R] /P /AllOn >>",
            8: _stream(_FORM, "/Type /XObject /Subtype /Form /BBox [0 0 100 100] /OC 5 0 R"),
            9: _stream(_CONTENT),
            10: "<< /Lang (en) >>",
        }
    )


def _form_is_drawn(page: pylopdf.Page) -> bool:
    pixmap = page.get_pixmap()
    offset = (50 * pixmap.width + 150) * pixmap.n
    return pixmap.samples[offset : offset + 3] == b"\x00\x00\xff"


def test_get_ocgs_reads_the_default_configuration() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    assert doc.get_ocgs() == {
        5: {"name": "Visible", "intent": ["View"], "on": True, "usage": "Artwork"},
        6: {"name": "Hidden", "intent": [], "on": False, "usage": None},
    }
    assert pylopdf.Document().get_ocgs() == {}


@pytest.mark.parametrize(
    ("config", "states"),
    [
        ("/BaseState /OFF", {5: False, 6: False}),
        ("/BaseState /OFF /ON [6 0 R]", {5: False, 6: True}),
        ("/BaseState /Unchanged /OFF [5 0 R]", {5: False, 6: True}),
        ("/ON [5 0 R 6 0 R] /OFF [6 0 R]", {5: True, 6: False}),
        ("", {5: True, 6: True}),
    ],
)
def test_on_follows_base_state_on_and_off(config: str, states: dict[int, bool]) -> None:
    doc = pylopdf.open(stream=_layered_pdf(config))
    assert {xref: info["on"] for xref, info in doc.get_ocgs().items()} == states


def test_group_state_controls_extraction() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    page = doc[0]
    assert "Shown" in page.get_text()
    assert "Secret" not in page.get_text()
    doc.catalog["OCProperties"]["D"]["OFF"] = []
    assert doc.get_ocgs()[6]["on"] is True
    assert "Secret" in doc[0].get_text()


def test_set_oc_attaches_and_detaches_xobjects() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    assert doc.get_oc(8) == 5
    assert _form_is_drawn(doc[0])
    doc.set_oc(8, 6)
    assert doc.get_oc(8) == 6
    assert not _form_is_drawn(doc[0])
    doc.set_oc(8, 7)
    assert doc.get_oc(8) == 7
    assert not _form_is_drawn(doc[0])
    doc.set_oc(8, 0)
    assert doc.get_oc(8) == 0
    assert "OC" not in doc.objects[8]
    assert _form_is_drawn(doc[0])
    doc.set_oc(8, 0)
    assert doc.is_dirty


def test_get_oc_reads_only_references() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    doc.objects[8]["OC"] = {"Type": pylopdf.Name("OCG"), "Name": "inline"}
    assert doc.get_oc(8) == 0


@pytest.mark.parametrize("xref", [5, 9, 999, -1])
def test_get_and_set_oc_refuse_other_objects(xref: int) -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    with pytest.raises(ValueError, match="bad object type"):
        doc.get_oc(xref)
    with pytest.raises(ValueError, match="bad object type"):
        doc.set_oc(xref, 5)


@pytest.mark.parametrize("oc", [4, 8, 10, 999, -2])
def test_set_oc_refuses_other_targets(oc: int) -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    with pytest.raises(ValueError, match=f"bad object type at xref {oc}"):
        doc.set_oc(8, oc)
    assert doc.get_oc(8) == 5


def test_get_ocmd_describes_membership_dictionaries() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    assert doc.get_ocmd(7) == {"xref": 7, "ocgs": [5, 6], "policy": "AllOn", "ve": None}
    doc.objects[7] = {
        "Type": pylopdf.Name("OCMD"),
        "OCGs": doc.objects[6],
        "VE": [pylopdf.Name("Or"), doc.objects[5], [pylopdf.Name("Not"), doc.objects[6]]],
    }
    assert doc.get_ocmd(7) == {"xref": 7, "ocgs": [6], "policy": None, "ve": ["or", 5, ["not", 6]]}
    with pytest.raises(ValueError, match="bad object type"):
        doc.get_ocmd(5)
    with pytest.raises(ValueError, match="bad xref"):
        doc.get_ocmd(999)


def test_set_ocmd_matches_pymupdf() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    created = doc.set_ocmd(ocgs=[5, 6], policy="anyoff")
    assert doc.get_ocmd(created) == {"xref": created, "ocgs": [5, 6], "policy": "AnyOff", "ve": None}
    expression = doc.set_ocmd(ve=["AND", 5, ["not", 6]])
    assert doc.get_ocmd(expression)["ve"] == ["and", 5, ["not", 6]]
    written = doc.objects[expression]["VE"]
    assert (written[0], written[1], written[2][0], written[2][1]) == ("And", doc.objects[5], "Not", doc.objects[6])
    empty = doc.set_ocmd()
    assert doc.get_ocmd(empty) == {"xref": empty, "ocgs": None, "policy": None, "ve": None}
    assert doc.set_ocmd(7, ocgs=[5], policy="AllOff") == 7
    assert doc.get_ocmd(7) == {"xref": 7, "ocgs": [5], "policy": "AllOff", "ve": None}


def test_set_ocmd_policy_controls_rendering() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    any_on = doc.set_ocmd(ocgs=[5, 6], policy="AnyOn")
    doc.set_oc(8, any_on)
    assert _form_is_drawn(doc[0])
    doc.set_ocmd(any_on, ocgs=[5, 6], policy="AllOn")
    assert not _form_is_drawn(doc[0])


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"ocgs": [5, 999]}, "bad OCGs: {999}"),
        ({"policy": "most"}, "bad policy: most"),
        ({"ve": ["and"]}, "bad 've' format"),
        ({"ve": ["xor", 5, 6]}, "bad operand: xor"),
        ({"ve": ["not", 5, 6]}, "bad 've' format"),
        ({"ve": ["or", 5, 999]}, "bad OCG 999"),
        ({"ve": ["or", 5, ["and"]]}, "bad 've' format"),
        ({"xref": 5}, "bad xref or not an OCMD"),
        ({"xref": 999}, "bad xref or not an OCMD"),
    ],
)
def test_set_ocmd_refuses_bad_input(arguments: dict[str, object], message: str) -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    count = len(doc.objects)
    with pytest.raises(ValueError, match=message.replace("{", r"\{").replace("}", r"\}")):
        doc.set_ocmd(**arguments)  # ty: ignore[invalid-argument-type]
    assert len(doc.objects) == count
    assert not doc.is_dirty


def test_add_ocg_creates_pymupdfs_structure() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    first = doc.add_ocg("A")
    second = doc.add_ocg("B", on=0)
    third = doc.add_ocg("C", intent=["View", "Design"], usage="Technical")
    assert doc.get_ocgs() == {
        first: {"name": "A", "intent": ["View"], "on": True, "usage": "Artwork"},
        second: {"name": "B", "intent": ["View"], "on": False, "usage": "Artwork"},
        third: {"name": "C", "intent": ["View", "Design"], "on": True, "usage": "Technical"},
    }
    groups = [doc.objects[xref] for xref in (first, second, third)]
    properties = doc.catalog["OCProperties"]
    assert list(properties["OCGs"]) == groups
    assert list(properties["D"]["Order"]) == groups
    assert list(properties["D"]["ON"]) == [groups[0], groups[2]]
    assert list(properties["D"]["OFF"]) == [groups[1]]
    assert list(properties["D"]["RBGroups"]) == []
    assert dict(groups[0]["Usage"]["CreatorInfo"]) == {"Creator": b"pylopdf", "Subtype": "Artwork"}
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.get_ocgs() == doc.get_ocgs()


def test_add_ocg_extends_existing_properties_and_configs() -> None:
    doc = pylopdf.open(stream=_layered_pdf("/OFF [6 0 R]"))
    doc.catalog["OCProperties"]["Configs"] = [{"Name": "Print"}]
    added = doc.add_ocg("Print only", config=0, on=False)
    properties = doc.catalog["OCProperties"]
    assert [group.xref for group in properties["OCGs"]] == [5, 6, added]
    assert "Order" not in properties["D"]
    assert [group.xref for group in properties["Configs"][0]["OFF"]] == [added]
    assert [group.xref for group in properties["Configs"][0]["Order"]] == [added]
    assert doc.get_ocgs()[added]["on"] is True
    with pytest.raises(ValueError, match="bad config number"):
        doc.add_ocg("Missing", config=1)
    assert len(doc.get_ocgs()) == 3


def test_added_groups_hide_xobjects() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    off = doc.add_ocg("Off", on=False)
    doc.set_oc(8, off)
    assert not _form_is_drawn(doc[0])


def test_page_get_oc_items_lists_groups_and_membership_dictionaries() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    assert doc[0].get_oc_items() == [("L0", 5, "ocg"), ("L1", 6, "ocg"), ("M", 7, "ocmd")]
    resources = doc[0].obj["Resources"]
    doc.objects[2]["Resources"] = resources
    del doc[0].obj["Resources"]
    assert doc[0].get_oc_items() == [("L0", 5, "ocg"), ("L1", 6, "ocg"), ("M", 7, "ocmd")]
    assert pylopdf.Document().new_page().get_oc_items() == []


def test_annot_get_oc() -> None:
    doc = pylopdf.open(stream=_layered_pdf())
    page = doc[0]
    annot = page.add_highlight_annot(pylopdf.Rect(0, 0, 20, 20))
    assert annot.get_oc() == 0
    doc.objects[annot.xref]["OC"] = doc.objects[5]
    assert annot.get_oc() == 5
    page.obj["Annots"].append(
        {"Type": pylopdf.Name("Annot"), "Subtype": pylopdf.Name("Square"), "Rect": [0, 0, 5, 5], "OC": doc.objects[7]}
    )
    direct = doc[0].annots()[-1]
    assert direct.xref == 0
    assert direct.get_oc() == 7
