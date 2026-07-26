"""Tests for page image, PDF, text, and replacement drawing.

Placement is verified end to end through rendered pixel colors, exposing any
failure in coordinate transforms, rotation, or XObject registration.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"
NOTO_SANS_JP = (
    Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-cjk" / "src" / "pylopdf_fonts_cjk" / "NotoSansJP-Regular.otf"
)

RED = (255, 0, 0)
GREEN = (0, 128, 0)
WHITE = (255, 255, 255)


def _solid_png(width: int, height: int, rgb: tuple[int, int, int], alpha: int | None = None) -> bytes:
    """Build a solid PNG, using RGBA with alpha and RGB when alpha is None."""
    if alpha is None:
        color_type, px = 2, bytes(rgb)
    else:
        color_type, px = 6, bytes((*rgb, alpha))
    raw = b"".join(b"\x00" + px * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int]:
    """Return RGB at display point ``(x, y)`` after rendering on white."""
    pix = page.get_pixmap(background=WHITE)
    offset = y * pix.stride + x * 4
    r, g, b = pix.samples[offset : offset + 3]
    return (r, g, b)


def _new_page_doc(width: float = 200, height: float = 100) -> pylopdf.Document:
    doc = pylopdf.Document()
    doc.new_page(width=width, height=height)
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
    return pylopdf.open(stream=pdf)


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
    return pylopdf.open(stream=build_raw_pdf(objects))


def _split_pixmap() -> pylopdf.Pixmap:
    """Render a wide image with red on the left and green on the right."""
    source = _new_page_doc(20, 10)
    source[0].insert_image((0, 0, 10, 10), stream=_solid_png(2, 2, RED), keep_proportion=False)
    source[0].insert_image((10, 0, 20, 10), stream=_solid_png(2, 2, GREEN), keep_proportion=False)
    return source[0].get_pixmap(background=WHITE)


def test_drawing_wraps_existing_contents_only_once() -> None:
    doc = _new_page_doc()
    page = doc[0]
    page.insert_text((10, 20), "First")
    page.insert_text((10, 40), "Second")
    after_second = doc.complexity["object_count"]

    page.insert_text((10, 60), "Third")

    assert doc.complexity["object_count"] - after_second == 2


def test_drawing_allows_exact_final_contents_boundary() -> None:
    doc = _many_contents_doc(4093)
    page = doc[0]
    page.insert_text((10, 20), "Exact boundary")
    after_first = doc.tobytes()

    with pytest.raises(pylopdf.PdfError, match="4096-stream page Contents"):
        page.insert_text((10, 40), "One too many")
    assert doc.tobytes() == after_first


def test_drawing_rejects_raw_contents_array_before_input_decode() -> None:
    doc = _many_contents_doc(4097)
    before = doc.tobytes()

    with pytest.raises(pylopdf.PdfError, match="4096-entry"):
        doc[0].insert_image((0, 0, 10, 10), stream=b"not an image")
    assert doc.tobytes() == before


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
    before = doc.tobytes()

    with pytest.raises(pylopdf.PdfError, match=message):
        doc[0].insert_text((10, 20), "Text")
    assert doc.tobytes() == before


@pytest.mark.parametrize("operation", ["image", "ocr", "pdf", "text", "textbox"])
def test_drawing_preflights_contents_before_mutation(operation: str) -> None:
    doc = _many_contents_doc(4094)
    page = doc[0]
    before = doc.tobytes()

    def run_operation() -> None:
        if operation == "image":
            page.insert_image((0, 0, 10, 10), stream=b"not an image")
        elif operation == "ocr":
            page.insert_ocr_text_layer([(10, 10, 50, 20, "Text")])
        elif operation == "pdf":
            page.show_pdf_page((0, 0, 10, 10), _new_page_doc())
        elif operation == "text":
            page.insert_text((10, 20), "Text")
        else:
            page.insert_textbox((10, 10, 100, 50), "Text")

    with pytest.raises(pylopdf.PdfError, match="4096-stream page Contents"):
        run_operation()
    assert doc.tobytes() == before


def test_insert_png_draws_at_rect() -> None:
    doc = _new_page_doc()
    page = doc[0]
    page.insert_image((20, 30, 60, 70), stream=_solid_png(4, 4, RED))
    assert _pixel(page, 40, 50) == RED  # Center of rect.
    assert _pixel(page, 10, 50) == WHITE  # Outside rect.
    assert _pixel(page, 40, 20) == WHITE


def test_insert_png_alpha_is_preserved() -> None:
    doc = _new_page_doc()
    page = doc[0]
    # Fully transparent green leaves white; fully opaque green renders green.
    page.insert_image((10, 10, 50, 50), stream=_solid_png(2, 2, GREEN, alpha=0))
    page.insert_image((60, 10, 100, 50), stream=_solid_png(2, 2, GREEN, alpha=255))
    assert _pixel(page, 30, 30) == WHITE
    assert _pixel(page, 80, 30) == GREEN


def test_insert_pixmap_directly_preserves_alpha() -> None:
    source = _new_page_doc(20, 10)
    source[0].insert_image((0, 0, 10, 10), stream=_solid_png(2, 2, RED), keep_proportion=False)
    pixmap = source[0].get_pixmap()

    target = _new_page_doc(40, 20)
    target[0].insert_image((0, 0, 40, 20), pixmap=pixmap, keep_proportion=False)
    assert _pixel(target[0], 10, 10) == RED
    assert _pixel(target[0], 30, 10) == WHITE
    reopened = pylopdf.open(stream=target.tobytes())
    assert _pixel(reopened[0], 10, 10) == RED
    assert _pixel(reopened[0], 30, 10) == WHITE


def test_insert_opaque_pixmap_omits_soft_mask() -> None:
    source = _new_page_doc(4, 2)
    pixmap = source[0].get_pixmap(background=GREEN)

    target = _new_page_doc(40, 20)
    target[0].insert_image((0, 0, 40, 20), pixmap=pixmap)
    assert _pixel(target[0], 20, 10) == GREEN
    assert b"/SMask" not in target.tobytes()


def test_insert_pixmap_on_rotated_page_uses_display_coordinates() -> None:
    source = _new_page_doc(2, 2)
    pixmap = source[0].get_pixmap(background=RED)

    target = pylopdf.Document()
    target.new_page(width=100, height=200)
    target[0].set_rotation(90)
    target[0].insert_image((150, 25, 190, 75), pixmap=pixmap)
    assert _pixel(target[0], 170, 50) == RED
    assert _pixel(target[0], 50, 50) == WHITE


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
def test_insert_image_rotates_clockwise(
    source_kind: str,
    rotate: int,
    red_point: tuple[int, int],
    green_point: tuple[int, int],
) -> None:
    pixmap = _split_pixmap()
    target = _new_page_doc(100, 100)
    if source_kind == "stream":
        target[0].insert_image(
            (10, 10, 90, 90),
            stream=pixmap.tobytes(),
            rotate=rotate,
            keep_proportion=False,
        )
    else:
        target[0].insert_image(
            (10, 10, 90, 90),
            pixmap=pixmap,
            rotate=rotate,
            keep_proportion=False,
        )
    assert _pixel(target[0], *red_point) == RED
    assert _pixel(target[0], *green_point) == GREEN


def test_insert_image_rotation_preserves_rotated_aspect() -> None:
    target = _new_page_doc(100, 100)
    target[0].insert_image((10, 10, 90, 90), pixmap=_split_pixmap(), rotate=90)
    assert _pixel(target[0], 50, 30) == RED
    assert _pixel(target[0], 50, 70) == GREEN
    assert _pixel(target[0], 20, 50) == WHITE
    assert _pixel(target[0], 80, 50) == WHITE


def test_insert_image_rotation_composes_with_page_rotation() -> None:
    target = pylopdf.Document()
    target.new_page(width=100, height=200)
    target[0].set_rotation(90)
    target[0].insert_image(
        (110, 10, 190, 90),
        pixmap=_split_pixmap(),
        rotate=90,
        keep_proportion=False,
    )
    assert _pixel(target[0], 150, 30) == RED
    assert _pixel(target[0], 150, 70) == GREEN
    assert _pixel(target[0], 50, 50) == WHITE


def test_insert_image_keep_proportion_centers() -> None:
    doc = _new_page_doc()
    page = doc[0]
    # A square in a 40×20 rect fits as a centered 20×20 image.
    page.insert_image((100, 40, 140, 60), stream=_solid_png(2, 2, RED))
    assert _pixel(page, 120, 50) == RED  # Center.
    assert _pixel(page, 103, 50) == WHITE  # Side margin.
    assert _pixel(page, 137, 50) == WHITE


def test_insert_image_fills_rect_without_keep_proportion() -> None:
    doc = _new_page_doc()
    page = doc[0]
    page.insert_image((100, 40, 140, 60), stream=_solid_png(2, 2, RED), keep_proportion=False)
    assert _pixel(page, 103, 50) == RED
    assert _pixel(page, 137, 50) == RED


def test_insert_image_on_rotated_page_uses_display_coordinates() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)  # Portrait page.
    page = doc[0]
    page.set_rotation(90)  # Display is 200×100 landscape.
    assert page.rect.width == 200
    page.insert_image((150, 25, 190, 75), stream=_solid_png(2, 2, RED))
    # Rendering uses display space, so the image appears at the specified point.
    assert _pixel(page, 170, 50) == RED
    assert _pixel(page, 50, 50) == WHITE


def test_insert_jpeg_roundtrips_bytes_exactly() -> None:
    src = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    jpegs = [i for i in src[0].get_images() if i["ext"] == "jpeg"]
    assert jpegs
    original = jpegs[0]["image"]

    doc = _new_page_doc(400, 400)
    page = doc[0]
    page.insert_image((0, 0, 400, 400), stream=original)
    extracted = page.get_images()
    assert len(extracted) == 1
    assert extracted[0]["ext"] == "jpeg"
    assert extracted[0]["image"] == original  # DCTDecode passthrough round trip.


def test_insert_image_survives_save_roundtrip() -> None:
    doc = _new_page_doc()
    doc[0].insert_image((20, 30, 60, 70), stream=_solid_png(4, 4, RED))
    reopened = pylopdf.open(stream=doc.tobytes())
    assert _pixel(reopened[0], 40, 50) == RED


def test_insert_image_rejects_bad_input() -> None:
    doc = _new_page_doc()
    page = doc[0]
    with pytest.raises(ValueError, match="exactly one"):
        page.insert_image((0, 0, 10, 10))
    with pytest.raises(ValueError, match="exactly one"):
        page.insert_image(
            (0, 0, 10, 10),
            stream=_solid_png(1, 1, RED),
            pixmap=page.get_pixmap(),
        )
    with pytest.raises(TypeError, match="pixmap must be a Pixmap"):
        page.insert_image((0, 0, 10, 10), pixmap=b"not a pixmap")  # type: ignore[arg-type]
    before = page.get_pixmap(background=WHITE).samples
    with pytest.raises(TypeError, match="rotate must be a multiple of 90"):
        page.insert_image((0, 0, 10, 10), stream=_solid_png(1, 1, RED), rotate=True)
    with pytest.raises(ValueError, match="rotate must be a multiple of 90"):
        page.insert_image((0, 0, 10, 10), stream=_solid_png(1, 1, RED), rotate=45)
    assert page.get_pixmap(background=WHITE).samples == before
    assert page.get_images() == []
    with pytest.raises(ValueError, match="rect"):
        page.insert_image((50, 50, 10, 10), stream=_solid_png(1, 1, RED))
    with pytest.raises(pylopdf.PdfError, match="image format"):
        page.insert_image((0, 0, 10, 10), stream=b"not an image")
    truncated_jpeg = bytes([0xFF, 0xD8, 0xFF, 0xC0, 0, 8, 8, 0, 1, 0, 1])
    with pytest.raises(pylopdf.PdfError, match="JPEG"):
        page.insert_image((0, 0, 10, 10), stream=truncated_jpeg)


def test_show_pdf_page_overlays_vector_text() -> None:
    stamp = pylopdf.open(stream=build_pdf(["STAMPTEXT"]))
    doc = pylopdf.Document()
    doc.new_page()  # A4-sized default page.
    page = doc[0]
    page.show_pdf_page((50, 50, 550, 700), stamp)
    # Text remains vector/extractable after conversion to a Form XObject.
    assert "STAMPTEXT" in page.get_text()


def test_show_pdf_page_draws_at_rect() -> None:
    # Stamp source: a red image covering a 100×100 page.
    stamp = _new_page_doc(100, 100)
    stamp[0].insert_image((0, 0, 100, 100), stream=_solid_png(2, 2, RED), keep_proportion=False)

    doc = _new_page_doc(200, 100)
    page = doc[0]
    page.show_pdf_page((120, 20, 180, 80), stamp)
    assert _pixel(page, 150, 50) == RED  # Center of rect.
    assert _pixel(page, 60, 50) == WHITE  # Outside rect.


def test_show_pdf_page_scales_source_crop_into_rect() -> None:
    # A 50×100 portrait stamp fits centered at width 50 in a 100×100 rect.
    stamp = _new_page_doc(50, 100)
    stamp[0].insert_image((0, 0, 50, 100), stream=_solid_png(2, 2, RED), keep_proportion=False)

    doc = _new_page_doc(200, 120)
    page = doc[0]
    page.show_pdf_page((50, 10, 150, 110), stamp)
    assert _pixel(page, 100, 60) == RED  # Center band is red.
    assert _pixel(page, 60, 60) == WHITE  # Sides are margins.
    assert _pixel(page, 140, 60) == WHITE


def test_show_pdf_page_imports_another_page_from_same_document() -> None:
    doc = _new_page_doc(100, 100)
    doc[0].insert_image((0, 0, 100, 100), stream=_solid_png(2, 2, RED), keep_proportion=False)
    doc.embfile_add("keep.txt", b"reachable target attachment")
    target = doc.new_page(width=200, height=100)

    target.show_pdf_page((120, 20, 180, 80), doc, pno=0)

    assert _pixel(target, 150, 50) == RED
    assert _pixel(target, 60, 50) == WHITE
    assert doc.embfile_names() == ["keep.txt"]
    assert doc.embfile_get("keep.txt") == b"reachable target attachment"


def test_show_pdf_page_imports_target_page_from_pre_edit_snapshot() -> None:
    doc = _new_page_doc(100, 100)
    page = doc[0]
    page.insert_image((0, 0, 50, 100), stream=_solid_png(2, 2, RED), keep_proportion=False)

    page.show_pdf_page((50, 0, 100, 100), doc, pno=0, keep_proportion=False)

    assert _pixel(page, 25, 50) == RED
    assert _pixel(page, 62, 50) == RED
    assert _pixel(page, 87, 50) == WHITE


def test_show_pdf_page_does_not_keep_unreachable_source_data() -> None:
    """Do not leak attachments unreachable from the imported Form."""
    secret = b"SECRET-UNREFERENCED-STAMP-ATTACHMENT-91af"
    source = pylopdf.Document()
    source.new_page(width=100, height=100)
    source.embfile_add("secret.txt", secret)
    target = pylopdf.Document()
    page = target.new_page(width=100, height=100)

    page.show_pdf_page((0, 0, 100, 100), source)

    assert target.embfile_names() == []
    assert secret not in target.tobytes()


def test_show_pdf_page_accepts_negative_pno() -> None:
    stamp = pylopdf.open(stream=build_pdf(["FIRST", "LAST"]))
    doc = pylopdf.Document()
    doc.new_page()
    page = doc[0]
    page.show_pdf_page((50, 50, 550, 700), stamp, pno=-1)
    assert "LAST" in page.get_text()
    assert "FIRST" not in page.get_text()


def test_show_pdf_page_underlay_draws_below() -> None:
    # Opaque green overlay wins over a red underlay.
    red_stamp = _new_page_doc(100, 100)
    red_stamp[0].insert_image((0, 0, 100, 100), stream=_solid_png(2, 2, RED), keep_proportion=False)

    doc = _new_page_doc(100, 100)
    page = doc[0]
    page.insert_image((0, 0, 100, 100), stream=_solid_png(2, 2, GREEN), keep_proportion=False)
    page.show_pdf_page((0, 0, 100, 100), red_stamp, overlay=False)
    assert _pixel(page, 50, 50) == GREEN


def test_insert_text_is_extractable_at_position() -> None:
    doc = pylopdf.Document()
    doc.new_page()  # A4-like page also verifies drawing without Resources.
    page = doc[0]
    page.insert_text((50, 100), "Confidential", fontsize=12)
    words = page.get_text("words")
    assert [w[4] for w in words] == ["Confidential"]
    x0, y0, _, y1 = words[0][:4]
    assert abs(x0 - 50) < 2  # Baseline left equals the requested x.
    assert y0 < 100 < y1  # Requested baseline y lies inside the bbox.


def test_insert_text_multiline_stacks_downward() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    page = doc[0]
    page.insert_text((50, 100), "First\nSecond", fontsize=10)
    words = {w[4]: w for w in page.get_text("words")}
    assert words["Second"][1] > words["First"][1]  # Second line is lower.


def test_insert_text_on_rotated_page_reads_upright() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)
    page = doc[0]
    page.set_rotation(90)
    page.insert_text((20, 50), "Rotated")
    pix = page.get_pixmap(background=WHITE)
    assert (pix.width, pix.height) == (200, 100)  # Rendering uses display space.
    # Extraction/search share rendering's rotation-resolved display space.
    words = page.get_text("words")
    assert [w[4] for w in words] == ["Rotated"]
    assert abs(words[0][0] - 20) < 2  # Requested display x.
    assert words[0][1] < 50 < words[0][3]  # Baseline display y lies in bbox.
    assert page.search_for("Rotated")


def test_insert_text_subset_embeds_unicode_font() -> None:
    doc = _new_page_doc(300, 150)
    page = doc[0]
    page.insert_text(
        (20, 60),
        "日本語テキスト",
        fontsize=24,
        fontfile=NOTO_SANS_JP,
        fontname="private-resource-name-is-ignored",
    )

    assert page.get_text().strip() == "日本語テキスト"
    assert page.search_for("日本語")
    # The 4.5 MB source is subset to the glyphs used by this one-page Form.
    saved = doc.tobytes()
    assert len(saved) < 50_000
    reopened = pylopdf.open(stream=saved)
    assert reopened[0].get_text().strip() == "日本語テキスト"
    assert reopened[0].search_for("テキスト")


def test_insert_text_auto_embeds_optional_cjk_font() -> None:
    pytest.importorskip("pylopdf_fonts_cjk")
    doc = _new_page_doc(300, 150)
    page = doc[0]

    page.insert_text((20, 60), "日本語", fontsize=24)
    page.insert_text((20, 100), "明朝体", fontsize=24, fontname="tiro")

    assert page.get_text().splitlines() == ["日本語", "明朝体"]
    spans = [span for block in page.get_text("dict")["blocks"] for line in block["lines"] for span in line["spans"]]
    assert spans[0]["font"].startswith("NotoSansJP")
    assert spans[1]["font"].startswith("NotoSerifJP")
    assert len(doc.tobytes()) < 100_000


def test_insert_text_embedded_fontbuffer_multiline_and_rotation() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=150, height=300)
    page = doc[0]
    page.set_rotation(90)
    page.insert_text(
        (30, 55),
        "一行目\n二行目",
        fontsize=18,
        fontbuffer=NOTO_SANS_JP.read_bytes(),
        color=(0.8, 0.1, 0.2),
    )

    words = {word[4]: word for word in page.get_text("words")}
    assert set(words) == {"一行目", "二行目"}
    assert abs(words["一行目"][0] - 30) < 2
    assert words["一行目"][1] < 55 < words["一行目"][3]
    assert words["二行目"][1] > words["一行目"][1]
    assert page.get_pixmap().width == 300


def test_insert_text_embedded_overlay_order() -> None:
    font_data = NOTO_SANS_JP.read_bytes()

    underlay = _new_page_doc(160, 80)
    underlay[0].insert_image((0, 0, 160, 80), stream=_solid_png(2, 2, GREEN), keep_proportion=False)
    underlay[0].insert_text(
        (10, 55),
        "TEXT",
        fontsize=48,
        fontbuffer=font_data,
        color=(1.0, 0.0, 0.0),
        overlay=False,
    )
    underlay_rgb = bytes(channel for offset, channel in enumerate(underlay[0].get_pixmap().samples) if offset % 4 != 3)
    assert set(underlay_rgb) == {0, 128}

    overlay = _new_page_doc(160, 80)
    overlay[0].insert_image((0, 0, 160, 80), stream=_solid_png(2, 2, GREEN), keep_proportion=False)
    overlay[0].insert_text(
        (10, 55),
        "TEXT",
        fontsize=48,
        fontbuffer=font_data,
        color=(1.0, 0.0, 0.0),
    )
    overlay_rgb = bytes(channel for offset, channel in enumerate(overlay[0].get_pixmap().samples) if offset % 4 != 3)
    assert set(overlay_rgb) != {0, 128}

    standard_underlay = _new_page_doc(160, 80)
    standard_underlay[0].insert_image((0, 0, 160, 80), stream=_solid_png(2, 2, GREEN), keep_proportion=False)
    standard_underlay[0].insert_text(
        (10, 55),
        "TEXT",
        fontsize=48,
        color=(1.0, 0.0, 0.0),
        overlay=False,
    )
    standard_rgb = bytes(
        channel for offset, channel in enumerate(standard_underlay[0].get_pixmap().samples) if offset % 4 != 3
    )
    assert set(standard_rgb) == {0, 128}


def test_insert_textbox_wraps_aligns_and_reports_spare_height() -> None:
    doc = _new_page_doc(200, 120)
    page = doc[0]
    rect = (10, 10, 110, 90)

    spare = page.insert_textbox(rect, "one two three four five six", fontsize=10, align=pylopdf.TEXT_ALIGN_RIGHT)

    assert spare > 0
    lines = page.get_text("dict")["blocks"][0]["lines"]
    assert len(lines) == 2
    assert " ".join(span["text"] for span in lines[0]["spans"]) == "one two three four five"
    assert lines[0]["bbox"][2] == pytest.approx(rect[2], abs=0.1)
    assert lines[1]["bbox"][2] == pytest.approx(rect[2], abs=0.1)
    assert all(rect[0] <= line["bbox"][0] < line["bbox"][2] <= rect[2] + 0.1 for line in lines)
    assert all(rect[1] <= line["bbox"][1] < line["bbox"][3] <= rect[3] for line in lines)


def test_insert_textbox_justifies_only_soft_wrapped_lines() -> None:
    doc = _new_page_doc(200, 120)
    page = doc[0]
    rect = (10, 10, 110, 90)

    page.insert_textbox(rect, "one two three four five six", fontsize=10, align=pylopdf.TEXT_ALIGN_JUSTIFY)

    lines = page.get_text("dict")["blocks"][0]["lines"]
    assert lines[0]["bbox"][0] == pytest.approx(rect[0], abs=0.1)
    assert lines[0]["bbox"][2] == pytest.approx(rect[2], abs=0.1)
    assert lines[1]["bbox"][0] == pytest.approx(rect[0], abs=0.1)
    assert lines[1]["bbox"][2] < rect[2] - 20


def test_insert_textbox_overflow_is_non_drawing_and_empty_text_is_free() -> None:
    doc = _new_page_doc(200, 100)
    page = doc[0]
    before = page.get_pixmap(background=WHITE).samples

    spare = page.insert_textbox((10, 10, 110, 15), "one two three four", fontsize=10)

    assert spare < 0
    assert page.get_text() == ""
    assert page.get_pixmap(background=WHITE).samples == before
    assert page.insert_textbox((10, 10, 110, 25), "", fontsize=10) == 15
    assert page.get_text() == ""


def test_insert_textbox_auto_embeds_optional_cjk_font_and_survives_rotation() -> None:
    pytest.importorskip("pylopdf_fonts_cjk")
    doc = pylopdf.Document()
    doc.new_page(width=120, height=200)
    page = doc[0]
    page.set_rotation(90)
    rect = (10, 10, 100, 80)

    spare = page.insert_textbox(
        rect,
        "日本語の文章を空白なしで折り返します。",
        fontsize=12,
        align=pylopdf.TEXT_ALIGN_CENTER,
    )

    assert spare > 0
    lines = page.get_text("dict")["blocks"][0]["lines"]
    assert len(lines) >= 3
    assert "".join(span["text"] for line in lines for span in line["spans"]) == "日本語の文章を空白なしで折り返します。"
    assert all(rect[0] <= line["bbox"][0] < line["bbox"][2] <= rect[2] + 0.1 for line in lines)
    assert all(rect[1] <= line["bbox"][1] < line["bbox"][3] <= rect[3] for line in lines)
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened[0].get_text().replace("\n", "") == "日本語の文章を空白なしで折り返します。"


def test_insert_textbox_tabs_custom_leading_and_bad_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _new_page_doc()
    page = doc[0]
    first = page.insert_textbox((10, 10, 150, 80), "a\tb\nc", fontsize=10, expandtabs=4, lineheight=1.0)
    assert first > 0
    assert page.get_text().splitlines() == ["a   b", "c"]

    with pytest.raises(ValueError, match="align"):
        page.insert_textbox((10, 10, 100, 80), "x", align=4)
    with pytest.raises(ValueError, match="expandtabs"):
        page.insert_textbox((10, 10, 100, 80), "x", expandtabs=True)
    with pytest.raises(ValueError, match="lineheight"):
        page.insert_textbox((10, 10, 100, 80), "x", lineheight=0)
    with monkeypatch.context() as context:
        context.setattr("pylopdf._bundled_cjk_fonts", lambda: ())
        with pytest.raises(ValueError, match=r"pylopdf\[cjk\].*fontfile or fontbuffer"):
            page.insert_textbox((10, 10, 100, 80), "日本語")
    with pytest.raises(pylopdf.PdfError, match="does not contain all glyphs"):
        page.insert_textbox((10, 10, 100, 80), "🦀", fontfile=NOTO_SANS_JP)


@pytest.mark.parametrize("fontfile", [None, NOTO_SANS_JP])
def test_insert_textbox_underlay_stays_below_existing_content(fontfile: Path | None) -> None:
    doc = _new_page_doc(160, 80)
    page = doc[0]
    page.insert_image((0, 0, 160, 80), stream=_solid_png(2, 2, GREEN), keep_proportion=False)

    page.insert_textbox(
        (5, 5, 155, 75),
        "TEXT",
        fontsize=48,
        fontfile=fontfile,
        color=(1.0, 0.0, 0.0),
        overlay=False,
    )

    rgb = bytes(channel for offset, channel in enumerate(page.get_pixmap().samples) if offset % 4 != 3)
    assert set(rgb) == {0, 128}


def test_get_images_bbox_on_rotated_page_is_display_space() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)
    page = doc[0]
    page.set_rotation(90)
    page.insert_image((150, 25, 190, 75), stream=_solid_png(2, 2, RED), keep_proportion=False)
    bbox = page.get_images()[0]["bbox"]
    assert abs(bbox.x0 - 150) < 1
    assert abs(bbox.y0 - 25) < 1
    assert abs(bbox.x1 - 190) < 1
    assert abs(bbox.y1 - 75) < 1


def test_insert_text_survives_save_roundtrip() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc[0].insert_text((72, 72), "Persistent")
    reopened = pylopdf.open(stream=doc.tobytes())
    assert "Persistent" in reopened[0].get_text()


def test_insert_text_page_numbering_recipe() -> None:
    # Exact page-number recipe published in the README.
    doc = pylopdf.Document()
    for _ in range(3):
        doc.new_page()
    for i, page in enumerate(doc):
        page.insert_text((page.rect.width - 90, page.rect.height - 30), f"Page {i + 1} / 3", fontsize=9)
    for i in range(3):
        assert f"Page {i + 1} / 3" in doc[i].get_text()


def test_insert_text_rejects_cjk_without_optional_or_explicit_font(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = pylopdf.Document()
    doc.new_page()
    with monkeypatch.context() as context:
        context.setattr("pylopdf._bundled_cjk_fonts", lambda: ())
        with pytest.raises(ValueError, match=r"pylopdf\[cjk\].*fontfile or fontbuffer"):
            doc[0].insert_text((50, 50), "社外秘")
    with pytest.raises(ValueError, match="fontfile or fontbuffer"):
        doc[0].insert_text((50, 50), "기밀")


def test_insert_text_rejects_unknown_font_and_bad_args() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    page = doc[0]
    with pytest.raises(ValueError, match="fontname"):
        page.insert_text((50, 50), "x", fontname="nosuch")
    with pytest.raises(ValueError, match="fontsize"):
        page.insert_text((50, 50), "x", fontsize=0)
    with pytest.raises(ValueError, match="color"):
        page.insert_text((50, 50), "x", color=(2.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="cannot both"):
        page.insert_text((50, 50), "x", fontfile=NOTO_SANS_JP, fontbuffer=b"font")
    with pytest.raises(ValueError, match="fontindex"):
        page.insert_text((50, 50), "x", fontindex=1)
    with pytest.raises(ValueError, match="fontindex"):
        page.insert_text((50, 50), "x", fontbuffer=NOTO_SANS_JP.read_bytes(), fontindex=True)
    with pytest.raises(pylopdf.PdfError, match="font data"):
        page.insert_text((50, 50), "x", fontbuffer=b"not a font")
    with pytest.raises(pylopdf.PdfError, match="does not contain all glyphs"):
        page.insert_text((50, 50), "🦀", fontfile=NOTO_SANS_JP)


def test_replace_text_replaces_and_counts() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    assert page.replace_text("PDF", "Cat") == 1
    text = page.get_text()
    assert "Hello Cat" in text
    assert "PDF" not in text


def test_replace_text_returns_zero_when_absent() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    assert "Hello PDF" in page.get_text()
    before = doc.tobytes()

    assert page.replace_text("XYZ", "abc") == 0
    assert doc.tobytes() == before
    assert "Hello PDF" in page.get_text()


def test_replace_text_requires_needle() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    with pytest.raises(ValueError, match="search"):
        doc[0].replace_text("", "abc")


def test_replace_text_detaches_shared_page_contents() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    doc.copy_page(0)

    assert doc[0].replace_text("PDF", "Cat") == 1
    assert "Hello Cat" in doc[0].get_text()
    assert "Hello PDF" in doc[1].get_text()


def test_replace_text_validates_input_without_mutation() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    before = doc.tobytes()

    with pytest.raises(ValueError, match="exactly one character"):
        page.replace_text("PDF", "Cat", default_char="??")
    with pytest.raises(pylopdf.LimitError) as caught:
        page.replace_text("PDF", "x" * 4094)
    assert caught.value.code == "replacement_input_size"
    assert doc.tobytes() == before


def test_replace_text_allows_exact_input_boundary_and_linear_fallback() -> None:
    boundary = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    assert boundary[0].replace_text("PDF", "x" * 4093) == 1

    fallback = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    assert fallback[0].replace_text("PDF", "🦀", default_char="!") == 1
    assert "Hello !" in fallback[0].get_text()


@pytest.mark.parametrize("max_size", [0, -1, True, 1.5])
def test_replace_text_validates_output_budget(max_size: object) -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    with pytest.raises((TypeError, ValueError), match="max_size"):
        doc[0].replace_text("PDF", "Cat", max_size=max_size)  # type: ignore[arg-type]


def test_replace_text_bounds_content_and_preserves_document() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    before = doc.tobytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        page.replace_text("PDF", "Cat", max_size=16)
    assert caught.value.code == "replacement_output_size"
    assert doc.tobytes() == before


def test_replace_text_bounds_replacement_growth_before_mutation() -> None:
    doc = pylopdf.open(stream=build_pdf(["A" * 100]))
    before = doc.tobytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        doc[0].replace_text("A", "BBBBBBBB", max_size=256)
    assert caught.value.code == "replacement_output_size"
    assert doc.tobytes() == before


def test_replace_text_rejects_oversized_contents_shape_before_mutation() -> None:
    doc = _many_contents_doc(4097)
    before = doc.tobytes()

    with pytest.raises(pylopdf.PdfError, match="4096-entry"):
        doc[0].replace_text("x", "y")
    assert doc.tobytes() == before


def test_replace_text_error_is_atomic() -> None:
    stream = b"Tf (Hello) Tj"
    doc = pylopdf.open(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
            }
        )
    )
    before = doc.tobytes()

    with pytest.raises(pylopdf.PdfError, match="text replacement failed"):
        doc[0].replace_text("Hello", "Goodbye")
    assert doc.tobytes() == before
