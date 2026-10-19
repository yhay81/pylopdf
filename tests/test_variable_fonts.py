"""Weight instances of the variable faces that the script font extras install."""

from __future__ import annotations

import re
import struct
from pathlib import Path

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


def _with_weight_class(data: bytes, weight: int) -> bytes:
    """Return font data whose OS/2 table declares another weight class."""
    table_count = struct.unpack_from(">H", data, 4)[0]
    for index in range(table_count):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        if tag == b"OS/2":
            patched = bytearray(data)
            struct.pack_into(">H", patched, offset + 4, weight)
            return bytes(patched)
    msg = "font has no OS/2 table"
    raise AssertionError(msg)


@pytest.fixture
def font_registry(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, int, bool], pylopdf.Font]:
    """Give each test an empty registry so registrations cannot leak."""
    registry: dict[tuple[str, int, bool], pylopdf.Font] = {}
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
    with pytest.raises(ValueError, match="as given"):
        pylopdf.Font(fontbuffer=data, weight="bold")
    assert doc.page_count == 1


@pytest.mark.parametrize(("language", "serif", "text"), SCRIPT_FACES)
def test_variable_extras_provide_named_weight_instances(
    language: pylopdf.FontLanguage,
    serif: bool,  # noqa: FBT001
    text: str,
) -> None:
    pytest.importorskip(EXTRA_MODULES[language])
    regular = pylopdf.Font(language=language, serif=serif)
    bold = pylopdf.Font(language=language, serif=serif, weight="bold")
    semibold = pylopdf.Font(language=language, serif=serif, weight=600)
    between = pylopdf.Font(language=language, serif=serif, weight=650)

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
@pytest.mark.parametrize(("language", "serif", "text"), SCRIPT_FACES)
def test_a_bold_instance_measures_and_draws_the_same_width(
    language: pylopdf.FontLanguage,
    serif: bool,  # noqa: FBT001
    text: str,
) -> None:
    pytest.importorskip(EXTRA_MODULES[language])
    bold = pylopdf.Font(language=language, serif=serif, weight="bold")
    regular = pylopdf.Font(language=language, serif=serif)
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


def test_a_prefix_of_a_bold_line_ends_where_the_next_glyph_begins() -> None:
    pytest.importorskip("pylopdf_fonts_th")
    bold = pylopdf.Font(language="th", weight="bold")
    _doc, page = _new_page()
    x, size, text, prefix = 20.5, 30.0, "ภาษาไทย", "ภาษา"

    page.insert_text((x, 80), text, font=bold, fontsize=size)

    following = next(char for char in _chars(page) if char["c"] == text[len(prefix)])
    assert following["origin"][0] == pytest.approx(x + bold.text_length(prefix, size), abs=1e-3)


def test_textbox_lays_out_a_bold_instance_with_its_advances() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    bold = pylopdf.Font(language="he", weight="bold")
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


def test_a_weight_outside_the_axis_is_refused(font_registry: dict[tuple[str, int, bool], pylopdf.Font]) -> None:
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


def test_a_registered_face_takes_precedence_over_the_weight_axis(
    font_registry: dict[tuple[str, int, bool], pylopdf.Font],
) -> None:
    pytest.importorskip("pylopdf_fonts_he")
    # The patched face is the variable font at its default (Thin) instance,
    # registered as the family's weight-700 face.
    registered = pylopdf.register_font(fontbuffer=_with_weight_class(HEBREW_SANS.read_bytes(), 700))

    selected = pylopdf.Font(language="he", weight="bold")

    assert set(font_registry) == {("noto sans hebrew", 700, False)}
    assert (selected.name, selected.weight) == (registered.name, 700) == ("NotoSansHebrew-Thin", 700)
    assert pylopdf.Font(language="he", weight=600).name == "NotoSansHebrew-SemiBold"


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


def test_weights_share_the_installed_font_file() -> None:
    pytest.importorskip("pylopdf_fonts_he")
    pylopdf.Font(language="he")
    reads = _Document.package_font_reads()

    faces = [pylopdf.Font(language="he", weight=weight) for weight in (100, 300, 700, 900)]
    _doc, page = _new_page()
    page.insert_text((20, 60), HEBREW, fontsize=12, font_language="he")

    assert _Document.package_font_reads() == reads
    assert len({face.name for face in faces}) == len(faces)
