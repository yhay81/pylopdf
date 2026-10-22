"""Tests for ``Document.page_labels`` and ``Page.label``."""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf
from pylopdf import PageLabelRange, PageLabelStyle


def _six_page_doc() -> pylopdf.Document:
    return pylopdf.open(build_pdf([f"Page {i}" for i in range(6)]))


def _labels(doc: pylopdf.Document) -> list[str]:
    return [page.label for page in doc.pages]


def test_assign_and_compute_labels() -> None:
    doc = _six_page_doc()
    doc.page_labels = [PageLabelRange(0, PageLabelStyle.LOWER_ROMAN), PageLabelRange(3, prefix="A-")]
    assert _labels(doc) == ["i", "ii", "iii", "A-1", "A-2", "A-3"]
    assert doc.pages.labeled("A-2") == [4]


def test_labels_roundtrip_through_save() -> None:
    doc = _six_page_doc()
    ranges = [
        PageLabelRange(0, PageLabelStyle.UPPER_ROMAN, first=5),
        PageLabelRange(2, PageLabelStyle.LOWER_LETTERS, "付-"),
    ]
    doc.page_labels = reversed(ranges)  # Ranges are written in page order.
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.page_labels == ranges
    assert _labels(reopened)[:3] == ["V", "VI", "付-a"]


def test_styles_take_members_or_their_values() -> None:
    assert PageLabelRange(0, "r") == PageLabelRange(0, PageLabelStyle.LOWER_ROMAN)
    assert PageLabelRange(0).style is PageLabelStyle.DECIMAL
    assert repr(PageLabelRange(2, None, "x")) == "PageLabelRange(start=2, style=None, prefix='x', first=1)"
    with pytest.raises(ValueError, match="style"):
        PageLabelRange(0, "X")


def test_prefix_only_style() -> None:
    doc = _six_page_doc()
    doc.page_labels = [PageLabelRange(0, None, "表紙")]
    assert doc.page_labels == [PageLabelRange(0, None, "表紙")]
    assert doc.pages[0].label == "表紙"
    assert doc.pages[5].label == "表紙"


def test_letters_style_wraps_past_z() -> None:
    doc = _six_page_doc()
    doc.page_labels = [PageLabelRange(0, PageLabelStyle.UPPER_LETTERS, first=26)]
    assert _labels(doc)[:3] == ["Z", "AA", "BB"]


def test_absent_and_removed_labels() -> None:
    doc = _six_page_doc()
    assert doc.page_labels == []
    assert doc.pages[0].label == ""
    doc.page_labels = [PageLabelRange(0)]
    doc.page_labels = []  # An empty list removes labels.
    assert doc.page_labels == []
    doc.page_labels = [PageLabelRange(0)]
    del doc.page_labels
    assert doc.page_labels == []
    assert doc.pages[0].label == ""
    del doc.page_labels  # Removing absent labels changes nothing.


@pytest.mark.parametrize(
    ("make", "error", "match"),
    [
        (lambda: PageLabelRange(-1), ValueError, "start"),
        (lambda: PageLabelRange(0, first=0), ValueError, "first"),
        (lambda: PageLabelRange(True), TypeError, "start"),
        (lambda: PageLabelRange(0, prefix=5), TypeError, "prefix"),  # ty: ignore[invalid-argument-type]
    ],
)
def test_ranges_validate_on_construction(make: object, error: type[Exception], match: str) -> None:
    with pytest.raises(error, match=match):
        make()  # ty: ignore[call-non-callable]


def test_assigning_validates_the_ranges_together() -> None:
    doc = _six_page_doc()
    with pytest.raises(ValueError, match="page 0"):
        doc.page_labels = [PageLabelRange(2)]
    with pytest.raises(ValueError, match="distinct"):
        doc.page_labels = [PageLabelRange(0, None), PageLabelRange(0)]
    with pytest.raises(TypeError, match="PageLabelRange"):
        doc.page_labels = [{"startpage": 0}]  # ty: ignore[invalid-assignment]
    assert doc.page_labels == []


