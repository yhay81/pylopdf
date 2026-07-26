"""Tests for extracting page images with Page.get_images."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def _image_pdf(
    data: bytes,
    *,
    width: int = 1,
    height: int = 1,
    placements: int = 1,
    filters: str = "/DCTDecode",
) -> bytes:
    """Build one page that repeatedly draws one shared raster XObject."""
    draw = b"q 1 0 0 1 0 0 cm /Im0 Do Q\n" * placements
    image = (
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter {filters} "
            f"/Length {len(data)} >>\nstream\n"
        ).encode()
        + data
        + b"\nendstream"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] "
                "/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>"
            ),
            4: f"<< /Length {len(draw)} >>\nstream\n".encode() + draw + b"endstream",
            5: image,
        }
    )


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


def test_image_extraction_rejects_excessive_placements_atomically() -> None:
    """Repeated use of one tiny source cannot multiply into an unbounded list."""
    doc = pylopdf.open(stream=_image_pdf(b"\xff\xd8\xff", placements=4_097))

    with pytest.raises(pylopdf.PdfError, match="4096-placement safety limit"):
        doc[0].get_images()
    assert doc.page_count == 1


def test_image_extraction_rejects_pixel_budget_before_decode() -> None:
    """Declared dimensions are bounded before decoding the raster payload."""
    doc = pylopdf.open(stream=_image_pdf(b"\xff\xd8\xff", width=8_001, height=8_000))

    with pytest.raises(pylopdf.PdfError, match="64000000-pixel safety limit"):
        doc[0].get_images()


def test_image_extraction_rejects_cumulative_output_bytes() -> None:
    """Small repeated placements cannot duplicate more than 64 MiB of payload."""
    one_mibibyte_jpeg = b"\xff\xd8\xff" + bytes(1024 * 1024 - 3)
    doc = pylopdf.open(stream=_image_pdf(one_mibibyte_jpeg, placements=65))

    with pytest.raises(pylopdf.PdfError, match="67108864-byte output safety limit"):
        doc[0].get_images()


def test_image_extraction_bounds_flate_wrapped_jpeg_passthrough() -> None:
    """The Flate-to-DCT fast path stops before expanding an oversized payload."""
    oversized_jpeg = b"\xff\xd8\xff" + bytes(64 * 1024 * 1024 - 2)
    compressed = zlib.compress(oversized_jpeg)
    doc = pylopdf.open(stream=_image_pdf(compressed, filters="[/FlateDecode /DCTDecode]"))

    with pytest.raises(pylopdf.PdfError, match="67108864-byte output safety limit"):
        doc[0].get_images()
