"""Regression tests for outline target encoding and PDF numeric admission."""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf
from pylopdf import Destination, Launch, OutlineEntry, RemoteDestination


@pytest.mark.parametrize(
    "target",
    [RemoteDestination("日本語.pdf", 4), Launch("café.txt")],
)
def test_unicode_file_targets_round_trip(target: RemoteDestination | Launch) -> None:
    doc = pylopdf.open(build_pdf(["a"]))
    doc.outline = [OutlineEntry(1, "file", target)]
    assert doc.outline[0].destination == target
    assert pylopdf.open(doc.to_bytes()).outline[0].destination == target

    item = doc.outline[0]
    item.destination = None
    item.destination = target
    assert item.destination == target
    assert pylopdf.open(doc.to_bytes()).outline[0].destination == target


@pytest.mark.parametrize(
    "target", [Destination(0, (1e100, 0)), Destination(0, (0, -1e100)), Destination(0, zoom=1e100)]
)
def test_targets_reject_pdf_real_overflow_before_mutation(target: Destination) -> None:
    doc = pylopdf.open(build_pdf(["a"]))
    original = [OutlineEntry(1, "original", Destination(0))]
    doc.outline = original
    item = doc.outline[0]
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="PDF real-number range"):
        doc.outline = [OutlineEntry(1, "invalid", target)]
    assert doc.to_bytes() == before
    assert doc.outline.entries() == original

    with pytest.raises(pylopdf.PdfError, match="PDF real-number range"):
        item.destination = target
    assert doc.to_bytes() == before
    assert item.destination == Destination(0)


def test_targets_reject_overflow_after_display_coordinate_conversion() -> None:
    doc = pylopdf.open(build_pdf(["a"]))
    doc.pages[0].obj["MediaBox"] = [2e38, 0, 3e38, 100]
    doc.outline = [OutlineEntry(1, "original", Destination(0))]
    item = doc.outline[0]
    before = doc.to_bytes()
    target = Destination(0, (2e38, 0))

    with pytest.raises(pylopdf.PdfError, match="PDF real-number range"):
        item.destination = target
    assert doc.to_bytes() == before
    assert item.destination == Destination(0)

    with pytest.raises(pylopdf.PdfError, match="PDF real-number range"):
        doc.outline = [OutlineEntry(1, "invalid", target)]
    assert doc.to_bytes() == before


@pytest.mark.parametrize("direct_root", [True, False])
def test_open_state_recounts_direct_and_aliased_outline_roots(*, direct_root: bool) -> None:
    root = "<< /First 5 0 R /Count 1 >>" if direct_root else "4 0 R"
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: f"<< /Type /Catalog /Pages 2 0 R /Outlines {root} >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: "7 0 R",
                5: "<< /Title (A) /First 6 0 R /Count -1 >>",
                6: "<< /Title (B) >>",
                7: "<< /First 5 0 R /Count 1 >>",
            }
        )
    )
    item = doc.outline[0]
    assert not item.is_open
    assert doc.catalog["Outlines"]["Count"] == 1

    item.is_open = True
    assert item.is_open
    assert doc.catalog["Outlines"]["Count"] == 2
    assert pylopdf.open(doc.to_bytes()).catalog["Outlines"]["Count"] == 2

    item.is_open = False
    assert not item.is_open
    assert doc.catalog["Outlines"]["Count"] == 1


@pytest.mark.parametrize("first", ["6 0 R", "99 0 R"])
def test_null_and_missing_children_have_no_open_state(first: str) -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /Outlines 4 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: "<< /First 5 0 R /Count 2 >>",
                5: f"<< /Title (A) /First {first} /Count 1 >>",
                6: "null",
            }
        )
    )
    item = doc.outline[0]
    assert item.children == ()
    assert not item.is_open
    before = doc.to_bytes()

    for is_open in (False, True):
        with pytest.raises(pylopdf.PdfError, match="without children"):
            item.is_open = is_open
        assert doc.to_bytes() == before
