"""Pixmap.from_file and Pixmap.from_bytes decode PNG and JPEG."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import pylopdf
from pylopdf import Color, Pixmap

CMYK_JPEG = Path(__file__).parent / "assets" / "images" / "cmyk-300dpi.jpg"
PNG_GRAY, PNG_RGB, PNG_PALETTE, PNG_GRAY_ALPHA, PNG_RGBA = 0, 2, 3, 4, 6
METRE_UNIT = 1


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png(  # noqa: PLR0913 - one keyword per PNG header field and chunk.
    width: int,
    height: int,
    rows: list[bytes],
    *,
    color_type: int,
    bit_depth: int = 8,
    palette: bytes | None = None,
    transparency: bytes | None = None,
    pixels_per_unit: tuple[int, int, int] | None = None,
) -> bytes:
    """Return a PNG of ``rows``, each the packed samples of one row without its filter byte."""
    header = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    chunks = [_chunk(b"IHDR", header)]
    if pixels_per_unit is not None:
        chunks.append(_chunk(b"pHYs", struct.pack(">IIB", *pixels_per_unit)))
    if palette is not None:
        chunks.append(_chunk(b"PLTE", palette))
    if transparency is not None:
        chunks.append(_chunk(b"tRNS", transparency))
    chunks.append(_chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + row for row in rows))))
    chunks.append(_chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _shapes_page() -> pylopdf.Page:
    page = pylopdf.Document().pages.new((40, 30))
    with page.draw() as canvas:
        canvas.rect((5, 5, 25, 20), fill=Color(1, 0, 0, 0.5))
        canvas.circle((30, 15), 8, fill=Color(0, 0, 1))
    return page


@pytest.mark.parametrize(
    ("options", "channels"),
    [
        ({}, 3),
        ({"alpha": True}, 4),
        ({"colorspace": "gray"}, 1),
        ({"colorspace": "gray", "alpha": True}, 2),
    ],
)
def test_png_round_trips_every_layout(options: dict[str, object], channels: int) -> None:
    rendered = _shapes_page().pixmap(dpi=150, **options)  # ty: ignore[invalid-argument-type]

    decoded = Pixmap.from_bytes(rendered.to_bytes())

    assert (decoded.width, decoded.height, decoded.channels) == (rendered.width, rendered.height, channels)
    assert decoded.colorspace is rendered.colorspace
    assert bytes(decoded) == bytes(rendered)
    assert decoded.resolution == (150, 150)


@pytest.mark.parametrize("colorspace", ["rgb", "gray"])
def test_jpeg_decodes_to_the_samples_it_encoded(colorspace: str) -> None:
    rendered = _shapes_page().pixmap(dpi=144, colorspace=colorspace)  # ty: ignore[invalid-argument-type]

    decoded = Pixmap.from_bytes(rendered.to_bytes("jpeg", quality=100))

    assert (decoded.width, decoded.height, decoded.channels) == (rendered.width, rendered.height, rendered.channels)
    assert decoded.resolution == (144, 144)
    differences = [abs(a - b) for a, b in zip(bytes(decoded), bytes(rendered), strict=True)]
    assert max(differences) <= 48
    assert sum(differences) / len(differences) < 2


def test_a_cmyk_jpeg_decodes_to_rgb() -> None:
    decoded = Pixmap.from_file(CMYK_JPEG)

    assert (decoded.width, decoded.height, decoded.channels) == (32, 16, 3)
    assert decoded.resolution == (300, 300)
    data = bytes(decoded)
    row = 8 * decoded.stride
    centres = [tuple(data[row + x * 3 : row + x * 3 + 3]) for x in (4, 12, 20, 28)]
    expected = [(0, 255, 255), (255, 0, 255), (255, 255, 0), (0, 0, 0)]
    for centre, colour in zip(centres, expected, strict=True):
        assert all(abs(a - b) <= 2 for a, b in zip(centre, colour, strict=True)), (centre, colour)


def test_png_bit_depths_palettes_and_transparency_become_8_bit_samples() -> None:
    gray_16 = _png(2, 1, [b"\x12\x34\xff\xff"], color_type=PNG_GRAY, bit_depth=16)
    assert (Pixmap.from_bytes(gray_16).channels, bytes(Pixmap.from_bytes(gray_16))) == (1, b"\x12\xff")

    gray_1 = _png(8, 1, [b"\xa5"], color_type=PNG_GRAY, bit_depth=1)
    assert bytes(Pixmap.from_bytes(gray_1)) == b"\xff\x00\xff\x00\x00\xff\x00\xff"

    rgba_16 = _png(1, 1, [b"\x10\x00\x20\x00\x30\x00\x80\x00"], color_type=PNG_RGBA, bit_depth=16)
    assert bytes(Pixmap.from_bytes(rgba_16)) == b"\x10\x20\x30\x80"

    palette = _png(3, 1, [b"\x00\x01\x02"], color_type=PNG_PALETTE, palette=b"\xff\x00\x00\x00\xff\x00\x00\x00\xff")
    decoded = Pixmap.from_bytes(palette)
    assert (decoded.channels, bytes(decoded)) == (3, b"\xff\x00\x00\x00\xff\x00\x00\x00\xff")

    transparent = _png(
        2, 1, [b"\x00\x01"], color_type=PNG_PALETTE, palette=b"\x00\x00\x00\xff\xff\xff", transparency=b"\x00"
    )
    decoded = Pixmap.from_bytes(transparent)
    assert (decoded.channels, bytes(decoded)) == (4, b"\x00\x00\x00\x00\xff\xff\xff\xff")

    keyed = _png(2, 1, [b"\x10\x80"], color_type=PNG_GRAY, transparency=b"\x00\x80")
    assert bytes(Pixmap.from_bytes(keyed)) == b"\x10\xff\x80\x00"

    gray_alpha = _png(1, 1, [b"\x40\x20"], color_type=PNG_GRAY_ALPHA)
    assert (Pixmap.from_bytes(gray_alpha).channels, bytes(Pixmap.from_bytes(gray_alpha))) == (2, b"\x40\x20")


@pytest.mark.parametrize(
    ("pixels_per_unit", "resolution"),
    [
        (None, (96, 96)),
        ((11811, 3780, METRE_UNIT), (300, 96)),
        ((5906, 5906, METRE_UNIT), (150, 150)),
        # Without a unit, pHYs states only an aspect ratio.
        ((2, 1, 0), (96, 96)),
        ((0, 0, METRE_UNIT), (96, 96)),
        ((4_000_000_000, 1, METRE_UNIT), (65535, 1)),
    ],
)
def test_png_resolution_comes_from_phys(
    pixels_per_unit: tuple[int, int, int] | None, resolution: tuple[int, int]
) -> None:
    data = _png(1, 1, [b"\x00\x00\x00"], color_type=PNG_RGB, pixels_per_unit=pixels_per_unit)

    assert Pixmap.from_bytes(data).resolution == resolution


def test_a_jpeg_without_a_density_states_96_dpi() -> None:
    data = bytearray(_shapes_page().pixmap().to_bytes("jpeg"))
    # Units 0 in the JFIF header state only an aspect ratio.
    data[13] = 0

    assert Pixmap.from_bytes(bytes(data)).resolution == (96, 96)


def test_from_file_reads_paths(tmp_path: Path) -> None:
    rendered = _shapes_page().pixmap(dpi=100)
    rendered.save(tmp_path / "page.png")

    for path in (tmp_path / "page.png", str(tmp_path / "page.png")):
        decoded = Pixmap.from_file(path)
        assert bytes(decoded) == bytes(rendered)
        assert decoded.resolution == (100, 100)
    with pytest.raises(FileNotFoundError):
        Pixmap.from_file(tmp_path / "missing.png")


def test_from_bytes_takes_any_byte_buffer() -> None:
    data = _shapes_page().pixmap().to_bytes()
    expected = bytes(Pixmap.from_bytes(data))

    assert bytes(Pixmap.from_bytes(bytearray(data))) == expected
    assert bytes(Pixmap.from_bytes(memoryview(data))) == expected
    with pytest.raises(TypeError, match="from_bytes needs bytes"):
        Pixmap.from_bytes("not bytes")  # ty: ignore[invalid-argument-type]


def test_input_size_is_bounded_before_decoding(tmp_path: Path) -> None:
    data = _shapes_page().pixmap().to_bytes()
    (tmp_path / "page.png").write_bytes(data)

    for source in (data, bytearray(data)):
        with pytest.raises(pylopdf.LimitError) as error:
            Pixmap.from_bytes(source, max_size=len(data) - 1)
        assert error.value.code == "image_input_size"
    with pytest.raises(pylopdf.LimitError) as error:
        Pixmap.from_file(tmp_path / "page.png", max_size=len(data) - 1)
    assert error.value.code == "image_input_size"
    assert Pixmap.from_bytes(data, max_size=len(data)).width == 40
    assert Pixmap.from_file(tmp_path / "page.png", max_size=None).width == 40


def test_pixel_count_is_bounded_before_decoding() -> None:
    png = _shapes_page().pixmap().to_bytes()
    jpeg = Pixmap.from_file(CMYK_JPEG).to_bytes("jpeg")

    for data, pixels, kind in ((png, 40 * 30, "PNG"), (jpeg, 32 * 16, "JPEG")):
        with pytest.raises(pylopdf.LimitError, match=f"{kind} image contains {pixels} pixels") as error:
            Pixmap.from_bytes(data, max_pixels=pixels - 1)
        assert error.value.code == "image_pixel_count"
        assert Pixmap.from_bytes(data, max_pixels=pixels).width > 0
        assert Pixmap.from_bytes(data, max_pixels=None).width > 0
    # The header alone decides: this PNG's image data is not even valid.
    huge = _png(20_000, 20_000, [], color_type=PNG_RGB)
    with pytest.raises(pylopdf.LimitError, match="400000000 pixels"):
        Pixmap.from_bytes(huge)


@pytest.mark.parametrize(
    ("keyword", "value", "error"),
    [
        ("max_size", 0, ValueError),
        ("max_size", True, TypeError),
        ("max_pixels", 0, ValueError),
        ("max_pixels", -1, ValueError),
        ("max_pixels", 1.5, TypeError),
        ("max_pixels", False, TypeError),
    ],
)
def test_limits_must_be_positive_integers_or_none(keyword: str, value: object, error: type[Exception]) -> None:
    with pytest.raises(error, match=f"{keyword} must be a positive integer or None"):
        Pixmap.from_bytes(_shapes_page().pixmap().to_bytes(), **{keyword: value})  # ty: ignore[invalid-argument-type]


def test_a_side_beyond_65535_pixels_is_refused() -> None:
    wide = _png(70_000, 1, [b"\x00" * 70_000], color_type=PNG_GRAY)

    with pytest.raises(pylopdf.PdfError, match="1 to 65535 pixels a side, not 70000x1"):
        Pixmap.from_bytes(wide)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"GIF89a", "unsupported image format"),
        (b"", "unsupported image format"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "failed to decode PNG"),
        (b"\xff\xd8\xff\xe0garbage", "failed to decode JPEG"),
    ],
)
def test_malformed_or_unsupported_input_raises(data: bytes, message: str) -> None:
    with pytest.raises(pylopdf.PdfError, match=message):
        Pixmap.from_bytes(data)


def test_a_truncated_png_raises() -> None:
    data = _shapes_page().pixmap().to_bytes()

    with pytest.raises(pylopdf.PdfError, match="failed to decode PNG"):
        Pixmap.from_bytes(data[: len(data) // 2])


def test_a_decoded_image_draws_like_its_source() -> None:
    rendered = _shapes_page().pixmap(dpi=72, alpha=True)
    decoded = Pixmap.from_bytes(rendered.to_bytes())
    doc = pylopdf.Document()
    page = doc.pages.new((40, 30))
    with page.draw() as canvas:
        canvas.image((0, 0, 40, 30), decoded)

    assert page.resources.images[0].width == 40
