"""Images-to-PDF and comic-archive ecosystem recipes."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest

import pylopdf

IMAGES = Path(__file__).parent / "assets" / "images"
_RECIPES = '''
import io
import zipfile

import pylopdf

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".jp2", ".j2k", ".jpx")


def page_size(info: pylopdf.ImageInfo) -> tuple[float, float]:
    """Return the size in points at which an image shows upright at its own resolution."""
    width = info.width * 72 / info.resolution[0]
    height = info.height * 72 / info.resolution[1]
    # Orientations 5 to 8 turn the image a quarter, as canvas.image shows it.
    return (height, width) if info.orientation >= 5 else (width, height)


def images_to_pdf(images) -> bytes:
    """Return a PDF with one page per image, given as paths or encoded bytes."""
    doc = pylopdf.Document()
    for image in images:
        size = page_size(pylopdf.image_info(image))
        with doc.pages.new(size).draw() as canvas:
            # The page has the image's shape at its resolution, which can differ across and down.
            canvas.image((0, 0, *size), image, keep_aspect=False)
    return doc.to_bytes()


def cbz_to_pdf(path) -> bytes:
    """Return a PDF of a comic book archive's images, in the order of their names."""
    with zipfile.ZipFile(path) as archive:
        names = sorted(name for name in archive.namelist() if name.lower().endswith(IMAGE_SUFFIXES))
        return images_to_pdf([archive.read(name) for name in names])


def pillow_frames(path) -> list[bytes]:
    """Return every frame of an image that Pillow reads as PNG bytes."""
    from PIL import Image, ImageOps, ImageSequence

    frames = []
    with Image.open(path) as image:
        for frame in ImageSequence.Iterator(image):
            upright = ImageOps.exif_transpose(frame)
            if upright.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
                upright = upright.convert("RGBA" if "A" in upright.getbands() else "RGB")
            buffer = io.BytesIO()
            upright.save(buffer, "PNG", dpi=frame.info.get("dpi", (96, 96)))
            frames.append(buffer.getvalue())
    return frames
'''


def _recipes() -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec(compile(_RECIPES, __file__, "exec"), namespace)  # noqa: S102 - fixed recipe snippets.
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
