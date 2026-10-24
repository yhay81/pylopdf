"""Tests for placement-aware raster image compression."""

from __future__ import annotations

import base64
import functools
import itertools
import zlib
from collections.abc import Iterable
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf, drawn_images

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
    images = drawn_images(pylopdf.open(ASSETS / "wdl6812-manuscript.pdf").pages[0])
    image = next(image for image in images if image["ext"] == "jpeg")
    return image["image"], image["width"], image["height"]


def build_jpeg_pdf(
    placements: Iterable[tuple[float, float, float, float]],
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


def build_flate_pdf(
    *,
    color_space: str = "DeviceRGB",
    predictor: int | None = None,
    corrupt: bool = False,
    compressible: bool = False,
) -> tuple[bytes, bytes]:
    """Draw a deterministic Flate raster and return its encoded payload."""
    width = height = 128
    components = 1 if color_space == "DeviceGray" else 3
    if compressible:
        samples = bytearray(width * height * components)
    else:
        state = 0x12345678
        samples = bytearray()
        for _ in range(width * height * components):
            state = (1_664_525 * state + 1_013_904_223) & 0xFFFFFFFF
            samples.append(state >> 24)
    if predictor is not None and 10 <= predictor <= 15:
        row_bytes = width * components
        filtered = b"".join(
            b"\x00" + samples[offset : offset + row_bytes] for offset in range(0, len(samples), row_bytes)
        )
    else:
        filtered = bytes(samples)
    encoded = zlib.compress(filtered)
    if corrupt:
        encoded = encoded[: max(4, len(encoded) // 3)]
    decode_params = (
        ""
        if predictor is None
        else (f"/DecodeParms << /Predictor {predictor} /Colors {components} /BitsPerComponent 8 /Columns {width} >>")
    )
    commands = "q 72 0 0 72 0 0 cm /Im0 Do Q"
    image = (
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /{color_space} /BitsPerComponent 8 /Filter /FlateDecode "
            f"{decode_params} /Length {len(encoded)} >>\nstream\n"
        ).encode("ascii")
        + encoded
        + b"\nendstream"
    )
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
            3: ("<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /XObject << /Im0 5 0 R >> >> >>"),
            4: f"<< /Length {len(commands)} >>\nstream\n{commands}\nendstream",
            5: image,
        }
    )
    return pdf, encoded


def test_rewrite_images_downsamples_and_reduces_jpeg() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    page = doc.pages[0]
    original = drawn_images(page)[0]
    before = bytes(page.pixmap(alpha=True))

    result = doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=65)

    compressed = drawn_images(page)[0]
    after = bytes(page.pixmap(alpha=True))
    assert result == pylopdf.ImageCompressionResult(
        considered=1,
        rewritten=1,
        skipped=0,
        bytes_before=len(original["image"]),
        bytes_after=len(compressed["image"]),
        bytes_saved=len(original["image"]) - len(compressed["image"]),
    )
    assert (compressed["width"], compressed["height"]) == (100, 100)
    assert len(compressed["image"]) < len(original["image"])
    assert len(after) == len(before)
    assert sum(abs(a - b) for a, b in zip(before, after, strict=True)) / len(after) < 40

    reopened = pylopdf.open(doc.to_bytes())
    assert (drawn_images(reopened.pages[0])[0]["width"], drawn_images(reopened.pages[0])[0]["height"]) == (100, 100)


def test_rewrite_images_preserves_pixels_needed_by_largest_reuse() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0), (18, 18, 100, 0)]))

    result = doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=65)

    assert result.rewritten == 1
    image = drawn_images(doc.pages[0])[0]
    assert (image["width"], image["height"]) == (100, 100)


def test_rewrite_images_limits_each_placement_axis_independently() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(144, 72, 0, 0)]))

    result = doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=65)

    assert result.rewritten == 1
    image = drawn_images(doc.pages[0])[0]
    assert (image["width"], image["height"]) == (200, 100)


def test_rewrite_images_can_recompress_without_downsampling() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    original = drawn_images(doc.pages[0])[0]

    result = doc.rewrite_images(quality=60)

    compressed = drawn_images(doc.pages[0])[0]
    assert result.rewritten == 1
    assert (compressed["width"], compressed["height"]) == (
        original["width"],
        original["height"],
    )
    assert len(compressed["image"]) < len(original["image"])


