"""pymupdf's Document constructor, authentication codes, and metadata."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

ENCRYPTED = Path(__file__).parent / "assets" / "encrypted"


def _info_pdf(info: str) -> bytes:
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: info,
        }
    )
    return pdf.replace(b"/Root 1 0 R >>", b"/Root 1 0 R /Info 4 0 R >>")


def test_open_is_the_document_class() -> None:
    assert pylopdf.open is pylopdf.Document
    assert isinstance(pylopdf.open(), pylopdf.Document)


@pytest.mark.parametrize("filetype", ["pdf", "PDF", ".pdf", "application/pdf"])
def test_third_positional_parameter_is_filetype(filetype: str) -> None:
    data = build_pdf(["Typed"])

    assert pylopdf.Document(None, data, filetype).page_count == 1
    assert pylopdf.open(stream=data, filetype=filetype).page_count == 1
    # pymupdf's legacy form names the file type in place of a filename.
    assert pylopdf.open(filetype, data).page_count == 1


def test_unsupported_filetype_raises() -> None:
    data = build_pdf(["Typed"])

    with pytest.raises(pylopdf.PdfError, match="filetype 'png' is not supported"):
        pylopdf.open(stream=data, filetype="png")
    # A password passed third, as pylopdf allowed before, now binds to filetype.
    with pytest.raises(pylopdf.PdfError, match="filetype 'secret' is not supported"):
        pylopdf.Document(None, data, "secret")


def test_options_are_keyword_only() -> None:
    data = (ENCRYPTED / "user-aes-256.pdf").read_bytes()

    with pytest.raises(TypeError):
        pylopdf.Document(None, data, None, "userpw")  # ty: ignore[too-many-positional-arguments]
    assert pylopdf.Document(stream=data, password="userpw").page_count == 2


@pytest.mark.parametrize("wrap", [bytes, bytearray, memoryview, io.BytesIO])
def test_stream_accepts_pymupdf_buffer_types(wrap: type) -> None:
    doc = pylopdf.open(stream=wrap(build_pdf(["Buffered"])))

    assert doc[0].get_text().strip() == "Buffered"


def test_stream_rejects_other_types() -> None:
    with pytest.raises(TypeError, match="stream must be"):
        pylopdf.open(stream="%PDF-1.7")  # ty: ignore[invalid-argument-type]


def test_authenticate_unencrypted_document_returns_one() -> None:
    assert pylopdf.open(stream=build_pdf(["Plain"])).authenticate("anything") == 1


def test_authenticate_checks_a_document_opened_with_an_empty_user_password() -> None:
    doc = pylopdf.open(ENCRYPTED / "owneronly-aes-256.pdf")

    assert not doc.needs_pass
    assert not doc.is_encrypted
    assert doc.authenticate("") == 2
    assert doc.authenticate("ownerpw") == 4
    assert doc.authenticate("wrong") == 0
    assert doc.page_count >= 1


def test_authenticate_refuses_an_empty_owner_password_alone() -> None:
    data = pylopdf.open(stream=build_pdf(["Locked"])).tobytes(
        encryption=pylopdf.PDF_ENCRYPT_AES_256,
        user_pw="user",
        owner_pw="",
    )
    # lopdf opens a document whose owner password is empty without a password.
    doc = pylopdf.open(stream=data)

    assert doc.authenticate("") == 0
    assert doc.authenticate("user") == 2
    assert doc[0].get_text().strip() == "Locked"


@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("user-rc4-40.pdf", "Standard V1 R2 40-bit RC4"),
        ("user-rc4-128.pdf", "Standard V2 R3 128-bit RC4"),
        ("user-aes-128.pdf", "Standard V4 R4 128-bit AES"),
        ("user-aes-256.pdf", "Standard V5 R6 256-bit AES"),
        ("owneronly-aes-256.pdf", "Standard V5 R6 256-bit AES"),
    ],
)
def test_metadata_describes_encryption(name: str, description: str) -> None:
    doc = pylopdf.open(ENCRYPTED / name, password="userpw")

    assert doc.metadata["encryption"] == description


def test_metadata_has_pymupdf_keys() -> None:
    metadata = pylopdf.open(stream=build_pdf(["Plain"])).metadata

    assert list(metadata) == [
        "format",
        "title",
        "author",
        "subject",
        "keywords",
        "creator",
        "producer",
        "creationDate",
        "modDate",
        "trapped",
        "encryption",
    ]
    assert metadata["encryption"] is None
    assert metadata["trapped"] == ""


@pytest.mark.parametrize("trapped", ["/True", "(True)"])
def test_metadata_reads_trapped_as_a_name_or_a_string(trapped: str) -> None:
    data = _info_pdf(f"<< /Title (Report) /Trapped {trapped} >>")

    assert pylopdf.open(stream=data).metadata["trapped"] == "True"
    # lopdf's fast probe drops /Trapped, so peek_metadata does not report it.
    assert "trapped" not in pylopdf.peek_metadata(stream=data)


def test_set_metadata_round_trips_the_metadata_dictionary() -> None:
    doc = pylopdf.open(stream=_info_pdf("<< /Title (Report) /Author (Alice) /Trapped /False >>"))
    before = doc.metadata

    doc.set_metadata(before)

    assert doc.metadata == before


def test_set_metadata_writes_trapped_as_a_name() -> None:
    doc = pylopdf.open(stream=build_pdf(["Plain"]))

    doc.set_metadata({"trapped": "True", "title": "Trapped"})

    assert doc.metadata["trapped"] == "True"
    data = doc.tobytes()
    assert b"/Trapped/True" in data
    assert pylopdf.open(stream=data).metadata["trapped"] == "True"
    doc.set_metadata({"trapped": "maybe"})
    assert doc.metadata["trapped"] == "maybe"


@pytest.mark.parametrize("removal", [None, "", "none", "null"])
def test_set_metadata_false_values_remove_entries(removal: str | None) -> None:
    doc = pylopdf.open(stream=_info_pdf("<< /Title (Report) /Author (Alice) >>"))

    doc.set_metadata({"title": removal})

    assert doc.metadata["title"] == ""
    assert doc.metadata["author"] == "Alice"


def test_set_metadata_ignores_read_only_keys() -> None:
    doc = pylopdf.open(stream=build_pdf(["Plain"]))
    version = doc.metadata["format"]

    doc.set_metadata({"format": "PDF 2.0", "encryption": "Standard", "title": "Kept"})

    assert doc.metadata["format"] == version
    assert doc.metadata["encryption"] is None
    assert doc.metadata["title"] == "Kept"


def test_set_metadata_empty_dictionary_removes_info() -> None:
    doc = pylopdf.open(stream=_info_pdf("<< /Title (Report) >>"))
    assert doc.trailer.get("Info") is not None

    doc.set_metadata({})

    assert doc.trailer.get("Info") is None
    assert doc.metadata["title"] == ""
    reopened = pylopdf.open(stream=doc.tobytes(garbage=1))
    assert reopened.metadata["title"] == ""
    assert b"Report" not in doc.tobytes(garbage=1)


def test_set_metadata_rejects_unknown_keys_and_non_dicts() -> None:
    doc = pylopdf.open(stream=build_pdf(["Plain"]))

    with pytest.raises(ValueError, match="unknown metadata key"):
        doc.set_metadata({"bogus": "x", "title": "Unchanged"})  # ty: ignore[invalid-argument-type]
    assert doc.metadata["title"] == ""
    with pytest.raises(TypeError, match="metadata must be a dict"):
        doc.set_metadata([("title", "x")])  # ty: ignore[invalid-argument-type]
