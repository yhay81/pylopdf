"""Reusable fonts in pymupdf's shape, and their measurement."""

from __future__ import annotations

import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import pylopdf

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
NOTO_SERIF_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSerifJP-Regular.otf"

# Latin kerning pairs, a ligature candidate, and full-width CJK in one line.
MIXED_LINE = "AVATAR office 見出し、本文。"


def _with_weight_class(data: bytes, weight: int) -> bytes:
    """Return font data whose OS/2 table declares another weight class.

    The repository bundles regular faces only. Changing the two-byte
    usWeightClass field gives a face of the same family that declares
    ``weight`` without adding another font file.
    """
    table_count = struct.unpack_from(">H", data, 4)[0]
    for index in range(table_count):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        if tag == b"OS/2":
            patched = bytearray(data)
            struct.pack_into(">H", patched, offset + 4, weight)
            return bytes(patched)
    msg = "font has no OS/2 table"
    raise AssertionError(msg)


@pytest.fixture(scope="module")
def regular() -> pylopdf.Font:
    return pylopdf.Font(fontfile=NOTO_SANS_JP)


@pytest.fixture(scope="module")
def bold_data() -> bytes:
    return _with_weight_class(NOTO_SANS_JP.read_bytes(), 700)


def _new_page(width: float = 400, height: float = 200) -> tuple[pylopdf.Document, pylopdf.Page]:
    doc = pylopdf.Document()
    return doc, doc.pages.new((width, height))


