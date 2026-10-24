"""Save options for compression effort, the cross-reference form, decompression, and the document ID."""

from __future__ import annotations

import io
import re
from itertools import product
from pathlib import Path
from typing import Any

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
ENCRYPTED = Path(__file__).parent / "assets" / "encrypted"
ID = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f]*)>\s*<([0-9A-Fa-f]*)>\s*\]")


def _repetitive_pdf(*, version: str = "1.7") -> bytes:
    content = "".join(f"{i} {i} m {i + 50} {i + 70} l S\n" for i in range(400))
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 600 600] >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
            4: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        },
        version=version,
    )


def _document_id(data: bytes) -> tuple[bytes, bytes] | None:
    found = ID.findall(data)
    return None if not found else found[-1]


def test_compression_level_trades_size_for_effort() -> None:
    sizes = {level: len(pylopdf.open(_repetitive_pdf()).to_bytes(compression_level=level)) for level in (1, 6, 9)}

    # zlib's higher levels search harder; level 9 is not always smaller than 6.
    assert sizes[9] < sizes[1]
    assert sizes[6] < sizes[1]
    # Level 0 stores the data, which is never smaller, so the stream keeps no filter.
    stored = pylopdf.open(_repetitive_pdf()).to_bytes(compression_level=0)
    assert b"/FlateDecode" not in stored
    for level in (0, 1, 6, 9):
        reopened = pylopdf.open(pylopdf.open(_repetitive_pdf()).to_bytes(compression_level=level))
        assert len(reopened.pages[0].content().paths) == 400


def test_compression_level_applies_to_object_streams() -> None:
    doc = pylopdf.open(REAL_WORLD / "f1040.pdf")
    fast = doc.to_bytes(object_streams=True, compress=False, compression_level=1)
    small = pylopdf.open(REAL_WORLD / "f1040.pdf").to_bytes(object_streams=True, compress=False, compression_level=9)

    assert len(small) < len(fast)


@pytest.mark.parametrize(("value", "error"), [(10, ValueError), (-1, ValueError), (True, TypeError), (6.0, TypeError)])
def test_compression_level_is_from_0_through_9(value: object, error: type[Exception]) -> None:
    with pytest.raises(error, match="compression_level"):
        pylopdf.open(build_pdf(["A"])).to_bytes(compression_level=value)  # ty: ignore[invalid-argument-type]


def test_cross_reference_writes_a_table_or_a_stream() -> None:
    table = _repetitive_pdf(version="1.4")
    as_stream = pylopdf.open(table).to_bytes(cross_reference="stream")
    as_table = pylopdf.open(as_stream).to_bytes(cross_reference="table")

    assert b"/Type/XRef" in as_stream
    assert b"\nxref\n" not in as_stream
    # A cross-reference stream needs PDF 1.5.
    assert pylopdf.open(as_stream).version == "1.5"
    assert b"\nxref\n" in as_table
    assert b"/Type/XRef" not in as_table
    # None keeps the form that was read.
    assert b"/Type/XRef" in pylopdf.open(as_stream).to_bytes()
    assert b"\nxref\n" in pylopdf.open(as_table).to_bytes()
    for data in (as_stream, as_table):
        assert len(pylopdf.open(data).pages[0].content().paths) == 400


def test_the_chosen_form_stays_for_later_saves() -> None:
    doc = pylopdf.open(_repetitive_pdf())
    doc.to_bytes(cross_reference="stream")

    assert b"/Type/XRef" in doc.to_bytes()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"cross_reference": "hybrid"}, "cross_reference must be"),
        ({"cross_reference": "table", "object_streams": True}, "needs a cross-reference stream"),
        ({"decompress": True}, "decompress=True needs compress=False"),
        ({"incremental": True, "decompress": True, "compress": False}, "decompress"),
        ({"incremental": True, "cross_reference": "table"}, "cross_reference"),
    ],
)
def test_conflicting_options_are_refused_before_any_change(options: dict[str, Any], message: str) -> None:
    doc = pylopdf.open(_repetitive_pdf())
    before = doc.to_bytes(keep_id=True)

    with pytest.raises(ValueError, match=message):
        doc.to_bytes(**options)

    assert doc.to_bytes(keep_id=True) == before


def test_decompress_removes_the_filters_it_can_decode() -> None:
    compressed = pylopdf.open(_repetitive_pdf()).to_bytes()
    assert b"/FlateDecode" in compressed

    expanded = pylopdf.open(compressed).to_bytes(compress=False, decompress=True)

    assert b"/FlateDecode" not in expanded
    assert b"0 0 m 50 70 l S" in expanded
    assert len(pylopdf.open(expanded).pages[0].content().paths) == 400


