"""Tests for bounded XMP PDF/A self-claim inspection."""

from __future__ import annotations

import zlib

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def _xmp_pdf(xmp: str, *, filter_value: bytes | None = None) -> bytes:
    """Build one PDF with an indirect XMP metadata stream."""
    payload = xmp.encode()
    encoded = zlib.compress(payload) if filter_value in {b"/FlateDecode", b"/Fl"} else payload
    filter_entry = b"" if filter_value is None else b" /Filter " + filter_value
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /Metadata 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: (
                b"<< /Type /Metadata /Subtype /XML"
                + filter_entry
                + b" /Length "
                + str(len(encoded)).encode()
                + b" >>\nstream\n"
                + encoded
                + b"\nendstream"
            ),
        }
    )


def test_pdfa_claim_reads_exact_attribute_tokens() -> None:
    xmp = """\
<x:xmpmeta>
  <rdf:RDF>
    <rdf:Description
      notpdfaid:part="9"
      note=" pdfaid:part='8' "
      pdfaid:part = ' 2 '
      pdfaid:conformance=" B "/>
  </rdf:RDF>
</x:xmpmeta>
"""
    doc = pylopdf.open(_xmp_pdf(xmp))
    assert doc.pdfa_claim == pylopdf.PdfaClaim(part=2, conformance="B")


def test_pdfa_claim_reads_elements_without_matching_comments_or_cdata() -> None:
    xmp = """\
<x:xmpmeta>
  <!-- <pdfaid:part>9</pdfaid:part> -->
  <![CDATA[<pdfaid:part>8</pdfaid:part>]]>
  <notpdfaid:part>7</notpdfaid:part>
  <pdfaid:part >4</pdfaid:part>
</x:xmpmeta>
"""
    doc = pylopdf.open(_xmp_pdf(xmp))
    assert doc.pdfa_claim == pylopdf.PdfaClaim(part=4, conformance="")


def test_pdfa_claim_returns_none_without_an_exact_numeric_claim() -> None:
    assert pylopdf.open(build_pdf(["plain"])).pdfa_claim is None
    non_numeric = pylopdf.open(_xmp_pdf('<rdf:Description pdfaid:part="two"/>'))
    assert non_numeric.pdfa_claim is None


def test_pdfa_claim_bounds_compressed_xmp_before_materializing_it() -> None:
    xmp = '<rdf:Description pdfaid:part="3" pdfaid:conformance="U"/>' + " " * 2_000_000
    doc = pylopdf.open(_xmp_pdf(xmp, filter_value=b"/FlateDecode"))

    with pytest.raises(pylopdf.LimitError) as caught:
        _ = doc.pdfa_claim
    assert caught.value.code == "xmp_metadata_size"
    assert "1048576-byte" in str(caught.value)

    # The core takes the bound that the property fixes at 1 MiB.
    with pytest.raises(pylopdf.LimitError, match="1024-byte"):
        doc._doc.pdfa_claim(1024)  # noqa: SLF001
    assert doc._doc.pdfa_claim(len(xmp)) == (3, "U")  # noqa: SLF001
    assert doc._doc.pdfa_claim(None) == (3, "U")  # noqa: SLF001


def test_pdfa_claim_normalizes_filter_abbreviations_and_reports_decode_errors() -> None:
    xmp = '<rdf:Description pdfaid:part="2" pdfaid:conformance="A"/>'
    abbreviated = pylopdf.open(_xmp_pdf(xmp, filter_value=b"/Fl"))
    assert abbreviated.pdfa_claim == pylopdf.PdfaClaim(part=2, conformance="A")

    unsupported = pylopdf.open(_xmp_pdf(xmp, filter_value=b"/DCTDecode"))
    with pytest.raises(pylopdf.PdfError, match="failed to decode XMP metadata"):
        _ = unsupported.pdfa_claim

    malformed = pylopdf.open(_xmp_pdf(xmp, filter_value=b"123"))
    with pytest.raises(pylopdf.PdfError, match="XMP metadata has an invalid Filter"):
        _ = malformed.pdfa_claim


def test_pdfa_claim_is_a_record() -> None:
    claim = pylopdf.PdfaClaim(part=2, conformance="B")
    assert claim == pylopdf.PdfaClaim(part=2, conformance="B")
    assert repr(claim) == "PdfaClaim(part=2, conformance='B')"
    with pytest.raises(AttributeError):
        claim.part = 3  # ty: ignore[invalid-assignment]
