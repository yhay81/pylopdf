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


def test_open_takes_a_path_or_bytes_and_document_creates_an_empty_one(tmp_path: Path) -> None:
    data = build_pdf(["Typed"])
    path = tmp_path / "typed.pdf"
    path.write_bytes(data)

    assert len(pylopdf.Document().pages) == 0
    assert pylopdf.Document().path is None
    assert pylopdf.open(data).path is None
    assert pylopdf.open(path).path == path
    assert pylopdf.open(str(path)).path == path
    with pytest.raises(TypeError):
        pylopdf.Document(data)  # ty: ignore[too-many-positional-arguments]
    with pytest.raises(TypeError):
        pylopdf.open()  # ty: ignore[missing-argument]


def test_open_options_are_keyword_only() -> None:
    data = (ENCRYPTED / "user-aes-256.pdf").read_bytes()

    with pytest.raises(TypeError):
        pylopdf.open(data, "userpw")  # ty: ignore[too-many-positional-arguments]
    assert len(pylopdf.open(data, password="userpw").pages) == 2


@pytest.mark.parametrize("wrap", [bytes, bytearray, memoryview])
def test_open_accepts_byte_buffers(wrap: type) -> None:
    doc = pylopdf.open(wrap(build_pdf(["Buffered"])))

    assert doc.pages[0].get_text().strip() == "Buffered"


def test_open_rejects_other_sources() -> None:
    with pytest.raises(TypeError, match="read the file object first"):
        pylopdf.open(io.BytesIO(build_pdf(["Buffered"])))  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="path"):
        pylopdf.open(42)  # ty: ignore[invalid-argument-type]


def test_unlocking_an_unencrypted_document_grants_owner_access() -> None:
    assert pylopdf.open(build_pdf(["Plain"])).unlock("anything") is pylopdf.Access.OWNER


def test_unlock_checks_a_document_opened_with_an_empty_user_password() -> None:
    doc = pylopdf.open(ENCRYPTED / "owneronly-aes-256.pdf")

    assert not doc.needs_password
    assert doc.unlock("") is pylopdf.Access.USER
    assert doc.unlock("ownerpw") is pylopdf.Access.OWNER
    with pytest.raises(pylopdf.PasswordError):
        doc.unlock("wrong")
    assert len(doc.pages) >= 1


def test_unlock_refuses_an_empty_owner_password_alone() -> None:
    encryption = pylopdf.Encryption(user_password="user", owner_password="")
    data = pylopdf.open(build_pdf(["Locked"])).to_bytes(encryption=encryption)
    # lopdf opens a document whose owner password is empty without a password.
    doc = pylopdf.open(data)

    with pytest.raises(pylopdf.PasswordError):
        doc.unlock("")
    assert doc.unlock("user") is pylopdf.Access.USER
    assert doc.pages[0].get_text().strip() == "Locked"


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
    metadata = pylopdf.open(build_pdf(["Plain"])).metadata

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

    assert pylopdf.open(data).metadata["trapped"] == "True"
    # lopdf's fast probe drops /Trapped, so probe does not report it.
    assert "trapped" not in pylopdf.probe(data).metadata


def test_set_metadata_round_trips_the_metadata_dictionary() -> None:
    doc = pylopdf.open(_info_pdf("<< /Title (Report) /Author (Alice) /Trapped /False >>"))
    before = doc.metadata

    doc.set_metadata(before)

    assert doc.metadata == before


def test_set_metadata_writes_trapped_as_a_name() -> None:
    doc = pylopdf.open(build_pdf(["Plain"]))

    doc.set_metadata({"trapped": "True", "title": "Trapped"})

    assert doc.metadata["trapped"] == "True"
    data = doc.to_bytes()
    assert b"/Trapped/True" in data
    assert pylopdf.open(data).metadata["trapped"] == "True"
    doc.set_metadata({"trapped": "maybe"})
    assert doc.metadata["trapped"] == "maybe"


@pytest.mark.parametrize("removal", [None, "", "none", "null"])
def test_set_metadata_false_values_remove_entries(removal: str | None) -> None:
    doc = pylopdf.open(_info_pdf("<< /Title (Report) /Author (Alice) >>"))

    doc.set_metadata({"title": removal})

    assert doc.metadata["title"] == ""
    assert doc.metadata["author"] == "Alice"


def test_set_metadata_ignores_read_only_keys() -> None:
    doc = pylopdf.open(build_pdf(["Plain"]))
    version = doc.metadata["format"]

    doc.set_metadata({"format": "PDF 2.0", "encryption": "Standard", "title": "Kept"})

    assert doc.metadata["format"] == version
    assert doc.metadata["encryption"] is None
    assert doc.metadata["title"] == "Kept"


def test_set_metadata_empty_dictionary_removes_info() -> None:
    doc = pylopdf.open(_info_pdf("<< /Title (Report) >>"))
    assert doc.trailer.get("Info") is not None

    doc.set_metadata({})

    assert doc.trailer.get("Info") is None
    assert doc.metadata["title"] == ""
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.metadata["title"] == ""
    assert b"Report" not in doc.to_bytes()


def test_set_metadata_rejects_unknown_keys_and_non_dicts() -> None:
    doc = pylopdf.open(build_pdf(["Plain"]))

    with pytest.raises(ValueError, match="unknown metadata key"):
        doc.set_metadata({"bogus": "x", "title": "Unchanged"})  # ty: ignore[invalid-argument-type]
    assert doc.metadata["title"] == ""
    with pytest.raises(TypeError, match="metadata must be a dict"):
        doc.set_metadata([("title", "x")])  # ty: ignore[invalid-argument-type]
