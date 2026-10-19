"""Objects nested deeper than hayro reads.

hayro 0.8 reads no array or dictionary nested more than 64 levels deep, counting the object that holds it, and treats
the whole object as absent; hayro 0.7 read such objects by recursion. lopdf reads them up to 100 levels deep. A page
dictionary nested that deeply would drop out of hayro's page list and shift every later page, a trailer or catalog
nested that deeply would keep hayro from opening the document, and so would a root page-tree node, except that hayro
then collects the pages without the attributes they inherit. The rendering copy cuts those dictionaries instead, and
each page whose dictionary it cut is refused with `object_depth`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

import pytest
from conftest import build_raw_pdf
from test_content_nesting import OPERATIONS, SHOW

import pylopdf

CODE = "object_depth"
#: The levels hayro reads in one object, the object itself included.
READ_DEPTH = 64
FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
PAGE = "/Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> "


def _arrays(levels: int) -> str:
    return "[" * levels + "1" + "]" * levels


def _stream(data: str) -> str:
    return f"<< /Length {len(data)} >>\nstream\n{data}\nendstream"


def _document(page_entries: list[str], *, catalog: str = "", pages: str = "", trailer: str = "") -> bytes:
    """Build one page per string of extra page entries, each drawing `SHOW` on 300 by 300 points.

    The page tree's root holds the MediaBox, so a page that hayro reads without its inherited attributes comes out
    as an A4 page.
    """
    count = len(page_entries)
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(count))
    objects: dict[int, str | bytes] = {
        1: f"<< /Type /Catalog /Pages 2 0 R {catalog}>>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {count} /MediaBox [0 0 300 300] {pages}>>",
        3: FONT,
    }
    for index, entries in enumerate(page_entries):
        objects[4 + 2 * index] = f"<< {PAGE}/Contents {5 + 2 * index} 0 R {entries}>>"
        objects[5 + 2 * index] = _stream(SHOW)
    data = build_raw_pdf(objects)
    # The trailer follows the cross-reference table, so a longer one moves no offset.
    return data.replace(b"/Root 1 0 R >>", f"/Root 1 0 R {trailer}>>".encode())


def _refusal(call: Callable[[], object]) -> pylopdf.LimitError:
    with pytest.raises(pylopdf.LimitError) as caught:
        call()
    return caught.value


def test_a_page_at_the_limit_is_read() -> None:
    """The page dictionary is the first level, so an entry may nest 63 more."""
    doc = pylopdf.Document(stream=_document([f"/PieceInfo {_arrays(READ_DEPTH - 1)} "]))

    for operation in OPERATIONS.values():
        operation(doc)
    assert "PAGE TEXT" in doc.get_page_text(0)
    assert doc[0].get_pixmap().width == 300


@pytest.mark.parametrize("levels", [READ_DEPTH, 99])
def test_every_operation_refuses_a_page_nested_too_deeply(levels: int) -> None:
    data = _document([f"/PieceInfo {_arrays(levels)} "])
    for operation in OPERATIONS.values():
        doc = pylopdf.Document(stream=data)
        error = _refusal(partial(operation, doc))
        assert error.code == CODE
        assert error.args[0] == CODE
        assert "page 1 nests" in str(error)


def test_only_the_page_nested_too_deeply_is_refused() -> None:
    doc = pylopdf.Document(stream=_document(["", f"/PieceInfo {_arrays(80)} ", ""]))

    assert "PAGE TEXT" in doc.get_page_text(0)
    assert "PAGE TEXT" in doc.get_page_text(2)
    assert doc[2].get_pixmap().width == 300
    assert len(doc.render_pages([0, 2], workers=2)) == 2
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE
    assert _refusal(doc.get_text).code == CODE
    assert _refusal(doc.render_pages).code == CODE
    assert _refusal(doc.compress_images).code == CODE


def test_a_deep_page_stays_refused_across_refusals_and_edits() -> None:
    doc = pylopdf.Document(stream=_document(["", f"/PieceInfo {_arrays(80)} "]))

    assert _refusal(lambda: doc.get_page_text(1)).code == CODE
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE
    doc[0].insert_text((20, 250), "ADDED")
    assert "ADDED" in doc.get_page_text(0)
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE

    doc.delete_pages([1])
    assert "ADDED" in doc.get_text()


def test_inherited_resources_nested_too_deeply_refuse_the_pages_that_inherit_them() -> None:
    """An intermediate page-tree node lends its deep resources to its own kids only."""
    deep = f"/Resources << /Font << /F1 3 0 R >> /Properties << /P0 {_arrays(80)} >> >>"
    data = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [4 0 R 6 0 R] /Count 2 /MediaBox [0 0 300 300] >>",
            3: FONT,
            4: f"<< {PAGE}/Contents 5 0 R >>",
            5: _stream(SHOW),
            6: f"<< /Type /Pages /Parent 2 0 R /Kids [7 0 R] /Count 1 {deep} >>",
            7: "<< /Type /Page /Parent 6 0 R /Contents 8 0 R >>",
            8: _stream(SHOW),
        }
    )
    doc = pylopdf.Document(stream=data)

    assert "PAGE TEXT" in doc.get_page_text(0)
    assert doc[0].get_pixmap().width == 300
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE


@pytest.mark.parametrize("where", ["catalog", "pages", "trailer"])
def test_a_document_root_nested_too_deeply_still_opens(where: str) -> None:
    """The copy drops the deep entry, and every page renders with the attributes it inherits."""
    deep = f"/PieceInfo {_arrays(80)} "
    data = _document(
        ["", ""],
        catalog=deep if where == "catalog" else "",
        pages=deep if where == "pages" else "",
        trailer=f"/Info << /Title (deep) /Extra {_arrays(80)} >> " if where == "trailer" else "",
    )
    doc = pylopdf.Document(stream=data)

    for operation in OPERATIONS.values():
        operation(doc)
    assert doc.get_text().count("PAGE TEXT") == 2
    assert [pixmap.width for pixmap in (doc[0].get_pixmap(), doc[1].get_pixmap())] == [300, 300]


def test_the_web_profile_refuses_such_documents_when_they_open() -> None:
    """`DocumentLimits.web()` bounds direct nesting at 64 levels when it opens a document."""
    with pytest.raises(pylopdf.LimitError) as caught:
        pylopdf.Document(stream=_document([f"/PieceInfo {_arrays(80)} "]), limits=pylopdf.DocumentLimits.web())
    assert caught.value.code == CODE


def test_the_saved_document_keeps_the_deep_entries() -> None:
    """Only the rendering copy is cut; the editable and saved document keep what lopdf read."""
    deep = f"/PieceInfo {_arrays(80)} "
    doc = pylopdf.Document(stream=_document(["", deep], catalog=deep))
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE

    reopened = pylopdf.Document(stream=doc.tobytes())
    assert reopened.complexity["max_object_depth"] > READ_DEPTH
    assert _refusal(lambda: reopened.get_page_text(1)).code == CODE
