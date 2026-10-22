"""Remove the structure content of pages that `delete_pages` and `select` drop."""

from __future__ import annotations

import re
from collections.abc import Mapping

import pytest
from conftest import build_raw_pdf

import pylopdf


def _content(text: str, mcids: int) -> str:
    marked = "".join(
        f"/P <</MCID {mcid}>> BDC BT /F1 12 Tf 72 {700 - 20 * mcid} Td ({text} {mcid}) Tj ET EMC\n"
        for mcid in range(mcids)
    )
    return f"<< /Length {len(marked)} >>\nstream\n{marked}endstream"


def _page(contents: int, struct_parents: int, annots: str = "") -> str:
    return (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {contents} 0 R "
        f"/Resources << /Font << /F1 9 0 R >> >> /StructParents {struct_parents}{annots} >>"
    )


def _link(page: int, struct_parent: int, y: int) -> str:
    return f"<< /Type /Annot /Subtype /Link /Rect [72 {y} 144 {y + 20}] /P {page} 0 R /StructParent {struct_parent} >>"


def _tagged_pdf() -> bytes:
    """Three pages A (3), B (4), and C (5) whose structure spans all of them.

    Elements 13, 15, 17, 22, 23, and 26 hold content only on page B; 14 holds
    content on A and B; 24 names page B in `/Pg` but marks content on A; 27
    holds an annotation of page A through an OBJR whose `/Pg` names page B.
    """
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 10 0 R /MarkInfo << /Marked true >> >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>",
        3: _page(6, 0, " /Annots [20 0 R 28 0 R]"),
        4: _page(7, 1, " /Annots [21 0 R]"),
        5: _page(8, 2),
        6: _content("Alpha", 6),
        7: _content("Beta", 5),
        8: _content("Gamma", 1),
        9: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        10: "<< /Type /StructTreeRoot /K 11 0 R /ParentTree 30 0 R /IDTree 31 0 R /ParentTreeNextKey 7 >>",
        11: "<< /S /Document /P 10 0 R /K [12 0 R 13 0 R 14 0 R 15 0 R 16 0 R 18 0 R 22 0 R 24 0 R 26 0 R 27 0 R] >>",
        12: "<< /S /P /P 11 0 R /Pg 3 0 R /K 0 /ID (pa) >>",
        13: "<< /S /P /P 11 0 R /Pg 4 0 R /K [0 << /Type /MCR /Pg 4 0 R /MCID 1 >>] /ID (pb) >>",
        14: "<< /S /Sect /P 11 0 R /Pg 3 0 R /K [1 << /Type /MCR /Pg 4 0 R /MCID 2 >> 17 0 R] >>",
        15: "<< /S /Link /P 11 0 R /K [<< /Type /OBJR /Obj 21 0 R /Pg 4 0 R >>] >>",
        16: "<< /S /Link /P 11 0 R /Pg 3 0 R /K << /Type /OBJR /Obj 20 0 R >> >>",
        17: "<< /S /Span /P 14 0 R /Pg 4 0 R /K 3 /ID (span) >>",
        18: "<< /S /P /P 11 0 R /Pg 5 0 R /K 0 /ID (pc) >>",
        19: "null",
        20: _link(3, 3, 100),
        21: _link(4, 4, 100),
        22: "<< /S /Div /P 11 0 R /K [23 0 R] >>",
        23: "<< /S /P /P 22 0 R /Pg 4 0 R /K 4 >>",
        24: "<< /S /Div /P 11 0 R /Pg 4 0 R /K [<< /Type /MCR /Pg 3 0 R /MCID 2 >>] >>",
        25: _link(4, 5, 200),
        26: "<< /S /Link /P 11 0 R /K << /Type /OBJR /Obj 25 0 R >> >>",
        27: "<< /S /Link /P 11 0 R /K [<< /Type /OBJR /Obj 28 0 R /Pg 4 0 R >>] >>",
        28: _link(3, 6, 200),
        29: "null",
        30: (
            "<< /Nums [0 [12 0 R 14 0 R 24 0 R] 1 [13 0 R 13 0 R 14 0 R 17 0 R 23 0 R] 2 [18 0 R] "
            "3 16 0 R 4 15 0 R 5 26 0 R 6 27 0 R] >>"
        ),
        31: "<< /Kids [32 0 R] >>",
        32: "<< /Limits [(pa) (span)] /Names [(pa) 12 0 R (pb) 13 0 R (pc) 18 0 R (span) 17 0 R] >>",
    }
    return build_raw_pdf(objects)


