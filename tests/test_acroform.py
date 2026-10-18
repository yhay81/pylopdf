"""Tests for AcroForm field reading and writing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import build_raw_pdf

import pylopdf

NOTO_SANS_JP = (
    Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
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
    return _build_sparse_pdf(objects)


def _build_sparse_pdf(objects: dict[int, str]) -> bytes:
    """Serialize objects, writing a free cross-reference entry for each absent number."""
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


def _build_button_state_pdf(states: str, *, indirect_kids: bool = False) -> bytes:
    """Build one checkbox with a caller-provided normal-state dictionary."""
    kids = "8 0 R" if indirect_kids else "[6 0 R]"
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [6 0 R] >>",
        4: "<< /Fields [5 0 R] >>",
        5: f"<< /FT /Btn /T (button) /V /Off /Kids {kids} >>",
        6: (
            "<< /Parent 5 0 R /Type /Annot /Subtype /Widget /Rect [10 10 30 30]"
            f" /AS /Off /AP << /N << {states} >> >> >>"
        ),
        7: _form_stream(""),
    }
    if indirect_kids:
        objects[8] = "[6 0 R]"
    return build_raw_pdf(objects)


def _opaque_pixels(doc: pylopdf.Document, clip: tuple[float, float, float, float]) -> int:
    pix = doc[0].get_pixmap(clip=clip)
    return sum(alpha != 0 for alpha in pix.samples[3::4])


# Object 6 is an indirect null. Number 7 is a free cross-reference entry below
# the highest object, so its reference stays missing through loading, saving,
# and reloading. ISO 32000-2 §7.3.7 reads a null value as an omitted key, and
# §7.3.10 reads a reference to a missing object as null.
NULL_EQUIVALENTS = {"null": "null", "indirect-null": "6 0 R", "missing": "7 0 R"}
null_equivalents = pytest.mark.parametrize("value", list(NULL_EQUIVALENTS.values()), ids=list(NULL_EQUIVALENTS))


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
    monkeypatch.setattr(pylopdf, "_bundled_font_providers", lambda: ())
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


def test_fill_unicode_text_auto_uses_optional_jp_font(monkeypatch: pytest.MonkeyPatch) -> None:
    font_data = NOTO_SANS_JP.read_bytes()
    provider = SimpleNamespace(language="ja", extra="jp", sans=font_data, serif=font_data)
    monkeypatch.setattr(pylopdf, "_bundled_font_providers", lambda: (provider,))
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


def test_fill_multiline_text_field_bounds_layout_lines_atomically() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    before = doc.tobytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.set_form_field("notes", "\n" * 4096)

    assert caught.value.code == "text_line_count"
    assert doc.tobytes() == before


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


def test_state_appearance_render_snapshot_obeys_interpretation_budget() -> None:
    doc = pylopdf.open(
        stream=_build_form_pdf(),
        limits=pylopdf.DocumentLimits(max_interpretation_size=1),
    )
    doc.set_form_field("agree", True)
    with pytest.raises(pylopdf.LimitError) as error:
        doc[0].get_pixmap()
    assert error.value.code == "interpretation_size"
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["agree"] == "Yes"


def test_state_appearance_survives_bounded_snapshot_reload() -> None:
    doc = pylopdf.open(
        stream=_build_form_pdf(),
        limits=pylopdf.DocumentLimits(max_interpretation_size=1024 * 1024),
    )
    doc.set_form_field("agree", True)

    assert _opaque_pixels(doc, (45, 107, 75, 137)) > 0


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


def test_form_font_input_boundary_preserves_field() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    font_size = NOTO_SANS_JP.stat().st_size

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.set_form_field("customer", "updated", fontfile=NOTO_SANS_JP, max_font_size=font_size - 1)

    assert caught.value.code == "font_input_size"
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["customer"] == "initial"

    doc.set_form_field("customer", "updated", fontfile=NOTO_SANS_JP, max_font_size=font_size)
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["customer"] == "updated"


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


def test_form_field_duplicate_reference_is_visited_once() -> None:
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Fields [5 0 R 5 0 R] >>",
            5: "<< /FT /Tx /T (field) /V (value) >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    assert doc.get_form_fields() == [{"name": "field", "type": "text", "value": "value"}]


def test_form_field_tree_rejects_excessive_edges_atomically() -> None:
    fields = "5 0 R " * 8193
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: f"<< /Fields [{fields}] >>",
            5: "<< /FT /Tx /T (field) /V (value) >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
        doc.get_form_fields()
    with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
        doc.set_form_field("field", "updated")
    assert doc.tobytes() == before


def test_form_field_tree_rejects_excessive_nodes_without_partial_output() -> None:
    field_ids = range(5, 4101)
    fields = " ".join(f"{object_id} 0 R" for object_id in field_ids)
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
        4: f"<< /Fields [{fields}] >>",
    }
    for object_id in range(5, 4100):
        objects[object_id] = f"<< /FT /Tx /T (field{object_id}) >>"
    objects[4100] = "<< /FT /Tx /T (parent) /Kids [4101 0 R] >>"
    objects[4101] = "<< /T (child) >>"

    doc = pylopdf.open(stream=build_raw_pdf(objects))
    with pytest.raises(pylopdf.PdfError, match="4096-node safety limit"):
        doc.get_form_fields()


def test_form_field_tree_rejects_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
        4: "<< /Fields [5 0 R] >>",
    }
    for object_id in range(5, 69):
        objects[object_id] = f"<< /FT /Tx /T (n{object_id}) /Kids [{object_id + 1} 0 R] >>"
    objects[69] = "<< /FT /Tx /T (leaf) >>"

    doc = pylopdf.open(stream=build_raw_pdf(objects))
    with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
        doc.get_form_fields()


def test_form_field_tree_rejects_excessive_name_text() -> None:
    name = "x" * (1024 * 1024 + 1)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Fields [5 0 R] >>",
            5: f"<< /FT /Tx /T ({name}) >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    with pytest.raises(pylopdf.PdfError, match=r"field-name.*1048576-byte safety limit"):
        doc.get_form_fields()


def test_form_field_tree_rejects_excessive_value_items() -> None:
    values = "(x) " * 4097
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Fields [5 0 R] >>",
            5: f"<< /FT /Ch /T (choice) /V [{values}] >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    with pytest.raises(pylopdf.PdfError, match="4096-item safety limit"):
        doc.get_form_fields()


def test_form_field_tree_rejects_excessive_value_text() -> None:
    value = "x" * (1024 * 1024 + 1)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Fields [5 0 R] >>",
            5: f"<< /FT /Tx /T (field) /V ({value}) >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    with pytest.raises(pylopdf.PdfError, match=r"field-value.*1048576-byte safety limit"):
        doc.get_form_fields()


def test_set_form_field_rejects_excessive_value_text_atomically() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    before = doc.tobytes()
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.set_form_field(
            "customer",
            "x" * (1024 * 1024 + 1),
            fontfile="missing-font.otf",
        )
    assert caught.value.code == "form_field_input_size"
    assert doc.tobytes() == before


def test_set_form_field_rejects_excessive_name_before_button_lookup_atomically() -> None:
    doc = pylopdf.open(stream=_build_form_pdf())
    before = doc.tobytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.set_form_field("é" * (512 * 1024 + 1), True)

    assert caught.value.code == "form_field_input_size"
    assert doc.tobytes() == before


def test_form_field_tree_bounds_inherited_returned_values() -> None:
    value = "x" * 1024
    child_ids = range(6, 1031)
    kids = " ".join(f"{object_id} 0 R" for object_id in child_ids)
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
        4: "<< /Fields [5 0 R] >>",
        5: f"<< /FT /Tx /T (root) /V ({value}) /Kids [{kids}] >>",
    }
    for object_id in child_ids:
        objects[object_id] = f"<< /T (child{object_id}) >>"

    doc = pylopdf.open(stream=build_raw_pdf(objects))
    with pytest.raises(pylopdf.PdfError, match=r"returned field-value.*1048576-byte safety limit"):
        doc.get_form_fields()


def test_form_field_tree_rejects_excessive_widgets_atomically() -> None:
    widgets = "6 0 R " * 4097
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Fields [5 0 R] >>",
            5: f"<< /FT /Btn /T (button) /Kids [{widgets}] >>",
            6: "<< /Parent 5 0 R /Type /Annot /Subtype /Widget /Rect [10 10 30 30] >>",
        }
    )
    doc = pylopdf.open(stream=pdf)
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match="4096-widget safety limit"):
        doc.get_form_fields()
    with pytest.raises(pylopdf.PdfError, match="4096-widget safety limit"):
        doc.set_form_field("button", "Yes")
    assert doc.tobytes() == before


def test_form_button_states_reject_excessive_entries_atomically() -> None:
    states = " ".join(f"/S{index} 7 0 R" for index in range(8193))
    doc = pylopdf.open(stream=_build_button_state_pdf(states))
    assert doc.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Off"}]
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match="8192-entry safety limit"):
        doc.set_form_field("button", True)
    with pytest.raises(pylopdf.PdfError, match="8192-entry safety limit"):
        doc.set_form_field("button", "S0")
    assert doc.tobytes() == before


def test_form_button_states_reject_excessive_unique_names() -> None:
    states = " ".join(f"/S{index} 7 0 R" for index in range(4097))
    doc = pylopdf.open(stream=_build_button_state_pdf(states))
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match="4096-name safety limit"):
        doc.set_form_field("button", True)
    assert doc.tobytes() == before


def test_form_button_states_reject_excessive_name_bytes() -> None:
    states = " ".join(f"/S{index}{'x' * 1024} 7 0 R" for index in range(1024))
    doc = pylopdf.open(stream=_build_button_state_pdf(states))
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match=r"button-state name.*1048576-byte safety limit"):
        doc.set_form_field("button", True)
    with pytest.raises(pylopdf.PdfError, match=r"button-state name.*1048576-byte safety limit"):
        doc.set_form_field("button", "S0")
    assert doc.tobytes() == before


def test_form_button_state_update_refuses_over_limit_output_atomically() -> None:
    states = " ".join(f"/S{index} 7 0 R" for index in range(8192))
    doc = pylopdf.open(stream=_build_button_state_pdf(states))
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match="update exceeds the 8192-entry safety limit"):
        doc.set_form_field("button", "S0")
    assert doc.tobytes() == before


def test_form_button_supports_indirect_kids_array() -> None:
    doc = pylopdf.open(stream=_build_button_state_pdf("/Off 7 0 R /Yes 7 0 R", indirect_kids=True))
    doc.set_form_field("button", True)
    assert doc.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Yes"}]


GREEN = (0, 255, 0)
RED = (255, 0, 0)
SOLID_BUTTON_STATES = {20: _form_stream("0 1 0 rg 0 0 20 20 re f"), 21: _form_stream("1 0 0 rg 0 0 20 20 re f")}


def _build_widget_kids_pdf(field: str, kids: list[str], extra: dict[int, str] | None = None) -> bytes:
    """Build top-level field 5 whose widget kids 8, 9, and so on add the `kids` entries.

    Kid `index` covers an 80-by-30 rectangle at x = 10 + 100 * index on a
    300-by-100 page. Object 6 is null and number 7 stays free, as
    `NULL_EQUIVALENTS` expects.
    """
    numbers = range(8, 8 + len(kids))
    refs = " ".join(f"{number} 0 R" for number in numbers)
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Annots [{refs}] >>",
        4: "<< /Fields [5 0 R] >>",
        5: f"<< {field} /Kids [{refs}] >>",
        6: "null",
        **(extra or {}),
    }
    for index, (number, entries) in enumerate(zip(numbers, kids, strict=True)):
        left = 10 + 100 * index
        objects[number] = (
            f"<< /Parent 5 0 R {entries} /Type /Annot /Subtype /Widget"
            f" /Rect [{left} 10 {left + 80} 40] /P 3 0 R /F 4 >>"
        )
    return _build_sparse_pdf(objects)


def _kid_ink(doc: pylopdf.Document) -> list[int]:
    """Count the opaque pixels inside each of the three kid widgets."""
    return [_opaque_pixels(doc, (10 + 100 * index, 60, 90 + 100 * index, 90)) for index in range(3)]


def _kid_colors(doc: pylopdf.Document) -> list[tuple[int, ...]]:
    """Sample the RGB color at the center of each of the three kid widgets."""
    pix = doc[0].get_pixmap()
    centers = ((75 * pix.width + 50 + 100 * index) * pix.n for index in range(3))
    return [tuple(pix.samples[center : center + 3]) for center in centers]


@null_equivalents
def test_null_named_kids_are_widgets_of_their_text_field(value: str) -> None:
    """Kids whose `/T` is null-equivalent are widgets of their parent field, as kids without `/T` are.

    Each such kid was an unnamed child field with its parent's full name, so
    the field was listed once per kid and a fill drew only the kid with the
    lowest object number.
    """
    source = pylopdf.open(stream=_build_widget_kids_pdf("/FT /Tx /T (name) /V (old)", [f"/T {value}"] * 3))

    for doc in (source, pylopdf.open(stream=source.tobytes())):
        assert doc.get_form_fields() == [{"name": "name", "type": "text", "value": "old"}]
        assert _kid_ink(doc) == [0, 0, 0]
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(stream=doc.tobytes())):
            assert current.get_form_fields() == [{"name": "name", "type": "text", "value": "new"}]
            ink = _kid_ink(current)
            assert ink[0] > 0
            assert ink == [ink[0]] * 3
            assert b"/NeedAppearances false" in current.tobytes()


@null_equivalents
def test_null_named_kids_follow_their_checkbox_state(value: str) -> None:
    """Checkbox fills synchronize `/AS` on every kid whose `/T` is null-equivalent."""
    states = "/AS /Off /AP << /N << /Yes 20 0 R /Off 21 0 R >> >>"
    pdf = _build_widget_kids_pdf("/FT /Btn /T (agree) /V /Off", [f"/T {value} {states}"] * 3, SOLID_BUTTON_STATES)
    source = pylopdf.open(stream=pdf)

    for doc in (source, pylopdf.open(stream=source.tobytes())):
        assert doc.get_form_fields() == [{"name": "agree", "type": "checkbox", "value": "Off"}]
        assert _kid_colors(doc) == [RED] * 3
        for checked, state, color in ((True, "Yes", GREEN), (False, "Off", RED)):
            doc.set_form_field("agree", checked)
            for current in (doc, pylopdf.open(stream=doc.tobytes())):
                assert current.get_form_fields() == [{"name": "agree", "type": "checkbox", "value": state}]
                assert _kid_colors(current) == [color] * 3


@null_equivalents
def test_null_named_kids_follow_their_radio_state(value: str) -> None:
    """Radio fills turn on the matching kid and turn off the other kids whose `/T` is null-equivalent."""
    kids = [f"/T {value} /AS /Off /AP << /N << /{state} 20 0 R /Off 21 0 R >> >>" for state in ("A", "B", "C")]
    pdf = _build_widget_kids_pdf("/FT /Btn /Ff 32768 /T (plan) /V /Off", kids, SOLID_BUTTON_STATES)
    source = pylopdf.open(stream=pdf)

    for doc in (source, pylopdf.open(stream=source.tobytes())):
        assert doc.get_form_fields() == [{"name": "plan", "type": "radio", "value": "Off"}]
        doc.set_form_field("plan", "B")
        for current in (doc, pylopdf.open(stream=doc.tobytes())):
            assert current.get_form_fields() == [{"name": "plan", "type": "radio", "value": "B"}]
            assert _kid_colors(current) == [RED, GREEN, RED]


@pytest.mark.parametrize(
    ("name", "extra"),
    [("5", {}), ("/name", {}), ("20 0 R", {20: "20 0 R"})],
    ids=["number", "name-object", "reference-cycle"],
)
def test_unusable_kid_names_still_make_child_fields(name: str, extra: dict[int, str]) -> None:
    """Only null-equivalent `/T` values are omitted; any other value makes the kid a child field.

    Such a child field has its parent's full name and reads its own value.
    """
    pdf = _build_widget_kids_pdf("/FT /Tx /T (name) /V (parent)", [f"/T {name} /V (kid)"], extra)
    source = pylopdf.open(stream=pdf)

    for doc in (source, pylopdf.open(stream=source.tobytes())):
        assert doc.get_form_fields() == [{"name": "name", "type": "text", "value": "kid"}]


def _build_widget_budget_pdf(kids: str, widget_entries: str = "") -> bytes:
    """Build checkbox 5 with the `kids` entries and widget 8; object 6 is null and number 7 stays free."""
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [8 0 R] >>",
        4: "<< /Fields [5 0 R] >>",
        5: f"<< /FT /Btn /T (button) /V /Off /Kids [{kids}] >>",
        6: "null",
        8: (
            f"<< /Parent 5 0 R {widget_entries} /Type /Annot /Subtype /Widget /Rect [10 10 30 30]"
            " /AS /Off /AP << /N << /Yes 9 0 R /Off 9 0 R >> >> >>"
        ),
        9: _form_stream("0 0 20 20 re f"),
    }
    return _build_sparse_pdf(objects)


@null_equivalents
def test_null_kid_entries_cost_only_their_edge(value: str) -> None:
    """Null-equivalent `/Kids` entries are skipped alike instead of counting as widgets.

    An indirect null and a missing object counted toward the 4,096-widget
    limit, while a null entry was skipped. Every entry still counts toward the
    8,192-edge limit, and the `/Fields` entry takes one edge.
    """
    widgets = "8 0 R " * 4096
    source = pylopdf.open(stream=_build_widget_budget_pdf(widgets + f"{value} " * 4095))

    for doc in (source, pylopdf.open(stream=source.tobytes())):
        assert doc.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Off"}]
        doc.set_form_field("button", True)
        for current in (doc, pylopdf.open(stream=doc.tobytes())):
            assert current.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Yes"}]

    over_limit = pylopdf.open(stream=_build_widget_budget_pdf(widgets + f"{value} " * 4096))
    for doc in (over_limit, pylopdf.open(stream=over_limit.tobytes())):
        before = doc.tobytes()
        with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
            doc.get_form_fields()
        with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
            doc.set_form_field("button", "Yes")
        assert doc.tobytes() == before


@null_equivalents
def test_null_named_kids_count_toward_the_widget_limit(value: str) -> None:
    """Kids whose `/T` is null-equivalent count toward the 4,096-widget limit like other widgets.

    As a child field, such a kid was visited once however often `/Kids`
    referenced it.
    """
    source = pylopdf.open(stream=_build_widget_budget_pdf("8 0 R " * 4097, f"/T {value}"))

    for doc in (source, pylopdf.open(stream=source.tobytes())):
        before = doc.tobytes()
        with pytest.raises(pylopdf.PdfError, match="4096-widget safety limit"):
            doc.get_form_fields()
        with pytest.raises(pylopdf.PdfError, match="4096-widget safety limit"):
            doc.set_form_field("button", "Yes")
        assert doc.tobytes() == before


@pytest.mark.parametrize("kind", list(NULL_EQUIVALENTS))
def test_null_field_entries_cost_only_their_edge(kind: str) -> None:
    """Null-equivalent `/Fields` entries are skipped alike instead of counting as tree nodes.

    Distinct indirect nulls and missing objects counted toward the 4,096-node
    limit, while null entries were skipped.
    """
    numbers = range(10, 4106)
    entries = "null " * len(numbers) if kind == "null" else " ".join(f"{number} 0 R" for number in numbers)
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
        4: f"<< /Fields [5 0 R {entries}] >>",
        5: "<< /FT /Tx /T (field) /V (value) >>",
        # An unreferenced object keeps the missing numbers below the highest one.
        5000: "<< >>",
    }
    if kind == "indirect-null":
        objects.update(dict.fromkeys(numbers, "null"))
    source = pylopdf.open(stream=_build_sparse_pdf(objects))

    for doc in (source, pylopdf.open(stream=source.tobytes())):
        assert doc.get_form_fields() == [{"name": "field", "type": "text", "value": "value"}]
