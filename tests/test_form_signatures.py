"""Signature fields: their signed state and the form's signature flags."""

from __future__ import annotations

from conftest import build_raw_pdf

import pylopdf
from pylopdf import FieldType, SignatureField, SignatureFlags, TextField


def test_a_created_signature_field_is_unsigned() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 200))
    page.widgets.add(SignatureField("Approval", (20, 20, 200, 60)))
    page.widgets.add(TextField("name", (20, 80, 200, 100)))

    assert doc.form["Approval"].type is FieldType.SIGNATURE
    assert doc.form["Approval"].is_signed is False
    assert doc.form["name"].is_signed is False
    assert doc.form.signature_flags == SignatureFlags(0)
    assert pylopdf.Document().form.signature_flags == SignatureFlags(0)


def test_a_signature_value_marks_the_field_signed() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R 6 0 R] /SigFlags 3 >> >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [4 0 R 6 0 R] >>",
                4: "<< /FT /Sig /T (a) /V 5 0 R /Type /Annot /Subtype /Widget /Rect [10 10 90 30] >>",
                5: "<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached >>",
                6: "<< /FT /Sig /T (b) /V 7 0 R /Type /Annot /Subtype /Widget /Rect [10 40 90 60] >>",
            }
        )
    )

    assert doc.form["a"].is_signed is True
    # A reference to a missing object is no signature.
    assert doc.form["b"].is_signed is False
    assert doc.form.signature_flags == SignatureFlags.SIGNATURES_EXIST | SignatureFlags.APPEND_ONLY


def test_unknown_signature_flag_bits_are_dropped() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [] /SigFlags 13 >> >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
            }
        )
    )

    assert doc.form.signature_flags == SignatureFlags.SIGNATURES_EXIST
