"""Weight instances of variable faces, from the script font extras or passed in as files or bytes."""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Literal

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf.pylopdf_core import _Document

FONT_ROOT = Path(__file__).parents[1] / "fonts"
HEBREW_SANS = FONT_ROOT / "pylopdf-fonts-he" / "src" / "pylopdf_fonts_he" / "NotoSansHebrew-Variable.ttf"
ARABIC_SANS = FONT_ROOT / "pylopdf-fonts-ar" / "src" / "pylopdf_fonts_ar" / "NotoSansArabic-Variable.ttf"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"

HEBREW = "שלום עולם"
# Advances of shin, lamed, vav, and final mem in Noto Sans Hebrew, in font
# units (1,000 per em), that fontTools.varLib.instancer gives an instance of
# the variable font at each weight. pylopdf computes them independently.
SHALOM = "שלום"
SHALOM_ADVANCES = {
    100: (674, 489, 264, 666),
    400: (730, 522, 301, 684),
    700: (787, 556, 338, 702),
}
# Faces of the four script extras, with text that each face draws. Extraction
# reads one glyph of a multi-glyph cluster, such as a Devanagari consonant with
# a vowel sign, so the Devanagari text has no such cluster and extraction
# reports every drawn advance.
SCRIPT_FACES = [
    pytest.param("ar", False, "مرحبا بالعالم", id="ar-sans"),
    pytest.param("ar", True, "مرحبا بالعالم", id="ar-serif"),
    pytest.param("he", False, HEBREW, id="he-sans"),
    pytest.param("he", True, HEBREW, id="he-serif"),
    pytest.param("hi", False, "नमक नगर", id="hi-sans"),
    pytest.param("hi", True, "नमक नगर", id="hi-serif"),
    pytest.param("th", False, "ภาษาไทย", id="th-sans"),
    pytest.param("th", True, "ภาษาไทย", id="th-serif"),
]
EXTRA_MODULES = {"ar": "pylopdf_fonts_ar", "he": "pylopdf_fonts_he", "hi": "pylopdf_fonts_hi", "th": "pylopdf_fonts_th"}
# The repository file of each face, which tests also pass as fontfile or
# fontbuffer, so those cases run without the extras installed.
VARIABLE_FONT_FILES = {
    ("ar", False): "NotoSansArabic-Variable.ttf",
    ("ar", True): "NotoNaskhArabic-Variable.ttf",
    ("he", False): "NotoSansHebrew-Variable.ttf",
    ("he", True): "NotoSerifHebrew-Variable.ttf",
    ("hi", False): "NotoSansDevanagari-Variable.ttf",
    ("hi", True): "NotoSerifDevanagari-Variable.ttf",
    ("th", False): "NotoSansThai-Variable.ttf",
    ("th", True): "NotoSerifThai-Variable.ttf",
}
# Where a face comes from: Font(language=...), or its file as fontfile or fontbuffer.
SOURCES = ["language", "fontfile", "fontbuffer"]
EXPLICIT_SOURCES = ["fontfile", "fontbuffer"]
BOLD_FLAG = 16
# A subset name in a font or descriptor dictionary: a six-letter tag, then the face.
SUBSET_NAME = re.compile(rb"/(?:BaseFont|FontName) */[A-Z]{6}\+([A-Za-z0-9_-]+)")


def _new_page(width: float = 400, height: float = 200) -> tuple[pylopdf.Document, pylopdf.Page]:
    doc = pylopdf.Document()
    return doc, doc.new_page(width=width, height=height)


def _spans(page: pylopdf.Page) -> list[pylopdf.TextSpan]:
    return [span for block in page.get_text("dict")["blocks"] for line in block["lines"] for span in line["spans"]]


