"""JPEG 2000 input: decoded into Pixmaps, drawn as JPXDecode data, and extracted unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest

import pylopdf

IMAGES = Path(__file__).parent / "assets" / "images"
RED, GREEN, BLUE, WHITE, YELLOW = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (255, 255, 0)


def _pixel(pixmap: pylopdf.Pixmap, x: int, y: int) -> tuple[int, ...]:
    data = bytes(pixmap)
    offset = y * pixmap.stride + x * pixmap.channels
    return tuple(data[offset : offset + pixmap.channels])


def _draw_fixtures(canvas: pylopdf.Canvas) -> None:
    """Draw every fixture on a yellow 200 by 100 page."""
    canvas.rect((0, 0, 200, 100), fill=pylopdf.Color(1, 1, 0))
    canvas.image((0, 0, 64, 32), (IMAGES / "rgb.jp2").read_bytes())
    canvas.image((70, 0, 102, 32), IMAGES / "rgba.jp2")
    canvas.image((110, 0, 142, 16), IMAGES / "gray.j2k")
    canvas.image((0, 40, 64, 72), IMAGES / "palette.jp2")


def _check_fixture_pixels(page: pylopdf.Page) -> None:
    pixmap = page.pixmap()
    assert [_pixel(pixmap, x, 16) for x in (8, 24, 40, 56)] == [RED, GREEN, BLUE, WHITE]
    # Opaque blue on the left of the RGBA image, a quarter of blue over yellow on the right.
    assert _pixel(pixmap, 74, 16) == BLUE
    assert _pixel(pixmap, 94, 16) == (191, 191, 64)
    assert [_pixel(pixmap, x, 8) for x in (116, 136)] == [(0, 0, 0), WHITE]
    assert [_pixel(pixmap, x, 56) for x in (8, 24, 40, 56)] == [RED, GREEN, BLUE, WHITE]


@pytest.mark.parametrize(
    ("name", "size", "channels", "resolution", "pixels"),
    [
        ("rgb.jp2", (32, 16), 3, (150, 300), [RED, GREEN, BLUE, WHITE]),
        ("palette.jp2", (32, 16), 3, (96, 96), [RED, GREEN, BLUE, WHITE]),
        ("rgba.jp2", (16, 16), 4, (96, 96), [(0, 0, 255, 255), (0, 0, 255, 64)]),
        ("gray.j2k", (16, 8), 1, (96, 96), [(0,), (255,)]),
    ],
)
def test_pixmaps_decode_jpeg_2000(
    name: str, size: tuple[int, int], channels: int, resolution: tuple[int, int], pixels: list[tuple[int, ...]]
) -> None:
    for pixmap in (pylopdf.Pixmap.from_file(IMAGES / name), pylopdf.Pixmap.from_bytes((IMAGES / name).read_bytes())):
        assert (pixmap.width, pixmap.height, pixmap.channels) == (*size, channels)
        assert pixmap.resolution == resolution
        step = pixmap.width // len(pixels)
        assert [_pixel(pixmap, step * index + step // 2, 4) for index in range(len(pixels))] == pixels


def test_jpeg_2000_input_is_limited_and_checked() -> None:
    data = (IMAGES / "rgb.jp2").read_bytes()
    with pytest.raises(pylopdf.LimitError, match="JPEG 2000 image contains 512 pixels") as excinfo:
        pylopdf.Pixmap.from_bytes(data, max_pixels=511)
    assert excinfo.value.code == "image_pixel_count"
    doc = pylopdf.Document()
    with doc.pages.new((100, 100)).draw() as canvas, pytest.raises(pylopdf.LimitError) as excinfo:
        canvas.image((0, 0, 10, 10), data, max_pixels=511)
    assert excinfo.value.code == "image_pixel_count"
    broken = data[:60]
    with pytest.raises(pylopdf.PdfError, match="JPEG 2000"):
        pylopdf.Pixmap.from_bytes(broken)
    with doc.pages[0].draw() as canvas, pytest.raises(pylopdf.PdfError, match="JPEG 2000"):
        canvas.image((0, 0, 10, 10), broken)
    assert len(doc.images) == 0


def test_canvas_stores_jpeg_2000_data_as_it_is() -> None:
    doc = pylopdf.Document()
    with doc.pages.new((200, 100)).draw() as canvas:
        _draw_fixtures(canvas)
    _check_fixture_pixels(doc.pages[0])

    stored = {image.obj.read(decode=False): image for image in doc.images.values()}
    for name in ("rgb.jp2", "rgba.jp2", "gray.j2k"):
        data = (IMAGES / name).read_bytes()
        image = stored[data]
        assert image.filters == ("JPXDecode",)
        # The data states its own color space and depth, and alpha for the RGBA image.
        assert "ColorSpace" not in image.obj
        assert "BitsPerComponent" not in image.obj
        assert image.obj.get("SMaskInData") == (1 if name == "rgba.jp2" else None)
        encoded = image.extract()
        assert (encoded.format, encoded.data) == ("jpx", data)
    assert stored[(IMAGES / "rgb.jp2").read_bytes()].extract().resolution == (150, 300)
    # Palette indices would draw as gray in hayro, so a palette image is decoded.
    palette = next(image for image in doc.images.values() if image.filters == ("FlateDecode",))
    assert (palette.width, palette.height, palette.colorspace) == (32, 16, "DeviceRGB")
    assert palette.extract().format == "png"

    reopened = pylopdf.open(doc.to_bytes())
    _check_fixture_pixels(reopened.pages[0])


def test_new_documents_decode_jpeg_2000() -> None:
    builder = pylopdf.create()
    with builder.page((200, 100)) as canvas:
        _draw_fixtures(canvas)
    doc = builder.finish()

    _check_fixture_pixels(doc.pages[0])
    assert all("JPXDecode" not in image.filters for image in doc.images.values())


def test_replace_takes_jpeg_2000() -> None:
    doc = pylopdf.Document()
    with doc.pages.new((64, 32)).draw() as canvas:
        canvas.image((0, 0, 64, 32), (IMAGES / "gray.j2k").read_bytes(), keep_aspect=False)
    (image,) = doc.images.values()

    image.replace(IMAGES / "rgba.jp2")

    assert (image.width, image.height, image.filters) == (16, 16, ("JPXDecode",))
    assert image.obj.get("SMaskInData") == 1
    assert _pixel(doc.pages[0].pixmap(), 8, 16) == BLUE