def _chars(page: pylopdf.Page) -> list[pylopdf.TextChar]:
    return [
        char
        for block in page.get_text("rawdict")["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]


def test_font_reads_face_metadata_and_vertical_metrics(regular: pylopdf.Font) -> None:
    assert regular.name == "Noto Sans JP Regular"
    assert regular.family == "Noto Sans JP"
    assert regular.weight == 400
    assert regular.is_bold is False
    assert regular.is_italic is False
    assert regular.is_serif is False
    assert pylopdf.Font(fontfile=NOTO_SERIF_JP).is_serif is True
    assert regular.units_per_em == 1000
    # The hhea metrics that pymupdf reports and places lines with; the face
    # does not set USE_TYPO_METRICS, so its OS/2 values (0.88, -0.12) are unused.
    assert regular.ascender == pytest.approx(1.16)
    assert regular.descender == pytest.approx(-0.288)
    assert repr(regular) == "Font('Noto Sans JP Regular')"


def test_font_loads_the_same_face_from_a_path_or_bytes(regular: pylopdf.Font) -> None:
    from_bytes = pylopdf.Font(fontbuffer=NOTO_SANS_JP.read_bytes())
    positional = pylopdf.Font(None, str(NOTO_SANS_JP))

    for font in (from_bytes, positional):
        assert font.name == regular.name
        assert font.text_length(MIXED_LINE, 12) == regular.text_length(MIXED_LINE, 12)


def test_text_length_scales_with_size_and_includes_shaping(regular: pylopdf.Font) -> None:
    assert regular.text_length("") == 0
    # Full-width CJK advances one em per character, and the default size is 11.
    assert regular.text_length("見出し", 10) == pytest.approx(30)
    assert regular.text_length("見出し") == pytest.approx(33)
    assert regular.text_length(MIXED_LINE, 22) == pytest.approx(2 * regular.text_length(MIXED_LINE, 11))
    # Kerning pulls "AV" closer than the two glyphs measured alone.
    assert regular.text_length("AV", 100) < regular.text_length("A", 100) + regular.text_length("V", 100)


def test_text_length_equals_the_drawn_advance(regular: pylopdf.Font) -> None:
    doc, page = _new_page()
    x, y, size = 30.25, 80.0, 13.5

    page.insert_text((x, y), MIXED_LINE, font=regular, fontsize=size)

    chars = _chars(page)
    assert "".join(char["c"] for char in chars) == MIXED_LINE
    assert chars[0]["origin"][0] == pytest.approx(x, abs=1e-3)
    # The last glyph's bbox ends at its origin plus its advance.
    assert chars[-1]["bbox"][2] == pytest.approx(x + regular.text_length(MIXED_LINE, size), abs=1e-2)
    # A prefix ends where the glyph after it begins.
    prefix = "AVATAR office 見出し、"
    offsets = [0]
    for char in chars:
        offsets.append(offsets[-1] + len(char["c"]))
    following = chars[offsets.index(len(prefix))]
    assert following["origin"][0] == pytest.approx(x + regular.text_length(prefix, size), abs=1e-2)
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].get_text().strip() == MIXED_LINE


def test_text_length_matches_textbox_layout(regular: pylopdf.Font) -> None:
    _doc, page = _new_page()
    rect = (20, 20, 380, 120)

    spare = page.insert_textbox(rect, "見出しAVATAR", font=regular, fontsize=16, align=pylopdf.TEXT_ALIGN_RIGHT)

    # As in pymupdf, one line counts its default line height, the ascender
    # minus the descender, plus one more descender.
    line_height = regular.ascender - regular.descender
    assert spare == pytest.approx(rect[3] - rect[1] - (line_height - regular.descender) * 16, abs=1e-6)
    line = page.get_text("dict")["blocks"][0]["lines"][0]
    assert line["bbox"][0] == pytest.approx(rect[2] - regular.text_length("見出しAVATAR", 16), abs=1e-2)
    assert line["bbox"][2] == pytest.approx(rect[2], abs=1e-2)


def test_font_parameter_draws_the_same_bytes_as_fontbuffer(regular: pylopdf.Font) -> None:
    data = NOTO_SANS_JP.read_bytes()
    expected, actual = (_new_page() for _ in range(2))

    expected[1].insert_text((20, 60), "日本語\n二行目", fontbuffer=data, fontsize=18, color=(0.2, 0.3, 0.4))
    actual[1].insert_text((20, 60), "日本語\n二行目", font=regular, fontsize=18, color=(0.2, 0.3, 0.4))
    expected[1].insert_textbox((20, 120, 200, 190), "折り返す本文です。" * 3, fontbuffer=data, fontsize=11)
    actual[1].insert_textbox((20, 120, 200, 190), "折り返す本文です。" * 3, font=regular, fontsize=11)

    assert actual[0].to_bytes() == expected[0].to_bytes()


def test_a_bold_face_loaded_from_its_file_draws_as_given(bold_data: bytes) -> None:
    bold = pylopdf.Font(fontbuffer=bold_data)
    assert (bold.weight, bold.is_bold, bold.family) == (700, True, "Noto Sans JP")

    expected, actual = (_new_page() for _ in range(2))
    expected[1].insert_text((20, 60), "太字", fontbuffer=bold_data, fontsize=18)
    actual[1].insert_text((20, 60), "太字", font=bold, fontsize=18)
    assert actual[0].to_bytes() == expected[0].to_bytes()


@pytest.mark.parametrize(
    ("fontname", "name", "family", "flags"),
    [
        ("helv", "Helvetica", "Helvetica", (False, False, False)),
        ("Helvetica", "Helvetica", "Helvetica", (False, False, False)),
        ("hebo", "Helvetica-Bold", "Helvetica", (True, False, False)),
        ("helvetica-oblique", "Helvetica-Oblique", "Helvetica", (False, True, False)),
        ("tiro", "Times-Roman", "Times", (False, False, True)),
        ("Times-BoldItalic", "Times-BoldItalic", "Times", (True, True, True)),
        ("cour", "Courier", "Courier", (False, False, False)),
        ("zadb", "ZapfDingbats", "ZapfDingbats", (False, False, False)),
    ],
)
def test_standard_14_fonts_by_name_or_alias(
    fontname: str,
    name: str,
    family: str,
    flags: tuple[bool, bool, bool],
) -> None:
    font = pylopdf.Font(fontname)

    assert (font.name, font.family) == (name, family)
    assert (font.is_bold, font.is_italic, font.is_serif) == flags
    assert font.weight == (700 if flags[0] else 400)
    assert font.units_per_em == 1000
    assert font.ascender > 0 > font.descender


def test_standard_14_fonts_measure_and_draw_with_afm_widths() -> None:
    helvetica = pylopdf.Font("helv")
    # Adobe AFM widths: H 722, e 556, l 222, o 556, space 278, W 944, r 333, d 556.
    assert helvetica.text_length("Hello World", 11) == pytest.approx(56.837)
    assert helvetica.has_glyph(65) == 65
    assert helvetica.has_glyph("é") == 0xE9
    assert helvetica.has_glyph(0x3042) == 0
    with pytest.raises(pylopdf.PdfError, match="outside WinAnsi"):
        helvetica.text_length("あ")

    expected, actual = (_new_page() for _ in range(2))
    expected[1].insert_text((10, 40), "Hello", fontname="hebo", fontsize=14)
    actual[1].insert_text((10, 40), "Hello", font=pylopdf.Font("Helvetica-Bold"), fontsize=14)
    expected[1].insert_textbox((10, 60, 300, 150), "Wrapped Times text " * 4, fontname="tiro", align=2)
    actual[1].insert_textbox((10, 60, 300, 150), "Wrapped Times text " * 4, font=pylopdf.Font("tiro"), align=2)
    assert actual[0].to_bytes() == expected[0].to_bytes()
    with pytest.raises(ValueError, match="WinAnsi"):
        actual[1].insert_text((10, 40), "日本語", font=helvetica)


def test_one_font_draws_on_several_documents_and_threads(regular: pylopdf.Font) -> None:
    def draw(index: int) -> tuple[float, str]:
        _doc, page = _new_page()
        text = f"見出し{index}"
        page.insert_text((10, 40), text, font=regular, fontsize=12)
        return regular.text_length(text, 12), page.get_text().strip()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(draw, range(8)))

    assert [text for _, text in results] == [f"見出し{index}" for index in range(8)]
    assert {width for width, _ in results} == {regular.text_length("見出し0", 12)}