def _chars(page: pylopdf.Page) -> list[pylopdf.TextChar]:
    return [
        char
        for block in page.get_text("rawdict")["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]


def _ink(page: pylopdf.Page) -> int:
    """Count the pixels that drawing covered on a transparent page."""
    return sum(alpha > 128 for alpha in page.get_pixmap(2).samples[3::4])


def _variable_font_path(language: str, serif: bool) -> Path:  # noqa: FBT001
    package = FONT_ROOT / f"pylopdf-fonts-{language}" / "src" / EXTRA_MODULES[language]
    return package / VARIABLE_FONT_FILES[language, serif]


def _variable_font(
    source: str,
    language: pylopdf.FontLanguage,
    serif: bool,  # noqa: FBT001
    weight: Literal["regular", "bold"] | int | None = None,
) -> pylopdf.Font:
    """Load a script face from its installed extra, or its file as fontfile or fontbuffer."""
    if source == "language":
        pytest.importorskip(EXTRA_MODULES[language])
        return pylopdf.Font(language=language, serif=serif, weight=weight)
    path = _variable_font_path(language, serif)
    if source == "fontfile":
        return pylopdf.Font(path, weight=weight)
    return pylopdf.Font(fontbuffer=path.read_bytes(), weight=weight)


def _table_record(data: bytes, wanted: bytes) -> tuple[int, int]:
    """Return the table-directory position and data offset of one table."""
    table_count = struct.unpack_from(">H", data, 4)[0]
    for index in range(table_count):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        if tag == wanted:
            return 12 + 16 * index, offset
    msg = f"font has no {wanted.decode()} table"
    raise AssertionError(msg)


def _with_weight_class(data: bytes, weight: int) -> bytes:
    """Return font data whose OS/2 table declares another weight class."""
    _record, offset = _table_record(data, b"OS/2")
    patched = bytearray(data)
    struct.pack_into(">H", patched, offset + 4, weight)
    return bytes(patched)


def _with_typo_ascender(data: bytes, ascender: int) -> bytes:
    """Return font data whose OS/2 typographic ascender, which ``Font.ascender`` reads, differs."""
    _record, offset = _table_record(data, b"OS/2")
    patched = bytearray(data)
    struct.pack_into(">h", patched, offset + 68, ascender)
    return bytes(patched)


def _without_weight_axis(data: bytes) -> bytes:
    """Return a variable font's data as a static face of its default instance.

    Renaming the ``fvar`` table hides every axis from pylopdf, krilla, and
    HarfRust; the new tag keeps the table directory sorted.
    """
    record, _offset = _table_record(data, b"fvar")
    patched = bytearray(data)
    struct.pack_into(">4s", patched, record, b"fvaz")
    return bytes(patched)


@pytest.fixture
def font_registry(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, int | None, bool], pylopdf.Font]:
    """Give each test an empty registry so registrations cannot leak."""
    registry: dict[tuple[str, int | None, bool], pylopdf.Font] = {}
    monkeypatch.setattr(pylopdf, "_REGISTERED_FONTS", registry)
    return registry


def test_hebrew_generation_embeds_the_regular_instance() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    doc, page = _new_page(500, 300)

    page.insert_text((20, 60), HEBREW, fontsize=24)
    page.insert_text((20, 100), HEBREW, fontsize=24, font_language="he")
    page.insert_textbox((20, 120, 480, 160), HEBREW, fontsize=24, font_language="he")
    page.insert_text((20, 260), SHALOM, fontsize=100, font_language="he")

    spans = _spans(page)
    assert [span["font"] for span in spans] == ["NotoSansHebrew-Regular"] * 4
    # The variable font's default instance is Thin; the drawn advances are
    # those of its weight-400 instance.
    widths = {char["c"]: char["bbox"][2] - char["bbox"][0] for char in _chars(page) if char["origin"][1] > 200}
    assert [widths[char] for char in SHALOM] == pytest.approx([advance / 10 for advance in SHALOM_ADVANCES[400]])
    reopened = pylopdf.open(stream=doc.tobytes())
    assert {span["font"] for span in _spans(reopened[0])} == {"NotoSansHebrew-Regular"}
    assert reopened[0].get_text().count(HEBREW) == 3


def test_font_language_selects_the_regular_hebrew_instance() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    regular = pylopdf.Font(language="he")
    thin = pylopdf.Font(language="he", weight=100)

    assert (regular.name, regular.family, regular.weight) == ("NotoSansHebrew-Regular", "Noto Sans Hebrew", 400)
    assert (thin.name, thin.weight) == ("NotoSansHebrew-Thin", 100)
    for weight, advances in SHALOM_ADVANCES.items():
        face = pylopdf.Font(language="he", weight=weight)
        assert [face.text_length(char, 1000) for char in SHALOM] == pytest.approx(advances)
    # Drawing with the font extra draws what the Font draws.
    expected, actual = (_new_page() for _ in range(2))
    expected[1].insert_text((20, 60), HEBREW, fontsize=18, font=regular)
    actual[1].insert_text((20, 60), HEBREW, fontsize=18, font_language="he")
    assert actual[0].tobytes() == expected[0].tobytes()


def test_explicit_variable_font_input_keeps_its_default_instance() -> None:
    data = HEBREW_SANS.read_bytes()
    doc, page = _new_page(500, 300)

    page.insert_text((20, 150), SHALOM, fontsize=100, fontbuffer=data)
    face = pylopdf.Font(fontbuffer=data)

    assert {span["font"] for span in _spans(page)} == {"NotoSansHebrew-Thin"}
    widths = [char["bbox"][2] - char["bbox"][0] for char in _chars(page)]
    assert widths == pytest.approx([advance / 10 for advance in SHALOM_ADVANCES[100]])
    assert (face.name, face.weight) == ("NotoSansHebrew-Thin", 100)
    assert doc.page_count == 1


@pytest.mark.parametrize("source", EXPLICIT_SOURCES)
def test_an_explicit_weight_selects_an_instance_of_the_input(source: str) -> None:
    """An omitted weight loads the face as given; an explicit one, even regular, selects that instance."""
    as_given = _variable_font(source, "he", False)
    regular = _variable_font(source, "he", False, weight="regular")

    assert (as_given.name, as_given.weight) == ("NotoSansHebrew-Thin", 100)
    assert (regular.name, regular.weight) == ("NotoSansHebrew-Regular", 400)
    for weight, advances in SHALOM_ADVANCES.items():
        face = _variable_font(source, "he", False, weight=weight)
        assert [face.text_length(char, 1000) for char in SHALOM] == pytest.approx(advances)
    _doc, page = _new_page(500, 300)
    page.insert_text((20, 150), SHALOM, fontsize=100, font=regular)
    assert {span["font"] for span in _spans(page)} == {"NotoSansHebrew-Regular"}
    widths = [char["bbox"][2] - char["bbox"][0] for char in _chars(page)]
    assert widths == pytest.approx([advance / 10 for advance in SHALOM_ADVANCES[400]])


@pytest.mark.parametrize("source", EXPLICIT_SOURCES)
@pytest.mark.parametrize(("language", "serif", "text"), SCRIPT_FACES)
def test_explicit_input_without_a_weight_draws_as_before(
    source: str,
    language: pylopdf.FontLanguage,
    serif: bool,  # noqa: FBT001
    text: str,
) -> None:
    """A face loaded without ``weight``, or at its default weight, draws what fontfile and fontbuffer draw."""
    path = _variable_font_path(language, serif)
    as_given = _variable_font(source, language, serif)
    default_weight = _variable_font(source, language, serif, weight=as_given.weight)
    drawn = []
    for font in (as_given, default_weight, None):
        doc, page = _new_page()
        if font is not None:
            page.insert_text((20, 60), text, fontsize=18, font=font)
            page.insert_textbox((20, 80, 380, 180), text, fontsize=14, font=font)
        elif source == "fontfile":
            page.insert_text((20, 60), text, fontsize=18, fontfile=path)
            page.insert_textbox((20, 80, 380, 180), text, fontsize=14, fontfile=path)
        else:
            page.insert_text((20, 60), text, fontsize=18, fontbuffer=path.read_bytes())
            page.insert_textbox((20, 80, 380, 180), text, fontsize=14, fontbuffer=path.read_bytes())
        drawn.append(doc.tobytes())

    assert drawn[0] == drawn[1] == drawn[2]
    assert default_weight.name == as_given.name
    # The subset keeps krilla's name, the default instance's PostScript name.
    assert {match.decode() for match in SUBSET_NAME.findall(drawn[0])} == {as_given.name}


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(("language", "serif", "text"), SCRIPT_FACES)
def test_variable_faces_provide_named_weight_instances(
    source: str,
    language: pylopdf.FontLanguage,
    serif: bool,  # noqa: FBT001
    text: str,
) -> None:
    regular = _variable_font(source, language, serif, weight="regular")
    bold = _variable_font(source, language, serif, weight="bold")
    semibold = _variable_font(source, language, serif, weight=600)
    between = _variable_font(source, language, serif, weight=650)

    assert regular.name is not None
    prefix = regular.name.rsplit("-", 1)[0]
    assert regular.name == f"{prefix}-Regular"
    assert (bold.name, bold.weight, bold.family) == (f"{prefix}-Bold", 700, regular.family)
    assert (semibold.name, semibold.weight) == (f"{prefix}-SemiBold", 600)
    assert (between.name, between.weight) == (f"{prefix}_650wght", 650)
    # Heavier instances of these faces advance farther.
    widths = [font.text_length(text, 20) for font in (regular, semibold, between, bold)]
    assert widths == sorted(widths)
    assert widths[0] < widths[-1]
    # The weight axis varies neither the em nor the line metrics.
    assert (bold.units_per_em, bold.ascender, bold.descender) == (
        regular.units_per_em,
        regular.ascender,
        regular.descender,
    )


# Noto Sans Arabic draws some dots as glyphs of their own, which extraction skips.
@pytest.mark.filterwarnings("ignore:text extraction skipped glyphs:pylopdf.PylopdfWarning")
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(("language", "serif", "text"), SCRIPT_FACES)
def test_a_bold_instance_measures_and_draws_the_same_width(
    source: str,
    language: pylopdf.FontLanguage,
    serif: bool,  # noqa: FBT001
    text: str,
) -> None:
    bold = _variable_font(source, language, serif, weight="bold")
    regular = _variable_font(source, language, serif, weight="regular")
    doc, page = _new_page()
    x, size = 30.25, 24.0

    page.insert_text((x, 80), text, font=bold, fontsize=size)

    chars = _chars(page)
    assert min(char["origin"][0] for char in chars) == pytest.approx(x, abs=1e-3)
    assert max(char["bbox"][2] for char in chars) == pytest.approx(x + bold.text_length(text, size), abs=1e-2)
    spans = _spans(page)
    assert {span["font"] for span in spans} == {bold.name}
    assert all(span["flags"] & BOLD_FLAG for span in spans)
    _regular_doc, regular_page = _new_page()
    regular_page.insert_text((x, 80), text, font=regular, fontsize=size)
    assert _ink(page) > 1.2 * _ink(regular_page)
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened[0].get_text().strip() == text


@pytest.mark.parametrize("source", SOURCES)
def test_a_prefix_of_a_bold_line_ends_where_the_next_glyph_begins(source: str) -> None:
    bold = _variable_font(source, "th", False, weight="bold")
    _doc, page = _new_page()
    x, size, text, prefix = 20.5, 30.0, "ภาษาไทย", "ภาษา"

    page.insert_text((x, 80), text, font=bold, fontsize=size)

    following = next(char for char in _chars(page) if char["c"] == text[len(prefix)])
    assert following["origin"][0] == pytest.approx(x + bold.text_length(prefix, size), abs=1e-3)


@pytest.mark.parametrize("source", SOURCES)
def test_textbox_lays_out_a_bold_instance_with_its_advances(source: str) -> None:
    bold = _variable_font(source, "he", False, weight="bold")
    _doc, page = _new_page()
    rect = (20, 20, 380, 120)

    spare = page.insert_textbox(rect, HEBREW, font=bold, fontsize=16, align=pylopdf.TEXT_ALIGN_RIGHT)

    assert spare == pytest.approx(rect[3] - rect[1] - (bold.ascender - bold.descender) * 16, abs=1e-6)
    line = page.get_text("dict")["blocks"][0]["lines"][0]
    assert line["bbox"][0] == pytest.approx(rect[2] - bold.text_length(HEBREW, 16), abs=1e-2)
    assert line["bbox"][2] == pytest.approx(rect[2], abs=1e-2)


def test_each_weight_on_a_page_keeps_its_own_subset_name() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    doc, page = _new_page(400, 300)
    faces = [pylopdf.Font(language="he", weight=weight) for weight in (100, 400, 650, 700, 900)]

    for index, face in enumerate(faces):
        page.insert_text((20, 40 + 50 * index), HEBREW, font=face, fontsize=20)

    names = ["NotoSansHebrew-Thin", "NotoSansHebrew-Regular", "NotoSansHebrew_650wght"]
    names += ["NotoSansHebrew-Bold", "NotoSansHebrew-Black"]
    assert [face.name for face in faces] == names
    assert [span["font"] for span in _spans(page)] == names
    # The Type0 font, its CID font, and their descriptor all name the instance.
    saved = doc.tobytes()
    subset_names = [match.decode() for match in SUBSET_NAME.findall(saved)]
    assert sorted(subset_names) == sorted(name for name in names for _ in range(3))
    # Heavier instances cover more of the page.
    ink = []
    for face in faces:
        _single, single_page = _new_page(400, 100)
        single_page.insert_text((20, 60), HEBREW, font=face, fontsize=30)
        ink.append(_ink(single_page))
    assert ink == sorted(ink)
    assert ink[1] > 2 * ink[0]


def test_a_weight_outside_the_axis_is_refused(
    font_registry: dict[tuple[str, int | None, bool], pylopdf.Font],
) -> None:
    pytest.importorskip("pylopdf_fonts_ar")
    pytest.importorskip("pylopdf_fonts_he")
    with pytest.raises(ValueError, match=r"installs 'Noto Naskh Arabic' with weights 400 through 700; register its"):
        pylopdf.Font(language="ar", serif=True, weight=900)
    with pytest.raises(ValueError, match=r"'Noto Sans Hebrew' with weights 100 through 900.*weight-1000 face"):
        pylopdf.Font(language="he", weight=1000)
    with pytest.raises(ValueError, match=r"installs one 'Noto Sans Hebrew' face; register its bold italic face"):
        pylopdf.Font(language="he", weight="bold", italic=True)

    with pytest.warns(pylopdf.PylopdfWarning, match="no registered weight-50 face; using its regular face") as record:
        fallback = pylopdf.Font(language="he", weight=50, allow_regular_fallback=True)
    assert (fallback.name, fallback.weight) == ("NotoSansHebrew-Regular", 400)
    assert record[0].filename == __file__
    assert pylopdf.Font(language="ar", serif=True, weight=700).name == "NotoNaskhArabic-Bold"
    assert not font_registry


def test_explicit_input_refuses_weights_that_the_font_does_not_define() -> None:
    naskh = _variable_font_path("ar", True)
    for source in EXPLICIT_SOURCES:
        for weight in (300, 900):
            outside = f"weight {weight} is outside the font's weight axis, which runs from 400 through 700"
            with pytest.raises(ValueError, match=outside):
                _variable_font(source, "ar", True, weight=weight)
    # The same face without its weight axis has only its default instance's weight.
    static = _without_weight_axis(HEBREW_SANS.read_bytes())
    with pytest.raises(ValueError, match="no weight axis, and its only face is weight-100, not regular; load the"):
        pylopdf.Font(fontbuffer=static, weight="regular")
    assert pylopdf.Font(fontbuffer=static, weight=100).name == "NotoSansHebrew-Thin"
    # The other lookup arguments still select only installed or registered faces.
    for option in ({"italic": True}, {"serif": True}, {"allow_regular_fallback": True}):
        with pytest.raises(ValueError, match="select an installed or registered face"):
            pylopdf.Font(naskh, weight="bold", **option)  # ty: ignore[invalid-argument-type]


def test_a_registered_face_takes_precedence_over_the_weight_axis(
    font_registry: dict[tuple[str, int | None, bool], pylopdf.Font],
) -> None:
    pytest.importorskip("pylopdf_fonts_he")
    # The patched face is a static face of the default (Thin) instance,
    # registered as the family's weight-700 face.
    static_bold = _without_weight_axis(_with_weight_class(HEBREW_SANS.read_bytes(), 700))
    registered = pylopdf.register_font(fontbuffer=static_bold)

    selected = pylopdf.Font(language="he", weight="bold")

    assert set(font_registry) == {("noto sans hebrew", 700, False)}
    assert (selected.name, selected.weight) == (registered.name, 700) == ("NotoSansHebrew-Thin", 700)
    assert pylopdf.Font(language="he", weight=600).name == "NotoSansHebrew-SemiBold"


@pytest.mark.parametrize("source", EXPLICIT_SOURCES)
def test_a_registered_variable_face_provides_every_weight_on_its_axis(
    font_registry: dict[tuple[str, int | None, bool], pylopdf.Font],
    source: str,
) -> None:
    if source == "fontfile":
        registered = pylopdf.register_font(HEBREW_SANS)
    else:
        registered = pylopdf.register_font(fontbuffer=HEBREW_SANS.read_bytes())

    # The registered face is the variable font as given.
    assert font_registry == {("noto sans hebrew", None, False): registered}
    assert (registered.name, registered.weight) == ("NotoSansHebrew-Thin", 100)
    # The lookups select the regular instance by default and any other weight on the axis.
    expected: dict[Literal["regular", "bold"] | int | None, tuple[str, int]] = {
        None: ("NotoSansHebrew-Regular", 400),
        100: ("NotoSansHebrew-Thin", 100),
        "bold": ("NotoSansHebrew-Bold", 700),
        650: ("NotoSansHebrew_650wght", 650),
        900: ("NotoSansHebrew-Black", 900),
    }
    for weight, (name, weight_class) in expected.items():
        face = pylopdf.Font(family="noto sans hebrew", weight=weight)
        assert (face.name, face.weight, face.family) == (name, weight_class, "Noto Sans Hebrew")
    for weight, advances in SHALOM_ADVANCES.items():
        face = pylopdf.Font(family="Noto Sans Hebrew", weight=weight)
        assert [face.text_length(char, 1000) for char in SHALOM] == pytest.approx(advances)
    # A looked-up instance draws what explicit input of the file at that weight draws.
    expected_doc, actual_doc = (_new_page() for _ in range(2))
    expected_doc[1].insert_text((20, 60), HEBREW, fontsize=18, font=pylopdf.Font(HEBREW_SANS, weight=600))
    actual_doc[1].insert_text((20, 60), HEBREW, fontsize=18, font=pylopdf.Font(family="Noto Sans Hebrew", weight=600))
    assert actual_doc[0].tobytes() == expected_doc[0].tobytes()

    with pytest.raises(
        ValueError,
        match=r"no weight-1000 face of 'Noto Sans Hebrew' is registered, and its registered variable face "
        r"provides weights 100 through 900; load one",
    ):
        pylopdf.Font(family="Noto Sans Hebrew", weight=1000)
    with pytest.raises(ValueError, match="no bold italic face of 'Noto Sans Hebrew' is registered; load one"):
        pylopdf.Font(family="Noto Sans Hebrew", weight="bold", italic=True)
    with pytest.warns(pylopdf.PylopdfWarning, match="no registered weight-1000 face; using its regular face"):
        fallback = pylopdf.Font(family="Noto Sans Hebrew", weight=1000, allow_regular_fallback=True)
    assert (fallback.name, fallback.weight) == ("NotoSansHebrew-Regular", 400)


def test_a_face_registered_at_a_weight_takes_precedence_over_a_registered_axis(
    font_registry: dict[tuple[str, int | None, bool], pylopdf.Font],
) -> None:
    pylopdf.register_font(HEBREW_SANS)
    static_bold = pylopdf.register_font(
        fontbuffer=_without_weight_axis(_with_weight_class(HEBREW_SANS.read_bytes(), 700)),
    )

    bold = pylopdf.Font(family="Noto Sans Hebrew", weight="bold")
    semibold = pylopdf.Font(family="Noto Sans Hebrew", weight=600)

    assert set(font_registry) == {("noto sans hebrew", None, False), ("noto sans hebrew", 700, False)}
    assert (bold.name, bold.weight) == (static_bold.name, 700) == ("NotoSansHebrew-Thin", 700)
    assert semibold.name == "NotoSansHebrew-SemiBold"
    # Registering another variable face of the family and style replaces the earlier one.
    replacement = pylopdf.register_font(fontbuffer=_with_typo_ascender(HEBREW_SANS.read_bytes(), 1500))
    assert font_registry["noto sans hebrew", None, False] is replacement
    assert len(font_registry) == 2
    assert pylopdf.Font(family="Noto Sans Hebrew", weight=600).ascender == pytest.approx(1.5)


def test_a_registered_variable_face_takes_precedence_over_the_installed_axis(
    font_registry: dict[tuple[str, int | None, bool], pylopdf.Font],
) -> None:
    pytest.importorskip("pylopdf_fonts_he")
    installed = pylopdf.Font(language="he", weight="bold")
    # The copy's typographic ascender tells its instances apart from the installed face's.
    pylopdf.register_font(fontbuffer=_with_typo_ascender(HEBREW_SANS.read_bytes(), 1500))

    bold = pylopdf.Font(language="he", weight="bold")

    assert bold.name == installed.name == "NotoSansHebrew-Bold"
    assert bold.ascender == pytest.approx(1.5)
    assert installed.ascender != pytest.approx(1.5)
    # The installed face still provides the regular weight.
    assert pylopdf.Font(language="he").ascender == installed.ascender
    assert font_registry


def test_static_and_default_weight_faces_draw_as_before() -> None:
    """Drawing an installed face at the regular weight changes only a face whose default is another weight.

    Explicit input of the same file is drawn as given, so a static face and a
    variable face whose default instance is regular draw the same bytes both ways.
    """
    pytest.importorskip("pylopdf_fonts_jp")
    pytest.importorskip("pylopdf_fonts_ar")
    pytest.importorskip("pylopdf_fonts_he")
    cases: list[tuple[pylopdf.FontLanguage, str, Path, bool]] = [
        ("ja", "日本語のテキスト", NOTO_SANS_JP, True),
        ("ar", "مرحبا بالعالم", ARABIC_SANS, True),
        ("he", HEBREW, HEBREW_SANS, False),
    ]
    for language, text, path, unchanged in cases:
        installed, explicit = (_new_page() for _ in range(2))
        installed[1].insert_text((20, 60), text, fontsize=18, font_language=language)
        installed[1].insert_textbox((20, 80, 380, 180), text, fontsize=14, font_language=language)
        explicit[1].insert_text((20, 60), text, fontsize=18, fontbuffer=path.read_bytes())
        explicit[1].insert_textbox((20, 80, 380, 180), text, fontsize=14, fontbuffer=path.read_bytes())
        assert (installed[0].tobytes() == explicit[0].tobytes()) is unchanged, language


def test_form_appearances_use_the_regular_instance() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R 5 0 R] /DA (/Helv 0 Tf 0 g) >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 200] >>",
            3: "<< /Type /Page /Parent 2 0 R /Annots [4 0 R 5 0 R] >>",
            4: "<< /FT /Tx /T (name) /Type /Annot /Subtype /Widget /Rect [20 140 280 170] /P 3 0 R /F 4 >>",
            5: (
                "<< /FT /Tx /Ff 16777216 /MaxLen 6 /T (code) /Type /Annot /Subtype /Widget"
                " /Rect [20 80 280 110] /P 3 0 R /F 4 >>"
            ),
        },
    )
    doc = pylopdf.open(stream=pdf)

    doc.set_form_field("name", HEBREW)
    doc.set_form_field("code", "שלום")

    saved = doc.tobytes()
    assert {match.decode() for match in SUBSET_NAME.findall(saved)} == {"NotoSansHebrew-Regular"}
    reopened = pylopdf.open(stream=saved)
    assert {field["name"]: field["value"] for field in reopened.get_form_fields()} == {"name": HEBREW, "code": "שלום"}


