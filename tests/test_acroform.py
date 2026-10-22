"""Tests for AcroForm field reading and writing."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TypeAlias

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
    pix = doc.pages[0].pixmap(clip=clip, alpha=True)
    return sum(alpha != 0 for alpha in pix.samples[3::4])


# Object 6 is an indirect null. Number 7 is a free cross-reference entry below
# the highest object, so its reference stays missing through loading, saving,
# and reloading. ISO 32000-2 §7.3.7 reads a null value as an omitted key, and
# §7.3.10 reads a reference to a missing object as null.
NULL_EQUIVALENTS = {"null": "null", "indirect-null": "6 0 R", "missing": "7 0 R"}
null_equivalents = pytest.mark.parametrize("value", list(NULL_EQUIVALENTS.values()), ids=list(NULL_EQUIVALENTS))
INHERITABLE_FIELD_KEYS = ("FT", "Ff", "MaxLen", "Q", "V")


def _build_inherited_comb_pdf(child: str, extra: dict[int, str] | None = None) -> bytes:
    """Build a comb field whose child field `code.inner` adds `child` entries.

    The child inherits the text type, comb flag, MaxLen 6, right alignment,
    and value of its parent `code`. Filling the sibling `other` completes the
    child's missing appearance from the value that it inherits.
    """
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 250 100] /Annots [8 0 R 9 0 R] >>",
        4: "<< /Fields [5 0 R 9 0 R] >>",
        5: "<< /FT /Tx /Ff 16777216 /MaxLen 6 /Q 2 /T (code) /V (P42) /Kids [8 0 R] >>",
        6: "null",
        8: (
            f"<< /T (inner) /Parent 5 0 R {child} /Type /Annot /Subtype /Widget"
            " /Rect [3 30 247 60] /P 3 0 R /F 4 /BS << /W 0 >> >>"
        ),
        9: "<< /FT /Tx /T (other) /Type /Annot /Subtype /Widget /Rect [3 70 247 95] /P 3 0 R /F 4 >>",
        **(extra or {}),
    }
    return _build_sparse_pdf(objects)


def _inner_comb_cells(doc: pylopdf.Document, count: int = 6) -> list[bool]:
    """Report which comb cells of `code.inner`, inside its 240-point content box, hold ink."""
    width = 240 / count
    return [_opaque_pixels(doc, (5 + index * width, 40, 5 + (index + 1) * width, 70)) > 0 for index in range(count)]


def _build_default_quadding_pdf(
    acroform: str,
    *,
    parent: str = "",
    child: str = "",
    extra: dict[int, str] | None = None,
    direct: bool = False,
) -> bytes:
    """Build comb, text, and choice fields without `/Q` beyond the given entries.

    The interactive form dictionary adds `acroform` entries and is a direct
    catalog value when `direct` is true. The comb field `code` adds `parent`
    entries, and its child `code.inner` adds `child` entries and inherits the
    comb flag, MaxLen 6, and value P42. The text field `other` and the combo
    box `level` hold xyz. No widget has an appearance or a border, so filling
    one field draws it and completes the appearances of the other two.
    """
    form = f"<< /Fields [5 0 R 9 0 R 10 0 R] {acroform} >>"
    objects = {
        1: f"<< /Type /Catalog /Pages 2 0 R /AcroForm {form if direct else '4 0 R'} >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 250 100] /Annots [8 0 R 9 0 R 10 0 R] >>",
        5: f"<< /FT /Tx /Ff 16777216 /MaxLen 6 /T (code) /V (P42) {parent} /Kids [8 0 R] >>",
        6: "null",
        8: (
            f"<< /T (inner) /Parent 5 0 R {child} /Type /Annot /Subtype /Widget"
            " /Rect [3 30 247 60] /P 3 0 R /F 4 /BS << /W 0 >> >>"
        ),
        9: (
            "<< /FT /Tx /T (other) /V (xyz) /Type /Annot /Subtype /Widget"
            " /Rect [3 70 247 95] /P 3 0 R /F 4 /BS << /W 0 >> >>"
        ),
        10: (
            "<< /FT /Ch /Ff 131072 /T (level) /V (xyz) /Opt [(xyz) (A12)] /Type /Annot /Subtype /Widget"
            " /Rect [3 3 247 27] /P 3 0 R /F 4 /BS << /W 0 >> >>"
        ),
        **(extra or {}),
    }
    if not direct:
        objects[4] = form
    return _build_sparse_pdf(objects)


def _quadding_ink(doc: pylopdf.Document) -> dict[str, tuple[bool, ...]]:
    """Report the inked comb cells of `code.inner` and the inked halves of `other` and `level`."""
    return {
        "code.inner": tuple(_inner_comb_cells(doc)),
        "other": tuple(_opaque_pixels(doc, (left, 5, left + 120, 30)) > 0 for left in (5, 125)),
        "level": tuple(_opaque_pixels(doc, (left, 73, left + 120, 97)) > 0 for left in (5, 125)),
    }


LEFT_ALIGNED = {"code.inner": (True, True, True, False, False, False), "other": (True, False), "level": (True, False)}
RIGHT_ALIGNED = {"code.inner": (False, False, False, True, True, True), "other": (False, True), "level": (False, True)}


def test_get_form_fields_lists_all() -> None:
    doc = pylopdf.open(_build_form_pdf())
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
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: ())
    doc = pylopdf.open(_build_form_pdf())
    assert _opaque_pixels(doc, (45, 67, 255, 97)) == 0
    doc.set_form_field("customer", "山田 太郎")
    doc.set_form_field("person.first", "Taro")
    data = doc.to_bytes()
    assert b"/NeedAppearances true" in data
    reopened = pylopdf.open(data)
    fields = {f["name"]: f["value"] for f in reopened.get_form_fields()}
    assert fields["customer"] == "山田 太郎"
    assert fields["person.first"] == "Taro"
    # The environment may omit the optional CJK font, but WinAnsi is always
    # represented by a native appearance and survives a save/reopen cycle.
    assert _opaque_pixels(reopened, (45, 147, 255, 177)) > 0


def test_fill_unicode_text_with_embedded_font_renders_and_roundtrips() -> None:
    doc = pylopdf.open(_build_form_pdf())
    doc.set_form_field("customer", "山田 太郎", fontfile=NOTO_SANS_JP)
    assert _opaque_pixels(doc, (45, 67, 255, 97)) > 0
    data = doc.to_bytes()
    assert b"/FontFile" in data
    assert b"/NeedAppearances false" in data
    reopened = pylopdf.open(data)
    assert {field["name"]: field["value"] for field in reopened.get_form_fields()}["customer"] == "山田 太郎"
    assert _opaque_pixels(reopened, (45, 67, 255, 97)) > 0


def test_fill_unicode_text_auto_uses_optional_jp_font(monkeypatch: pytest.MonkeyPatch) -> None:
    font_data = NOTO_SANS_JP.read_bytes()
    provider = SimpleNamespace(language="ja", extra="jp", sans=font_data, serif=font_data)
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: (provider,))
    doc = pylopdf.open(_build_form_pdf())
    doc.set_form_field("customer", "山田 太郎")
    assert _opaque_pixels(doc, (45, 67, 255, 97)) > 0
    assert b"/NeedAppearances false" in doc.to_bytes()


def test_fill_choice_field_generates_appearance() -> None:
    doc = pylopdf.open(_build_form_pdf())
    doc.set_form_field("level", "pro")
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["level"] == "pro"
    assert _opaque_pixels(doc, (295, 67, 505, 97)) > 0


def test_fill_multiline_text_field_wraps_and_renders() -> None:
    doc = pylopdf.open(_build_form_pdf())
    value = "A deliberately long first line that wraps inside the field.\nA second line."
    doc.set_form_field("notes", value)
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["notes"] == value
    assert _opaque_pixels(doc, (45, 187, 255, 257)) > 0


def test_fill_multiline_text_field_bounds_layout_lines_atomically() -> None:
    doc = pylopdf.open(_build_form_pdf())
    before = doc.to_bytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.set_form_field("notes", "\n" * 4096)

    assert caught.value.code == "text_line_count"
    assert doc.to_bytes() == before


def test_fill_comb_field_centers_characters_and_enforces_maxlen() -> None:
    doc = pylopdf.open(_build_form_pdf())
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
    doc = pylopdf.open(_build_form_pdf())
    doc.set_form_field("code", "日A\u0301", fontfile=NOTO_SANS_JP)
    data = doc.to_bytes()
    assert b"/NeedAppearances false" in data
    reopened = pylopdf.open(data)
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
    doc = pylopdf.open(source)
    before = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match=message):
        doc.set_form_field("code", "12")
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["code"] is None
    assert doc.to_bytes() == before


def test_missing_field_entry_stays_a_reference_after_saving() -> None:
    """Saving keeps number 7 free, so reloaded documents still read a missing object."""
    saved = pylopdf.open(_build_inherited_comb_pdf("/V 7 0 R")).to_bytes()

    assert re.search(rb"/V\s*7 0 R", saved)
    assert not re.search(rb"(?m)^7 0 obj", saved)


@null_equivalents
def test_null_child_field_entries_inherit_parent_values(value: str) -> None:
    """Null-equivalent child entries inherit like absent ones; `/V` read as no value."""
    entries = " ".join(f"/{key} {value}" for key in INHERITABLE_FIELD_KEYS)
    doc = pylopdf.open(_build_inherited_comb_pdf(entries))

    for current in (doc, pylopdf.open(doc.to_bytes())):
        assert current.get_form_fields() == [
            {"name": "code.inner", "type": "text", "value": "P42"},
            {"name": "other", "type": "text", "value": None},
        ]


@null_equivalents
@pytest.mark.parametrize("target", ["code.inner", "other"])
def test_comb_appearance_inherits_past_null_child_entries(value: str, target: str) -> None:
    """Comb layout and alignment use the parent's `/MaxLen` and `/Q` past null child entries.

    Filling the child raised for its null-equivalent `/MaxLen`, and a null
    `/Q` replaced the parent's right alignment with left alignment. Filling the
    sibling skipped the child's missing appearance instead of drawing the
    value that the child inherits.
    """
    entries = " ".join(f"/{key} {value}" for key in INHERITABLE_FIELD_KEYS)
    source = pylopdf.open(_build_inherited_comb_pdf(entries))

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field(target, "A12")
        with pytest.raises(pylopdf.PdfError, match="exceeding MaxLen 6"):
            doc.set_form_field("code.inner", "1234567")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            # Right alignment places three characters in the last three of six cells.
            assert _inner_comb_cells(current) == [False, False, False, True, True, True]


@null_equivalents
@pytest.mark.parametrize("target", ["code.inner", "other"])
def test_text_appearance_inherits_past_null_child_entries(value: str, target: str) -> None:
    """A single-line child draws right-aligned text past null-equivalent `/Q` and `/V`.

    A null `/Q` aligned the text left, and filling the sibling completed the
    child's missing appearance without the value that the child inherits.
    """
    source = pylopdf.open(_build_inherited_comb_pdf(f"/Ff 0 /Q {value} /V {value}"))

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field(target, "A12")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert _opaque_pixels(current, (5, 40, 125, 70)) == 0
            assert _opaque_pixels(current, (125, 40, 245, 70)) > 0


@pytest.mark.parametrize(
    ("entries", "extra"),
    [
        ("/MaxLen 4 /Q 0 /V (own)", {}),
        ("/MaxLen 10 0 R /Q 11 0 R /V 12 0 R", {10: "4", 11: "0", 12: "(own)"}),
    ],
    ids=["direct", "indirect"],
)
def test_present_child_field_entries_override_parent_values(entries: str, extra: dict[int, str]) -> None:
    """Only null-equivalent entries inherit; a present child value still wins."""
    doc = pylopdf.open(_build_inherited_comb_pdf(entries, extra))
    assert doc.get_form_fields()[0] == {"name": "code.inner", "type": "text", "value": "own"}

    doc.set_form_field("other", "x")

    for current in (doc, pylopdf.open(doc.to_bytes())):
        # Left alignment places three characters in the first three of four cells.
        assert _inner_comb_cells(current, 4) == [True, True, True, False]


@pytest.mark.parametrize("direct", [False, True], ids=["indirect", "direct"])
@pytest.mark.parametrize("target", ["code.inner", "other", "level"])
def test_appearances_default_to_acroform_quadding(target: str, *, direct: bool) -> None:
    """Fields without `/Q` on their `/Parent` chain use the AcroForm's `/Q`.

    ISO 32000-2 Table 225 makes the interactive form dictionary's `/Q` the
    document-wide default. Filling a field and completing the missing
    appearances of the others aligned comb, text, and choice values left.
    """
    source = pylopdf.open(_build_default_quadding_pdf("/Q 2", direct=direct))

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field(target, "A12")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert _quadding_ink(current) == RIGHT_ALIGNED


@null_equivalents
@pytest.mark.parametrize("target", ["code.inner", "other"])
def test_null_field_quadding_reaches_acroform_default(value: str, target: str) -> None:
    """Null-equivalent `/Q` entries on the `/Parent` chain fall through to the AcroForm's `/Q`."""
    pdf = _build_default_quadding_pdf("/Q 2", parent=f"/Q {value}", child=f"/Q {value}")
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field(target, "A12")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert _quadding_ink(current) == RIGHT_ALIGNED


