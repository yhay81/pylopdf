"""Tests for page insertion, ranges, new_page, and copy_page."""

from __future__ import annotations

import pytest
from conftest import build_raw_pdf, png_size

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


def test_insert_pdf_negative_range_follows_pymupdf(three_page_pdf: bytes) -> None:
    # A negative from_page means the first page and a negative to_page the last.
    doc = pylopdf.Document()
    doc.insert_pdf(pylopdf.Document(stream=three_page_pdf), from_page=-2, to_page=-1)
    assert doc.page_count == 3
    assert "Page one" in doc.get_page_text(0)
    # Both ends clamp to the last source page; a descending range reverses.
    clamped = pylopdf.Document()
    clamped.insert_pdf(pylopdf.Document(stream=three_page_pdf), from_page=99, to_page=1)
    assert [clamped.get_page_text(index).strip() for index in range(2)] == ["Page three", "Page two"]


def test_insert_pdf_start_at(three_page_pdf: bytes, one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    src = pylopdf.Document(stream=one_page_pdf)
    doc.insert_pdf(src, start_at=1)
    assert doc.page_count == 4
    assert "Page one" in doc.get_page_text(0)
    assert "Hello PDF" in doc.get_page_text(1)
    assert "Page two" in doc.get_page_text(2)
    # Preserve page order after save and reload.
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert "Hello PDF" in reloaded.get_page_text(1)


def test_insert_pdf_start_at_zero_prepends(three_page_pdf: bytes, one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.insert_pdf(pylopdf.Document(stream=one_page_pdf), start_at=0)
    assert "Hello PDF" in doc.get_page_text(0)
    assert "Page one" in doc.get_page_text(1)


def test_insert_pdf_start_at_beyond_the_end_appends(three_page_pdf: bytes, one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.insert_pdf(pylopdf.Document(stream=one_page_pdf), start_at=40)
    assert doc.page_count == 4
    assert "Hello PDF" in doc.get_page_text(3)


def test_insert_pdf_empty_source_noop(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.insert_pdf(pylopdf.Document())
    assert doc.page_count == 3


def test_insert_pdf_does_not_keep_unreachable_source_data() -> None:
    """Do not copy unreachable attachments into the destination."""
    secret = b"SECRET-UNREFERENCED-ATTACHMENT-7c3f"
    source = pylopdf.Document()
    source.new_page(width=100, height=100)
    source.attachments.add("secret.txt", secret)

    target = pylopdf.Document()
    target.insert_pdf(source)

    assert list(target.attachments) == []
    assert secret not in target.tobytes()


def test_new_page_appends_blank(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    page = doc.new_page()
    assert doc.page_count == 2
    assert page.number == 1
    assert page.mediabox == pylopdf.Rect(0.0, 0.0, 595.0, 842.0)
    assert page.get_text() == ""
    assert png_size(doc.get_page_pixmap(1, alpha=True).tobytes()) == (595, 842)


def test_new_page_preserves_indirect_root_kids() -> None:
    doc = pylopdf.open(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids 4 0 R /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: "[3 0 R]",
            }
        )
    )

    doc.new_page(width=200, height=300)

    assert doc.page_count == 2
    assert doc[0].rect.width == pytest.approx(100)
    assert doc[1].rect == pylopdf.Rect(0, 0, 200, 300)
    assert pylopdf.open(stream=doc.tobytes()).page_count == 2

    doc.delete_page(1)

    assert doc.page_count == 1
    assert doc[0].rect.width == pytest.approx(100)
    assert pylopdf.open(stream=doc.tobytes()).page_count == 1


def test_new_page_insert_position_and_size(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    page = doc.new_page(1, width=300, height=400)
    assert page.number == 1
    assert doc.page_count == 4
    assert doc.get_page_text(1) == ""
    assert "Page two" in doc.get_page_text(2)
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.page_count == 4
    assert png_size(reloaded.get_page_pixmap(1, alpha=True).tobytes()) == (300, 400)


def test_new_page_invalid_size(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="width"):
        doc.new_page(width=0)
    with pytest.raises(ValueError, match="PDF real-number"):
        doc.new_page(width=1e39)


def test_copy_page_append_and_position(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.fullcopy_page(0)
    assert doc.page_count == 4
    assert "Page one" in doc.get_page_text(3)
    doc.fullcopy_page(2, to=0)  # Copy the current Page three to the front.
    assert doc.page_count == 5
    assert "Page three" in doc.get_page_text(0)
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.page_count == 5
    assert "Page three" in reloaded.get_page_text(0)
    assert reloaded.get_page_pixmap(0, alpha=True).tobytes() == reloaded.get_page_pixmap(3, alpha=True).tobytes()


def test_structure_ops_invalidate_pages(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    page = doc[0]
    doc.new_page()
    with pytest.raises(pylopdf.StalePageError):
        _ = page.mediabox


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("delete_page", (0.5,)),
        ("delete_pages", ([0.5],)),
        ("select", ([0.5],)),
        ("new_page", (0.5,)),
        ("fullcopy_page", (0, 0.5)),
    ],
)
def test_native_argument_refusal_keeps_existing_pages(
    three_page_pdf: bytes,
    operation: str,
    arguments: tuple[object, ...],
) -> None:
    doc = pylopdf.open(stream=three_page_pdf)
    page = doc[0]
    before = doc.tobytes()

    with pytest.raises(TypeError):
        getattr(doc, operation)(*arguments)

    assert doc.tobytes() == before
    assert "Page one" in page.get_text()


def test_insert_pdf_native_argument_refusal_keeps_existing_pages(
    three_page_pdf: bytes,
    one_page_pdf: bytes,
) -> None:
    doc = pylopdf.open(stream=three_page_pdf)
    source = pylopdf.open(stream=one_page_pdf)
    page = doc[0]
    before = doc.tobytes()

    with pytest.raises(TypeError):
        doc.insert_pdf(source, start_at=0.5)  # ty: ignore[invalid-argument-type]

    assert doc.tobytes() == before
    assert "Page one" in page.get_text()


def test_new_page_native_size_refusal_keeps_existing_pages(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    page = doc[0]
    before = doc.tobytes()

    with pytest.raises(pylopdf.PdfError, match="positive finite"):
        doc.new_page(width=1e-50)

    assert doc.tobytes() == before
    assert "Hello PDF" in page.get_text()


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
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
                3: f"<< /Type /Page /Parent 2 0 R {source_attributes} >>",
            }
        )
    )
    expected_crop = source[0].cropbox
    expected_rotation = source[0].rotation
    expected_png = source.get_page_pixmap(0, alpha=True).tobytes()
    source_bytes = source.tobytes()
    target = pylopdf.open(stream=_build_cropped_parent_pdf())
    original_png = target.get_page_pixmap(0, alpha=True).tobytes()

    target.insert_pdf(source)

    assert target[1].cropbox == expected_crop
    assert target[1].rotation == expected_rotation
    assert target.get_page_pixmap(1, alpha=True).tobytes() == expected_png
    assert target.get_page_pixmap(0, alpha=True).tobytes() == original_png
    assert source.tobytes() == source_bytes
    reopened = pylopdf.open(stream=target.tobytes())
    assert reopened[1].cropbox == expected_crop
    assert reopened[1].rotation == expected_rotation
    assert reopened.get_page_pixmap(1, alpha=True).tobytes() == expected_png


def test_new_page_uses_requested_geometry_under_cropped_rotated_parent() -> None:
    target = pylopdf.open(stream=_build_cropped_parent_pdf())
    original_png = target.get_page_pixmap(0, alpha=True).tobytes()

    page = target.new_page(width=612, height=792)

    assert page.rotation == 0
    assert page.mediabox == page.cropbox == pylopdf.Rect(0, 0, 612, 792)
    assert png_size(page.get_pixmap(alpha=True).tobytes()) == (612, 792)
    assert target.get_page_pixmap(0, alpha=True).tobytes() == original_png
    reopened = pylopdf.open(stream=target.tobytes())
    assert reopened[1].rotation == 0
    assert png_size(reopened.get_page_pixmap(1, alpha=True).tobytes()) == (612, 792)


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
        stream=build_raw_pdf(
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
    expected_crop = pylopdf.Rect(0, 392, 300, 792) if inherited_values else pylopdf.Rect(0, 0, 612, 792)
    expected_rotation = 180 if inherited_values else 0
    assert source[0].mediabox == pylopdf.Rect(0, 0, 612, 792)
    assert source[0].cropbox == expected_crop
    assert source[0].rotation == expected_rotation
    expected_png = source.get_page_pixmap(0, alpha=True).tobytes()
    source_bytes = source.tobytes()
    target = pylopdf.open(stream=_build_cropped_parent_pdf())

    target.insert_pdf(source)

    for current in (target, pylopdf.open(stream=target.tobytes())):
        assert current[1].mediabox == pylopdf.Rect(0, 0, 612, 792)
        assert current[1].cropbox == expected_crop
        assert current[1].rotation == expected_rotation
        assert current.get_page_pixmap(1, alpha=True).tobytes() == expected_png
    assert source.tobytes() == source_bytes


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
        stream=build_raw_pdf(
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
    # pymupdf's cropbox flips the stored [0 0 300 400] against the MediaBox top.
    geometry = (pylopdf.Rect(0, 0, 612, 792), pylopdf.Rect(0, 392, 300, 792), 90)
    assert (doc[0].mediabox, doc[0].cropbox, doc[0].rotation) == geometry
    expected_png = doc.get_page_pixmap(0, alpha=True).tobytes()

    index = 0
    if operation == "select":
        doc.select([0])
    elif operation == "delete_pages":
        doc.delete_pages([1])
    elif operation == "copy_page":
        doc.fullcopy_page(0)
        index = 2
    else:
        doc[0].insert_text((20, 30), "Drawn")

    for current in (doc, pylopdf.open(stream=doc.tobytes())):
        page = current[index]
        assert (page.mediabox, page.cropbox, page.rotation) == geometry
        if operation == "insert_text":
            assert current.get_page_text(index).split() == ["Drawn", "MISSING-REFERENCES"]
        else:
            assert current.get_page_pixmap(index, alpha=True).tobytes() == expected_png


@pytest.mark.parametrize("operation", ["new_page", "insert_pdf", "fullcopy_page"])
def test_add_page_refuses_existing_parent_cycle_before_mutation(operation: str) -> None:
    target = pylopdf.open(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: "<< /Type /Page /Parent 5 0 R /MediaBox [0 0 100 100] >>",
                5: "<< /Type /Pages /Parent 5 0 R >>",
            }
        )
    )
    existing = target[0]
    before = target.tobytes()
    source = pylopdf.Document()
    source.new_page()
    source[0].insert_text((10, 30), "IMPORTED-DATA-MUST-NOT-REMAIN")
    arguments = (source,) if operation == "insert_pdf" else (0,) if operation == "fullcopy_page" else ()

    with pytest.raises(pylopdf.PdfError, match="inheritance tree contains a reference cycle"):
        getattr(target, operation)(*arguments)

    assert target.tobytes() == before
    assert b"IMPORTED-DATA-MUST-NOT-REMAIN" not in target.tobytes()
    assert existing.number == 0
    assert existing.mediabox == pylopdf.Rect(0, 0, 100, 100)
    assert target.page_count == 2
