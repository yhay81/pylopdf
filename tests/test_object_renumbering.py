"""Renumber imported source objects in time linear in their count."""

from __future__ import annotations

import re
import time

import pytest
from conftest import build_pdf, build_raw_pdf, outline_entries

import pylopdf

FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _stream(text: str) -> str:
    content = f"BT /F1 20 Tf 20 100 Td ({text}) Tj ET"
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"


def _descending_pages_pdf(texts: list[str]) -> bytes:
    """Build pages whose object numbers descend in page order."""
    count = len(texts)
    kids = " ".join(f"{4 + 2 * slot} 0 R" for slot in reversed(range(count)))
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {count} /MediaBox [0 0 200 200] "
        "/Resources << /Font << /F1 3 0 R >> >> >>",
        3: FONT,
    }
    for slot in range(count):
        objects[4 + 2 * slot] = f"<< /Type /Page /Parent 2 0 R /Contents {5 + 2 * slot} 0 R >>"
        objects[5 + 2 * slot] = _stream(texts[count - 1 - slot])
    return build_raw_pdf(objects)


def _kids_numbers(saved: bytes) -> list[int]:
    kids = re.search(rb"/Kids\s*\[([^\]]*)\]", saved)
    assert kids is not None
    return [int(number) for number in re.findall(rb"(\d+) 0 R", kids.group(1))]


def _sparse_pdf(objects: dict[int, str]) -> bytes:
    """Build an xref-table PDF whose unused object numbers are free entries."""
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
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
    return bytes(out)


def _missing_content_source(missing: int, *, free_entry: bool = False) -> pylopdf.Document:
    """Build a source page that draws object 4 and the missing object `missing`."""
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> >>",
        3: f"<< /Type /Page /Parent 2 0 R /Contents [4 0 R {missing} 0 R] >>",
        4: _stream("Source text"),
        5: FONT,
    }
    if free_entry:
        # Leave the missing number as a free entry below the highest object.
        objects[missing + 1] = "<< /Unused true >>"
    return pylopdf.open(_sparse_pdf(objects))


def _target_with_form() -> pylopdf.Document:
    """Build a one-page target whose object 6 is a Form XObject that it draws."""
    form = "BT /F1 20 Tf 20 700 Td (TARGET FORM) Tj ET"
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R "
                "/Resources << /Font << /F1 5 0 R >> /XObject << /T 6 0 R >> >> >>",
                4: "<< /Length 5 >>\nstream\n/T Do\nendstream",
                5: FONT,
                6: f"<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] "
                f"/Resources << /Font << /F1 5 0 R >> >> /Length {len(form)} >>\nstream\n{form}\nendstream",
            }
        )
    )


def test_imported_pages_take_ascending_object_numbers_in_page_order() -> None:
    source = pylopdf.open(_descending_pages_pdf(["First", "Second", "Third"]))
    doc = pylopdf.open(build_pdf(["Target"]))

    doc.pages.extend(source.pages)
    doc.pages.new()
    with doc.pages[1].draw() as canvas:
        canvas.text("Added after import", (20, 150))

    texts = [doc.pages[index].text() for index in range(len(doc.pages))]
    for index, expected in enumerate(["Target", "First", "Second", "Third"]):
        assert expected in texts[index]
    assert "Added after import" in texts[1]
    saved = doc.to_bytes()
    imported = _kids_numbers(saved)[1:4]
    assert imported == sorted(imported)
    reloaded = pylopdf.open(saved)
    assert [reloaded.pages[index].text() for index in range(len(reloaded.pages))] == texts


@pytest.mark.parametrize(
    "kids",
    # In a seven-object target, source objects 3 and 6 become numbers 10 and 13.
    ["3 0 R 10 0 R", "13 0 R 3 0 R"],
    ids=["missing-kid-names-the-page", "missing-kid-names-an-orphan-page"],
)
def test_imports_the_page_the_source_reports_when_a_missing_kid_shares_a_new_number(kids: str) -> None:
    """The source page tree is read before its objects are renumbered.

    lopdf's renumbering kept the number of a reference to a missing object.
    Reading the tree after it resolved the missing Kids entry to whichever
    source object took that number, so the import rejected a reused page or
    took a page outside the source page tree.
    """
    source = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: f"<< /Type /Pages /Kids [{kids}] /Count 1 /MediaBox [0 0 200 200] "
                "/Resources << /Font << /F1 5 0 R >> >> >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: _stream("Reported page"),
                5: FONT,
                6: "<< /Type /Page /Parent 2 0 R /Contents 7 0 R >>",
                7: _stream("Orphan page"),
            }
        )
    )
    assert len(source.pages) == 1
    inserted = pylopdf.open(build_pdf(["Target one", "Target two"]))
    stamped = pylopdf.open(build_pdf(["Target one", "Target two"]))
    assert inserted.complexity.object_count == stamped.complexity.object_count == 7

    inserted.pages.extend(source.pages)
    with stamped.pages[0].draw() as canvas:
        canvas.page((0, 0, 100, 100), source.pages[0])

    assert len(inserted.pages) == 3
    assert "Reported page" in pylopdf.open(inserted.to_bytes()).pages[2].text()
    stamp_text = pylopdf.open(stamped.to_bytes()).pages[0].text()
    assert "Reported page" in stamp_text
    assert "Orphan page" not in stamp_text


