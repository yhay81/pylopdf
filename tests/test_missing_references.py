"""Read references to missing objects the way readers read null.

ISO 32000-2 §7.3.7 treats a null dictionary value as an omitted key, and
§7.3.10 reads a reference to a missing object as null. Loading nulls only
references above every object, so a reference to a free number below the
highest object still reaches readers. Each case here behaves the same for a
null value, an indirect null, and such a missing object.
"""

from __future__ import annotations

import re

import pytest
from conftest import build_pdf, outline_entries, outline_tuples

import pylopdf

FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

# Object 6 is an indirect null. Number 7 is a free cross-reference entry below
# the highest object, 8, which the catalog keeps reachable so that saving and
# reloading preserve the gap.
NULLS = {"null": "null", "indirect-null": "6 0 R", "missing": "7 0 R"}
null_values = pytest.mark.parametrize("value", list(NULLS.values()), ids=list(NULLS))


def _stream(content: str) -> str:
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"


def _text(text: str, y: int = 100) -> str:
    return _stream(f"BT /F1 20 Tf 20 {y} Td ({text}) Tj ET")


def _pdf(  # noqa: PLR0913 - each keyword places one fixture value.
    *,
    contents: str = "5 0 R",
    parent: str = "2 0 R",
    page: str = "",
    pages: str = "",
    catalog: str = "",
    trailer: str = "",
    extra: dict[int, str] | None = None,
) -> bytes:
    """Build a one-page PDF whose cross-reference table frees number 7."""
    objects = {
        1: f"<< /Type /Catalog /Pages 2 0 R /PylopdfFiller 8 0 R {catalog} >>",
        2: (
            "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 4 0 R >> >> {pages} >>"
        ),
        3: f"<< /Type /Page /Parent {parent} /Contents {contents} {page} >>",
        4: FONT,
        5: _text("TARGET PAGE"),
        6: "null",
        8: "<< >>",
        **(extra or {}),
    }
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n{objects[number]}\nendobj\n".encode("latin-1")
    size = max(objects) + 1
    xref_pos = len(out)
    out += f"xref\n0 {size}\n".encode()
    for number in range(size):
        out += f"{offsets[number]:010d} 00000 n \n".encode() if number in offsets else b"0000000000 65535 f \n"
    out += f"trailer\n<< /Size {size} /Root 1 0 R {trailer} >>\nstartxref\n{xref_pos}\n%%EOF".encode()
    return bytes(out)


def _with_reloaded(doc: pylopdf.Document) -> tuple[pylopdf.Document, pylopdf.Document]:
    return doc, pylopdf.open(doc.to_bytes())


def test_missing_number_below_the_highest_object_stays_a_reference() -> None:
    """Only numbers above every object load as null, so 7 reaches readers."""
    saved = pylopdf.open(_pdf(page="/Annots 7 0 R")).to_bytes()

    assert re.search(rb"/Annots\s*7 0 R", saved)


@null_values
def test_annotations_start_a_new_array_for_a_null_annots_entry(value: str) -> None:
    """Adding an annotation raised `PdfError` for the indirect null and missing array."""
    doc = pylopdf.open(_pdf(page=f"/Annots {value}"))
    assert doc.pages[0].annots() == []

    doc.pages[0].links.add((10, 10, 100, 30), "https://example.com/")
    doc.pages[0].add_highlight_annot([(10, 40, 100, 60)])

    for current in _with_reloaded(doc):
        assert [annot.type[1] for annot in current.pages[0].annots()] == ["Link", "Highlight"]
        assert [link.target for link in current.pages[0].links] == ["https://example.com/"]


@null_values
def test_metadata_creates_an_info_dictionary_for_a_null_info_entry(value: str) -> None:
    """Setting metadata raised `PdfError` for the indirect null and missing `/Info`."""
    doc = pylopdf.open(_pdf(trailer=f"/Info {value}"))
    assert "title" not in doc.metadata

    doc.metadata.update({"title": "Edited"})

    for current in _with_reloaded(doc):
        assert current.metadata["title"] == "Edited"


@null_values
def test_page_with_a_null_parent_reads_and_edits_its_own_attributes(value: str) -> None:
    """Geometry readers and every edit raised `PdfError` for the unresolvable parent."""
    doc = pylopdf.open(
        _pdf(
            parent=value,
            page="/MediaBox [0 0 300 400] /Rotate 90 /Resources << /Font << /F1 4 0 R >> >>",
        )
    )

    assert doc.pages[0].boxes.media == pylopdf.Rect(0, 0, 300, 400)
    assert doc.pages[0].rotation == 90
    doc.pages[0].insert_text((20, 40), "STAMP")
    doc.pages.extend(doc.pages[0])

    for current in _with_reloaded(doc):
        assert len(current.pages) == 2
        assert [page.boxes.media for page in current.pages] == [pylopdf.Rect(0, 0, 300, 400)] * 2
        assert [page.rotation for page in current.pages] == [90, 90]
        assert "STAMP" in current.pages[1].text().splitlines()


