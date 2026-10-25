"""Replacing an image object in place, and drawing image objects with the canvas."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf


def _image(width: int, height: int, data: bytes, extra: str = "") -> bytes:
    compressed = zlib.compress(data)
    return (
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode {extra}"
            f"/Length {len(compressed)} >>\nstream\n"
        ).encode()
        + compressed
        + b"\nendstream"
    )


def _shared_image_pdf() -> bytes:
    """Build a page that draws image 5, which has a soft mask and a layer, on the page and in a Form."""
    content = b"q 40 0 0 40 0 0 cm /Im0 Do Q /Fm0 Do"
    form = b"q 40 0 0 40 50 0 cm /Im0 Do Q"
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [7 0 R] /D << /ON [7 0 R] >> >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 40] /Contents 4 0 R "
                "/Resources << /XObject << /Im0 5 0 R /Fm0 8 0 R >> >> >>"
            ),
            4: f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
            5: _image(2, 1, bytes([10, 20]), "/SMask 6 0 R /OC 7 0 R /StructParent 3 "),
            6: _image(2, 1, bytes([255, 128])),
            7: "<< /Type /OCG /Name (Pictures) >>",
            8: (
                "<< /Type /XObject /Subtype /Form /BBox [0 0 100 40] "
                f"/Resources << /XObject << /Im0 5 0 R >> >> /Length {len(form)} >>\nstream\n"
            ).encode()
            + form
            + b"\nendstream",
        }
    )


def _pixmap(color: tuple[int, ...], size: int = 4) -> pylopdf.Pixmap:
    return pylopdf.Pixmap.from_array(memoryview(bytes(list(color) * size * size)).cast("B", (size, size, len(color))))


def _pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int]:
    pixmap = page.pixmap()
    offset = y * pixmap.stride + x * 3
    data = bytes(pixmap)
    return data[offset], data[offset + 1], data[offset + 2]


def test_replace_draws_the_new_image_at_every_placement() -> None:
    doc = pylopdf.open(_shared_image_pdf())
    image = doc.images[5]

    image.replace(_pixmap((255, 0, 0)))

    page = doc.pages[0]
    assert _pixel(page, 20, 20) == (255, 0, 0)
    assert _pixel(page, 70, 20) == (255, 0, 0)
    assert [placement.xref for placement in page.content().images] == [5, 5]
    assert (image.width, image.height, image.colorspace, image.soft_mask) == (4, 4, "DeviceRGB", None)
    # The layer and the structure parent belong to the placements and stay.
    stream = doc.objects[5]
    assert isinstance(image.layer, pylopdf.Layer)
    assert stream["StructParent"] == 3
    reopened = pylopdf.open(doc.to_bytes())
    assert 6 not in reopened.images
    assert _pixel(reopened.pages[0], 70, 20) == (255, 0, 0)


def test_replace_takes_encoded_images_and_their_alpha(tmp_path: Path) -> None:
    doc = pylopdf.open(_shared_image_pdf())
    image = doc.images[5]
    jpeg = _pixmap((0, 0, 255), 16).to_bytes(format="jpeg")

    image.replace(jpeg)
    assert image.filters == ("DCTDecode",)
    assert doc.objects[5].read(decode=False) == jpeg

    png = _pixmap((0, 255, 0, 128), 8).to_bytes()
    path = tmp_path / "half.png"
    path.write_bytes(png)
    image.replace(path)
    assert image.filters == ("FlateDecode",)
    assert image.soft_mask is not None
    red, green, _ = _pixel(doc.pages[0], 20, 20)
    assert green > 100
    assert red > 100


def test_replace_refuses_bad_input_without_changing_anything() -> None:
    doc = pylopdf.open(_shared_image_pdf())
    image = doc.images[5]
    before = doc.to_bytes()
    jpeg = _pixmap((0, 0, 255), 16).to_bytes(format="jpeg")

    with pytest.raises(pylopdf.LimitError) as excinfo:
        image.replace(jpeg, max_size=10)
    assert excinfo.value.code == "image_input_size"
    with pytest.raises(pylopdf.LimitError) as excinfo:
        image.replace(_pixmap((0, 0, 0), 16).to_bytes(), max_pixels=100)
    assert excinfo.value.code == "image_pixel_count"
    with pytest.raises(pylopdf.PdfError, match="unsupported image format"):
        image.replace(b"GIF89a not an image")
    with pytest.raises(FileNotFoundError):
        image.replace("missing-image.png")
    with pytest.raises(TypeError, match="Pixmap"):
        image.replace(42)  # ty: ignore[invalid-argument-type]
    for value in (0, -1):
        with pytest.raises(ValueError, match="max_size"):
            image.replace(jpeg, max_size=value)
    assert doc.to_bytes() == before
    doc.objects[5] = 1
    with pytest.raises(pylopdf.StaleObjectError, match="no longer an image"):
        image.replace(jpeg)


def test_canvas_draws_image_objects_as_it_draws_their_sources() -> None:
    source = pylopdf.Document()
    with source.pages.new((300, 200)).draw() as canvas:
        canvas.image((10, 10, 110, 60), _pixmap((255, 0, 0, 255), 4))
    (placement,) = source.pages[0].content().images
    image = source.images[placement.xref]
    translucent = _pixmap((0, 0, 255, 128), 4)

    def draw(page: pylopdf.Page, first: object, second: object) -> None:
        with page.draw() as canvas:
            canvas.rect((0, 0, 300, 200), fill=pylopdf.Color(1, 1, 0))
            canvas.image((10, 10, 110, 60), first)  # ty: ignore[invalid-argument-type]
            canvas.image((150, 10, 250, 110), second, rotate=90, keep_aspect=False)  # ty: ignore[invalid-argument-type]

    doc = pylopdf.Document()
    page = doc.pages.new((300, 200))
    with doc.pages.new((300, 200)).draw() as canvas:
        canvas.image((0, 0, 10, 10), translucent)
    other = doc.images[doc.pages[1].content().images[0].xref]
    page = doc.pages[0]
    draw(page, image, other)
    expected = pylopdf.Document()
    draw(expected.pages.new((300, 200)), _pixmap((255, 0, 0, 255), 4), translucent)

    assert bytes(page.pixmap()) == bytes(expected.pages[0].pixmap())
    # The image of this document is drawn again; the other one is copied once.
    assert len(pylopdf.open(doc.to_bytes()).images) == 3
    builder = pylopdf.create()
    with builder.page((300, 200)) as canvas:
        canvas.image((10, 10, 110, 60), image)
    assert len(builder.finish().pages[0].content().images) == 1
    with doc.pages[0].draw() as canvas, pytest.raises(TypeError, match="ImageObject"):
        canvas.image((0, 0, 10, 10), 42)  # ty: ignore[invalid-argument-type]


def test_a_replaced_image_is_not_placed_again_for_its_old_samples() -> None:
    doc = pylopdf.Document()
    old = _pixmap((255, 0, 0)).to_bytes()
    with doc.pages.new((100, 50)).draw() as canvas:
        canvas.image((0, 0, 50, 50), old)
    (first,) = doc.pages[0].content().images
    doc.images[first.xref].replace(_pixmap((0, 0, 255)))

    with doc.pages[0].draw() as canvas:
        canvas.image((50, 0, 100, 50), old)

    page = doc.pages[0]
    assert len({placement.xref for placement in page.content().images}) == 2
    assert _pixel(page, 25, 25) == (0, 0, 255)
    assert _pixel(page, 75, 25) == (255, 0, 0)


def test_a_new_documents_canvas_draws_image_items_as_their_pixels() -> None:
    source = pylopdf.Document()
    with source.pages.new((300, 200)).draw() as canvas:
        canvas.rect((0, 0, 300, 200), fill=pylopdf.Color(1, 1, 0))
        with canvas.opacity(0.5):
            canvas.image((20, 30, 120, 80), _pixmap((255, 0, 0, 255)), rotate=90, keep_aspect=False)
        canvas.image((150, 10, 250, 110), _pixmap((0, 0, 255, 128)))
    items = source.pages[0].content(text_runs=False, paths=False).images

    builder = pylopdf.create()
    with builder.page((300, 200)) as canvas:
        canvas.rect((0, 0, 300, 200), fill=pylopdf.Color(1, 1, 0))
        for item in items:
            canvas.draw(item)

    assert bytes(builder.finish().pages[0].pixmap()) == bytes(source.pages[0].pixmap())
