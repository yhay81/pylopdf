"""Image file headers (image_info) and the EXIF orientation that canvas.image applies."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import pylopdf

IMAGES = Path(__file__).parent / "assets" / "images"
W, H = 32, 16
RED, GREEN, BLUE, WHITE = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)
QUADRANTS = (RED, GREEN, BLUE, WHITE)


def _quadrant(x: int, y: int) -> tuple[int, int, int]:
    """Return the stored color at a pixel: red, green on top, blue, white below."""
    return QUADRANTS[(2 if y >= H // 2 else 0) + (1 if x >= W // 2 else 0)]


def _quadrants() -> pylopdf.Pixmap:
    rows = b"".join(bytes(channel for x in range(W) for channel in _quadrant(x, y)) for y in range(H))
    return pylopdf.Pixmap.from_array(memoryview(rows).cast("B", (H, W, 3)))


def _tiff_orientation(orientation: int) -> bytes:
    """Return a big-endian TIFF structure whose first IFD holds only an orientation."""
    entry = struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
    return b"MM\0*" + struct.pack(">I", 8) + struct.pack(">H", 1) + entry + struct.pack(">I", 0)


def _jpeg(orientation: int | None = None) -> bytes:
    data = _quadrants().to_bytes(format="jpeg", quality=100)
    if orientation is None:
        return data
    payload = b"Exif\0\0" + _tiff_orientation(orientation)
    return data[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + data[2:]


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def _png(orientation: int | None = None) -> bytes:
    data = _quadrants().to_bytes()
    if orientation is None:
        return data
    # eXIf goes after IHDR, which ends 33 bytes in.
    return data[:33] + _chunk(b"eXIf", _tiff_orientation(orientation)) + data[33:]


def _palette_png() -> bytes:
    """Return a 2-by-1 PNG whose palette maps 0 to opaque red and 1 to transparent blue."""
    header = struct.pack(">IIBBBBB", 2, 1, 8, 3, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"PLTE", bytes([255, 0, 0, 0, 0, 255]))
        + _chunk(b"tRNS", bytes([255, 0]))
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00\x01"))
        + _chunk(b"IEND", b"")
    )


def _upright(u: int, v: int, orientation: int) -> tuple[int, int]:
    """Return the stored pixel that an upright view shows at (u, v), as EXIF defines each orientation."""
    return {
        1: (u, v),
        2: (W - 1 - u, v),
        3: (W - 1 - u, H - 1 - v),
        4: (u, H - 1 - v),
        5: (v, u),
        6: (v, H - 1 - u),
        7: (W - 1 - v, H - 1 - u),
        8: (W - 1 - v, u),
    }[orientation]


def _pixel(pixmap: pylopdf.Pixmap, x: int, y: int) -> tuple[int, ...]:
    data = bytes(pixmap)
    offset = y * pixmap.stride + x * pixmap.channels
    return tuple(data[offset : offset + pixmap.channels])


def _check_upright(page: pylopdf.Page, orientation: int, *, turn: int = 0) -> None:
    """Check that the page shows the quadrants as the orientation asks, then turned clockwise by ``turn``."""
    pixmap = page.pixmap()
    width, height = pixmap.width, pixmap.height
    for u in (width // 4, 3 * width // 4):
        for v in (height // 4, 3 * height // 4):
            # Undo the extra turn first, in the upright image's own frame.
            uu, vv = {0: (u, v), 90: (v, width - 1 - u), 180: (width - 1 - u, height - 1 - v)}[turn]
            expected = _quadrant(*_upright(uu, vv, orientation))
            got = _pixel(pixmap, u, v)
            assert max(abs(a - b) for a, b in zip(got, expected, strict=True)) <= 40, (orientation, (u, v), got)


def test_image_info_reads_headers_as_pixmaps_decode_them(tmp_path: Path) -> None:
    gray = pylopdf.Pixmap.from_array(memoryview(bytes(8)).cast("B", (2, 4)), resolution=(150, 200))
    rgba = pylopdf.Pixmap.from_array(memoryview(bytes(32)).cast("B", (2, 4, 4)))
    cases = {
        "rgb.jp2": (IMAGES / "rgb.jp2").read_bytes(),
        "rgba.jp2": (IMAGES / "rgba.jp2").read_bytes(),
        "gray.j2k": (IMAGES / "gray.j2k").read_bytes(),
        "palette.jp2": (IMAGES / "palette.jp2").read_bytes(),
        "cmyk.jpg": (IMAGES / "cmyk-300dpi.jpg").read_bytes(),
        "gray.png": gray.to_bytes(),
        "rgba.png": rgba.to_bytes(),
        "palette.png": _palette_png(),
        "oriented.jpg": _jpeg(6),
    }
    expected = {
        "rgb.jp2": ("jpx", 32, 16, 3, False, 8, (150, 300), 1),
        "rgba.jp2": ("jpx", 16, 16, 3, True, 8, (96, 96), 1),
        "gray.j2k": ("jpx", 16, 8, 1, False, 8, (96, 96), 1),
        "palette.jp2": ("jpx", 32, 16, 3, False, 8, (96, 96), 1),
        "cmyk.jpg": ("jpeg", 32, 16, 4, False, 8, (300, 300), 1),
        "gray.png": ("png", 4, 2, 1, False, 8, (150, 200), 1),
        "rgba.png": ("png", 4, 2, 3, True, 8, (96, 96), 1),
        "palette.png": ("png", 2, 1, 3, True, 8, (96, 96), 1),
        "oriented.jpg": ("jpeg", 32, 16, 3, False, 8, (96, 96), 6),
    }
    for name, data in cases.items():
        info = pylopdf.image_info(data)
        assert (
            info.format,
            info.width,
            info.height,
            info.components,
            info.alpha,
            info.bits,
            info.resolution,
            info.orientation,
        ) == expected[name], name
        path = tmp_path / name
        path.write_bytes(data)
        assert pylopdf.image_info(path) == info
        # A Pixmap decodes the same size, resolution, and alpha, with CMYK as RGB.
        pixmap = pylopdf.Pixmap.from_bytes(data)
        assert (pixmap.width, pixmap.height, pixmap.resolution) == (info.width, info.height, info.resolution)
        assert pixmap.channels == min(info.components, 3) + info.alpha
    assert pylopdf.image_info(_png(8)).orientation == 8


def test_image_info_refuses_what_it_cannot_read(tmp_path: Path) -> None:
    data = _jpeg()
    with pytest.raises(pylopdf.LimitError) as excinfo:
        pylopdf.image_info(data, max_size=10)
    assert excinfo.value.code == "image_input_size"
    path = tmp_path / "image.jpg"
    path.write_bytes(data)
    with pytest.raises(pylopdf.LimitError) as excinfo:
        pylopdf.image_info(path, max_size=10)
    assert excinfo.value.code == "image_input_size"
    assert pylopdf.image_info(path, max_size=None).width == W
    with pytest.raises(pylopdf.PdfError, match="unsupported image format"):
        pylopdf.image_info(b"GIF89a")
    for broken in (data[:20], _png()[:20], (IMAGES / "rgb.jp2").read_bytes()[:40]):
        with pytest.raises(pylopdf.PdfError):
            pylopdf.image_info(broken)
    with pytest.raises(FileNotFoundError):
        pylopdf.image_info(tmp_path / "missing.png")
    with pytest.raises(TypeError, match="path or bytes"):
        pylopdf.image_info(42)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="max_size"):
        pylopdf.image_info(data, max_size=0)


@pytest.mark.parametrize("orientation", range(1, 9))
@pytest.mark.parametrize("new_document", [False, True], ids=["open", "new"])
def test_canvas_shows_jpeg_as_its_exif_orientation_asks(orientation: int, *, new_document: bool) -> None:
    data = _jpeg(orientation)
    size = (W, H) if orientation <= 4 else (H, W)
    if new_document:
        builder = pylopdf.create()
        with builder.page(size) as canvas:
            canvas.image((0, 0, *size), data)
        page = builder.finish().pages[0]
    else:
        page = pylopdf.Document().pages.new(size)
        with page.draw() as canvas:
            canvas.image((0, 0, *size), data)

    _check_upright(page, orientation)


@pytest.mark.parametrize("orientation", [2, 6, 7])
def test_canvas_shows_png_as_its_exif_orientation_asks(orientation: int) -> None:
    size = (W, H) if orientation <= 4 else (H, W)
    page = pylopdf.Document().pages.new(size)
    with page.draw() as canvas:
        canvas.image((0, 0, *size), _png(orientation))

    _check_upright(page, orientation)


def test_rotate_turns_the_upright_image() -> None:
    page = pylopdf.Document().pages.new((W, H))
    with page.draw() as canvas:
        # Orientation 6 stands the image up; rotate=90 turns the upright image once more.
        canvas.image((0, 0, W, H), _jpeg(6), rotate=90)
    _check_upright(page, 6, turn=90)

    # A Pixmap has no orientation, and an image object none that drawing reads.
    pixmap = pylopdf.Pixmap.from_bytes(_jpeg(6))
    doc = pylopdf.Document()
    first = doc.pages.new((W, H))
    with first.draw() as canvas:
        canvas.image((0, 0, W, H), pixmap)
    _check_upright(first, 1)