@null_values
def test_inheritance_keeps_attributes_below_a_null_parent(value: str) -> None:
    """The page tree root names the null parent; its own attributes still apply."""
    doc = pylopdf.open(_pdf(pages=f"/Parent {value} /Rotate 90"))

    assert doc.pages[0].boxes.media == pylopdf.Rect(0, 0, 612, 792)
    assert doc.pages[0].rotation == 90
    doc.pages[0].insert_text((20, 300), "STAMP")
    doc.pages.select([0, 0])

    for current in _with_reloaded(doc):
        assert [page.rotation for page in current.pages] == [90, 90]
        for number in range(2):
            assert sorted(current.pages[number].text().splitlines()) == ["STAMP", "TARGET PAGE"]


@null_values
def test_text_insertion_starts_a_null_font_category(value: str) -> None:
    """Registering a font raised `PdfError` for every null-equivalent category."""
    doc = pylopdf.open(_pdf(page=f"/Resources << /Font {value} >>", extra={5: _stream("0 0 m 10 10 l S")}))

    doc.pages[0].insert_text((20, 300), "STAMP")

    for current in _with_reloaded(doc):
        assert current.pages[0].text().splitlines() == ["STAMP"]


@null_values
def test_page_import_starts_a_null_xobject_category(value: str) -> None:
    """Registering a Form XObject raised `PdfError` for every null-equivalent category."""
    doc = pylopdf.open(_pdf(page=f"/Resources << /Font << /F1 4 0 R >> /XObject {value} >>"))

    doc.pages[0].show_pdf_page((0, 300, 612, 792), pylopdf.open(build_pdf(["SOURCE"])), 0)

    for current in _with_reloaded(doc):
        assert sorted(current.pages[0].text().splitlines()) == ["SOURCE", "TARGET PAGE"]


@null_values
def test_toc_reads_a_null_outlines_entry_as_absent(value: str) -> None:
    """Reading the outline raised `PdfError` for every null-equivalent outline root."""
    doc = pylopdf.open(_pdf(catalog=f"/Outlines {value}"))
    assert outline_tuples(doc) == []

    doc.outline = outline_entries([(1, "Start", 0)])

    for current in _with_reloaded(doc):
        assert outline_tuples(current) == [(1, "Start", 0)]


@null_values
@pytest.mark.parametrize("layout", ["array", "single"])
def test_drawing_after_a_null_content_entry_stays_visible(value: str, layout: str) -> None:
    """Drawing dropped null content entries but kept the indirect null and missing ones.

    Renderers stop at the first content entry that is not a stream, so a kept
    entry hid the closing isolation stream and every drawing appended after it.
    """
    contents = f"[5 0 R {value}]" if layout == "array" else value
    expected = ["STAMP", "TARGET PAGE"] if layout == "array" else ["STAMP"]
    doc = pylopdf.open(_pdf(contents=contents))

    doc.pages[0].insert_text((20, 300), "STAMP")

    for current in _with_reloaded(doc):
        assert current.pages[0].text().splitlines() == expected


@null_values
def test_attachments_skip_a_null_file_specification(value: str) -> None:
    """The walker listed the indirect null and missing FileSpecs that it skips as null."""
    doc = pylopdf.open(
        _pdf(
            catalog=f"/Names << /EmbeddedFiles << /Names [(a.txt) {value} (b.txt) 9 0 R] >> >>",
            extra={
                9: "<< /Type /Filespec /F (b.txt) /EF << /F 10 0 R >> >>",
                10: "<< /Type /EmbeddedFile /Length 1 >>\nstream\nB\nendstream",
            },
        )
    )
    assert list(doc.attachments) == ["b.txt"]
    with pytest.raises(KeyError):
        doc.attachments["a.txt"]

    doc.attachments.add("c.txt", b"C")

    for current in _with_reloaded(doc):
        assert list(current.attachments) == ["b.txt", "c.txt"]
        assert current.attachments["b.txt"].read() == b"B"


@null_values
def test_attachment_add_replaces_a_null_names_entry(value: str) -> None:
    """Adding an attachment raised `PdfError` for every null-equivalent `/Names`."""
    doc = pylopdf.open(_pdf(catalog=f"/Names {value}"))
    assert list(doc.attachments) == []

    doc.attachments.add("note.txt", b"note")

    for current in _with_reloaded(doc):
        assert list(current.attachments) == ["note.txt"]
        assert current.attachments["note.txt"].read() == b"note"
