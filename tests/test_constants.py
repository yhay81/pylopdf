"""pymupdf's constant groups as IntEnum and IntFlag members."""

from __future__ import annotations

import enum
from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf


@pytest.mark.parametrize(
    ("group", "prefix", "values"),
    [
        (pylopdf.LinkKind, "LINK_", {"NONE": 0, "GOTO": 1, "URI": 2, "LAUNCH": 3, "NAMED": 4, "GOTOR": 5}),
        (pylopdf.TextAlign, "TEXT_ALIGN_", {"LEFT": 0, "CENTER": 1, "RIGHT": 2, "JUSTIFY": 3}),
        (
            pylopdf.Permissions,
            "PDF_PERM_",
            {
                "PRINT": 4,
                "MODIFY": 8,
                "COPY": 16,
                "ANNOTATE": 32,
                "FORM": 256,
                "ACCESSIBILITY": 512,
                "ASSEMBLE": 1024,
                "PRINT_HQ": 2048,
            },
        ),
        (
            pylopdf.EncryptionMethod,
            "PDF_ENCRYPT_",
            {"KEEP": 0, "NONE": 1, "RC4_40": 2, "RC4_128": 3, "AES_128": 4, "AES_256": 5, "UNKNOWN": 6},
        ),
    ],
)
def test_module_constants_are_members_with_pymupdf_values(
    group: type[enum.IntEnum | enum.IntFlag],
    prefix: str,
    values: dict[str, int],
) -> None:
    assert group.__module__ == "pylopdf"
    for name, value in values.items():
        member = getattr(pylopdf, prefix + name)
        assert member is group[name]
        assert member == value
        assert isinstance(member, int)
        assert prefix + name in pylopdf.__all__


def test_permissions_drop_the_old_member_names() -> None:
    assert sum(member.value for member in pylopdf.Permissions if member.name != "ALL") == pylopdf.Permissions.ALL
    for old in ("FILL_FORMS", "COPY_FOR_ACCESSIBILITY", "PRINT_HIGH_QUALITY"):
        assert not hasattr(pylopdf.Permissions, old)


def test_get_links_reports_link_kind_members() -> None:
    page = pylopdf.open(stream=build_pdf(["Link"]))[0]
    page.insert_link({"kind": pylopdf.LINK_URI, "from": (10, 10, 100, 30), "uri": "https://example.com/"})

    (link,) = page.get_links()

    assert link["kind"] is pylopdf.LINK_URI
    assert repr(link["kind"]) == "<LinkKind.URI: 2>"


def test_text_align_members_and_plain_integers_align_alike() -> None:
    def spare(align: int) -> list[tuple[float, ...]]:
        page = pylopdf.open().new_page()
        page.insert_textbox((20, 20, 300, 100), "centered", align=align)
        return [tuple(round(value, 3) for value in word[:4]) for word in page.get_text("words")]

    assert spare(pylopdf.TEXT_ALIGN_CENTER) == spare(1)
    assert spare(pylopdf.TEXT_ALIGN_CENTER) != spare(pylopdf.TEXT_ALIGN_LEFT)


def test_save_defaults_to_pdf_encrypt_none() -> None:
    doc = pylopdf.open(stream=build_pdf(["Plain"]))

    assert b"/Encrypt" not in doc.tobytes()
    assert b"/Encrypt" not in doc.tobytes(encryption=pylopdf.PDF_ENCRYPT_NONE)


@pytest.mark.parametrize("method", ["save", "tobytes"])
def test_aes_256_encrypts_with_passwords(tmp_path: Path, method: str) -> None:
    doc = pylopdf.open(stream=build_pdf(["Secret"]))
    options = {"encryption": pylopdf.PDF_ENCRYPT_AES_256, "user_pw": "user", "owner_pw": "owner"}

    if method == "save":
        target = tmp_path / "locked.pdf"
        doc.save(target, **options)  # ty: ignore[invalid-argument-type]
        data = target.read_bytes()
    else:
        data = doc.tobytes(**options)  # ty: ignore[invalid-argument-type]

    locked = pylopdf.open(stream=data)
    assert locked.needs_pass
    assert pylopdf.open(stream=data, password="user")[0].get_text().strip() == "Secret"
    assert not doc.is_encrypted


def test_aes_256_without_passwords_encrypts_with_empty_passwords() -> None:
    data = pylopdf.open(stream=build_pdf(["Open"])).tobytes(encryption=pylopdf.PDF_ENCRYPT_AES_256)

    assert b"/Encrypt" in data
    reopened = pylopdf.open(stream=data)
    assert not reopened.needs_pass
    assert reopened[0].get_text().strip() == "Open"


@pytest.mark.parametrize("password", [{"user_pw": "x"}, {"owner_pw": "x"}, {"user_pw": ""}])
def test_password_without_encrypting_method_raises(tmp_path: Path, password: dict[str, str]) -> None:
    doc = pylopdf.open(stream=build_pdf(["Plain"]))
    before = doc.tobytes()

    with pytest.raises(pylopdf.PdfError, match="PDF_ENCRYPT_AES_256"):
        doc.tobytes(garbage=4, **password)  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.PdfError, match="PDF_ENCRYPT_AES_256"):
        doc.save(tmp_path / "out.pdf", **password)  # ty: ignore[invalid-argument-type]
    assert not (tmp_path / "out.pdf").exists()
    assert doc.tobytes() == before


@pytest.mark.parametrize(
    ("encryption", "name"),
    [
        (pylopdf.PDF_ENCRYPT_KEEP, "PDF_ENCRYPT_KEEP"),
        (pylopdf.PDF_ENCRYPT_RC4_40, "PDF_ENCRYPT_RC4_40"),
        (pylopdf.PDF_ENCRYPT_RC4_128, "PDF_ENCRYPT_RC4_128"),
        (pylopdf.PDF_ENCRYPT_AES_128, "PDF_ENCRYPT_AES_128"),
        (pylopdf.PDF_ENCRYPT_UNKNOWN, "PDF_ENCRYPT_UNKNOWN"),
        (99, "99"),
    ],
)
def test_unsupported_encryption_methods_raise(encryption: int, name: str) -> None:
    doc = pylopdf.open(stream=build_pdf(["Plain"]))

    with pytest.raises(pylopdf.PdfError, match=f"encryption={name} is not supported"):
        doc.tobytes(encryption=encryption, user_pw="x")


def test_encryption_must_be_an_integer() -> None:
    doc = pylopdf.open(stream=build_pdf(["Plain"]))

    with pytest.raises(TypeError, match="PDF_ENCRYPT"):
        doc.tobytes(encryption=True)
    with pytest.raises(TypeError, match="PDF_ENCRYPT"):
        doc.tobytes(encryption="aes")  # ty: ignore[invalid-argument-type]
