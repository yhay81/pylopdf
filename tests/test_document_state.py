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

    assert doc.pages[0].text().strip() == "Buffered"


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
    assert doc.pages[0].text().strip() == "Locked"


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
def test_encryption_method_describes_the_encryption(name: str, description: str) -> None:
    doc = pylopdf.open(ENCRYPTED / name, password="userpw")

    assert doc.encryption_method == description


def test_version_and_encryption_method_of_a_plain_document() -> None:
    doc = pylopdf.open(build_pdf(["Plain"]))

    assert doc.encryption_method is None
    assert doc.version == pylopdf.probe(build_pdf(["Plain"])).version


def test_metadata_lists_only_the_entries_present() -> None:
    assert dict(pylopdf.open(build_pdf(["Plain"])).metadata) == {}
    doc = pylopdf.open(_info_pdf("<< /Trapped /False /Title (Report) /CreationDate (D:20260101) /Custom (x) >>"))

    # Keys follow pylopdf's order, whatever the dictionary's; other Info keys are not listed.
    assert list(doc.metadata) == ["title", "created", "trapped"]
    assert doc.metadata == {"title": "Report", "created": "D:20260101", "trapped": "False"}
    assert len(doc.metadata) == 3
    assert "author" not in doc.metadata
    with pytest.raises(KeyError):
        _ = doc.metadata["author"]
    assert doc.metadata.get("author", "") == ""
    assert repr(doc.metadata) == "Metadata({'title': 'Report', 'created': 'D:20260101', 'trapped': 'False'})"


@pytest.mark.parametrize("trapped", ["/True", "(True)"])
def test_metadata_reads_trapped_as_a_name_or_a_string(trapped: str) -> None:
    data = _info_pdf(f"<< /Title (Report) /Trapped {trapped} >>")

    assert pylopdf.open(data).metadata["trapped"] == "True"
    # lopdf's fast probe drops /Trapped, so probe does not report it.
    assert "trapped" not in pylopdf.probe(data).metadata


def test_metadata_is_a_live_view() -> None:
    doc = pylopdf.open(build_pdf(["Plain"]))
    metadata = doc.metadata

    doc.metadata["title"] = "Report"
    assert metadata["title"] == "Report"
    assert pylopdf.open(doc.to_bytes()).metadata["title"] == "Report"


def test_assigning_metadata_round_trips_the_mapping() -> None:
    doc = pylopdf.open(_info_pdf("<< /Title (Report) /Author (Alice) /Trapped /False >>"))
    before = dict(doc.metadata)

    doc.metadata = before

    assert doc.metadata == before


def test_assigning_metadata_replaces_every_entry() -> None:
    doc = pylopdf.open(_info_pdf("<< /Title (Report) /Author (Alice) /Custom (kept) >>"))

    doc.metadata = {"subject": "New"}

    assert dict(doc.metadata) == {"subject": "New"}
    # Info keys outside the mapping stay, as the mapping does not list them.
    info = doc.trailer["Info"]
    assert info["Custom"].text == "kept"


def test_metadata_writes_trapped_as_a_name() -> None:
    doc = pylopdf.open(build_pdf(["Plain"]))

    doc.metadata.update(trapped="True", title="Trapped")

    assert doc.metadata["trapped"] == "True"
    data = doc.to_bytes()
    assert b"/Trapped/True" in data
    assert pylopdf.open(data).metadata["trapped"] == "True"
    with pytest.raises(ValueError, match="trapped"):
        doc.metadata["trapped"] = "maybe"
    assert doc.metadata["trapped"] == "True"


def test_deleting_a_metadata_key_removes_the_entry() -> None:
    doc = pylopdf.open(_info_pdf("<< /Title (Report) /Author (Alice) >>"))

    del doc.metadata["title"]

    assert dict(doc.metadata) == {"author": "Alice"}
    with pytest.raises(KeyError):
        del doc.metadata["title"]
    assert doc.metadata.pop("author") == "Alice"
    assert dict(doc.metadata) == {}


@pytest.mark.parametrize("value", ["", None, 5, b"x"])
def test_metadata_values_are_nonempty_strings(value: object) -> None:
    doc = pylopdf.open(_info_pdf("<< /Title (Report) >>"))

    with pytest.raises((TypeError, ValueError)):
        doc.metadata["title"] = value  # ty: ignore[invalid-assignment]
    with pytest.raises((TypeError, ValueError)):
        doc.metadata = {"title": value}  # ty: ignore[invalid-assignment]

    assert doc.metadata["title"] == "Report"


def test_clearing_metadata_removes_info() -> None:
    for clear in (lambda doc: doc.metadata.clear(), lambda doc: setattr(doc, "metadata", {})):
        doc = pylopdf.open(_info_pdf("<< /Title (Report) >>"))
        assert doc.trailer.get("Info") is not None

        clear(doc)

        assert doc.trailer.get("Info") is None
        assert dict(doc.metadata) == {}
        assert dict(pylopdf.open(doc.to_bytes()).metadata) == {}
        assert b"Report" not in doc.to_bytes()


def test_metadata_refuses_unknown_keys_and_other_values_before_any_change() -> None:
    doc = pylopdf.open(build_pdf(["Plain"]))

    with pytest.raises(KeyError, match="unknown metadata key"):
        doc.metadata.update({"bogus": "x", "title": "Unchanged"})
    with pytest.raises(KeyError, match="unknown metadata key"):
        doc.metadata = {"format": "PDF 2.0"}
    with pytest.raises(KeyError, match="unknown metadata key"):
        doc.metadata["creationDate"] = "D:2026"
    assert dict(doc.metadata) == {}
    with pytest.raises(TypeError, match="metadata must be a mapping"):
        doc.metadata = [("title", "x")]  # ty: ignore[invalid-assignment]
    doc.metadata.update([("title", "pairs")], author="keywords")
    assert dict(doc.metadata) == {"title": "pairs", "author": "keywords"}