def test_decompress_keeps_image_filters() -> None:
    doc = pylopdf.open(REAL_WORLD / "patent-us223898.pdf")
    images = {xref: image.filters for xref, image in doc.images.items()}

    expanded = pylopdf.open(doc.to_bytes(compress=False, decompress=True))

    assert any("DCTDecode" in filters or "CCITTFaxDecode" in filters for filters in images.values())
    for xref, filters in images.items():
        if any(name in filters for name in ("DCTDecode", "CCITTFaxDecode", "JPXDecode")):
            assert expanded.images[xref].filters == filters


def test_saving_gives_a_document_an_id_and_keeps_its_first_element() -> None:
    original = build_pdf(["A"])
    assert _document_id(original) is None
    doc = pylopdf.open(original)

    first = doc.to_bytes()
    found = _document_id(first)
    assert found is not None
    permanent, changing = found
    assert permanent == changing
    # An unedited document keeps its identifier.
    assert _document_id(doc.to_bytes()) == found

    doc.metadata["title"] = "Edited"
    edited = _document_id(doc.to_bytes())

    assert edited is not None
    assert edited[0] == permanent
    assert edited[1] != changing


def test_the_id_is_a_digest_of_the_content() -> None:
    def build() -> bytes:
        doc = pylopdf.Document()
        doc.pages.new()
        doc.metadata["title"] = "Same"
        return doc.to_bytes()

    assert build() == build()
    other = pylopdf.Document()
    other.pages.new()
    other.metadata["title"] = "Different"
    assert _document_id(other.to_bytes()) != _document_id(build())


def test_keep_id_writes_the_id_as_read() -> None:
    original = build_pdf(["A"])
    doc = pylopdf.open(original)
    doc.metadata["title"] = "Edited"

    assert _document_id(doc.to_bytes(keep_id=True)) is None
    with_id = pylopdf.open(doc.to_bytes())
    with_id.metadata["title"] = "Again"
    assert _document_id(with_id.to_bytes(keep_id=True)) == _document_id(doc.to_bytes())


def test_an_unedited_incremental_save_keeps_the_bytes_without_an_id() -> None:
    original = build_pdf(["A"])

    assert pylopdf.open(original).to_bytes(incremental=True) == original


def test_an_edited_incremental_save_renews_the_id() -> None:
    original = pylopdf.open(build_pdf(["A"])).to_bytes()
    permanent, changing = _document_id(original) or (b"", b"")
    doc = pylopdf.open(original)
    doc.metadata["title"] = "Edited"

    update = doc.to_bytes(incremental=True)[len(original) :]

    assert _document_id(update) == (permanent, _document_id(update)[1])  # ty: ignore[not-subscriptable]
    assert _document_id(update)[1] != changing  # ty: ignore[not-subscriptable]


@pytest.mark.parametrize("name", ["user-aes-128.pdf", "user-rc4-128.pdf"])
def test_renewing_the_id_keeps_encryption_that_derives_its_key_from_it(name: str) -> None:
    doc = pylopdf.open(ENCRYPTED / name, password="userpw")
    doc.metadata["title"] = "Edited"
    encryption = (
        pylopdf.Encryption.KEEP if "aes" in name else pylopdf.Encryption(user_password="userpw", method="aes-128")
    )

    data = doc.to_bytes(encryption=encryption)

    with pylopdf.open(data, password="userpw") as reopened:
        assert reopened.metadata["title"] == "Edited"
        assert "Encrypted page one" in reopened.pages[0].text()


def test_pikepdf_reads_every_combination_of_save_options() -> None:
    pikepdf = pytest.importorskip("pikepdf", reason="interop group (pikepdf) is not installed")
    source = (REAL_WORLD / "f1040.pdf").read_bytes()
    combinations = product((None, "table", "stream"), (False, True), (False, True), (0, 9))
    checked = 0
    for cross_reference, object_streams, decompress, level in combinations:
        if object_streams and cross_reference == "table":
            continue
        doc = pylopdf.open(source)
        doc.metadata["title"] = "Options"
        data = doc.to_bytes(
            cross_reference=cross_reference,
            object_streams=object_streams,
            decompress=decompress,
            compress=not decompress,
            compression_level=level,
        )
        with pikepdf.open(io.BytesIO(data)) as pdf:
            assert pdf.check_pdf_syntax() == [], (cross_reference, object_streams, decompress, level)
            assert len(pdf.pages) == len(doc.pages)
            assert str(pdf.docinfo["/Title"]) == "Options"
        checked += 1
    assert checked == 20
