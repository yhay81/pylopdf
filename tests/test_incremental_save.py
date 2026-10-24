"""Incremental saves: the original bytes, unchanged, followed by one update."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import build_pdf, build_raw_pdf, draw_text, with_xref_entry_offset

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
ENCRYPTED = Path(__file__).parent / "assets" / "encrypted"
OBJECT_HEADER = re.compile(rb"(?m)^(\d+) 0 obj\b")


def _update(saved: bytes, original: bytes) -> bytes:
    """Return the bytes that an incremental save appended to ``original``."""
    assert saved[: len(original)] == original
    return saved[len(original) :]


def _written(update: bytes) -> set[int]:
    return {int(number) for number in OBJECT_HEADER.findall(update)}


def _xref_stream_number(update: bytes) -> int | None:
    found = re.search(rb"(?m)^(\d+) 0 obj\s*<<[^>]*/Type\s*/XRef", update)
    return None if found is None else int(found.group(1))


def _objects_of(update: bytes) -> set[int]:
    """Return the objects of an update, without the cross-reference stream that indexes them."""
    return _written(update) - {_xref_stream_number(update)}


def test_an_unedited_document_saves_as_it_was_read() -> None:
    original = build_pdf(["Hello"])
    doc = pylopdf.open(original)

    assert doc.can_save_incrementally
    assert doc.to_bytes(incremental=True) == original


def test_an_edit_appends_only_the_objects_that_it_changed() -> None:
    original = build_pdf(["One", "Two"])
    doc = pylopdf.open(original)
    info_before = set(doc.objects)

    doc.metadata["title"] = "Edited"
    saved = doc.to_bytes(incremental=True)

    update = _update(saved, original)
    assert b"%PDF-" not in update
    new_objects = set(doc.objects) - info_before
    # The Info dictionary is new, and the trailer names it; nothing else changed.
    assert _objects_of(update) == new_objects
    assert b"/Prev " in update
    with pylopdf.open(saved) as reopened:
        assert reopened.metadata["title"] == "Edited"
        assert [page.text().strip() for page in reopened.pages] == ["One", "Two"]
        assert not reopened.is_repaired
        assert reopened.can_save_incrementally


def test_drawing_writes_the_page_and_its_new_objects() -> None:
    original = build_pdf(["Base"])
    doc = pylopdf.open(original)
    page_xref = doc.pages[0].xref
    before = set(doc.objects)

    draw_text(doc.pages[0], (72, 200), "Added")
    saved = doc.to_bytes(incremental=True)

    written = _objects_of(_update(saved, original))
    assert page_xref in written
    assert written <= (set(doc.objects) - before) | {page_xref}
    with pylopdf.open(saved) as reopened:
        assert "Added" in reopened.pages[0].text()
        assert "Base" in reopened.pages[0].text()


def test_later_incremental_saves_extend_the_same_original_bytes() -> None:
    original = build_pdf(["Base"])
    doc = pylopdf.open(original)
    doc.metadata["title"] = "First"
    first = doc.to_bytes(incremental=True)
    doc.metadata["subject"] = "Second"

    second = doc.to_bytes(incremental=True)

    # Both updates extend the bytes the document was opened from; the second
    # holds every change since then in one section.
    _update(second, original)
    assert second.count(b"startxref") == 2
    assert first != second
    with pylopdf.open(second) as reopened:
        assert reopened.metadata["title"] == "First"
        assert reopened.metadata["subject"] == "Second"


def test_an_incremental_output_can_be_extended_again() -> None:
    original = build_pdf(["Base"])
    doc = pylopdf.open(original)
    doc.metadata["title"] = "First"
    first = doc.to_bytes(incremental=True)

    reopened = pylopdf.open(first)
    reopened.metadata["title"] = "Second"
    second = reopened.to_bytes(incremental=True)

    _update(second, first)
    assert second.count(b"startxref") == 3
    with pylopdf.open(second) as final:
        assert final.metadata["title"] == "Second"


def test_a_page_deletion_leaves_the_removed_objects_in_the_earlier_revision() -> None:
    original = build_pdf(["Keep", "Drop"])
    doc = pylopdf.open(original)
    dropped = doc.pages[1].xref

    del doc.pages[1]
    saved = doc.to_bytes(incremental=True)

    written = _objects_of(_update(saved, original))
    # Nothing refers to the removed page any more, so the update leaves it
    # alone instead of overwriting it.
    assert dropped not in written
    with pylopdf.open(saved) as reopened:
        assert [page.text().strip() for page in reopened.pages] == ["Keep"]


def test_compress_compresses_only_the_streams_that_the_update_writes() -> None:
    original = build_pdf(["Base"])
    doc = pylopdf.open(original)
    content_streams = {xref for xref in doc.objects if isinstance(doc.objects[xref], pylopdf.Stream)}

    added = doc.objects.add_stream(b"0 0 m 10 10 l S\n" * 200, filter=None)
    doc.catalog["PieceInfo"] = doc.objects[added]
    compressed = _update(doc.to_bytes(incremental=True), original)
    plain = _update(doc.to_bytes(incremental=True, compress=False), original)

    assert added in _objects_of(compressed)
    assert b"/FlateDecode" in compressed
    assert b"/FlateDecode" not in plain
    # The original content stream, which has no filter, is not rewritten.
    assert not (_objects_of(compressed) & content_streams)
    # The document in memory keeps its streams as they were.
    assert doc.objects[added].get("Filter") is None
    assert all(doc.objects[xref].get("Filter") is None for xref in content_streams)


def test_a_cross_reference_stream_document_gets_a_cross_reference_stream_update() -> None:
    original = pylopdf.open(build_pdf(["Base"])).to_bytes(object_streams=True)
    doc = pylopdf.open(original)
    assert doc.can_save_incrementally

    draw_text(doc.pages[0], (72, 200), "Added")
    saved = doc.to_bytes(incremental=True)

    update = _update(saved, original)
    assert _xref_stream_number(update) is not None
    assert b"\nxref\n" not in update
    with pylopdf.open(saved) as reopened:
        assert "Added" in reopened.pages[0].text()


def test_a_cross_reference_table_document_gets_a_table_update() -> None:
    original = build_pdf(["Base"])
    assert b"\nxref\n" in original
    doc = pylopdf.open(original)
    doc.metadata["title"] = "T"

    update = _update(doc.to_bytes(incremental=True), original)

    assert b"\nxref\n" in update
    assert b"trailer" in update
    assert _xref_stream_number(update) is None


def test_save_writes_the_incremental_file(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    original = build_pdf(["Base"])
    source.write_bytes(original)
    doc = pylopdf.open(source)
    doc.metadata["title"] = "Saved"

    other = tmp_path / "other.pdf"
    doc.save(other, incremental=True)
    # Saving over the file the document was opened from works as well,
    # because the document keeps the original bytes.
    doc.save(source, incremental=True)

    assert other.read_bytes() == source.read_bytes()
    _update(source.read_bytes(), original)
    with pylopdf.open(source) as reopened:
        assert reopened.metadata["title"] == "Saved"


def test_incremental_saves_do_not_change_is_dirty() -> None:
    doc = pylopdf.open(build_pdf(["Base"]))
    doc.metadata["title"] = "Edited"

    doc.to_bytes(incremental=True)

    assert doc.is_dirty


@pytest.mark.filterwarnings("ignore:text extraction skipped glyphs:pylopdf.PylopdfWarning")
def test_real_world_documents_save_incrementally() -> None:
    saved_any = False
    for path in sorted(REAL_WORLD.glob("*.pdf")):
        original = path.read_bytes()
        doc = pylopdf.open(original)
        if not doc.can_save_incrementally:
            continue
        saved_any = True
        assert doc.to_bytes(incremental=True) == original, path.name
        pages = len(doc.pages)
        draw_text(doc.pages[0], (36, 36), "Incremental")
        doc.metadata["title"] = "Incremental"
        saved = doc.to_bytes(incremental=True)
        _update(saved, original)
        with pylopdf.open(saved) as reopened:
            assert len(reopened.pages) == pages, path.name
            assert "Incremental" in reopened.pages[0].text(), path.name
            assert reopened.metadata["title"] == "Incremental", path.name
            assert not reopened.is_repaired, path.name
    assert saved_any


def test_incremental_output_respects_max_size() -> None:
    original = build_pdf(["Base"])
    doc = pylopdf.open(original)
    doc.metadata["title"] = "T"

    with pytest.raises(pylopdf.LimitError) as raised:
        doc.to_bytes(incremental=True, max_size=len(original))

    assert raised.value.code == "pdf_output_size"


def test_a_created_document_cannot_save_incrementally() -> None:
    doc = pylopdf.Document()
    doc.pages.new()

    assert not doc.can_save_incrementally
    with pytest.raises(pylopdf.PdfError, match="cannot be saved incrementally"):
        doc.to_bytes(incremental=True)


@pytest.mark.parametrize("shift", [0, 12])
def test_a_repaired_document_cannot_save_incrementally(shift: int) -> None:
    original = build_pdf(["Base"])
    value = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", original)
    assert value is not None
    startxref = str(int(value.group(1)) + shift if shift else 0).encode()
    damaged = original[: value.start(1)] + startxref + original[value.end(1) :]
    with pytest.warns(pylopdf.PylopdfWarning):
        doc = pylopdf.open(damaged)
    assert doc.is_repaired

    assert not doc.can_save_incrementally
    with pytest.raises(pylopdf.PdfError, match="cannot be saved incrementally"):
        doc.to_bytes(incremental=True)


def test_a_document_whose_objects_differ_from_their_entries_cannot_save_incrementally() -> None:
    raw = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        }
    )
    # The entry of the page points into the catalog: lopdf loads the page by
    # its header, while a reader that follows the entry would not find it.
    doc = pylopdf.open(with_xref_entry_offset(raw, 3, raw.index(b"1 0 obj")))

    assert not doc.is_repaired
    assert not doc.can_save_incrementally


def test_an_encrypted_document_cannot_save_incrementally_yet() -> None:
    doc = pylopdf.open(ENCRYPTED / "user-aes-256.pdf", password="userpw")

    assert not doc.can_save_incrementally


@pytest.mark.parametrize(
    ("options", "named"),
    [
        ({"dedupe": True}, "dedupe"),
        ({"renumber": True}, "renumber"),
        ({"object_streams": True}, "object_streams"),
        ({"encryption": pylopdf.Encryption(user_password="u")}, "encryption"),
    ],
)
def test_options_that_rewrite_objects_refuse_incremental_saves(options: dict[str, Any], named: str) -> None:
    doc = pylopdf.open(build_pdf(["Base"]))
    doc.metadata["title"] = "Edited"
    revision_objects = set(doc.objects)

    with pytest.raises(ValueError, match=named):
        doc.to_bytes(incremental=True, **options)

    # Refused before anything changed.
    assert set(doc.objects) == revision_objects


def test_incremental_must_be_a_bool() -> None:
    doc = pylopdf.open(build_pdf(["Base"]))

    with pytest.raises(TypeError, match="incremental"):
        doc.to_bytes(incremental=1)  # ty: ignore[invalid-argument-type]


def test_pikepdf_reads_incremental_output() -> None:
    pikepdf = pytest.importorskip("pikepdf", reason="interop group (pikepdf) is not installed")
    original = (REAL_WORLD / "f1040.pdf").read_bytes()
    doc = pylopdf.open(original)
    doc.form[next(iter(doc.form))].value = "Filled"
    draw_text(doc.pages[1], (72, 72), "Incremental")
    doc.metadata["title"] = "Incremental"

    saved = doc.to_bytes(incremental=True)

    with pikepdf.open(io.BytesIO(saved)) as pdf:
        assert pdf.check_pdf_syntax() == []
        assert len(pdf.pages) == len(doc.pages)
        assert str(pdf.docinfo["/Title"]) == "Incremental"


def test_a_raised_version_goes_into_the_catalog() -> None:
    original = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        },
        version="1.4",
    )
    doc = pylopdf.open(original)
    # Writing object streams raises the version of the document in memory.
    doc.to_bytes(object_streams=True)
    assert doc.version == "1.5"

    saved = doc.to_bytes(incremental=True)

    # An update cannot change the header, so the catalog says it instead.
    assert saved.startswith(b"%PDF-1.4")
    assert 1 in _objects_of(_update(saved, original))
    with pylopdf.open(saved) as reopened:
        assert reopened.catalog["Version"] == "1.5"


def test_keeping_the_encryption_is_what_an_incremental_save_does() -> None:
    original = build_pdf(["Base"])
    doc = pylopdf.open(original)
    doc.metadata["title"] = "Kept"

    kept = doc.to_bytes(incremental=True, encryption=pylopdf.Encryption.KEEP)

    assert kept == doc.to_bytes(incremental=True)
