"""Tests for AcroForm field reading and writing."""

from __future__ import annotations

from pathlib import Path

import pytest

import pylopdf

NOTO_SANS_JP = (
    Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-cjk" / "src" / "pylopdf_fonts_cjk" / "NotoSansJP-Regular.otf"
)


def _form_stream(content: str) -> str:
    """Build an uncompressed Form XObject stream."""
    return (
        f"<< /Type /XObject /Subtype /Form /BBox [0 0 20 20] /Length {len(content.encode('ascii'))} >>"
        f"\nstream\n{content}\nendstream"
    )


def _build_form_pdf(*, checkbox_on_content: str = "", text_widget_extra: str = "") -> bytes:
    """Build text, choice, checkbox, radio, and nested AcroForm fields."""
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 8 0 R >>",
        2: "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
        3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: ("<< /Type /Page /Parent 2 0 R /Annots [9 0 R 10 0 R 14 0 R 15 0 R 17 0 R 18 0 R 23 0 R 25 0 R] >>"),
        8: (
            "<< /Fields [9 0 R 10 0 R 13 0 R 15 0 R 16 0 R 23 0 R 24 0 R]"
            " /DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 3 0 R >> >> >>"
        ),
        # Text field with an initial value.
        9: "<< /FT /Tx /T (customer) /V (initial) /Type /Annot /Subtype /Widget"
        f" /Rect [50 700 250 720] /P 4 0 R /F 4 {text_widget_extra} >>",
        # Checkbox with Yes and Off appearances.
        10: "<< /FT /Btn /T (agree) /V /Off /AS /Off /Type /Annot /Subtype /Widget"
        " /Rect [50 660 70 680] /P 4 0 R /F 4 /AP << /N << /Yes 11 0 R /Off 12 0 R >> >> >>",
        11: _form_stream(checkbox_on_content),
        12: _form_stream(""),
        # Nested person.first field inheriting FT from its parent.
        13: "<< /T (person) /FT /Tx /Kids [14 0 R] >>",
        14: "<< /T (first) /Parent 13 0 R /Type /Annot /Subtype /Widget /Rect [50 620 250 640] /P 4 0 R /F 4 >>",
        # Right-aligned combo box.
        15: (
            "<< /FT /Ch /Ff 131072 /Q 2 /T (level) /V (basic) /Opt [(basic) (pro)]"
            " /Type /Annot /Subtype /Widget /Rect [300 700 500 720] /P 4 0 R /F 4 >>"
        ),
        # Radio group with one export state per child widget.
        16: "<< /FT /Btn /Ff 32768 /T (plan) /V /Off /Kids [17 0 R 18 0 R] >>",
        17: (
            "<< /Parent 16 0 R /AS /Off /Type /Annot /Subtype /Widget"
            " /Rect [300 660 320 680] /P 4 0 R /F 4"
            " /AP << /N << /Pro 19 0 R /Off 20 0 R >> >> >>"
        ),
        18: (
            "<< /Parent 16 0 R /AS /Off /Type /Annot /Subtype /Widget"
            " /Rect [350 660 370 680] /P 4 0 R /F 4"
            " /AP << /N << /Basic 21 0 R /Off 22 0 R >> >> >>"
        ),
        19: _form_stream(""),
        20: _form_stream(""),
        21: _form_stream(""),
        22: _form_stream(""),
        23: ("<< /FT /Tx /Ff 4096 /T (notes) /Type /Annot /Subtype /Widget /Rect [50 540 250 600] /P 4 0 R /F 4 >>"),
        # Six-position centered comb field with inherited field attributes.
        24: "<< /FT /Tx /Ff 16777216 /MaxLen 6 /Q 1 /T (code) /Kids [25 0 R] >>",
        25: ("<< /Parent 24 0 R /Type /Annot /Subtype /Widget /Rect [300 540 500 565] /P 4 0 R /F 4 /BS << /W 0 >> >>"),
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


def _opaque_pixels(doc: pylopdf.Document, clip: tuple[float, float, float, float]) -> int:
    pix = doc[0].get_pixmap(clip=clip)
    return sum(alpha != 0 for alpha in pix.samples[3::4])


