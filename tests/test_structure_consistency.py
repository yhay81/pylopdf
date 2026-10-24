"""Every page operation on tagged real-world documents leaves a consistent structure tree.

The walk below checks what ISO 32000-2 §14.7 ties together: each page's
`/StructParents` key maps its MCIDs to elements on that page that hold those
MCIDs, each annotation's `/StructParent` maps to an element with an OBJR to
it, every element's `/P` is the element or root that lists it, and
`/ParentTreeNextKey` exceeds every key. It stands in for the consistency
harness of #382 until that harness runs in CI.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
TAGGED = ["f1040.pdf", "mhlw-doc.pdf"]


def _as_list(value: object) -> list[object]:
    if isinstance(value, pylopdf.Array):
        return list(value)
    return [] if value is None else [value]


def _parent_tree(root: pylopdf.Dictionary) -> dict[int, object]:
    entries: dict[int, object] = {}
    nodes: list[pylopdf.Dictionary] = [root["ParentTree"]]
    while nodes:
        node = nodes.pop()
        nums = _as_list(node.get("Nums"))
        for key, value in zip(nums[::2], nums[1::2], strict=True):
            assert isinstance(key, int)
            entries[key] = value
        nodes.extend(kid for kid in _as_list(node.get("Kids")) if isinstance(kid, pylopdf.Dictionary))
    return entries


def _check_parents(root: pylopdf.Dictionary) -> None:
    """Every element's /P is the element or root that lists it."""
    stack: list[tuple[pylopdf.Dictionary, pylopdf.Dictionary]] = [
        (kid, root) for kid in _as_list(root.get("K")) if isinstance(kid, pylopdf.Dictionary)
    ]
    seen: set[int] = set()
    while stack:
        element, parent = stack.pop()
        if element.get("Type") in {"MCR", "OBJR"} or "S" not in element:
            continue
        if element.xref is not None:
            if element.xref in seen:
                continue
            seen.add(element.xref)
            assert element["P"].xref == parent.xref
        stack.extend((kid, element) for kid in _as_list(element.get("K")) if isinstance(kid, pylopdf.Dictionary))


def _check_page(page: pylopdf.Page, tree: dict[int, object]) -> list[int]:
    """Check a page's and its annotations' keys and return them."""
    keys: list[int] = []
    key = page.obj.get("StructParents")
    if key is not None:
        keys.append(key)
        for mcid, owner in enumerate(_as_list(tree.get(key))):
            if not isinstance(owner, pylopdf.Dictionary):
                continue
            assert owner["Pg"].xref == page.xref, (page.index, mcid)
            kids = _as_list(owner.get("K"))
            assert mcid in kids or any(
                isinstance(kid, pylopdf.Dictionary) and kid.get("MCID") == mcid for kid in kids
            ), (page.index, mcid)
    for annotation in _as_list(page.obj.get("Annots")):
        if not isinstance(annotation, pylopdf.Dictionary) or "StructParent" not in annotation:
            continue
        keys.append(annotation["StructParent"])
        owner = tree.get(annotation["StructParent"])
        if isinstance(owner, pylopdf.Dictionary):
            references = [
                kid
                for kid in _as_list(owner.get("K"))
                if isinstance(kid, pylopdf.Dictionary) and kid.get("Type") == "OBJR"
            ]
            assert any(reference["Obj"].xref == annotation.xref for reference in references)
    return keys


def _check(doc: pylopdf.Document) -> None:
    root = doc.catalog["StructTreeRoot"]
    assert isinstance(root, pylopdf.Dictionary)
    tree = _parent_tree(root)
    assert root.get("ParentTreeNextKey", 0) > max(tree, default=-1)
    _check_parents(root)
    keys = [key for page in doc.pages for key in _check_page(page, tree)]
    assert len(keys) == len(set(keys)), "two pages or annotations share a parent-tree key"


def _open(name: str) -> pylopdf.Document:
    return pylopdf.open(REAL_WORLD / name)


Operation = Callable[[pylopdf.Document], None]

OPERATIONS: dict[str, Operation] = {
    "delete": lambda doc: doc.pages.__delitem__(0),
    "select": lambda doc: doc.pages.select([len(doc.pages) - 1, 0]),
    "move": lambda doc: doc.pages.move(0, len(doc.pages) - 1),
    "copy": lambda doc: doc.pages.insert(0, doc.pages[len(doc.pages) - 1]),
    "copy_all": lambda doc: doc.pages.extend(list(doc.pages)),
    "import_self": lambda doc: doc.pages.extend(pylopdf.open(doc.to_bytes()).pages),
    "import_other": lambda doc: doc.pages.extend(_open(TAGGED[1] if "f1040" in str(doc.path) else TAGGED[0]).pages),
    "import_one": lambda doc: doc.pages.insert(1, _open(TAGGED[0]).pages[1]),
}


@pytest.mark.parametrize("name", TAGGED)
@pytest.mark.parametrize("operation", list(OPERATIONS))
def test_page_operations_keep_the_structure_tree_consistent(name: str, operation: str) -> None:
    doc = _open(name)
    _check(doc)

    OPERATIONS[operation](doc)

    _check(doc)
    _check(pylopdf.open(doc.to_bytes()))


@pytest.mark.parametrize("name", TAGGED)
def test_tagging_drawing_keeps_the_structure_tree_consistent(name: str) -> None:
    doc = _open(name)

    with doc.pages[0].draw() as canvas, canvas.tag(pylopdf.TagRole.PARAGRAPH):
        canvas.text("Added", (36, 36))

    _check(doc)
    _check(pylopdf.open(doc.to_bytes()))
