"""``Canvas.tag`` on pages of open documents: maintaining the structure tree."""

from __future__ import annotations

from pathlib import Path

import pytest

import pylopdf
from pylopdf import Color, ListNumbering, TableHeaderScope, TagRole

RED = Color(1, 0, 0)


FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"


def _font() -> pylopdf.Font:
    return pylopdf.Font.from_file(NOTO_SANS_JP)


def _structure(doc: pylopdf.Document) -> pylopdf.Dictionary:
    root = doc.catalog["StructTreeRoot"]
    assert isinstance(root, pylopdf.Dictionary)
    return root


def _parent_tree(root: pylopdf.Dictionary) -> dict[int, list[int]]:
    """Return the parent tree as keys to the object numbers of their elements."""
    entries: dict[int, list[int]] = {}
    nodes = [root["ParentTree"]]
    while nodes:
        node = nodes.pop()
        nums = list(node.get("Nums") or [])
        for key, value in zip(nums[::2], nums[1::2], strict=True):
            entries[key] = [element.xref for element in value] if isinstance(value, pylopdf.Array) else [value.xref]
        nodes.extend(node.get("Kids") or [])
    return entries


def _check_consistency(doc: pylopdf.Document) -> None:
    """Every MCID that the parent tree maps names an element whose kids hold that MCID."""
    root = _structure(doc)
    for key, owners in _parent_tree(root).items():
        for mcid, owner in enumerate(owners):
            kids = doc.objects[owner]["K"]
            kids = list(kids) if isinstance(kids, pylopdf.Array) else [kids]
            if mcid in kids:
                # Page content names its MCIDs directly; the page holds the key.
                continue
            matching = [kid for kid in kids if isinstance(kid, pylopdf.Dictionary) and kid.get("MCID") == mcid]
            assert matching, (key, mcid, owner)
            assert matching[0]["Stm"]["StructParents"] == key


def _roles(kids: pylopdf.Array) -> list[str]:
    return [str(kid["S"]) for kid in kids if isinstance(kid, pylopdf.Dictionary) and "S" in kid]


def test_tags_on_an_untagged_document_create_its_structure_tree() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    with page.draw() as canvas:
        with canvas.tag(TagRole.HEADING_1):
            canvas.text("Title", (20, 40), size=18)
        with canvas.tag(TagRole.PARAGRAPH, lang="en-US"):
            canvas.text("Hello", (20, 80))
        with canvas.tag(TagRole.FIGURE, alt="A red square"):
            canvas.rect((20, 100, 60, 140), fill=RED)

    root = _structure(doc)
    assert _roles(root["K"]) == ["H1", "P", "Figure"]
    heading, paragraph, figure = root["K"]
    assert paragraph["Lang"].text == "en-US"
    assert figure["Alt"].text == "A red square"
    assert heading["P"].xref == root.xref
    assert heading["Pg"].xref == page.xref
    form = heading["K"][0]["Stm"]
    assert form["StructParents"] == 0
    assert root["ParentTreeNextKey"] == 1
    assert _parent_tree(root) == {0: [heading.xref, paragraph.xref, figure.xref]}
    content = form.read()
    assert b"/H1 <</MCID 0>> BDC" in content
    assert b"/Figure <</MCID 2>> BDC" in content
    assert page.text().split() == ["Title", "Hello"]
    _check_consistency(doc)

    reopened = pylopdf.open(doc.to_bytes())
    assert _roles(_structure(reopened)["K"]) == ["H1", "P", "Figure"]
    _check_consistency(reopened)


def test_kids_keep_the_order_of_drawing_and_nesting() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    with page.draw() as canvas, canvas.tag(TagRole.PARAGRAPH):
        canvas.text("Before", (20, 40))
        with canvas.tag(TagRole.SPAN, actual_text="Middle"):
            canvas.text("Middle", (20, 60))
        canvas.text("After", (20, 80))

    (paragraph,) = _structure(doc)["K"]
    kids = list(paragraph["K"])
    assert [kid.get("MCID") if kid.get("Type") == "MCR" else str(kid["S"]) for kid in kids] == [0, "Span", 2]
    assert kids[1]["P"].xref == paragraph.xref
    assert kids[1]["ActualText"].text == "Middle"
    _check_consistency(doc)