@pytest.mark.parametrize(
    "value",
    [*NULL_EQUIVALENTS.values(), "3", "/Right"],
    ids=[*NULL_EQUIVALENTS, "out-of-range", "non-integer"],
)
@pytest.mark.parametrize("target", ["code.inner", "other"])
def test_null_or_unusable_acroform_quadding_aligns_left(value: str, target: str) -> None:
    """A null-equivalent AcroForm `/Q` reads as absent, and an unusable one aligns left."""
    source = pylopdf.open(_build_default_quadding_pdf(f"/Q {value}"))

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field(target, "A12")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert _quadding_ink(current) == LEFT_ALIGNED


@pytest.mark.parametrize(
    ("parent", "child", "extra"),
    [
        ("", "/Q 0", {}),
        ("", "/Q 11 0 R", {11: "0"}),
        ("/Q 0", "", {}),
        ("", "/Q 3", {}),
    ],
    ids=["field", "indirect", "parent", "out-of-range"],
)
@pytest.mark.parametrize("target", ["code.inner", "other"])
def test_field_quadding_overrides_acroform_default(parent: str, child: str, extra: dict[int, str], target: str) -> None:
    """A `/Q` on the `/Parent` chain, including `/Q 0`, wins over the AcroForm's `/Q`.

    An out-of-range field value still stops inheritance and aligns left. Fields
    without `/Q` keep the document default.
    """
    pdf = _build_default_quadding_pdf("/Q 2", parent=parent, child=child, extra=extra)
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field(target, "A12")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert _quadding_ink(current) == {**RIGHT_ALIGNED, "code.inner": LEFT_ALIGNED["code.inner"]}


