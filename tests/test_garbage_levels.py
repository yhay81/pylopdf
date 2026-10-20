"""pymupdf's cumulative ``garbage`` levels for ``save`` and ``tobytes``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
OBJECT_HEADER = re.compile(rb"(?m)^(\d+) (\d+) obj\b")


def _object_ids(data: bytes) -> list[tuple[int, int]]:
    return [(int(number), int(generation)) for number, generation in OBJECT_HEADER.findall(data)]


def _object_body(data: bytes, number: int) -> bytes:
    start = re.search(rf"(?m)^{number} 0 obj\b".encode(), data)
    assert start is not None
    return data[start.end() : data.index(b"endobj", start.end())]


def _signature(doc: pylopdf.Document) -> list[tuple[str, bytes]]:
    return [(doc[i].get_text(), doc[i].get_pixmap(dpi=24).samples) for i in range(doc.page_count)]


def _twice_imported() -> pylopdf.Document:
    """Import one source twice, so every font and resource exists twice."""
    source = pylopdf.open(REAL_WORLD / "f1040.pdf")
    doc = pylopdf.open()
    doc.insert_pdf(source)
    doc.insert_pdf(source)
    return doc


@pytest.mark.parametrize("garbage", [0, 1, 2, 3, 4, False, True])
def test_every_level_keeps_text_and_rendering(garbage: int) -> None:
    path = REAL_WORLD / "bill-hr815.pdf"
    expected = _signature(pylopdf.open(path))
    doc = pylopdf.open(path)

    saved = doc.tobytes(garbage=garbage)

    assert _signature(doc) == expected
    assert _signature(pylopdf.open(stream=saved)) == expected


@pytest.mark.parametrize("garbage", [-1, 5, 100])
def test_levels_outside_zero_to_four_raise_pdf_error(garbage: int) -> None:
    doc = pylopdf.open(stream=build_pdf(["A"]))
    with pytest.raises(pylopdf.PdfError, match="garbage must be an integer from 0 to 4"):
        doc.tobytes(garbage=garbage)


def test_a_non_integer_level_raises_type_error(tmp_path: Path) -> None:
    doc = pylopdf.open(stream=build_pdf(["A"]))
    with pytest.raises(TypeError, match="garbage must be an integer"):
        doc.tobytes(garbage="4")  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="garbage must be an integer"):
        doc.save(tmp_path / "out.pdf", garbage=1.0)  # ty: ignore[invalid-argument-type]
    assert not (tmp_path / "out.pdf").exists()


def test_levels_merge_progressively_more_duplicates() -> None:
    single = pylopdf.open(REAL_WORLD / "f1040.pdf")
    expected = _signature(single) * 2
    sizes = {}
    counts = {}
    for level in (1, 2, 3, 4):
        doc = _twice_imported()
        saved = doc.tobytes(garbage=level)
        sizes[level] = len(saved)
        counts[level] = len(_object_ids(saved))
        reopened = pylopdf.open(stream=saved)
        assert _signature(reopened) == expected
        assert len(reopened.get_form_fields()) == 2 * len(single.get_form_fields())
    assert counts[2] == counts[1]
    assert counts[3] < counts[2]
    assert counts[4] < counts[3]
    # Level 4 removes the second copy of every font program, so the output
    # falls well below twice the size of one copy.
    assert sizes[4] < sizes[1] * 0.6


def test_merging_follows_chains_of_equal_objects() -> None:
    """A font, its descriptor, and its font file merge as one chain."""
    one = pylopdf.open(REAL_WORLD / "f1040.pdf").tobytes(garbage=4)
    two = _twice_imported().tobytes(garbage=4)
    one_font_files = len(re.findall(rb"/FontFile[23]? ", one))
    assert one_font_files > 0
    assert len(re.findall(rb"/FontFile[23]? ", two)) == one_font_files


def test_level_two_numbers_objects_consecutively_with_generation_zero() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
        3: "(UNREACHABLE)",
        4: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R /Probe 7 0 R >>",
        5: "(UNREACHABLE TOO)",
        6: "<< /Length 0 >>\nstream\n\nendstream",
        7: "(KEPT)",
    }

    one = pylopdf.open(stream=build_raw_pdf(objects)).tobytes(garbage=1)
    two = pylopdf.open(stream=build_raw_pdf(objects)).tobytes(garbage=2)

    assert [number for number, _ in _object_ids(one)] == [1, 2, 4, 6, 7]
    assert _object_ids(two) == [(number, 0) for number in range(1, 6)]
    assert b"(KEPT)" in two
    assert pylopdf.open(stream=two).page_count == 1


def test_compaction_nulls_references_to_missing_objects() -> None:
    """A missing number must not come to name an object after renumbering."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Dangling 3 0 R >>",
        2: "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
        3: "(FREED)",
        4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
        5: "<< /Length 0 >>\nstream\n\nendstream",
    }
    raw = build_raw_pdf(objects)
    # Mark object 3 free, so that the catalog names a missing number below
    # the highest object; compaction gives number 3 to the page.
    entries = raw.index(b"0000000000 65535 f \n") + 20
    third = entries + 2 * 20
    raw = raw[:third] + b"0000000000 00001 f \n" + raw[third + 20 :]
    doc = pylopdf.open(stream=raw)
    assert b"/Dangling 3 0 R" in doc.tobytes()

    saved = doc.tobytes(garbage=2)

    assert _object_ids(saved) == [(number, 0) for number in range(1, 5)]
    catalog = _object_body(saved, 1)
    assert re.search(rb"/Dangling\s*null", catalog), catalog
    assert pylopdf.open(stream=saved).page_count == 1


