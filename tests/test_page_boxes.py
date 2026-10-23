"""Page boxes in PDF user space, ``Page.rotation``, and ``Page.user_to_display``."""

from __future__ import annotations

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Matrix, Point, Rect

A4 = (210 * 72 / 25.4, 297 * 72 / 25.4)
OTHER_BOXES = ("CropBox", "BleedBox", "TrimBox", "ArtBox")


def _page(page: str, pages: str = "") -> pylopdf.Page:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: f"<< /Type /Pages /Kids [3 0 R] /Count 1 {pages} >>",
                3: f"<< /Type /Page /Parent 2 0 R {page} >>",
            }
        )
    )
    return doc.pages[0]


def test_boxes_read_as_stored_with_ordered_corners() -> None:
    page = _page("/MediaBox [612 792 10 20] /CropBox [50 100 400 700] /ArtBox [60 110 70 120]")
    boxes = page.boxes

    assert boxes.media == Rect(10, 20, 612, 792)
    assert boxes.crop == Rect(50, 100, 400, 700)
    assert boxes.art == Rect(60, 110, 70, 120)
    assert page.rect == Rect(0, 0, 350, 600)


def test_boxes_take_their_defaults() -> None:
    page = _page("/MediaBox [0 0 300 400] /CropBox [10 10 200 300]")
    assert page.boxes.bleed == page.boxes.trim == page.boxes.art == Rect(10, 10, 200, 300)

    page = _page("/MediaBox [0 0 300 400]")
    assert page.boxes.crop == page.boxes.bleed == page.boxes.media == Rect(0, 0, 300, 400)

    # hayro draws a page without a MediaBox as A4, and so does page.rect.
    assert _page("").boxes.media == Rect(0, 0, *A4)


def test_only_the_mediabox_and_cropbox_inherit() -> None:
    """ISO 32000-2 Table 31 lets a page inherit its MediaBox and CropBox, not the others."""
    inherited = "/MediaBox [0 0 300 400] /CropBox [10 10 250 350] /TrimBox [20 20 30 30] /ArtBox [20 20 30 30]"
    page = _page("", pages=inherited)

    assert page.boxes.media == Rect(0, 0, 300, 400)
    assert page.boxes.crop == Rect(10, 10, 250, 350)
    assert page.boxes.trim == page.boxes.art == Rect(10, 10, 250, 350)


def test_assigning_a_box_stores_it_in_user_space() -> None:
    page = _page("/MediaBox [0 0 600 800]")

    page.boxes.crop = (100, 50, 500, 350)
    page.boxes.trim = Rect(110, 60, 490, 340)

    assert page.boxes.crop == Rect(100, 50, 500, 350)
    assert list(page.obj["CropBox"]) == [100, 50, 500, 350]
    assert list(page.obj["TrimBox"]) == [110, 60, 490, 340]
    assert page.rect == Rect(0, 0, 400, 300)
    pixmap = page.pixmap(dpi=72, alpha=True)
    assert (pixmap.width, pixmap.height) == (400, 300)
    reopened = pylopdf.open(page.document.to_bytes()).pages[0]
    assert (reopened.boxes.crop, reopened.boxes.trim) == (Rect(100, 50, 500, 350), Rect(110, 60, 490, 340))


@pytest.mark.parametrize("key", ["crop", "bleed", "trim", "art"])
@pytest.mark.parametrize(
    ("rect", "match"),
    [
        ((-1, 0, 100, 100), "must lie inside the MediaBox"),
        ((0, 0, 601, 100), "must lie inside the MediaBox"),
        ((0, 0, 100, 801), "must lie inside the MediaBox"),
        ((100, 0, 100, 50), "x0 < x1 and y0 < y1"),
        ((0, 50, 100, 50), "x0 < x1 and y0 < y1"),
        ((0, 0, float("nan"), 50), "x0 < x1 and y0 < y1"),
        ("abcd", "must be 4 numbers"),
    ],
)
def test_assigned_boxes_need_area_inside_the_mediabox(key: str, rect: object, match: str) -> None:
    page = _page("/MediaBox [0 0 600 800]")

    with pytest.raises(ValueError, match=match):
        setattr(page.boxes, key, rect)

    assert not any(name in page.obj for name in OTHER_BOXES)


def test_deleting_a_box_restores_its_default() -> None:
    boxes = " ".join(f"/{key} [10 10 90 90]" for key in OTHER_BOXES)
    page = _page(f"/MediaBox [0 0 100 100] {boxes}")

    del page.boxes.trim
    assert "TrimBox" not in page.obj
    assert page.boxes.trim == Rect(10, 10, 90, 90)  # The CropBox.

    del page.boxes.crop
    assert "CropBox" not in page.obj
    assert page.boxes.crop == Rect(0, 0, 100, 100)
    assert page.boxes.trim == Rect(0, 0, 100, 100)
    assert page.boxes.art == Rect(10, 10, 90, 90)  # Still the page's own.

    del page.boxes.art  # Removing an absent box changes nothing.
    del page.boxes.art
    with pytest.raises(AttributeError):
        del page.boxes.media  # ty: ignore[invalid-assignment]


