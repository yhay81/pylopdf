"""AcroForm（Document.get_form_fields / set_form_field）のテスト。"""

from __future__ import annotations

import pytest

import pylopdf


def _build_form_pdf() -> bytes:
    """テキスト・チェックボックス・ネストしたテキストを持つ最小 AcroForm PDF。"""
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 8 0 R >>",
        2: "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
        3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: "<< /Type /Page /Parent 2 0 R /Annots [9 0 R 10 0 R 14 0 R] >>",
        8: "<< /Fields [9 0 R 10 0 R 13 0 R] /DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 3 0 R >> >> >>",
        # テキストフィールド（初期値付き）
        9: "<< /FT /Tx /T (customer) /V (initial) /Type /Annot /Subtype /Widget"
        " /Rect [50 700 250 720] /P 4 0 R /F 4 >>",
        # チェックボックス（Yes/Off の外観を持つ）
        10: "<< /FT /Btn /T (agree) /V /Off /AS /Off /Type /Annot /Subtype /Widget"
        " /Rect [50 660 70 680] /P 4 0 R /F 4 /AP << /N << /Yes 11 0 R /Off 12 0 R >> >> >>",
        11: "<< /Type /XObject /Subtype /Form /BBox [0 0 20 20] /Length 0 >>\nstream\n\nendstream",
        12: "<< /Type /XObject /Subtype /Form /BBox [0 0 20 20] /Length 0 >>\nstream\n\nendstream",
        # ネスト: person.first（FT は親から継承）
        13: "<< /T (person) /FT /Tx /Kids [14 0 R] >>",
        14: "<< /T (first) /Parent 13 0 R /Type /Annot /Subtype /Widget /Rect [50 620 250 640] /P 4 0 R /F 4 >>",
    }
    out = bytearray(b"%PDF-1.6\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in range(1, size):
        if num in offsets:
            out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
        else:
            out += b"0000000000 65535 f \n"
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return bytes(out)


def test_get_form_fields_lists_all() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    fields = {f["name"]: f for f in doc.get_form_fields()}
    assert set(fields) == {"customer", "agree", "person.first"}
    assert fields["customer"]["type"] == "text"
    assert fields["customer"]["value"] == "initial"
    assert fields["agree"]["type"] == "checkbox"
    assert fields["agree"]["value"] == "Off"
    assert fields["person.first"]["type"] == "text"  # FT は親から継承
    assert fields["person.first"]["value"] is None


def test_fill_text_field_roundtrip() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("customer", "山田 太郎")
    doc.set_form_field("person.first", "Taro")
    data = doc.tobytes()
    assert b"/NeedAppearances true" in data
    reopened = pylopdf.open(stream=data)
    fields = {f["name"]: f["value"] for f in reopened.get_form_fields()}
    assert fields["customer"] == "山田 太郎"
    assert fields["person.first"] == "Taro"


def test_fill_checkbox_with_bool() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("agree", True)
    fields = {f["name"]: f["value"] for f in doc.get_form_fields()}
    assert fields["agree"] == "Yes"  # AP の on 状態名を自動解決
    reopened = pylopdf.open(stream=doc.tobytes())
    assert {f["name"]: f["value"] for f in reopened.get_form_fields()}["agree"] == "Yes"

    doc.set_form_field("agree", False)
    assert {f["name"]: f["value"] for f in doc.get_form_fields()}["agree"] == "Off"


def test_form_errors() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    with pytest.raises(pylopdf.PdfError, match="見つかりません"):
        doc.set_form_field("nosuch", "x")
    with pytest.raises(ValueError, match="name"):
        doc.set_form_field("", "x")


def test_no_form_returns_empty() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    assert doc.get_form_fields() == []
