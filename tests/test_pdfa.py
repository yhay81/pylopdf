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
    doc = pylopdf.open(stream=_xmp_pdf(xmp))
    assert doc.get_pdfa_claim() == (2, "B")


def test_pdfa_claim_reads_elements_without_matching_comments_or_cdata() -> None:
    xmp = """\
<x:xmpmeta>
  <!-- <pdfaid:part>9</pdfaid:part> -->
  <![CDATA[<pdfaid:part>8</pdfaid:part>]]>
  <notpdfaid:part>7</notpdfaid:part>
  <pdfaid:part >4</pdfaid:part>
</x:xmpmeta>
"""
    doc = pylopdf.open(stream=_xmp_pdf(xmp))
    assert doc.get_pdfa_claim() == (4, "")


def test_pdfa_claim_returns_none_without_an_exact_numeric_claim() -> None:
    assert pylopdf.open(stream=build_pdf(["plain"])).get_pdfa_claim() is None
    non_numeric = pylopdf.open(
        stream=_xmp_pdf('<rdf:Description pdfaid:part="two"/>'),
    )
    assert non_numeric.get_pdfa_claim() is None


def test_pdfa_claim_bounds_compressed_xmp_before_materializing_it() -> None:
    xmp = '<rdf:Description pdfaid:part="3" pdfaid:conformance="U"/>' + " " * 2_000_000
    doc = pylopdf.open(stream=_xmp_pdf(xmp, filter_value=b"/FlateDecode"))

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.get_pdfa_claim(max_size=1024)
    assert caught.value.code == "xmp_metadata_size"
    assert "1024-byte" in str(caught.value)

    assert doc.get_pdfa_claim(max_size=len(xmp)) == (3, "U")
    assert doc.get_pdfa_claim(max_size=None) == (3, "U")


def test_pdfa_claim_normalizes_filter_abbreviations_and_reports_decode_errors() -> None:
    xmp = '<rdf:Description pdfaid:part="2" pdfaid:conformance="A"/>'
    abbreviated = pylopdf.open(stream=_xmp_pdf(xmp, filter_value=b"/Fl"))
    assert abbreviated.get_pdfa_claim() == (2, "A")

    unsupported = pylopdf.open(stream=_xmp_pdf(xmp, filter_value=b"/DCTDecode"))
    with pytest.raises(pylopdf.PdfError, match="failed to decode XMP metadata"):
        unsupported.get_pdfa_claim()

    malformed = pylopdf.open(stream=_xmp_pdf(xmp, filter_value=b"123"))
    with pytest.raises(pylopdf.PdfError, match="XMP metadata has an invalid Filter"):
        malformed.get_pdfa_claim()


@pytest.mark.parametrize("max_size", [True, 1.5, "1024"])
def test_pdfa_claim_rejects_non_integer_size_limits(max_size: object) -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    with pytest.raises(TypeError, match="max_size"):
        doc.get_pdfa_claim(max_size=max_size)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_size", [0, -1])
def test_pdfa_claim_rejects_non_positive_size_limits(max_size: int) -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    with pytest.raises(ValueError, match="max_size"):
        doc.get_pdfa_claim(max_size=max_size)