def test_rewrite_images_downsamples_grayscale_jpeg() -> None:
    doc = pylopdf.open(
        build_jpeg_pdf(
            [(72, 72, 0, 0)],
            source=(GRAY_GRADIENT_JPEG, 64, 64),
            color_space="DeviceGray",
        )
    )

    result = doc.rewrite_images(dpi_threshold=48, dpi_target=36, quality=65)

    compressed = drawn_images(doc.pages[0])[0]
    assert result.rewritten == 1
    assert (compressed["width"], compressed["height"]) == (36, 36)
    assert compressed["ext"] == "jpeg"
    assert len(compressed["image"]) < len(GRAY_GRADIENT_JPEG)


@pytest.mark.parametrize(
    ("color_space", "predictor"),
    [("DeviceRGB", None), ("DeviceRGB", 15), ("DeviceGray", 15)],
)
def test_rewrite_images_converts_safe_flate_rasters(
    color_space: str,
    predictor: int | None,
) -> None:
    source, encoded = build_flate_pdf(color_space=color_space, predictor=predictor)
    doc = pylopdf.open(source)
    page = doc.pages[0]
    before = bytes(page.pixmap(alpha=True))

    result = doc.rewrite_images(dpi_threshold=96, dpi_target=64, quality=65)

    compressed = drawn_images(page)[0]
    after = bytes(page.pixmap(alpha=True))
    assert result == pylopdf.ImageCompressionResult(
        considered=1,
        rewritten=1,
        skipped=0,
        bytes_before=len(encoded),
        bytes_after=len(compressed["image"]),
        bytes_saved=len(encoded) - len(compressed["image"]),
    )
    assert compressed["ext"] == "jpeg"
    assert (compressed["width"], compressed["height"]) == (64, 64)
    assert len(compressed["image"]) < len(encoded)
    assert len(after) == len(before)
    assert sum(abs(a - b) for a, b in zip(before, after, strict=True)) / len(after) < 50

    reopened = pylopdf.open(doc.to_bytes())
    assert drawn_images(reopened.pages[0])[0]["ext"] == "jpeg"


@pytest.mark.parametrize("predictor", [2, 9])
def test_rewrite_images_skips_unsupported_flate_predictors(predictor: int) -> None:
    source, _ = build_flate_pdf(predictor=predictor)
    doc = pylopdf.open(source)
    original = doc.to_bytes()

    result = doc.rewrite_images(dpi_threshold=96, dpi_target=64, quality=65)

    assert result.rewritten == 0
    assert result.skipped == 1
    assert doc.to_bytes() == original


def test_rewrite_images_skips_flate_when_jpeg_is_not_smaller() -> None:
    source, _ = build_flate_pdf(compressible=True)
    doc = pylopdf.open(source)
    original = doc.to_bytes()

    result = doc.rewrite_images(quality=65)

    assert result.rewritten == 0
    assert result.skipped == 1
    assert doc.to_bytes() == original


def test_rewrite_images_rejects_malformed_flate_atomically() -> None:
    source, _ = build_flate_pdf(corrupt=True)
    doc = pylopdf.open(source)
    original = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match=r"failed to decode Flate image|unexpected sample count"):
        doc.rewrite_images(dpi_threshold=96, dpi_target=64, quality=65)

    assert doc.to_bytes() == original


def test_rewrite_images_skips_encoding_that_is_not_smaller() -> None:
    doc = pylopdf.open(
        build_jpeg_pdf(
            [(72, 72, 0, 0)],
            source=(GRAY_GRADIENT_JPEG, 64, 64),
            color_space="DeviceGray",
        )
    )

    result = doc.rewrite_images(quality=100)

    assert result.rewritten == 0
    assert result.skipped == 1
    assert drawn_images(doc.pages[0])[0]["image"] == GRAY_GRADIENT_JPEG


def test_rewrite_images_is_idempotent_at_same_settings() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    assert doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=65).rewritten == 1
    once = doc.to_bytes()

    result = doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=65)

    assert result.rewritten == 0
    assert result.skipped == 1
    assert doc.to_bytes() == once


def test_rewrite_images_skips_custom_mask_semantics() -> None:
    doc = pylopdf.open(
        build_jpeg_pdf(
            [(72, 72, 0, 0)],
            image_options="/Mask [0 0 0 0 0 0]",
        )
    )
    original = doc.to_bytes()

    result = doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=65)

    assert result.considered == 1
    assert result.rewritten == 0
    assert result.skipped == 1
    assert doc.to_bytes() == original