def test_fill_checkbox_with_bool() -> None:
    doc = pylopdf.open(_build_form_pdf())
    doc.set_form_field("agree", True)
    on_pixels = doc.pages[0].pixmap(clip=(45, 107, 75, 137), alpha=True).samples
    fields = {f["name"]: f["value"] for f in doc.get_form_fields()}
    assert fields["agree"] == "Yes"  # Resolve the AP on-state name automatically.
    data = doc.to_bytes()
    assert b"/NeedAppearances false" in data
    reopened = pylopdf.open(data)
    assert {f["name"]: f["value"] for f in reopened.get_form_fields()}["agree"] == "Yes"
    assert _opaque_pixels(reopened, (45, 107, 75, 137)) > 0

    doc.set_form_field("agree", False)
    assert {f["name"]: f["value"] for f in doc.get_form_fields()}["agree"] == "Off"
    assert doc.pages[0].pixmap(clip=(45, 107, 75, 137), alpha=True).samples != on_pixels


def test_state_appearance_render_snapshot_obeys_interpretation_budget() -> None:
    doc = pylopdf.open(_build_form_pdf(), limits=pylopdf.DocumentLimits(max_interpretation_size=1))
    doc.set_form_field("agree", True)
    with pytest.raises(pylopdf.LimitError) as error:
        doc.pages[0].pixmap(alpha=True)
    assert error.value.code == "interpretation_size"
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["agree"] == "Yes"


def test_state_appearance_survives_bounded_snapshot_reload() -> None:
    doc = pylopdf.open(_build_form_pdf(), limits=pylopdf.DocumentLimits(max_interpretation_size=1024 * 1024))
    doc.set_form_field("agree", True)

    assert _opaque_pixels(doc, (45, 107, 75, 137)) > 0


def test_fill_radio_selects_only_matching_widget() -> None:
    doc = pylopdf.open(_build_form_pdf())
    doc.set_form_field("plan", "Basic")
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["plan"] == "Basic"
    pro = _opaque_pixels(doc, (295, 107, 325, 137))
    basic = _opaque_pixels(doc, (345, 107, 375, 137))
    assert basic > pro > 0


def test_nonempty_custom_button_appearance_is_preserved() -> None:
    green_square = "0 1 0 rg 0 0 20 20 re f"
    doc = pylopdf.open(_build_form_pdf(checkbox_on_content=green_square))
    doc.set_form_field("agree", True)
    pix = doc.pages[0].pixmap(clip=(50, 112, 70, 132), alpha=True)
    center = (10 * pix.width + 10) * pix.n
    assert tuple(pix.samples[center : center + 3]) == (0, 255, 0)
    assert green_square.encode() in doc.to_bytes()


def test_widget_rotation_background_and_alignment_render() -> None:
    extra = "/Q 2 /MK << /R 90 /BG [1 1 0] /BC [1 0 0] >> /BS << /W 2 >>"
    doc = pylopdf.open(_build_form_pdf(text_widget_extra=extra))
    doc.set_form_field("customer", "right")
    data = doc.to_bytes()
    assert b"/Matrix[0 1 -1 0 200 0]" in data
    assert _opaque_pixels(doc, (45, 67, 255, 97)) > 0


def test_invalid_embedded_font_does_not_partially_update_field() -> None:
    doc = pylopdf.open(_build_form_pdf())
    with pytest.raises(pylopdf.PdfError, match="font data"):
        doc.set_form_field("customer", "updated", fontbuffer=b"not a font")
    fields = {field["name"]: field["value"] for field in doc.get_form_fields()}
    assert fields["customer"] == "initial"
    assert _opaque_pixels(doc, (45, 67, 255, 97)) == 0


def test_form_font_input_is_opened_only_to_draw_text() -> None:
    doc = pylopdf.open(_build_form_pdf())

    doc.set_form_field("agree", True, fontbuffer=b"not a font")
    doc.set_form_field("customer", "", fontbuffer=b"not a font")

    fields = {field["name"]: field["value"] for field in doc.get_form_fields()}
    assert (fields["agree"], fields["customer"]) == ("Yes", "")


def test_form_font_input_boundary_preserves_field() -> None:
    doc = pylopdf.open(_build_form_pdf())
    font_size = NOTO_SANS_JP.stat().st_size

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.set_form_field("customer", "updated", fontfile=NOTO_SANS_JP, max_font_size=font_size - 1)

    assert caught.value.code == "font_input_size"
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["customer"] == "initial"

    doc.set_form_field("customer", "updated", fontfile=NOTO_SANS_JP, max_font_size=font_size)
    assert {field["name"]: field["value"] for field in doc.get_form_fields()}["customer"] == "updated"