def test_get_form_fields_lists_all() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    fields = {f["name"]: f for f in doc.get_form_fields()}
    assert set(fields) == {"customer", "agree", "person.first", "level", "plan", "notes", "code"}
    assert fields["customer"]["type"] == "text"
    assert fields["customer"]["value"] == "initial"
    assert fields["agree"]["type"] == "checkbox"
    assert fields["agree"]["value"] == "Off"
    assert fields["person.first"]["type"] == "text"  # FT is inherited from the parent.
    assert fields["person.first"]["value"] is None
    assert fields["level"] == {"name": "level", "type": "combobox", "value": "basic"}
    assert fields["plan"]["type"] == "radio"
    assert fields["notes"]["type"] == "text"
    assert fields["code"]["type"] == "text"


def test_fill_text_field_roundtrip_without_unicode_font(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pylopdf, "_bundled_cjk_fonts", lambda: ())
    doc = pylopdf.open(stream=_build_form_pdf())
    assert _opaque_pixels(doc, (45, 67, 255, 97)) == 0
    doc.set_form_field("customer", "山田 太郎")
    doc.set_form_field("person.first", "Taro")
    data = doc.tobytes()
    assert b"/NeedAppearances true" in data
    reopened = pylopdf.open(stream=data)
    fields = {f["name"]: f["value"] for f in reopened.get_form_fields()}
    assert fields["customer"] == "山田 太郎"
    assert fields["person.first"] == "Taro"
    # The environment may omit the optional CJK font, but WinAnsi is always
    # represented by a native appearance and survives a save/reopen cycle.
    assert _opaque_pixels(reopened, (45, 147, 255, 177)) > 0


def test_fill_unicode_text_with_embedded_font_renders_and_roundtrips() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("customer", "山田 太郎", fontfile=NOTO_SANS_JP)
    assert _opaque_pixels(doc, (45, 67, 255, 97)) > 0
    data = doc.tobytes()
    assert b"/FontFile" in data
    assert b"/NeedAppearances false" in data
    reopened = pylopdf.open(stream=data)
    assert {field["name"]: field["value"] for field in reopened.get_form_fields()}["customer"] == "山田 太郎"
    assert _opaque_pixels(reopened, (45, 67, 255, 97)) > 0


def test_fill_unicode_text_auto_uses_optional_cjk_font(monkeypatch: pytest.MonkeyPatch) -> None:
    font_data = NOTO_SANS_JP.read_bytes()
    monkeypatch.setattr(pylopdf, "_bundled_cjk_fonts", lambda: (("sans", font_data),))
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("customer", "山田 太郎")
    assert _opaque_pixels(doc, (45, 67, 255, 97)) > 0
    assert b"/NeedAppearances false" in doc.tobytes()


def test_fill_choice_field_generates_appearance() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("level", "pro")
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["level"] == "pro"
    assert _opaque_pixels(doc, (295, 67, 505, 97)) > 0


def test_fill_multiline_text_field_wraps_and_renders() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    value = "A deliberately long first line that wraps inside the field.\nA second line."
    doc.set_form_field("notes", value)
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["notes"] == value
    assert _opaque_pixels(doc, (45, 187, 255, 257)) > 0


def test_fill_comb_field_centers_characters_and_enforces_maxlen() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("code", "A12")
    fields = {field["name"]: field["value"] for field in doc.get_form_fields()}
    assert fields["code"] == "A12"

    # Center alignment places three characters in slots 2–4 of the six-cell field.
    cell_width = 200 / 6
    counts = [
        _opaque_pixels(doc, (300 + index * cell_width, 227, 300 + (index + 1) * cell_width, 252)) for index in range(6)
    ]
    assert counts[0] == 0
    assert all(count > 0 for count in counts[1:4])
    assert counts[4:] == [0, 0]

    with pytest.raises(pylopdf.PdfError, match="exceeding MaxLen 6"):
        doc.set_form_field("code", "1234567")
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["code"] == "A12"


