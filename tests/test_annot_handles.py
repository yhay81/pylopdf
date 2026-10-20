"""pymupdf's Annot handles and annotation constants (M0)."""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def _annotated() -> pylopdf.Document:
    annots = (
        "<< /Type /Annot /Subtype /Text /Rect [10 10 30 30] /Contents (Note) /Name /Comment "
        "/T (Ann) /CreationDate (D:20260101) /M (D:20260102) /Subj (Review) /NM (n-1) "
        "/F 4 /C [1 0 0] /IC [0 0 1] >>"
    )
    return pylopdf.open(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [4 0 R "
                    "<< /Type /Annot /Subtype /FreeText /IT /FreeTextCallout /Rect [50 50 80 80] /F 2 >>] >>"
                ),
                4: annots,
            }
        )
    )


def test_annots_returns_read_only_handles() -> None:
    page = _annotated()[0]

    text, free_text = page.annots()

    assert isinstance(text, pylopdf.Annot)
    assert text.type == (pylopdf.PDF_ANNOT_TEXT, "Text")
    assert text.type[0] is pylopdf.AnnotType.TEXT
    assert free_text.type == (pylopdf.PDF_ANNOT_FREE_TEXT, "FreeText", "FreeTextCallout")
    assert text.rect == pylopdf.Rect(10, 70, 30, 90)
    assert text.info == {
        "content": "Note",
        "name": "Comment",
        "title": "Ann",
        "creationDate": "D:20260101",
        "modDate": "D:20260102",
        "subject": "Review",
        "id": "n-1",
    }
    assert text.colors == {"stroke": (1.0, 0.0, 0.0), "fill": (0.0, 0.0, 1.0)}
    assert free_text.colors == {"stroke": (), "fill": ()}
    assert text.flags == pylopdf.PDF_ANNOT_IS_PRINT
    assert free_text.flags & pylopdf.PDF_ANNOT_IS_HIDDEN
    assert (text.xref, free_text.xref) == (4, 0)
    assert text.parent is page
    assert repr(text) == "'Text' annotation on page 0"
    with pytest.raises(AttributeError):
        text.rect = pylopdf.Rect(0, 0, 1, 1)  # ty: ignore[invalid-assignment]


def test_annots_filters_by_type() -> None:
    page = _annotated()[0]

    assert [annot.type[1] for annot in page.annots(types=[pylopdf.PDF_ANNOT_FREE_TEXT])] == ["FreeText"]
    assert page.annots(types=[pylopdf.PDF_ANNOT_LINK]) == []


def test_handles_compare_by_annotation() -> None:
    doc = _annotated()

    first, second = doc[0].annots(), doc[0].annots()

    assert first == second
    assert len(set(first) | set(second)) == 2
    assert first[0] != first[1]


def test_handles_go_stale() -> None:
    doc = _annotated()
    doc.new_page()
    text = doc[0].annots()[0]

    del doc[0].obj["Annots"][0]
    with pytest.raises(pylopdf.StaleObjectError):
        _ = text.rect

    remaining = doc[0].annots()[0]
    doc.delete_page(1)
    with pytest.raises(pylopdf.StaleObjectError):
        _ = remaining.type


def test_add_highlight_annot_returns_its_annot() -> None:
    doc = pylopdf.open(stream=build_pdf(["Highlight me"]))
    page = doc[0]
    hit = page.search_for("Highlight")[0]

    annot = page.add_highlight_annot(hit, content="note")

    assert annot.type == (pylopdf.PDF_ANNOT_HIGHLIGHT, "Highlight")
    assert annot.info["content"] == "note"
    assert annot.xref > 0
    assert annot in page.annots()
    quad_annot = page.add_highlight_annot(hit.quad)
    assert quad_annot.rect == pytest.approx(annot.rect, abs=1e-3)
    assert page.add_highlight_annot([hit.quad, hit]).xref > quad_annot.xref
    with pytest.raises(TypeError, match="quads is required"):
        page.add_highlight_annot()


def test_insert_link_takes_a_pymupdf_link_dictionary() -> None:
    doc = pylopdf.open(stream=build_pdf(["Links"]))
    page = doc[0]

    page.insert_link({"kind": pylopdf.LINK_URI, "from": pylopdf.Rect(10, 10, 60, 30), "uri": "https://e.test/"})

    (link,) = page.get_links()
    assert link["uri"] == "https://e.test/"
    assert link["from"] == pytest.approx(pylopdf.Rect(10, 10, 60, 30))
    assert page.annots()[0].type == (pylopdf.PDF_ANNOT_LINK, "Link")
    with pytest.raises(pylopdf.PdfError, match="LINK_URI only"):
        page.insert_link({"kind": pylopdf.LINK_GOTO, "from": (0, 0, 1, 1), "page": 0})
    with pytest.raises(TypeError):
        page.insert_link((0, 0, 1, 1), "https://e.test/")  # ty: ignore[too-many-positional-arguments]
    assert not hasattr(page, "add_link_annot")


def test_annotation_constants_match_pymupdf() -> None:
    assert (pylopdf.PDF_ANNOT_TEXT, pylopdf.PDF_ANNOT_WIDGET, pylopdf.PDF_ANNOT_3D) == (0, 21, 26)
    assert (pylopdf.PDF_ANNOT_UNKNOWN, pylopdf.PDF_ANNOT_PROJECTION) == (-1, 27)
    assert pylopdf.PDF_ANNOT_3D is pylopdf.AnnotType.THREE_D
    assert pylopdf.PDF_ANNOT_IS_LOCKED_CONTENTS == 512
    assert pylopdf.AnnotFlags(6) == pylopdf.PDF_ANNOT_IS_PRINT | pylopdf.PDF_ANNOT_IS_HIDDEN
