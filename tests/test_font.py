"""Reusable OpenType faces, their measurement, and family/weight registration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import with_weight_class

import pylopdf

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
NOTO_SERIF_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSerifJP-Regular.otf"

# Latin kerning pairs, a ligature candidate, and full-width CJK in one line.
MIXED_LINE = "AVATAR office 見出し、本文。"


@pytest.fixture
def font_registry(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, int, bool], pylopdf.Font]:
    """Give each test an empty registry so registrations cannot leak."""
    registry: dict[tuple[str, int, bool], pylopdf.Font] = {}
    monkeypatch.setattr(pylopdf, "_REGISTERED_FONTS", registry)
    return registry


@pytest.fixture(scope="module")
def regular() -> pylopdf.Font:
    return pylopdf.Font(NOTO_SANS_JP)


@pytest.fixture(scope="module")
def bold_data() -> bytes:
    return with_weight_class(NOTO_SANS_JP.read_bytes(), 700)


def _new_page(width: float = 400, height: float = 200) -> tuple[pylopdf.Document, pylopdf.Page]:
    doc = pylopdf.Document()
    return doc, doc.new_page(width=width, height=height)


def _chars(page: pylopdf.Page) -> list[pylopdf.TextChar]:
    return [
        char
        for block in page.get_text("rawdict")["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        for char in span["chars"]
    ]


def test_font_reads_face_metadata_and_vertical_metrics(regular: pylopdf.Font) -> None:
    assert regular.name == "NotoSansJP-Regular"
    assert regular.family == "Noto Sans JP"
    assert regular.weight == 400
    assert regular.is_italic is False
    assert regular.units_per_em == 1000
    # OS/2 typographic metrics, which insert_textbox places lines with.
    assert regular.ascender == pytest.approx(0.88)
    assert regular.descender == pytest.approx(-0.12)
    assert repr(regular) == "Font(name='NotoSansJP-Regular', family='Noto Sans JP', weight=400)"


def test_font_loads_the_same_face_from_a_path_or_bytes(regular: pylopdf.Font) -> None:
    from_bytes = pylopdf.Font(fontbuffer=NOTO_SANS_JP.read_bytes())
    from_keyword = pylopdf.Font(fontfile=str(NOTO_SANS_JP))

    for font in (from_bytes, from_keyword):
        assert font.name == regular.name
        assert font.text_length(MIXED_LINE, 12) == regular.text_length(MIXED_LINE, 12)


def test_text_length_scales_with_size_and_includes_shaping(regular: pylopdf.Font) -> None:
    assert regular.text_length("") == 0
    # Full-width CJK advances one em per character.
    assert regular.text_length("見出し", 10) == pytest.approx(30)
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
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened[0].get_text().strip() == MIXED_LINE


def test_text_length_matches_textbox_layout(regular: pylopdf.Font) -> None:
    _doc, page = _new_page()
    rect = (20, 20, 380, 120)

    spare = page.insert_textbox(rect, "見出しAVATAR", font=regular, fontsize=16, align=pylopdf.TEXT_ALIGN_RIGHT)

    # One line occupies the face's ascent plus descent.
    assert spare == pytest.approx(rect[3] - rect[1] - (regular.ascender - regular.descender) * 16, abs=1e-6)
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

    assert actual[0].tobytes() == expected[0].tobytes()


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
        with pytest.raises(ValueError, match="one line"):
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


def test_has_glyph_reads_the_character_map(regular: pylopdf.Font) -> None:
    assert regular.has_glyph("髙")
    assert regular.has_glyph("A")
    assert not regular.has_glyph("🦀")
    with pytest.raises(ValueError, match="one-character"):
        regular.has_glyph("ab")
    with pytest.raises(TypeError, match="one-character"):
        regular.has_glyph(65)  # ty: ignore[invalid-argument-type]


def test_font_validates_sources_and_lookup_arguments() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        pylopdf.Font()
    with pytest.raises(ValueError, match="exactly one"):
        pylopdf.Font(NOTO_SANS_JP, fontbuffer=b"font")
    with pytest.raises(ValueError, match="exactly one"):
        pylopdf.Font(family="Noto Sans JP", language="ja")
    with pytest.raises(ValueError, match="as given"):
        pylopdf.Font(NOTO_SANS_JP, weight="bold")
    with pytest.raises(ValueError, match="as given"):
        pylopdf.Font(NOTO_SANS_JP, allow_regular_fallback=True)
    with pytest.raises(ValueError, match="fontindex requires"):
        pylopdf.Font(family="Noto Sans JP", fontindex=1)
    with pytest.raises(ValueError, match="serif"):
        pylopdf.Font(family="Noto Sans JP", serif=True)
    for weight in ("heavy", 0, 1001):
        with pytest.raises(ValueError, match="weight"):
            pylopdf.Font(family="Noto Sans JP", weight=weight)  # ty: ignore[invalid-argument-type]
    for weight in (True, 700.0, None):
        with pytest.raises(TypeError, match="weight"):
            pylopdf.Font(family="Noto Sans JP", weight=weight)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="font_language"):
        pylopdf.Font(language="fr")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="fontindex"):
        pylopdf.Font(NOTO_SANS_JP, fontindex=-1)
    with pytest.raises(pylopdf.PdfError, match="invalid"):
        pylopdf.Font(fontbuffer=b"not a font")
    with pytest.raises(pylopdf.PdfError, match="invalid"):
        pylopdf.Font(NOTO_SANS_JP, fontindex=1)


def test_font_input_is_bounded() -> None:
    size = NOTO_SANS_JP.stat().st_size
    for source in ({"fontfile": NOTO_SANS_JP}, {"fontbuffer": NOTO_SANS_JP.read_bytes()}):
        with pytest.raises(pylopdf.LimitError) as exc_info:
            pylopdf.Font(**source, max_font_size=size - 1)  # ty: ignore[invalid-argument-type]
        assert exc_info.value.code == "font_input_size"
        assert pylopdf.Font(**source, max_font_size=size).weight == 400  # ty: ignore[invalid-argument-type]
    for value in (0, -1, True, 1.5):
        with pytest.raises((TypeError, ValueError), match="max_font_size"):
            pylopdf.Font(NOTO_SANS_JP, max_font_size=value)  # ty: ignore[invalid-argument-type]


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
    before = doc.tobytes()

    assert page.insert_textbox((10, 10, 110, 15), "折り返せない長さの本文", font=regular, fontsize=10) < 0
    assert page.insert_textbox((10, 10, 110, 25), "", font=regular, fontsize=10) == 15
    assert doc.tobytes() == before


def test_register_font_enables_family_and_weight_lookup(
    font_registry: dict[tuple[str, int, bool], pylopdf.Font],
    bold_data: bytes,
) -> None:
    registered_regular = pylopdf.register_font(NOTO_SANS_JP)
    registered_bold = pylopdf.register_font(fontbuffer=bold_data)

    assert registered_bold.family == "Noto Sans JP"
    assert registered_bold.weight == 700
    assert set(font_registry) == {("noto sans jp", 400, False), ("noto sans jp", 700, False)}
    bold = pylopdf.Font(family="noto sans JP", weight="bold")
    assert bold.weight == 700
    assert pylopdf.Font(family="Noto Sans JP", weight=700).weight == 700
    assert pylopdf.Font(family="Noto Sans JP").weight == 400
    assert registered_regular.weight == 400

    # The looked-up face draws exactly what the registered bytes draw.
    expected, actual = (_new_page() for _ in range(2))
    expected[1].insert_text((20, 60), "太字", fontbuffer=bold_data, fontsize=18)
    actual[1].insert_text((20, 60), "太字", font=bold, fontsize=18)
    assert actual[0].tobytes() == expected[0].tobytes()


def test_registering_the_same_family_weight_and_style_replaces_it(
    font_registry: dict[tuple[str, int, bool], pylopdf.Font],
) -> None:
    first = pylopdf.register_font(NOTO_SANS_JP)
    second = pylopdf.register_font(fontbuffer=NOTO_SANS_JP.read_bytes())

    assert len(font_registry) == 1
    assert font_registry["noto sans jp", 400, False] is second
    assert first is not second


def test_a_missing_weight_raises_unless_regular_fallback_is_allowed(
    font_registry: dict[tuple[str, int, bool], pylopdf.Font],
) -> None:
    with pytest.raises(ValueError, match=r"no bold face of 'Noto Sans JP' is registered.*register_font"):
        pylopdf.Font(family="Noto Sans JP", weight="bold")
    with pytest.raises(ValueError, match="neither a bold nor a regular face of 'Noto Sans JP' is registered"):
        pylopdf.Font(family="Noto Sans JP", weight="bold", allow_regular_fallback=True)

    pylopdf.register_font(NOTO_SANS_JP)
    with pytest.raises(ValueError, match="no bold italic face"):
        pylopdf.Font(family="Noto Sans JP", weight="bold", italic=True)
    with pytest.warns(pylopdf.PylopdfWarning, match="no registered bold face; using its regular face") as record:
        fallback = pylopdf.Font(family="Noto Sans JP", weight="bold", allow_regular_fallback=True)
    assert fallback.weight == 400
    assert record[0].filename == __file__
    assert len(font_registry) == 1


def test_language_selects_the_installed_regular_face() -> None:
    pytest.importorskip("pylopdf_fonts_jp")

    sans = pylopdf.Font(language="ja")
    serif = pylopdf.Font(language="ja", serif=True)

    assert (sans.name, sans.weight) == ("NotoSansJP-Regular", 400)
    assert serif.name == "NotoSerifJP-Regular"
    assert sans.text_length("見出し", 10) == pytest.approx(30)


def test_language_bold_comes_from_a_registered_face_of_the_same_family(
    font_registry: dict[tuple[str, int, bool], pylopdf.Font],
    bold_data: bytes,
) -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    with pytest.raises(ValueError, match=r"pylopdf\[jp\] installs one 'Noto Sans JP' face; register its bold face"):
        pylopdf.Font(language="ja", weight="bold")
    with pytest.warns(pylopdf.PylopdfWarning, match="using its regular face"):
        fallback = pylopdf.Font(language="ja", weight="bold", allow_regular_fallback=True)
    assert fallback.weight == 400

    pylopdf.register_font(fontbuffer=bold_data)

    assert pylopdf.Font(language="ja", weight="bold").weight == 700
    # The serif face belongs to another family, so its bold is still missing.
    with pytest.raises(ValueError, match="Noto Serif JP"):
        pylopdf.Font(language="ja", serif=True, weight="bold")
    assert font_registry


def test_language_requires_its_font_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pylopdf._bundled_font_providers", lambda: ())
    with pytest.raises(ValueError, match=r"language='ko' requires pylopdf\[ko\]"):
        pylopdf.Font(language="ko")


def test_register_font_validates_its_input(font_registry: dict[tuple[str, int, bool], pylopdf.Font]) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        pylopdf.register_font()
    with pytest.raises(pylopdf.PdfError, match="invalid"):
        pylopdf.register_font(fontbuffer=b"not a font")
    assert not font_registry
    serif = pylopdf.register_font(NOTO_SERIF_JP)
    assert font_registry == {("noto serif jp", 400, False): serif}