def test_text_length_rejects_line_breaks_missing_glyphs_and_bad_input(regular: pylopdf.Font) -> None:
    for text in ("一行目\n二行目", "a\rb"):
        with pytest.raises(pylopdf.PdfError, match="one line"):
            regular.text_length(text)
    with pytest.raises(pylopdf.PdfError, match=r"does not contain all glyphs.*U\+1F980"):
        regular.text_length("crab 🦀")
    for size in (0, -1, float("nan"), float("inf"), "12pt"):
        with pytest.raises(ValueError, match="fontsize"):
            regular.text_length("x", size)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="text"):
        regular.text_length(b"bytes")  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.LimitError, match="UTF-8") as exc_info:
        regular.text_length("あ" * 4, max_text_size=11)
    assert exc_info.value.code == "text_input_size"
    assert regular.text_length("あ" * 4, max_text_size=None) == pytest.approx(44)


def test_drawing_names_the_missing_character(regular: pylopdf.Font) -> None:
    _doc, page = _new_page()
    with pytest.raises(pylopdf.PdfError, match=r"no glyph for U\+1F980 \('🦀'\)"):
        page.insert_text((10, 40), "🦀", font=regular)


def test_has_glyph_returns_glyph_numbers(regular: pylopdf.Font) -> None:
    # pymupdf 1.28 returns the same glyph numbers for this face.
    assert regular.has_glyph(ord("髙")) == 14456
    assert regular.has_glyph(65) == 34
    assert regular.has_glyph("髙") == 14456
    assert regular.has_glyph(0x1F980) == 0
    with pytest.raises(ValueError, match="one-character"):
        regular.has_glyph("ab")
    with pytest.raises(ValueError, match="code point"):
        regular.has_glyph(0x110000)
    for value in (True, 65.0, None):
        with pytest.raises(TypeError, match="code point"):
            regular.has_glyph(value)  # ty: ignore[invalid-argument-type]


