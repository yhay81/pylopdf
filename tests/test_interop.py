"""エコシステム連携レシピ（README 記載）の統合テスト。

組版・新規文書の PDF/A 出力 = typst（typst-py）、電子署名 = pyHanko という
「自前実装せず連携で解決する」方針（ROADMAP）のレシピが実際に動くことを保証する。
interop dependency-group（`uv sync --group interop`）が無い環境では skip される。
"""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest

import pylopdf

typst = pytest.importorskip("typst", reason="interop グループ（typst）が未インストール")
pytest.importorskip("pyhanko", reason="interop グループ（pyhanko）が未インストール")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

TYP_SOURCE = b"""#set document(title: "pylopdf interop test")
= Hello pylopdf

Interop recipe test document.
"""


def test_typst_compile_to_pylopdf() -> None:
    # README レシピ: typst.compile() が返す PDF bytes を pylopdf.open(stream=) へ直結する
    pdf_bytes = typst.compile(TYP_SOURCE)
    assert isinstance(pdf_bytes, bytes)
    with pylopdf.open(stream=pdf_bytes) as doc:
        assert doc.page_count == 1
        assert "Hello pylopdf" in doc.get_page_text(0)


def test_typst_pdfa_output_opens() -> None:
    # 新規文書の PDF/A は typst 側（krilla の検証付き出力）に任せる。
    # PDF/A は XMP メタデータの非圧縮格納が必須なので、識別子が生バイトに現れる
    pdf_bytes = typst.compile(TYP_SOURCE, pdf_standards="a-2b")
    assert b"pdfaid" in pdf_bytes
    with pylopdf.open(stream=pdf_bytes) as doc:
        assert doc.page_count == 1
        assert "Hello pylopdf" in doc.get_page_text(0)


def _make_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    """テスト専用の自己署名証明書と秘密鍵を PEM で書き出す（cryptography は pyhanko の依存）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pylopdf interop test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = tmp_path / "key.pem"
    cert_path = tmp_path / "cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def test_pyhanko_signs_pylopdf_output_incrementally(tmp_path: Path) -> None:
    # README レシピ: pylopdf で作成・編集した PDF に pyHanko が増分更新で署名する。
    # 増分更新は既存バイト列に追記するだけなので、pylopdf の出力は署名後も無加工で残る
    doc = pylopdf.Document()
    doc.new_page()
    doc.set_metadata({"title": "pylopdf interop test"})
    original = doc.tobytes()

    key_path, cert_path = _make_self_signed_cert(tmp_path)
    signer = signers.SimpleSigner.load(str(key_path), str(cert_path))
    assert signer is not None
    out = signers.sign_pdf(
        IncrementalPdfFileWriter(io.BytesIO(original)),
        signers.PdfSignatureMetadata(field_name="Signature1"),
        signer=signer,
    )
    signed = out.getvalue()

    assert signed[: len(original)] == original
    assert b"/ByteRange" in signed
    with pylopdf.open(stream=signed) as reopened:
        assert reopened.page_count == 1
        assert reopened.metadata["title"] == "pylopdf interop test"