def test_fill_comb_field_supports_unicode_graphemes() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("code", "日A\u0301", fontfile=NOTO_SANS_JP)
    data = doc.tobytes()
    assert b"/NeedAppearances false" in data
    reopened = pylopdf.open(stream=data)
    assert {field["name"]: field["value"] for field in reopened.get_form_fields()}["code"] == "日A\u0301"
    assert _opaque_pixels(reopened, (300, 227, 500, 252)) > 0


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    [
        (b"/MaxLen 6", b"/MaxLen 0", "positive MaxLen"),
        (b"/Ff 16777216", b"/Ff 16781312", "cannot also be multiline"),
    ],
)
def test_malformed_comb_field_is_rejected_atomically(original: bytes, replacement: bytes, message: str) -> None:
    source = _build_form_pdf().replace(original, replacement)
    doc = pylopdf.open(stream=source)
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match=message):
        doc.set_form_field("code", "12")
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["code"] is None
    assert doc.tobytes() == before


def test_fill_checkbox_with_bool() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("agree", True)
    on_pixels = doc[0].get_pixmap(clip=(45, 107, 75, 137)).samples
    fields = {f["name"]: f["value"] for f in doc.get_form_fields()}
    assert fields["agree"] == "Yes"  # Resolve the AP on-state name automatically.
    data = doc.tobytes()
    assert b"/NeedAppearances false" in data
    reopened = pylopdf.open(stream=data)
    assert {f["name"]: f["value"] for f in reopened.get_form_fields()}["agree"] == "Yes"
    assert _opaque_pixels(reopened, (45, 107, 75, 137)) > 0

    doc.set_form_field("agree", False)
    assert {f["name"]: f["value"] for f in doc.get_form_fields()}["agree"] == "Off"
    assert doc[0].get_pixmap(clip=(45, 107, 75, 137)).samples != on_pixels


def test_fill_radio_selects_only_matching_widget() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    doc.set_form_field("plan", "Basic")
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["plan"] == "Basic"
    pro = _opaque_pixels(doc, (295, 107, 325, 137))
    basic = _opaque_pixels(doc, (345, 107, 375, 137))
    assert basic > pro > 0


def test_nonempty_custom_button_appearance_is_preserved() -> None:
    green_square = "0 1 0 rg 0 0 20 20 re f"
    doc = pylopdf.open(stream=_build_form_pdf(checkbox_on_content=green_square))
    doc.set_form_field("agree", True)
    pix = doc[0].get_pixmap(clip=(50, 112, 70, 132))
    center = (10 * pix.width + 10) * pix.n
    assert tuple(pix.samples[center : center + 3]) == (0, 255, 0)
    assert green_square.encode() in doc.tobytes()


def test_widget_rotation_background_and_alignment_render() -> None:
    extra = "/Q 2 /MK << /R 90 /BG [1 1 0] /BC [1 0 0] >> /BS << /W 2 >>"
    doc = pylopdf.open(stream=_build_form_pdf(text_widget_extra=extra))
    doc.set_form_field("customer", "right")
    data = doc.tobytes()
    assert b"/Matrix[0 1 -1 0 200 0]" in data
    assert _opaque_pixels(doc, (45, 67, 255, 97)) > 0


def test_invalid_embedded_font_does_not_partially_update_field() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    with pytest.raises(pylopdf.PdfError, match="font data"):
        doc.set_form_field("customer", "updated", fontbuffer=b"not a font")
    fields = {field["name"]: field["value"] for field in doc.get_form_fields()}
    assert fields["customer"] == "initial"
    assert _opaque_pixels(doc, (45, 67, 255, 97)) == 0


def test_form_errors() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    with pytest.raises(pylopdf.PdfError, match="not found"):
        doc.set_form_field("nosuch", "x")
    with pytest.raises(ValueError, match="name"):
        doc.set_form_field("", "x")
    with pytest.raises(ValueError, match="fontindex requires"):
        doc.set_form_field("customer", "x", fontindex=1)
    with pytest.raises(TypeError, match="string or bool"):
        doc.set_form_field("customer", 1)  # type: ignore[arg-type]


def test_no_form_returns_empty() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    assert doc.get_form_fields() == []
