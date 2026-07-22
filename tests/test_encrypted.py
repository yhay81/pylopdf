"""暗号化 PDF の読み取り対応のテスト。

フィクスチャは tests/assets/encrypted/（generate.py で生成、パスワードは
user="userpw" / owner="ownerpw"、owneronly-* は user 空）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "encrypted"

USER_PROTECTED = ["user-rc4-40.pdf", "user-rc4-128.pdf", "user-aes-128.pdf", "user-aes-256.pdf"]
USER = pytest.mark.parametrize("name", USER_PROTECTED)


@USER
def test_open_without_password_raises_on_use(name: str) -> None:
    doc = pylopdf.open(ASSETS / name)
    assert doc.needs_pass
    assert doc.is_encrypted
    with pytest.raises(ValueError, match="暗号化された PDF"):
        _ = doc.page_count
    with pytest.raises(ValueError, match="暗号化された PDF"):
        doc.tobytes()


@USER
def test_open_with_user_password(name: str) -> None:
    doc = pylopdf.open(ASSETS / name, password="userpw")
    assert doc.page_count == 2
    assert doc.needs_pass
    assert not doc.is_encrypted
    assert "Encrypted page one" in doc.get_page_text(0)
    assert doc.render_page(0, 0.5).startswith(b"\x89PNG")


@USER
def test_open_with_owner_password(name: str) -> None:
    doc = pylopdf.open(ASSETS / name, password="ownerpw")
    assert doc.page_count == 2


@USER
def test_open_with_wrong_password_raises(name: str) -> None:
    with pytest.raises(ValueError, match="invalid password"):
        pylopdf.open(ASSETS / name, password="wrong")


@USER
def test_wrong_password_is_password_error(name: str) -> None:
    """パスワード起因の失敗は PasswordError で捕捉できる（ValueError 互換）。"""
    with pytest.raises(pylopdf.PasswordError):
        pylopdf.open(ASSETS / name, password="wrong")


def test_unauthenticated_use_is_encrypted_document_error() -> None:
    doc = pylopdf.open(ASSETS / "user-aes-256.pdf")
    with pytest.raises(pylopdf.EncryptedDocumentError):
        _ = doc.page_count


def test_peek_metadata_reports_encrypted_flag() -> None:
    meta = pylopdf.peek_metadata(ASSETS / "user-aes-256.pdf")
    assert meta["encrypted"] is True


@USER
def test_authenticate(name: str) -> None:
    doc = pylopdf.open(ASSETS / name)
    assert doc.authenticate("wrong") == 0
    assert doc.is_encrypted
    assert doc.authenticate("userpw") == 2
    assert not doc.is_encrypted
    assert doc.needs_pass
    assert doc.page_count == 2
    assert "Encrypted page two" in doc.get_page_text(1)
    # 復号済みなら再認証は「認証不要」の 1 を返す
    assert doc.authenticate("anything") == 1


@USER
def test_authenticate_owner_password_code(name: str) -> None:
    doc = pylopdf.open(ASSETS / name)
    assert doc.authenticate("ownerpw") == 4
    assert doc.page_count == 2


@USER
def test_authenticate_from_stream(name: str) -> None:
    doc = pylopdf.open(stream=(ASSETS / name).read_bytes())
    assert doc.authenticate("userpw") == 2
    assert doc.page_count == 2


@pytest.mark.parametrize("password", [None, "", "ownerpw", "wrong"])
def test_owner_only_opens_transparently(password: str | None) -> None:
    """user password 空（権限制限のみ）の PDF は password 引数にかかわらず認証不要。"""
    doc = pylopdf.open(ASSETS / "owneronly-aes-256.pdf", password=password)
    assert not doc.needs_pass
    assert not doc.is_encrypted
    assert doc.page_count == 2
    assert "Encrypted page one" in doc.get_page_text(0)


def test_empty_page_lists_reject_unauthenticated_pdf() -> None:
    doc = pylopdf.open(ASSETS / "user-aes-256.pdf")
    with pytest.raises(ValueError, match="暗号化された PDF"):
        doc.delete_pages([])
    with pytest.raises(ValueError, match="暗号化された PDF"):
        doc.select([])


def test_decrypted_save_produces_plain_pdf() -> None:
    """復号 → 編集 → 保存で、暗号化の外れた通常 PDF ができる。"""
    doc = pylopdf.open(ASSETS / "user-aes-256.pdf", password="userpw")
    doc.select([0])
    reopened = pylopdf.open(stream=doc.tobytes())
    assert not reopened.needs_pass
    assert not reopened.is_encrypted
    assert reopened.page_count == 1
    assert "Encrypted page one" in reopened.get_page_text(0)


def test_plain_pdf_reports_not_encrypted(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    assert not doc.needs_pass
    assert not doc.is_encrypted
    assert doc.authenticate("whatever") == 1
