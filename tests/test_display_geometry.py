"""Display geometry of pages whose CropBox extends past the MediaBox.

pymupdf 2.0 (MuPDF 1.28.5, and pymupdf 1.28.2 alike) defines ``Page.rect`` as
the CropBox clipped to the MediaBox and extracts text, places images and
shapes, and writes annotations in that rectangle, but its ``insert_text``
measures from the corner of the unclipped CropBox, so on these pages it draws
off the visible page. hayro-syntax 0.8 renders and extracts the same clipped
box, and when the CropBox misses the MediaBox it displays an A4 page from the
corner where clipping leaves the box, where MuPDF displays Letter at the
origin. pylopdf draws in hayro's box, so drawing, annotations, ``Page.rect``,
rendering, and extraction share one display space. pymupdf is
not a test dependency; apart from the A4 pages, every rectangle below is the
``Page.rect`` that pymupdf reports.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from conftest import build_raw_pdf, drawn_images

import pylopdf

A4 = (210 * 72 / 25.4, 297 * 72 / 25.4)
FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_HEBREW = FONT_ROOT / "pylopdf-fonts-he" / "src" / "pylopdf_fonts_he" / "NotoSansHebrew-Variable.ttf"

# Page boxes, the user-space box that rendering displays, and a user-space
# point inside that box where the page draws "Hello".
PAGES = {
    "cropbox-inside": ("/MediaBox [0 0 600 800] /CropBox [50 60 550 760]", (50, 60, 550, 760), (100, 500)),
    "cropbox-exceeds": ("/MediaBox [0 0 600 800] /CropBox [-50 -40 650 850]", (0, 0, 600, 800), (100, 500)),
    "cropbox-overlaps": ("/MediaBox [0 0 600 800] /CropBox [300 400 900 1200]", (300, 400, 600, 800), (350, 600)),
    "cropbox-misses": (
        "/MediaBox [0 0 600 800] /CropBox [700 900 800 1000]",
        (700, 900, 700 + A4[0], 900 + A4[1]),
        (750, 1200),
    ),
    "mediabox-missing": ("", (0, 0, *A4), (100, 500)),
}
ROTATIONS = (0, 90, 180, 270)

geometry = pytest.mark.parametrize("rotation", ROTATIONS)
pages = pytest.mark.parametrize("name", list(PAGES))


def _display_point(name: str, rotation: int, x: float, y: float) -> tuple[float, float]:
    """Map a user-space point to display space as PDF viewers rotate pages."""
    x0, y0, x1, y1 = PAGES[name][1]
    return {
        0: (x - x0, y1 - y),
        90: (y - y0, x - x0),
        180: (x1 - x, y - y0),
        270: (y1 - y, x1 - x),
    }[rotation]


def _display_size(name: str, rotation: int) -> tuple[float, float]:
    x0, y0, x1, y1 = PAGES[name][1]
    return (y1 - y0, x1 - x0) if rotation in (90, 270) else (x1 - x0, y1 - y0)


def _pdf(name: str, rotation: int) -> bytes:
    """Draw "Hello" at the page's point, with a URI link around it and a link to it."""
    entries, _, (x, y) = PAGES[name]
    stream = f"BT /F1 12 Tf 1 0 0 1 {x} {y} Tm (Hello) Tj ET"
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                f"<< /Type /Page /Parent 2 0 R {entries} /Rotate {rotation} "
                "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R /Annots [6 0 R 7 0 R] >>"
            ),
            4: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            6: (
                f"<< /Type /Annot /Subtype /Link /Rect [{x - 4} {y - 4} {x + 32} {y + 12}] /Border [0 0 0] "
                "/A << /S /URI /URI (https://example.com/) >> >>"
            ),
            7: f"<< /Type /Annot /Subtype /Link /Rect [0 0 1 1] /Border [0 0 0] /Dest [3 0 R /XYZ {x} {y} 0] >>",
        }
    )


def _page(name: str, rotation: int) -> pylopdf.Page:
    return pylopdf.open(stream=_pdf(name, rotation))[0]


