"""Page.clean_contents, Annotation.clean_contents, and Page.clip_to_rect."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Rect

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
HELVETICA = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"


def _stream(content: str, dictionary: str = "") -> str:
    return f"<< {dictionary} /Length {len(content)} >>\nstream\n{content}\nendstream"


def _page(contents: str, resources: str, *extra: str) -> bytes:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] >>",
        3: f"<< /Type /Page /Parent 2 0 R /Resources {resources} /Contents {contents} >>",
        4: HELVETICA,
    }
    objects.update(enumerate(extra, start=5))
    return build_raw_pdf(objects)


def test_cleaning_joins_normalizes_balances_and_prunes() -> None:
    first = _stream("% a comment\nq q BT /F1 12 Tf 20 250 Td (Hello) Tj ET")
    second = _stream("Q Q Q 1 0 0 RG 0 0 m 100 100 l S q")
    form = _stream("0 0 10 10 re f", "/Type /XObject /Subtype /Form /BBox [0 0 10 10]")
    resources = "<< /Font << /F1 4 0 R /Unused 4 0 R >> /XObject << /Fm 7 0 R >> /ExtGState << /G0 << /CA 0.5 >> >> >>"
    doc = pylopdf.open(_page("[5 0 R 6 0 R]", resources, first, second, form))
    page = doc.pages[0]
    before = (page.text(), bytes(page.pixmap(dpi=36)))

    page.clean_contents()

    data = page.obj["Contents"].read()
    assert b"comment" not in data
    operators = [operation.operator for operation in page.operations]
    assert operators.count("q") == operators.count("Q")
    resources = page.obj["Resources"]
    assert list(resources["Font"]) == ["F1"]
    assert "XObject" not in resources
    assert "ExtGState" not in resources
    assert (page.text(), bytes(page.pixmap(dpi=36))) == before
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].text() == before[0]


def test_cleaning_keeps_names_that_inherited_resources_need() -> None:
    # The form has no resources of its own and draws with the page's font.
    form = _stream("BT /F1 12 Tf 0 0 Td (In form) Tj ET", "/Type /XObject /Subtype /Form /BBox [0 0 300 300]")
    resources = "<< /Font << /F1 4 0 R >> /XObject << /Fm 6 0 R >> >>"
    doc = pylopdf.open(_page("5 0 R", resources, _stream("q 1 0 0 1 20 200 cm /Fm Do Q"), form))
    page = doc.pages[0]

    page.clean_contents()

    assert list(page.obj["Resources"]["Font"]) == ["F1"]
    assert page.text().strip() == "In form"


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
@pytest.mark.parametrize("path", sorted(REAL_WORLD.glob("*.pdf")), ids=lambda path: path.name)
def test_cleaning_every_corpus_page_keeps_text_and_pixels(path: Path) -> None:
    doc = pylopdf.open(path)
    pages = range(min(len(doc.pages), 3))
    before = [(doc.pages[pno].text(), bytes(doc.pages[pno].pixmap(dpi=36))) for pno in pages]
    for pno in pages:
        doc.pages[pno].clean_contents()
    reopened = pylopdf.open(doc.to_bytes())
    after = [(reopened.pages[pno].text(), bytes(reopened.pages[pno].pixmap(dpi=36))) for pno in pages]
    assert after == before


def test_annotation_appearances_are_cleaned_in_place() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    annotation = page.annotations.add(pylopdf.Square(Rect(20, 20, 120, 80), interior_color=pylopdf.Color(0, 0, 1)))
    appearance = annotation.obj["AP"]["N"]
    appearance.write(b"% note\n" + appearance.read() + b" Q")
    before = bytes(page.pixmap(dpi=36))

    annotation.clean_contents()

    data = annotation.obj["AP"]["N"].read()
    assert b"note" not in data
    assert bytes(page.pixmap(dpi=36)) == before


def test_clip_to_rect_removes_what_lies_outside() -> None:
    content = (
        "BT /F1 12 Tf 20 250 Td (Outside) Tj 0 -150 Td (Inside) Tj ET"
        " 0 0 1 rg 200 200 50 50 re f 1 0 0 rg 0 0 300 300 re f"
    )
    resources = "<< /Font << /F1 4 0 R >> >>"
    doc = pylopdf.open(_page("5 0 R", resources, _stream(content)))
    page = doc.pages[0]

    report = page.clip_to_rect(Rect(10, 180, 150, 220))

    assert page.text().split() == ["Inside"]
    # The blue square lies outside; the red page fill crosses the edge and stays, clipped.
    assert report.paths == 1
    paths = page.content().paths
    assert len(paths) == 1
    assert paths[0].context.clip_bbox is not None
    assert tuple(paths[0].context.clip_bbox) == pytest.approx((10, 180, 150, 220), abs=0.01)
