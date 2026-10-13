"""Keep navigation from retaining pages excluded by structural edits."""

from __future__ import annotations

from typing import Literal

import pytest
from conftest import build_raw_pdf

import pylopdf

_EXCLUDED_TEXT = "EXCLUDED-PAGE-CONTENT-9f7c"


def _stream(text: str) -> str:
    content = f"BT /F1 20 Tf 20 100 Td ({text}) Tj ET"
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"


def _linked_pdf(*, indirect_annots: bool = False, excessive_names: bool = False) -> bytes:
    """Combine direct, named, legacy, outline, and external navigation."""
    annots = "[6 0 R 7 0 R 8 0 R 9 0 R 22 0 R 23 0 R 24 0 R]"
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Names << /Dests 11 0 R >> "
        "/Dests << /legacygone [4 0 R /Fit] /legacykeep [5 0 R /Fit] >> /Outlines 12 0 R /OpenAction [4 0 R /Fit] >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 /MediaBox [0 0 200 200] "
        "/Resources << /Font << /F1 10 0 R >> >> >>",
        3: f"<< /Type /Page /Parent 2 0 R /Contents 13 0 R /Annots {'25 0 R' if indirect_annots else annots} >>",
        4: "<< /Type /Page /Parent 2 0 R /Contents 14 0 R >>",
        5: "<< /Type /Page /Parent 2 0 R /Contents 15 0 R >>",
        6: "<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /Dest [4 0 R /Fit] >>",
        7: "<< /Type /Annot /Subtype /Link /Rect [20 1 30 10] /A << /S /GoTo /D (gone) >> >>",
        8: "<< /Type /Annot /Subtype /Link /Rect [40 1 50 10] /Dest /legacygone >>",
        9: "<< /Type /Annot /Subtype /Link /Rect [60 1 70 10] /Dest (keep) >>",
        10: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        11: "<< /Kids [16 0 R 17 0 R] >>",
        12: "<< /Type /Outlines /First 18 0 R /Last 20 0 R /Count 4 >>",
        13: _stream("Retained first page"),
        14: _stream(_EXCLUDED_TEXT),
        15: _stream("Retained last page"),
        16: "<< /Names [(gone) [4 0 R /Fit]] /Limits [(gone) (gone)] >>",
        17: "<< /Names [(keep) [5 0 R /Fit]] /Limits [(keep) (keep)] >>",
        18: "<< /Title (Removed) /Dest [4 0 R /Fit] /Next 19 0 R /First 21 0 R >>",
        19: "<< /Title (Named removed) /A << /S /GoTo /D (gone) >> /Prev 18 0 R /Next 20 0 R >>",
        20: "<< /Title (Retained) /Dest (keep) /Prev 19 0 R >>",
        21: "<< /Title (Retained child) /Dest [5 0 R /Fit] /Parent 18 0 R >>",
        22: "<< /Type /Annot /Subtype /Link /Rect [80 1 90 10] /A << /S /URI /URI (https://example.com) >> >>",
        23: "<< /Type /Annot /Subtype /Link /Rect [100 1 110 10] /A << /S /GoToR /F (other.pdf) /D (gone) >> >>",
        24: "<< /Type /Annot /Subtype /Link /Rect [120 1 130 10] /Dest /legacykeep >>",
    }
    if indirect_annots:
        objects[25] = annots
    if excessive_names:
        pairs = " ".join(f"(name{index}) [5 0 R /Fit]" for index in range(4097))
        objects[17] = f"<< /Names [{pairs}] >>"
    return build_raw_pdf(objects)


@pytest.mark.parametrize("operation", ["delete", "select"])
@pytest.mark.parametrize("indirect_annots", [False, True])
def test_excluded_page_navigation_is_removed(operation: Literal["delete", "select"], *, indirect_annots: bool) -> None:
    """Remove excluded targets while preserving retained and external links."""
    doc = pylopdf.Document(stream=_linked_pdf(indirect_annots=indirect_annots))
    if operation == "delete":
        doc.delete_page(1)
        source, target = 0, 1
    else:
        doc.select([2, 0])
        source, target = 1, 0
    assert doc.page_count == 2
    saved = doc.tobytes(garbage=True)
    assert _EXCLUDED_TEXT.encode() not in saved
    for current in (doc, pylopdf.Document(stream=saved)):
        links = current[source].get_links()
        goto = [link for link in links if link["kind"] == pylopdf.LINK_GOTO]
        assert [link["nameddest"] for link in goto] == ["keep", "legacykeep"]
        assert [link["page"] for link in goto] == [target, target]
        assert [link["kind"] for link in links] == [
            pylopdf.LINK_GOTO,
            pylopdf.LINK_URI,
            pylopdf.LINK_GOTOR,
            pylopdf.LINK_GOTO,
        ]
        assert current.get_toc() == [[2, "Retained child", target + 1], [1, "Retained", target + 1]]
        assert _EXCLUDED_TEXT not in current.get_text()


@pytest.mark.parametrize("indirect_annots", [False, True])
def test_duplicate_selected_pages_share_clean_navigation(*, indirect_annots: bool) -> None:
    """Clean direct and shared annotation arrays on selected page duplicates."""
    doc = pylopdf.Document(stream=_linked_pdf(indirect_annots=indirect_annots))
    doc.select([0, 0, 2])
    saved = doc.tobytes(garbage=True)
    assert _EXCLUDED_TEXT.encode() not in saved
    reloaded = pylopdf.Document(stream=saved)
    for page_number in (0, 1):
        links = reloaded[page_number].get_links()
        assert len(links) == 4
        assert [link["page"] for link in links if link["kind"] == pylopdf.LINK_GOTO] == [2, 2]


