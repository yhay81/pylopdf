"""ページラベル（Document.get/set_page_labels・Page.get_label）のテスト。"""

from __future__ import annotations

import pytest
from conftest import build_pdf

import pylopdf


def _six_page_doc() -> pylopdf.Document:
    return pylopdf.open(stream=build_pdf([f"Page {i}" for i in range(6)]))


def test_set_and_compute_labels() -> None:
    doc = _six_page_doc()
    doc.set_page_labels(
        [
            {"startpage": 0, "style": "r"},
            {"startpage": 3, "style": "D", "prefix": "A-"},
        ]
    )
    assert [doc[i].get_label() for i in range(6)] == ["i", "ii", "iii", "A-1", "A-2", "A-3"]


def test_labels_roundtrip_through_save() -> None:
    doc = _six_page_doc()
    labels = [
        {"startpage": 0, "style": "R", "prefix": "", "firstpagenum": 5},
        {"startpage": 2, "style": "a", "prefix": "付-", "firstpagenum": 1},
    ]
    doc.set_page_labels(labels)
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.get_page_labels() == [
        {"startpage": 0, "style": "R", "prefix": "", "firstpagenum": 5},
        {"startpage": 2, "style": "a", "prefix": "付-", "firstpagenum": 1},
    ]
    assert reopened[0].get_label() == "V"
    assert reopened[1].get_label() == "VI"
    assert reopened[2].get_label() == "付-a"


def test_prefix_only_style() -> None:
    doc = _six_page_doc()
    doc.set_page_labels([{"startpage": 0, "prefix": "表紙"}])
    assert doc[0].get_label() == "表紙"
    assert doc[5].get_label() == "表紙"


def test_letters_style_wraps_past_z() -> None:
    doc = _six_page_doc()
    doc.set_page_labels([{"startpage": 0, "style": "A", "firstpagenum": 26}])
    assert doc[0].get_label() == "Z"
    assert doc[1].get_label() == "AA"
    assert doc[2].get_label() == "BB"


def test_empty_labels() -> None:
    doc = _six_page_doc()
    assert doc.get_page_labels() == []
    assert doc[0].get_label() == ""
    doc.set_page_labels([{"startpage": 0, "style": "D"}])
    doc.set_page_labels([])  # 空リストで削除
    assert doc.get_page_labels() == []
    assert doc[0].get_label() == ""


def test_set_labels_validation() -> None:
    doc = _six_page_doc()
    with pytest.raises(ValueError, match="startpage 0"):
        doc.set_page_labels([{"startpage": 2, "style": "D"}])
    with pytest.raises(ValueError, match="style"):
        doc.set_page_labels([{"startpage": 0, "style": "X"}])
    with pytest.raises(ValueError, match="firstpagenum"):
        doc.set_page_labels([{"startpage": 0, "style": "D", "firstpagenum": 0}])
    with pytest.raises(ValueError, match="重複"):
        doc.set_page_labels([{"startpage": 0}, {"startpage": 0, "style": "D"}])
