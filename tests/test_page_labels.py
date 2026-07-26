"""Tests for page-label APIs on Document and Page."""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

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
    labels: list[pylopdf.PageLabelSpec] = [
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
    doc.set_page_labels([])  # An empty list removes labels.
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
    with pytest.raises(ValueError, match="unique"):
        doc.set_page_labels([{"startpage": 0}, {"startpage": 0, "style": "D"}])


def test_page_label_tree_reference_cycle_is_visited_once() -> None:
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /PageLabels 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Nums [0 << /S /D /P (A-) >>] /Kids [4 0 R] >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    assert doc.get_page_labels() == [{"startpage": 0, "style": "D", "prefix": "A-", "firstpagenum": 1}]
    assert doc[0].get_label() == "A-1"


def test_page_label_tree_rejects_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /PageLabels 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    }
    for object_id in range(4, 36):
        objects[object_id] = f"<< /Kids [{object_id + 1} 0 R] >>"
    objects[36] = "<< /Nums [0 << /S /D >>] >>"

    doc = pylopdf.open(stream=build_raw_pdf(objects))
    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        doc.get_page_labels()


def test_page_label_tree_rejects_excessive_entries_without_partial_output() -> None:
    pairs = " ".join(f"{index} << /S /D >>" for index in range(4097))
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /PageLabels 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: f"<< /Nums [{pairs}] >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
        doc.get_page_labels()


def test_page_label_tree_rejects_excessive_text_without_partial_output() -> None:
    prefix = "x" * (1024 * 1024 + 1)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /PageLabels 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: f"<< /Nums [0 << /P ({prefix}) >>] >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    with pytest.raises(pylopdf.PdfError, match="1048576-byte safety limit"):
        doc.get_page_labels()


def test_set_page_labels_refuses_to_create_over_limit_output_atomically() -> None:
    doc = _six_page_doc()
    before = doc.tobytes()

    with pytest.raises(ValueError, match="4096 ranges"):
        doc.set_page_labels([{"startpage": 0}] * 4097)
    assert doc.tobytes() == before

    with pytest.raises(pylopdf.PdfError, match="1048576-byte safety limit"):
        doc.set_page_labels([{"startpage": 0, "prefix": "x" * (1024 * 1024 + 1)}])
    assert doc.tobytes() == before
