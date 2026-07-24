"""Tests for Page.get_pixmap rendering and warning integration."""

from __future__ import annotations

import gc
import sysconfig
import warnings
from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def test_pixmap_dimensions_and_samples(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    pix = doc[0].get_pixmap()
    assert isinstance(pix, pylopdf.Pixmap)
    assert pix.width == 612
    assert pix.height == 792
    assert pix.n == 4
    assert pix.stride == pix.width * 4
    assert len(pix.samples) == pix.width * pix.height * 4


def test_pixmap_buffer_protocol_matches_build_abi(one_page_pdf: bytes) -> None:
    pix = pylopdf.open(stream=one_page_pdf)[0].get_pixmap()
    if sysconfig.get_config_var("Py_GIL_DISABLED") != 1:
        with pytest.raises(TypeError, match="bytes-like object"):
            memoryview(pix)  # type: ignore[arg-type]  # Buffer exists only in version-specific builds.
        return

    expected = pix.samples
    view = memoryview(pix)  # type: ignore[arg-type]  # Buffer exists only in version-specific builds.
    assert view.readonly
    assert view.format == "B"
    assert view.ndim == 1
    assert view.nbytes == len(expected)
    assert bytes(view) == expected
    with pytest.raises(TypeError, match="cannot modify read-only memory"):
        view[0] = 0
    del pix
    gc.collect()
    assert bytes(view) == expected


def test_pixmap_scale_and_dpi(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    assert doc[0].get_pixmap(scale=2.0).width == 612 * 2
    assert doc[0].get_pixmap(dpi=144).width == 612 * 2
    with pytest.raises(ValueError, match="cannot both be specified"):
        doc[0].get_pixmap(scale=2.0, dpi=144)


def test_pixmap_tobytes_is_png(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    pix = doc[0].get_pixmap()
    assert pix.tobytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_pixmap_background(one_page_pdf: bytes) -> None:
    """Use opaque pixels with a background and transparency by default."""
    doc = pylopdf.open(stream=one_page_pdf)
    transparent = doc[0].get_pixmap()
    white = doc[0].get_pixmap(background=(255, 255, 255))
    assert transparent.samples[3] == 0
    assert white.samples[:4] == b"\xff\xff\xff\xff"


def test_pixmap_matches_png_rendering(one_page_pdf: bytes) -> None:
    """Produce identical PNG pixels through Pixmap and render_page."""
    doc = pylopdf.open(stream=one_page_pdf)
    assert doc[0].get_pixmap().tobytes() == doc.render_page(0)


def test_pixmap_clip_matches_full_render_region(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    full = doc[0].get_pixmap()
    clipped = doc[0].get_pixmap(clip=(10, 20, 110, 70))

    assert (clipped.width, clipped.height) == (100, 50)
    expected = b"".join(full.samples[y * full.stride + 10 * 4 : y * full.stride + 110 * 4] for y in range(20, 70))
    assert clipped.samples == expected


def test_pixmap_clip_rounds_outward_and_intersects_page(one_page_pdf: bytes) -> None:
    page = pylopdf.open(stream=one_page_pdf)[0]

    fractional = page.get_pixmap(scale=2, clip=(0.25, 0.25, 1.25, 1.25))
    assert (fractional.width, fractional.height) == (3, 3)

    clamped = page.get_pixmap(clip=(-10, -20, 50, 30))
    assert (clamped.width, clamped.height) == (50, 30)


def test_pixmap_clip_rejects_invalid_or_non_intersecting_rect(one_page_pdf: bytes) -> None:
    page = pylopdf.open(stream=one_page_pdf)[0]
    with pytest.raises(ValueError, match="clip must be a finite rect"):
        page.get_pixmap(clip=(10, 10, 10, 20))
    with pytest.raises(pylopdf.PdfError, match="does not intersect"):
        page.get_pixmap(clip=(700, 800, 710, 810))


def test_pixmap_clip_uses_rotated_display_coordinates(one_page_pdf: bytes) -> None:
    page = pylopdf.open(stream=one_page_pdf)[0]
    page.set_rotation(90)
    full = page.get_pixmap()
    clipped = page.get_pixmap(clip=(0, 0, 100, 50))

    assert (full.width, full.height) == (792, 612)
    assert (clipped.width, clipped.height) == (100, 50)
    assert clipped.samples == b"".join(full.samples[y * full.stride : y * full.stride + 400] for y in range(50))


def build_broken_image_pdf() -> bytes:
    """Build a one-page PDF that triggers ImageDecodeFailure on a broken DCT."""
    stream = "q 100 0 0 100 100 600 cm /Im0 Do Q"
    broken_jpeg = "\xff\xd8\xff"  # A truncated JPEG containing only SOI bytes.
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /XObject << /Im0 5 0 R >> >> >>",
        4: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        5: "<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceRGB"
        " /BitsPerComponent 8 /Filter /DCTDecode /Length 3 >>\nstream\n" + broken_jpeg + "\nendstream",
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in sorted(objects):
        out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return bytes(out)


def test_broken_image_emits_warning() -> None:
    doc = pylopdf.open(stream=build_broken_image_pdf())
    with pytest.warns(pylopdf.PylopdfWarning, match="decode"):
        doc.render_page(0)


def test_clean_render_emits_no_warning(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        doc.render_page(0)
        doc.get_page_text(0)


def test_warnings_do_not_leak_between_operations() -> None:
    """Do not leak a broken-PDF warning into the next clean operation."""
    broken = pylopdf.open(stream=build_broken_image_pdf())
    with pytest.warns(pylopdf.PylopdfWarning):
        broken.render_page(0)
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        broken.get_page_text(0)


def test_corpus_render_has_no_warnings() -> None:
    """Render a regular corpus PDF without warnings."""
    doc = pylopdf.open(ASSETS / "usrguide.pdf")
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        doc[0].get_pixmap(scale=0.5)


def test_multi_page_pixmap() -> None:
    doc = pylopdf.open(stream=build_pdf(["One", "Two"], page_size=(200, 100)))
    pix = doc[1].get_pixmap()
    assert (pix.width, pix.height) == (200, 100)
