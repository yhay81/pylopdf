"""Tests for placement-aware JPEG image compression."""

from __future__ import annotations

import base64
import functools
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"
# Generated 64x64 grayscale linear-gradient JPEG, kept inline to avoid a
# test-only image-library dependency.
GRAY_GRADIENT_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAME"
    "BgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/wAALCABAAEABAREA"
    "/8QAFgABAQEAAAAAAAAAAAAAAAAAAAgJ/8QAGhAAAgMBAQAAAAAAAAAAAAAAABcBZKFT"
    "kf/aAAgBAQAAPwDD5axwkLWOEhaxwkLWOEhaxwkLWOEhaxwkLWOElAravgW1fAtq+Bb"
    "V8C2r4FtXwLavgW1fCgFtXwLavgW1fAtq+BbV8C2r4FtXwLavhQC2r4FtXwLavgW1fA"
    "tq+BbV8C2r4FtXwoBbV8C2r4FtXwLavgW1fAtq+BbV8C2r4UAtq+BbV8C2r4FtXwLav"
    "gW1fAtq+BbV8KAW1fAtq+BbV8C2r4FtXwLavgW1fAtq+FALeOEeBbxwjwLeOEeBbxwj"
    "wLeOEeBbxwjwLeOEeBbxwjw//9k="
)


@functools.cache
def source_jpeg() -> tuple[bytes, int, int]:
    """Return a licensed real-world JPEG already bundled inside the corpus."""
    images = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")[0].get_images()
    image = next(image for image in images if image["ext"] == "jpeg")
    return image["image"], image["width"], image["height"]


def build_jpeg_pdf(
    placements: list[tuple[float, float, float, float]],
    *,
    image_options: str = "",
    source: tuple[bytes, int, int] | None = None,
    color_space: str = "DeviceRGB",
) -> bytes:
    """Draw one shared JPEG XObject at each PDF-space placement."""
    jpeg, width, height = source or source_jpeg()
    commands = "\n".join(
        f"q {draw_width} 0 0 {draw_height} {x} {y} cm /Im0 Do Q" for draw_width, draw_height, x, y in placements
    )
    image = (
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /{color_space} /BitsPerComponent 8 /Filter /DCTDecode "
            f"{image_options} /Length {len(jpeg)} >>\nstream\n"
        ).encode("ascii")
        + jpeg
        + b"\nendstream"
    )
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
            3: ("<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /XObject << /Im0 5 0 R >> >> >>"),
            4: f"<< /Length {len(commands)} >>\nstream\n{commands}\nendstream",
            5: image,
        }
    )


def test_compress_images_downsamples_and_reduces_jpeg() -> None:
    doc = pylopdf.open(stream=build_jpeg_pdf([(72, 72, 0, 0)]))
    page = doc[0]
    original = page.get_images()[0]
    before = page.get_pixmap().samples

    result = doc.compress_images(dpi=100, quality=65)

    compressed = page.get_images()[0]
    after = page.get_pixmap().samples
    assert result == {
        "considered": 1,
        "rewritten": 1,
        "skipped": 0,
        "bytes_before": len(original["image"]),
        "bytes_after": len(compressed["image"]),
        "bytes_saved": len(original["image"]) - len(compressed["image"]),
    }
    assert (compressed["width"], compressed["height"]) == (100, 100)
    assert len(compressed["image"]) < len(original["image"])
    assert len(after) == len(before)
    assert sum(abs(a - b) for a, b in zip(before, after, strict=True)) / len(after) < 40

    reopened = pylopdf.open(stream=doc.tobytes())
    assert (reopened[0].get_images()[0]["width"], reopened[0].get_images()[0]["height"]) == (100, 100)


def test_compress_images_preserves_pixels_needed_by_largest_reuse() -> None:
    doc = pylopdf.open(stream=build_jpeg_pdf([(72, 72, 0, 0), (18, 18, 100, 0)]))

    result = doc.compress_images(dpi=100, quality=65)

    assert result["rewritten"] == 1
    image = doc[0].get_images()[0]
    assert (image["width"], image["height"]) == (100, 100)


def test_compress_images_limits_each_placement_axis_independently() -> None:
    doc = pylopdf.open(stream=build_jpeg_pdf([(144, 72, 0, 0)]))

    result = doc.compress_images(dpi=100, quality=65)

    assert result["rewritten"] == 1
    image = doc[0].get_images()[0]
    assert (image["width"], image["height"]) == (200, 100)


def test_compress_images_can_recompress_without_downsampling() -> None:
    doc = pylopdf.open(stream=build_jpeg_pdf([(72, 72, 0, 0)]))
    original = doc[0].get_images()[0]

    result = doc.compress_images(dpi=None, quality=60)

    compressed = doc[0].get_images()[0]
    assert result["rewritten"] == 1
    assert (compressed["width"], compressed["height"]) == (
        original["width"],
        original["height"],
    )
    assert len(compressed["image"]) < len(original["image"])


