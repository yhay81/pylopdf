"""pymupdf's page-box convention."""

from __future__ import annotations

import pytest
from conftest import build_raw_pdf

import pylopdf


def _page(page: str, pages: str = "") -> pylopdf.Page:
    doc = pylopdf.open(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: f"<< /Type /Pages /Kids [3 0 R] /Count 1 {pages} >>",
                3: f"<< /Type /Page /Parent 2 0 R {page} >>",
            }
        )
    )
    return doc[0]


def test_mediabox_is_stored_and_normalized() -> None:
    page = _page("/MediaBox [612 792 10 20]")

    assert page.mediabox == pylopdf.Rect(10, 20, 612, 792)


def test_cropbox_flips_y_against_the_mediabox_top() -> None:
    page = _page("/MediaBox [10 20 610 820] /CropBox [50 100 400 700]")

    assert page.cropbox == pylopdf.Rect(50, 120, 400, 720)
    assert page.rect == pylopdf.Rect(0, 0, 350, 600)


def test_cropbox_defaults_to_the_mediabox() -> None:
    page = _page("/MediaBox [0 0 300 400]")

    assert page.cropbox == page.mediabox == pylopdf.Rect(0, 0, 300, 400)


def test_set_cropbox_round_trips_and_stores_pdf_coordinates() -> None:
    page = _page("/MediaBox [0 0 600 800]")

    page.set_cropbox((100, 50, 500, 350))

    assert page.cropbox == pylopdf.Rect(100, 50, 500, 350)
    assert list(page.obj["CropBox"]) == [100, 450, 500, 750]
    assert page.rect == pylopdf.Rect(0, 0, 400, 300)
    pixmap = page.get_pixmap(dpi=72, alpha=True)
    assert (pixmap.width, pixmap.height) == (400, 300)


@pytest.mark.parametrize(
    "rect",
    [(-1, 0, 100, 100), (0, 0, 601, 100), (0, -1, 100, 100), (0, 0, 100, 801), (100, 0, 100, 50), (0, 50, 100, 50)],
)
def test_set_cropbox_requires_a_box_with_area_inside_the_mediabox(rect: tuple[float, ...]) -> None:
    page = _page("/MediaBox [0 0 600 800]")

    with pytest.raises(ValueError, match=r"CropBox not in MediaBox|x0 < x1 and y0 < y1"):
        page.set_cropbox(rect)

    assert "CropBox" not in page.obj
    assert page.cropbox == page.mediabox


def test_set_mediabox_removes_the_other_page_boxes() -> None:
    boxes = " ".join(f"/{key} [10 10 90 90]" for key in ("CropBox", "BleedBox", "TrimBox", "ArtBox"))
    page = _page(f"/MediaBox [0 0 100 100] {boxes}")

    page.set_mediabox((0, 0, 200, 300))

    assert page.mediabox == pylopdf.Rect(0, 0, 200, 300)
    assert page.cropbox == page.mediabox
    assert page.rect == pylopdf.Rect(0, 0, 200, 300)
    for key in ("CropBox", "BleedBox", "TrimBox", "ArtBox"):
        assert key not in page.obj
    reopened = pylopdf.open(stream=page.parent.tobytes())[0]
    assert reopened.cropbox == reopened.mediabox == pylopdf.Rect(0, 0, 200, 300)


def test_set_mediabox_overrides_an_inherited_cropbox() -> None:
    page = _page("/MediaBox [0 0 100 100]", pages="/CropBox [10 10 50 50]")
    assert page.cropbox == pylopdf.Rect(10, 50, 50, 90)

    page.set_mediabox((0, 0, 400, 500))

    assert page.cropbox == page.mediabox == pylopdf.Rect(0, 0, 400, 500)
    assert page.rect == pylopdf.Rect(0, 0, 400, 500)


@pytest.mark.parametrize("rect", [(0, 0, 0, 100), (0, 0, 100, 0), (100, 0, 0, 100)])
def test_set_mediabox_refuses_an_empty_rectangle(rect: tuple[float, ...]) -> None:
    page = _page("/MediaBox [0 0 100 100] /CropBox [10 10 90 90]")

    with pytest.raises(ValueError, match="x0 < x1 and y0 < y1"):
        page.set_mediabox(rect)

    assert page.mediabox == pylopdf.Rect(0, 0, 100, 100)
    assert "CropBox" in page.obj
