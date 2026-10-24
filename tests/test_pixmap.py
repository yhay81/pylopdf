"""Tests for Page.pixmap rendering and warning integration."""

from __future__ import annotations

import gc
import os
import stat
import warnings
from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def test_pixmap_dimensions_and_samples(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    pix = doc.pages[0].pixmap(alpha=True)
    assert isinstance(pix, pylopdf.Pixmap)
    assert pix.width == 612
    assert pix.height == 792
    assert pix.channels == 4
    assert pix.stride == pix.width * 4
    assert len(bytes(pix)) == pix.width * pix.height * 4


def test_pixmap_exposes_a_zero_copy_read_only_buffer(one_page_pdf: bytes) -> None:
    # Every wheel, the abi3-py311 wheels included, implements the buffer protocol.
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(alpha=True)
    expected = bytes(pix)
    view = memoryview(pix)
    assert view.readonly
    assert view.format == "B"
    assert (view.ndim, view.shape, view.strides) == (3, (792, 612, 4), (612 * 4, 4, 1))
    assert view.c_contiguous
    assert view.nbytes == len(expected)
    assert bytes(view) == expected
    assert view[1, 2, 3] == expected[612 * 4 + 2 * 4 + 3]
    with pytest.raises(TypeError, match="cannot modify read-only memory"):
        view[0, 0, 0] = 0
    del pix
    gc.collect()
    assert bytes(view) == expected


def test_a_gray_pixmap_buffer_has_two_dimensions(one_page_pdf: bytes) -> None:
    page = pylopdf.open(one_page_pdf).pages[0]

    gray = memoryview(page.pixmap(colorspace="gray"))
    gray_alpha = memoryview(page.pixmap(colorspace=pylopdf.Colorspace.GRAY, alpha=True))

    assert (gray.shape, gray.strides) == ((792, 612), (612, 1))
    assert gray_alpha.shape == (792, 612, 2)


def test_pixmap_tobytes_bounds_encoded_output(one_page_pdf: bytes) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(alpha=True)
    expected = pix.to_bytes(max_size=None)

    assert pix.to_bytes(max_size=len(expected)) == expected
    with pytest.raises(pylopdf.LimitError) as caught:
        pix.to_bytes(max_size=len(expected) - 1)
    assert caught.value.code == "pixmap_output_size"


@pytest.mark.parametrize("max_size", [0, -1, True, 1.5])
def test_pixmap_tobytes_validates_output_limit(one_page_pdf: bytes, max_size: object) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(alpha=True)

    with pytest.raises((TypeError, ValueError), match="max_size"):
        pix.to_bytes(max_size=max_size)  # ty: ignore[invalid-argument-type]


def test_pixmap_save_writes_png_to_pathlike(one_page_pdf: bytes, tmp_path: Path) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(alpha=True)
    target = tmp_path / "rendered page.png"
    target.write_bytes(b"existing output")

    pix.save(target)
    assert target.read_bytes() == pix.to_bytes()
    assert list(tmp_path.glob(".pylopdf-*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows mode bits do not expose the POSIX contract")
def test_pixmap_save_preserves_existing_file_mode(one_page_pdf: bytes, tmp_path: Path) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(alpha=True)
    target = tmp_path / "page.png"
    target.write_bytes(b"existing output")
    target.chmod(0o640)

    pix.save(target)

    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs host configuration")
def test_pixmap_save_atomically_updates_a_symlink_target(one_page_pdf: bytes, tmp_path: Path) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(alpha=True)
    target = tmp_path / "target.png"
    target.write_bytes(b"existing output")
    link = tmp_path / "page.png"
    link.symlink_to(target.name)

    pix.save(link)

    assert link.is_symlink()
    assert target.read_bytes() == pix.to_bytes()


def test_pixmap_save_cleans_up_after_replace_failure(one_page_pdf: bytes, tmp_path: Path) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(alpha=True)
    target = tmp_path / "page.png"
    target.mkdir()
    marker = target / "existing"
    marker.write_bytes(b"preserved")

    with pytest.raises(pylopdf.PdfError, match="failed to save the image"):
        pix.save(target)

    assert marker.read_bytes() == b"preserved"
    assert list(tmp_path.glob(".pylopdf-*.tmp")) == []


def test_pixmap_save_reports_io_error(one_page_pdf: bytes, tmp_path: Path) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(alpha=True)
    target = tmp_path / "missing" / "page.png"

    with pytest.raises(pylopdf.PdfError, match="failed to save the image"):
        pix.save(target)


def test_pixmap_save_takes_the_format_from_the_extension(one_page_pdf: bytes, tmp_path: Path) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap()

    pix.save(tmp_path / "page.JPG", quality=70)
    pix.save(tmp_path / "page.jpeg")
    pix.save(tmp_path / "page.data", format="png")

    assert (tmp_path / "page.JPG").read_bytes() == pix.to_bytes("jpeg", quality=70)
    assert (tmp_path / "page.jpeg").read_bytes() == pix.to_bytes("jpeg")
    assert (tmp_path / "page.data").read_bytes() == pix.to_bytes()
    with pytest.raises(ValueError, match="cannot tell the image format"):
        pix.save(tmp_path / "page.tiff")
    with pytest.raises(ValueError, match="format must be 'png' or 'jpeg'"):
        pix.save(tmp_path / "page.png", format="jpg")  # ty: ignore[invalid-argument-type]
    assert not (tmp_path / "page.tiff").exists()


def test_jpeg_output_refuses_alpha(one_page_pdf: bytes, tmp_path: Path) -> None:
    page = pylopdf.open(one_page_pdf).pages[0]
    pix = page.pixmap(alpha=True)

    with pytest.raises(ValueError, match="JPEG has no alpha channel"):
        pix.to_bytes("jpeg")
    with pytest.raises(ValueError, match="JPEG has no alpha channel"):
        pix.save(tmp_path / "page.jpg")
    with pytest.raises(ValueError, match="JPEG has no alpha channel"):
        page.document.render(alpha=True, format="jpeg")


@pytest.mark.parametrize("quality", [0, 101, True, 9.5, "high"])
def test_quality_must_be_an_integer_from_1_through_100(one_page_pdf: bytes, quality: object) -> None:
    page = pylopdf.open(one_page_pdf).pages[0]

    with pytest.raises((TypeError, ValueError), match="quality must be an integer from 1 through 100"):
        page.pixmap().to_bytes("jpeg", quality=quality)  # ty: ignore[invalid-argument-type]
    with pytest.raises((TypeError, ValueError), match="quality must be an integer from 1 through 100"):
        page.document.render(format="jpeg", quality=quality)  # ty: ignore[invalid-argument-type]


def _jpeg_size(data: bytes) -> tuple[int, int, int]:
    """Return a baseline JPEG's width, height, and components from its SOF0 marker."""
    offset = data.index(b"\xff\xc0")
    height, width = int.from_bytes(data[offset + 5 : offset + 7]), int.from_bytes(data[offset + 7 : offset + 9])
    return width, height, data[offset + 9]


def test_jpeg_output_keeps_size_and_components(one_page_pdf: bytes) -> None:
    page = pylopdf.open(one_page_pdf).pages[0]

    assert _jpeg_size(page.pixmap(dpi=36).to_bytes("jpeg")) == (306, 396, 3)
    assert _jpeg_size(page.pixmap(dpi=36, colorspace="gray").to_bytes("jpeg")) == (306, 396, 1)
    low, high = (page.pixmap(dpi=36).to_bytes("jpeg", quality=quality) for quality in (10, 100))
    assert len(low) < len(high)
    batch = page.document.render(dpi=36, format="jpeg", quality=10, colorspace="gray")
    assert batch == [page.pixmap(dpi=36, colorspace="gray").to_bytes("jpeg", quality=10)]


def test_gray_rendering_converts_rgb_with_bt601_weights() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((30, 10))
    with page.draw() as canvas:
        canvas.rect((0, 0, 10, 10), fill=pylopdf.Color(1, 0, 0))
        canvas.rect((10, 0, 20, 10), fill=pylopdf.Color(0, 1, 0))
        canvas.rect((20, 0, 30, 10), fill=pylopdf.Color(0, 0, 1))

    gray = page.pixmap(colorspace="gray")
    rgb = page.pixmap()

    assert (gray.channels, gray.colorspace, gray.stride) == (1, pylopdf.Colorspace.GRAY, 30)
    assert (rgb.colorspace, repr(gray)) == (pylopdf.Colorspace.RGB, "<Pixmap 30x10 gray>")
    row = bytes(gray)[5 * 30 : 6 * 30]
    assert (row[5], row[15], row[25]) == (77, 149, 29)
    gray_alpha = page.pixmap(colorspace="gray", alpha=True)
    assert gray_alpha.channels == 2
    assert repr(gray_alpha) == "<Pixmap 30x10 gray alpha>"
    assert bytes(gray_alpha)[: 2 * 30 : 2] == bytes(gray)[:30]
    with pytest.raises(ValueError, match="colorspace must be 'gray' or 'rgb'"):
        page.pixmap(colorspace="cmyk")  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("name", ["usrguide.pdf", "pdfium-smask-blend.pdf", "wdl6812-manuscript.pdf"])
def test_every_layout_converts_the_same_raster(name: str) -> None:
    page = pylopdf.open(Path(__file__).parent / "assets" / "real_world" / name).pages[0]

    def luma(data: bytes, channels: int) -> bytes:
        return bytes(
            (77 * data[i] + 150 * data[i + 1] + 29 * data[i + 2] + 128) >> 8 for i in range(0, len(data), channels)
        )

    rgb = bytes(page.pixmap(dpi=36))
    opaque = bytes(page.pixmap(dpi=36, alpha=True, background=(255, 255, 255, 255)))
    assert rgb == bytes(byte for index, byte in enumerate(opaque) if index % 4 != 3)
    assert bytes(page.pixmap(dpi=36, colorspace="gray")) == luma(rgb, 3)
    rgba = bytes(page.pixmap(dpi=36, alpha=True))
    gray_alpha = bytes(page.pixmap(dpi=36, alpha=True, colorspace="gray"))
    assert gray_alpha[0::2] == luma(rgba, 4)
    assert gray_alpha[1::2] == rgba[3::4]


def test_from_array_copies_the_pixels_of_a_buffer() -> None:
    pixels = bytes(range(2 * 3 * 4))

    pix = pylopdf.Pixmap.from_array(memoryview(pixels).cast("B", (2, 3, 4)))

    assert (pix.width, pix.height, pix.channels, pix.alpha, pix.colorspace) == (3, 2, 4, True, "rgb")
    assert bytes(pix) == pixels
    gray = pylopdf.Pixmap.from_array(memoryview(bytes(6)).cast("B", (2, 3)))
    assert (gray.width, gray.height, gray.channels, gray.colorspace) == (3, 2, 1, "gray")
    gray_alpha = pylopdf.Pixmap.from_array(memoryview(bytes(12)).cast("B", (2, 3, 2)))
    assert (gray_alpha.channels, gray_alpha.alpha) == (2, True)
    assert pylopdf.Pixmap.from_array(memoryview(bytes(18)).cast("B", (2, 3, 3))).alpha is False


def test_from_array_round_trips_a_rendered_page(one_page_pdf: bytes) -> None:
    pix = pylopdf.open(one_page_pdf).pages[0].pixmap(dpi=24, alpha=True)

    copy = pylopdf.Pixmap.from_array(pix)

    assert (copy.width, copy.height, copy.channels, bytes(copy)) == (pix.width, pix.height, 4, bytes(pix))
    assert copy.to_bytes() == pix.to_bytes()


@pytest.mark.parametrize(
    ("array", "error", "message"),
    [
        (b"\x00" * 12, ValueError, r"shaped \(height, width\)"),
        (memoryview(bytes(10)).cast("B", (2, 1, 5)), ValueError, "1 to 4 channels"),
        (memoryview(bytes(70000)).cast("B", (1, 70000)), ValueError, "1 to 65535 pixels a side"),
        (memoryview(bytes(8)).cast("i"), TypeError, "unsigned bytes"),
        ([1, 2, 3], TypeError, "unsigned bytes"),
    ],
)
def test_from_array_refuses_other_shapes_and_types(array: object, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        pylopdf.Pixmap.from_array(array)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("channels", "colorspace", "alpha"), [(1, "DeviceGray", False), (2, "DeviceGray", True), (4, "DeviceRGB", True)]
)
def test_inserted_gray_and_alpha_pixmaps_keep_their_layout(
    channels: int,
    colorspace: str,
    alpha: bool,  # noqa: FBT001 - parametrized.
) -> None:
    samples = bytes(value % 256 for value in range(4 * 3 * channels))
    pix = pylopdf.Pixmap.from_array(memoryview(samples).cast("B", (3, 4, channels)))
    doc = pylopdf.Document()
    page = doc.pages.new((40, 30))

    with page.draw() as canvas:
        canvas.image(page.rect, pix)
    xref = page.content(text_runs=False, paths=False, annotations=False).images[-1].xref

    image = doc.images[xref]
    assert (image.width, image.height, image.colorspace) == (4, 3, colorspace)
    assert (image.soft_mask is not None) is alpha
    assert page.content(text_runs=False, paths=False).images[0].xref == xref


def test_pixmap_background(one_page_pdf: bytes) -> None:
    """Use opaque pixels with a background and transparency by default."""
    doc = pylopdf.open(one_page_pdf)
    transparent = doc.pages[0].pixmap(alpha=True)
    white = doc.pages[0].pixmap(background=(255, 255, 255), alpha=True)
    assert bytes(transparent)[3] == 0
    assert bytes(white)[:4] == b"\xff\xff\xff\xff"


def test_pixmap_matches_png_rendering(one_page_pdf: bytes) -> None:
    """Produce identical PNG pixels through Pixmap and render_page."""
    doc = pylopdf.open(one_page_pdf)
    assert doc.pages[0].pixmap(alpha=True).to_bytes() == doc.pages[0].pixmap(alpha=True).to_bytes()


def test_pixmap_clip_matches_full_render_region(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    full = doc.pages[0].pixmap(alpha=True)
    clipped = doc.pages[0].pixmap(clip=(10, 20, 110, 70), alpha=True)

    assert (clipped.width, clipped.height) == (100, 50)
    expected = b"".join(bytes(full)[y * full.stride + 10 * 4 : y * full.stride + 110 * 4] for y in range(20, 70))
    assert bytes(clipped) == expected


def test_pixmap_clip_rounds_outward_and_intersects_page(one_page_pdf: bytes) -> None:
    page = pylopdf.open(one_page_pdf).pages[0]

    fractional = page.pixmap(matrix=pylopdf.Matrix(2, 2), clip=(0.25, 0.25, 1.25, 1.25), alpha=True)
    assert (fractional.width, fractional.height) == (3, 3)

    clamped = page.pixmap(clip=(-10, -20, 50, 30), alpha=True)
    assert (clamped.width, clamped.height) == (50, 30)


def test_pixmap_clip_rejects_invalid_or_non_intersecting_rect(one_page_pdf: bytes) -> None:
    page = pylopdf.open(one_page_pdf).pages[0]
    with pytest.raises(ValueError, match="clip must be a finite rect"):
        page.pixmap(clip=(10, 10, 10, 20), alpha=True)
    with pytest.raises(pylopdf.PdfError, match="does not intersect"):
        page.pixmap(clip=(700, 800, 710, 810), alpha=True)


def test_pixmap_clip_uses_rotated_display_coordinates(one_page_pdf: bytes) -> None:
    page = pylopdf.open(one_page_pdf).pages[0]
    page.rotation = 90
    full = page.pixmap(alpha=True)
    clipped = page.pixmap(clip=(0, 0, 100, 50), alpha=True)

    assert (full.width, full.height) == (792, 612)
    assert (clipped.width, clipped.height) == (100, 50)
    assert bytes(clipped) == b"".join(bytes(full)[y * full.stride : y * full.stride + 400] for y in range(50))


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
    doc = pylopdf.open(build_broken_image_pdf())
    with pytest.warns(pylopdf.PylopdfWarning, match="decode"):
        doc.pages[0].pixmap(alpha=True).to_bytes()


def test_clean_render_emits_no_warning(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        doc.pages[0].pixmap(alpha=True).to_bytes()
        doc.pages[0].text()


def test_warnings_do_not_leak_between_operations() -> None:
    """Do not leak a broken-PDF warning into the next clean operation."""
    broken = pylopdf.open(build_broken_image_pdf())
    with pytest.warns(pylopdf.PylopdfWarning):
        broken.pages[0].pixmap(alpha=True).to_bytes()
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        broken.pages[0].text()


def test_corpus_render_has_no_warnings() -> None:
    """Render a regular corpus PDF without warnings."""
    doc = pylopdf.open(ASSETS / "usrguide.pdf")
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        doc.pages[0].pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True)


def test_multi_page_pixmap() -> None:
    doc = pylopdf.open(build_pdf(["One", "Two"], page_size=(200, 100)))
    pix = doc.pages[1].pixmap(alpha=True)
    assert (pix.width, pix.height) == (200, 100)
