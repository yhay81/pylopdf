"""The options of ``save`` and ``to_bytes`` that change objects: compress, prune, dedupe, and renumber."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from conftest import build_pdf, build_raw_pdf, form_entries

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
    return [(page.text(), bytes(page.pixmap(dpi=24, alpha=True))) for page in doc.pages]


def _twice_imported() -> pylopdf.Document:
    """Import one source twice, so every font and resource exists twice."""
    source = pylopdf.open(REAL_WORLD / "f1040.pdf")
    doc = pylopdf.Document()
    doc.pages.extend(source.pages)
    doc.pages.extend(source.pages)
    return doc


OPTIONS: list[dict[str, Any]] = [
    {"compress": False, "prune": False},
    {},
    {"renumber": True},
    {"dedupe": True},
    {"object_streams": True},
    {"dedupe": True, "renumber": True, "object_streams": True},
]


@pytest.mark.parametrize("options", OPTIONS)
def test_every_option_keeps_text_and_rendering(options: dict[str, Any]) -> None:
    path = REAL_WORLD / "bill-hr815.pdf"
    doc = pylopdf.open(path)
    # Warm this document's text and rendering caches before saving.
    expected = _signature(doc)

    saved = doc.to_bytes(**options)

    assert _signature(doc) == expected
    assert _signature(pylopdf.open(saved)) == expected


@pytest.mark.parametrize("option", ["compress", "prune", "dedupe", "renumber", "object_streams"])
def test_options_must_be_booleans(option: str, tmp_path: Path) -> None:
    doc = pylopdf.open(build_pdf(["A"]))
    with pytest.raises(TypeError, match=f"{option} must be True or False"):
        doc.to_bytes(**{option: 1})  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match=f"{option} must be True or False"):
        doc.save(tmp_path / "out.pdf", **{option: None})  # ty: ignore[invalid-argument-type]
    assert not (tmp_path / "out.pdf").exists()


def test_dedupe_needs_prune() -> None:
    doc = pylopdf.open(build_pdf(["A"]))
    with pytest.raises(ValueError, match="dedupe=True needs prune=True"):
        doc.to_bytes(dedupe=True, prune=False)


def test_saving_compresses_and_prunes_by_default() -> None:
    # lopdf compresses a stream only when Flate makes it smaller.
    content = "0 0 m 100 100 l S\n" * 40 + "% PLAIN"
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        4: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        5: "(UNREACHABLE)",
    }
    raw = build_raw_pdf(objects)

    plain = pylopdf.open(raw).to_bytes(compress=False, prune=False)
    default = pylopdf.open(raw).to_bytes()

    assert b"% PLAIN" in plain
    assert b"(UNREACHABLE)" in plain
    assert b"% PLAIN" not in default
    assert b"/FlateDecode" in default
    assert b"(UNREACHABLE)" not in default
    assert pylopdf.open(default).pages[0].content() == pylopdf.open(plain).pages[0].content()


def test_a_save_that_changes_nothing_keeps_the_original_bytes() -> None:
    # This file has no unreachable object and no stream that Flate makes smaller.
    doc = pylopdf.open(REAL_WORLD / "senate-expenditures.pdf")
    doc.pages[0].text()
    assert doc._doc.snapshot_source() == "original"  # noqa: SLF001

    doc.to_bytes()

    assert doc._doc.snapshot_source() == "original"  # noqa: SLF001
    doc.to_bytes(renumber=True)
    assert doc._doc.snapshot_source() == "none"  # noqa: SLF001


def test_saving_again_keeps_the_snapshot() -> None:
    doc = pylopdf.open(REAL_WORLD / "usrguide.pdf")
    doc.to_bytes()
    doc.pages[0].text()
    source = doc._doc.snapshot_source()  # noqa: SLF001
    assert source != "none"

    doc.to_bytes()

    assert doc._doc.snapshot_source() == source  # noqa: SLF001


def test_dedupe_merges_duplicates_and_renumber_keeps_the_count() -> None:
    single = pylopdf.open(REAL_WORLD / "f1040.pdf")
    expected = _signature(single) * 2
    sizes = {}
    counts = {}
    variants: dict[str, dict[str, Any]] = {"prune": {}, "renumber": {"renumber": True}, "dedupe": {"dedupe": True}}
    for name, options in variants.items():
        doc = _twice_imported()
        saved = doc.to_bytes(**options)
        sizes[name] = len(saved)
        counts[name] = len(_object_ids(saved))
        reopened = pylopdf.open(saved)
        assert _signature(reopened) == expected
        assert len(form_entries(reopened)) == 2 * len(form_entries(single))
    assert counts["renumber"] == counts["prune"]
    assert counts["dedupe"] < counts["prune"]
    # Merging removes the second copy of every font program, so the output
    # falls well below twice the size of one copy.
    assert sizes["dedupe"] < sizes["prune"] * 0.6


def test_merging_follows_chains_of_equal_objects() -> None:
    """A font, its descriptor, and its font file merge as one chain."""
    one = pylopdf.open(REAL_WORLD / "f1040.pdf").to_bytes(dedupe=True, renumber=True)
    two = _twice_imported().to_bytes(dedupe=True, renumber=True)
    one_font_files = len(re.findall(rb"/FontFile[23]? ", one))
    assert one_font_files > 0
    assert len(re.findall(rb"/FontFile[23]? ", two)) == one_font_files


def test_renumber_numbers_objects_consecutively_with_generation_zero() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
        3: "(UNREACHABLE)",
        4: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R /Probe 7 0 R >>",
        5: "(UNREACHABLE TOO)",
        6: "<< /Length 0 >>\nstream\n\nendstream",
        7: "(KEPT)",
    }

    one = pylopdf.open(build_raw_pdf(objects)).to_bytes()
    two = pylopdf.open(build_raw_pdf(objects)).to_bytes(renumber=True)

    assert [number for number, _ in _object_ids(one)] == [1, 2, 4, 6, 7]
    assert _object_ids(two) == [(number, 0) for number in range(1, 6)]
    assert b"(KEPT)" in two
    assert len(pylopdf.open(two).pages) == 1


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
    doc = pylopdf.open(raw)
    assert b"/Dangling 3 0 R" in doc.to_bytes()

    saved = doc.to_bytes(renumber=True)

    assert _object_ids(saved) == [(number, 0) for number in range(1, 5)]
    catalog = _object_body(saved, 1)
    assert re.search(rb"/Dangling\s*null", catalog), catalog
    assert len(pylopdf.open(saved).pages) == 1


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

    saved = pylopdf.open(raw).to_bytes(dedupe=True, renumber=True)

    reopened = pylopdf.open(saved)
    assert len(reopened.pages) == 2
    assert len(reopened.pages[0].annotations) == len(reopened.pages[1].annotations) == 1
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

    saved = pylopdf.open(build_raw_pdf(objects)).to_bytes(dedupe=True, renumber=True)

    assert saved.count(b"(RING)") == 4
    assert len(pylopdf.open(saved).pages) == 1


def test_drawing_after_compaction_isolates_page_content_once() -> None:
    def contents_entries(doc: pylopdf.Document) -> int:
        saved = doc.to_bytes()
        match = re.search(rb"/Contents\s*\[([^\]]*)\]", saved)
        assert match is not None
        return len(re.findall(rb"\d+ \d+ R", match.group(1)))

    plain = pylopdf.open(build_pdf(["Base"]))
    with plain.pages[0].draw() as canvas:
        canvas.text("One", (50, 50))
    with plain.pages[0].draw() as canvas:
        canvas.text("Two", (50, 80))

    compacted = pylopdf.open(build_pdf(["Base"]))
    with compacted.pages[0].draw() as canvas:
        canvas.text("One", (50, 50))
    compacted.to_bytes(dedupe=True, renumber=True)
    with compacted.pages[0].draw() as canvas:
        canvas.text("Two", (50, 80))

    assert contents_entries(compacted) == contents_entries(plain)
    assert compacted.pages[0].text() == plain.pages[0].text()


def test_images_placed_after_merging_save_and_merge_again() -> None:
    pixmap = pylopdf.open(REAL_WORLD / "pdf20-simple.pdf").pages[0].pixmap(dpi=24, alpha=True)
    doc = pylopdf.open(build_pdf(["A", "B"]))
    with doc.pages[0].draw() as canvas:
        canvas.image((10, 10, 60, 60), pixmap)
    doc.to_bytes(dedupe=True, renumber=True)
    with doc.pages[1].draw() as canvas:
        canvas.image((10, 10, 60, 60), pixmap)

    saved = doc.to_bytes(dedupe=True, renumber=True)

    once = pylopdf.open(build_pdf(["A", "B"]))
    with once.pages[0].draw() as canvas:
        canvas.image((10, 10, 60, 60), pixmap)
    expected = len(re.findall(rb"/Subtype\s*/Image", once.to_bytes(dedupe=True, renumber=True)))
    assert len(re.findall(rb"/Subtype\s*/Image", saved)) == expected
    reopened = pylopdf.open(saved)
    assert bytes(reopened.pages[0].pixmap(dpi=24, alpha=True)) == bytes(doc.pages[0].pixmap(dpi=24, alpha=True))
    assert bytes(reopened.pages[1].pixmap(dpi=24, alpha=True)) == bytes(doc.pages[1].pixmap(dpi=24, alpha=True))


def test_saving_to_a_file_accepts_every_option(tmp_path: Path) -> None:
    doc = _twice_imported()
    target = tmp_path / "out.pdf"

    doc.save(target, dedupe=True, renumber=True, object_streams=True)

    assert len(pylopdf.open(target).pages) == 2 * len(pylopdf.open(REAL_WORLD / "f1040.pdf").pages)


@pytest.mark.parametrize("method", ["save", "to_bytes"])
def test_object_streams_pack_objects_into_object_streams(tmp_path: Path, method: str) -> None:
    doc = pylopdf.open(build_pdf(["Compact"] * 3))

    if method == "save":
        target = tmp_path / "out.pdf"
        doc.save(target, object_streams=True)
        data = target.read_bytes()
    else:
        data = doc.to_bytes(object_streams=True)

    assert b"/ObjStm" in data
    assert b"/ObjStm" not in doc.to_bytes()
    reopened = pylopdf.open(data)
    assert [page.text().strip() for page in reopened.pages] == ["Compact"] * 3


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
