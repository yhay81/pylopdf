"""Tests for extracting page images with Page.get_images."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def _decode_png(data: bytes) -> tuple[int, int, int, bytes]:
    """Decode one non-interlaced 8-bit PNG for exact extraction assertions."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = height = color_type = 0
    compressed = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += length + 12
        if kind == b"IHDR":
            width, height, depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            assert (depth, compression, filtering, interlace) == (8, 0, 0, 0)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break

    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    encoded = zlib.decompress(compressed)
    previous = bytearray(stride)
    pixels = bytearray()
    offset = 0

    def paeth(left: int, above: int, upper_left: int) -> int:
        estimate = left + above - upper_left
        distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
        return (left, above, upper_left)[distances.index(min(distances))]

    for _ in range(height):
        filter_type = encoded[offset]
        row = bytearray(encoded[offset + 1 : offset + 1 + stride])
        offset += stride + 1
        for index, value in enumerate(row):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            predictor = {
                0: 0,
                1: left,
                2: above,
                3: (left + above) // 2,
                4: paeth(left, above, upper_left),
            }[filter_type]
            row[index] = (value + predictor) & 0xFF
        pixels.extend(row)
        previous = row
    return width, height, color_type, bytes(pixels)


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


def _two_image_pdf(first: bytes, second: bytes) -> bytes:
    """Build one page drawing a passthrough JPEG then a Flate RGB raster."""
    draw = b"q 1 0 0 1 0 0 cm /Im0 Do Q\nq 1 0 0 1 1 1 cm /Im1 Do Q"
    first_image = (
        (
            "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
            f"/Length {len(first)} >>\nstream\n"
        ).encode()
        + first
        + b"\nendstream"
    )
    second_image = (
        (
            "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(second)} >>\nstream\n"
        ).encode()
        + second
        + b"\nendstream"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] "
                "/Resources << /XObject << /Im0 5 0 R /Im1 6 0 R >> >> "
                "/Contents 4 0 R >>"
            ),
            4: f"<< /Length {len(draw)} >>\nstream\n".encode() + draw + b"\nendstream",
            5: first_image,
            6: second_image,
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


def test_image_extraction_streams_rgb_alpha_without_changing_pixels() -> None:
    """Keep straight RGBA samples exact while avoiding a complete interleaved copy."""
    source = pylopdf.Document()
    source.new_page(width=2, height=1)
    pixmap = source[0].get_pixmap(background=(10, 20, 30, 40))

    target = pylopdf.Document()
    target.new_page(width=2, height=1)
    target[0].insert_image((0, 0, 2, 1), pixmap=pixmap)
    extracted = target[0].get_images()

    assert len(extracted) == 1
    assert extracted[0]["ext"] == "png"
    assert _decode_png(extracted[0]["image"]) == (2, 1, 6, pixmap.samples)


def test_image_extraction_streams_gray_alpha_without_changing_pixels() -> None:
    """Keep grayscale soft-mask samples exact through bounded PNG streaming."""
    gray = zlib.compress(bytes([10, 20]))
    alpha = zlib.compress(bytes([30, 40]))
    draw = b"q 2 0 0 1 0 0 cm /Im0 Do Q"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 2 1] "
                "/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>"
            ),
            4: f"<< /Length {len(draw)} >>\nstream\n".encode() + draw + b"\nendstream",
            5: (
                "<< /Type /XObject /Subtype /Image /Width 2 /Height 1 "
                "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                f"/SMask 6 0 R /Length {len(gray)} >>\nstream\n"
            ).encode()
            + gray
            + b"\nendstream",
            6: (
                "<< /Type /XObject /Subtype /Image /Width 2 /Height 1 "
                "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                f"/Length {len(alpha)} >>\nstream\n"
            ).encode()
            + alpha
            + b"\nendstream",
        }
    )

    extracted = pylopdf.open(stream=pdf)[0].get_images()

    assert len(extracted) == 1
    assert _decode_png(extracted[0]["image"]) == (2, 1, 4, bytes([10, 30, 20, 40]))


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


def test_image_extraction_preserves_flate_wrapped_jpeg_passthrough() -> None:
    """Chunked Flate decoding returns the exact nested JPEG payload."""
    jpeg = b"\xff\xd8\xfffallible-passthrough"
    doc = pylopdf.open(stream=_image_pdf(zlib.compress(jpeg), filters="[/FlateDecode /DCTDecode]"))

    image = doc[0].get_images()[0]

    assert image["ext"] == "jpeg"
    assert image["image"] == jpeg


def test_image_extraction_bounds_png_encoding_to_remaining_output() -> None:
    """PNG fallback writes directly into the remaining page payload budget."""
    almost_full_jpeg = b"\xff\xd8\xff" + bytes(64 * 1024 * 1024 - 19)
    flate_rgb = zlib.compress(b"\x00\x80\xff")
    doc = pylopdf.open(stream=_two_image_pdf(almost_full_jpeg, flate_rgb))

    with pytest.raises(pylopdf.PdfError, match="67108864-byte output safety limit"):
        doc[0].get_images()