def test_compress_images_downsamples_grayscale_jpeg() -> None:
    doc = pylopdf.open(
        stream=build_jpeg_pdf(
            [(72, 72, 0, 0)],
            source=(GRAY_GRADIENT_JPEG, 64, 64),
            color_space="DeviceGray",
        )
    )

    result = doc.compress_images(dpi=36, quality=65)

    compressed = doc[0].get_images()[0]
    assert result["rewritten"] == 1
    assert (compressed["width"], compressed["height"]) == (36, 36)
    assert compressed["ext"] == "jpeg"
    assert len(compressed["image"]) < len(GRAY_GRADIENT_JPEG)


def test_compress_images_skips_encoding_that_is_not_smaller() -> None:
    doc = pylopdf.open(
        stream=build_jpeg_pdf(
            [(72, 72, 0, 0)],
            source=(GRAY_GRADIENT_JPEG, 64, 64),
            color_space="DeviceGray",
        )
    )

    result = doc.compress_images(dpi=None, quality=100)

    assert result["rewritten"] == 0
    assert result["skipped"] == 1
    assert doc[0].get_images()[0]["image"] == GRAY_GRADIENT_JPEG


def test_compress_images_is_idempotent_at_same_settings() -> None:
    doc = pylopdf.open(stream=build_jpeg_pdf([(72, 72, 0, 0)]))
    assert doc.compress_images(dpi=100, quality=65)["rewritten"] == 1
    once = doc.tobytes()

    result = doc.compress_images(dpi=100, quality=65)

    assert result["rewritten"] == 0
    assert result["skipped"] == 1
    assert doc.tobytes() == once


def test_compress_images_skips_custom_mask_semantics() -> None:
    doc = pylopdf.open(
        stream=build_jpeg_pdf(
            [(72, 72, 0, 0)],
            image_options="/Mask [0 0 0 0 0 0]",
        )
    )
    original = doc.tobytes()

    result = doc.compress_images(dpi=100, quality=65)

    assert result["considered"] == 1
    assert result["rewritten"] == 0
    assert result["skipped"] == 1
    assert doc.tobytes() == original


def test_compress_images_rolls_back_all_candidates_on_decode_error() -> None:
    jpeg, _, _ = source_jpeg()
    doc = pylopdf.Document()
    page = doc.new_page(width=200, height=100)
    page.insert_image((0, 0, 72, 72), stream=jpeg)
    page.insert_image((100, 0, 172, 72), stream=jpeg[:-20])
    original = page.get_images()

    with pytest.raises(pylopdf.PdfError, match="failed to decode JPEG"):
        doc.compress_images(dpi=100, quality=65)

    assert page.get_images() == original


def test_compress_images_skips_source_above_pixel_limit_before_decode() -> None:
    jpeg, _, _ = source_jpeg()
    doc = pylopdf.open(
        stream=build_jpeg_pdf(
            [(72, 72, 0, 0)],
            source=(jpeg, 10_000, 7_000),
        )
    )

    result = doc.compress_images(dpi=100, quality=65)

    assert result["considered"] == 1
    assert result["rewritten"] == 0
    assert result["skipped"] == 1


def test_compress_images_empty_document_is_a_noop() -> None:
    doc = pylopdf.open(stream=build_pdf(["Text only"]))
    assert doc.compress_images() == {
        "considered": 0,
        "rewritten": 0,
        "skipped": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "bytes_saved": 0,
    }


def test_compress_images_does_not_count_inline_images() -> None:
    commands = "q 10 0 0 10 0 0 cm BI /W 1 /H 1 /CS /RGB /BPC 8 /F /AHx ID FF0000> EI Q"
    doc = pylopdf.open(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 20 20] >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: f"<< /Length {len(commands)} >>\nstream\n{commands}\nendstream",
            }
        )
    )

    assert doc.compress_images() == {
        "considered": 0,
        "rewritten": 0,
        "skipped": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "bytes_saved": 0,
    }


@pytest.mark.parametrize("dpi", [True, 0, 35, 2401, float("inf"), "150"])
def test_compress_images_rejects_invalid_dpi(dpi: object) -> None:
    with pytest.raises(pylopdf.PdfError, match="dpi must"):
        pylopdf.open(stream=build_pdf(["Text only"])).compress_images(dpi=dpi)  # type: ignore[arg-type]


@pytest.mark.parametrize("quality", [True, 0, 101, 75.5, "75"])
def test_compress_images_rejects_invalid_quality(quality: object) -> None:
    with pytest.raises(pylopdf.PdfError, match="quality must"):
        pylopdf.open(stream=build_pdf(["Text only"])).compress_images(quality=quality)  # type: ignore[arg-type]