def test_lists_and_tables_carry_their_attributes() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    with page.draw() as canvas:
        with canvas.tag(TagRole.LIST, numbering=ListNumbering.DECIMAL), canvas.tag(TagRole.LIST_ITEM):
            with canvas.tag(TagRole.LABEL):
                canvas.text("1.", (20, 40))
            with canvas.tag(TagRole.LIST_BODY):
                canvas.text("First", (40, 40))
        with canvas.tag(TagRole.TABLE), canvas.tag(TagRole.TABLE_ROW):
            with canvas.tag(TagRole.TABLE_HEADER, scope=TableHeaderScope.ROW):
                canvas.text("Name", (20, 100))
            with canvas.tag(TagRole.TABLE_DATA):
                canvas.text("Ada", (100, 100))

    lists, table = _structure(doc)["K"]
    assert (lists["A"]["O"], lists["A"]["ListNumbering"]) == ("List", "Decimal")
    header = table["K"][0]["K"][0]
    assert (str(header["S"]), header["A"]["O"], header["A"]["Scope"]) == ("TH", "Table", "Row")
    _check_consistency(doc)


def test_tags_join_a_tagged_documents_document_element() -> None:
    builder = pylopdf.create(tagged=True, language="en")
    with builder.page((300, 300)) as canvas, canvas.tag(TagRole.PARAGRAPH):
        canvas.text("Existing", (20, 40), font=_font())
    doc = pylopdf.open(builder.to_bytes())
    root = _structure(doc)
    before = _parent_tree(root)
    (document,) = root["K"]
    assert document["S"] == "Document"
    existing = len(document["K"])

    with doc.pages[0].draw() as canvas:
        with canvas.tag(TagRole.PARAGRAPH):
            canvas.text("Added", (20, 80), font=_font())
        canvas.rect((10, 10, 20, 20), fill=RED)

    root = _structure(doc)
    (document,) = root["K"]
    assert len(document["K"]) == existing + 1
    added = document["K"][-1]
    assert (str(added["S"]), added["P"].xref) == ("P", document.xref)
    tree = _parent_tree(root)
    new_keys = set(tree) - set(before)
    assert len(new_keys) == 1
    assert root["ParentTreeNextKey"] == max(tree) + 1
    form = added["K"][0]["Stm"]
    assert form["StructParents"] == next(iter(new_keys))
    content = form.read()
    # Drawing outside every tag scope of a tagged document is an artifact.
    assert b"/Artifact BMC" in content
    assert doc.pages[0].text().split() == ["Existing", "Added"]
    _check_consistency(doc)
    _check_consistency(pylopdf.open(doc.to_bytes()))


def test_untagged_documents_get_no_artifacts() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    with page.draw() as canvas:
        canvas.text("Plain", (20, 40))

    assert "StructTreeRoot" not in doc.catalog
    form = next(iter(page.obj["Resources"]["XObject"].values()))
    assert b"BMC" not in form.read()
    assert b"BDC" not in form.read()


def test_artifact_scopes_mark_without_elements() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    with page.draw() as canvas:
        with canvas.tag(TagRole.ARTIFACT):
            canvas.text("Page 1", (20, 280))
        with canvas.tag(TagRole.PARAGRAPH):
            canvas.text("Body", (20, 40))

    root = _structure(doc)
    assert _roles(root["K"]) == ["P"]
    form = root["K"][0]["K"][0]["Stm"]
    content = form.read()
    assert content.index(b"/Artifact BMC") < content.index(b"/P <</MCID 0>> BDC")


def test_opacity_inside_a_tag_is_drawn_inside_its_content() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    with page.draw() as canvas, canvas.tag(TagRole.FIGURE, alt="Faded"), canvas.opacity(0.5):
        canvas.rect((20, 20, 80, 80), fill=RED)

    (figure,) = _structure(doc)["K"]
    form = figure["K"][0]["Stm"]
    content = form.read()
    start = content.index(b"/Figure <</MCID 0>> BDC")
    assert start < content.index(b"Do", start) < content.index(b"EMC", start)
    _check_consistency(doc)


def test_tags_inside_opacity_scopes_are_refused() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    canvas = page.draw()
    with canvas.opacity(0.5), canvas.tag(TagRole.PARAGRAPH):
        canvas.text("x", (20, 40))
    with pytest.raises(pylopdf.PdfError, match="opacity"):
        canvas.__exit__(None, None, None)
    assert "StructTreeRoot" not in doc.catalog


def test_a_lone_document_element_reference_takes_the_new_elements() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    document = doc.objects.add({"Type": pylopdf.Name("StructElem"), "S": pylopdf.Name("Document"), "K": []})
    root = doc.objects.add(
        {
            "Type": pylopdf.Name("StructTreeRoot"),
            "K": doc.objects[document],
            "ParentTree": {"Nums": []},
        }
    )
    doc.objects[document]["P"] = doc.objects[root]
    doc.catalog["StructTreeRoot"] = doc.objects[root]

    with page.draw() as canvas, canvas.tag(TagRole.PARAGRAPH):
        canvas.text("Inside", (20, 40))

    root_dict = _structure(doc)
    assert root_dict["K"].xref == document
    (paragraph,) = doc.objects[document]["K"]
    assert (str(paragraph["S"]), paragraph["P"].xref) == ("P", document)
    _check_consistency(doc)
