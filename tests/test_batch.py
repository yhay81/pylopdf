"""Drawing batches: text and shapes on several pages with one subset per font."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from conftest import with_weight_class

import pylopdf
from pylopdf.pylopdf_core import _Document

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
A4 = (595.28, 841.89)
WHITE = (255, 255, 255)
GREEN = (0.0, 0.5, 0.0)
RED = (1.0, 0.0, 0.0)
MARKER = (0.83, 0.26, 0.05)


@pytest.fixture(scope="module")
def regular() -> pylopdf.Font:
    return pylopdf.Font(NOTO_SANS_JP)


@pytest.fixture(scope="module")
def bold() -> pylopdf.Font:
    """Return a stand-in bold face: Noto Sans JP declaring weight class 700."""
    return pylopdf.Font(fontbuffer=with_weight_class(NOTO_SANS_JP.read_bytes(), 700))


def _chars(page: pylopdf.Page) -> list[pylopdf.TextChar]:
    return [
        char
        for block in page.get_text("rawdict")["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]


def _line_chars(page: pylopdf.Page, baseline: float) -> list[pylopdf.TextChar]:
    return [char for char in _chars(page) if abs(char["origin"][1] - baseline) < 0.5]


def _embedded_font_files(data: bytes) -> int:
    return data.count(b"/FontFile")


def _pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int]:
    pixmap = page.get_pixmap(background=WHITE)
    offset = y * pixmap.stride + x * 4
    red, green, blue = pixmap.samples[offset : offset + 3]
    return (red, green, blue)


def test_site_survey_report_shape(regular: pylopdf.Font, bold: pylopdf.Font) -> None:
    """New A4 pages with regular and bold Japanese and shapes embed each face once."""
    doc = pylopdf.Document()
    headings = [f"現地調査報告書 {number}" for number in range(1, 4)]

    with doc.batch() as batch:
        for heading in headings:
            canvas = batch.new_page(*A4)
            assert canvas.rect == pytest.approx((0, 0, *A4))
            end = canvas.insert_text((56, 70), heading, font=bold, fontsize=18, color=(0.09, 0.23, 0.34))
            assert end == pytest.approx((56 + bold.text_length(heading, 18), 70))
            label_end = canvas.insert_text((56, 100), "所在地：", font=bold, fontsize=10.5)
            canvas.insert_text(label_end, "東京都千代田区", font=regular, fontsize=10.5)
            for row in range(40):
                canvas.insert_text(
                    (56, 130 + 16 * row), f"{row + 1}. 調査項目の本文です。", font=regular, fontsize=10.5
                )
            canvas.draw_rect((40, 40, 555, 800), color=(0.62, 0.7, 0.76), width=0.5)
            canvas.draw_rect((56, 108, 300, 112), color=None, fill=(0.91, 0.94, 0.96))
            canvas.draw_line((56, 82), (539, 82), color=(0.09, 0.23, 0.34), width=1.6)
            canvas.draw_circle((500, 64), 7, color=(1, 1, 1), fill=MARKER, width=1.2)
            canvas.draw_polyline([(480, 56), (490, 76), (470, 76)], closed=True, color=None, fill=MARKER)

    saved = doc.tobytes()
    assert _embedded_font_files(saved) == 2
    reopened = pylopdf.open(stream=saved)
    assert reopened.page_count == 3
    for page, heading in zip(reopened, headings, strict=True):
        assert page.rect == pytest.approx((0, 0, *A4))
        lines = page.get_text().splitlines()
        assert lines[:3] == [heading, "所在地：東京都千代田区", "1. 調査項目の本文です。"]
        assert len(lines) == 42
        # The measured width is the drawn width.
        drawn = _line_chars(page, 70)
        assert drawn[0]["origin"][0] == pytest.approx(56, abs=1e-3)
        assert drawn[-1]["bbox"][2] == pytest.approx(56 + bold.text_length(heading, 18), abs=1e-2)
        value = _line_chars(page, 100)
        assert value[len("所在地：")]["origin"][0] == pytest.approx(56 + bold.text_length("所在地：", 10.5), abs=1e-2)
        drawings = page.get_drawings()
        assert [drawing["type"] for drawing in drawings] == ["s", "f", "s", "fs", "f"]
        assert [drawing["width"] for drawing in drawings if drawing["type"] != "f"] == pytest.approx([0.5, 1.6, 1.2])
        assert drawings[3]["fill"] == pytest.approx(MARKER, abs=1 / 255)
        assert drawings[3]["rect"] == pytest.approx((493, 57, 507, 71), abs=1e-3)


def test_one_subset_per_face_replaces_one_per_call(regular: pylopdf.Font) -> None:
    lines = [f"{row + 1}. 本文の行" for row in range(5)]
    per_call = pylopdf.Document()
    batched = pylopdf.Document()
    for _ in range(2):
        page = per_call.new_page(width=A4[0], height=A4[1])
        for row, text in enumerate(lines):
            page.insert_text((56, 80 + 20 * row), text, font=regular)

    with batched.batch() as batch:
        for _ in range(2):
            canvas = batch.new_page(*A4)
            for row, text in enumerate(lines):
                canvas.insert_text((56, 80 + 20 * row), text, font=regular)

    per_call_bytes, batched_bytes = per_call.tobytes(), batched.tobytes()
    assert _embedded_font_files(per_call_bytes) == 10
    assert _embedded_font_files(batched_bytes) == 1
    assert len(batched_bytes) * 4 < len(per_call_bytes)
    assert [page.get_text() for page in batched] == [page.get_text() for page in per_call]


def test_batch_stamps_existing_pages_above_or_below_their_content(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    for _ in range(3):
        page = doc.new_page(width=200, height=100)
        page.draw_rect((0, 0, 200, 100), color=None, fill=GREEN)
    count = doc.page_count

    with doc.batch() as batch:
        for pno in range(count):
            canvas = batch.page(pno)
            canvas.insert_text((10, 90), f"{pno + 1} / {count}", font=regular, fontsize=9)
            canvas.draw_rect((150, 10, 190, 50), color=None, fill=RED)

    under = pylopdf.Document()
    under.new_page(width=200, height=100).draw_rect((0, 0, 200, 100), color=None, fill=GREEN)
    with under.batch() as batch:
        batch.page(0, overlay=False).draw_rect((150, 10, 190, 50), color=None, fill=RED)

    assert [page.get_text().strip() for page in doc] == ["1 / 3", "2 / 3", "3 / 3"]
    assert _embedded_font_files(doc.tobytes()) == 1
    assert _pixel(doc[1], 170, 30) == (255, 0, 0)
    assert _pixel(under[0], 170, 30) == (0, 128, 0)


def test_canvas_follows_page_rotation_and_crop(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    page = doc.new_page(width=300, height=200)
    page.set_cropbox((20, 10, 280, 190))
    page.set_rotation(90)
    expected = pylopdf.Document(stream=doc.tobytes())

    with doc.batch() as batch:
        canvas = batch.page(0)
        assert (canvas.width, canvas.height) == (180, 260)
        canvas.insert_text((30, 50), "回転", font=regular, fontsize=12)
        canvas.draw_rect((100, 100, 140, 160), color=(0, 0, 1), width=2)
    expected[0].insert_text((30, 50), "回転", font=regular, fontsize=12)
    expected[0].draw_rect((100, 100, 140, 160), color=(0, 0, 1), width=2)

    word = doc[0].get_text("words")[0]
    assert word[4] == "回転"
    assert word[:4] == pytest.approx(expected[0].get_text("words")[0][:4], abs=1e-3)
    assert word[0] == pytest.approx(30, abs=1e-3)
    assert doc[0].get_drawings()[0]["rect"] == pytest.approx((100, 100, 140, 160), abs=1e-3)


def test_canvas_text_matches_page_text_and_textbox(regular: pylopdf.Font) -> None:
    text = "見出しAVATAR\n二行目"
    box_text = "折り返して置く本文です。" * 6
    rect = (20, 120, 220, 260)
    direct = pylopdf.Document()
    direct_page = direct.new_page(width=300, height=300)
    direct_page.insert_text((20, 40), text, font=regular, fontsize=14)
    direct_spare = direct_page.insert_textbox(rect, box_text, font=regular, fontsize=11, align=pylopdf.TEXT_ALIGN_RIGHT)

    batched = pylopdf.Document()
    with batched.batch() as batch:
        canvas = batch.new_page(300, 300)
        end = canvas.insert_text((20, 40), text, font=regular, fontsize=14)
        spare = canvas.insert_textbox(rect, box_text, font=regular, fontsize=11, align=pylopdf.TEXT_ALIGN_RIGHT)

    assert spare == pytest.approx(direct_spare)
    assert end == pytest.approx((20 + regular.text_length("二行目", 14), 40 + 14 * 1.2))
    expected = direct_page.get_text("rawdict")
    actual = batched[0].get_text("rawdict")
    expected_chars = [
        char
        for block in expected["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]
    actual_chars = [
        char
        for block in actual["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]
    assert [char["c"] for char in actual_chars] == [char["c"] for char in expected_chars]
    for actual_char, expected_char in zip(actual_chars, expected_chars, strict=True):
        assert actual_char["origin"] == pytest.approx(expected_char["origin"], abs=1e-3)


def test_textbox_overflow_and_empty_text_record_nothing(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=200, height=100)
    before = doc.tobytes()

    with doc.batch() as batch:
        canvas = batch.page(0)
        assert canvas.insert_textbox((10, 10, 110, 15), "収まらない長さの本文", font=regular) < 0
        assert canvas.insert_textbox((10, 10, 110, 25), "", font=regular) == 15

    assert doc.tobytes() == before


def test_text_runs_continue_from_the_returned_point(regular: pylopdf.Font, bold: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    with doc.batch() as batch:
        canvas = batch.new_page(300, 100)
        end = canvas.insert_text((10, 50), "太字", font=bold, fontsize=12)
        end = canvas.insert_text(end, "と通常", font=regular, fontsize=12)
        canvas.insert_text(end, "の続き", font=bold, fontsize=12)

    chars = _line_chars(doc[0], 50)
    assert "".join(char["c"] for char in chars) == "太字と通常の続き"
    for previous, following in pairwise(chars):
        assert following["origin"][0] == pytest.approx(previous["bbox"][2], abs=1e-2)


def test_text_is_filled_after_a_stroked_shape(regular: pylopdf.Font) -> None:
    """A preceding outline must not stroke glyphs into faux bold."""
    doc = pylopdf.Document()
    with doc.batch() as batch:
        canvas = batch.new_page(200, 100)
        canvas.draw_rect((5, 5, 195, 95), color=(0, 0, 0), width=3)
        canvas.insert_text((20, 60), "本文", font=regular, fontsize=20)

    content = doc.tobytes(deflate=False)
    assert b"0 Tr" in content
    assert b"1 Tr" not in content
    assert b"2 Tr" not in content
    assert doc[0].get_text().strip() == "本文"


def test_batch_output_is_deterministic_and_loads_sequentially(regular: pylopdf.Font) -> None:
    def build() -> bytes:
        doc = pylopdf.Document()
        with doc.batch() as batch:
            for number in range(2):
                canvas = batch.new_page(*A4)
                canvas.insert_text((56, 70), f"頁 {number}", font=regular, fontsize=12)
                canvas.draw_circle((100, 100), 10, fill=MARKER)
        return doc.tobytes()

    sequential, fallback, _ = _Document.generated_pdf_loads()
    first = build()
    after = _Document.generated_pdf_loads()
    assert after[0] == sequential + 1
    assert after[1] == fallback
    assert build() == first


def _draw_then_fail(doc: pylopdf.Document, font: pylopdf.Font) -> pylopdf.DrawingBatch:
    """Record drawing in a ``with`` block that an exception leaves."""
    with doc.batch() as batch:
        batch.page(0).insert_text((10, 50), "破棄", font=font)
        batch.new_page()
        message = "stop"
        raise RuntimeError(message)


def test_exception_discards_the_batch(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=200, height=100)
    before = doc.tobytes()
    page = doc[0]
    batches: list[pylopdf.DrawingBatch] = []

    with pytest.raises(RuntimeError, match="stop"):
        batches.append(_draw_then_fail(doc, regular))

    assert not batches
    assert doc.tobytes() == before
    assert page.rect.width == 200
    batch = doc.batch()
    batch.commit()
    with pytest.raises(pylopdf.PdfError, match="already committed or discarded"):
        batch.new_page()
    with pytest.raises(pylopdf.PdfError, match="already committed or discarded"):
        batch.commit()


def test_new_pages_append_in_canvas_order_even_when_blank(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    old_page = doc[0]

    with doc.batch() as batch:
        batch.new_page(200, 100)
        batch.new_page(300, 100).insert_text((10, 50), "三頁目", font=regular)
        batch.page(0).draw_line((0, 0), (100, 100))

    assert [page.rect.width for page in doc] == [100, 200, 300]
    assert doc[1].get_text() == ""
    assert doc[2].get_text().strip() == "三頁目"
    with pytest.raises(pylopdf.StalePageError):
        _ = old_page.rect


def test_a_batch_without_new_pages_keeps_page_views(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    page = doc.new_page(width=100, height=100)
    with doc.batch() as batch:
        batch.page(0).insert_text((10, 50), "印", font=regular)
    assert page.get_text().strip() == "印"


def test_commit_checks_pages_before_changing_the_document(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    for _ in range(2):
        doc.new_page(width=200, height=100)
    batch = doc.batch()
    batch.page(1).insert_text((10, 50), "削除", font=regular)
    batch.new_page()
    doc.delete_page(1)
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match="no longer exists"):
        batch.commit()
    assert doc.tobytes() == before

    rotated = doc.batch()
    rotated.page(0).draw_line((0, 0), (10, 10))
    doc[0].set_rotation(90)
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match="display size changed"):
        rotated.commit()
    assert doc.tobytes() == before


def test_page_canvases_are_shared_per_page(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    with doc.batch() as batch:
        canvas = batch.page(0)
        assert batch.page(-1) is canvas
        with pytest.raises(ValueError, match="other overlay setting"):
            batch.page(0, overlay=False)
        canvas.insert_text((10, 50), "一", font=regular)
        with pytest.raises(IndexError):
            batch.page(1)
    assert repr(canvas) == "Canvas(width=100.0, height=100.0)"


def test_batch_limits_and_validation(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    small = doc.batch(max_text_size=8)
    canvas = small.page(0)
    canvas.insert_text((10, 50), "あい", font=regular)
    with pytest.raises(pylopdf.LimitError) as exc_info:
        canvas.insert_text((10, 70), "うえ", font=regular)
    assert exc_info.value.code == "text_input_size"
    with pytest.raises(pylopdf.LimitError) as exc_info:
        canvas.insert_text((10, 70), "\n" * 4096, font=regular)
    assert exc_info.value.code in {"text_input_size", "text_line_count"}
    for value in (0, -1, True, 1.5):
        with pytest.raises((TypeError, ValueError), match="max_text_size"):
            doc.batch(max_text_size=value)  # ty: ignore[invalid-argument-type]

    lines = doc.batch(max_text_size=None).page(0)
    with pytest.raises(pylopdf.LimitError) as exc_info:
        lines.insert_text((10, 50), "x\n" * 4096, font=regular)
    assert exc_info.value.code == "text_line_count"

    pages = doc.batch()
    for _ in range(4096):
        pages.new_page(10, 10)
    with pytest.raises(pylopdf.LimitError) as exc_info:
        pages.new_page(10, 10)
    assert exc_info.value.code == "batch_page_count"


def test_batch_path_points_are_bounded() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    canvas = doc.batch().page(0)
    half = [(float(index % 100), 1.0) for index in range(524_288)]
    canvas.draw_polyline(half)
    canvas.draw_polyline(half)
    with pytest.raises(pylopdf.LimitError) as exc_info:
        canvas.draw_line((0, 0), (1, 1))
    assert exc_info.value.code == "path_point_count"
    with pytest.raises(pylopdf.LimitError) as exc_info:
        doc.batch().page(0).draw_polyline((float(index), 0.0) for index in range(1_048_577))
    assert exc_info.value.code == "path_point_count"


def test_shape_and_text_arguments_are_validated(regular: pylopdf.Font) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    canvas = doc.batch().page(0)
    with pytest.raises(ValueError, match="cannot both be None"):
        canvas.draw_rect((1, 1, 9, 9), color=None)
    for width in (0, -1, float("nan"), "wide"):
        with pytest.raises(ValueError, match="width"):
            canvas.draw_line((0, 0), (1, 1), width=width)  # ty: ignore[invalid-argument-type]
    for radius in (0, -2, float("inf")):
        with pytest.raises(ValueError, match="radius"):
            canvas.draw_circle((5, 5), radius)
    with pytest.raises(ValueError, match="at least 2 points"):
        canvas.draw_polyline([(0, 0)])
    with pytest.raises(ValueError, match="at least 3 points"):
        canvas.draw_polyline([(0, 0), (1, 1)], closed=True)
    with pytest.raises(ValueError, match="finite coordinates"):
        canvas.draw_line((0, float("nan")), (1, 1))
    with pytest.raises(ValueError, match="x0 < x1"):
        canvas.draw_rect((9, 9, 1, 1))
    with pytest.raises(ValueError, match="color"):
        canvas.draw_rect((1, 1, 9, 9), fill=(2, 0, 0))
    with pytest.raises(TypeError, match=r"pylopdf\.Font"):
        canvas.insert_text((10, 50), "x", font="helv")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="non-empty"):
        canvas.insert_text((10, 50), "", font=regular)
    with pytest.raises(pylopdf.PdfError, match=r"U\+1F980"):
        canvas.insert_text((10, 50), "🦀", font=regular)
    batch = doc.batch()
    for size in ((0, 10), (10, float("nan")), (-1, 10)):
        with pytest.raises(ValueError, match="width / height"):
            batch.new_page(*size)


def test_page_draw_methods_add_shapes_immediately() -> None:
    doc = pylopdf.Document()
    page = doc.new_page(width=200, height=200)
    page.draw_line((10, 10), (190, 10), color=(0, 0, 1), width=2)
    page.draw_rect((10, 20, 60, 60), fill=(1, 1, 0))
    page.draw_circle((100, 100), 20, color=None, fill=(0, 1, 0))
    page.draw_polyline([(150, 150), (190, 150), (170, 190)], closed=True, color=None, fill=(1, 0, 0), overlay=False)

    drawings = page.get_drawings()
    assert [drawing["type"] for drawing in drawings] == ["f", "s", "fs", "f"]
    assert drawings[1]["color"] == pytest.approx((0, 0, 1))
    assert drawings[1]["width"] == pytest.approx(2)
    assert drawings[2]["fill"] == pytest.approx((1, 1, 0))
    assert drawings[3]["rect"] == pytest.approx((80, 80, 120, 120), abs=1e-3)
    assert _pixel(page, 100, 100) == (0, 255, 0)
    reopened = pylopdf.open(stream=doc.tobytes())
    assert len(reopened[0].get_drawings()) == 4