def _aligned_form_pdf() -> bytes:
    """Build a right-aligned text field with two widgets and a centered six-position comb field.

    Each widget's text area is inset 2 points inside its 260-point-wide
    rectangle, so in display coordinates the text field's areas end at x = 278
    and the comb positions, each 256 / 6 points wide, start at x = 22.
    """
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R 7 0 R] /DA (/Helv 0 Tf 0 g) >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 200] >>",
            3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R 6 0 R 7 0 R] >>",
            4: "<< /FT /Tx /Q 2 /T (name) /Kids [5 0 R 6 0 R] >>",
            5: "<< /Parent 4 0 R /Type /Annot /Subtype /Widget /Rect [20 140 280 170] /P 3 0 R /F 4 >>",
            6: "<< /Parent 4 0 R /Type /Annot /Subtype /Widget /Rect [20 20 280 50] /P 3 0 R /F 4 >>",
            7: (
                "<< /FT /Tx /Ff 16777216 /MaxLen 6 /Q 1 /T (code) /Type /Annot /Subtype /Widget"
                " /Rect [20 80 280 110] /P 3 0 R /F 4 >>"
            ),
        },
    )


def _fill_aligned_form(doc: pylopdf.Document, source: str, font: pylopdf.Font | None) -> None:
    """Fill both fields with ``font``, or without one through Noto Sans Hebrew's file or bytes."""
    for name, value in (("name", HEBREW), ("code", "A12")):
        if font is not None:
            doc.set_form_field(name, value, font=font)
        elif source == "fontfile":
            doc.set_form_field(name, value, fontfile=HEBREW_SANS)
        else:
            doc.set_form_field(name, value, fontbuffer=HEBREW_SANS.read_bytes())


