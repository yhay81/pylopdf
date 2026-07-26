"""Tests for the invisible OCR layer from Page.insert_ocr_text_layer.

Verify both sides of the contract: nothing is drawn, while extraction and
search see the text. Because no font program is embedded, behavior must not
depend on CJK fallback fonts.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import build_pdf

import pylopdf


def _blank_page_doc() -> pylopdf.Document:
    doc = pylopdf.Document()
    doc.new_page(width=300, height=200)
    return doc


def _rotate_box_clockwise(
    box: tuple[float, float, float, float],
    width: float,
    height: float,
    rotation: int,
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    if rotation == 90:
        return (height - y1, x0, height - y0, x1)
    if rotation == 180:
        return (width - x1, height - y1, width - x0, height - y0)
    if rotation == 270:
        return (y0, width - x1, y1, width - x0)
    return box


def test_ocr_layer_is_extractable_and_searchable() -> None:
    doc = _blank_page_doc()
    page = doc[0]
    page.insert_ocr_text_layer(
        [
            (50, 50, 150, 70, "Hello"),
            (50, 80, 170, 100, "日本語テキスト"),
        ]
    )
    text = page.get_text()
    assert "Hello" in text
    assert "日本語テキスト" in text

    hits = page.search_for("日本語")
    assert hits
    # Match near the requested bbox; the synthetic font need not match exactly.
    assert abs(hits[0].x0 - 50) < 5
    assert 70 < hits[0].y0 < 105
    assert 70 < hits[0].y1 < 110


def test_ocr_layer_is_invisible() -> None:
    doc = _blank_page_doc()
    page = doc[0]
    page.insert_ocr_text_layer([(50, 50, 150, 70, "Invisible")])
    pix = page.get_pixmap(background=(255, 255, 255))
    samples = pix.samples
    assert all(samples[i] == 255 for i in range(0, len(samples), 4))  # Every pixel remains white.


def test_ocr_layer_does_not_need_fallback_fonts() -> None:
    # A non-embedded reference font remains extractable when CJK fallbacks are
    # disabled, proving independence from the [cjk] extra.
    doc = _blank_page_doc()
    doc.set_fallback_font(None)
    page = doc[0]
    page.insert_ocr_text_layer([(50, 50, 200, 75, "帳票スキャン")])
    assert "帳票スキャン" in page.get_text()


def test_ocr_layer_survives_save_roundtrip() -> None:
    doc = _blank_page_doc()
    doc[0].insert_ocr_text_layer([(50, 50, 150, 70, "Persistent"), (50, 80, 150, 100, "残存")])
    reopened = pylopdf.open(stream=doc.tobytes())
    text = reopened[0].get_text()
    assert "Persistent" in text
    assert "残存" in text
    assert reopened[0].search_for("残存")


def test_ocr_layer_accepts_get_text_words_shape() -> None:
    # Pass get_text("words") eight-item tuples directly; only the first five
    # items are used.
    src = pylopdf.Document()
    src.new_page()
    src[0].insert_text((72, 100), "Roundtrip works")
    words = src[0].get_text("words")
    assert words

    doc = _blank_page_doc()
    doc[0].insert_ocr_text_layer(words)
    extracted = {w[4] for w in doc[0].get_text("words")}
    assert extracted == {"Roundtrip", "works"}


def test_ocr_layer_on_rotated_page_uses_display_coordinates() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)
    page = doc[0]
    page.set_rotation(90)  # Display is 200 x 100.
    page.insert_ocr_text_layer([(120, 30, 180, 50, "回転ページ")])
    hits = page.search_for("回転ページ")
    assert hits
    assert abs(hits[0].x0 - 120) < 5  # Match near the requested display coordinates.


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_ocr_layer_orients_sideways_baselines(rotation: pylopdf.OcrRotation) -> None:
    doc = _blank_page_doc()
    page = doc[0]
    page.insert_ocr_text_layer([(80, 30, 105, 150, "Vertical box")], rotation=rotation)

    hits = page.search_for("Vertical box")

    assert hits
    assert hits[0].height > hits[0].width
    assert tuple(hits[0]) == pytest.approx((80, 30, 105, 150), abs=0.1)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_ocr_layer_orders_rotated_columns_logically(rotation: pylopdf.OcrRotation) -> None:
    logical_width = 400.0
    logical_height = 300.0
    logical_words = [
        ((20.0, 10.0, 380.0, 35.0), "Spanning heading"),
        ((40.0, 60.0, 150.0, 80.0), "Left one"),
        ((40.0, 100.0, 150.0, 120.0), "Left two"),
        ((40.0, 140.0, 150.0, 160.0), "Left three"),
        ((240.0, 60.0, 350.0, 80.0), "Right one"),
        ((240.0, 100.0, 350.0, 120.0), "Right two"),
        ((240.0, 140.0, 350.0, 160.0), "Right three"),
        ((20.0, 260.0, 380.0, 285.0), "Spanning footer"),
    ]
    display_rotation = (360 - rotation) % 360
    physical_words = [
        (
            _rotate_box_clockwise(box, logical_width, logical_height, display_rotation),
            text,
        )
        for box, text in logical_words
    ]
    page_width, page_height = (
        (logical_height, logical_width) if display_rotation in (90, 270) else (logical_width, logical_height)
    )
    doc = pylopdf.Document()
    doc.new_page(width=page_width, height=page_height)
    page = doc[0]
    # Geometry, not caller ordering, determines the logical reading sequence.
    scrambled = physical_words[4:] + physical_words[:4]
    page.insert_ocr_text_layer(
        [(*box, text) for box, text in scrambled],
        rotation=rotation,
    )

    expected = [text for _, text in logical_words]
    assert page.get_text().splitlines() == expected
    for box, text in physical_words:
        hits = page.search_for(text)
        assert len(hits) == 1
        assert tuple(hits[0]) == pytest.approx(box, abs=0.1)


def test_ocr_layer_rejects_empty() -> None:
    doc = _blank_page_doc()
    with pytest.raises(ValueError, match="words"):
        doc[0].insert_ocr_text_layer([])
    with pytest.raises(ValueError, match="words"):
        doc[0].insert_ocr_text_layer([(10, 10, 50, 20, "")])
    with pytest.raises(pylopdf.OcrError, match="rotation"):
        doc[0].insert_ocr_text_layer([(10, 10, 50, 20, "Text")], rotation=45)  # type: ignore[arg-type]


def test_ocr_layer_bounds_generator_words_before_full_materialization() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello"]))
    before = doc.tobytes()
    consumed = 0

    def words() -> Iterator[tuple[int, int, int, int, str]]:
        nonlocal consumed
        for index in range(5000):
            consumed += 1
            yield (10, 10, 50, 20, f"word-{index}")

    with pytest.raises(ValueError, match="4096 non-empty entries"):
        doc[0].insert_ocr_text_layer(words())
    assert consumed == 4097
    assert doc.tobytes() == before


def test_ocr_layer_bounds_utf8_text_before_mutation() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello"]))
    before = doc.tobytes()

    with pytest.raises(ValueError, match="1048576 UTF-8 bytes"):
        doc[0].insert_ocr_text_layer([(10, 10, 50, 20, "é" * (512 * 1024 + 1))])
    assert doc.tobytes() == before


def test_ocr_layer_bounds_distinct_cids_before_mutation() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello"]))
    before = doc.tobytes()
    text = "".join(chr(codepoint) for codepoint in range(1, 0x11000) if not 0xD800 <= codepoint <= 0xDFFF)
    text = text[:65_535]

    with pytest.raises(pylopdf.PdfError, match="65,534 per call"):
        doc[0].insert_ocr_text_layer([(10, 10, 290, 20, text)])
    assert doc.tobytes() == before