def test_identical_pages_annotations_and_layers_are_never_merged() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /OCProperties << /OCGs [7 0 R 8 0 R] /D << /ON [7 0 R 8 0 R] >> >> >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
        3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R] >>",
        4: "<< /Type /Page /Parent 2 0 R /Annots [6 0 R] >>",
        5: "<< /Type /Annot /Subtype /Square /Rect [10 10 50 50] >>",
        6: "<< /Type /Annot /Subtype /Square /Rect [10 10 50 50] >>",
        7: "<< /Type /OCG /Name (Layer) >>",
        8: "<< /Type /OCG /Name (Layer) >>",
        9: "<< /Shared (EQUAL) >>",
        10: "<< /Shared (EQUAL) >>",
    }
    raw = (
        build_raw_pdf(objects)
        .replace(b"/Annots [5 0 R] >>", b"/Annots [5 0 R] /A 9 0 R >>", 1)
        .replace(b"/Annots [6 0 R] >>", b"/Annots [6 0 R] /A 10 0 R >>", 1)
    )
    raw = _fix_offsets(raw)

    saved = pylopdf.open(stream=raw).tobytes(garbage=4)

    reopened = pylopdf.open(stream=saved)
    assert reopened.page_count == 2
    assert len(reopened[0].annots()) == len(reopened[1].annots()) == 1
    assert len(re.findall(rb"/Type\s*/Page\b(?!s)", saved)) == 2
    assert len(re.findall(rb"/Subtype\s*/Square", saved)) == 2
    assert len(re.findall(rb"/Type\s*/OCG", saved)) == 2
    # Ordinary equal dictionaries do merge.
    assert saved.count(b"(EQUAL)") == 1


def test_reference_cycles_are_kept_and_saved() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Ring [4 0 R 6 0 R] >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
        3: "<< /Type /Page /Parent 2 0 R >>",
        4: "<< /Other 5 0 R /Tag (RING) >>",
        5: "<< /Other 4 0 R /Tag (RING) >>",
        6: "<< /Other 7 0 R /Tag (RING) >>",
        7: "<< /Other 6 0 R /Tag (RING) >>",
    }

    saved = pylopdf.open(stream=build_raw_pdf(objects)).tobytes(garbage=4)

    assert saved.count(b"(RING)") == 4
    assert pylopdf.open(stream=saved).page_count == 1


def test_drawing_after_compaction_isolates_page_content_once() -> None:
    def contents_entries(doc: pylopdf.Document) -> int:
        saved = doc.tobytes()
        match = re.search(rb"/Contents\s*\[([^\]]*)\]", saved)
        assert match is not None
        return len(re.findall(rb"\d+ \d+ R", match.group(1)))

    plain = pylopdf.open(stream=build_pdf(["Base"]))
    plain[0].insert_text((50, 50), "One")
    plain[0].insert_text((50, 80), "Two")

    compacted = pylopdf.open(stream=build_pdf(["Base"]))
    compacted[0].insert_text((50, 50), "One")
    compacted.tobytes(garbage=4)
    compacted[0].insert_text((50, 80), "Two")

    assert contents_entries(compacted) == contents_entries(plain)
    assert compacted[0].get_text() == plain[0].get_text()


def test_images_placed_after_merging_save_and_merge_again() -> None:
    pixmap = pylopdf.open(REAL_WORLD / "pdf20-simple.pdf")[0].get_pixmap(dpi=24)
    doc = pylopdf.open(stream=build_pdf(["A", "B"]))
    doc[0].insert_image((10, 10, 60, 60), pixmap=pixmap)
    doc.tobytes(garbage=4)
    doc[1].insert_image((10, 10, 60, 60), pixmap=pixmap)

    saved = doc.tobytes(garbage=4)

    once = pylopdf.open(stream=build_pdf(["A", "B"]))
    once[0].insert_image((10, 10, 60, 60), pixmap=pixmap)
    expected = len(re.findall(rb"/Subtype\s*/Image", once.tobytes(garbage=4)))
    assert len(re.findall(rb"/Subtype\s*/Image", saved)) == expected
    reopened = pylopdf.open(stream=saved)
    assert reopened[0].get_pixmap(dpi=24).samples == doc[0].get_pixmap(dpi=24).samples
    assert reopened[1].get_pixmap(dpi=24).samples == doc[1].get_pixmap(dpi=24).samples


def test_saving_to_a_file_accepts_levels(tmp_path: Path) -> None:
    doc = _twice_imported()
    target = tmp_path / "out.pdf"

    doc.save(target, garbage=4, deflate=True, use_objstms=True)

    assert pylopdf.open(target).page_count == 2 * pylopdf.open(REAL_WORLD / "f1040.pdf").page_count


def _fix_offsets(raw: bytes) -> bytes:
    """Rebuild the xref table after editing object bodies in place."""
    body_end = raw.index(b"xref\n")
    body = raw[:body_end]
    offsets = [match.start() for match in OBJECT_HEADER.finditer(body)]
    out = bytearray(body)
    xref_pos = len(out)
    out += f"xref\n0 {len(offsets) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
    return bytes(out)
