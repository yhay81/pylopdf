"""Tests for Page objects, rotation, and page boxes."""

from __future__ import annotations

import pytest
from conftest import png_size

import pylopdf


def test_page_on_closed_document(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    page = doc.pages[0]
    doc.close()
    with pytest.raises(pylopdf.DocumentClosedError):
        _ = page.rotation


def test_set_mediabox_roundtrip(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    doc.pages[0].boxes.media = (0, 0, 300, 400)
    assert doc.pages[0].boxes.media == pylopdf.Rect(0.0, 0.0, 300.0, 400.0)
    # Persist the box through save and apply it to rendering dimensions.
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].boxes.media == pylopdf.Rect(0.0, 0.0, 300.0, 400.0)
    assert png_size(reopened.pages[0].pixmap(alpha=True).to_bytes()) == (300, 400)


@pytest.mark.parametrize(
    "rect",
    [
        (0, 0, -10, 400),
        (0, 0, 612, 0),
        (0, 0, float("nan"), 400),
        (0, 0, 1e39, 400),
        (0, 0, 612),
        "abcd",
    ],
)
def test_set_box_invalid(one_page_pdf: bytes, rect: object) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match="MediaBox"):
        doc.pages[0].boxes.media = rect  # ty: ignore[invalid-assignment]


def test_rotation_roundtrip_and_render(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    page = doc.pages[0]
    assert page.rotation == 0
    page.rotation = 90
    assert page.rotation == 90
    # Rotation swaps rendered dimensions from 612 x 792 to 792 x 612.
    assert png_size(doc.pages[0].pixmap(alpha=True).to_bytes()) == (792, 612)
    page.rotation = -90
    assert page.rotation == 270
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].rotation == 270


def test_page_repr(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    assert repr(doc.pages[0]) == "<Page 0 of <pylopdf.Document>>"
