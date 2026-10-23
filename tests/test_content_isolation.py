"""Drawing on pages whose content leaves the graphics-state stack unbalanced.

hayro joins a page's content streams and interprets them as one stream. One
`q` before and one `Q` after the existing content isolate it only when its own
`q` and `Q` balance: a `Q` of the wrapper then closes a `q` that the content
left open instead of the wrapper's own, and a `Q` of the content that finds no
`q` closes the wrapper's. Drawing therefore wraps the content once in as many
`q` and `Q` as hayro's content parser finds it to need, so appended content
starts from the page's initial graphics state. Each page below doubles the
user space where one wrapper `q`/`Q` pair would leave it in force for the
appended content, which would then be drawn twice as far from the origin.
"""

from __future__ import annotations

import struct
import zlib

import pytest
from conftest import build_raw_pdf, drawn_images

import pylopdf

ROTATIONS = [0, 90, 180, 270]

# A blue square drawn through the doubled user space.
SQUARE = b"0 0 1 rg 0 0 5 5 re f"
# Doubles the user space and then leaves one `q` open, as pdf.js
# issue13971.pdf does.
OPEN_SCALE = b"2 0 0 2 0 0 cm q " + SQUARE
# Leaves two `q` open, the outer one doubling the user space, as pdf.js
# bug1795263.pdf does.
NESTED_SCALE = b"q 2 0 0 2 0 0 cm q " + SQUARE
# Restores two states that it never saved, then doubles the user space.
EXCESS_SCALE = b"Q Q 2 0 0 2 0 0 cm " + SQUARE
CONTENTS = [
    pytest.param((OPEN_SCALE,), id="open"),
    pytest.param((NESTED_SCALE,), id="nested"),
    pytest.param((EXCESS_SCALE,), id="excess"),
    pytest.param((b"2 0 0 2 0 0 cm", b"q " + SQUARE), id="split-across-streams"),
]

RED = (1.0, 0.0, 0.0)


def _page(*contents: bytes, rotation: int = 0) -> pylopdf.Document:
    """Open one 200x100 page whose `/Contents` array holds each stream in turn."""
    first = 4
    references = " ".join(f"{first + index} 0 R" for index in range(len(contents)))
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Rotate {rotation} /Contents [{references}] >>",
    }
    for index, data in enumerate(contents):
        objects[first + index] = f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"
    return pylopdf.open(build_raw_pdf(objects))


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Build a solid 8-bit RGB PNG."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _word(page: pylopdf.Page, text: str) -> tuple[float, float, float, float]:
    (word,) = [word for word in page.words() if word.text == text]
    return tuple(word.bbox)


def _assert_text_at(page: pylopdf.Page, text: str, point: tuple[float, float]) -> None:
    x0, y0, _, y1 = _word(page, text)
    assert x0 == pytest.approx(point[0], abs=0.5)
    assert y0 < point[1] < y1


def _inserted_image_bbox(page: pylopdf.Page) -> tuple[float, ...]:
    """Return where the 2x2 test image was drawn, among any images of the page."""
    (image,) = [image for image in drawn_images(page) if (image["width"], image["height"]) == (2, 2)]
    return tuple(image["bbox"])


def _rgb(paint: pylopdf.Paint | None) -> tuple[float, float, float] | None:
    return paint.rgb if isinstance(paint, pylopdf.Color) else None


def _red_rects(page: pylopdf.Page) -> list[tuple[float, ...]]:
    return [
        tuple(path.bbox)
        for path in page.content().paths
        if RED in (_rgb(path.fill), _rgb(path.stroke.paint if path.stroke else None))
    ]


def _square(page: pylopdf.Page) -> tuple[float, ...]:
    (path,) = [path for path in page.content().paths if _rgb(path.fill) == (0.0, 0.0, 1.0)]
    return tuple(path.bbox)


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("contents", CONTENTS)
def test_insert_text_starts_from_the_initial_graphics_state(contents: tuple[bytes, ...], rotation: int) -> None:
    page = _page(*contents, rotation=rotation).pages[0]
    square = _square(page)

    with page.draw() as page_canvas:
        page_canvas.text("Probe", (20, 40), size=10)

    _assert_text_at(page, "Probe", (20, 40))
    assert _square(page) == pytest.approx(square)


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("contents", CONTENTS)
def test_textbox_starts_from_the_initial_graphics_state(contents: tuple[bytes, ...], rotation: int) -> None:
    page = _page(*contents, rotation=rotation).pages[0]

    with page.draw() as canvas:
        assert canvas.textbox((20, 30, 90, 60), "Boxed", size=10).fits

    x0, y0, _, _ = _word(page, "Boxed")
    assert x0 == pytest.approx(20, abs=0.5)
    assert 30 <= y0 < 45


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("overlay", [True, False], ids=["overlay", "underlay"])
@pytest.mark.parametrize("contents", CONTENTS)
def test_insert_image_starts_from_the_initial_graphics_state(
    contents: tuple[bytes, ...],
    rotation: int,
    *,
    overlay: bool,
) -> None:
    page = _page(*contents, rotation=rotation).pages[0]

    page.insert_image((20, 30, 60, 70), stream=_png(2, 2, (255, 0, 0)), keep_proportion=False, overlay=overlay)

    assert _inserted_image_bbox(page) == pytest.approx((20, 30, 60, 70), abs=0.5)


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("contents", CONTENTS)
def test_canvas_starts_from_the_initial_graphics_state(contents: tuple[bytes, ...], rotation: int) -> None:
    page = _page(*contents, rotation=rotation).pages[0]

    with page.draw() as canvas:
        canvas.rect((20, 30, 60, 70), stroke=pylopdf.Color(*RED))
        canvas.text("Drawn", (25, 50), size=8)

    assert _red_rects(page) == [pytest.approx((20, 30, 60, 70), abs=0.01)]
    _assert_text_at(page, "Drawn", (25, 50))