def test_form_font_parameter_draws_the_same_bytes_as_font_input() -> None:
    """A loaded Font draws text and comb appearances exactly as its file or bytes do."""
    face = pylopdf.Font(fontfile=NOTO_SANS_JP)
    by_font, by_file, by_buffer = (pylopdf.open(_build_form_pdf()) for _ in range(3))

    for name, value in (("customer", "山田 太郎"), ("code", "日Á")):
        by_font.set_form_field(name, value, font=face)
        by_file.set_form_field(name, value, fontfile=NOTO_SANS_JP)
        by_buffer.set_form_field(name, value, fontbuffer=NOTO_SANS_JP.read_bytes())

    saved = by_font.to_bytes()
    assert saved == by_file.to_bytes() == by_buffer.to_bytes()
    assert b"/NeedAppearances false" in saved
    reopened = pylopdf.open(saved)
    fields = {field["name"]: field["value"] for field in reopened.get_form_fields()}
    assert (fields["customer"], fields["code"]) == ("山田 太郎", "日Á")
    assert _opaque_pixels(reopened, (45, 67, 255, 97)) > 0


def test_form_font_parameter_fills_buttons_and_empty_values() -> None:
    doc = pylopdf.open(_build_form_pdf())
    face = pylopdf.Font(fontfile=NOTO_SANS_JP)

    doc.set_form_field("agree", True, font=face)
    doc.set_form_field("plan", "Pro", font=face)
    doc.set_form_field("customer", "", font=face)

    fields = {field["name"]: field["value"] for field in doc.get_form_fields()}
    assert (fields["agree"], fields["plan"], fields["customer"]) == ("Yes", "Pro", "")
    # Only drawn text embeds the face.
    assert b"/FontFile" not in doc.to_bytes()


def test_form_font_parameter_failures_leave_the_document_unchanged() -> None:
    doc = pylopdf.open(_build_form_pdf())
    face = pylopdf.Font(fontfile=NOTO_SANS_JP)
    doc.set_form_field("code", "A12", font=face)
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="exceeding MaxLen 6"):
        doc.set_form_field("code", "1234567", font=face)
    with pytest.raises(pylopdf.PdfError, match=r"no glyph for U\+1F980"):
        doc.set_form_field("customer", "\U0001f980", font=face)

    assert doc.to_bytes() == before


def test_form_font_parameter_rejects_other_font_sources() -> None:
    doc = pylopdf.open(_build_form_pdf())
    face = pylopdf.Font(fontfile=NOTO_SANS_JP)
    before = doc.to_bytes()

    for extra in (
        {"fontfile": NOTO_SANS_JP},
        {"fontbuffer": b"font"},
        {"fontindex": 1},
        {"font_language": "ja"},
    ):
        with pytest.raises(ValueError, match="font cannot be combined"):
            doc.set_form_field("customer", "x", font=face, **extra)  # ty: ignore[invalid-argument-type]
        with pytest.raises(ValueError, match="font cannot be combined"):
            doc.set_form_field("agree", True, font=face, **extra)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match=r"pylopdf\.Font"):
        doc.set_form_field("customer", "x", font="helv")  # ty: ignore[invalid-argument-type]

    assert doc.to_bytes() == before


def test_form_font_parameter_refuses_standard_14_fonts() -> None:
    """Form appearances embed an OpenType face; WinAnsi text without ``font`` uses Helvetica."""
    doc = pylopdf.open(_build_form_pdf())
    before = doc.to_bytes()

    for name, value in (("customer", "x"), ("agree", True)):
        with pytest.raises(pylopdf.PdfError, match="not the Standard 14 font 'Times-Roman'"):
            doc.set_form_field(name, value, font=pylopdf.Font("tiro"))
    # The core entry point refuses one as well.
    with pytest.raises(pylopdf.PdfError, match="not a Standard 14 font"):
        doc._doc.set_form_field_font("customer", "x", pylopdf.Font("helv")._font)  # noqa: SLF001

    assert doc.to_bytes() == before


def test_form_errors() -> None:
    doc = pylopdf.open(_build_form_pdf())
    with pytest.raises(pylopdf.PdfError, match="not found"):
        doc.set_form_field("nosuch", "x")
    with pytest.raises(ValueError, match="name"):
        doc.set_form_field("", "x")
    with pytest.raises(ValueError, match="fontindex requires"):
        doc.set_form_field("customer", "x", fontindex=1)
    with pytest.raises(TypeError, match="string or bool"):
        doc.set_form_field("customer", 1)  # ty: ignore[invalid-argument-type]


def test_no_form_returns_empty() -> None:
    doc = pylopdf.Document()
    doc.pages.new()
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
    doc = pylopdf.open(pdf)
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
    doc = pylopdf.open(pdf)
    before = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
        doc.get_form_fields()
    with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
        doc.set_form_field("field", "updated")
    assert doc.to_bytes() == before


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

    doc = pylopdf.open(build_raw_pdf(objects))
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

    doc = pylopdf.open(build_raw_pdf(objects))
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
    doc = pylopdf.open(pdf)
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
    doc = pylopdf.open(pdf)
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
    doc = pylopdf.open(pdf)
    with pytest.raises(pylopdf.PdfError, match=r"field-value.*1048576-byte safety limit"):
        doc.get_form_fields()


def test_set_form_field_rejects_excessive_value_text_atomically() -> None:
    doc = pylopdf.open(_build_form_pdf())
    before = doc.to_bytes()
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.set_form_field(
            "customer",
            "x" * (1024 * 1024 + 1),
            fontfile="missing-font.otf",
        )
    assert caught.value.code == "form_field_input_size"
    assert doc.to_bytes() == before


def test_set_form_field_rejects_excessive_name_before_button_lookup_atomically() -> None:
    doc = pylopdf.open(_build_form_pdf())
    before = doc.to_bytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.set_form_field("é" * (512 * 1024 + 1), True)

    assert caught.value.code == "form_field_input_size"
    assert doc.to_bytes() == before


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

    doc = pylopdf.open(build_raw_pdf(objects))
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
    doc = pylopdf.open(pdf)
    before = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="4096-widget safety limit"):
        doc.get_form_fields()
    with pytest.raises(pylopdf.PdfError, match="4096-widget safety limit"):
        doc.set_form_field("button", "Yes")
    assert doc.to_bytes() == before


def test_form_button_states_reject_excessive_entries_atomically() -> None:
    states = " ".join(f"/S{index} 7 0 R" for index in range(8193))
    doc = pylopdf.open(_build_button_state_pdf(states))
    assert doc.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Off"}]
    before = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="8192-entry safety limit"):
        doc.set_form_field("button", True)
    with pytest.raises(pylopdf.PdfError, match="8192-entry safety limit"):
        doc.set_form_field("button", "S0")
    assert doc.to_bytes() == before


def test_form_button_states_reject_excessive_unique_names() -> None:
    states = " ".join(f"/S{index} 7 0 R" for index in range(4097))
    doc = pylopdf.open(_build_button_state_pdf(states))
    before = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="4096-name safety limit"):
        doc.set_form_field("button", True)
    assert doc.to_bytes() == before