def test_font_validates_sources_and_lookup_arguments() -> None:
    with pytest.raises(pylopdf.PdfError, match="no default font"):
        pylopdf.Font()
    with pytest.raises(pylopdf.PdfError, match="one font source"):
        pylopdf.Font("helv", NOTO_SANS_JP)
    with pytest.raises(pylopdf.PdfError, match="one font source"):
        pylopdf.Font(family="Noto Sans JP", language="ja")
    for fontname in ("figo", "cjk", "japan", "Arial"):
        with pytest.raises(pylopdf.PdfError, match="Standard 14"):
            pylopdf.Font(fontname)
    for options in ({"is_bold": True}, {"is_italic": True}, {"is_serif": True}, {"allow_regular_fallback": True}):
        with pytest.raises(pylopdf.PdfError, match="as given"):
            pylopdf.Font("helv", **options)  # ty: ignore[invalid-argument-type]
        with pytest.raises(pylopdf.PdfError, match="as given"):
            pylopdf.Font(fontfile=NOTO_SANS_JP, **options)  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.PdfError, match="fontname names one Standard 14 variant"):
        pylopdf.Font("helv", weight=700)
    with pytest.raises(pylopdf.PdfError, match="fontindex requires"):
        pylopdf.Font("helv", fontindex=1)
    with pytest.raises(pylopdf.PdfError, match="fontindex requires"):
        pylopdf.Font(family="Noto Sans JP", fontindex=1)
    with pytest.raises(pylopdf.PdfError, match="is_serif"):
        pylopdf.Font(family="Noto Sans JP", is_serif=True)
    for weight in (0, 1001):
        with pytest.raises(ValueError, match="weight"):
            pylopdf.Font(family="Noto Sans JP", weight=weight)
    for weight in (True, 700.0, "bold"):
        with pytest.raises(TypeError, match="weight"):
            pylopdf.Font(family="Noto Sans JP", weight=weight)  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.PdfError, match="contradicts is_bold"):
        pylopdf.Font(language="ja", is_bold=True, weight=500)
    with pytest.raises(ValueError, match="font_language"):
        pylopdf.Font(language="fr")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="fontindex"):
        pylopdf.Font(fontfile=NOTO_SANS_JP, fontindex=-1)
    with pytest.raises(pylopdf.PdfError, match="invalid"):
        pylopdf.Font(fontbuffer=b"not a font")
    with pytest.raises(pylopdf.PdfError, match="invalid"):
        pylopdf.Font(fontfile=NOTO_SANS_JP, fontindex=1)
    with pytest.raises(TypeError, match="fontname"):
        pylopdf.Font(NOTO_SANS_JP)  # ty: ignore[invalid-argument-type]


def test_an_explicit_weight_must_match_a_static_face(regular: pylopdf.Font, bold_data: bytes) -> None:
    """A static face has one weight; pylopdf refuses others rather than imitating them."""
    as_given = _new_page()
    as_given[1].insert_text((20, 60), MIXED_LINE, font=regular, fontsize=18)
    for weight in (400, None):
        face = pylopdf.Font(fontfile=NOTO_SANS_JP, weight=weight)
        assert (face.name, face.weight) == ("Noto Sans JP Regular", 400)
        drawn = _new_page()
        drawn[1].insert_text((20, 60), MIXED_LINE, font=face, fontsize=18)
        assert drawn[0].to_bytes() == as_given[0].to_bytes()
    with pytest.raises(pylopdf.PdfError, match="no weight axis, and its only face is regular, not bold; load the bold"):
        pylopdf.Font(fontfile=NOTO_SANS_JP, weight=700)
    with pytest.raises(pylopdf.PdfError, match="its only face is regular, not weight-500"):
        pylopdf.Font(fontbuffer=NOTO_SANS_JP.read_bytes(), weight=500)
    # A face that declares weight 700 provides that weight and nothing else.
    assert pylopdf.Font(fontbuffer=bold_data, weight=700).is_bold
    with pytest.raises(pylopdf.PdfError, match="its only face is bold, not regular"):
        pylopdf.Font(fontbuffer=bold_data, weight=400)


