"""Tests for page image, PDF, text, and replacement drawing.

Placement is verified end to end through rendered pixel colors, exposing any
failure in coordinate transforms, rotation, or XObject registration.
"""

from __future__ import annotations

import itertools
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from conftest import build_pdf, build_raw_pdf, draw_text, draw_textbox, drawn_images, ocr_words

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"
FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
NOTO_SANS_KO = FONT_ROOT / "pylopdf-fonts-ko" / "src" / "pylopdf_fonts_ko" / "NotoSansKR-Regular.otf"
NOTO_SANS_ZH_CN = FONT_ROOT / "pylopdf-fonts-zh-cn" / "src" / "pylopdf_fonts_zh_cn" / "NotoSansSC-Regular.otf"
NOTO_SANS_ZH_TW = FONT_ROOT / "pylopdf-fonts-zh-tw" / "src" / "pylopdf_fonts_zh_tw" / "NotoSansTC-Regular.otf"

RED = (255, 0, 0)
GREEN = (0, 128, 0)
WHITE = (255, 255, 255)


def _solid_png_samples(width: int, height: int, color_type: int, pixel: bytes) -> bytes:
    """Build one solid 8-bit PNG for a caller-selected color type."""
    raw = b"".join(b"\x00" + pixel * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _solid_png(width: int, height: int, rgb: tuple[int, int, int], alpha: int | None = None) -> bytes:
    """Build a solid PNG, using RGBA with alpha and RGB when alpha is None."""
    if alpha is None:
        return _solid_png_samples(width, height, 2, bytes(rgb))
    return _solid_png_samples(width, height, 6, bytes((*rgb, alpha)))


def _png_header(width: int, height: int) -> bytes:
    """Build enough valid IHDR data to exercise pre-decode dimension checks."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    body = b"IHDR" + ihdr
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + body + struct.pack(">I", zlib.crc32(body))


def _pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int]:
    """Return RGB at display point ``(x, y)`` after rendering on white."""
    pix = page.pixmap(background=WHITE, alpha=True)
    offset = y * pix.stride + x * 4
    r, g, b = bytes(pix)[offset : offset + 3]
    return (r, g, b)


def _new_page_doc(width: float = 200, height: float = 100) -> pylopdf.Document:
    doc = pylopdf.Document()
    doc.pages.new((width, height))
    return doc


def _many_contents_doc(count: int) -> pylopdf.Document:
    refs = " ".join(["5 0 R"] * count)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
            4: f"[{refs}]",
            5: "<< /Length 0 >>\nstream\n\nendstream",
        }
    )
    return pylopdf.open(pdf)


def _contents_reference_doc(depth: int, *, cycle: bool = False) -> pylopdf.Document:
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
    }
    if cycle:
        objects[4] = "4 0 R"
    else:
        for offset in range(depth):
            objects[4 + offset] = f"{5 + offset} 0 R"
        objects[4 + depth] = "<< /Length 0 >>\nstream\n\nendstream"
    return pylopdf.open(build_raw_pdf(objects))


def _content_shape_doc(shape: str, *, shared: bool) -> pylopdf.Document:
    contents = {
        "stream": "6 0 R",
        "direct_array": "[6 0 R]",
        "indirect_array": "5 0 R",
    }[shape]
    kids = "[3 0 R 4 0 R]" if shared else "[3 0 R]"
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids {kids} /Count {2 if shared else 1} /MediaBox [0 0 200 100] >>",
        3: f"<< /Type /Page /Parent 2 0 R /Contents {contents} >>",
        4: (f"<< /Type /Page /Parent 2 0 R /Contents {contents} >>" if shared else "null"),
        5: "[6 0 R]" if shape == "indirect_array" else "null",
        6: "<< /Length 29 >>\nstream\nq 1 0 0 rg 0 0 200 100 re f Q\nendstream",
    }
    return pylopdf.open(build_raw_pdf(objects))


def _resource_shape_doc(resources: str, target: str | None = None) -> pylopdf.Document:
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
        3: f"<< /Type /Page /Parent 2 0 R /Resources {resources} >>",
    }
    if target is not None:
        objects[4] = target
    return pylopdf.open(build_raw_pdf(objects))


def _shared_resource_doc() -> pylopdf.Document:
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Resources 5 0 R /Contents 6 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R /Resources 5 0 R /Contents 6 0 R >>",
                5: "<< >>",
                6: "<< /Length 29 >>\nstream\nq 1 0 0 rg 0 0 200 100 re f Q\nendstream",
            }
        )
    )


def _page_parent_chain_doc(depth: int, *, cycle: bool = False) -> pylopdf.Document:
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
        3: "<< /Type /Page /Parent 4 0 R >>",
    }
    if cycle:
        objects[4] = "<< /Type /Pages /Parent 4 0 R >>"
    else:
        for offset in range(depth):
            object_number = 4 + offset
            parent_number = object_number + 1 if offset + 1 < depth else 2
            objects[object_number] = f"<< /Type /Pages /Parent {parent_number} 0 R >>"
    return pylopdf.open(build_raw_pdf(objects))


def _compressed_content_doc(decompressed_size: int) -> pylopdf.Document:
    compressor = zlib.compressobj()
    chunk = b"q" * (1024 * 1024)
    parts = [
        compressor.compress(chunk[: min(len(chunk), decompressed_size - offset)])
        for offset in range(0, decompressed_size, len(chunk))
    ]
    parts.append(compressor.flush())
    compressed = b"".join(parts)
    stream = f"<< /Length {len(compressed)} /Filter /FlateDecode >>\nstream\n".encode() + compressed + b"\nendstream"
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: stream,
            }
        )
    )


def _filtered_content_doc(
    filter_name: str,
    payload: bytes,
    *,
    decode_params: str = "",
) -> pylopdf.Document:
    stream = (
        (f"<< /Length {len(payload)} /Filter /{filter_name} {decode_params}>>\nstream\n").encode()
        + payload
        + b"\nendstream"
    )
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: stream,
            }
        )
    )


def _filter_chain_content_doc(count: int) -> pylopdf.Document:
    filters = " ".join(["/FlateDecode"] * count)
    stream = f"<< /Length 0 /Filter [{filters}] >>\nstream\n\nendstream"
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: stream,
            }
        )
    )


def _split_pixmap() -> pylopdf.Pixmap:
    """Render a wide image with red on the left and green on the right."""
    source = _new_page_doc(20, 10)
    with source.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), _solid_png(2, 2, RED), keep_aspect=False)
    with source.pages[0].draw() as canvas:
        canvas.image((10, 0, 20, 10), _solid_png(2, 2, GREEN), keep_aspect=False)
    return source.pages[0].pixmap(background=WHITE, alpha=True)


def test_drawing_wraps_existing_contents_only_once() -> None:
    doc = _new_page_doc()
    page = doc.pages[0]
    counts = [doc.complexity["object_count"]]
    for y, text in ((20, "First"), (40, "Second"), (60, "Third")):
        with page.draw() as canvas:
            canvas.text(text, (10, y))
        counts.append(doc.complexity["object_count"])

    first, second, third = (after - before for before, after in itertools.pairwise(counts))
    # The empty page had nothing to wrap; the second drawing wraps the first
    # in one q and one Q stream, and the third needs no wrapper.
    assert second == first + 2
    assert third == first


def test_drawing_allows_exact_final_contents_boundary() -> None:
    doc = _many_contents_doc(4093)
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.text("Exact boundary", (10, 20))
    after_first = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="4096-stream page Contents"), page.draw() as canvas:
        canvas.text("One too many", (10, 40))
    assert doc.to_bytes() == after_first


@pytest.mark.parametrize("shape", ["stream", "direct_array", "indirect_array"])
@pytest.mark.parametrize("shared", [False, True], ids=["unshared", "shared"])
@pytest.mark.parametrize("overlay", [False, True], ids=["underlay", "overlay"])
def test_drawing_preserves_content_shape_order_and_shared_pages(
    shape: str,
    *,
    shared: bool,
    overlay: bool,
) -> None:
    doc = _content_shape_doc(shape, shared=shared)
    with doc.pages[0].draw(underlay=not overlay) as canvas:
        canvas.image((0, 0, 200, 100), _solid_png(2, 2, GREEN), keep_aspect=False)

    assert _pixel(doc.pages[0], 100, 50) == (GREEN if overlay else RED)
    if shared:
        assert _pixel(doc.pages[1], 100, 50) == RED


def test_drawing_rejects_raw_contents_array_before_mutation() -> None:
    doc = _many_contents_doc(4097)
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="4096-entry"), doc.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), _solid_png(1, 1, RED))
    assert doc.to_bytes() == before


@pytest.mark.parametrize(
    ("doc", "operation", "message"),
    [
        pytest.param(
            _resource_shape_doc("/Bad"),
            "image",
            "page Resources must resolve to a dictionary",
            id="resources-type",
        ),
        pytest.param(
            _resource_shape_doc("4 0 R", "4 0 R"),
            "image",
            "page Resources contains a reference cycle",
            id="resources-cycle",
        ),
        pytest.param(
            _resource_shape_doc("<< /XObject 4 0 R >>", "4 0 R"),
            "image",
            "Resources/XObject contains a reference cycle",
            id="xobject-cycle",
        ),
        pytest.param(
            _resource_shape_doc("<< /Font 4 0 R >>", "4 0 R"),
            "ocr",
            "Resources/Font contains a reference cycle",
            id="font-cycle",
        ),
    ],
)
def test_drawing_preflights_resource_shape_before_mutation(
    doc: pylopdf.Document,
    operation: str,
    message: str,
) -> None:
    before = doc.to_bytes()

    def run_operation() -> None:
        if operation == "image":
            with doc.pages[0].draw() as canvas:
                canvas.image((0, 0, 10, 10), _solid_png(1, 1, RED))
        else:
            # The OCR text layer is the one drawing that registers a font in
            # the page's resources; the canvas keeps its fonts in its Form.
            doc.pages[0].apply_ocr(words=ocr_words([(10, 10, 50, 20, "Text")]), skip_existing=False)

    with pytest.raises(pylopdf.PdfError, match=message):
        run_operation()
    assert doc.to_bytes() == before


def test_drawing_rejects_full_resource_category_before_mutation() -> None:
    entries = " ".join(f"/R{index} null" for index in range(4096))
    doc = _resource_shape_doc("4 0 R", f"<< /XObject << {entries} >> >>")
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"), doc.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), _solid_png(1, 1, RED))
    assert doc.to_bytes() == before


def test_drawing_detaches_shared_resources_from_other_pages() -> None:
    doc = _shared_resource_doc()
    with doc.pages[0].draw() as canvas:
        canvas.image((0, 0, 200, 100), _solid_png(2, 2, GREEN), keep_aspect=False)
    assert _pixel(doc.pages[0], 100, 50) == GREEN
    assert _pixel(doc.pages[1], 100, 50) == RED

    del doc.pages[0]
    compacted = doc.to_bytes()
    assert b"/PyloX" not in compacted
    assert _pixel(pylopdf.open(compacted).pages[0], 100, 50) == RED


def test_drawing_accepts_exact_page_inheritance_depth_boundary() -> None:
    doc = _page_parent_chain_doc(62)
    with doc.pages[0].draw() as canvas:
        canvas.text("Text", (10, 20))
    assert "Text" in doc.pages[0].text()


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        pytest.param(
            _page_parent_chain_doc(63),
            "64-level safety limit",
            id="depth",
        ),
        pytest.param(
            _page_parent_chain_doc(1, cycle=True),
            "reference cycle",
            id="cycle",
        ),
    ],
)
def test_drawing_rejects_page_inheritance_amplification(
    doc: pylopdf.Document,
    message: str,
) -> None:
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match=message), doc.pages[0].draw() as canvas:
        canvas.text("Text", (10, 20))
    assert doc.to_bytes() == before


def test_placed_page_rejects_source_inheritance_before_target_mutation() -> None:
    target = _resource_shape_doc("<< >>")
    before = target.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="64-level safety limit"), target.pages[0].draw() as canvas:
        canvas.page((0, 0, 100, 100), _page_parent_chain_doc(63).pages[0])
    assert target.to_bytes() == before


def test_placed_page_bounds_decompressed_source_content_before_target_mutation() -> None:
    target = _resource_shape_doc("<< >>")
    before = target.to_bytes()
    source = _compressed_content_doc(64 * 1024 * 1024)

    with (
        pytest.raises(pylopdf.LimitError, match=r"Form source page content.*limit") as caught,
        target.pages[0].draw() as canvas,
    ):
        canvas.page((0, 0, 100, 100), source.pages[0])
    assert caught.value.code == "form_content_size"
    assert target.to_bytes() == before


@pytest.mark.parametrize(
    ("filter_name", "payload", "decode_params"),
    [
        pytest.param("CCITTFaxDecode", b"unsupported", "", id="unsupported"),
        pytest.param(
            "FlateDecode",
            zlib.compress(b"invalid predicted row"),
            "/DecodeParms << /Predictor 12 /Columns 0 >> ",
            id="decoder-error",
        ),
    ],
)
def test_placed_page_rejects_undecodable_source_content_before_target_mutation(
    filter_name: str,
    payload: bytes,
    decode_params: str,
) -> None:
    target = _resource_shape_doc("<< >>")
    before = target.to_bytes()
    source = _filtered_content_doc(filter_name, payload, decode_params=decode_params)

    with (
        pytest.raises(pylopdf.PdfError, match="failed to decode Form source page content"),
        target.pages[0].draw() as canvas,
    ):
        canvas.page((0, 0, 100, 100), source.pages[0])
    assert target.to_bytes() == before


def test_placed_page_decodes_abbreviated_source_content_filter() -> None:
    target = _resource_shape_doc("<< >>")
    operators = b"q 1 0 0 rg 0 0 200 100 re f Q"
    source = _filtered_content_doc("Fl", zlib.compress(operators))

    with target.pages[0].draw() as canvas:
        canvas.page((0, 0, 200, 100), source.pages[0], keep_aspect=False)
    assert _pixel(target.pages[0], 100, 50) == RED


@pytest.mark.parametrize("filter_name", ["ASCIIHexDecode", "AHx", "RunLengthDecode", "RL"])
def test_placed_page_decodes_ascii_hex_and_run_length_source_content(filter_name: str) -> None:
    """Form imports decode ASCIIHex and RunLength layers since lopdf 0.45."""
    target = _resource_shape_doc("<< >>")
    operators = b"q 1 0 0 rg 0 0 200 100 re f Q"
    if filter_name in {"ASCIIHexDecode", "AHx"}:
        payload = operators.hex().encode() + b">"
    else:
        payload = bytes([len(operators) - 1]) + operators + b"\x80"
    source = _filtered_content_doc(filter_name, payload)

    with target.pages[0].draw() as canvas:
        canvas.page((0, 0, 200, 100), source.pages[0], keep_aspect=False)
    assert _pixel(target.pages[0], 100, 50) == RED


def test_placed_page_rejects_excessive_source_filter_chain_before_target_mutation() -> None:
    target = _resource_shape_doc("<< >>")
    before = target.to_bytes()

    with pytest.raises(pylopdf.LimitError) as caught, target.pages[0].draw() as canvas:
        canvas.page((0, 0, 100, 100), _filter_chain_content_doc(17).pages[0])
    assert caught.value.code == "stream_filter_count"
    assert target.to_bytes() == before


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        pytest.param(_contents_reference_doc(40), "32-reference-depth", id="depth"),
        pytest.param(_contents_reference_doc(0, cycle=True), "reference cycle", id="cycle"),
    ],
)
def test_drawing_rejects_contents_reference_amplification(
    doc: pylopdf.Document,
    message: str,
) -> None:
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match=message), doc.pages[0].draw() as canvas:
        canvas.text("Text", (10, 20))
    assert doc.to_bytes() == before


@pytest.mark.parametrize("operation", ["image", "ocr", "pdf", "text", "textbox"])
def test_drawing_preflights_contents_before_mutation(operation: str) -> None:
    doc = _many_contents_doc(4094)
    page = doc.pages[0]
    before = doc.to_bytes()

    def run_operation() -> None:
        if operation == "image":
            with page.draw() as canvas:
                canvas.image((0, 0, 10, 10), _solid_png(1, 1, RED))
        elif operation == "ocr":
            page.apply_ocr(words=ocr_words([(10, 10, 50, 20, "Text")]), skip_existing=False)
        elif operation == "pdf":
            with page.draw() as canvas:
                canvas.page((0, 0, 10, 10), _new_page_doc().pages[0])
        elif operation == "text":
            with page.draw() as canvas:
                canvas.text("Text", (10, 20))
        else:
            with page.draw() as canvas:
                canvas.textbox((10, 10, 100, 50), "Text")

    with pytest.raises(pylopdf.PdfError, match="4096-stream page Contents"):
        run_operation()
    assert doc.to_bytes() == before


def test_insert_png_draws_at_rect() -> None:
    doc = _new_page_doc()
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.image((20, 30, 60, 70), _solid_png(4, 4, RED))
    assert _pixel(page, 40, 50) == RED  # Center of rect.
    assert _pixel(page, 10, 50) == WHITE  # Outside rect.
    assert _pixel(page, 40, 20) == WHITE


def test_insert_png_alpha_is_preserved() -> None:
    doc = _new_page_doc()
    page = doc.pages[0]
    # Fully transparent green leaves white; fully opaque green renders green.
    with page.draw() as canvas:
        canvas.image((10, 10, 50, 50), _solid_png(2, 2, GREEN, alpha=0))
    with page.draw() as canvas:
        canvas.image((60, 10, 100, 50), _solid_png(2, 2, GREEN, alpha=255))
    assert _pixel(page, 30, 30) == WHITE
    assert _pixel(page, 80, 30) == GREEN


def test_insert_grayscale_png_alpha_is_preserved() -> None:
    doc = _new_page_doc()
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.image((10, 10, 50, 50), _solid_png_samples(2, 2, 4, bytes((128, 0))))
    with page.draw() as canvas:
        canvas.image((60, 10, 100, 50), _solid_png_samples(2, 2, 4, bytes((128, 255))))
    assert _pixel(page, 30, 30) == WHITE
    assert _pixel(page, 80, 30) == (128, 128, 128)


def test_insert_pixmap_directly_preserves_alpha() -> None:
    source = _new_page_doc(20, 10)
    with source.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), _solid_png(2, 2, RED), keep_aspect=False)
    pixmap = source.pages[0].pixmap(alpha=True)

    target = _new_page_doc(40, 20)
    with target.pages[0].draw() as canvas:
        canvas.image((0, 0, 40, 20), pixmap, keep_aspect=False)
    assert _pixel(target.pages[0], 10, 10) == RED
    assert _pixel(target.pages[0], 30, 10) == WHITE
    reopened = pylopdf.open(target.to_bytes())
    assert _pixel(reopened.pages[0], 10, 10) == RED
    assert _pixel(reopened.pages[0], 30, 10) == WHITE


def test_insert_opaque_pixmap_omits_soft_mask() -> None:
    source = _new_page_doc(4, 2)
    pixmap = source.pages[0].pixmap(background=GREEN, alpha=True)

    target = _new_page_doc(40, 20)
    with target.pages[0].draw() as canvas:
        canvas.image((0, 0, 40, 20), pixmap)
    assert _pixel(target.pages[0], 20, 10) == GREEN
    assert b"/SMask" not in target.to_bytes()


def test_insert_pixmap_on_rotated_page_uses_display_coordinates() -> None:
    source = _new_page_doc(2, 2)
    pixmap = source.pages[0].pixmap(background=RED, alpha=True)

    target = pylopdf.Document()
    target.pages.new((100, 200))
    target.pages[0].rotation = 90
    with target.pages[0].draw() as canvas:
        canvas.image((150, 25, 190, 75), pixmap)
    assert _pixel(target.pages[0], 170, 50) == RED
    assert _pixel(target.pages[0], 50, 50) == WHITE


@pytest.mark.parametrize("source_kind", ["stream", "pixmap"])
@pytest.mark.parametrize(
    ("rotate", "red_point", "green_point"),
    [
        (0, (30, 50), (70, 50)),
        (90, (50, 30), (50, 70)),
        (180, (70, 50), (30, 50)),
        (-90, (50, 70), (50, 30)),
    ],
)
def test_image_rotates_clockwise(
    source_kind: str,
    rotate: int,
    red_point: tuple[int, int],
    green_point: tuple[int, int],
) -> None:
    pixmap = _split_pixmap()
    target = _new_page_doc(100, 100)
    if source_kind == "stream":
        with target.pages[0].draw() as canvas:
            canvas.image((10, 10, 90, 90), pixmap.to_bytes(), keep_aspect=False, rotate=rotate)
    else:
        with target.pages[0].draw() as canvas:
            canvas.image((10, 10, 90, 90), pixmap, keep_aspect=False, rotate=rotate)
    assert _pixel(target.pages[0], *red_point) == RED
    assert _pixel(target.pages[0], *green_point) == GREEN


def test_image_rotation_preserves_rotated_aspect() -> None:
    target = _new_page_doc(100, 100)
    with target.pages[0].draw() as canvas:
        canvas.image((10, 10, 90, 90), _split_pixmap(), rotate=90)
    assert _pixel(target.pages[0], 50, 30) == RED
    assert _pixel(target.pages[0], 50, 70) == GREEN
    assert _pixel(target.pages[0], 20, 50) == WHITE
    assert _pixel(target.pages[0], 80, 50) == WHITE


def test_image_rotation_composes_with_page_rotation() -> None:
    target = pylopdf.Document()
    target.pages.new((100, 200))
    target.pages[0].rotation = 90
    with target.pages[0].draw() as canvas:
        canvas.image((110, 10, 190, 90), _split_pixmap(), keep_aspect=False, rotate=90)
    assert _pixel(target.pages[0], 150, 30) == RED
    assert _pixel(target.pages[0], 150, 70) == GREEN
    assert _pixel(target.pages[0], 50, 50) == WHITE


def test_image_keep_proportion_centers() -> None:
    doc = _new_page_doc()
    page = doc.pages[0]
    # A square in a 40×20 rect fits as a centered 20×20 image.
    with page.draw() as canvas:
        canvas.image((100, 40, 140, 60), _solid_png(2, 2, RED))
    assert _pixel(page, 120, 50) == RED  # Center.
    assert _pixel(page, 103, 50) == WHITE  # Side margin.
    assert _pixel(page, 137, 50) == WHITE


def test_image_fills_rect_without_keep_proportion() -> None:
    doc = _new_page_doc()
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.image((100, 40, 140, 60), _solid_png(2, 2, RED), keep_aspect=False)
    assert _pixel(page, 103, 50) == RED
    assert _pixel(page, 137, 50) == RED


def test_image_on_rotated_page_uses_display_coordinates() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 200))  # Portrait page.
    page = doc.pages[0]
    page.rotation = 90  # Display is 200×100 landscape.
    assert page.rect.width == 200
    with page.draw() as canvas:
        canvas.image((150, 25, 190, 75), _solid_png(2, 2, RED))
    # Rendering uses display space, so the image appears at the specified point.
    assert _pixel(page, 170, 50) == RED
    assert _pixel(page, 50, 50) == WHITE


def test_insert_jpeg_roundtrips_bytes_exactly() -> None:
    src = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    jpegs = [i for i in drawn_images(src.pages[0]) if i["ext"] == "jpeg"]
    assert jpegs
    original = jpegs[0]["image"]

    doc = _new_page_doc(400, 400)
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.image((0, 0, 400, 400), original)
    extracted = drawn_images(page)
    assert len(extracted) == 1
    assert extracted[0]["ext"] == "jpeg"
    assert extracted[0]["image"] == original  # DCTDecode passthrough round trip.


def test_image_survives_save_roundtrip() -> None:
    doc = _new_page_doc()
    with doc.pages[0].draw() as canvas:
        canvas.image((20, 30, 60, 70), _solid_png(4, 4, RED))
    reopened = pylopdf.open(doc.to_bytes())
    assert _pixel(reopened.pages[0], 40, 50) == RED


def test_image_rejects_bad_input() -> None:
    doc = _new_page_doc()
    page = doc.pages[0]
    before = bytes(page.pixmap(background=WHITE, alpha=True))
    with pytest.raises(TypeError, match="source must be a path, bytes, or a Pixmap"), page.draw() as canvas:
        canvas.image((0, 0, 10, 10), 42)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="rotate must be a multiple of 90"), page.draw() as canvas:
        canvas.image((0, 0, 10, 10), _solid_png(1, 1, RED), rotate=True)
    with pytest.raises(ValueError, match="rotate must be a multiple of 90"), page.draw() as canvas:
        canvas.image((0, 0, 10, 10), _solid_png(1, 1, RED), rotate=45)
    with pytest.raises(ValueError, match="rect"), page.draw() as canvas:
        canvas.image((50, 50, 10, 10), _solid_png(1, 1, RED))
    with pytest.raises(pylopdf.PdfError, match="image format"), page.draw() as canvas:
        canvas.image((0, 0, 10, 10), b"not an image")
    truncated_jpeg = bytes([0xFF, 0xD8, 0xFF, 0xC0, 0, 8, 8, 0, 1, 0, 1])
    with pytest.raises(pylopdf.PdfError, match="JPEG"), page.draw() as canvas:
        canvas.image((0, 0, 10, 10), truncated_jpeg)
    assert bytes(page.pixmap(background=WHITE, alpha=True)) == before
    assert drawn_images(page) == []


@pytest.mark.parametrize("name", ["max_size", "max_pixels"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_image_validates_resource_budgets(name: str, value: object) -> None:
    page = _new_page_doc().pages[0]
    kwargs = {name: value}

    with pytest.raises((TypeError, ValueError), match=name), page.draw() as canvas:
        canvas.image((0, 0, 10, 10), _solid_png(1, 1, RED), **kwargs)  # ty: ignore[invalid-argument-type]


def test_image_bounds_encoded_stream_before_core_copy() -> None:
    png = _solid_png(2, 2, RED)
    rejected = _new_page_doc()
    before = rejected.complexity

    with pytest.raises(pylopdf.LimitError) as caught, rejected.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), png, max_size=len(png) - 1)

    assert caught.value.code == "image_input_size"
    assert rejected.complexity == before
    assert drawn_images(rejected.pages[0]) == []

    boundary = _new_page_doc()
    with boundary.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), png, max_size=len(png))
    assert len(drawn_images(boundary.pages[0])) == 1


def test_image_reads_filename_through_bounded_core_path(tmp_path: Path) -> None:
    png = _solid_png(2, 2, RED)
    path = tmp_path / "image.png"
    path.write_bytes(png)
    doc = _new_page_doc()
    before = doc.complexity

    with pytest.raises(pylopdf.LimitError) as caught, doc.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), path, max_size=len(png) - 1)

    assert caught.value.code == "image_input_size"
    assert doc.complexity == before
    assert drawn_images(doc.pages[0]) == []

    with doc.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), path, max_size=None)
    assert len(drawn_images(doc.pages[0])) == 1


def test_image_bounds_png_pixels_before_decode() -> None:
    oversized = _png_header(8_001, 8_000)
    rejected = _new_page_doc()
    before = rejected.complexity

    with pytest.raises(pylopdf.LimitError) as caught, rejected.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), oversized)

    assert caught.value.code == "image_pixel_count"
    assert rejected.complexity == before
    assert drawn_images(rejected.pages[0]) == []

    png = _solid_png(2, 2, RED)
    with pytest.raises(pylopdf.LimitError) as exact_rejection, rejected.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), png, max_pixels=3)
    assert exact_rejection.value.code == "image_pixel_count"

    boundary = _new_page_doc()
    with boundary.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), png, max_pixels=4)
    assert len(drawn_images(boundary.pages[0])) == 1


def test_image_budgets_do_not_apply_to_pixmap() -> None:
    pixmap = _new_page_doc(2, 2).pages[0].pixmap(alpha=True)
    target = _new_page_doc()

    with target.pages[0].draw() as canvas:
        canvas.image((0, 0, 10, 10), pixmap, max_size=1, max_pixels=1)

    assert len(drawn_images(target.pages[0])) == 1


def test_placed_page_overlays_vector_text() -> None:
    stamp = pylopdf.open(build_pdf(["STAMPTEXT"]))
    doc = pylopdf.Document()
    doc.pages.new()  # A4-sized default page.
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.page((50, 50, 550, 700), stamp.pages[0])
    # Text remains vector/extractable after conversion to a Form XObject.
    assert "STAMPTEXT" in page.text()


def test_placed_page_draws_at_rect() -> None:
    # Stamp source: a red image covering a 100×100 page.
    stamp = _new_page_doc(100, 100)
    with stamp.pages[0].draw() as canvas:
        canvas.image((0, 0, 100, 100), _solid_png(2, 2, RED), keep_aspect=False)

    doc = _new_page_doc(200, 100)
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.page((120, 20, 180, 80), stamp.pages[0])
    assert _pixel(page, 150, 50) == RED  # Center of rect.
    assert _pixel(page, 60, 50) == WHITE  # Outside rect.


def test_placed_page_scales_source_crop_into_rect() -> None:
    # A 50×100 portrait stamp fits centered at width 50 in a 100×100 rect.
    stamp = _new_page_doc(50, 100)
    with stamp.pages[0].draw() as canvas:
        canvas.image((0, 0, 50, 100), _solid_png(2, 2, RED), keep_aspect=False)

    doc = _new_page_doc(200, 120)
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.page((50, 10, 150, 110), stamp.pages[0])
    assert _pixel(page, 100, 60) == RED  # Center band is red.
    assert _pixel(page, 60, 60) == WHITE  # Sides are margins.
    assert _pixel(page, 140, 60) == WHITE


def test_placed_page_imports_another_page_from_same_document() -> None:
    doc = _new_page_doc(100, 100)
    with doc.pages[0].draw() as canvas:
        canvas.image((0, 0, 100, 100), _solid_png(2, 2, RED), keep_aspect=False)
    doc.attachments.add("keep.txt", b"reachable target attachment")
    target = doc.pages.new((200, 100))

    with target.draw() as canvas:
        canvas.page((120, 20, 180, 80), doc.pages[0])

    assert _pixel(target, 150, 50) == RED
    assert _pixel(target, 60, 50) == WHITE
    assert list(doc.attachments) == ["keep.txt"]
    assert doc.attachments["keep.txt"].read() == b"reachable target attachment"


def test_placed_page_imports_target_page_from_pre_edit_snapshot() -> None:
    doc = _new_page_doc(100, 100)
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.image((0, 0, 50, 100), _solid_png(2, 2, RED), keep_aspect=False)

    with page.draw() as canvas:
        canvas.page((50, 0, 100, 100), doc.pages[0], keep_aspect=False)

    assert _pixel(page, 25, 50) == RED
    assert _pixel(page, 62, 50) == RED
    assert _pixel(page, 87, 50) == WHITE


def test_placed_page_does_not_keep_unreachable_source_data() -> None:
    """Do not leak attachments unreachable from the imported Form."""
    secret = b"SECRET-UNREFERENCED-STAMP-ATTACHMENT-91af"
    source = pylopdf.Document()
    source.pages.new((100, 100))
    source.attachments.add("secret.txt", secret)
    target = pylopdf.Document()
    page = target.pages.new((100, 100))

    with page.draw() as canvas:
        canvas.page((0, 0, 100, 100), source.pages[0])

    assert list(target.attachments) == []
    assert secret not in target.to_bytes()


def test_placed_page_accepts_negative_pno() -> None:
    stamp = pylopdf.open(build_pdf(["FIRST", "LAST"]))
    doc = pylopdf.Document()
    doc.pages.new()
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.page((50, 50, 550, 700), stamp.pages[-1])
    assert "LAST" in page.text()
    assert "FIRST" not in page.text()


def test_placed_page_underlay_draws_below() -> None:
    # Opaque green overlay wins over a red underlay.
    red_stamp = _new_page_doc(100, 100)
    with red_stamp.pages[0].draw() as canvas:
        canvas.image((0, 0, 100, 100), _solid_png(2, 2, RED), keep_aspect=False)

    doc = _new_page_doc(100, 100)
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.image((0, 0, 100, 100), _solid_png(2, 2, GREEN), keep_aspect=False)
    with page.draw(underlay=True) as canvas:
        canvas.page((0, 0, 100, 100), red_stamp.pages[0])
    assert _pixel(page, 50, 50) == GREEN


def test_text_is_extractable_at_position() -> None:
    doc = pylopdf.Document()
    doc.pages.new()  # A4-like page also verifies drawing without Resources.
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.text("Confidential", (50, 100), size=12)
    words = page.words()
    assert [w.text for w in words] == ["Confidential"]
    x0, y0, _, y1 = words[0].bbox
    assert abs(x0 - 50) < 2  # Baseline left equals the requested x.
    assert y0 < 100 < y1  # Requested baseline y lies inside the bbox.


def test_text_lines_stack_downward() -> None:
    doc = pylopdf.Document()
    doc.pages.new()
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.text("First\nSecond", (50, 100), size=10)
    words = {w.text: w for w in page.words()}
    assert words["Second"].bbox.y0 > words["First"].bbox.y0  # Second line is lower.


def test_text_on_rotated_page_reads_upright() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 200))
    page = doc.pages[0]
    page.rotation = 90
    with page.draw() as canvas:
        canvas.text("Rotated", (20, 50))
    pix = page.pixmap(background=WHITE, alpha=True)
    assert (pix.width, pix.height) == (200, 100)  # Rendering uses display space.
    # Extraction/search share rendering's rotation-resolved display space.
    words = page.words()
    assert [w.text for w in words] == ["Rotated"]
    assert abs(words[0].bbox.x0 - 20) < 2  # Requested display x.
    assert words[0].bbox.y0 < 50 < words[0].bbox.y1  # Baseline display y lies in bbox.
    assert page.search("Rotated")


def test_text_subset_embeds_unicode_font() -> None:
    doc = _new_page_doc(300, 150)
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.text("日本語テキスト", (20, 60), font=pylopdf.Font.from_file(NOTO_SANS_JP), size=24)

    assert page.text().strip() == "日本語テキスト"
    assert page.search("日本語")
    # The 4.5 MB source is subset to the glyphs used by this one-page Form.
    saved = doc.to_bytes()
    assert len(saved) < 50_000
    reopened = pylopdf.open(saved)
    assert reopened.pages[0].text().strip() == "日本語テキスト"
    assert reopened.pages[0].search("テキスト")


def test_text_auto_embeds_optional_jp_font() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    doc = _new_page_doc(300, 150)
    page = doc.pages[0]

    with page.draw() as canvas:
        canvas.text("日本語", (20, 60), size=24)
        canvas.text("明朝体", (20, 100), font=pylopdf.Font.installed(language="ja", serif=True), size=24)

    assert page.text().splitlines() == ["日本語", "明朝体"]
    spans = [span for block in page.layout().blocks for line in block.lines for span in line.spans]
    assert spans[0].font.startswith("NotoSansJP")
    assert spans[1].font.startswith("NotoSerifJP")
    assert len(doc.to_bytes()) < 100_000


@pytest.mark.parametrize(
    ("font_language", "text", "font_prefix"),
    [
        ("ar", "مرحبا بالعالم", "NotoSansArabic"),
        ("he", "שלום עולם", "NotoSansHebrew"),
        ("hi", "नमस्ते", "NotoSansDevanagari"),
        ("ja", "日本語", "NotoSansJP"),
        ("ko", "한국어", "NotoSansKR"),
        ("th", "ภาษาไทย", "NotoSansThai"),
        ("zh-CN", "简体中文", "NotoSansSC"),
        ("zh-TW", "繁體中文", "NotoSansTC"),
    ],
)
def test_text_optional_font_conformance(
    font_language: pylopdf.FontLanguage,
    text: str,
    font_prefix: str,
) -> None:
    """Generate, subset, save, reopen, extract, and search each bundled script."""
    doc = _new_page_doc(300, 150)
    with doc.pages[0].draw() as canvas:
        canvas.text(text, (20, 60), font=pylopdf.Font.installed(language=font_language), size=24)

    saved = doc.to_bytes()
    assert len(saved) < 100_000
    reopened = pylopdf.open(saved)
    assert reopened.pages[0].text().strip() == text
    assert reopened.pages[0].search(text)
    span = reopened.pages[0].layout().blocks[0].lines[0].spans[0]
    assert span.font.startswith(font_prefix)


@pytest.mark.parametrize(
    ("text", "font_prefix"),
    [
        ("مرحبا", "NotoSansArabic"),
        ("שלום", "NotoSansHebrew"),
        ("नमस्ते", "NotoSansDevanagari"),
        ("日本語", "NotoSansJP"),
        ("한국어", "NotoSansKR"),
        ("ภาษาไทย", "NotoSansThai"),
    ],
)
def test_text_detects_unambiguous_script_provider(text: str, font_prefix: str) -> None:
    doc = _new_page_doc(300, 150)
    with doc.pages[0].draw() as canvas:
        canvas.text(text, (20, 60), size=24)

    span = doc.pages[0].layout().blocks[0].lines[0].spans[0]
    assert span.font.startswith(font_prefix)


def test_text_disambiguates_han_with_an_installed_font() -> None:
    pytest.importorskip("pylopdf_fonts_ko")
    pytest.importorskip("pylopdf_fonts_zh_cn")
    doc = _new_page_doc(300, 180)

    with doc.pages[0].draw() as canvas:
        canvas.text("한국어", (20, 50), size=20)
        canvas.text("简体中文", (20, 100), font=pylopdf.Font.installed(language="zh-CN"), size=20)

    spans = [span for block in doc.pages[0].layout().blocks for line in block.lines for span in line.spans]
    assert spans[0].font.startswith("NotoSansKR")
    assert spans[1].font.startswith("NotoSansSC")


def test_text_refuses_mixed_scripts_without_a_font() -> None:
    page = _new_page_doc().pages[0]
    with pytest.raises(ValueError, match="font_language"):
        pylopdf.Font.installed(language="fr")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="multiple bundled font locales"), page.draw() as canvas:
        canvas.text("かな한국어", (20, 40))
    assert page.text() == ""


def test_text_embedded_font_multiline_and_rotation() -> None:
    doc = pylopdf.Document()
    doc.pages.new((150, 300))
    page = doc.pages[0]
    page.rotation = 90
    font = pylopdf.Font.from_bytes(NOTO_SANS_JP.read_bytes())
    with page.draw() as canvas:
        canvas.text("一行目\n二行目", (30, 55), font=font, size=18, fill=pylopdf.Color(0.8, 0.1, 0.2))

    words = {word.text: word for word in page.words()}
    assert set(words) == {"一行目", "二行目"}
    assert abs(words["一行目"].bbox.x0 - 30) < 2
    assert words["一行目"].bbox.y0 < 55 < words["一行目"].bbox.y1
    assert words["二行目"].bbox.y0 > words["一行目"].bbox.y0
    assert page.pixmap(alpha=True).width == 300


@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("underlay", [False, True])
def test_text_overlay_order(embedded: bool, underlay: bool) -> None:  # noqa: FBT001 - parametrized.
    font = pylopdf.Font.from_bytes(NOTO_SANS_JP.read_bytes()) if embedded else None
    doc = _new_page_doc(160, 80)
    with doc.pages[0].draw() as canvas:
        canvas.image((0, 0, 160, 80), _solid_png(2, 2, GREEN), keep_aspect=False)
    with doc.pages[0].draw(underlay=underlay) as canvas:
        canvas.text("TEXT", (10, 55), font=font, size=48, fill=pylopdf.Color(1.0, 0.0, 0.0))

    rgb = bytes(channel for offset, channel in enumerate(bytes(doc.pages[0].pixmap(alpha=True))) if offset % 4 != 3)
    # Text drawn below the green image is hidden by it.
    assert (set(rgb) == {0, 128}) is underlay


def test_textbox_wraps_aligns_and_reports_spare_height() -> None:
    doc = _new_page_doc(200, 120)
    page = doc.pages[0]
    rect = (10, 10, 110, 90)

    with page.draw() as canvas:
        spare = canvas.textbox(rect, "one two three four five six", align="right", size=10).spare_height

    assert spare > 0
    lines = page.layout().blocks[0].lines
    assert len(lines) == 2
    assert " ".join(span.text for span in lines[0].spans) == "one two three four five"
    assert lines[0].bbox.x1 == pytest.approx(rect[2], abs=0.1)
    assert lines[1].bbox.x1 == pytest.approx(rect[2], abs=0.1)
    assert all(rect[0] <= line.bbox.x0 < line.bbox.x1 <= rect[2] + 0.1 for line in lines)
    assert all(rect[1] <= line.bbox.y0 < line.bbox.y1 <= rect[3] for line in lines)


def test_textbox_justifies_only_soft_wrapped_lines() -> None:
    doc = _new_page_doc(200, 120)
    page = doc.pages[0]
    rect = (10, 10, 110, 90)

    with page.draw() as canvas:
        canvas.textbox(rect, "one two three four five six", align="justify", size=10)

    lines = page.layout().blocks[0].lines
    assert lines[0].bbox.x0 == pytest.approx(rect[0], abs=0.1)
    assert lines[0].bbox.x1 == pytest.approx(rect[2], abs=0.1)
    assert lines[1].bbox.x0 == pytest.approx(rect[0], abs=0.1)
    assert lines[1].bbox.x1 < rect[2] - 20


def test_textbox_overflow_is_non_drawing_and_empty_text_is_free() -> None:
    doc = _new_page_doc(200, 100)
    page = doc.pages[0]
    before = bytes(page.pixmap(background=WHITE, alpha=True))

    with page.draw() as canvas:
        overflow = canvas.textbox((10, 10, 110, 15), "one two three four", size=10)
        empty = canvas.textbox((10, 10, 110, 25), "", size=10)

    assert overflow.spare_height < 0
    assert empty == pylopdf.TextFit(spare_height=15)
    assert page.text() == ""
    assert bytes(page.pixmap(background=WHITE, alpha=True)) == before


def test_textbox_auto_embeds_optional_jp_font_and_survives_rotation() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    doc = pylopdf.Document()
    doc.pages.new((120, 200))
    page = doc.pages[0]
    page.rotation = 90
    rect = (10, 10, 100, 80)

    with page.draw() as canvas:
        spare = canvas.textbox(rect, "日本語の文章を空白なしで折り返します。", align="center", size=12).spare_height

    assert spare > 0
    lines = [line for block in page.layout().blocks for line in block.lines]
    assert len(lines) >= 3
    assert "".join(line.text for line in lines) == "日本語の文章を空白なしで折り返します。"
    assert all(rect[0] <= line.bbox.x0 < line.bbox.x1 <= rect[2] + 0.1 for line in lines)
    assert all(rect[1] <= line.bbox.y0 < line.bbox.y1 <= rect[3] for line in lines)
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].text().replace("\n", "") == "日本語の文章を空白なしで折り返します。"


def test_textbox_tabs_custom_leading_and_bad_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _new_page_doc()
    page = doc.pages[0]
    with page.draw() as canvas:
        first = canvas.textbox((10, 10, 150, 80), "a\tb\nc", size=10, line_height=1.0).spare_height
    assert first > 0
    # Each tab becomes one space.
    assert page.text().splitlines() == ["a b", "c"]

    with pytest.raises(ValueError, match="align"), page.draw() as canvas:
        canvas.textbox((10, 10, 100, 80), "x", align="middle")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="line_height"), page.draw() as canvas:
        canvas.textbox((10, 10, 100, 80), "x", line_height=0)
    with monkeypatch.context() as context:
        context.setattr("pylopdf._fonts._bundled_font_providers", lambda: ())
        with pytest.raises(ValueError, match="optional font extra or pass a Font"), page.draw() as canvas:
            canvas.textbox((10, 10, 100, 80), "日本語")
    with pytest.raises(pylopdf.PdfError, match="does not contain all glyphs"), page.draw() as canvas:
        canvas.textbox((10, 10, 100, 80), "🦀", font=pylopdf.Font.from_file(NOTO_SANS_JP))


@pytest.mark.parametrize("fontfile", [None, NOTO_SANS_JP])
def test_textbox_underlay_stays_below_existing_content(fontfile: Path | None) -> None:
    doc = _new_page_doc(160, 80)
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.image((0, 0, 160, 80), _solid_png(2, 2, GREEN), keep_aspect=False)
    font = None if fontfile is None else pylopdf.Font.from_file(fontfile)

    with page.draw(underlay=True) as canvas:
        canvas.textbox((5, 5, 155, 75), "TEXT", font=font, size=48, fill=pylopdf.Color(1.0, 0.0, 0.0))

    rgb = bytes(channel for offset, channel in enumerate(bytes(page.pixmap(alpha=True))) if offset % 4 != 3)
    assert set(rgb) == {0, 128}


def test_drawn_image_bbox_on_rotated_page_is_display_space() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 200))
    page = doc.pages[0]
    page.rotation = 90
    with page.draw() as canvas:
        canvas.image((150, 25, 190, 75), _solid_png(2, 2, RED), keep_aspect=False)
    bbox = drawn_images(page)[0]["bbox"]
    assert abs(bbox.x0 - 150) < 1
    assert abs(bbox.y0 - 25) < 1
    assert abs(bbox.x1 - 190) < 1
    assert abs(bbox.y1 - 75) < 1


def test_text_survives_save_roundtrip() -> None:
    doc = pylopdf.Document()
    doc.pages.new()
    with doc.pages[0].draw() as canvas:
        canvas.text("Persistent", (72, 72))
    reopened = pylopdf.open(doc.to_bytes())
    assert "Persistent" in reopened.pages[0].text()


def test_standard_14_text_escapes_binary_and_delimiters() -> None:
    doc = pylopdf.Document()
    doc.pages.new()

    with doc.pages[0].draw() as canvas:
        canvas.text("\x01(A)\\€", (10, 20))

    assert b"(\\001\\(A\\)\\\\\\200) Tj" in doc.to_bytes(compress=False)


def test_text_page_numbering_recipe() -> None:
    # Page-number footer recipe.
    doc = pylopdf.Document()
    for _ in range(3):
        doc.pages.new()
    for i, page in enumerate(doc.pages):
        with page.draw() as canvas:
            canvas.text(f"Page {i + 1} / 3", (page.rect.width - 90, page.rect.height - 30), size=9)
    for i in range(3):
        assert f"Page {i + 1} / 3" in doc.pages[i].text()


def test_text_refuses_unicode_without_an_installed_extra_or_a_font(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = pylopdf.Document()
    doc.pages.new()
    with monkeypatch.context() as context:
        context.setattr("pylopdf._fonts._bundled_font_providers", lambda: ())
        with pytest.raises(ValueError, match="optional font extra or pass a Font"), doc.pages[0].draw() as canvas:
            canvas.text("社外秘", (50, 50))
        with pytest.raises(ValueError, match=r"font_language='ko'.*explicit font"), doc.pages[0].draw() as canvas:
            canvas.text("기밀", (50, 50))
    assert doc.pages[0].text() == ""


def test_text_refuses_bad_arguments() -> None:
    doc = pylopdf.Document()
    doc.pages.new()
    page = doc.pages[0]
    with pytest.raises(ValueError, match="PostScript name"):
        pylopdf.Font.standard("nosuch")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="size"), page.draw() as canvas:
        canvas.text("x", (50, 50), size=0)
    with pytest.raises(ValueError, match="from 0 to 1"), page.draw() as canvas:
        canvas.text("x", (50, 50), fill=pylopdf.Color(2.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="index"):
        pylopdf.Font.from_bytes(NOTO_SANS_JP.read_bytes(), index=True)
    with pytest.raises(pylopdf.PdfError, match="font"):
        pylopdf.Font.from_bytes(b"not a font")
    with pytest.raises(pylopdf.PdfError, match="does not contain all glyphs"), page.draw() as canvas:
        canvas.text("🦀", (50, 50), font=pylopdf.Font.from_file(NOTO_SANS_JP))
    assert page.text() == ""


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_font_budget_is_validated(value: object) -> None:
    with pytest.raises((TypeError, ValueError), match="max_size"):
        pylopdf.Font.from_bytes(b"font", max_size=value)  # ty: ignore[invalid-argument-type]


def test_font_bytes_are_bounded_before_the_core_copy() -> None:
    doc = _new_page_doc()
    before = doc.complexity

    with pytest.raises(pylopdf.LimitError) as caught:
        pylopdf.Font.from_bytes(b"too large", max_size=1)

    assert caught.value.code == "font_input_size"
    assert doc.complexity == before


def test_font_files_are_read_through_a_bounded_core_path() -> None:
    font_size = NOTO_SANS_JP.stat().st_size

    with pytest.raises(pylopdf.LimitError) as caught:
        pylopdf.Font.from_file(NOTO_SANS_JP, max_size=font_size - 1)

    assert caught.value.code == "font_input_size"
    doc = _new_page_doc()
    with doc.pages[0].draw() as canvas:
        canvas.text("A", (10, 20), font=pylopdf.Font.from_file(NOTO_SANS_JP, max_size=font_size))
    assert "A" in doc.pages[0].text()


def _draw_generated_text(method: str, page: pylopdf.Page, text: str, **options: Any) -> None:  # noqa: ANN401
    """Draw text with ``Canvas.text`` or ``Canvas.textbox``, as ``method`` names."""
    if method == "text":
        draw_text(page, (10, 20), text, **options)
    else:
        draw_textbox(page, (10, 10, 100, 80), text, **options)


@pytest.mark.parametrize("method", ["text", "textbox"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_generated_text_validates_text_budget(method: str, value: object) -> None:
    page = _new_page_doc().pages[0]

    with pytest.raises((TypeError, ValueError), match="max_text_size"):
        _draw_generated_text(method, page, "x", max_text_size=value)


def test_text_bounds_utf8_input_atomically() -> None:
    doc = _new_page_doc()
    before = doc.complexity

    with pytest.raises(pylopdf.LimitError) as caught, doc.pages[0].draw() as canvas:
        canvas.text("é", (10, 20), max_text_size=1)

    assert caught.value.code == "text_input_size"
    assert doc.complexity == before
    assert doc.pages[0].text() == ""

    with doc.pages[0].draw() as canvas:
        canvas.text("é", (10, 20), max_text_size=2)
        canvas.text("unbounded", (10, 40), max_text_size=None)
    assert doc.pages[0].text().splitlines() == ["é", "unbounded"]


@pytest.mark.parametrize("method", ["text", "textbox"])
def test_generated_text_bounds_physical_lines_atomically(method: str) -> None:
    doc = _new_page_doc()
    before = doc.complexity
    # 4,097 lines, both for text, which splits text with str.splitlines(), and
    # for textboxes, which split it at line breaks.
    text = "\n" * 4096 + "x"

    with pytest.raises(pylopdf.LimitError) as caught:
        _draw_generated_text(method, doc.pages[0], text, max_text_size=len(text))

    assert caught.value.code == "text_line_count"
    assert doc.complexity == before
    assert doc.pages[0].text() == ""


def test_generated_text_line_limit_boundary_and_opt_out_do_not_draw_on_overflow() -> None:
    bounded = _new_page_doc()
    before = bounded.complexity
    assert draw_textbox(bounded.pages[0], (10, 10, 100, 20), "\n" * 4095, max_text_size=4095).spare_height < 0
    assert bounded.complexity == before

    unbounded = _new_page_doc()
    before = unbounded.complexity
    assert draw_textbox(unbounded.pages[0], (10, 10, 100, 20), "\n" * 4096, max_text_size=None).spare_height < 0
    assert unbounded.complexity == before


def test_textbox_bounds_soft_wrapped_lines_atomically() -> None:
    doc = _new_page_doc()
    before = doc.complexity
    text = "a" * 4097

    with pytest.raises(pylopdf.LimitError) as caught, doc.pages[0].draw() as canvas:
        canvas.textbox((10, 10, 16, 80), text, size=10, max_text_size=len(text))

    assert caught.value.code == "text_line_count"
    assert doc.complexity == before
    assert doc.pages[0].text() == ""


def test_textbox_bounds_text_before_expanding_tabs() -> None:
    rejected = _new_page_doc()
    before = rejected.complexity

    with pytest.raises(pylopdf.LimitError) as caught, rejected.pages[0].draw() as canvas:
        canvas.textbox((10, 10, 150, 80), "ab\tcd", max_text_size=4)

    assert caught.value.code == "text_input_size"
    assert rejected.complexity == before

    accepted = _new_page_doc()
    assert draw_textbox(accepted.pages[0], (10, 10, 150, 80), "a\tb", max_text_size=3).fits
    assert accepted.pages[0].text().strip() == "a b"


def test_textbox_wide_single_line_preserves_content() -> None:
    doc = _new_page_doc(1_000_000_000, 100)
    text = "word " * 10_000

    with doc.pages[0].draw() as canvas:
        spare = canvas.textbox((10, 10, 999_999_990, 90), text, max_text_size=len(text)).spare_height

    assert spare > 0
    assert doc.complexity["object_count"] > 4


def test_textbox_dense_break_indexes_preserve_content() -> None:
    doc = _new_page_doc(220, 2_500)
    text = " ".join(["a"] * 2_048)

    with doc.pages[0].draw() as canvas:
        spare = canvas.textbox((10, 10, 210, 2_490), text, size=10, max_text_size=len(text)).spare_height

    assert spare > 0
    assert doc.pages[0].text().split() == ["a"] * 2_048


def test_replace_text_replaces_and_counts() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    page = doc.pages[0]
    assert page.replace_text("PDF", "Cat") == 1
    text = page.text()
    assert "Hello Cat" in text
    assert "PDF" not in text


def test_replace_text_returns_zero_when_absent() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    page = doc.pages[0]
    assert "Hello PDF" in page.text()
    before = doc.to_bytes()

    assert page.replace_text("XYZ", "abc") == 0
    assert doc.to_bytes() == before
    assert "Hello PDF" in page.text()


def test_replace_text_requires_needle() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    with pytest.raises(ValueError, match="search"):
        doc.pages[0].replace_text("", "abc")


def test_replace_text_detaches_shared_page_contents() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    doc.pages.extend(doc.pages[0])

    assert doc.pages[0].replace_text("PDF", "Cat") == 1
    assert "Hello Cat" in doc.pages[0].text()
    assert "Hello PDF" in doc.pages[1].text()


def test_replace_text_validates_input_without_mutation() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    page = doc.pages[0]
    before = doc.to_bytes()

    with pytest.raises(ValueError, match="exactly one character"):
        page.replace_text("PDF", "Cat", default_char="??")
    with pytest.raises(pylopdf.LimitError) as caught:
        page.replace_text("PDF", "x" * 4094)
    assert caught.value.code == "replacement_input_size"
    assert doc.to_bytes() == before


def test_replace_text_rejects_without_encoding_complete_input() -> None:
    class EncodeMustNotRun(str):
        __slots__ = ()

        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
            del encoding, errors
            msg = "replace_text must count UTF-8 without encoding the complete input"
            raise AssertionError(msg)

    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    before = doc.to_bytes()
    replacement = EncodeMustNotRun("x" * 4097)

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.pages[0].replace_text("PDF", replacement)
    assert caught.value.code == "replacement_input_size"
    assert doc.to_bytes() == before


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("search", 1),
        ("replacement", 1),
        ("default_char", 1),
    ],
)
def test_replace_text_rejects_non_string_inputs(argument: str, value: object) -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    kwargs: dict[str, object] = {"search": "PDF", "replacement": "Cat", argument: value}
    with pytest.raises(TypeError, match=argument):
        doc.pages[0].replace_text(**kwargs)  # ty: ignore[invalid-argument-type]


def test_replace_text_allows_exact_input_boundary_and_linear_fallback() -> None:
    boundary = pylopdf.open(build_pdf(["Hello PDF"]))
    assert boundary.pages[0].replace_text("PDF", "x" * 4093) == 1

    fallback = pylopdf.open(build_pdf(["Hello PDF"]))
    assert fallback.pages[0].replace_text("PDF", "🦀", default_char="!") == 1
    assert "Hello !" in fallback.pages[0].text()


@pytest.mark.parametrize("max_size", [0, -1, True, 1.5])
def test_replace_text_validates_output_budget(max_size: object) -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    with pytest.raises((TypeError, ValueError), match="max_size"):
        doc.pages[0].replace_text("PDF", "Cat", max_size=max_size)  # ty: ignore[invalid-argument-type]


def test_replace_text_bounds_content_and_preserves_document() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    page = doc.pages[0]
    before = doc.to_bytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        page.replace_text("PDF", "Cat", max_size=16)
    assert caught.value.code == "replacement_output_size"
    assert doc.to_bytes() == before


def test_replace_text_bounds_replacement_growth_before_mutation() -> None:
    doc = pylopdf.open(build_pdf(["A" * 100]))
    before = doc.to_bytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.pages[0].replace_text("A", "BBBBBBBB", max_size=256)
    assert caught.value.code == "replacement_output_size"
    assert doc.to_bytes() == before


def test_replace_text_rejects_oversized_contents_shape_before_mutation() -> None:
    doc = _many_contents_doc(4097)
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="4096-entry"):
        doc.pages[0].replace_text("x", "y")
    assert doc.to_bytes() == before


def test_replace_text_rejects_excessive_filter_chain_before_mutation() -> None:
    doc = _filter_chain_content_doc(17)
    before = doc.to_bytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.pages[0].replace_text("x", "y")
    assert caught.value.code == "stream_filter_count"
    assert doc.to_bytes() == before


def test_replace_text_error_is_atomic() -> None:
    stream = b"Tf (Hello) Tj"
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
            }
        )
    )
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="text replacement failed"):
        doc.pages[0].replace_text("Hello", "Goodbye")
    assert doc.to_bytes() == before