def _objects(data: bytes) -> dict[int, bytes]:
    return {int(match[1]): match[2] for match in re.finditer(rb"(\d+) 0 obj\s*(.*?)\s*endobj", data, re.DOTALL)}


def _references(body: bytes) -> list[int]:
    return [int(number) for number in re.findall(rb"(\d+) 0 R", body)]


def _kids(body: bytes) -> bytes:
    match = re.search(rb"/K(\[.*?\]|<<.*?>>|\d+ 0 R|\d+)", body, re.DOTALL)
    assert match is not None, body
    return match[1]


def _assert_references_resolve(objects: dict[int, bytes]) -> None:
    for number, body in objects.items():
        for reference in _references(body):
            assert reference in objects, (number, reference)


def _delete_page_b(edit: str) -> pylopdf.Document:
    doc = pylopdf.open(_tagged_pdf())
    if edit == "delete":
        del doc.pages[1:2]
    elif edit == "select":
        doc.pages.select([0, 2])
    else:
        doc.pages.select([0, 0, 2])
    return doc


@pytest.mark.parametrize("edit", ["delete", "select", "select-repeated"])
def test_deleting_a_page_removes_its_structure_content(edit: str) -> None:
    saved = _delete_page_b(edit).tobytes(garbage=True)
    objects = _objects(saved)

    for number in (4, 7, 13, 15, 17, 21, 22, 23, 25, 26):
        assert number not in objects, number
    _assert_references_resolve(objects)
    assert objects[11] == b"<</S/Document/P 10 0 R/K[12 0 R 14 0 R 16 0 R 18 0 R 24 0 R 27 0 R]>>"
    assert objects[14] == b"<</S/Sect/P 11 0 R/Pg 3 0 R/K[1]>>"
    assert objects[24] == b"<</S/Div/P 11 0 R/K[<</Type/MCR/Pg 3 0 R/MCID 2>>]>>"
    assert objects[27] == b"<</S/Link/P 11 0 R/K[<</Type/OBJR/Obj 28 0 R/Pg 3 0 R>>]>>"
    assert objects[30] == b"<</Nums[0[12 0 R 14 0 R 24 0 R] 2[18 0 R] 3 16 0 R 6 27 0 R]>>"
    assert objects[32] == b"<</Limits[(pa)(span)]/Names[(pa) 12 0 R(pc) 18 0 R]>>"

    reopened = pylopdf.open(saved)
    texts = [page.get_text() for page in reopened.pages]
    assert "Beta" not in "".join(texts)
    assert "Gamma 0" in texts[-1]


def test_deleting_every_page_but_one_keeps_the_root() -> None:
    doc = pylopdf.open(_tagged_pdf())
    doc.pages.select([2])
    objects = _objects(doc.tobytes(garbage=True))

    _assert_references_resolve(objects)
    assert objects[11] == b"<</S/Document/P 10 0 R/K[18 0 R]>>"
    assert b"/StructTreeRoot 10 0 R" in objects[1]
    assert objects[30] == b"<</Nums[2[18 0 R]]>>"
    assert objects[32] == b"<</Limits[(pa)(span)]/Names[(pc) 18 0 R]>>"
    assert {3, 4, 20, 21, 25, 28}.isdisjoint(objects)


def test_a_document_without_dropped_structure_saves_unchanged() -> None:
    data = _tagged_pdf()
    reordered = pylopdf.open(data)
    reordered.pages.select([2, 1, 0])
    untouched = pylopdf.open(data)
    untouched.pages.select([0, 1, 2])
    reordered_objects = _objects(reordered.tobytes(garbage=True))
    untouched_objects = _objects(untouched.tobytes(garbage=True))

    for number in (10, 11, 13, 14, 15, 30, 32):
        assert reordered_objects[number] == untouched_objects[number]


