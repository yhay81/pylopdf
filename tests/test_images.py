"""Tests for extracting page images with Page.get_images."""

from __future__ import annotations

from pathlib import Path

from conftest import build_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def test_text_only_page_has_no_images() -> None:
    doc = pylopdf.open(stream=build_pdf(["No images here"]))
    assert doc[0].get_images() == []


def test_scanned_page_yields_png() -> None:
    """Decode a CCITT-scanned patent page as PNG."""
    doc = pylopdf.open(ASSETS / "patent-us223898.pdf")
    images = doc[0].get_images()
    assert len(images) >= 1
    image = images[0]
    assert image["ext"] == "png"
    assert image["image"].startswith(b"\x89PNG\r\n\x1a\n")
    assert image["width"] > 100
    assert image["height"] > 100


def test_jpeg_passthrough() -> None:
    """Return original JPEG bytes for an image using only DCTDecode."""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    images = doc[0].get_images()
    jpegs = [i for i in images if i["ext"] == "jpeg"]
    assert jpegs, "DCT image was not returned as JPEG"
    for image in jpegs:
        assert image["image"].startswith(b"\xff\xd8\xff")


def test_image_bbox_is_on_page() -> None:
    doc = pylopdf.open(ASSETS / "patent-us223898.pdf")
    page = doc[0]
    page_rect = page.rect
    for image in page.get_images():
        bbox = image["bbox"]
        assert isinstance(bbox, pylopdf.Rect)
        assert bbox.x0 < bbox.x1
        assert bbox.y0 < bbox.y1
        assert bbox.x1 <= page_rect.x1 + 1
        assert bbox.y1 <= page_rect.y1 + 1
