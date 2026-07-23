"""添付ファイル（Document.embfile_*）のテスト。"""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def test_embfile_add_get_roundtrip() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello"]))
    payload = b"\x00\x01binary\xff\xfe" * 100
    doc.embfile_add("invoice.xml", payload, filename="請求書データ.xml", desc="請求書の構造化データ")
    assert doc.embfile_names() == ["invoice.xml"]
    assert doc.embfile_get("invoice.xml") == payload

    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.embfile_names() == ["invoice.xml"]
    assert reopened.embfile_get("invoice.xml") == payload


def test_embfile_survives_compressed_and_garbage_save() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello"]))
    payload = b"repetitive " * 1000
    doc.embfile_add("data.txt", payload)
    data = doc.tobytes(garbage=True, deflate=True, object_streams=True)
    reopened = pylopdf.open(stream=data)
    assert reopened.embfile_get("data.txt") == payload


def test_embfile_multiple_names_sorted() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("b.txt", b"B")
    doc.embfile_add("a.txt", b"A")
    assert doc.embfile_names() == ["a.txt", "b.txt"]
    assert doc.embfile_get("a.txt") == b"A"
    assert doc.embfile_get("b.txt") == b"B"


def test_inline_filespec_reads_do_not_mutate_document() -> None:
    """合法なインライン FileSpec を読むだけで孤立オブジェクトを増やさない。"""
    pdf = build_raw_pdf(
        {
            1: (
                "<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << "
                "/Names [(x.txt) << /Type /Filespec /F (x.txt) /EF << /F 4 0 R >> >>] >> >> >>"
            ),
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: b"<< /Type /EmbeddedFile /Length 3 >>\nstream\nabc\nendstream",
        }
    )
    doc = pylopdf.open(stream=pdf)
    before = len(doc.tobytes())

    for _ in range(4):
        assert doc.embfile_names() == ["x.txt"]
        assert doc.embfile_get("x.txt") == b"abc"

    assert len(doc.tobytes()) == before
    doc.embfile_add("y.txt", b"def")
    assert doc.embfile_names() == ["x.txt", "y.txt"]
    assert doc.embfile_get("x.txt") == b"abc"
    doc.embfile_del("x.txt")
    assert doc.embfile_names() == ["y.txt"]


def test_embfile_del_removes() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("a.txt", b"A")
    doc.embfile_add("b.txt", b"B")
    doc.embfile_del("a.txt")
    assert doc.embfile_names() == ["b.txt"]
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.embfile_names() == ["b.txt"]


def test_embfile_errors() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("a.txt", b"A")
    with pytest.raises(pylopdf.PdfError, match="already exists"):
        doc.embfile_add("a.txt", b"other")
    with pytest.raises(pylopdf.PdfError, match="not found"):
        doc.embfile_get("missing.txt")
    with pytest.raises(pylopdf.PdfError, match="not found"):
        doc.embfile_del("missing.txt")
    with pytest.raises(ValueError, match="name"):
        doc.embfile_add("", b"x")
