"""Integration tests for the ecosystem recipes documented in the README.

Verify the roadmap policy of integrating rather than reimplementing: typst-py
for typesetting/new-document PDF/A and pyHanko for signatures. Tests skip when
the interop dependency group is absent.
"""

from __future__ import annotations

import datetime
import io
from pathlib import Path

import pytest

import pylopdf

typst = pytest.importorskip("typst", reason="interop group (typst) is not installed")
pytest.importorskip("pyhanko", reason="interop group (pyhanko) is not installed")

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
    # README recipe: pass typst.compile() PDF bytes directly to open(stream=).
    pdf_bytes = typst.compile(TYP_SOURCE)
    assert isinstance(pdf_bytes, bytes)
    with pylopdf.open(stream=pdf_bytes) as doc:
        assert doc.page_count == 1
        assert "Hello pylopdf" in doc.get_page_text(0)


def test_typst_pdfa_output_opens() -> None:
    # Let typst/krilla produce validated new-document PDF/A. PDF/A requires
    # uncompressed XMP metadata, so the identifier appears in raw bytes.
    pdf_bytes = typst.compile(TYP_SOURCE, pdf_standards="a-2b")
    assert b"pdfaid" in pdf_bytes
    with pylopdf.open(stream=pdf_bytes) as doc:
        assert doc.page_count == 1
        assert "Hello pylopdf" in doc.get_page_text(0)


def test_typst_bold_italic_flow_into_markdown() -> None:
    # Embedded font weight/italic metadata flows through span flags to emphasis.
    pdf = typst.compile(b'#set document(title: "t")\nNormal and *bold emphasis* and _italic part_ here.')
    with pylopdf.open(stream=pdf) as doc:
        span = doc.get_page_text(0, "dict")["blocks"][0]["lines"][0]["spans"][1]
        assert span["flags"] & 16  # Bold, using the pymupdf-compatible bit.
        assert "Bold" in span["font"]
        md = doc.to_markdown()
    assert "**bold emphasis**" in md
    assert "*italic part*" in md


def test_pdfa_claim_reads_typst_output() -> None:
    # get_pdfa_claim reads typst/krilla's validated PDF/A declaration.
    pdf_a = typst.compile(TYP_SOURCE, pdf_standards="a-2b")
    with pylopdf.open(stream=pdf_a) as doc:
        assert doc.get_pdfa_claim() == (2, "B")
    plain = typst.compile(TYP_SOURCE)
    with pylopdf.open(stream=plain) as doc:
        assert doc.get_pdfa_claim() is None


def test_typst_japanese_watermark_via_show_pdf_page() -> None:
    """Apply a CJK watermark built with typst and fonts-cjk to every page.

    typst subset-embeds the font, so watermark text extracts and renders without
    pylopdf's CJK fallback.
    """
    fonts = pytest.importorskip("pylopdf_fonts_cjk", reason="cjk extra is not installed")
    stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
    stamp_pdf = typst.compile(stamp_typ.encode(), font_paths=[str(fonts.sans_path().parent)])
    stamp = pylopdf.open(stream=stamp_pdf)

    doc = pylopdf.Document()
    doc.new_page()  # A4-sized default page.
    page = doc[0]
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)

    # Vector overlay leaves the CJK watermark directly extractable.
    assert "社外秘" in page.get_text()
    # fill:none keeps the page transparent; corners stay white on a white render.
    pix = page.get_pixmap(background=(255, 255, 255))
    assert tuple(pix.samples[0:3]) == (255, 255, 255)


def _make_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    """Write a test-only self-signed certificate and private key as PEM."""
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
    # README recipe: pyHanko incrementally signs pylopdf output. Incremental
    # updates append bytes, preserving pylopdf's complete output unchanged.
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