def test_form_button_states_reject_excessive_name_bytes() -> None:
    states = " ".join(f"/S{index}{'x' * 1024} 7 0 R" for index in range(1024))
    doc = pylopdf.open(_build_button_state_pdf(states))
    before = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match=r"button-state name.*1048576-byte safety limit"):
        doc.set_form_field("button", True)
    with pytest.raises(pylopdf.PdfError, match=r"button-state name.*1048576-byte safety limit"):
        doc.set_form_field("button", "S0")
    assert doc.to_bytes() == before


def test_form_button_state_update_refuses_over_limit_output_atomically() -> None:
    states = " ".join(f"/S{index} 7 0 R" for index in range(8192))
    doc = pylopdf.open(_build_button_state_pdf(states))
    before = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="update exceeds the 8192-entry safety limit"):
        doc.set_form_field("button", "S0")
    assert doc.to_bytes() == before


def test_form_button_supports_indirect_kids_array() -> None:
    doc = pylopdf.open(_build_button_state_pdf("/Off 7 0 R /Yes 7 0 R", indirect_kids=True))
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


def _kid_ink(doc: pylopdf.Document, count: int = 3) -> list[int]:
    """Count the opaque pixels inside each of the first `count` kid widgets."""
    return [_opaque_pixels(doc, (10 + 100 * index, 60, 90 + 100 * index, 90)) for index in range(count)]


def _kid_colors(doc: pylopdf.Document, count: int = 3) -> list[tuple[int, ...]]:
    """Sample the RGB color at the center of each of the first `count` kid widgets."""
    pix = doc.pages[0].pixmap(alpha=True)
    centers = ((75 * pix.width + 50 + 100 * index) * pix.n for index in range(count))
    return [tuple(pix.samples[center : center + 3]) for center in centers]


@null_equivalents
def test_null_named_kids_are_widgets_of_their_text_field(value: str) -> None:
    """Kids whose `/T` is null-equivalent are widgets of their parent field, as kids without `/T` are.

    Each such kid was an unnamed child field with its parent's full name, so
    the field was listed once per kid and a fill drew only the kid with the
    lowest object number.
    """
    source = pylopdf.open(_build_widget_kids_pdf("/FT /Tx /T (name) /V (old)", [f"/T {value}"] * 3))

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "name", "type": "text", "value": "old"}]
        assert _kid_ink(doc) == [0, 0, 0]
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert current.get_form_fields() == [{"name": "name", "type": "text", "value": "new"}]
            ink = _kid_ink(current)
            assert ink[0] > 0
            assert ink == [ink[0]] * 3
            assert b"/NeedAppearances false" in current.to_bytes()


@null_equivalents
def test_null_named_kids_follow_their_checkbox_state(value: str) -> None:
    """Checkbox fills synchronize `/AS` on every kid whose `/T` is null-equivalent."""
    states = "/AS /Off /AP << /N << /Yes 20 0 R /Off 21 0 R >> >>"
    pdf = _build_widget_kids_pdf("/FT /Btn /T (agree) /V /Off", [f"/T {value} {states}"] * 3, SOLID_BUTTON_STATES)
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "agree", "type": "checkbox", "value": "Off"}]
        assert _kid_colors(doc) == [RED] * 3
        for checked, state, color in ((True, "Yes", GREEN), (False, "Off", RED)):
            doc.set_form_field("agree", checked)
            for current in (doc, pylopdf.open(doc.to_bytes())):
                assert current.get_form_fields() == [{"name": "agree", "type": "checkbox", "value": state}]
                assert _kid_colors(current) == [color] * 3


@null_equivalents
def test_null_named_kids_follow_their_radio_state(value: str) -> None:
    """Radio fills turn on the matching kid and turn off the other kids whose `/T` is null-equivalent."""
    kids = [f"/T {value} /AS /Off /AP << /N << /{state} 20 0 R /Off 21 0 R >> >>" for state in ("A", "B", "C")]
    pdf = _build_widget_kids_pdf("/FT /Btn /Ff 32768 /T (plan) /V /Off", kids, SOLID_BUTTON_STATES)
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "plan", "type": "radio", "value": "Off"}]
        doc.set_form_field("plan", "B")
        for current in (doc, pylopdf.open(doc.to_bytes())):
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
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
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
    source = pylopdf.open(_build_widget_budget_pdf(widgets + f"{value} " * 4095))

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Off"}]
        doc.set_form_field("button", True)
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert current.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Yes"}]

    over_limit = pylopdf.open(_build_widget_budget_pdf(widgets + f"{value} " * 4096))
    for doc in (over_limit, pylopdf.open(over_limit.to_bytes())):
        before = doc.to_bytes()
        with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
            doc.get_form_fields()
        with pytest.raises(pylopdf.PdfError, match="8192-edge safety limit"):
            doc.set_form_field("button", "Yes")
        assert doc.to_bytes() == before


@null_equivalents
def test_null_named_kids_count_toward_the_widget_limit(value: str) -> None:
    """Kids whose `/T` is null-equivalent count toward the 4,096-widget limit like other widgets.

    As a child field, such a kid was visited once however often `/Kids`
    referenced it.
    """
    source = pylopdf.open(_build_widget_budget_pdf("8 0 R " * 4097, f"/T {value}"))

    for doc in (source, pylopdf.open(source.to_bytes())):
        before = doc.to_bytes()
        with pytest.raises(pylopdf.PdfError, match="4096-widget safety limit"):
            doc.get_form_fields()
        with pytest.raises(pylopdf.PdfError, match="4096-widget safety limit"):
            doc.set_form_field("button", "Yes")
        assert doc.to_bytes() == before


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
    source = pylopdf.open(_build_sparse_pdf(objects))

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "field", "type": "text", "value": "value"}]


# An intermediate field is nameless when its `/T` is absent or reads as null.
NAMELESS = {"absent": "", **{kind: f"/T {value}" for kind, value in NULL_EQUIVALENTS.items()}}
nameless_kinds = pytest.mark.parametrize("nameless", list(NAMELESS.values()), ids=list(NAMELESS))

# A string is a widget kid with those entries, and a list is a nameless
# intermediate field whose kids follow the list.
FoldedKids: TypeAlias = "list[str | FoldedKids]"


