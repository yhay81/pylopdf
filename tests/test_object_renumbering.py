"""Renumber imported source objects in time linear in their count."""

from __future__ import annotations

import re
import time

import pytest
from conftest import build_pdf, build_raw_pdf

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


def test_imported_pages_take_ascending_object_numbers_in_page_order() -> None:
    source = pylopdf.Document(stream=_descending_pages_pdf(["First", "Second", "Third"]))
    doc = pylopdf.Document(stream=build_pdf(["Target"]))

    doc.insert_pdf(source)
    doc.new_page()
    doc[1].insert_text((20, 150), "Added after import")

    texts = [doc.get_page_text(index) for index in range(doc.page_count)]
    for index, expected in enumerate(["Target", "First", "Second", "Third"]):
        assert expected in texts[index]
    assert "Added after import" in texts[1]
    saved = doc.tobytes()
    imported = _kids_numbers(saved)[1:4]
    assert imported == sorted(imported)
    reloaded = pylopdf.Document(stream=saved)
    assert [reloaded.get_page_text(index) for index in range(reloaded.page_count)] == texts


@pytest.mark.parametrize(
    "kids",
    # In a seven-object target, source objects 3 and 6 become numbers 10 and 13.
    ["3 0 R 10 0 R", "13 0 R 3 0 R"],
    ids=["missing-kid-names-the-page", "missing-kid-names-an-orphan-page"],
)
def test_imports_the_page_the_source_reports_when_a_missing_kid_shares_a_new_number(kids: str) -> None:
    """The source page tree is read before its objects are renumbered.

    A reference to a missing object keeps its number. Reading the tree after
    renumbering resolved the missing Kids entry to whichever source object took
    that number, so the import rejected a reused page or took a page outside
    the source page tree.
    """
    source = pylopdf.Document(
        stream=build_raw_pdf(
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
    assert source.page_count == 1
    inserted = pylopdf.Document(stream=build_pdf(["Target one", "Target two"]))
    stamped = pylopdf.Document(stream=build_pdf(["Target one", "Target two"]))
    assert inserted.complexity["object_count"] == stamped.complexity["object_count"] == 7

    inserted.insert_pdf(source)
    stamped[0].show_pdf_page((0, 0, 100, 100), source, 0)

    assert inserted.page_count == 3
    assert "Reported page" in pylopdf.Document(stream=inserted.tobytes()).get_page_text(2)
    stamp_text = pylopdf.Document(stream=stamped.tobytes()).get_page_text(0)
    assert "Reported page" in stamp_text
    assert "Orphan page" not in stamp_text


def test_imports_from_a_source_with_tens_of_thousands_of_objects_stay_fast() -> None:
    """Renumbering the copied source is linear in its object count.

    lopdf's `renumber_objects_with` scans a list of visited IDs for every
    reference, walks the graph twice when page numbers descend in page order,
    and rescans the bookmark table that `set_toc` fills once per renumbered
    object. These two imports from a source of about 64,000 objects took about
    7.7 s locally with it; the generous bound only catches that regression.
    """
    page_count = 30_000
    source = pylopdf.Document(stream=_descending_pages_pdf([f"P{index}" for index in range(page_count)]))
    source.set_toc([[1, f"Entry {index}", index + 1] for index in range(2048)])
    doc = pylopdf.Document(stream=build_pdf(["Target"]))

    start = time.perf_counter()
    doc.insert_pdf(source, from_page=-1, to_page=-1)
    doc[0].show_pdf_page((0, 0, 100, 100), source, 0)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"insert_pdf and show_pdf_page took {elapsed:.2f}s"
    assert doc.page_count == 2
    assert "P0" in doc.get_page_text(0)
    assert f"P{page_count - 1}" in doc.get_page_text(1)
