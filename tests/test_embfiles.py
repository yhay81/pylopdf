"""添付ファイル（Document.embfile_*）のテスト。"""

from __future__ import annotations

import pytest
from conftest import build_pdf

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
    with pytest.raises(pylopdf.PdfError, match="既にあります"):
        doc.embfile_add("a.txt", b"other")
    with pytest.raises(pylopdf.PdfError, match="見つかりません"):
        doc.embfile_get("missing.txt")
    with pytest.raises(pylopdf.PdfError, match="見つかりません"):
        doc.embfile_del("missing.txt")
    with pytest.raises(ValueError, match="name"):
        doc.embfile_add("", b"x")