@pytest.mark.parametrize("source", EXPLICIT_SOURCES)
def test_form_appearances_draw_a_bold_instance_with_its_advances(source: str) -> None:
    """Text and comb appearances fit, align, and center with the bold instance's advances."""
    bold = _variable_font(source, "he", False, weight="bold")
    doc = pylopdf.open(stream=_aligned_form_pdf())

    _fill_aligned_form(doc, source, bold)

    saved = doc.tobytes()
    # Each widget embeds its own subset, named after the instance.
    assert [match.decode() for match in SUBSET_NAME.findall(saved)] == ["NotoSansHebrew-Bold"] * 9
    assert b"/NeedAppearances false" in saved
    page = doc[0]
    spans = _spans(page)
    assert {span["font"] for span in spans} == {"NotoSansHebrew-Bold"}
    assert all(span["flags"] & BOLD_FLAG for span in spans)
    # Both values fit at the 12-point size that auto-fitting starts from.
    assert [span["size"] for span in spans] == pytest.approx([12.0] * len(spans))
    lines = [line for block in page.get_text("dict")["blocks"] for line in block["lines"]]
    first_name, _code, second_name = sorted(lines, key=lambda line: line["bbox"][1])
    for name in (first_name, second_name):
        assert name["bbox"][0] == pytest.approx(278 - bold.text_length(HEBREW, 12), abs=1e-3)
        assert name["bbox"][2] == pytest.approx(278, abs=1e-3)
    # Three characters take the middle positions 1 through 3 of six.
    cell = 256 / 6
    comb = sorted((char for char in _chars(page) if 90 < char["origin"][1] < 120), key=lambda char: char["origin"][0])
    assert [char["c"] for char in comb] == ["A", "1", "2"]
    expected = [22 + (1 + index) * cell + (cell - bold.text_length(char, 12)) / 2 for index, char in enumerate("A12")]
    assert [char["origin"][0] for char in comb] == pytest.approx(expected, abs=1e-3)
    reopened = pylopdf.open(stream=saved)
    assert {field["name"]: field["value"] for field in reopened.get_form_fields()} == {"name": HEBREW, "code": "A12"}