def _build_folded_pdf(field: str, kids: FoldedKids, nameless: str = "", extra: dict[int, str] | None = None) -> bytes:
    """Build top-level field 5 whose `/Kids` follow `kids`, adding `nameless` to every intermediate field.

    Widget `index`, counted in document order, covers an 80-by-30 rectangle at
    x = 10 + 100 * index. Object 6 is null and number 7 stays free, as
    `NULL_EQUIVALENTS` expects; the kids are numbered from 8 in document order.
    """
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        4: "<< /Fields [5 0 R] >>",
        6: "null",
        **(extra or {}),
    }
    next_number = 8
    widgets: list[str] = []

    def add(parent: int, kid: str | FoldedKids) -> str:
        nonlocal next_number
        number = next_number
        next_number += 1
        assert number not in objects
        if isinstance(kid, str):
            left = 10 + 100 * len(widgets)
            widgets.append(f"{number} 0 R")
            objects[number] = (
                f"<< /Parent {parent} 0 R {kid} /Type /Annot /Subtype /Widget"
                f" /Rect [{left} 10 {left + 80} 40] /P 3 0 R /F 4 >>"
            )
        else:
            grandkids = " ".join(add(number, grandkid) for grandkid in kid)
            objects[number] = f"<< /Parent {parent} 0 R {nameless} /Kids [{grandkids}] >>"
        return f"{number} 0 R"

    refs = " ".join(add(5, kid) for kid in kids)
    objects[3] = (
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {20 + 100 * len(widgets)} 100] /Annots [{' '.join(widgets)}] >>"
    )
    objects[5] = f"<< {field} /Kids [{refs}] >>"
    return _build_sparse_pdf(objects)


@nameless_kinds
def test_nameless_intermediate_widgets_belong_to_the_field_above(nameless: str) -> None:
    """Widgets below a nameless intermediate field are widgets of the field above it.

    The intermediate was a widget itself, so a fill drew none of the widgets
    below it and still cleared NeedAppearances.
    """
    source = pylopdf.open(_build_folded_pdf("/FT /Tx /T (name) /V (old)", [["", "", ""]], nameless))

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "name", "type": "text", "value": "old"}]
        assert _kid_ink(doc) == [0, 0, 0]
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert current.get_form_fields() == [{"name": "name", "type": "text", "value": "new"}]
            ink = _kid_ink(current)
            assert ink[0] > 0
            assert ink == [ink[0]] * 3
            assert b"/NeedAppearances false" in current.to_bytes()


@nameless_kinds
def test_widget_kids_and_nested_intermediates_fold_into_one_field(nameless: str) -> None:
    """Direct widget kids, sibling intermediates, and a nested intermediate all show one field's value."""
    kids: FoldedKids = ["", ["", ""], [[""], ""]]
    source = pylopdf.open(_build_folded_pdf("/FT /Tx /T (name)", kids, nameless))

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "name", "type": "text", "value": None}]
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            ink = _kid_ink(current, 5)
            assert ink[0] > 0
            assert ink == [ink[0]] * 5
            assert b"/NeedAppearances false" in current.to_bytes()


@nameless_kinds
def test_nameless_intermediate_widgets_follow_their_checkbox_state(nameless: str) -> None:
    """Checkbox fills synchronize `/AS` on direct widget kids and on the widgets below an intermediate."""
    states = "/AS /Off /AP << /N << /Yes 20 0 R /Off 21 0 R >> >>"
    pdf = _build_folded_pdf("/FT /Btn /T (agree) /V /Off", [states, [states, states]], nameless, SOLID_BUTTON_STATES)
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "agree", "type": "checkbox", "value": "Off"}]
        assert _kid_colors(doc) == [RED] * 3
        for checked, state, color in ((True, "Yes", GREEN), (False, "Off", RED)):
            doc.set_form_field("agree", checked)
            for current in (doc, pylopdf.open(doc.to_bytes())):
                assert current.get_form_fields() == [{"name": "agree", "type": "checkbox", "value": state}]
                assert _kid_colors(current) == [color] * 3
                assert b"/NeedAppearances false" in current.to_bytes()


@nameless_kinds
def test_nameless_intermediate_widgets_follow_their_radio_state(nameless: str) -> None:
    """Radio fills turn on the matching widget below an intermediate and turn off every other widget."""
    first, second, third = (f"/AS /Off /AP << /N << /{state} 20 0 R /Off 21 0 R >> >>" for state in "ABC")
    pdf = _build_folded_pdf(
        "/FT /Btn /Ff 32768 /T (plan) /V /Off", [first, [second, third]], nameless, SOLID_BUTTON_STATES
    )
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "plan", "type": "radio", "value": "Off"}]
        doc.set_form_field("plan", "C")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert current.get_form_fields() == [{"name": "plan", "type": "radio", "value": "C"}]
            assert _kid_colors(current) == [RED, RED, GREEN]
            assert b"/NeedAppearances false" in current.to_bytes()


@nameless_kinds
def test_named_fields_below_nameless_intermediates_are_child_fields(nameless: str) -> None:
    """A named field below an intermediate is a child field of the field above, with no name component between.

    It inherits through the intermediate, and the field above is no longer a
    leaf. The field above was listed as a leaf instead, and the named field was
    unreachable.
    """
    pdf = _build_folded_pdf("/FT /Tx /T (name) /V (parent)", [["/T (inner)"]], f"{nameless} /V (folded)")
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "name.inner", "type": "text", "value": "folded"}]
        before = doc.to_bytes()
        with pytest.raises(pylopdf.PdfError, match="form field not found"):
            doc.set_form_field("name", "new")
        assert doc.to_bytes() == before
        doc.set_form_field("name.inner", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert current.get_form_fields() == [{"name": "name.inner", "type": "text", "value": "new"}]
            assert _kid_ink(current, 1)[0] > 0
            assert b"/NeedAppearances false" in current.to_bytes()


def _build_cycle_pdf(field_kids: str, intermediates: dict[int, str]) -> bytes:
    """Build text field 5 with the given kids and intermediates; 9 and 10 are widgets in kid-ink order."""
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Annots [9 0 R 10 0 R] >>",
        4: "<< /Fields [5 0 R] >>",
        5: f"<< /FT /Tx /T (name) /Kids {field_kids} >>",
        9: "<< /Type /Annot /Subtype /Widget /Rect [10 10 90 40] /P 3 0 R /F 4 >>",
        10: "<< /Type /Annot /Subtype /Widget /Rect [110 10 190 40] /P 3 0 R /F 4 >>",
        **intermediates,
    }
    return _build_sparse_pdf(objects)


@pytest.mark.parametrize(
    "intermediates",
    [
        {8: "<< /Parent 5 0 R /Kids [8 0 R 9 0 R 10 0 R] >>"},
        {8: "<< /Parent 5 0 R /Kids [11 0 R 9 0 R] >>", 11: "<< /Parent 8 0 R /Kids [8 0 R 10 0 R] >>"},
    ],
    ids=["itself", "ancestor"],
)
def test_cycles_through_nameless_intermediates_end(intermediates: dict[int, str]) -> None:
    """An intermediate that is its own kid or a kid of its own kid is expanded once."""
    source = pylopdf.open(_build_cycle_pdf("[8 0 R]", intermediates))

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "name", "type": "text", "value": None}]
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            ink = _kid_ink(current, 2)
            assert ink[0] > 0
            assert ink == [ink[0]] * 2
            assert b"/NeedAppearances false" in current.to_bytes()