def _tagged_with(replacements: Mapping[int, bytes | str], extra: Mapping[int, bytes | str]) -> bytes:
    data = _tagged_pdf()
    objects: dict[int, bytes | str] = dict(_objects(data))
    objects.update(replacements)
    objects.update(extra)
    return build_raw_pdf(objects)


def _assert_refused(data: bytes, code: str = "structure_tree_count") -> None:
    doc = pylopdf.open(data)
    before = doc.tobytes()
    with pytest.raises(pylopdf.LimitError) as caught:
        del doc.pages[1:2]
    assert caught.value.code == code
    assert len(doc.pages) == 3
    assert doc.tobytes() == before
    with pytest.raises(pylopdf.LimitError):
        doc.pages.select([0])
    assert len(doc.pages) == 3


def _element_chain(first: int, depth: int) -> dict[int, bytes | str]:
    """Nest `depth` elements below the Document element; the last marks page B."""
    chain: dict[int, bytes | str] = {}
    for level in range(depth):
        chain[first + level] = f"<< /S /Div /K [{first + level + 1} 0 R] >>"
    chain[first + depth - 1] = "<< /S /P /Pg 4 0 R /K 0 >>"
    return chain


def test_a_structure_tree_deeper_than_256_elements_is_refused() -> None:
    depth = 257
    first = 33
    extra = _element_chain(first, depth)
    replacements = {11: f"<< /S /Document /P 10 0 R /K [12 0 R {first} 0 R] >>"}
    _assert_refused(_tagged_with(replacements, extra))

    doc = pylopdf.open(_tagged_with(replacements, _element_chain(first, 254)))
    del doc.pages[1:2]
    objects = _objects(doc.tobytes(garbage=True))
    assert first not in objects
    assert _references(_kids(objects[11])) == [12]


def test_a_parent_tree_deeper_than_32_levels_is_refused() -> None:
    first = 33
    depth = 32
    extra = {first + level: f"<< /Kids [{first + level + 1} 0 R] >>" for level in range(depth)}
    extra[first + depth] = "<< /Nums [0 12 0 R] >>"
    replacements = {10: "<< /Type /StructTreeRoot /K 11 0 R /ParentTree 33 0 R >>"}
    _assert_refused(_tagged_with(replacements, extra))

    extra = {first + level: f"<< /Kids [{first + level + 1} 0 R] >>" for level in range(depth - 1)}
    extra[first + depth - 1] = "<< /Nums [0 12 0 R 1 13 0 R] >>"
    doc = pylopdf.open(_tagged_with(replacements, extra))
    del doc.pages[1:2]
    objects = _objects(doc.tobytes(garbage=True))
    assert _references(objects[first + depth - 1]) == [12]


def test_a_cyclic_structure_tree_is_walked_once() -> None:
    replacements = {
        13: "<< /S /P /P 11 0 R /Pg 4 0 R /K [0 11 0 R 13 0 R] /ID (pb) >>",
        23: "<< /S /P /P 22 0 R /Pg 4 0 R /K [4 22 0 R] >>",
    }
    doc = pylopdf.open(_tagged_with(replacements, {}))
    del doc.pages[1:2]
    objects = _objects(doc.tobytes(garbage=True))

    _assert_references_resolve(objects)
    assert len(doc.pages) == 2
    assert 4 not in objects


def test_a_reference_cycle_in_kids_reads_as_absent() -> None:
    extra = {33: "34 0 R", 34: "33 0 R"}
    replacements = {14: "<< /S /Sect /P 11 0 R /Pg 3 0 R /K [1 33 0 R << /Type /MCR /Pg 4 0 R /MCID 2 >> 17 0 R] >>"}
    doc = pylopdf.open(_tagged_with(replacements, extra))
    del doc.pages[1:2]
    objects = _objects(doc.tobytes(garbage=True))
    assert 4 not in objects
    assert _references(_kids(objects[14])) == [33]