def test_font_input_is_bounded() -> None:
    size = NOTO_SANS_JP.stat().st_size
    for source in ({"fontfile": NOTO_SANS_JP}, {"fontbuffer": NOTO_SANS_JP.read_bytes()}):
        with pytest.raises(pylopdf.LimitError) as exc_info:
            pylopdf.Font(**source, max_font_size=size - 1)  # ty: ignore[invalid-argument-type]
        assert exc_info.value.code == "font_input_size"
        assert pylopdf.Font(**source, max_font_size=size).weight == 400  # ty: ignore[invalid-argument-type]
    for value in (0, -1, True, 1.5):
        with pytest.raises((TypeError, ValueError), match="max_font_size"):
            pylopdf.Font(fontfile=NOTO_SANS_JP, max_font_size=value)  # ty: ignore[invalid-argument-type]


def test_font_parameter_rejects_other_font_sources(regular: pylopdf.Font) -> None:
    _doc, page = _new_page()
    for extra in (
        {"fontfile": NOTO_SANS_JP},
        {"fontbuffer": b"font"},
        {"fontindex": 1},
        {"font_language": "ja"},
    ):
        with pytest.raises(ValueError, match="font cannot be combined"):
            page.insert_text((10, 40), "x", font=regular, **extra)  # ty: ignore[invalid-argument-type]
        with pytest.raises(ValueError, match="font cannot be combined"):
            page.insert_textbox((10, 10, 100, 80), "x", font=regular, **extra)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match=r"pylopdf\.Font"):
        page.insert_text((10, 40), "x", font="helv")  # ty: ignore[invalid-argument-type]
    assert page.get_text() == ""


def test_textbox_with_font_keeps_its_no_draw_boundaries(regular: pylopdf.Font) -> None:
    doc, page = _new_page()
    before = doc.to_bytes()

    assert page.insert_textbox((10, 10, 110, 15), "折り返せない長さの本文", font=regular, fontsize=10) < 0
    assert page.insert_textbox((10, 10, 110, 25), "", font=regular, fontsize=10) == 15
    assert doc.to_bytes() == before


def test_language_and_family_select_installed_extra_faces() -> None:
    pytest.importorskip("pylopdf_fonts_jp")

    sans = pylopdf.Font(language="ja")
    serif = pylopdf.Font(language="ja", is_serif=True)

    assert (sans.name, sans.weight) == ("Noto Sans JP Regular", 400)
    assert serif.name == "Noto Serif JP Regular"
    assert pylopdf.Font(language="ja", weight=400).name == sans.name
    assert pylopdf.Font(family="noto sans jp").name == sans.name
    assert pylopdf.Font(family="Noto Serif JP").is_serif
    assert sans.text_length("見出し", 10) == pytest.approx(30)
    with pytest.raises(pylopdf.PdfError, match="no pylopdf font extra installs the family 'Arial'"):
        pylopdf.Font(family="Arial")


def test_a_missing_weight_raises_unless_regular_fallback_is_allowed() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    for options in ({"is_bold": True}, {"weight": 700}, {"is_italic": True}):
        with pytest.raises(pylopdf.PdfError, match=r"pylopdf\[jp\] installs one 'Noto Sans JP' face.*fontfile"):
            pylopdf.Font(language="ja", **options)  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.PdfError, match="'Noto Serif JP'"):
        pylopdf.Font(family="Noto Serif JP", is_bold=True)

    with pytest.warns(pylopdf.PylopdfWarning, match="no bold 'Noto Sans JP' face; using its own face") as record:
        fallback = pylopdf.Font(language="ja", is_bold=True, allow_regular_fallback=True)
    assert fallback.weight == 400
    assert record[0].filename == __file__


def test_language_requires_its_font_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: ())
    with pytest.raises(pylopdf.PdfError, match=r"language='ko' requires pylopdf\[ko\]"):
        pylopdf.Font(language="ko")
    with pytest.raises(pylopdf.PdfError, match=r"requires pylopdf\[jp\]"):
        pylopdf.Font(family="Noto Sans JP")
