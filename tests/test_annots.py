"""Tests for Page annotation APIs.

Highlights generate appearance streams, so Hayro renders them. Visual
correctness is checked through rendered pixels.
"""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

YELLOWISH_BLUE_MAX = 210
WHITE_MIN = 240


def _region_pixels(page: pylopdf.Page, rect: pylopdf.Rect) -> list[tuple[int, int, int]]:
    """Return RGB pixels inside ``rect`` after rendering on white."""
    pix = page.get_pixmap(background=(255, 255, 255))
    out = []
    for y in range(int(rect.y0), min(int(rect.y1), pix.height)):
        for x in range(int(rect.x0), min(int(rect.x1), pix.width)):
            offset = y * pix.stride + x * 4
            r, g, b = pix.samples[offset : offset + 3]
            out.append((r, g, b))
    return out


def _has_yellowish(pixels: list[tuple[int, int, int]]) -> bool:
    """Return whether the sample contains yellowish pixels."""
    return any(r > WHITE_MIN and g > WHITE_MIN and b < YELLOWISH_BLUE_MAX for r, g, b in pixels)


def test_search_and_highlight() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    hits = page.search_for("Hello")
    assert hits
    page.add_highlight_annot(hits)

    annots = page.annots()
    assert len(annots) == 1
    annot = annots[0]
    assert annot["type"] == "Highlight"
    assert annot["uri"] is None
    hit = hits[0]
    # The annotation covers the hit; allow small f32 storage differences.
    assert annot["rect"].x0 <= hit.x0 + 0.01
    assert annot["rect"].x1 >= hit.x1 - 0.01
    # The appearance stream makes the highlight visible in Hayro.
    assert _has_yellowish(_region_pixels(page, hit))
    # Multiply blending preserves the black text pixels.
    assert any(r < 128 for r, _, _ in _region_pixels(page, hit))


def test_highlight_survives_save_roundtrip() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    hits = doc[0].search_for("PDF")
    doc[0].add_highlight_annot(hits, content="review note")
    reopened = pylopdf.open(stream=doc.tobytes())
    annots = reopened[0].annots()
    assert len(annots) == 1
    assert annots[0]["type"] == "Highlight"
    assert annots[0]["contents"] == "review note"
    assert _has_yellowish(_region_pixels(reopened[0], hits[0]))


def test_highlight_multiple_rects_in_one_annot() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    rects = [pylopdf.Rect(10, 10, 40, 20), pylopdf.Rect(60, 40, 90, 50)]
    page.add_highlight_annot(rects)
    annots = page.annots()
    assert len(annots) == 1
    # The bounding rectangle covers both input rectangles.
    assert annots[0]["rect"].x0 <= 10
    assert annots[0]["rect"].x1 >= 90
    assert annots[0]["rect"].y1 >= 50
    assert _has_yellowish(_region_pixels(page, rects[0]))
    assert _has_yellowish(_region_pixels(page, rects[1]))


def test_highlight_single_rect_argument() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    page = doc[0]
    page.add_highlight_annot((20, 20, 80, 40))
    assert page.annots()[0]["type"] == "Highlight"
    assert _has_yellowish(_region_pixels(page, pylopdf.Rect(20, 20, 80, 40)))


def test_highlight_on_rotated_page_uses_display_coordinates() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)
    page = doc[0]
    page.set_rotation(90)  # Display coordinates are 200 x 100.
    target = pylopdf.Rect(120, 30, 180, 70)
    page.add_highlight_annot(target)
    assert _has_yellowish(_region_pixels(page, target))
    assert not _has_yellowish(_region_pixels(page, pylopdf.Rect(10, 10, 60, 60)))
    # Readback also uses display coordinates.
    annot_rect = page.annots()[0]["rect"]
    assert abs(annot_rect.x0 - target.x0) < 1
    assert abs(annot_rect.y1 - target.y1) < 1


def test_add_link_annot_reads_back() -> None:
    doc = pylopdf.open(stream=build_pdf(["Visit example"]))
    page = doc[0]
    hits = page.search_for("example")
    page.add_link_annot(hits[0], "https://example.com/")
    reopened = pylopdf.open(stream=doc.tobytes())
    annots = reopened[0].annots()
    assert len(annots) == 1
    assert annots[0]["type"] == "Link"
    assert annots[0]["uri"] == "https://example.com/"


def test_copy_page_detaches_shared_indirect_annots_array() -> None:
    """Detach a shared Annots array before annotating a copied page."""
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots 4 0 R >>",
            4: "[5 0 R]",
            5: (
                "<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /Border [0 0 0] "
                "/A << /S /URI /URI (https://a.example) >> >>"
            ),
        }
    )
    doc = pylopdf.open(stream=pdf)
    doc.copy_page(0)

    doc[1].add_link_annot((20, 20, 30, 30), "https://b.example")

    assert [len(page.annots()) for page in doc] == [1, 2]
    assert [annot["uri"] for annot in doc[0].annots()] == ["https://a.example"]


def test_annots_empty_on_fresh_page() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    assert doc[0].annots() == []


def test_annots_rejects_bad_input() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    page = doc[0]
    with pytest.raises(ValueError, match="rects"):
        page.add_highlight_annot([])
    with pytest.raises(ValueError, match="opacity"):
        page.add_highlight_annot((10, 10, 50, 20), opacity=0)
    with pytest.raises(ValueError, match="uri"):
        page.add_link_annot((10, 10, 50, 20), "")