def test_direct_elements_are_edited_in_place() -> None:
    replacements = {
        11: (
            "<< /S /Document /P 10 0 R /K [12 0 R 13 0 R "
            "<< /S /Div /Pg 4 0 R /K [0 << /Type /MCR /Pg 3 0 R /MCID 3 >> "
            "<< /S /Span /K [<< /Type /MCR /Pg 4 0 R /MCID 4 >>] >>] >> "
            "<< /S /Div /K [<< /S /P /Pg 4 0 R /K 1 >>] >> 18 0 R] >>"
        ),
    }
    doc = pylopdf.open(_tagged_with(replacements, {}))
    del doc.pages[1:2]
    objects = _objects(doc.tobytes(garbage=True))

    _assert_references_resolve(objects)
    assert objects[11] == b"<</S/Document/P 10 0 R/K[12 0 R<</S/Div/K[<</Type/MCR/Pg 3 0 R/MCID 3>>]>> 18 0 R]>>"
    assert 4 not in objects


def test_elements_that_only_the_lookup_trees_name_are_walked() -> None:
    replacements = {11: "<< /S /Document /P 10 0 R /K [12 0 R 18 0 R] >>"}
    doc = pylopdf.open(_tagged_with(replacements, {}))
    del doc.pages[1:2]
    objects = _objects(doc.tobytes(garbage=True))

    _assert_references_resolve(objects)
    assert {4, 13, 15, 17, 21, 23, 25, 26}.isdisjoint(objects)
    assert objects[30] == b"<</Nums[0[12 0 R 14 0 R 24 0 R] 2[18 0 R] 3 16 0 R 6 27 0 R]>>"
    assert objects[32] == b"<</Limits[(pa)(span)]/Names[(pa) 12 0 R(pc) 18 0 R]>>"


def test_remaining_elements_lose_references_to_removed_elements() -> None:
    replacements = {
        12: "<< /S /P /P 11 0 R /Pg 3 0 R /K 0 /ID (pa) /Ref [17 0 R 18 0 R] >>",
        18: "<< /S /P /P 11 0 R /Pg 5 0 R /K 0 /ID (pc) /Ref 13 0 R >>",
        16: "<< /S /Link /P 11 0 R /Pg 3 0 R /K << /Type /OBJR /Obj 20 0 R >> /Ref [13 0 R 23 0 R] >>",
    }
    doc = pylopdf.open(_tagged_with(replacements, {}))
    del doc.pages[1:2]
    objects = _objects(doc.tobytes(garbage=True))

    _assert_references_resolve(objects)
    assert {4, 13, 17, 23}.isdisjoint(objects)
    assert objects[12] == b"<</S/P/P 11 0 R/Pg 3 0 R/K 0/ID(pa)/Ref[18 0 R]>>"
    assert objects[18] == b"<</S/P/P 11 0 R/Pg 5 0 R/K 0/ID(pc)>>"
    assert objects[16] == b"<</S/Link/P 11 0 R/Pg 3 0 R/K<</Type/OBJR/Obj 20 0 R>>>>"


def test_links_that_navigation_cleanup_removes_leave_the_structure_tree() -> None:
    replacements = {
        20: "<< /Type /Annot /Subtype /Link /Rect [72 100 144 120] /P 3 0 R /StructParent 3 /Dest [4 0 R /Fit] >>",
        16: "<< /S /Link /P 11 0 R /Pg 3 0 R /K [5 << /Type /OBJR /Obj 20 0 R >>] >>",
    }
    doc = pylopdf.open(_tagged_with(replacements, {}))
    del doc.pages[1:2]
    assert doc.pages[0].get_links() == []
    objects = _objects(doc.tobytes(garbage=True))

    _assert_references_resolve(objects)
    assert {4, 20}.isdisjoint(objects)
    assert objects[16] == b"<</S/Link/P 11 0 R/Pg 3 0 R/K[5]>>"
    assert objects[30] == b"<</Nums[0[12 0 R 14 0 R 24 0 R] 2[18 0 R] 6 27 0 R]>>"