@pytest.mark.parametrize("operation", ["delete", "select"])
def test_removing_every_page_drops_outline_and_destination_references(operation: Literal["delete", "select"]) -> None:
    doc = pylopdf.Document(stream=_linked_pdf())
    if operation == "delete":
        doc.delete_pages([0, 1, 2])
    else:
        doc.select([])
    saved = doc.tobytes(garbage=True)
    assert _EXCLUDED_TEXT.encode() not in saved
    for current in (doc, pylopdf.Document(stream=saved)):
        assert current.page_count == 0
        assert current.get_toc() == []


@pytest.mark.parametrize("operation", ["delete", "select"])
def test_cleanup_limit_failure_preserves_document(operation: Literal["delete", "select"]) -> None:
    """Reject a truncated destination tree before mutating pages or navigation."""
    doc = pylopdf.Document(stream=_linked_pdf(excessive_names=True))
    before = doc.tobytes()
    page = doc[0]
    if operation == "delete":
        with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
            doc.delete_page(1)
    else:
        with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
            doc.select([0, 2])
    assert doc.page_count == 3
    assert doc.tobytes() == before
    assert page.rect == pylopdf.Rect(0, 0, 200, 200)


@pytest.mark.parametrize("operation", ["delete", "select"])
def test_cleanup_refuses_navigation_tree_aliasing_a_page(operation: Literal["delete", "select"]) -> None:
    """Do not replace a page when a malformed destination root aliases it."""
    data = _linked_pdf().replace(b"/Dests 11 0 R", b"/Dests 3 0 R ")
    doc = pylopdf.Document(stream=data)
    before = doc.tobytes()
    if operation == "delete":
        with pytest.raises(pylopdf.PdfError, match="cannot alias a page"):
            doc.delete_page(1)
    else:
        with pytest.raises(pylopdf.PdfError, match="cannot alias a page"):
            doc.select([0, 2])
    assert doc.page_count == 3
    assert doc.tobytes() == before


@pytest.mark.parametrize("operation", ["delete", "select"])
def test_cleanup_refuses_annotation_aliasing_a_page(operation: Literal["delete", "select"]) -> None:
    """Do not treat navigation-bearing page dictionaries as annotations."""
    doc = pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R] >>",
                4: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R >>",
                5: "<< /Type /Page /Parent 2 0 R /Subtype /Link /Rect [1 1 10 10] /Dest [4 0 R /Fit] >>",
                6: _stream(_EXCLUDED_TEXT),
            }
        )
    )
    page = doc[0]
    before = doc.tobytes()
    if operation == "delete":
        with pytest.raises(pylopdf.PdfError, match="cannot alias a page"):
            doc.delete_page(1)
    else:
        with pytest.raises(pylopdf.PdfError, match="cannot alias a page"):
            doc.select([0, 2])
    assert doc.tobytes() == before
    assert doc.page_count == 3
    assert page.rect == pylopdf.Rect(0, 0, 200, 200)


@pytest.mark.parametrize("operation", ["delete", "select"])
def test_cleanup_refuses_outline_root_aliasing_a_page(operation: Literal["delete", "select"]) -> None:
    doc = pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R /First 5 0 R >>",
                5: "<< /Title (Retained) /Dest [3 0 R /Fit] >>",
                6: _stream(_EXCLUDED_TEXT),
            }
        )
    )
    page = doc[0]
    before = doc.tobytes()
    if operation == "delete":
        with pytest.raises(pylopdf.PdfError, match="cannot alias a page"):
            doc.delete_page(1)
    else:
        with pytest.raises(pylopdf.PdfError, match="cannot alias a page"):
            doc.select([0])
    assert doc.tobytes() == before
    assert doc.page_count == 2
    assert page.rect == pylopdf.Rect(0, 0, 200, 200)


@pytest.mark.parametrize("operation", ["delete", "select"])
def test_cleanup_preserves_indirect_name_tree_keys(operation: Literal["delete", "select"]) -> None:
    doc = pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /Names << /Dests 5 0 R >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [7 0 R] >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
                5: "<< /Names [6 0 R [3 0 R /Fit]] >>",
                6: "(keep)",
                7: "<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /Dest (keep) >>",
            }
        )
    )
    assert doc[0].get_links()[0]["page"] == 0
    if operation == "delete":
        doc.delete_page(1)
    else:
        doc.select([0])
    saved = doc.tobytes()
    for current in (doc, pylopdf.Document(stream=saved)):
        assert current[0].get_links()[0]["page"] == 0
        assert current[0].get_links()[0]["nameddest"] == "keep"


@pytest.mark.parametrize("operation", ["delete", "select"])
def test_cleanup_refuses_names_container_aliasing_a_page(operation: Literal["delete", "select"]) -> None:
    doc = pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /Names 4 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R /Dests 5 0 R >>",
                5: "<< /Names [(keep) [3 0 R /Fit]] >>",
                6: _stream(_EXCLUDED_TEXT),
            }
        )
    )
    page = doc[0]
    before = doc.tobytes()
    if operation == "delete":
        with pytest.raises(pylopdf.PdfError, match="cannot alias a page"):
            doc.delete_page(1)
    else:
        with pytest.raises(pylopdf.PdfError, match="cannot alias a page"):
            doc.select([0])
    assert doc.tobytes() == before
    assert doc.page_count == 2
    assert page.rect == pylopdf.Rect(0, 0, 200, 200)
