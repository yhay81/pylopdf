"""ページ操作（範囲付き結合・挿入位置・new_page・copy_page）のテスト。"""

from __future__ import annotations

import pytest
from conftest import png_size

import pylopdf


def test_insert_pdf_range(three_page_pdf: bytes, one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    src = pylopdf.Document(stream=three_page_pdf)
    doc.insert_pdf(src, from_page=1, to_page=2)
    assert doc.page_count == 3
    assert "Page two" in doc.get_page_text(1)
    assert "Page three" in doc.get_page_text(2)


def test_insert_pdf_reversed_range(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document()
    src = pylopdf.Document(stream=three_page_pdf)
    doc.insert_pdf(src, from_page=2, to_page=0)
    assert doc.page_count == 3
    assert "Page three" in doc.get_page_text(0)
    assert "Page one" in doc.get_page_text(2)


def test_insert_pdf_negative_range(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document()
    doc.insert_pdf(pylopdf.Document(stream=three_page_pdf), from_page=-2, to_page=-1)
    assert doc.page_count == 2
    assert "Page two" in doc.get_page_text(0)


def test_insert_pdf_start_at(three_page_pdf: bytes, one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    src = pylopdf.Document(stream=one_page_pdf)
    doc.insert_pdf(src, start_at=1)
    assert doc.page_count == 4
    assert "Page one" in doc.get_page_text(0)
    assert "Hello PDF" in doc.get_page_text(1)
    assert "Page two" in doc.get_page_text(2)
    # 保存 → 再読込しても並びが保たれる
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert "Hello PDF" in reloaded.get_page_text(1)


def test_insert_pdf_start_at_zero_prepends(three_page_pdf: bytes, one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.insert_pdf(pylopdf.Document(stream=one_page_pdf), start_at=0)
    assert "Hello PDF" in doc.get_page_text(0)
    assert "Page one" in doc.get_page_text(1)


def test_insert_pdf_start_at_out_of_range(three_page_pdf: bytes, one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(IndexError, match="start_at"):
        doc.insert_pdf(pylopdf.Document(stream=one_page_pdf), start_at=4)


def test_insert_pdf_empty_source_noop(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.insert_pdf(pylopdf.Document())
    assert doc.page_count == 3


def test_insert_pdf_does_not_keep_unreachable_source_data() -> None:
    """ページから到達しない添付データを取り込み先へ漏らさない。"""
    secret = b"SECRET-UNREFERENCED-ATTACHMENT-7c3f"
    source = pylopdf.Document()
    source.new_page(width=100, height=100)
    source.embfile_add("secret.txt", secret)

    target = pylopdf.Document()
    target.insert_pdf(source)

    assert target.embfile_names() == []
    assert secret not in target.tobytes()


def test_new_page_appends_blank(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    page = doc.new_page()
    assert doc.page_count == 2
    assert page.number == 1
    assert page.mediabox == pylopdf.Rect(0.0, 0.0, 595.0, 842.0)
    assert page.get_text() == ""
    assert png_size(doc.render_page(1)) == (595, 842)


def test_new_page_insert_position_and_size(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    page = doc.new_page(1, width=300, height=400)
    assert page.number == 1
    assert doc.page_count == 4
    assert doc.get_page_text(1) == ""
    assert "Page two" in doc.get_page_text(2)
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.page_count == 4
    assert png_size(reloaded.render_page(1)) == (300, 400)


def test_new_page_invalid_size(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="width"):
        doc.new_page(width=0)
    with pytest.raises(ValueError, match="PDF real-number"):
        doc.new_page(width=1e39)


def test_copy_page_append_and_position(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.copy_page(0)
    assert doc.page_count == 4
    assert "Page one" in doc.get_page_text(3)
    doc.copy_page(2, to=0)  # 現 2 ページ目（Page three）を先頭へ複製
    assert doc.page_count == 5
    assert "Page three" in doc.get_page_text(0)
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.page_count == 5
    assert "Page three" in reloaded.get_page_text(0)
    assert reloaded.render_page(0) == reloaded.render_page(3)


def test_structure_ops_invalidate_pages(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    page = doc[0]
    doc.new_page()
    with pytest.raises(pylopdf.StalePageError):
        _ = page.mediabox