def _origins(page: pylopdf.Page, text: str) -> list[tuple[float, float]]:
    return [
        span["origin"]
        for block in page.get_text("dict")["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        if span["text"] == text
    ]


def _words(page: pylopdf.Page, text: str) -> list[tuple[float, float, float, float]]:
    return [tuple(word[:4]) for word in page.get_text("words") if word[4] == text]


def _contains(outer: tuple[float, ...], inner: tuple[float, ...]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * 4
    red, green, blue = pixmap.samples[offset : offset + 3]
    return red, green, blue


def _png(width: int, height: int) -> bytes:
    rows = b"".join(b"\x00" + b"\x20\x40\x60" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data).to_bytes(4, "big")
        return len(data).to_bytes(4, "big") + kind + data + crc

    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes((8, 2, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


@pages
@geometry
def test_page_rect_is_the_page_that_rendering_and_extraction_display(name: str, rotation: int) -> None:
    page = _page(name, rotation)
    width, height = _display_size(name, rotation)

    assert tuple(page.rect) == pytest.approx((0, 0, width, height), abs=1e-9)
    # hayro truncates the page size to whole pixels.
    pixmap = page.get_pixmap(dpi=72, alpha=True)
    assert (pixmap.width, pixmap.height) == (int(width), int(height))
    layout = page.get_text("dict")
    assert (layout["width"], layout["height"]) == pytest.approx((width, height), abs=1e-3)
    # The box that the expectations derive from is the one that hayro shows.
    assert _origins(page, "Hello") == [pytest.approx(_display_point(name, rotation, *PAGES[name][2]), abs=1e-3)]


@pages
@geometry
@pytest.mark.parametrize("via", ["page", "shape", "embedded-font", "textbox", "ocr-layer"])
def test_text_drawn_at_a_display_point_is_extracted_there(name: str, rotation: int, via: str) -> None:
    page = _page(name, rotation)
    point = (40.0, 50.0)

    if via == "page":
        page.insert_text(point, "Probe", fontsize=12)
    elif via == "shape":
        with page.new_shape() as shape:
            shape.insert_text(point, "Probe", fontsize=12)
    elif via == "embedded-font":
        page.insert_text(point, "Probe", fontsize=12, fontfile=NOTO_SANS_HEBREW)
    elif via == "textbox":
        assert page.insert_textbox((40.0, 40.0, 300.0, 100.0), "Probe", fontsize=12) > 0
        # The first baseline lies one ascender below the top of the box.
        point = (40.0, 40.0 + pylopdf.Font("helv").ascender * 12)
    else:
        page.insert_ocr_text_layer([(40.0, 40.0, 100.0, 52.0, "Probe")])
        assert page.search_for("Probe") == [pytest.approx((40.0, 40.0, 100.0, 52.0), abs=0.1)]
        return

    assert _origins(page, "Probe") == [pytest.approx(point, abs=1e-3)]


@pages
@geometry
def test_annotations_and_links_share_the_display_space(name: str, rotation: int) -> None:
    page = _page(name, rotation)
    x, y = PAGES[name][2]

    # The link that the page stores around "Hello" surrounds the extracted word,
    # and the destination at the word's user-space origin is its display origin.
    (word,) = _words(page, "Hello")
    stored, destination = page.get_links()
    link = stored["from"]
    assert _contains(link, word)
    assert page.annots()[0].rect == link
    assert destination["to"] == pytest.approx(_display_point(name, rotation, x, y), abs=1e-3)

    # Added annotations read back as written and render where they were written.
    page.insert_link({"kind": pylopdf.LINK_URI, "from": (300, 20, 360, 60), "uri": "https://example.org/"})
    page.add_highlight_annot((10, 10, 60, 40), color=(1.0, 0.0, 0.0), opacity=1.0)
    added_link, highlight = page.annots()[2:]
    assert tuple(added_link.rect) == pytest.approx((300, 20, 360, 60), abs=1e-3)
    assert tuple(highlight.rect) == pytest.approx((10, 10, 60, 40), abs=1e-3)
    assert tuple(page.get_links()[2]["from"]) == pytest.approx((300, 20, 360, 60), abs=1e-3)
    pixmap = page.get_pixmap(dpi=72, background=(255, 255, 255), alpha=True)
    if name == "cropbox-misses":
        # hayro clips the page to the empty intersection, so nothing shows.
        assert pixmap.samples == b"\xff" * len(pixmap.samples)
        return
    assert _pixel(pixmap, 35, 25) == (255, 0, 0)
    assert _pixel(pixmap, 65, 25) == _pixel(pixmap, 35, 45) == (255, 255, 255)


@pages
@geometry
def test_images_and_shapes_land_on_their_display_rectangles(name: str, rotation: int) -> None:
    page = _page(name, rotation)

    page.insert_image((200, 100, 260, 160), stream=_png(4, 4))
    with page.new_shape() as shape:
        shape.draw_rect((100, 100, 150, 130))
        shape.finish(fill=(0.0, 0.0, 1.0), color=None)

    assert [tuple(image["bbox"]) for image in drawn_images(page)] == [pytest.approx((200, 100, 260, 160), abs=1e-3)]
    assert [tuple(drawing["rect"]) for drawing in page.get_drawings()] == [
        pytest.approx((100, 100, 150, 130), abs=1e-3)
    ]


@pages
@geometry
def test_a_placed_page_shows_what_rendering_shows_of_it(name: str, rotation: int) -> None:
    source = pylopdf.open(stream=_pdf(name, rotation))
    width, height = _display_size(name, rotation)
    target = pylopdf.Document()
    page = target.new_page(width=width, height=height)

    page.show_pdf_page((0, 0, width, height), source, 0)

    # The Form clips to the source page's visible box, so "Hello" keeps its
    # display position, and the page stays the size of the placed page.
    assert _origins(page, "Hello") == [pytest.approx(_display_point(name, rotation, *PAGES[name][2]), abs=1e-3)]
    assert tuple(page.rect) == pytest.approx((0, 0, width, height), abs=1e-3)


def test_page_boxes_follow_pymupdf_box_convention() -> None:
    page = _page("cropbox-overlaps", 90)

    assert page.mediabox == pylopdf.Rect(0, 0, 600, 800)
    # The stored CropBox [300 400 900 1200], with y flipped against the MediaBox top.
    assert page.cropbox == pylopdf.Rect(300, -400, 900, 400)
    assert page.rect == pylopdf.Rect(0, 0, 400, 300)
    assert _page("mediabox-missing", 0).mediabox == pylopdf.Rect(0, 0, *A4)


def test_setting_a_cropbox_past_the_mediabox_clips_the_page() -> None:
    doc = pylopdf.Document()
    page = doc.new_page(width=600, height=800)

    # set_cropbox refuses such a box, as pymupdf does; a file can still hold one.
    with pytest.raises(ValueError, match="CropBox not in MediaBox"):
        page.set_cropbox((-50, -50, 650, 840))
    page.obj["CropBox"] = [-50, -40, 650, 850]

    assert page.cropbox == pylopdf.Rect(-50, -50, 650, 840)
    assert page.rect == pylopdf.Rect(0, 0, 600, 800)
    page.insert_text((40, 50), "Probe", fontsize=12)
    assert _origins(page, "Probe") == [pytest.approx((40, 50), abs=1e-3)]
    pixmap = page.get_pixmap(dpi=72, alpha=True)
    assert (pixmap.width, pixmap.height) == (600, 800)
