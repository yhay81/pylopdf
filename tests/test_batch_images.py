"""Images inside drawing batches keep their place in drawing order."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import GRAY_GRADIENT_JPEG, solid_png

import pylopdf

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
A4 = (595.28, 841.89)
WHITE = (255, 255, 255)
GREEN_PNG = solid_png(4, 4, (0, 128, 0))
MARKER = (1.0, 0.0, 0.0)


@pytest.fixture(scope="module")
def regular() -> pylopdf.Font:
    return pylopdf.Font(NOTO_SANS_JP)


def _pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int]:
    pixmap = page.get_pixmap(background=WHITE)
    offset = y * pixmap.stride + x * 4
    red, green, blue = pixmap.samples[offset : offset + 3]
    return (red, green, blue)


def _image_xobjects(data: bytes) -> int:
    return data.count(b"/Subtype/Image") + data.count(b"/Subtype /Image")


def test_markers_drawn_after_a_photo_stay_on_top(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    with doc.batch() as batch:
        canvas = batch.new_page(200, 120)
        canvas.draw_rect((0, 0, 200, 120), color=None, fill=(0.9, 0.9, 0.9))
        canvas.insert_image((20, 20, 120, 100), stream=GREEN_PNG, keep_proportion=False)
        canvas.draw_circle((70, 60), 8, color=(1, 1, 1), fill=MARKER, width=1.2)
        canvas.insert_text((130, 60), "写真1", font=regular, fontsize=10)

    page = doc[0]
    assert _pixel(page, 70, 60) == (255, 0, 0)
    assert _pixel(page, 30, 30) == (0, 128, 0)
    assert _pixel(page, 10, 10) == (230, 230, 230)
    assert page.get_text().strip() == "写真1"
    [image] = page.get_images()
    assert image["bbox"] == pytest.approx((20, 20, 120, 100), abs=1e-3)
    drawings = page.get_drawings()
    assert [drawing["type"] for drawing in drawings] == ["f", "fs"]


def test_images_keep_order_below_existing_content() -> None:
    doc = pylopdf.Document()
    page = doc.new_page(width=200, height=120)
    page.draw_rect((60, 40, 80, 80), color=None, fill=(0, 0, 1))

    with doc.batch() as batch:
        canvas = batch.page(0, overlay=False)
        canvas.insert_image((20, 20, 120, 100), stream=GREEN_PNG, keep_proportion=False)
        canvas.draw_circle((100, 60), 8, color=None, fill=MARKER)

    assert _pixel(doc[0], 70, 60) == (0, 0, 255)
    assert _pixel(doc[0], 100, 60) == (255, 0, 0)
    assert _pixel(doc[0], 30, 30) == (0, 128, 0)


def test_identical_images_are_embedded_once_for_the_batch(regular: pylopdf.Font) -> None:
    pixmap = _pixmap()
    doc = pylopdf.Document()
    with doc.batch() as batch:
        for number in range(3):
            canvas = batch.new_page(200, 120)
            canvas.insert_image((10, 10, 40, 40), stream=GREEN_PNG)
            canvas.insert_image((50, 10, 80, 40), pixmap=pixmap)
            canvas.insert_text((10, 100), f"頁{number}", font=regular)
        batch.new_page(200, 120).insert_image((10, 10, 40, 40), stream=bytes(GREEN_PNG))

    data = doc.tobytes()
    assert _image_xobjects(data) == 2
    assert data.count(b"/FontFile") == 1
    assert [len(page.get_images()) for page in doc] == [2, 2, 2, 1]


def _pixmap() -> pylopdf.Pixmap:
    doc = pylopdf.Document()
    page = doc.new_page(width=10, height=10)
    page.draw_rect((0, 0, 10, 10), color=None, fill=(0.2, 0.4, 0.8))
    return page.get_pixmap()


def test_jpeg_passes_through_and_png_alpha_becomes_a_soft_mask() -> None:
    doc = pylopdf.Document()
    with doc.batch() as batch:
        canvas = batch.new_page(200, 120)
        canvas.insert_image((10, 10, 74, 74), stream=GRAY_GRADIENT_JPEG)
        canvas.insert_image((100, 10, 140, 50), stream=solid_png(4, 4, (255, 0, 0), alpha=128))

    data = doc.tobytes()
    assert GRAY_GRADIENT_JPEG in data
    assert b"/SMask" in data
    images = sorted(doc[0].get_images(), key=lambda image: image["bbox"][0])
    assert images[0]["ext"] == "jpeg"
    assert images[0]["image"] == GRAY_GRADIENT_JPEG
    assert images[1]["bbox"] == pytest.approx((100, 10, 140, 50), abs=1e-3)
    red, green, blue = _pixel(doc[0], 120, 30)
    assert red == 255
    assert 120 <= green <= 135
    assert 120 <= blue <= 135


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("keep_proportion", [True, False])
def test_placement_matches_page_insert_image(rotate: int, keep_proportion: bool) -> None:  # noqa: FBT001
    def build(*, batched: bool) -> pylopdf.Document:
        doc = pylopdf.Document()
        page = doc.new_page(width=300, height=200)
        page.set_cropbox((10, 5, 290, 195))
        page.set_rotation(90)
        rect = (20, 30, 150, 90)
        if batched:
            with doc.batch() as batch:
                batch.page(0).insert_image(
                    rect,
                    stream=GRAY_GRADIENT_JPEG,
                    rotate=rotate,
                    keep_proportion=keep_proportion,
                )
        else:
            page.insert_image(rect, stream=GRAY_GRADIENT_JPEG, rotate=rotate, keep_proportion=keep_proportion)
        return doc

    [expected] = build(batched=False)[0].get_images()
    [actual] = build(batched=True)[0].get_images()
    assert actual["bbox"] == pytest.approx(expected["bbox"], abs=1e-3)
    assert actual["image"] == expected["image"]


def test_an_image_from_a_file_and_image_only_canvases(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(GRAY_GRADIENT_JPEG)
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)

    with doc.batch() as batch:
        batch.page(0).insert_image((10, 10, 50, 50), filename=path)
        batch.new_page(100, 100).insert_image((0, 0, 100, 100), filename=str(path), keep_proportion=False)

    assert [len(page.get_images()) for page in doc] == [1, 1]
    assert doc[1].get_images()[0]["bbox"] == pytest.approx((0, 0, 100, 100), abs=1e-3)
    assert _image_xobjects(doc.tobytes()) == 1


def test_image_input_is_validated_when_recorded() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    before = doc.tobytes()
    batch = doc.batch(max_image_size=200)
    canvas = batch.page(0)

    with pytest.raises(pylopdf.PdfError, match="unsupported image format"):
        canvas.insert_image((0, 0, 10, 10), stream=b"GIF89a")
    with pytest.raises(pylopdf.LimitError) as exc_info:
        canvas.insert_image((0, 0, 10, 10), stream=GRAY_GRADIENT_JPEG, max_size=len(GRAY_GRADIENT_JPEG) - 1)
    assert exc_info.value.code == "image_input_size"
    with pytest.raises(pylopdf.LimitError) as exc_info:
        canvas.insert_image((0, 0, 10, 10), stream=solid_png(4, 4, (0, 0, 0)), max_pixels=15)
    assert exc_info.value.code == "image_pixel_count"
    canvas.insert_image((0, 0, 10, 10), stream=GREEN_PNG)
    canvas.insert_image((20, 0, 30, 10), stream=GREEN_PNG)
    with pytest.raises(pylopdf.LimitError) as exc_info:
        canvas.insert_image((0, 0, 10, 10), stream=GRAY_GRADIENT_JPEG)
    assert exc_info.value.code == "image_input_size"
    with pytest.raises(ValueError, match="exactly one"):
        canvas.insert_image((0, 0, 10, 10))
    with pytest.raises(ValueError, match="multiple of 90"):
        canvas.insert_image((0, 0, 10, 10), stream=GREEN_PNG, rotate=45)
    for value in (0, -1, True):
        with pytest.raises((TypeError, ValueError), match="max_image_size"):
            doc.batch(max_image_size=value)
    assert doc.tobytes() == before

    batch.commit()
    assert len(doc[0].get_images()) == 2
    assert _image_xobjects(doc.tobytes()) == 1


def test_site_survey_photo_page_shape(regular: pylopdf.Font) -> None:
    """A photo with a note box, a numbered marker, and a heading arrow on A4."""
    doc = pylopdf.Document()
    with doc.batch() as batch:
        canvas = batch.new_page(*A4)
        canvas.insert_text((56, 60), "調査記録", font=regular, fontsize=14)
        canvas.draw_rect((56, 80, 539, 420), color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
        canvas.insert_image((60, 84, 535, 416), stream=GRAY_GRADIENT_JPEG)
        canvas.draw_circle((200, 200), 3, color=None, fill=(0.71, 0.14, 0.09))
        canvas.draw_rect((206, 205, 330, 225), color=(0.71, 0.14, 0.09), fill=(1, 1, 1), width=0.75)
        canvas.insert_text((209, 219), "ひび割れあり", font=regular, fontsize=9)
        canvas.draw_line((300, 300), (300, 282), color=(0.83, 0.26, 0.05), width=1.6)
        canvas.draw_polyline([(300, 277), (297, 287), (303, 287)], closed=True, color=None, fill=(0.83, 0.26, 0.05))
        canvas.draw_circle((300, 300), 7, color=(1, 1, 1), fill=(0.83, 0.26, 0.05), width=1.2)
        end = canvas.insert_text((296, 303), "1", font=regular, fontsize=7.5, color=(1, 1, 1))
        assert end[0] > 296

    data = doc.tobytes()
    reopened = pylopdf.open(stream=data)
    page = reopened[0]
    assert page.get_text().splitlines() == ["調査記録", "ひび割れあり", "1"]
    assert len(page.get_images()) == 1
    assert [drawing["type"] for drawing in page.get_drawings()] == ["fs", "f", "fs", "s", "f", "fs"]
    # The note box, beside its text, is drawn over the photo.
    assert _pixel(page, 320, 215) == (255, 255, 255)
    assert _pixel(page, 400, 350) != (255, 255, 255)
    assert data.count(b"/FontFile") == 1