@pytest.mark.parametrize("field_kids", ["[5 0 R 9 0 R 10 0 R]", "[8 0 R]"], ids=["direct", "through-intermediate"])
def test_fields_below_their_own_intermediates_are_their_own_child_fields(field_kids: str) -> None:
    """A field below its own intermediate is its own child field, as a field in its own `/Kids` is.

    The field is therefore not a leaf in either case, and the walk still ends.
    """
    pdf = _build_cycle_pdf(field_kids, {8: "<< /Parent 5 0 R /Kids [5 0 R 9 0 R 10 0 R] >>"})
    source = pylopdf.open(pdf)

    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == []
        with pytest.raises(pylopdf.PdfError, match="form field not found"):
            doc.set_form_field("name", "new")


@pytest.mark.parametrize(
    "kids",
    ["null", "6 0 R", "7 0 R", "5", "[]"],
    ids=["null", "indirect-null", "missing", "not-an-array", "empty"],
)
def test_nameless_kids_without_kids_to_fold_stay_widgets(kids: str) -> None:
    """A nameless kid is an intermediate field only when its `/Kids` resolves to a non-empty array.

    Any other `/Kids` names no kids to fold, so the kid stays a widget and is
    drawn, as a field with such a `/Kids` is its own widget.
    """
    source = pylopdf.open(_build_folded_pdf("/FT /Tx /T (name)", [f"/Kids {kids}", ""]))

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            ink = _kid_ink(current, 2)
            assert ink[0] > 0
            assert ink == [ink[0]] * 2
            assert b"/NeedAppearances false" in current.to_bytes()


def test_nameless_widget_with_kids_is_drawn_with_its_kids() -> None:
    """A nameless kid that has kids and looks like a widget is a widget and an intermediate field at once.

    Only the kid itself was drawn; the widgets below it were not.
    """
    widget = "/Type /Annot /Subtype /Widget /P 3 0 R /F 4"
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Annots [5 0 R 6 0 R 7 0 R] >>",
        4: "<< /Fields [8 0 R] >>",
        5: f"<< /Parent 8 0 R /Kids [6 0 R 7 0 R] {widget} /Rect [10 10 90 40] >>",
        6: f"<< /Parent 5 0 R {widget} /Rect [110 10 190 40] >>",
        7: f"<< /Parent 5 0 R {widget} /Rect [210 10 290 40] >>",
        8: "<< /FT /Tx /T (name) /Kids [5 0 R] >>",
    }
    source = pylopdf.open(build_raw_pdf(objects))

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            ink = _kid_ink(current)
            assert ink[0] > 0
            assert ink == [ink[0]] * 3
            assert b"/NeedAppearances false" in current.to_bytes()


@nameless_kinds
def test_need_appearances_waits_for_the_widgets_below_intermediates(
    nameless: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NeedAppearances stays true while a widget below an intermediate lacks an appearance.

    Such widgets went unchecked, so a fill that drew no appearance still
    cleared NeedAppearances.
    """
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: ())
    source = pylopdf.open(_build_folded_pdf("/FT /Tx /T (name)", [["", ""]], nameless))

    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field("name", "山田")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert current.get_form_fields() == [{"name": "name", "type": "text", "value": "山田"}]
            assert _kid_ink(current, 2) == [0, 0]
            assert b"/NeedAppearances true" in current.to_bytes()
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert min(_kid_ink(current, 2)) > 0
            assert b"/NeedAppearances false" in current.to_bytes()


def _assert_checkbox_limit(at_limit: bytes, over_limit: bytes, message: str) -> None:
    """Fill checkbox `button` at a limit, and check that one more entry is refused atomically, live and reloaded."""
    source = pylopdf.open(at_limit)
    for doc in (source, pylopdf.open(source.to_bytes())):
        assert doc.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Off"}]
        doc.set_form_field("button", True)
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert current.get_form_fields() == [{"name": "button", "type": "checkbox", "value": "Yes"}]

    refused = pylopdf.open(over_limit)
    for doc in (refused, pylopdf.open(refused.to_bytes())):
        before = doc.to_bytes()
        with pytest.raises(pylopdf.PdfError, match=message):
            doc.get_form_fields()
        with pytest.raises(pylopdf.PdfError, match=message):
            doc.set_form_field("button", "Yes")
        assert doc.to_bytes() == before


def _build_folded_budget_pdf(kids: str, nameless: str = "") -> bytes:
    """Build checkbox 5 whose only kid is intermediate 8 with the `kids` entries and `nameless`; 9 is a widget."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [9 0 R] >>",
        4: "<< /Fields [5 0 R] >>",
        5: "<< /FT /Btn /T (button) /V /Off /Kids [8 0 R] >>",
        6: "null",
        7: _form_stream("0 0 20 20 re f"),
        8: f"<< /Parent 5 0 R {nameless} /Kids [{kids}] >>",
        9: (
            "<< /Parent 8 0 R /Type /Annot /Subtype /Widget /Rect [10 10 30 30]"
            " /AS /Off /AP << /N << /Yes 7 0 R /Off 7 0 R >> >> >>"
        ),
    }
    return build_raw_pdf(objects)


def test_nameless_intermediate_kids_count_toward_the_edge_limit() -> None:
    """Each `/Kids` entry of an intermediate takes one of the 8,192 edges, after the `/Fields` and field entries."""
    _assert_checkbox_limit(
        _build_folded_budget_pdf("9 0 R " + "null " * 8189),
        _build_folded_budget_pdf("9 0 R " + "null " * 8190),
        "8192-edge safety limit",
    )


@pytest.mark.parametrize(
    ("nameless", "widgets"),
    [("", 4096), ("/Type /Annot /Subtype /Widget /Rect [40 10 60 30]", 4095)],
    ids=["intermediate", "widget-intermediate"],
)
def test_nameless_intermediate_widgets_count_toward_the_widget_limit(nameless: str, widgets: int) -> None:
    """Widgets below an intermediate count toward the 4,096-widget limit, as does an intermediate that is a widget."""
    _assert_checkbox_limit(
        _build_folded_budget_pdf("9 0 R " * widgets, nameless),
        _build_folded_budget_pdf("9 0 R " * (widgets + 1), nameless),
        "4096-widget safety limit",
    )