def test_rewrite_images_rolls_back_all_candidates_on_decode_error() -> None:
    jpeg, _, _ = source_jpeg()
    doc = pylopdf.Document()
    page = doc.pages.new((200, 100))
    with page.draw() as canvas:
        canvas.image((0, 0, 72, 72), jpeg)
        canvas.image((100, 0, 172, 72), jpeg[:-20])
    original = drawn_images(page)

    with pytest.raises(pylopdf.PdfError, match="failed to decode JPEG"):
        doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=65)

    assert drawn_images(page) == original


def test_rewrite_images_skips_source_above_pixel_limit_before_decode() -> None:
    jpeg, _, _ = source_jpeg()
    doc = pylopdf.open(
        build_jpeg_pdf(
            [(72, 72, 0, 0)],
            source=(jpeg, 10_000, 7_000),
        )
    )

    result = doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=65)

    assert result.considered == 1
    assert result.rewritten == 0
    assert result.skipped == 1


def test_rewrite_images_accepts_exact_placement_limit() -> None:
    doc = pylopdf.open(
        build_jpeg_pdf(
            itertools.repeat((1, 1, 0, 0), 65_536),
            source=(GRAY_GRADIENT_JPEG, 64, 64),
            color_space="DeviceGray",
        )
    )

    result = doc.rewrite_images(quality=100)

    assert result.considered == 1


def test_rewrite_images_rejects_excessive_placements_atomically() -> None:
    doc = pylopdf.open(
        build_jpeg_pdf(
            itertools.repeat((1, 1, 0, 0), 65_537),
            source=(GRAY_GRADIENT_JPEG, 64, 64),
            color_space="DeviceGray",
        )
    )
    original = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="65536-placement safety limit"):
        doc.rewrite_images(quality=75)

    assert doc.to_bytes() == original


def test_rewrite_images_empty_document_is_a_noop() -> None:
    doc = pylopdf.open(build_pdf(["Text only"]))
    assert doc.rewrite_images(quality=75) == pylopdf.ImageCompressionResult(
        considered=0,
        rewritten=0,
        skipped=0,
        bytes_before=0,
        bytes_after=0,
        bytes_saved=0,
    )


def test_rewrite_images_does_not_count_inline_images() -> None:
    commands = "q 10 0 0 10 0 0 cm BI /W 1 /H 1 /CS /RGB /BPC 8 /F /AHx ID FF0000> EI Q"
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 20 20] >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: f"<< /Length {len(commands)} >>\nstream\n{commands}\nendstream",
            }
        )
    )

    assert doc.rewrite_images(quality=75) == pylopdf.ImageCompressionResult(
        considered=0,
        rewritten=0,
        skipped=0,
        bytes_before=0,
        bytes_after=0,
        bytes_saved=0,
    )


@pytest.mark.parametrize("dpi", [True, 35, 2401, float("inf"), "150"])
def test_rewrite_images_rejects_invalid_dpi(dpi: object) -> None:
    doc = pylopdf.open(build_pdf(["Text only"]))
    with pytest.raises(pylopdf.PdfError, match="dpi_threshold must"):
        doc.rewrite_images(dpi_threshold=dpi, dpi_target=36, quality=75)  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.PdfError, match="dpi_target must"):
        doc.rewrite_images(dpi_threshold=2400, dpi_target=dpi, quality=75)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("quality", [True, -1, 101, 75.5, "75"])
def test_rewrite_images_rejects_invalid_quality(quality: object) -> None:
    with pytest.raises(pylopdf.PdfError, match="quality must"):
        pylopdf.open(build_pdf(["Text only"])).rewrite_images(quality=quality)  # ty: ignore[invalid-argument-type]


def test_rewrite_images_requires_a_target_below_the_threshold() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    with pytest.raises(ValueError, match="must be less than"):
        doc.rewrite_images(dpi_threshold=100, dpi_target=100, quality=65)
    with pytest.raises(pylopdf.PdfError, match="dpi_target must"):
        doc.rewrite_images(dpi_threshold=100, quality=65)


def test_rewrite_images_refuses_resampling_without_recompression() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    original = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="requires quality"):
        doc.rewrite_images(dpi_threshold=150, dpi_target=100)
    assert doc.to_bytes() == original


