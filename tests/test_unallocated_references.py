"""Keep references to unallocated object numbers from aliasing new objects.

New objects take numbers above the highest object a document has. Loading
turns every reference to a higher number into null, which is how ISO 32000-2
§7.3.10 reads it; a kept reference resolved to whichever object a later import
or edit received that number.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf, ocr_words, outline_entries

import pylopdf

FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
NOTO_SANS_JP = (
    Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
)


def _stream(text: str, y: int = 100) -> str:
    content = f"BT /F1 20 Tf 20 {y} Td ({text}) Tj ET"
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"


def _target_objects(contents: str = "5 0 R", *, catalog: str = "") -> dict[int, str | bytes]:
    """Return a five-object, one-page target drawing `contents`."""
    return {
        1: f"<< /Type /Catalog /Pages 2 0 R {catalog} >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> >>",
        3: f"<< /Type /Page /Parent 2 0 R /Contents {contents} >>",
        4: FONT,
        5: _stream("TARGET PAGE"),
    }


def _target(contents: str = "5 0 R", *, catalog: str = "") -> pylopdf.Document:
    return pylopdf.open(build_raw_pdf(_target_objects(contents, catalog=catalog)))


def _sparse_pdf(objects: dict[int, str | bytes], size: int) -> bytes:
    """Build an xref-table PDF whose unused numbers below `size` are free entries."""
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        value = objects[number]
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode()
        out += value.encode("latin-1") if isinstance(value, str) else value
        out += b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {size}\n".encode()
    for number in range(size):
        out += f"{offsets[number]:010d} 00000 n \n".encode() if number in offsets else b"0000000000 65535 f \n"
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
    return bytes(out)


def _with_reloaded(doc: pylopdf.Document) -> tuple[pylopdf.Document, pylopdf.Document]:
    return doc, pylopdf.open(doc.to_bytes())


def test_reference_above_max_id_cannot_draw_an_imported_page() -> None:
    """The import numbers the source's objects 6-10, so object 10 is its content stream."""
    doc = _target("[5 0 R 10 0 R]")

    doc.pages.extend(pylopdf.open(build_pdf(["SOURCE CONTENT"])).pages)

    for current in _with_reloaded(doc):
        assert current.pages[0].text().split() == ["TARGET", "PAGE"]
        assert current.pages[1].text().split() == ["SOURCE", "CONTENT"]


def test_reference_above_max_id_cannot_repeat_a_stamp() -> None:
    """One of the stamps' content streams used to take object number 10."""
    doc = _target("[5 0 R 10 0 R]")
    stamps = ["STAMP ONE", "STAMP TWO", "STAMP THREE"]

    for index, stamp in enumerate(stamps):
        doc.pages[0].insert_text((20, 300 + 100 * index), stamp)

    for current in _with_reloaded(doc):
        lines = Counter(current.pages[0].text().splitlines())
        assert lines == Counter([*stamps, "TARGET PAGE"])


_ALLOCATIONS: dict[str, Callable[[pylopdf.Document], object]] = {
    "insert_pdf": lambda doc: doc.pages.extend(pylopdf.open(build_pdf(["Source"])).pages),
    "show_pdf_page": lambda doc: doc.pages[0].show_pdf_page((0, 0, 100, 100), pylopdf.open(build_pdf(["Source"])), 0),
    "insert_text": lambda doc: doc.pages[0].insert_text((20, 40), "Stamp"),
    "insert_text_embedded_font": lambda doc: doc.pages[0].insert_text((20, 40), "Stamp", fontfile=NOTO_SANS_JP),
    "insert_image": lambda doc: doc.pages[0].insert_image(
        (10, 10, 50, 50), pixmap=doc.pages[0].pixmap(matrix=pylopdf.Matrix(0.05, 0.05), alpha=True)
    ),
    "add_highlight_annot": lambda doc: doc.pages[0].add_highlight_annot([(10, 10, 100, 30)]),
    "add_link_annot": lambda doc: doc.pages[0].links.add((10, 10, 100, 30), "https://example.com/"),
    "insert_ocr_text_layer": lambda doc: doc.pages[0].apply_ocr(
        words=ocr_words([(10, 10, 100, 30, "Word")]), skip_existing=False
    ),
    "new_page": lambda doc: doc.pages.new(),
    "copy_page": lambda doc: doc.pages.extend(doc.pages[0]),
    "outline": lambda doc: setattr(doc, "outline", outline_entries([(1, "Start", 0)])),
    "set_metadata": lambda doc: doc.metadata.update({"title": "Edited"}),
    "attachments.add": lambda doc: doc.attachments.add("note.txt", b"note"),
}


@pytest.mark.parametrize("operation", list(_ALLOCATIONS))
def test_every_allocation_leaves_a_reference_above_max_id_null(operation: str) -> None:
    """Object 6 is the first number any of these edits allocates."""
    doc = _target(catalog="/PylopdfProbe 6 0 R")

    _ALLOCATIONS[operation](doc)

    saved = doc.to_bytes()
    assert re.search(rb"/PylopdfProbe\s*null", saved)
    assert "TARGET PAGE" in pylopdf.open(saved).pages[0].text()


def test_only_numbers_above_every_object_become_null() -> None:
    """Free and wrong-generation numbers at or below the highest object stay.

    New objects are numbered only above the highest object, so lower numbers
    cannot alias. Trailing free entries do not count: the highest object here
    is 7, so the free entry 9 and every higher number would be allocated.
    """
    objects = _target_objects("[5 0 R 6 0 R 7 0 R 7 1 R 9 0 R 10 0 R 10 3 R 4294967295 65535 R]")
    objects[7] = _stream("SEVEN", 200)
    doc = pylopdf.open(_sparse_pdf(objects, 10))

    saved = doc.to_bytes()

    assert re.search(rb"/Contents\s*\[5 0 R 6 0 R 7 0 R 7 1 R null null null null\]", saved)
    assert re.search(rb"/Size 8\b", saved)