@pytest.mark.parametrize("source", EXPLICIT_SOURCES)
def test_form_font_without_a_weight_draws_what_the_font_input_draws(source: str) -> None:
    """A face loaded without ``weight`` fills fields as fontfile and fontbuffer do, at the default instance."""
    drawn = []
    for font in (_variable_font(source, "he", False), None):
        doc = pylopdf.open(stream=_aligned_form_pdf())
        _fill_aligned_form(doc, source, font)
        drawn.append(doc.tobytes())

    assert drawn[0] == drawn[1]
    assert {match.decode() for match in SUBSET_NAME.findall(drawn[0])} == {"NotoSansHebrew-Thin"}


def test_form_font_of_an_installed_extra_draws_what_automatic_selection_draws() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    automatic, explicit = (pylopdf.open(stream=_aligned_form_pdf()) for _ in range(2))

    automatic.set_form_field("name", HEBREW)
    explicit.set_form_field("name", HEBREW, font=pylopdf.Font(language="he"))

    assert explicit.tobytes() == automatic.tobytes()


def test_weights_share_the_installed_font_file() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    pylopdf.Font(language="he")
    reads = _Document.package_font_reads()

    faces = [pylopdf.Font(language="he", weight=weight) for weight in (100, 300, 700, 900)]
    _doc, page = _new_page()
    page.insert_text((20, 60), HEBREW, fontsize=12, font_language="he")

    assert _Document.package_font_reads() == reads
    assert len({face.name for face in faces}) == len(faces)
