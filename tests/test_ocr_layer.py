"""Tests for the invisible OCR layer from Page.insert_ocr_text_layer.

Verify both sides of the contract: nothing is drawn, while extraction and
search see the text. Because no font program is embedded, behavior must not
depend on CJK fallback fonts.
"""

from __future__ import annotations

import pytest

import pylopdf


def _blank_page_doc() -> pylopdf.Document:
    doc = pylopdf.Document()
    doc.new_page(width=300, height=200)
    return doc


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


def test_ocr_layer_rejects_empty() -> None:
    doc = _blank_page_doc()
    with pytest.raises(ValueError, match="words"):
        doc[0].insert_ocr_text_layer([])
    with pytest.raises(ValueError, match="words"):
        doc[0].insert_ocr_text_layer([(10, 10, 50, 20, "")])
