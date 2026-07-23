"""Tests for Page.insert_image, show_pdf_page, and replace_text.

Placement is verified end to end through rendered pixel colors, exposing any
failure in coordinate transforms, rotation, or XObject registration.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"

RED = (255, 0, 0)
GREEN = (0, 128, 0)
WHITE = (255, 255, 255)


def _solid_png(width: int, height: int, rgb: tuple[int, int, int], alpha: int | None = None) -> bytes:
    """Build a solid PNG, using RGBA with alpha and RGB when alpha is None."""
    if alpha is None:
        color_type, px = 2, bytes(rgb)
    else:
        color_type, px = 6, bytes((*rgb, alpha))
    raw = b"".join(b"\x00" + px * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int]:
    """Return RGB at display point ``(x, y)`` after rendering on white."""
    pix = page.get_pixmap(background=WHITE)
    offset = y * pix.stride + x * 4
    r, g, b = pix.samples[offset : offset + 3]
    return (r, g, b)


def _new_page_doc(width: float = 200, height: float = 100) -> pylopdf.Document:
    doc = pylopdf.Document()
    doc.new_page(width=width, height=height)
    return doc


def test_insert_png_draws_at_rect() -> None:
    doc = _new_page_doc()
    page = doc[0]
    page.insert_image((20, 30, 60, 70), stream=_solid_png(4, 4, RED))
    assert _pixel(page, 40, 50) == RED  # Center of rect.
    assert _pixel(page, 10, 50) == WHITE  # Outside rect.
    assert _pixel(page, 40, 20) == WHITE


def test_insert_png_alpha_is_preserved() -> None:
    doc = _new_page_doc()
    page = doc[0]
    # Fully transparent green leaves white; fully opaque green renders green.
    page.insert_image((10, 10, 50, 50), stream=_solid_png(2, 2, GREEN, alpha=0))
    page.insert_image((60, 10, 100, 50), stream=_solid_png(2, 2, GREEN, alpha=255))
    assert _pixel(page, 30, 30) == WHITE
    assert _pixel(page, 80, 30) == GREEN


def test_insert_image_keep_proportion_centers() -> None:
    doc = _new_page_doc()
    page = doc[0]
    # A square in a 40×20 rect fits as a centered 20×20 image.
    page.insert_image((100, 40, 140, 60), stream=_solid_png(2, 2, RED))
    assert _pixel(page, 120, 50) == RED  # Center.
    assert _pixel(page, 103, 50) == WHITE  # Side margin.
    assert _pixel(page, 137, 50) == WHITE


def test_insert_image_fills_rect_without_keep_proportion() -> None:
    doc = _new_page_doc()
    page = doc[0]
    page.insert_image((100, 40, 140, 60), stream=_solid_png(2, 2, RED), keep_proportion=False)
    assert _pixel(page, 103, 50) == RED
    assert _pixel(page, 137, 50) == RED


def test_insert_image_on_rotated_page_uses_display_coordinates() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)  # Portrait page.
    page = doc[0]
    page.set_rotation(90)  # Display is 200×100 landscape.
    assert page.rect.width == 200
    page.insert_image((150, 25, 190, 75), stream=_solid_png(2, 2, RED))
    # Rendering uses display space, so the image appears at the specified point.
    assert _pixel(page, 170, 50) == RED
    assert _pixel(page, 50, 50) == WHITE


def test_insert_jpeg_roundtrips_bytes_exactly() -> None:
    src = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    jpegs = [i for i in src[0].get_images() if i["ext"] == "jpeg"]
    assert jpegs
    original = jpegs[0]["image"]

    doc = _new_page_doc(400, 400)
    page = doc[0]
    page.insert_image((0, 0, 400, 400), stream=original)
    extracted = page.get_images()
    assert len(extracted) == 1
    assert extracted[0]["ext"] == "jpeg"
    assert extracted[0]["image"] == original  # DCTDecode passthrough round trip.


def test_insert_image_survives_save_roundtrip() -> None:
    doc = _new_page_doc()
    doc[0].insert_image((20, 30, 60, 70), stream=_solid_png(4, 4, RED))
    reopened = pylopdf.open(stream=doc.tobytes())
    assert _pixel(reopened[0], 40, 50) == RED


def test_insert_image_rejects_bad_input() -> None:
    doc = _new_page_doc()
    page = doc[0]
    with pytest.raises(ValueError, match="filename or stream"):
        page.insert_image((0, 0, 10, 10))
    with pytest.raises(ValueError, match="rect"):
        page.insert_image((50, 50, 10, 10), stream=_solid_png(1, 1, RED))
    with pytest.raises(pylopdf.PdfError, match="image format"):
        page.insert_image((0, 0, 10, 10), stream=b"not an image")
    truncated_jpeg = bytes([0xFF, 0xD8, 0xFF, 0xC0, 0, 8, 8, 0, 1, 0, 1])
    with pytest.raises(pylopdf.PdfError, match="JPEG"):
        page.insert_image((0, 0, 10, 10), stream=truncated_jpeg)


def test_show_pdf_page_overlays_vector_text() -> None:
    stamp = pylopdf.open(stream=build_pdf(["STAMPTEXT"]))
    doc = pylopdf.Document()
    doc.new_page()  # A4-sized default page.
    page = doc[0]
    page.show_pdf_page((50, 50, 550, 700), stamp)
    # Text remains vector/extractable after conversion to a Form XObject.
    assert "STAMPTEXT" in page.get_text()


def test_show_pdf_page_draws_at_rect() -> None:
    # Stamp source: a red image covering a 100×100 page.
    stamp = _new_page_doc(100, 100)
    stamp[0].insert_image((0, 0, 100, 100), stream=_solid_png(2, 2, RED), keep_proportion=False)

    doc = _new_page_doc(200, 100)
    page = doc[0]
    page.show_pdf_page((120, 20, 180, 80), stamp)
    assert _pixel(page, 150, 50) == RED  # Center of rect.
    assert _pixel(page, 60, 50) == WHITE  # Outside rect.


def test_show_pdf_page_scales_source_crop_into_rect() -> None:
    # A 50×100 portrait stamp fits centered at width 50 in a 100×100 rect.
    stamp = _new_page_doc(50, 100)
    stamp[0].insert_image((0, 0, 50, 100), stream=_solid_png(2, 2, RED), keep_proportion=False)

    doc = _new_page_doc(200, 120)
    page = doc[0]
    page.show_pdf_page((50, 10, 150, 110), stamp)
    assert _pixel(page, 100, 60) == RED  # Center band is red.
    assert _pixel(page, 60, 60) == WHITE  # Sides are margins.
    assert _pixel(page, 140, 60) == WHITE


def test_show_pdf_page_rejects_same_document() -> None:
    doc = _new_page_doc()
    with pytest.raises(ValueError, match="same document"):
        doc[0].show_pdf_page((0, 0, 50, 50), doc)


def test_show_pdf_page_does_not_keep_unreachable_source_data() -> None:
    """Do not leak attachments unreachable from the imported Form."""
    secret = b"SECRET-UNREFERENCED-STAMP-ATTACHMENT-91af"
    source = pylopdf.Document()
    source.new_page(width=100, height=100)
    source.embfile_add("secret.txt", secret)
    target = pylopdf.Document()
    page = target.new_page(width=100, height=100)

    page.show_pdf_page((0, 0, 100, 100), source)

    assert target.embfile_names() == []
    assert secret not in target.tobytes()


def test_show_pdf_page_accepts_negative_pno() -> None:
    stamp = pylopdf.open(stream=build_pdf(["FIRST", "LAST"]))
    doc = pylopdf.Document()
    doc.new_page()
    page = doc[0]
    page.show_pdf_page((50, 50, 550, 700), stamp, pno=-1)
    assert "LAST" in page.get_text()
    assert "FIRST" not in page.get_text()


def test_show_pdf_page_underlay_draws_below() -> None:
    # Opaque green overlay wins over a red underlay.
    red_stamp = _new_page_doc(100, 100)
    red_stamp[0].insert_image((0, 0, 100, 100), stream=_solid_png(2, 2, RED), keep_proportion=False)

    doc = _new_page_doc(100, 100)
    page = doc[0]
    page.insert_image((0, 0, 100, 100), stream=_solid_png(2, 2, GREEN), keep_proportion=False)
    page.show_pdf_page((0, 0, 100, 100), red_stamp, overlay=False)
    assert _pixel(page, 50, 50) == GREEN


def test_insert_text_is_extractable_at_position() -> None:
    doc = pylopdf.Document()
    doc.new_page()  # A4-like page also verifies drawing without Resources.
    page = doc[0]
    page.insert_text((50, 100), "Confidential", fontsize=12)
    words = page.get_text("words")
    assert [w[4] for w in words] == ["Confidential"]
    x0, y0, _, y1 = words[0][:4]
    assert abs(x0 - 50) < 2  # Baseline left equals the requested x.
    assert y0 < 100 < y1  # Requested baseline y lies inside the bbox.


def test_insert_text_multiline_stacks_downward() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    page = doc[0]
    page.insert_text((50, 100), "First\nSecond", fontsize=10)
    words = {w[4]: w for w in page.get_text("words")}
    assert words["Second"][1] > words["First"][1]  # Second line is lower.


def test_insert_text_on_rotated_page_reads_upright() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)
    page = doc[0]
    page.set_rotation(90)
    page.insert_text((20, 50), "Rotated")
    pix = page.get_pixmap(background=WHITE)
    assert (pix.width, pix.height) == (200, 100)  # Rendering uses display space.
    # Extraction/search share rendering's rotation-resolved display space.
    words = page.get_text("words")
    assert [w[4] for w in words] == ["Rotated"]
    assert abs(words[0][0] - 20) < 2  # Requested display x.
    assert words[0][1] < 50 < words[0][3]  # Baseline display y lies in bbox.
    assert page.search_for("Rotated")


def test_get_images_bbox_on_rotated_page_is_display_space() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)
    page = doc[0]
    page.set_rotation(90)
    page.insert_image((150, 25, 190, 75), stream=_solid_png(2, 2, RED), keep_proportion=False)
    bbox = page.get_images()[0]["bbox"]
    assert abs(bbox.x0 - 150) < 1
    assert abs(bbox.y0 - 25) < 1
    assert abs(bbox.x1 - 190) < 1
    assert abs(bbox.y1 - 75) < 1


def test_insert_text_survives_save_roundtrip() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc[0].insert_text((72, 72), "Persistent")
    reopened = pylopdf.open(stream=doc.tobytes())
    assert "Persistent" in reopened[0].get_text()


def test_insert_text_page_numbering_recipe() -> None:
    # Exact page-number recipe published in the README.
    doc = pylopdf.Document()
    for _ in range(3):
        doc.new_page()
    for i, page in enumerate(doc):
        page.insert_text((page.rect.width - 90, page.rect.height - 30), f"Page {i + 1} / 3", fontsize=9)
    for i in range(3):
        assert f"Page {i + 1} / 3" in doc[i].get_text()


def test_insert_text_rejects_cjk_with_recipe_hint() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    with pytest.raises(ValueError, match="show_pdf_page"):
        doc[0].insert_text((50, 50), "社外秘")


def test_insert_text_rejects_unknown_font_and_bad_args() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    page = doc[0]
    with pytest.raises(ValueError, match="fontname"):
        page.insert_text((50, 50), "x", fontname="nosuch")
    with pytest.raises(ValueError, match="fontsize"):
        page.insert_text((50, 50), "x", fontsize=0)
    with pytest.raises(ValueError, match="color"):
        page.insert_text((50, 50), "x", color=(2.0, 0.0, 0.0))


def test_replace_text_replaces_and_counts() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    assert page.replace_text("PDF", "Cat") == 1
    text = page.get_text()
    assert "Hello Cat" in text
    assert "PDF" not in text


def test_replace_text_returns_zero_when_absent() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    assert doc[0].replace_text("XYZ", "abc") == 0


def test_replace_text_requires_needle() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    with pytest.raises(ValueError, match="search"):
        doc[0].replace_text("", "abc")