@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("contents", CONTENTS)
def test_show_pdf_page_starts_from_the_initial_graphics_state(contents: tuple[bytes, ...], rotation: int) -> None:
    source = pylopdf.Document()
    source.pages.new((100, 100))
    with source.pages[0].draw() as canvas:
        canvas.rect((0, 0, 100, 100), fill=pylopdf.Color(*RED))
    page = _page(*contents, rotation=rotation).pages[0]

    page.show_pdf_page((20, 30, 60, 70), source, 0)

    assert _red_rects(page) == [pytest.approx((20, 30, 60, 70), abs=0.01)]


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param((b"q 2 0 0 2 0 0 cm q BI /W 4 /H 1 /BPC 8 /CS /G ID Q Q EI",), id="inline-image-data"),
        pytest.param((b"q 2 0 0 2 0 0 cm q /Span << /ActualText (Q Q) >> BDC EMC",), id="string"),
        pytest.param((b"q 2 0 0 2 0 0 cm q % Q Q\n",), id="comment"),
        pytest.param((b"q 2 0 0 2 0 0 cm q % no line end",), id="unterminated-comment"),
        pytest.param((b"q\n", OPEN_SCALE, b"Q\n"), id="outer-wrapper-leaves-q-open"),
        pytest.param((b"q\n", EXCESS_SCALE, b"Q\n"), id="outer-wrapper-restored-early"),
    ],
)
def test_drawing_counts_graphics_states_as_hayro_parses_them(contents: tuple[bytes, ...]) -> None:
    """Only operators save and restore states; data, strings, and comments do not.

    The first four pages leave two `q` open, the outer one doubling the user
    space, so a count that took the `Q` bytes in image data, a string, or a
    comment for operators, or a closing stream that the unterminated comment
    swallowed, would leave the doubled user space in force. Standalone `q` and
    `Q` streams around the content do not isolate content that leaves a `q`
    open or restores more states than it saves.
    """
    doc = _page(*contents)
    page = doc.pages[0]

    with page.draw() as page_canvas:
        page_canvas.text("Probe", (20, 40), size=10)
    page.insert_image((120, 30, 160, 70), stream=_png(2, 2, (255, 0, 0)), keep_proportion=False)

    _assert_text_at(page, "Probe", (20, 40))
    assert _inserted_image_bbox(page) == pytest.approx((120, 30, 160, 70), abs=0.5)
    reopened = pylopdf.open(doc.to_bytes())
    _assert_text_at(reopened.pages[0], "Probe", (20, 40))


def test_a_self_contained_outer_wrapper_needs_no_second_wrapper() -> None:
    wrapped = _page(b"q\n", b"2 0 0 2 0 0 cm " + SQUARE, b"Q\n")
    plain = _page(b"2 0 0 2 0 0 cm " + SQUARE)
    added = []
    for doc in (wrapped, plain):
        before = doc.complexity["object_count"]
        with doc.pages[0].draw() as page_canvas:
            page_canvas.text("Probe", (20, 40), size=10)
        _assert_text_at(doc.pages[0], "Probe", (20, 40))
        added.append(doc.complexity["object_count"] - before)

    # The canvas's Form with its resources and font, and the stream that draws
    # it; only the plain page also gets an opening and a closing stream.
    assert added == [4, 6]


def test_drawing_again_after_saving_keeps_the_initial_graphics_state() -> None:
    doc = _page(OPEN_SCALE)
    with doc.pages[0].draw() as page_canvas:
        page_canvas.text("First", (20, 40), size=10)

    reopened = pylopdf.open(doc.to_bytes())
    with reopened.pages[0].draw() as page_canvas:
        page_canvas.text("Second", (20, 70), size=10)

    _assert_text_at(reopened.pages[0], "First", (20, 40))
    _assert_text_at(reopened.pages[0], "Second", (20, 70))


def test_drawing_on_content_that_does_not_decode_still_succeeds() -> None:
    """A stream that neither lopdf nor hayro can decode counts as empty."""
    undecodable = b"<< /Length 4 /Filter /JBIG2Decode >>\nstream\n\x00\x01\x02\x03\nendstream"
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Contents [4 0 R 5 0 R] >>",
                4: f"<< /Length {len(OPEN_SCALE)} >>\nstream\n".encode() + OPEN_SCALE + b"\nendstream",
                5: undecodable,
            }
        )
    )

    with doc.pages[0].draw() as page_canvas:
        page_canvas.text("Probe", (20, 40), size=10)

    _assert_text_at(doc.pages[0], "Probe", (20, 40))