def test_object_above_every_cross_reference_entry_survives_new_objects() -> None:
    """The object that cross-reference entry 6 locates says it is object 9.

    lopdf stores it under its header number, and lopdf 0.45 raises `max_id`
    over it, so new objects start at 10; the unallocated 12 becomes null
    instead of naming one of the new stamps.
    """
    bodies = _target_objects("[5 0 R 9 0 R 12 0 R]")
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number, body in bodies.items():
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode()
        out += body.encode("latin-1") if isinstance(body, str) else body
        out += b"\nendobj\n"
    offsets[6] = len(out)
    out += f"9 0 obj\n{_stream('ABOVE MAX ID', 200)}\nendobj\n".encode()
    xref_pos = len(out)
    out += b"xref\n0 7\n0000000000 65535 f \n"
    for number in range(1, 7):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
    doc = pylopdf.open(bytes(out))
    stamps = [f"STAMP {index}" for index in range(4)]

    for index, stamp in enumerate(stamps):
        doc.pages[0].insert_text((20, 300 + 60 * index), stamp)

    for current in _with_reloaded(doc):
        lines = Counter(current.pages[0].text().splitlines())
        assert lines == Counter([*stamps, "ABOVE MAX ID", "TARGET PAGE"])


@pytest.mark.parametrize("unlock", ["password", "authenticate"])
def test_encrypted_document_nulls_references_above_max_id_once_decrypted(unlock: str) -> None:
    """Decryption reloads through the same boundary that nulls the reference.

    Indirect references are not encrypted, so an encrypted file is patched to
    name 9, above its seven objects, where the plaintext named object 6.
    """
    objects = _target_objects("[5 0 R 6 0 R]", catalog="/PylopdfProbe 6 0 R")
    objects[6] = _stream("PLACEHOLDER", 200)
    encrypted = pylopdf.open(build_raw_pdf(objects)).to_bytes(
        encryption=pylopdf.Encryption(user_password="user", owner_password="owner")
    )
    patched = encrypted.replace(b"/Contents[5 0 R 6 0 R]", b"/Contents[5 0 R 9 0 R]").replace(
        b"/PylopdfProbe 6 0 R", b"/PylopdfProbe 9 0 R"
    )
    assert patched.count(b" 9 0 R") == 2
    if unlock == "password":
        doc = pylopdf.open(patched, password="user")
    else:
        doc = pylopdf.open(patched)
        assert doc.needs_password
        assert doc.unlock("user") is pylopdf.Access.USER

    assert re.search(rb"/PylopdfProbe\s*null", doc.to_bytes())
    doc.pages.extend(pylopdf.open(build_pdf(["SOURCE CONTENT"])).pages)
    doc.pages[0].insert_text((20, 300), "STAMP")

    for current in _with_reloaded(doc):
        assert Counter(current.pages[0].text().splitlines()) == Counter(["STAMP", "TARGET PAGE"])


@pytest.mark.parametrize("contents", ["[5 0 R 90 0 R]", "90 0 R"], ids=["array-entry", "single-stream"])
def test_drawing_on_content_that_names_an_unallocated_object_stays_visible(contents: str) -> None:
    """Drawing drops null content entries but kept the unresolvable reference.

    Renderers stop at the first content entry that is not a stream, so the
    kept reference hid the isolation stream and every drawing appended after.
    """
    doc = _target(contents)

    doc.pages[0].insert_text((20, 300), "STAMP")

    for current in _with_reloaded(doc):
        assert "STAMP" in current.pages[0].text().splitlines()


def test_page_with_an_unallocated_parent_reads_and_edits_as_a_tree_root() -> None:
    """Geometry readers and drawing raised `PdfError` for the unresolvable parent."""
    objects = _target_objects()
    objects[3] = "<< /Type /Page /Parent 90 0 R /Contents 5 0 R /MediaBox [0 0 300 400] >>"
    doc = pylopdf.open(build_raw_pdf(objects))

    assert doc.pages[0].boxes.media == pylopdf.Rect(0, 0, 300, 400)
    doc.pages[0].insert_text((20, 40), "STAMP")

    for current in _with_reloaded(doc):
        assert current.pages[0].rect == pylopdf.Rect(0, 0, 300, 400)
        assert "STAMP" in current.pages[0].text().splitlines()


def test_link_annotation_replaces_an_unallocated_annots_array() -> None:
    """Adding an annotation raised `PdfError` for the unresolvable array."""
    objects = _target_objects()
    objects[3] = "<< /Type /Page /Parent 2 0 R /Contents 5 0 R /Annots 90 0 R >>"
    doc = pylopdf.open(build_raw_pdf(objects))

    doc.pages[0].links.add((10, 10, 100, 30), "https://example.com/")

    for current in _with_reloaded(doc):
        assert [link.target for link in current.pages[0].links] == ["https://example.com/"]


def test_metadata_replaces_an_unallocated_info_dictionary() -> None:
    """Setting metadata raised `PdfError` for the unresolvable trailer `/Info`."""
    data = build_raw_pdf(_target_objects()).replace(b"/Root 1 0 R >>", b"/Root 1 0 R /Info 90 0 R >>")
    doc = pylopdf.open(data)
    assert "title" not in doc.metadata

    doc.metadata.update({"title": "Edited"})

    for current in _with_reloaded(doc):
        assert current.metadata["title"] == "Edited"
