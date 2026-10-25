"""The images-to-PDF recipes of the ecosystem guide, run as written."""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest

import pylopdf

GUIDE = Path(__file__).parents[1] / "docs" / "en" / "ecosystem.md"
SECTION = "## Images and comic archives to PDF"
IMAGES = Path(__file__).parent / "assets" / "images"


def _recipes() -> dict[str, Any]:
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(SECTION)
    end = text.find("\n## ", start + len(SECTION))
    section = text[start : end if end != -1 else len(text)]
    blocks = re.findall(r"```python\n(.*?)```", section, flags=re.DOTALL)
    assert len(blocks) == 2
    namespace: dict[str, Any] = {}
    exec(compile("\n".join(blocks), str(GUIDE), "exec"), namespace)  # noqa: S102 - the guide's own code.
    return namespace


R = _recipes()


def _oriented_jpeg(orientation: int) -> bytes:
    """Return a 32-by-16 JPEG, red on the left and blue on the right, with an EXIF orientation."""
    rows = b"".join(bytes([255, 0, 0] * 16 + [0, 0, 255] * 16) for _ in range(16))
    data = pylopdf.Pixmap.from_array(memoryview(rows).cast("B", (16, 32, 3))).to_bytes(format="jpeg", quality=100)
    entry = struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
    payload = b"Exif\0\0MM\0*" + struct.pack(">IH", 8, 1) + entry + struct.pack(">I", 0)
    return data[:2] + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + data[2:]


def _sizes(data: bytes) -> list[tuple[float, float]]:
    doc = pylopdf.open(data)
    return [(round(page.rect.width, 2), round(page.rect.height, 2)) for page in doc.pages]


def test_images_become_pages_of_their_own_size() -> None:
    pdf = R["images_to_pdf"]([IMAGES / "rgb.jp2", (IMAGES / "cmyk-300dpi.jpg").read_bytes(), _oriented_jpeg(6)])

    # 32 by 16 pixels at 150 by 300 dpi, at 300 dpi, and at 96 dpi turned upright.
    assert _sizes(pdf) == [(15.36, 3.84), (7.68, 3.84), (12.0, 24.0)]
    doc = pylopdf.open(pdf)
    for page in doc.pages:
        (placement,) = page.content(text_runs=False, paths=False).images
        assert all(abs(a - b) < 1e-3 for a, b in zip(placement.bbox, page.rect, strict=True))
    # Orientation 6 turns the image clockwise: red on top, blue below.
    pixmap = doc.pages[2].pixmap(dpi=96)
    data = bytes(pixmap)
    top, bottom = 4 * pixmap.stride + 8 * 3, (pixmap.height - 4) * pixmap.stride + 8 * 3
    for offset, color in ((top, (255, 0, 0)), (bottom, (0, 0, 255))):
        assert all(abs(a - b) <= 8 for a, b in zip(data[offset : offset + 3], color, strict=True))
    # JPEG and JPEG 2000 data is stored as it is.
    assert [image.filters for image in doc.images.values()][:2] == [("JPXDecode",), ("DCTDecode",)]


def test_comic_archives_become_pages_in_name_order(tmp_path: Path) -> None:
    archive = tmp_path / "comic.cbz"
    with zipfile.ZipFile(archive, "w") as cbz:
        cbz.writestr("002.png", pylopdf.Pixmap.from_array(memoryview(bytes(12)).cast("B", (2, 2, 3))).to_bytes())
        cbz.writestr("001.JPG", (IMAGES / "cmyk-300dpi.jpg").read_bytes())
        cbz.writestr("notes.txt", b"not an image")

    assert _sizes(R["cbz_to_pdf"](archive)) == [(7.68, 3.84), (1.5, 1.5)]


def test_pillow_frames_feed_formats_that_pylopdf_does_not_read(tmp_path: Path) -> None:
    image_module = pytest.importorskip("PIL.Image", reason="interop group (Pillow) is not installed")
    path = tmp_path / "scan.tiff"
    first = image_module.new("RGB", (40, 20), (255, 0, 0))
    second = image_module.new("CMYK", (20, 40), (0, 255, 0, 0))
    first.save(path, save_all=True, append_images=[second], dpi=(200, 200))

    frames = R["pillow_frames"](path)

    assert len(frames) == 2
    assert [pylopdf.image_info(frame).format for frame in frames] == ["png", "png"]
    assert _sizes(R["images_to_pdf"](frames)) == [(14.4, 7.2), (7.2, 14.4)]