def test_deleting_a_cropbox_shows_the_whole_mediabox_when_an_ancestor_has_one() -> None:
    page = _page("/MediaBox [0 0 100 100] /CropBox [20 20 80 80]", pages="/CropBox [10 10 50 50]")

    del page.boxes.crop

    assert page.boxes.crop == page.boxes.media == Rect(0, 0, 100, 100)
    assert page.rect == Rect(0, 0, 100, 100)
    reopened = pylopdf.open(page.document.to_bytes()).pages[0]
    assert reopened.rect == Rect(0, 0, 100, 100)


def test_assigning_the_mediabox_removes_the_other_page_boxes() -> None:
    boxes = " ".join(f"/{key} [10 10 90 90]" for key in OTHER_BOXES)
    page = _page(f"/MediaBox [0 0 100 100] {boxes}")

    page.boxes.media = (0, 0, 200, 300)

    assert page.boxes.media == Rect(0, 0, 200, 300)
    assert page.boxes.crop == page.boxes.art == page.boxes.media
    assert page.rect == Rect(0, 0, 200, 300)
    for key in OTHER_BOXES:
        assert key not in page.obj
    reopened = pylopdf.open(page.document.to_bytes()).pages[0]
    assert reopened.boxes.crop == reopened.boxes.media == Rect(0, 0, 200, 300)


def test_assigning_the_mediabox_overrides_an_inherited_cropbox() -> None:
    page = _page("/MediaBox [0 0 100 100]", pages="/CropBox [10 10 50 50]")
    assert page.boxes.crop == Rect(10, 10, 50, 50)

    page.boxes.media = (0, 0, 400, 500)

    assert page.boxes.crop == page.boxes.media == Rect(0, 0, 400, 500)
    assert page.rect == Rect(0, 0, 400, 500)


@pytest.mark.parametrize("rect", [(0, 0, 0, 100), (0, 0, 100, 0), (100, 0, 0, 100)])
def test_assigning_an_empty_mediabox_raises(rect: tuple[float, ...]) -> None:
    page = _page("/MediaBox [0 0 100 100] /CropBox [10 10 90 90]")

    with pytest.raises(ValueError, match="x0 < x1 and y0 < y1"):
        page.boxes.media = rect

    assert page.boxes.media == Rect(0, 0, 100, 100)
    assert "CropBox" in page.obj


def test_boxes_view_reads_the_page_and_goes_stale_with_it() -> None:
    page = _page("/MediaBox [0 0 100 100]")
    boxes = page.boxes
    page.obj["CropBox"] = [10, 10, 20, 20]

    assert boxes.crop == Rect(10, 10, 20, 20)
    assert repr(boxes) == (
        "PageBoxes(media=Rect(x0=0.0, y0=0.0, x1=100.0, y1=100.0), crop=Rect(x0=10.0, y0=10.0, x1=20.0, y1=20.0), "
        "bleed=Rect(x0=10.0, y0=10.0, x1=20.0, y1=20.0), trim=Rect(x0=10.0, y0=10.0, x1=20.0, y1=20.0), "
        "art=Rect(x0=10.0, y0=10.0, x1=20.0, y1=20.0))"
    )
    page.document.pages.new()
    with pytest.raises(pylopdf.StalePageError):
        _ = boxes.media


def test_rotation_is_assigned_in_multiples_of_90() -> None:
    page = _page("/MediaBox [0 0 100 200]", pages="/Rotate 90")
    assert page.rotation == 90

    page.rotation = -90
    assert page.rotation == 270
    assert page.obj["Rotate"] == 270
    page.rotation = 720
    assert page.rotation == 0
    with pytest.raises(ValueError, match="multiple of 90"):
        page.rotation = 45
    with pytest.raises(TypeError, match="int"):
        page.rotation = 90.0  # ty: ignore[invalid-assignment]
    assert page.rotation == 0


@pytest.mark.parametrize(
    ("rotation", "corners"),
    [
        # Where the CropBox's bottom-left (100, 200) and top-right (400, 600) show.
        (0, [(0, 400), (300, 0)]),
        (90, [(0, 0), (400, 300)]),
        (180, [(300, 0), (0, 400)]),
        (270, [(400, 300), (0, 0)]),
    ],
)
def test_user_to_display_maps_user_space_to_display_space(rotation: int, corners: list[tuple[float, float]]) -> None:
    page = _page(f"/MediaBox [0 0 600 800] /CropBox [100 200 400 600] /Rotate {rotation}")
    matrix = page.user_to_display

    assert isinstance(matrix, Matrix)
    assert [Point(100, 200) * matrix, Point(400, 600) * matrix] == [Point(*corner) for corner in corners]
    assert page.rect == (Rect(page.boxes.crop) * matrix).normalized()
    assert Point(*corners[0]) * ~matrix == Point(100, 200)


def test_user_to_display_places_drawn_text_where_extraction_reads_it() -> None:
    page = _page("/MediaBox [0 0 600 800] /CropBox [100 200 400 600] /Rotate 90")
    target = Point(150, 300) * page.user_to_display

    with page.draw() as canvas:
        canvas.text("Probe", target, size=12)

    (word,) = page.words()
    assert word.bbox.x0 == pytest.approx(target.x, abs=0.5)
