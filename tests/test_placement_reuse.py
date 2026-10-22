"""Placing one image or page repeatedly shares one XObject.

Each case counts the XObjects that a garbage-collected save writes and compares
renders with documents that hold separate copies, so sharing can neither
duplicate objects nor change what a page shows.
"""

from __future__ import annotations

import hashlib
import re
import struct
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import build_pdf, drawn_images

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"

IMAGE = rb"/Subtype\s*/Image\b"
FORM = rb"/Subtype\s*/Form\b"
FONT_FILE = rb"/FontFile[23]?\b"

RED = (255, 0, 0)
GREEN = (0, 128, 0)
WHITE = (255, 255, 255)
LOGO_RECT = (40, 30, 160, 90)


def _png(width: int, height: int, color_type: int, rows: list[bytes], *, text: bytes | None = None) -> bytes:
    """Build an 8-bit PNG; ``text`` adds a tEXt chunk that changes no pixel."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    comment = chunk(b"tEXt", b"Comment\x00" + text) if text is not None else b""
    data = chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + row for row in rows)))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + comment + data + chunk(b"IEND", b"")


def _logo_png(*, text: bytes | None = None) -> bytes:
    """Build a 16x12 RGBA gradient with a translucent checkerboard, so it has a soft mask."""
    rows = [
        b"".join(bytes((x * 16, y * 20, 255 - x * 16, 255 if (x // 4 + y // 4) % 2 else 96)) for x in range(16))
        for y in range(12)
    ]
    return _png(16, 12, 6, rows, text=text)


def _solid_png(rgb: tuple[int, int, int]) -> bytes:
    return _png(2, 2, 2, [bytes(rgb) * 2] * 2)


def _noise_png(size: int) -> bytes:
    """Build an RGB square of noise, which JPEG recompression shrinks."""
    noise = hashlib.shake_256(b"placement reuse").digest(size * size * 3)
    return _png(size, size, 2, [noise[row * size * 3 : (row + 1) * size * 3] for row in range(size)])


def _manuscript_jpeg() -> bytes:
    images = drawn_images(pylopdf.open(ASSETS / "wdl6812-manuscript.pdf").pages[0])
    jpeg = min((image for image in images if image["ext"] == "jpeg"), key=lambda image: len(image["image"]))
    return jpeg["image"]


def _saved(doc: pylopdf.Document) -> bytes:
    return doc.tobytes(garbage=True)


def _count(data: bytes, pattern: bytes) -> int:
    return len(re.findall(pattern, data))


def _samples(page: pylopdf.Page) -> bytes:
    return page.get_pixmap(background=WHITE, alpha=True).samples


def _pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int]:
    """Return RGB at display point ``(x, y)`` after rendering on white."""
    pix = page.get_pixmap(background=WHITE, alpha=True)
    offset = y * pix.stride + x * 4
    r, g, b = pix.samples[offset : offset + 3]
    return (r, g, b)


def _pages(count: int, width: float = 200, height: float = 120) -> pylopdf.Document:
    doc = pylopdf.Document()
    for _ in range(count):
        doc.pages.new((width, height))
    return doc


def _logo_inserter(kind: str, tmp_path: Path) -> Callable[[pylopdf.Page], int]:
    """Return a function that places the same image input on a page."""
    if kind == "png":
        data = _logo_png()
        return lambda page: page.insert_image(LOGO_RECT, stream=data)
    if kind == "jpeg":
        data = _manuscript_jpeg()
        return lambda page: page.insert_image(LOGO_RECT, stream=data)
    if kind == "filename":
        path = tmp_path / "logo.png"
        path.write_bytes(_logo_png())
        return lambda page: page.insert_image(LOGO_RECT, filename=path)
    # Each call renders a new Pixmap; identical samples share one XObject.
    source = _pages(1, 32, 24)
    source.pages[0].insert_image((0, 0, 32, 24), stream=_logo_png())
    return lambda page: page.insert_image(LOGO_RECT, pixmap=source.pages[0].get_pixmap(alpha=True))


@pytest.mark.parametrize("kind", ["png", "jpeg", "filename", "pixmap"])
def test_repeated_image_input_embeds_one_xobject(kind: str, tmp_path: Path) -> None:
    insert = _logo_inserter(kind, tmp_path)
    reference = _pages(1)
    insert(reference.pages[0])
    doc = _pages(5)

    for page in doc.pages:
        insert(page)

    single = _count(_saved(reference), IMAGE)
    assert single >= 1
    assert _count(_saved(doc), IMAGE) == single
    expected = _samples(reference.pages[0])
    assert all(_samples(page) == expected for page in doc.pages)


def test_png_on_five_pages_keeps_one_image_and_one_soft_mask() -> None:
    logo = _logo_png()
    doc = _pages(5)

    for page in doc.pages:
        page.insert_image(LOGO_RECT, stream=logo)

    # One image and its soft mask; every placement used to add both again.
    assert _count(_saved(doc), IMAGE) == 2


def test_file_and_stream_input_share_one_xobject(tmp_path: Path) -> None:
    path = tmp_path / "logo.png"
    path.write_bytes(_logo_png())
    doc = _pages(2)

    doc.pages[0].insert_image(LOGO_RECT, filename=path)
    doc.pages[1].insert_image(LOGO_RECT, stream=path.read_bytes())

    assert _count(_saved(doc), IMAGE) == 2
    assert _samples(doc.pages[0]) == _samples(doc.pages[1])


def test_rect_and_rotation_do_not_split_one_image() -> None:
    placements = [((10, 10, 90, 50), 0), ((100, 10, 190, 110), 90), ((10, 60, 60, 110), 270)]
    shared = _pages(1)
    for rect, rotate in placements:
        shared.pages[0].insert_image(rect, stream=_logo_png(), rotate=rotate)
    # A tEXt chunk changes the input bytes but no pixel, so each copy is separate.
    copies = _pages(1)
    for index, (rect, rotate) in enumerate(placements):
        copies.pages[0].insert_image(rect, stream=_logo_png(text=str(index).encode()), rotate=rotate)

    assert _count(_saved(shared), IMAGE) == 2
    assert _count(_saved(copies), IMAGE) == 6
    assert _samples(shared.pages[0]) == _samples(copies.pages[0])


def test_distinct_images_keep_distinct_xobjects() -> None:
    doc = _pages(1, 200, 100)
    page = doc.pages[0]

    page.insert_image((0, 0, 100, 100), stream=_solid_png(RED), keep_proportion=False)
    page.insert_image((100, 0, 200, 100), stream=_solid_png(GREEN), keep_proportion=False)

    assert _pixel(page, 50, 50) == RED
    assert _pixel(page, 150, 50) == GREEN
    assert _count(_saved(doc), IMAGE) == 2


def test_pixmaps_with_equal_samples_but_other_dimensions_stay_distinct() -> None:
    wide = _pages(1, 4, 1)
    wide.pages[0].insert_image((0, 0, 4, 1), stream=_solid_png(RED), keep_proportion=False)
    square = _pages(1, 2, 2)
    square.pages[0].insert_image((0, 0, 2, 2), stream=_solid_png(RED), keep_proportion=False)
    wide_pixmap = wide.pages[0].get_pixmap(background=WHITE, alpha=True)
    square_pixmap = square.pages[0].get_pixmap(background=WHITE, alpha=True)
    assert wide_pixmap.samples == square_pixmap.samples
    doc = _pages(1, 200, 100)
    page = doc.pages[0]

    page.insert_image((0, 0, 100, 100), pixmap=wide_pixmap)
    page.insert_image((100, 0, 200, 100), pixmap=square_pixmap)

    # The wide image keeps its aspect ratio in a band; the square fills its rect.
    assert _pixel(page, 50, 10) == WHITE
    assert _pixel(page, 50, 50) == RED
    assert _pixel(page, 150, 10) == RED
    assert _count(_saved(doc), IMAGE) == 2


def test_repeated_page_placement_imports_one_form() -> None:
    source = pylopdf.open(ASSETS / "bill-hr815.pdf")
    reference = pylopdf.Document()
    first = reference.pages.new()
    first.show_pdf_page(first.rect, source, 0)
    doc = pylopdf.Document()

    for _ in range(5):
        page = doc.pages.new()
        page.show_pdf_page(page.rect, source, 0)
        # Reading the source is no edit, so the next placement still shares.
        source.pages[0].get_text()

    single = _saved(reference)
    repeated = _saved(doc)
    assert _count(single, FORM) == _count(repeated, FORM) == 1
    assert _count(single, FONT_FILE) == _count(repeated, FONT_FILE) == 5
    # Every further page adds its page dictionary and content stream only;
    # each import used to add the page's five embedded fonts again.
    assert len(repeated) - len(single) < 4096
    expected = _samples(reference.pages[0])
    assert all(_samples(page) == expected for page in doc.pages)


def test_edits_that_keep_placed_objects_keep_sharing_them() -> None:
    logo = _logo_png()
    doc = _pages(2)
    doc.pages[0].insert_image(LOGO_RECT, stream=logo)

    doc.pages.new((200, 120))
    doc.pages[1].insert_image(LOGO_RECT, stream=logo)
    doc.pages.extend(doc.pages[1])
    doc.pages.select([0, 1, 2, 3, 3])
    del doc.pages[3:4]
    doc.pages.extend(pylopdf.open(build_pdf(["Appendix"])).pages)
    doc.set_outline([(1, "Start", 0)])
    doc.set_metadata({"title": "Shared"})
    doc.pages[2].set_rotation(90)
    doc.pages[2].add_highlight_annot((40, 30, 160, 90))
    doc.tobytes(garbage=True, use_objstms=True)
    for page in doc.pages:
        page.insert_image(LOGO_RECT, stream=logo)

    assert _count(_saved(doc), IMAGE) == 2


def test_placement_after_its_xobject_was_pruned_adds_a_new_one() -> None:
    logo = _logo_png()
    source = _pages(1, 100, 100)
    source.pages[0].insert_image((0, 0, 50, 50), stream=_solid_png(RED), keep_proportion=False)
    reference = _pages(1)
    reference.pages[0].insert_image(LOGO_RECT, stream=logo)
    reference.pages[0].show_pdf_page((100, 0, 200, 100), source, 0)
    doc = _pages(2)
    doc.pages[0].insert_image(LOGO_RECT, stream=logo)
    doc.pages[0].show_pdf_page((100, 0, 200, 100), source, 0)

    # Deleting the only page that placed them prunes the image and the Form.
    del doc.pages[0:1]
    doc.pages[0].insert_image(LOGO_RECT, stream=logo)
    doc.pages[0].show_pdf_page((100, 0, 200, 100), source, 0)

    assert _samples(doc.pages[0]) == _samples(reference.pages[0])
    saved = _saved(doc)
    assert _count(saved, IMAGE) == _count(_saved(reference), IMAGE)
    assert _count(saved, FORM) == 1


def test_shared_placements_survive_save_and_reopen(tmp_path: Path) -> None:
    logo = _logo_png()
    source = pylopdf.open(ASSETS / "bill-hr815.pdf")
    doc = pylopdf.Document()
    for _ in range(3):
        page = doc.pages.new()
        page.insert_image(LOGO_RECT, stream=logo)
        page.show_pdf_page((300, 400, 600, 800), source, 0)
    path = tmp_path / "shared.pdf"
    doc.save(path, garbage=True)
    expected = [_samples(page) for page in doc.pages]

    reopened = pylopdf.open(path)

    assert [_samples(page) for page in reopened.pages] == expected
    saved = _saved(reopened)
    assert _count(saved, IMAGE) == 2
    assert _count(saved, FORM) == 1
    assert _count(saved, FONT_FILE) == 5
    # A reopened document remembers no placement yet: its first adds one image,
    # and later placements share it.
    for page in reopened.pages:
        page.insert_image((10, 10, 40, 40), stream=logo)
    assert _count(_saved(reopened), IMAGE) == 4


def test_same_document_page_placed_on_other_pages_imports_once() -> None:
    doc = _pages(5, 100, 100)
    doc.pages[0].insert_image((0, 0, 50, 50), stream=_solid_png(RED), keep_proportion=False)

    for page in list(doc.pages)[1:]:
        page.show_pdf_page(page.rect, doc, 0)

    saved = _saved(doc)
    assert _count(saved, FORM) == 1
    # Page 0 keeps its image, and the Form owns one imported copy of it.
    assert _count(saved, IMAGE) == 2
    for page in list(doc.pages)[1:]:
        assert _pixel(page, 25, 25) == RED
        assert _pixel(page, 75, 75) == WHITE


def test_page_placed_on_itself_imports_its_current_content_again() -> None:
    doc = _pages(1, 100, 100)
    page = doc.pages[0]
    page.insert_image((0, 0, 50, 50), stream=_solid_png(RED), keep_proportion=False)

    page.show_pdf_page((50, 0, 100, 50), doc, 0, keep_proportion=False)
    page.show_pdf_page((0, 50, 50, 100), doc, 0, keep_proportion=False)

    # The first placement shows the red square at half size in the top right.
    assert _pixel(page, 62, 12) == RED
    # The second shows the page with that placement in the bottom left: the
    # square and, at quarter size, the first placement's copy of it.
    assert _pixel(page, 12, 62) == RED
    assert _pixel(page, 31, 56) == RED
    assert _pixel(page, 43, 68) == WHITE


def test_same_document_source_edited_between_placements_is_imported_again() -> None:
    doc = _pages(3, 100, 100)
    doc.pages[0].insert_image((0, 0, 50, 50), stream=_solid_png(RED), keep_proportion=False)

    doc.pages[1].show_pdf_page(doc.pages[1].rect, doc, 0)
    doc.pages[0].insert_image((50, 50, 100, 100), stream=_solid_png(GREEN), keep_proportion=False)
    doc.pages[2].show_pdf_page(doc.pages[2].rect, doc, 0)

    assert _pixel(doc.pages[1], 25, 25) == _pixel(doc.pages[2], 25, 25) == RED
    assert _pixel(doc.pages[1], 75, 75) == WHITE
    assert _pixel(doc.pages[2], 75, 75) == GREEN


def test_source_document_edited_between_placements_is_imported_again() -> None:
    source = _pages(1, 100, 100)
    source.pages[0].insert_image((0, 0, 50, 50), stream=_solid_png(RED), keep_proportion=False)
    doc = _pages(3, 100, 100)

    doc.pages[0].show_pdf_page(doc.pages[0].rect, source, 0)
    source.pages[0].insert_image((50, 50, 100, 100), stream=_solid_png(GREEN), keep_proportion=False)
    doc.pages[1].show_pdf_page(doc.pages[1].rect, source, 0)
    doc.pages[2].show_pdf_page(doc.pages[2].rect, source, 0)

    assert _pixel(doc.pages[0], 75, 75) == WHITE
    assert _pixel(doc.pages[1], 75, 75) == _pixel(doc.pages[2], 75, 75) == GREEN
    # The edit made the second placement import again, and the third shares it.
    assert _count(_saved(doc), FORM) == 2


def test_image_compression_between_placements_keeps_the_next_placement_full_size() -> None:
    photo = _noise_png(240)
    doc = _pages(2, 200, 200)
    doc.pages[0].insert_image((0, 0, 72, 72), stream=photo)
    assert doc.rewrite_images(dpi_threshold=96, dpi_target=72, quality=50)["rewritten"] == 1

    doc.pages[1].insert_image((0, 0, 72, 72), stream=photo)

    assert drawn_images(doc.pages[0])[0]["width"] == 72
    assert drawn_images(doc.pages[1])[0]["width"] == 240


@pytest.mark.parametrize("edit", ["deflate", "replace_text"])
def test_edits_that_may_change_placed_objects_end_sharing(edit: str) -> None:
    logo = _logo_png()
    doc = pylopdf.open(build_pdf(["Hello", "World"]))
    doc.pages[0].insert_image(LOGO_RECT, stream=logo)

    if edit == "deflate":
        doc.tobytes(deflate=True)
    else:
        assert doc.pages[0].replace_text("Hello", "Howdy") == 1
    doc.pages[1].insert_image(LOGO_RECT, stream=logo)

    # The next placement adds a fresh image and soft mask.
    assert _count(_saved(doc), IMAGE) == 4
    assert _pixel(doc.pages[0], 100, 60) == _pixel(doc.pages[1], 100, 60)