def test_removed_page_label_names_raise_migration_hints() -> None:
    doc = _six_page_doc()
    with pytest.raises(AttributeError, match=r"doc\.page_labels"):
        doc.get_page_labels()  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match=r"doc\.page_labels"):
        doc.set_page_labels([])  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match=r"page\.label"):
        doc.pages[0].get_label()  # ty: ignore[unresolved-attribute]


def test_page_label_tree_reference_cycle_is_visited_once() -> None:
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /PageLabels 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Nums [0 << /S /D /P (A-) >>] /Kids [4 0 R] >>",
        }
    )
    doc = pylopdf.open(pdf)
    assert doc.page_labels == [PageLabelRange(0, PageLabelStyle.DECIMAL, "A-")]
    assert doc.pages[0].label == "A-1"


@pytest.mark.parametrize("source", ["raw_pdf", "catalog"])
def test_unsupported_stored_style_reads_as_prefix_only(source: str) -> None:
    if source == "raw_pdf":
        doc = pylopdf.open(
            build_raw_pdf(
                {
                    1: "<< /Type /Catalog /Pages 2 0 R /PageLabels 4 0 R >>",
                    2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                    3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                    4: "<< /Nums [0 << /S /Unknown /P (Appendix-) >>] >>",
                }
            )
        )
    else:
        doc = _six_page_doc()
        doc.catalog["PageLabels"] = {"Nums": [0, {"S": pylopdf.Name("Unknown"), "P": "Appendix-"}]}

    expected = [PageLabelRange(0, None, "Appendix-")]
    assert doc.page_labels == expected
    assert _labels(doc) == ["Appendix-"] * len(doc.pages)
    assert doc.pages.labeled("Appendix-") == list(range(len(doc.pages)))
    assert doc.catalog["PageLabels"]["Nums"][1]["S"] == pylopdf.Name("Unknown")

    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.page_labels == expected
    assert reopened.pages[0].label == "Appendix-"
    reopened.page_labels = reopened.page_labels
    assert "S" not in reopened.catalog["PageLabels"]["Nums"][1]
    normalized = pylopdf.open(reopened.to_bytes())
    assert normalized.page_labels == expected
    assert normalized.pages[0].label == "Appendix-"


def test_page_label_tree_rejects_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /PageLabels 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    }
    for object_id in range(4, 36):
        objects[object_id] = f"<< /Kids [{object_id + 1} 0 R] >>"
    objects[36] = "<< /Nums [0 << /S /D >>] >>"

    doc = pylopdf.open(build_raw_pdf(objects))
    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        _ = doc.page_labels
    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        _ = doc.pages[0].label


def test_page_label_tree_rejects_excessive_edges_without_partial_output() -> None:
    kids = "5 0 R " * 4097
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /PageLabels 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: f"<< /Nums [0 << /S /D >>] /Kids [{kids}] >>",
            5: "<< /Nums [1 << /S /D >>] >>",
        }
    )
    doc = pylopdf.open(pdf)
    with pytest.raises(pylopdf.PdfError, match="4096-edge safety limit"):
        _ = doc.page_labels


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
    doc = pylopdf.open(pdf)
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
        _ = doc.page_labels


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
    doc = pylopdf.open(pdf)
    with pytest.raises(pylopdf.PdfError, match="1048576-byte safety limit"):
        _ = doc.page_labels


def test_assigning_refuses_over_limit_ranges_atomically() -> None:
    doc = _six_page_doc()
    before = doc.to_bytes()

    with pytest.raises(ValueError, match="4096 ranges"):
        doc.page_labels = [PageLabelRange(index) for index in range(4097)]
    assert doc.to_bytes() == before

    with pytest.raises(pylopdf.LimitError, match="1048576-byte") as caught:
        doc.page_labels = [PageLabelRange(0, prefix="x" * (1024 * 1024 + 1))]
    assert caught.value.code == "page_label_input_size"
    assert doc.to_bytes() == before