def test_nameless_intermediates_count_toward_the_node_limit() -> None:
    """Each intermediate is a node, so the field and 4,095 intermediates reach the 4,096-node limit."""

    def build(count: int) -> bytes:
        numbers = range(10, 10 + count)
        refs = " ".join(f"{number} 0 R" for number in numbers)
        objects = {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [8 0 R] >>",
            4: "<< /Fields [5 0 R] >>",
            5: f"<< /FT /Btn /T (button) /V /Off /Kids [{refs}] >>",
            8: (
                "<< /Parent 10 0 R /Type /Annot /Subtype /Widget /Rect [10 10 30 30]"
                " /AS /Off /AP << /N << /Yes 9 0 R /Off 9 0 R >> >> >>"
            ),
            9: _form_stream("0 0 20 20 re f"),
            # Every intermediate holds one kid; only the first holds the widget.
            **{number: f"<< /Parent 5 0 R /Kids [{'8 0 R' if number == 10 else 'null'}] >>" for number in numbers},
        }
        return _build_sparse_pdf(objects)

    _assert_checkbox_limit(build(4095), build(4096), "4096-node safety limit")


def test_nameless_intermediates_count_toward_the_depth_limit() -> None:
    """Each intermediate is a level, so 63 nested intermediates below a top-level field reach the 64-level limit."""

    def build(depth: int) -> bytes:
        kids: FoldedKids = [""]
        for _ in range(depth):
            kids = [kids]
        return _build_folded_pdf("/FT /Tx /T (name)", kids)

    source = pylopdf.open(build(63))
    for doc in (source, pylopdf.open(source.to_bytes())):
        doc.set_form_field("name", "new")
        for current in (doc, pylopdf.open(doc.to_bytes())):
            assert _kid_ink(current, 1)[0] > 0
            assert b"/NeedAppearances false" in current.to_bytes()

    refused = pylopdf.open(build(64))
    for doc in (refused, pylopdf.open(refused.to_bytes())):
        before = doc.to_bytes()
        with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
            doc.get_form_fields()
        with pytest.raises(pylopdf.PdfError, match="64-level safety limit"):
            doc.set_form_field("name", "new")
        assert doc.to_bytes() == before


@pytest.mark.parametrize(
    ("field", "nameless", "limit", "message"),
    [
        ("/FT /Tx /T ({text})", "", 512 * 1024, r"materialized field-name.*1048576-byte safety limit"),
        ("/FT /Tx /T (name)", "/V ({text})", 1024 * 1024, r"encoded field-value.*1048576-byte safety limit"),
    ],
    ids=["materialized-name", "value"],
)
def test_nameless_intermediates_count_toward_the_text_limits(
    field: str, nameless: str, limit: int, message: str
) -> None:
    """An intermediate materializes the full name of the field above it and reads its own value within the limits."""

    def build(size: int) -> bytes:
        text = "x" * size
        return _build_folded_pdf(field.format(text=text), [[""]], nameless.format(text=text))

    source = pylopdf.open(build(limit))
    for doc in (source, pylopdf.open(source.to_bytes())):
        assert len(doc.get_form_fields()) == 1

    refused = pylopdf.open(build(limit + 1))
    for doc in (refused, pylopdf.open(refused.to_bytes())):
        before = doc.to_bytes()
        with pytest.raises(pylopdf.PdfError, match=message):
            doc.get_form_fields()
        with pytest.raises(pylopdf.PdfError, match=message):
            doc.set_form_field("name", "new")
        assert doc.to_bytes() == before


# 64 KiB of text that the kids of field 5 inherit. A copy per pushed kid or per
# returned leaf took 256-512 MiB for these inputs.
LONG_TEXT = "x" * (64 * 1024)
LONG_NAME_FIELD = [{"name": f"{LONG_TEXT}.c", "type": "text", "value": None}]
TYPE_LEAVES = range(6, 4101)
# Entries and `/Kids` of field 5, its kids from object 6, and the listed fields.
# A kid repeated 8,190 times takes all but one of the edges after `/Fields`.
SHARED_TEXT_CASES = {
    "child-field": (f"/FT /Tx /T ({LONG_TEXT})", "6 0 R " * 8190, {6: "<< /T (c) >>"}, LONG_NAME_FIELD),
    "intermediate": (
        f"/FT /Tx /T ({LONG_TEXT})",
        "6 0 R " * 8190,
        {6: "<< /Kids [7 0 R] >>", 7: "<< /T (c) >>"},
        LONG_NAME_FIELD,
    ),
    "type": (
        f"/FT /{LONG_TEXT}",
        "6 0 R " * 8190,
        {6: "<< /T (c) >>"},
        [{"name": "c", "type": "unknown", "value": None}],
    ),
    "type-leaves": (
        f"/FT /{LONG_TEXT}",
        " ".join(f"{number} 0 R" for number in TYPE_LEAVES),
        {number: f"<< /T (k{number}) >>" for number in TYPE_LEAVES},
        [{"name": name, "type": "unknown", "value": None} for name in sorted(f"k{number}" for number in TYPE_LEAVES)],
    ),
}


def _build_shared_text_pdf(field: str, kid_refs: str, kids: dict[int, str]) -> bytes:
    """Build field 5 with the `field` entries and the `kid_refs` kids, which `kids` defines from object 6."""
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Fields [5 0 R] >>",
            5: f"<< {field} /Kids [{kid_refs}] >>",
            **kids,
        }
    )


@pytest.mark.parametrize(("field", "kid_refs", "kids", "expected"), SHARED_TEXT_CASES.values(), ids=SHARED_TEXT_CASES)
def test_kids_list_the_long_name_or_type_they_inherit(
    field: str, kid_refs: str, kids: dict[int, str], expected: list[dict[str, str | None]]
) -> None:
    """A kid repeated on 8,190 edges is listed once, as one reference lists it, and 4,095 leaves share one type."""
    doc = pylopdf.open(_build_shared_text_pdf(field, kid_refs, kids))
    assert doc.get_form_fields() == expected


# Print by how many bytes one get_form_fields() call raised the peak RSS.
PEAK_RSS_GROWTH = """\
import resource
import sys

import pylopdf

doc = pylopdf.open(sys.argv[1])
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
doc.get_form_fields()
growth = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
print(growth if sys.platform == "darwin" else growth * 1024)
"""


@pytest.mark.parametrize(
    ("field", "kid_refs", "kids"), [case[:3] for case in SHARED_TEXT_CASES.values()], ids=SHARED_TEXT_CASES
)
def test_kids_share_the_long_name_or_type_they_inherit(
    tmp_path: Path, field: str, kid_refs: str, kids: dict[int, str]
) -> None:
    """Listing the fields in a fresh process raises its peak RSS by less than a quarter of the copies."""
    pytest.importorskip("resource")
    path = tmp_path / "form.pdf"
    path.write_bytes(_build_shared_text_pdf(field, kid_refs, kids))
    result = subprocess.run(  # noqa: S603 - fixed script under the active interpreter.
        [sys.executable, "-c", PEAK_RSS_GROWTH, str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) < 64 * 1024 * 1024
