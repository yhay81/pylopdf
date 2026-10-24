"""Page imports carry the structure tree of the pages they import."""

from __future__ import annotations

from conftest import build_raw_pdf

import pylopdf


def _content(text: str) -> str:
    return f"/Para <</MCID 0>> BDC BT /F1 12 Tf 20 150 Td ({text}) Tj ET EMC"


def _tagged(
    texts: list[str],
    *,
    role_map: str = "<< /Para /P >>",
    class_map: str = "<< /Bold << /O /Layout >> >>",
    identifier: str = "(A)",
) -> bytes:
    """Return a tagged document whose page i shows texts[i] in element i under a Document element."""
    count = len(texts)
    pages = list(range(3, 3 + count))
    contents = list(range(3 + count, 3 + 2 * count))
    font = 3 + 2 * count
    root = font + 1
    document = root + 1
    elements = list(range(document + 1, document + 1 + count))
    objects: dict[int, bytes | str] = {
        1: f"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot {root} 0 R /MarkInfo << /Marked true >> >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{page} 0 R' for page in pages)}] /Count {count} >>",
        font: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, text in enumerate(texts):
        stream = _content(text)
        objects[pages[index]] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents {contents[index]} 0 R "
            f"/StructParents {index} /Resources << /Font << /F1 {font} 0 R >> >> >>"
        )
        objects[contents[index]] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
        extra = f" /ID {identifier} /C /Bold" if index == count - 1 else ""
        objects[elements[index]] = (
            f"<< /Type /StructElem /S /Para /P {document} 0 R /Pg {pages[index]} 0 R /K 0{extra} >>"
        )
    nums = " ".join(f"{index} [{element} 0 R]" for index, element in enumerate(elements))
    objects[root] = (
        f"<< /Type /StructTreeRoot /K {document} 0 R /ParentTree << /Nums [{nums}] >> "
        f"/ParentTreeNextKey {count} /RoleMap {role_map} /ClassMap {class_map} "
        f"/IDTree << /Names [{identifier} {elements[-1]} 0 R] >> >>"
    )
    objects[document] = (
        f"<< /Type /StructElem /S /Document /P {root} 0 R /K [{' '.join(f'{element} 0 R' for element in elements)}] >>"
    )
    return build_raw_pdf(objects)


def _kids(element: pylopdf.Dictionary) -> list[pylopdf.Dictionary]:
    """Return an element's or the root's kids; a lone kid reads as itself."""
    kids = element["K"]
    return list(kids) if isinstance(kids, pylopdf.Array) else [kids]


def _parent_tree(root: pylopdf.Dictionary) -> dict[int, list[int]]:
    entries: dict[int, list[int]] = {}
    nodes = [root["ParentTree"]]
    while nodes:
        node = nodes.pop()
        nums = list(node.get("Nums") or [])
        for key, value in zip(nums[::2], nums[1::2], strict=True):
            entries[key] = [element.xref for element in value]
        nodes.extend(node.get("Kids") or [])
    return entries


def _check(doc: pylopdf.Document) -> None:
    """Every page's key maps its MCIDs to elements on that page."""
    root = doc.catalog["StructTreeRoot"]
    tree = _parent_tree(root)
    keys = [page.obj.get("StructParents") for page in doc.pages]
    assert len(set(keys)) == len(keys)
    for page in doc.pages:
        key = page.obj["StructParents"]
        for owner in tree[key]:
            element = doc.objects[owner]
            assert element["Pg"].xref == page.xref
    assert root["ParentTreeNextKey"] > max(tree)


def test_imported_pages_bring_their_elements_into_the_document_element() -> None:
    source = pylopdf.open(_tagged(["Skipped", "Imported"]))
    target = pylopdf.open(_tagged(["Own"], role_map="<< /Para /H1 >>", class_map="<< /Bold << /O /Layout /W 1 >> >>"))

    target.pages.extend(source.pages[1])

    root = target.catalog["StructTreeRoot"]
    (document,) = _kids(root)
    kids = _kids(document)
    assert len(kids) == 2
    own, imported = kids
    assert imported["P"].xref == document.xref
    assert imported["Pg"].xref == target.pages[1].xref
    # The receiving tree maps /Para to /H1, so the imported element takes the source's standard type.
    assert (own["S"], imported["S"]) == ("Para", "P")
    assert imported["ID"].text == "A+1"
    assert imported["C"] == "Bold_1"
    assert sorted(root["ClassMap"]) == ["Bold", "Bold_1"]
    names = list(root["IDTree"]["Names"])
    assert [name.text for name in names[::2]] == ["A", "A+1"]
    assert names[3].xref == imported.xref
    assert target.pages[1].text().split() == ["Imported"]
    _check(target)
    _check(pylopdf.open(target.to_bytes()))


def test_skipped_pages_leave_their_elements_behind() -> None:
    source = pylopdf.open(_tagged(["First", "Second", "Third"]))
    target = pylopdf.open(_tagged(["Own"]))

    target.pages.extend(source.pages[0:2])

    (document,) = _kids(target.catalog["StructTreeRoot"])
    assert len(_kids(document)) == 3
    saved = pylopdf.open(target.to_bytes())
    texts = [page.text().strip() for page in saved.pages]
    assert texts == ["Own", "First", "Second"]
    _check(saved)


def test_an_untagged_document_receives_the_whole_tree() -> None:
    source = pylopdf.open(_tagged(["One", "Two"]))
    target = pylopdf.Document()
    target.pages.new((200, 200))

    target.pages.extend(source.pages[1])

    root = target.catalog["StructTreeRoot"]
    (document,) = _kids(root)
    assert document["S"] == "Document"
    assert document["P"].xref == root.xref
    assert [kid["Pg"].xref for kid in _kids(document)] == [target.pages[1].xref]
    assert root["RoleMap"]["Para"] == "P"
    tree = _parent_tree(root)
    assert tree[target.pages[1].obj["StructParents"]] == [_kids(document)[0].xref]


def test_untagged_sources_leave_the_tree_alone() -> None:
    target = pylopdf.open(_tagged(["Own"]))
    source = pylopdf.Document()
    source.pages.new((200, 200))
    before = _parent_tree(target.catalog["StructTreeRoot"])

    target.pages.extend(source.pages)

    assert _parent_tree(target.catalog["StructTreeRoot"]) == before
    assert len(_kids(_kids(target.catalog["StructTreeRoot"])[0])) == 1


def test_keys_move_past_the_receiving_tree() -> None:
    source = pylopdf.open(_tagged(["A", "B"]))
    target = pylopdf.open(_tagged(["X", "Y", "Z"]))

    target.pages.extend(source.pages)

    keys = [page.obj["StructParents"] for page in target.pages]
    assert keys == [0, 1, 2, 3, 4]
    assert target.catalog["StructTreeRoot"]["ParentTreeNextKey"] == 5
    _check(target)