@pytest.mark.parametrize("free_entry", [False, True], ids=["above-highest-object", "free-entry"])
def test_missing_source_reference_cannot_draw_a_target_page(*, free_entry: bool) -> None:
    """Import renumbering turns a reference to a missing object into null.

    ISO 32000-2 §7.3.10 reads such a reference as null. A kept number made the
    imported page also draw object 7 of the target, its second page's content.
    """
    source = _missing_content_source(7, free_entry=free_entry)
    assert source.pages[0].text().split() == ["Source", "text"]
    target = pylopdf.open(build_pdf(["Target one", "TARGET PAGE TWO"]))

    target.pages.extend(source.pages)

    for doc in (target, pylopdf.open(target.to_bytes())):
        assert doc.pages[2].text().split() == ["Source", "text"]
        assert "TARGET PAGE TWO" in doc.pages[1].text()


@pytest.mark.parametrize("operation", ["insert_pdf", "canvas_page"])
def test_missing_source_resource_cannot_name_a_target_xobject(operation: str) -> None:
    """A missing XObject resource stays missing instead of naming target object 6."""
    content = "BT /F1 20 Tf 20 100 Td (Source text) Tj ET /X Do"
    source = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 5 0 R >> /XObject << /X 6 0 R >> >> >>",
                3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
                4: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
                5: FONT,
            }
        )
    )
    assert source.pages[0].text().split() == ["Source", "text"]
    target = _target_with_form()

    if operation == "insert_pdf":
        target.pages.extend(source.pages)
        texts = {0: ["TARGET", "FORM"], 1: ["Source", "text"]}
    else:
        with target.pages[0].draw() as canvas:
            canvas.page(target.pages[0].rect, source.pages[0])
        texts = {0: ["TARGET", "FORM", "Source", "text"]}

    for doc in (target, pylopdf.open(target.to_bytes())):
        assert {index: doc.pages[index].text().split() for index in texts} == texts


def test_missing_source_reference_cannot_name_an_object_added_later() -> None:
    """A missing reference cannot resolve to an object that a later edit adds.

    The five-object target numbers the first source's objects 6-10 and the
    stamp source's objects 11-15, so the stamped Form XObject becomes object
    16, the number of the first source's missing content entry.
    """
    target = pylopdf.open(build_pdf(["Target"]))
    target.pages.extend(_missing_content_source(16).pages)

    stamp = pylopdf.open(build_pdf(["Stamp"]))
    with target.pages[0].draw() as canvas:
        canvas.page(pylopdf.Rect(0, 396, 612, 792), stamp.pages[0])

    for doc in (target, pylopdf.open(target.to_bytes())):
        assert doc.pages[0].text().split() == ["Target", "Stamp"]
        assert doc.pages[1].text().split() == ["Source", "text"]


def test_imports_from_a_source_with_tens_of_thousands_of_objects_stay_fast() -> None:
    """Renumbering the copied source is linear in its object count.

    lopdf's `renumber_objects_with` scans a list of visited IDs for every
    reference, walks the graph twice when page numbers descend in page order,
    and rescans the bookmark table that lopdf's outline builder fills once per renumbered
    object. These two imports from a source of about 64,000 objects took about
    7.7 s locally with it; the generous bound only catches that regression.
    """
    page_count = 30_000
    source = pylopdf.open(_descending_pages_pdf([f"P{index}" for index in range(page_count)]))
    source.outline = outline_entries([(1, f"Entry {index}", index) for index in range(2048)])
    doc = pylopdf.open(build_pdf(["Target"]))

    start = time.perf_counter()
    doc.pages.extend(source.pages[page_count - 1])
    with doc.pages[0].draw() as canvas:
        canvas.page((0, 0, 100, 100), source.pages[0])
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"pages.extend and canvas.page took {elapsed:.2f}s"
    assert len(doc.pages) == 2
    assert "P0" in doc.pages[0].text()
    assert f"P{page_count - 1}" in doc.pages[1].text()
