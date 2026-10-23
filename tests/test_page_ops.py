"""Tests for page insertion, ranges, new_page, and copy_page."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import build_raw_pdf, png_size

import pylopdf

if TYPE_CHECKING:
    from collections.abc import Callable


def test_insert_pdf_negative_range_follows_pymupdf(three_page_pdf: bytes) -> None:
    # A negative from_page means the first page and a negative to_page the last.
    doc = pylopdf.Document()
    doc.pages.extend(pylopdf.open(three_page_pdf).pages)
    assert len(doc.pages) == 3
    assert "Page one" in doc.pages[0].text()
    # Both ends clamp to the last source page; a descending range reverses.
    clamped = pylopdf.Document()
    clamped.pages.extend(pylopdf.open(three_page_pdf).pages[1:100][::-1])
    assert [clamped.pages[index].text().strip() for index in range(2)] == ["Page three", "Page two"]


def test_insert_pdf_start_at(three_page_pdf: bytes, one_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    src = pylopdf.open(one_page_pdf)
    doc.pages.insert(1, src.pages)
    assert len(doc.pages) == 4
    assert "Page one" in doc.pages[0].text()
    assert "Hello PDF" in doc.pages[1].text()
    assert "Page two" in doc.pages[2].text()
    # Preserve page order after save and reload.
    reloaded = pylopdf.open(doc.to_bytes())
    assert "Hello PDF" in reloaded.pages[1].text()


def test_insert_pdf_empty_source_noop(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.pages.extend(pylopdf.Document().pages)
    assert len(doc.pages) == 3


def test_insert_pdf_does_not_keep_unreachable_source_data() -> None:
    """Do not copy unreachable attachments into the destination."""
    secret = b"SECRET-UNREFERENCED-ATTACHMENT-7c3f"
    source = pylopdf.Document()
    source.pages.new((100, 100))
    source.attachments.add("secret.txt", secret)

    target = pylopdf.Document()
    target.pages.extend(source.pages)

    assert list(target.attachments) == []
    assert secret not in target.to_bytes()


def test_new_page_appends_blank(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    page = doc.pages.new()
    assert len(doc.pages) == 2
    assert page.index == 1
    assert page.boxes.media == pylopdf.Rect(0.0, 0.0, 595.0, 842.0)
    assert page.text() == ""
    assert png_size(doc.pages[1].pixmap(alpha=True).to_bytes()) == (595, 842)


def test_new_page_preserves_indirect_root_kids() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids 4 0 R /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: "[3 0 R]",
            }
        )
    )

    doc.pages.new((200, 300))

    assert len(doc.pages) == 2
    assert doc.pages[0].rect.width == pytest.approx(100)
    assert doc.pages[1].rect == pylopdf.Rect(0, 0, 200, 300)
    assert len(pylopdf.open(doc.to_bytes()).pages) == 2

    del doc.pages[1]

    assert len(doc.pages) == 1
    assert doc.pages[0].rect.width == pytest.approx(100)
    assert len(pylopdf.open(doc.to_bytes()).pages) == 1


def test_new_page_insert_position_and_size(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    page = doc.pages.new((300, 400), index=1)
    assert page.index == 1
    assert len(doc.pages) == 4
    assert doc.pages[1].text() == ""
    assert "Page two" in doc.pages[2].text()
    reloaded = pylopdf.open(doc.to_bytes())
    assert len(reloaded.pages) == 4
    assert png_size(reloaded.pages[1].pixmap(alpha=True).to_bytes()) == (300, 400)


def test_new_page_invalid_size(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match="size"):
        doc.pages.new((0, 842))
    with pytest.raises(ValueError, match="real-number range"):
        doc.pages.new((1e39, 842))


def test_copy_page_append_and_position(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.pages.extend(doc.pages[0])
    assert len(doc.pages) == 4
    assert "Page one" in doc.pages[3].text()
    doc.pages.insert(0, doc.pages[2])  # Copy the current Page three to the front.
    assert len(doc.pages) == 5
    assert "Page three" in doc.pages[0].text()
    reloaded = pylopdf.open(doc.to_bytes())
    assert len(reloaded.pages) == 5
    assert "Page three" in reloaded.pages[0].text()
    assert reloaded.pages[0].pixmap(alpha=True).to_bytes() == reloaded.pages[3].pixmap(alpha=True).to_bytes()


def _delete(pages: pylopdf.Pages, index: object) -> None:
    del pages[index]  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "operation",
    [
        lambda pages: _delete(pages, 0.5),
        lambda pages: _delete(pages, slice(0.5, None)),
        lambda pages: pages.select([0.5]),
        lambda pages: pages.new(index=0.5),
        lambda pages: pages.insert(0.5, pages[0]),
        lambda pages: pages.move(0, 0.5),
    ],
    ids=["del", "del_slice", "select", "new", "insert", "move"],
)
def test_native_argument_refusal_keeps_existing_pages(
    three_page_pdf: bytes,
    operation: Callable[[pylopdf.Pages], object],
) -> None:
    doc = pylopdf.open(three_page_pdf)
    page = doc.pages[0]
    before = doc.to_bytes()

    with pytest.raises(TypeError):
        operation(doc.pages)

    assert doc.to_bytes() == before
    assert "Page one" in page.text()


def test_insert_pdf_native_argument_refusal_keeps_existing_pages(
    three_page_pdf: bytes,
    one_page_pdf: bytes,
) -> None:
    doc = pylopdf.open(three_page_pdf)
    source = pylopdf.open(one_page_pdf)
    page = doc.pages[0]
    before = doc.to_bytes()

    with pytest.raises(TypeError):
        doc.pages.insert(0.5, source.pages)  # ty: ignore[invalid-argument-type]

    assert doc.to_bytes() == before
    assert "Page one" in page.text()


def test_new_page_native_size_refusal_keeps_existing_pages(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    page = doc.pages[0]
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="positive finite"):
        doc.pages.new((1e-50, 842))

    assert doc.to_bytes() == before
    assert "Hello PDF" in page.text()


def _build_cropped_parent_pdf() -> bytes:
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: ("<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] /CropBox [0 0 80 100] /Rotate 90 >>"),
            3: "<< /Type /Page /Parent 2 0 R >>",
        }
    )


@pytest.mark.parametrize("source_attributes", ["", "/CropBox [5 7 305 407] /Rotate 180"])
def test_insert_pdf_preserves_geometry_under_cropped_rotated_parent(source_attributes: str) -> None:
    source = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
                3: f"<< /Type /Page /Parent 2 0 R {source_attributes} >>",
            }
        )
    )
    expected_crop = source.pages[0].boxes.crop
    expected_rotation = source.pages[0].rotation
    expected_png = source.pages[0].pixmap(alpha=True).to_bytes()
    source_bytes = source.to_bytes()
    target = pylopdf.open(_build_cropped_parent_pdf())
    original_png = target.pages[0].pixmap(alpha=True).to_bytes()

    target.pages.extend(source.pages)

    assert target.pages[1].boxes.crop == expected_crop
    assert target.pages[1].rotation == expected_rotation
    assert target.pages[1].pixmap(alpha=True).to_bytes() == expected_png
    assert target.pages[0].pixmap(alpha=True).to_bytes() == original_png
    assert source.to_bytes() == source_bytes
    reopened = pylopdf.open(target.to_bytes())
    assert reopened.pages[1].boxes.crop == expected_crop
    assert reopened.pages[1].rotation == expected_rotation
    assert reopened.pages[1].pixmap(alpha=True).to_bytes() == expected_png


def test_new_page_uses_requested_geometry_under_cropped_rotated_parent() -> None:
    target = pylopdf.open(_build_cropped_parent_pdf())
    original_png = target.pages[0].pixmap(alpha=True).to_bytes()

    page = target.pages.new((612, 792))

    assert page.rotation == 0
    assert page.boxes.media == page.boxes.crop == pylopdf.Rect(0, 0, 612, 792)
    assert png_size(page.pixmap(alpha=True).to_bytes()) == (612, 792)
    assert target.pages[0].pixmap(alpha=True).to_bytes() == original_png
    reopened = pylopdf.open(target.to_bytes())
    assert reopened.pages[1].rotation == 0
    assert png_size(reopened.pages[1].pixmap(alpha=True).to_bytes()) == (612, 792)


@pytest.mark.parametrize("null_value", ["null", "5 0 R", "99 0 R"], ids=["null", "indirect-null", "missing-object"])
@pytest.mark.parametrize("inherited_values", [False, True])
def test_insert_pdf_treats_null_page_and_parent_attributes_as_absent(
    *, null_value: str, inherited_values: bool
) -> None:
    """A null attribute is omitted, so the page inherits from its source ancestors.

    ISO 32000-2 §7.3.10 reads a reference to a missing object as null.
    """
    attributes = " ".join(f"/{key} {null_value}" for key in ("MediaBox", "CropBox", "Rotate", "Resources"))
    inherited = "/CropBox [0 0 300 400] /Rotate 180 /Resources << /Font << /F1 6 0 R >> >>" if inherited_values else ""
    content = "BT /F1 20 Tf 40 80 Td (NULL-INHERITANCE) Tj ET" if inherited_values else ""
    source = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: f"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] {inherited} >>",
                3: f"<< /Type /Pages /Parent 2 0 R /Kids [4 0 R] /Count 1 {attributes} >>",
                4: f"<< /Type /Page /Parent 3 0 R /Contents 7 0 R {attributes} >>",
                5: "null",
                6: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                7: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            }
        )
    )
    expected_crop = pylopdf.Rect(0, 0, 300, 400) if inherited_values else pylopdf.Rect(0, 0, 612, 792)
    expected_rotation = 180 if inherited_values else 0
    assert source.pages[0].boxes.media == pylopdf.Rect(0, 0, 612, 792)
    assert source.pages[0].boxes.crop == expected_crop
    assert source.pages[0].rotation == expected_rotation
    expected_png = source.pages[0].pixmap(alpha=True).to_bytes()
    source_bytes = source.to_bytes()
    target = pylopdf.open(_build_cropped_parent_pdf())

    target.pages.extend(source.pages)

    for current in (target, pylopdf.open(target.to_bytes())):
        assert current.pages[1].boxes.media == pylopdf.Rect(0, 0, 612, 792)
        assert current.pages[1].boxes.crop == expected_crop
        assert current.pages[1].rotation == expected_rotation
        assert current.pages[1].pixmap(alpha=True).to_bytes() == expected_png
    assert source.to_bytes() == source_bytes


@pytest.mark.parametrize("operation", ["select", "delete_pages", "copy_page", "insert_text"])
def test_page_edits_inherit_past_attributes_that_name_missing_objects(operation: str) -> None:
    """Same-document edits read a missing-object attribute as omitted, like imports.

    Flattening the page tree materializes the value that the page inherits from
    its original ancestors. Keeping the reference would make the page inherit
    from its new parent instead, and page readers and drawing used to raise.
    """
    attributes = " ".join(f"/{key} 99 0 R" for key in ("MediaBox", "CropBox", "Rotate", "Resources"))
    content = "BT /F1 20 Tf 40 80 Td (MISSING-REFERENCES) Tj ET"
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 /MediaBox [0 0 612 792] >>",
                3: "<< /Type /Pages /Parent 2 0 R /Kids [4 0 R] /Count 1 /CropBox [0 0 300 400] /Rotate 90 "
                "/Resources << /Font << /F1 5 0 R >> >> >>",
                4: f"<< /Type /Page /Parent 3 0 R /Contents 7 0 R {attributes} >>",
                5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                6: "<< /Type /Page /Parent 2 0 R >>",
                7: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            }
        )
    )
    geometry = (pylopdf.Rect(0, 0, 612, 792), pylopdf.Rect(0, 0, 300, 400), 90)
    assert (doc.pages[0].boxes.media, doc.pages[0].boxes.crop, doc.pages[0].rotation) == geometry
    expected_png = doc.pages[0].pixmap(alpha=True).to_bytes()

    index = 0
    if operation == "select":
        doc.pages.select([0])
    elif operation == "delete_pages":
        del doc.pages[1:2]
    elif operation == "copy_page":
        doc.pages.extend(doc.pages[0])
        index = 2
    else:
        with doc.pages[0].draw() as canvas:
            canvas.text("Drawn", (20, 30))

    for current in (doc, pylopdf.open(doc.to_bytes())):
        page = current.pages[index]
        assert (page.boxes.media, page.boxes.crop, page.rotation) == geometry
        if operation == "insert_text":
            assert current.pages[index].text().split() == ["Drawn", "MISSING-REFERENCES"]
        else:
            assert current.pages[index].pixmap(alpha=True).to_bytes() == expected_png


@pytest.mark.parametrize("operation", ["new", "import", "copy"])
def test_add_page_refuses_existing_parent_cycle_before_mutation(operation: str) -> None:
    target = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: "<< /Type /Page /Parent 5 0 R /MediaBox [0 0 100 100] >>",
                5: "<< /Type /Pages /Parent 5 0 R >>",
            }
        )
    )
    existing = target.pages[0]
    before = target.to_bytes()
    source = pylopdf.Document()
    source.pages.new()
    with source.pages[0].draw() as canvas:
        canvas.text("IMPORTED-DATA-MUST-NOT-REMAIN", (10, 30))
    actions: dict[str, Callable[[], object]] = {
        "new": target.pages.new,
        "import": lambda: target.pages.extend(source.pages),
        "copy": lambda: target.pages.extend(target.pages[0]),
    }

    with pytest.raises(pylopdf.PdfError, match="inheritance tree contains a reference cycle"):
        actions[operation]()

    assert target.to_bytes() == before
    assert b"IMPORTED-DATA-MUST-NOT-REMAIN" not in target.to_bytes()
    assert existing.index == 0
    assert existing.boxes.media == pylopdf.Rect(0, 0, 100, 100)
    assert len(target.pages) == 2
