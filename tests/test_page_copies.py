"""Copies of pages within a document carry annotations, widgets, and structure of their own."""

from __future__ import annotations

from conftest import build_raw_pdf

import pylopdf
from pylopdf import CheckBox, Color, Destination, Note, RadioButton, Square, TextField


def _kids(element: pylopdf.Dictionary) -> list[pylopdf.Dictionary]:
    kids = element["K"]
    return list(kids) if isinstance(kids, pylopdf.Array) else [kids]


def test_annotations_become_objects_of_the_copy() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    square = page.annotations.add(Square((20, 20, 80, 80), color=Color(0, 0, 1), contents="Original"))
    note = page.annotations.add(Note((150, 40), contents="Note"))
    note.popup_rect = (150, 100, 250, 160)
    page.links.add((20, 200, 100, 220), Destination(0, point=(10, 10)))
    del square, note

    doc.pages.insert(1, doc.pages[0])

    original, copy = doc.pages
    assert len(copy.annotations) == len(original.annotations) == 2
    for old, new in zip(original.annotations, copy.annotations, strict=True):
        assert new.xref != old.xref
        assert new.obj["P"].xref == copy.xref
        assert (new.type, new.rect) == (old.type, old.rect)
    popup = copy.annotations[1].obj["Popup"]
    assert popup.xref != original.annotations[1].obj["Popup"].xref
    assert popup["Parent"].xref == copy.annotations[1].xref
    assert copy.links[0].target == original.links[0].target
    assert copy.links[0].xref != original.links[0].xref

    copy.annotations[0].contents = "Edited"
    assert original.annotations[0].contents == "Original"


def test_widgets_join_their_fields() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    page.widgets.add(TextField("name", (20, 20, 200, 44), value="Ada"))
    page.widgets.add(CheckBox("agree", (20, 60, 36, 76)))
    page.widgets.add(RadioButton("plan", (20, 90, 36, 106), on_state="Basic", selected=True))
    page.widgets.add(RadioButton("plan", (50, 90, 66, 106), on_state="Pro"))

    doc.pages.extend(doc.pages[0])

    assert sorted(doc.form) == ["agree", "name", "plan"]
    assert [len(doc.form[name].widgets) for name in ("name", "agree", "plan")] == [2, 2, 4]
    original, copy = doc.pages
    assert {widget.xref for widget in copy.widgets}.isdisjoint(widget.xref for widget in original.widgets)
    assert all(widget.obj["P"].xref == copy.xref for widget in copy.widgets)

    doc.form["name"].value = "Grace"
    doc.form["agree"].value = True
    reopened = pylopdf.open(doc.to_bytes())
    assert sorted(reopened.form) == ["agree", "name", "plan"]
    assert reopened.form["name"].value == "Grace"
    assert {widget.obj["AS"] for widget in reopened.form["agree"].widgets} == {"Yes"}
    assert [len(reopened.pages[index].widgets) for index in range(2)] == [4, 4]


def _tagged() -> bytes:
    content = (
        "/P <</MCID 0>> BDC BT /F1 12 Tf 20 150 Td (Text) Tj ET EMC "
        "/Figure <</MCID 1>> BDC 0 0 1 rg 20 20 40 40 re f EMC"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /StructParents 0 "
                "/Annots [11 0 R] /Resources << /Font << /F1 5 0 R >> >> >>"
            ),
            4: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            6: (
                "<< /Type /StructTreeRoot /K 7 0 R /ParentTree << /Nums [0 [9 0 R 10 0 R] 1 12 0 R] >> "
                "/ParentTreeNextKey 2 >>"
            ),
            7: "<< /Type /StructElem /S /Document /P 6 0 R /K [8 0 R] >>",
            8: "<< /Type /StructElem /S /Sect /P 7 0 R /K [9 0 R 10 0 R 12 0 R] >>",
            9: "<< /Type /StructElem /S /P /P 8 0 R /Pg 3 0 R /K 0 /ID (para) >>",
            10: "<< /Type /StructElem /S /Figure /P 8 0 R /Pg 3 0 R /K [1] /Alt (Square) >>",
            11: (
                "<< /Type /Annot /Subtype /Link /Rect [20 100 80 120] /StructParent 1 /P 3 0 R "
                "/A << /S /URI /URI (https://example.test/) >> >>"
            ),
            12: ("<< /Type /StructElem /S /Link /P 8 0 R /Pg 3 0 R /K [<< /Type /OBJR /Obj 11 0 R /Pg 3 0 R >>] >>"),
        }
    )


def _parent_tree(root: pylopdf.Dictionary) -> dict[int, list[pylopdf.Dictionary]]:
    """Return the parent tree as keys to lists of elements; a lone element reads as a list of one."""
    nums = list(root["ParentTree"]["Nums"])
    return {
        key: list(value) if isinstance(value, pylopdf.Array) else [value]
        for key, value in zip(nums[::2], nums[1::2], strict=True)
    }


def test_tagged_copies_get_elements_and_keys_of_their_own() -> None:
    doc = pylopdf.open(_tagged())

    doc.pages.extend(doc.pages[0])

    original, copy = doc.pages
    root = doc.catalog["StructTreeRoot"]
    (document,) = _kids(root)
    sections = _kids(document)
    assert [str(section["S"]) for section in sections] == ["Sect", "Sect"]
    copied = sections[1]
    assert copied.xref != sections[0].xref
    assert [str(kid["S"]) for kid in _kids(copied)] == ["P", "Figure", "Link"]
    paragraph, figure, link = _kids(copied)
    assert (paragraph["Pg"].xref, figure["Pg"].xref) == (copy.xref, copy.xref)
    assert "ID" not in paragraph
    assert figure["Alt"].text == "Square"
    assert link["K"][0]["Obj"].xref == copy.links[0].xref

    tree = _parent_tree(root)
    page_key = copy.obj["StructParents"]
    assert page_key not in (0, 1)
    assert [element.xref for element in tree[page_key]] == [paragraph.xref, figure.xref]
    link_key = copy.links[0].obj["StructParent"]
    assert [element.xref for element in tree[link_key]] == [link.xref]
    assert root["ParentTreeNextKey"] == max(tree) + 1
    assert original.obj["StructParents"] == 0
    assert copy.text().split() == ["Text"]

    reopened = pylopdf.open(doc.to_bytes())
    tree = _parent_tree(reopened.catalog["StructTreeRoot"])
    for page in reopened.pages:
        for element in tree[page.obj["StructParents"]]:
            assert element["Pg"].xref == page.xref


def test_untagged_copies_keep_no_structure() -> None:
    doc = pylopdf.Document()
    doc.pages.new((200, 200))

    doc.pages.insert(0, doc.pages[0])

    assert "StructTreeRoot" not in doc.catalog
    assert "StructParents" not in doc.pages[0].obj