def test_rewrite_images_refuses_unsupported_options() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    with pytest.raises(pylopdf.PdfError, match="options"):
        doc.rewrite_images(quality=65, options=object())


def test_rewrite_images_converts_colour_sources_to_gray() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    page = doc.pages[0]

    result = doc.rewrite_images(quality=65, set_to_gray=True)

    assert result.rewritten == 1
    (image,) = page.content().images
    assert image.components == 1
    samples = bytes(page.pixmap(dpi=36))
    assert all(samples[i] == samples[i + 1] == samples[i + 2] for i in range(0, len(samples), 3))
    with pytest.raises(pylopdf.PdfError, match="set_to_gray"):
        pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)])).rewrite_images(set_to_gray=True)


@pytest.mark.parametrize("color_space", ["DeviceRGB", "DeviceGray"])
def test_rewrite_images_writes_lossless_flate(color_space: str) -> None:
    source, _ = build_flate_pdf(color_space=color_space, predictor=None)
    doc = pylopdf.open(source)
    page = doc.pages[0]
    before = bytes(page.pixmap(alpha=True))

    result = doc.rewrite_images(dpi_threshold=96, dpi_target=64, encoding="flate")

    assert result.rewritten == 1
    (image,) = drawn_images(page)
    assert (image["width"], image["height"]) == (64, 64)
    assert page.content().images[0].bits == 8
    obj = doc.objects[page.content().images[0].xref]
    assert obj["Filter"] == "FlateDecode"
    assert "PylopdfQuality" not in obj
    assert len(bytes(page.pixmap(alpha=True))) == len(before)


def test_rewrite_images_writes_bitonal_images() -> None:
    source, _ = build_flate_pdf(color_space="DeviceRGB", predictor=None)
    doc = pylopdf.open(source)
    page = doc.pages[0]

    result = doc.rewrite_images(encoding="bitonal")

    assert result.rewritten == 1
    (image,) = page.content().images
    assert image.bits == 1
    assert image.components == 1
    samples = set(bytes(doc.images[image.xref].to_pixmap()))
    assert samples == {0, 255}
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].content().images[0].bits == 1


def test_rewrite_images_refuses_unknown_encodings() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    with pytest.raises(ValueError, match="encoding"):
        doc.rewrite_images(encoding="png")  # ty: ignore[invalid-argument-type]


def test_rewrite_images_without_quality_or_threshold_does_nothing() -> None:
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    original = doc.to_bytes()

    assert doc.rewrite_images() == pylopdf.ImageCompressionResult(
        considered=0,
        rewritten=0,
        skipped=0,
        bytes_before=0,
        bytes_after=0,
        bytes_saved=0,
    )
    assert doc.to_bytes() == original


def test_rewrite_images_keeps_images_at_or_below_the_threshold() -> None:
    # The source is drawn at its own pixel size per inch, so 200 dpi stays.
    doc = pylopdf.open(build_jpeg_pdf([(72, 72, 0, 0)]))
    width = drawn_images(doc.pages[0])[0]["width"]

    doc.rewrite_images(dpi_threshold=width, dpi_target=100, quality=65)

    assert drawn_images(doc.pages[0])[0]["width"] == width


@pytest.mark.parametrize(
    ("keyword", "color_space", "flate"),
    [
        ("lossy", "DeviceRGB", False),
        ("lossless", "DeviceRGB", True),
        ("color", "DeviceRGB", True),
        ("gray", "DeviceGray", True),
    ],
)
def test_rewrite_images_selects_image_classes(keyword: str, color_space: str, *, flate: bool) -> None:
    if flate:
        source, _ = build_flate_pdf(color_space=color_space)
    else:
        source = build_jpeg_pdf([(72, 72, 0, 0)])
    doc = pylopdf.open(source)
    original = doc.to_bytes()

    excluded = doc.rewrite_images(dpi_threshold=96, dpi_target=64, quality=65, **{keyword: False})  # ty: ignore[invalid-argument-type]

    assert (excluded.rewritten, excluded.skipped) == (0, 1)
    assert doc.to_bytes() == original
    assert doc.rewrite_images(dpi_threshold=96, dpi_target=64, quality=65).rewritten == 1


def test_rewrite_images_replaces_compress_images() -> None:
    assert not hasattr(pylopdf.Document(), "compress_images")
