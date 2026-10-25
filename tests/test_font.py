"""Fonts: the faces that draw text, and the measurement of one line and of wrapped text."""

from __future__ import annotations

import math
import os
import re
import struct
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, get_args

import pytest

import pylopdf

if TYPE_CHECKING:
    from pylopdf import StandardFontName

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
NOTO_SERIF_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSerifJP-Regular.otf"

# Latin kerning pairs, a ligature candidate, and full-width CJK in one line.
MIXED_LINE = "AVATAR office 見出し、本文。"
# A paragraph without spaces, which wraps at UAX #14 opportunities between ideographs.
PARAGRAPH = "現地調査の結果、配管の腐食を確認しました。詳細は別紙の写真台帳を参照してください。"
SUBSET_NAME = re.compile(rb"/BaseFont\s*/([A-Z]{6})\+([A-Za-z0-9-]+)")


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
    return pylopdf.Font.from_file(NOTO_SANS_JP)


@pytest.fixture(scope="module")
def bold_data() -> bytes:
    return _with_weight_class(NOTO_SANS_JP.read_bytes(), 700)


def _new_page(width: float = 400, height: float = 200) -> tuple[pylopdf.Document, pylopdf.Page]:
    doc = pylopdf.Document()
    return doc, doc.pages.new((width, height))


def _glyphs(page: pylopdf.Page) -> list[pylopdf.Glyph]:
    return [glyph for run in page.content(paths=False, images=False).text_runs for glyph in run.glyphs]


def _end(glyph: pylopdf.Glyph) -> float:
    """Return where a glyph's advance ends on its baseline."""
    assert glyph.advance is not None
    return glyph.origin.x + glyph.advance


def _baselines(page: pylopdf.Page) -> list[float]:
    return [line.spans[0].origin.y for block in page.layout().blocks for line in block.lines]


def test_from_file_reads_face_metadata_and_vertical_metrics(regular: pylopdf.Font) -> None:
    assert regular.name == "Noto Sans JP Regular"
    assert regular.family == "Noto Sans JP"
    assert regular.weight == 400
    assert regular.is_bold is False
    assert regular.is_italic is False
    assert regular.is_serif is False
    assert pylopdf.Font.from_file(NOTO_SERIF_JP).is_serif is True
    assert regular.units_per_em == 1000
    # The hhea metrics; the face does not set USE_TYPO_METRICS, so its OS/2 values (0.88, -0.12) are unused.
    assert regular.ascender == pytest.approx(1.16)
    assert regular.descender == pytest.approx(-0.288)
    assert repr(regular) == "Font('Noto Sans JP Regular')"


def test_a_path_or_bytes_loads_the_same_face(regular: pylopdf.Font) -> None:
    data = NOTO_SANS_JP.read_bytes()
    faces = [
        pylopdf.Font.from_file(str(NOTO_SANS_JP)),
        pylopdf.Font.from_bytes(data),
        pylopdf.Font.from_bytes(bytearray(data)),
        pylopdf.Font.from_bytes(memoryview(data)),
    ]
    for face in faces:
        assert face.name == regular.name
        assert face.text_width(MIXED_LINE, 12) == regular.text_width(MIXED_LINE, 12)


def test_text_width_scales_with_size_and_includes_shaping(regular: pylopdf.Font) -> None:
    assert regular.text_width("", 11) == 0
    # Full-width CJK advances one em per character.
    assert regular.text_width("見出し", 10) == pytest.approx(30)
    assert regular.text_width(MIXED_LINE, 22) == pytest.approx(2 * regular.text_width(MIXED_LINE, 11))
    # Kerning pulls "AV" closer than the two glyphs measured alone.
    assert regular.text_width("AV", 100) < regular.text_width("A", 100) + regular.text_width("V", 100)


def test_text_width_equals_the_drawn_advance(regular: pylopdf.Font) -> None:
    doc, page = _new_page()
    x, y, size = 30.25, 80.0, 13.5

    with page.draw() as canvas:
        canvas.text(MIXED_LINE, (x, y), font=regular, size=size)

    glyphs = _glyphs(page)
    texts = [glyph.text or "" for glyph in glyphs]
    assert "".join(texts) == MIXED_LINE
    assert glyphs[0].origin.x == pytest.approx(x, abs=1e-3)
    # The last glyph ends at its origin plus its advance.
    assert _end(glyphs[-1]) == pytest.approx(x + regular.text_width(MIXED_LINE, size), abs=1e-2)
    # A prefix ends where the glyph after it begins.
    prefix = "AVATAR office 見出し、"
    offsets = [0]
    for text in texts:
        offsets.append(offsets[-1] + len(text))
    following = glyphs[offsets.index(len(prefix))]
    assert following.origin.x == pytest.approx(x + regular.text_width(prefix, size), abs=1e-2)
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].text().strip() == MIXED_LINE


def test_text_width_places_a_right_aligned_textbox_line(regular: pylopdf.Font) -> None:
    _doc, page = _new_page()
    rect = (20, 20, 380, 120)

    with page.draw() as canvas:
        canvas.textbox(rect, "見出しAVATAR", font=regular, size=16, align="right")

    line = page.layout().blocks[0].lines[0]
    assert line.bbox.x0 == pytest.approx(rect[2] - regular.text_width("見出しAVATAR", 16), abs=1e-2)
    assert line.bbox.x1 == pytest.approx(rect[2], abs=1e-2)


def test_a_bold_face_loaded_from_its_file_is_shaped_and_embedded_as_a_subset(bold_data: bytes) -> None:
    bold = pylopdf.Font.from_bytes(bold_data)
    assert (bold.weight, bold.is_bold, bold.family) == (700, True, "Noto Sans JP")
    assert pylopdf.Font.from_bytes(bold_data, weight=700).name == bold.name

    doc, page = _new_page()
    with page.draw() as canvas:
        fit = canvas.textbox((20, 20, 380, 180), PARAGRAPH * 2, font=bold, size=12)
    data = doc.to_bytes(compress=False)

    assert fit.fits
    # The Type0 font and its CIDFont name the subset of the face that was loaded.
    assert {match[1] for match in SUBSET_NAME.findall(data)} == {
        b"NotoSansJP-Regular",
        b"NotoSansJP-Regular-Identity-H",
    }
    # A subset of a few dozen glyphs, not the 4.5 MB face.
    assert len(data) < 100_000
    reopened = pylopdf.open(data)
    assert reopened.pages[0].text().replace("\n", "") == PARAGRAPH * 2


@pytest.mark.parametrize(
    ("name", "family", "flags"),
    [
        ("Helvetica", "Helvetica", (False, False, False)),
        ("Helvetica-Bold", "Helvetica", (True, False, False)),
        ("Helvetica-Oblique", "Helvetica", (False, True, False)),
        ("Times-Roman", "Times", (False, False, True)),
        ("Times-BoldItalic", "Times", (True, True, True)),
        ("Courier", "Courier", (False, False, False)),
        ("Symbol", "Symbol", (False, False, False)),
        ("ZapfDingbats", "ZapfDingbats", (False, False, False)),
    ],
)
def test_standard_14_fonts_by_postscript_name(
    name: StandardFontName,
    family: str,
    flags: tuple[bool, bool, bool],
) -> None:
    font = pylopdf.Font.standard(name)

    assert (font.name, font.family) == (name, family)
    assert (font.is_bold, font.is_italic, font.is_serif) == flags
    assert font.weight == (700 if flags[0] else 400)
    assert font.units_per_em == 1000
    assert font.ascender > 0 > font.descender


def test_standard_font_names_are_the_14_postscript_names() -> None:
    assert get_args(pylopdf.StandardFontName) == (
        "Courier",
        "Courier-Oblique",
        "Courier-Bold",
        "Courier-BoldOblique",
        "Helvetica",
        "Helvetica-Oblique",
        "Helvetica-Bold",
        "Helvetica-BoldOblique",
        "Times-Roman",
        "Times-Italic",
        "Times-Bold",
        "Times-BoldItalic",
        "Symbol",
        "ZapfDingbats",
    )
    for name in get_args(pylopdf.StandardFontName):
        assert pylopdf.Font.standard(name).name == name
    for constant in ("Base14_fontnames", "Base14_fontdict"):
        with pytest.raises(AttributeError, match=r"Font\.standard"):
            getattr(pylopdf, constant)


def test_standard_takes_only_the_14_postscript_names() -> None:
    for name in ("helv", "helvetica", "Arial", "cjk", ""):
        with pytest.raises(ValueError, match="PostScript name of a Standard 14 font"):
            pylopdf.Font.standard(name)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="name"):
        pylopdf.Font.standard(None)  # ty: ignore[invalid-argument-type]


def test_standard_14_fonts_measure_and_draw_with_afm_widths() -> None:
    helvetica = pylopdf.Font.standard("Helvetica")
    bold = pylopdf.Font.standard("Helvetica-Bold")
    # Adobe AFM widths: H 722, e 556, l 222, o 556, space 278, W 944, r 333, d 556.
    assert helvetica.text_width("Hello World", 11) == pytest.approx(56.837)
    assert helvetica.has_glyph("A")
    assert helvetica.has_glyph("é")
    assert not helvetica.has_glyph("あ")
    with pytest.raises(pylopdf.PdfError, match="outside WinAnsi"):
        helvetica.text_width("あ", 11)

    doc, page = _new_page()
    with page.draw() as canvas:
        canvas.text("Hello", (10, 40), font=bold, size=14)
        with pytest.raises(ValueError, match="WinAnsi"):
            canvas.text("日本語", (10, 80), font=helvetica)
    assert b"/BaseFont/Helvetica-Bold" in doc.to_bytes(compress=False)
    (run,) = page.content().text_runs
    assert _end(run.glyphs[-1]) == pytest.approx(10 + bold.text_width("Hello", 14), abs=1e-2)


def test_one_font_draws_on_several_documents_and_threads(regular: pylopdf.Font) -> None:
    def draw(index: int) -> tuple[float, str]:
        _doc, page = _new_page()
        text = f"見出し{index}"
        with page.draw() as canvas:
            canvas.text(text, (10, 40), font=regular, size=12)
        return regular.text_width(text, 12), page.text().strip()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(draw, range(8)))

    assert [text for _, text in results] == [f"見出し{index}" for index in range(8)]
    assert {width for width, _ in results} == {regular.text_width("見出し0", 12)}


def test_text_width_rejects_line_breaks_missing_glyphs_and_bad_input(regular: pylopdf.Font) -> None:
    for text in ("一行目\n二行目", "a\rb"):
        with pytest.raises(pylopdf.PdfError, match="one line"):
            regular.text_width(text, 11)
    with pytest.raises(pylopdf.PdfError, match=r"does not contain all glyphs.*U\+1F980"):
        regular.text_width("crab 🦀", 11)
    for size in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="size"):
            regular.text_width("x", size)
    for size in ("12pt", None, True):
        with pytest.raises(TypeError, match="size"):
            regular.text_width("x", size)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="text"):
        regular.text_width(b"bytes", 11)  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.LimitError, match="UTF-8") as exc_info:
        regular.text_width("あ" * 4, 11, max_text_size=11)
    assert exc_info.value.code == "text_input_size"
    assert regular.text_width("あ" * 4, 11, max_text_size=None) == pytest.approx(44)


def test_drawing_names_the_missing_character(regular: pylopdf.Font) -> None:
    _doc, page = _new_page()
    with page.draw() as canvas, pytest.raises(pylopdf.PdfError, match=r"no glyph for U\+1F980 \('🦀'\)"):
        canvas.text("🦀", (10, 40), font=regular)


def test_has_glyph_takes_one_character(regular: pylopdf.Font) -> None:
    assert regular.has_glyph("髙") is True
    assert regular.has_glyph("A") is True
    assert regular.has_glyph("🦀") is False
    for value in ("ab", ""):
        with pytest.raises(ValueError, match="one-character"):
            regular.has_glyph(value)
    for value in (65, None, b"A"):
        with pytest.raises(TypeError, match="one-character"):
            regular.has_glyph(value)  # ty: ignore[invalid-argument-type]


def test_font_has_no_public_constructor() -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = [
        ((), {}),
        (("helv",), {}),
        ((), {"fontfile": NOTO_SANS_JP}),
        ((), {"language": "ja"}),
    ]
    for args, kwargs in calls:
        with pytest.raises(TypeError, match=r"Font has no public constructor: Font\.standard\(name\)"):
            pylopdf.Font(*args, **kwargs)
    with pytest.raises(AttributeError, match="text_length"):
        cast("Any", pylopdf.Font.standard("Helvetica")).text_length("x", 11)


def test_loading_validates_its_arguments() -> None:
    for index in (-1, 2**32, True, 1.0):
        with pytest.raises(ValueError, match="index"):
            pylopdf.Font.from_file(NOTO_SANS_JP, index=index)  # ty: ignore[invalid-argument-type]
    for weight in (0, 1001):
        with pytest.raises(ValueError, match="weight"):
            pylopdf.Font.from_file(NOTO_SANS_JP, weight=weight)
    for weight in (True, 700.0, "bold"):
        with pytest.raises(TypeError, match="weight"):
            pylopdf.Font.from_bytes(b"font", weight=weight)  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.PdfError, match="invalid"):
        pylopdf.Font.from_bytes(b"not a font")
    with pytest.raises(pylopdf.PdfError, match="invalid"):
        pylopdf.Font.from_file(NOTO_SANS_JP, index=1)
    with pytest.raises(TypeError, match="path"):
        pylopdf.Font.from_file(NOTO_SANS_JP.read_bytes())  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="data"):
        pylopdf.Font.from_bytes(str(NOTO_SANS_JP))  # ty: ignore[invalid-argument-type]


def test_an_explicit_weight_must_match_a_static_face(regular: pylopdf.Font, bold_data: bytes) -> None:
    """A static face has one weight; pylopdf refuses others rather than imitating them."""
    for weight in (400, None):
        face = pylopdf.Font.from_file(NOTO_SANS_JP, weight=weight)
        assert (face.name, face.weight) == (regular.name, 400)
    with pytest.raises(pylopdf.PdfError, match="no weight axis, and its only face is regular, not bold; load the bold"):
        pylopdf.Font.from_file(NOTO_SANS_JP, weight=700)
    with pytest.raises(pylopdf.PdfError, match="its only face is regular, not weight-500"):
        pylopdf.Font.from_bytes(NOTO_SANS_JP.read_bytes(), weight=500)
    # A face that declares weight 700 provides that weight and nothing else.
    assert pylopdf.Font.from_bytes(bold_data, weight=700).is_bold
    with pytest.raises(pylopdf.PdfError, match="its only face is bold, not regular"):
        pylopdf.Font.from_bytes(bold_data, weight=400)


def test_font_input_is_bounded() -> None:
    size = NOTO_SANS_JP.stat().st_size
    data = NOTO_SANS_JP.read_bytes()
    loads = [
        lambda limit: pylopdf.Font.from_file(NOTO_SANS_JP, max_size=limit),
        lambda limit: pylopdf.Font.from_bytes(data, max_size=limit),
        lambda limit: pylopdf.Font.from_bytes(memoryview(data), max_size=limit),
    ]
    for load in loads:
        with pytest.raises(pylopdf.LimitError) as exc_info:
            load(size - 1)
        assert exc_info.value.code == "font_input_size"
        assert load(size).weight == 400
    for value in (0, -1, True, 1.5):
        with pytest.raises((TypeError, ValueError), match="max_size"):
            pylopdf.Font.from_file(NOTO_SANS_JP, max_size=value)  # ty: ignore[invalid-argument-type]
    assert pylopdf.Font.from_file(os.fspath(NOTO_SANS_JP), max_size=None).weight == 400


def test_installed_selects_the_faces_of_font_extras() -> None:
    pytest.importorskip("pylopdf_fonts_jp")

    sans = pylopdf.Font.installed(language="ja")
    serif = pylopdf.Font.installed(language="ja", serif=True)

    assert (sans.name, sans.weight) == ("Noto Sans JP Regular", 400)
    assert serif.name == "Noto Serif JP Regular"
    assert pylopdf.Font.installed(language="ja", weight=400).name == sans.name
    assert pylopdf.Font.installed(family="noto sans jp").name == sans.name
    assert pylopdf.Font.installed(family="Noto Serif JP").is_serif
    assert sans.text_width("見出し", 10) == pytest.approx(30)
    with pytest.raises(pylopdf.PdfError, match="no pylopdf font extra installs the family 'Arial'"):
        pylopdf.Font.installed(family="Arial")


def test_installed_validates_its_arguments() -> None:
    with pytest.raises(ValueError, match="pass language or family"):
        pylopdf.Font.installed()
    with pytest.raises(ValueError, match="pass language or family"):
        pylopdf.Font.installed(language="ja", family="Noto Sans JP")
    with pytest.raises(ValueError, match="serif selects the face of a language"):
        pylopdf.Font.installed(family="Noto Sans JP", serif=True)
    with pytest.raises(ValueError, match="font_language"):
        pylopdf.Font.installed(language="fr")  # ty: ignore[invalid-argument-type]
    for weight in (0, 1001):
        with pytest.raises(ValueError, match="weight"):
            pylopdf.Font.installed(language="ja", weight=weight)


def test_a_weight_or_style_that_an_extra_lacks_raises() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    with pytest.raises(pylopdf.PdfError, match=r"pylopdf\[jp\] installs one 'Noto Sans JP' face.*Font\.from_file"):
        pylopdf.Font.installed(language="ja", weight=700)
    with pytest.raises(pylopdf.PdfError, match=r"installs one 'Noto Sans JP' face; load its regular italic face"):
        pylopdf.Font.installed(language="ja", italic=True)
    with pytest.raises(pylopdf.PdfError, match="'Noto Serif JP'"):
        pylopdf.Font.installed(family="Noto Serif JP", weight=700)


def test_installed_requires_its_font_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: ())
    with pytest.raises(pylopdf.PdfError, match=r"language='ko' requires pylopdf\[ko\]"):
        pylopdf.Font.installed(language="ko")
    with pytest.raises(pylopdf.PdfError, match=r"requires pylopdf\[jp\]"):
        pylopdf.Font.installed(family="Noto Sans JP")


@pytest.mark.parametrize("standard", [False, True])
def test_text_height_is_the_height_that_a_textbox_needs(regular: pylopdf.Font, standard: bool) -> None:  # noqa: FBT001
    font = pylopdf.Font.standard("Times-Roman") if standard else regular
    text = "A paragraph that wraps onto several lines in a narrow box. " * 3 if standard else PARAGRAPH * 2
    size, width, x, top = 10.5, 150.0, 30.0, 40.25

    height = font.text_height(text, size, width=width)
    _doc, page = _new_page(400, 400)
    with page.draw() as canvas:
        exact = canvas.textbox((x, top, x + width, top + height), text, font=font, size=size)
        short = canvas.textbox((x, 300, x + width, 300 + height - 0.5), text, font=font, size=size)

    assert exact == pylopdf.TextFit(spare_height=0)
    assert short.spare_height == pytest.approx(-0.5)
    # Lines lie one line height apart, the font's ascender minus its descender.
    line_height = (font.ascender - font.descender) * size
    baselines = _baselines(page)
    assert len(baselines) > 2
    assert height == pytest.approx(len(baselines) * line_height)
    assert baselines[0] == pytest.approx(top + font.ascender * size, abs=1e-3)
    gaps = [later - earlier for earlier, later in pairwise(baselines)]
    assert gaps == pytest.approx([line_height] * len(gaps), abs=1e-3)
    # The descender of the last line ends at the bottom of the box.
    assert baselines[-1] - font.descender * size == pytest.approx(top + height, abs=1e-3)


def test_text_height_follows_line_height_and_line_breaks(regular: pylopdf.Font) -> None:
    size = 10.0
    extent = (regular.ascender - regular.descender) * size

    assert regular.text_height("一行", size, width=100) == pytest.approx(extent)
    assert regular.text_height("一行\n二行\r\n三行\r四行", size, width=100) == pytest.approx(4 * extent)
    # An empty line between paragraphs counts, and so does a trailing line break.
    assert regular.text_height("一\n\n三", size, width=100) == pytest.approx(3 * extent)
    assert regular.text_height("一\n", size, width=100) == pytest.approx(2 * extent)
    # A line height spaces the baselines; the first and last lines keep the font's extent.
    assert regular.text_height("一\n二\n三", size, width=100, line_height=2) == pytest.approx(extent + 2 * 2 * size)
    # Tabs are single spaces, as in the textbox.
    assert regular.text_height("a\tb", size, width=100) == regular.text_height("a b", size, width=100)
    assert regular.text_height("", size, width=100) == 0
    assert regular.text_height(PARAGRAPH, size, width=1000) == pytest.approx(extent)
    # Narrower boxes wrap the paragraph onto more lines.
    heights = [regular.text_height(PARAGRAPH, size, width=width) for width in (400, 200, 100, 50)]
    assert heights == sorted(heights)
    assert heights[0] < heights[-1]
    assert heights[-1] == pytest.approx(round(heights[-1] / extent) * extent)


def test_text_and_textbox_space_lines_alike(regular: pylopdf.Font) -> None:
    lines = "一行目\n二行目\n三行目"
    size = 12.0
    top = 50 - regular.ascender * size
    _doc, page = _new_page(400, 300)
    with page.draw() as canvas:
        canvas.text(lines, (20, 50), font=regular, size=size)
        canvas.textbox((200, top, 380, 280), lines, font=regular, size=size)
        canvas.text(lines, (20, 150), font=regular, size=size, line_height=1.5)
        canvas.textbox((200, top + 100, 380, 280), lines, font=regular, size=size, line_height=1.5)
    by_column: dict[bool, list[float]] = {False: [], True: []}
    for block in page.layout().blocks:
        for line in block.lines:
            by_column[line.bbox.x0 > 100].append(line.spans[0].origin.y)
    assert len(by_column[False]) == 6
    assert sorted(by_column[False]) == pytest.approx(sorted(by_column[True]), abs=1e-3)


def test_text_height_validates_its_input(regular: pylopdf.Font) -> None:
    with pytest.raises(ValueError, match="size"):
        regular.text_height("x", 0, width=100)
    with pytest.raises(ValueError, match="width"):
        regular.text_height("x", 10, width=-1)
    with pytest.raises(ValueError, match="line_height"):
        regular.text_height("x", 10, width=100, line_height=0)
    with pytest.raises(TypeError, match="width"):
        regular.text_height("x", 10, width="wide")  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="text"):
        regular.text_height(None, 10, width=100)  # ty: ignore[invalid-argument-type]
    with pytest.raises(pylopdf.PdfError, match="too narrow"):
        regular.text_height("見出し", 10, width=5)
    with pytest.raises(pylopdf.PdfError, match=r"U\+1F980"):
        regular.text_height("🦀", 10, width=100)
    with pytest.raises(pylopdf.LimitError) as exc_info:
        regular.text_height("あ" * 4, 10, width=100, max_text_size=11)
    assert exc_info.value.code == "text_input_size"
    with pytest.raises(pylopdf.LimitError) as exc_info:
        regular.text_height("\n" * 4096, 10, width=100)
    assert exc_info.value.code == "text_line_count"
    with pytest.raises(pylopdf.LimitError) as exc_info:
        regular.text_height("あ" * 5000, 10, width=10)
    assert exc_info.value.code == "text_line_count"
    assert regular.text_height("\n" * 4096, 10, width=100, max_text_size=None) > 0


@pytest.mark.parametrize(
    "name",
    ["Helvetica", "Times-Roman", "Times-Bold", "Courier-Bold", "Courier-BoldOblique"],
)
def test_standard_14_fonts_draw_under_their_postscript_names(name: StandardFontName) -> None:
    doc = pylopdf.Document()
    page = doc.pages.new()
    font = pylopdf.Font.standard(name)

    with page.draw() as canvas:
        canvas.text("Named", (40, 60), font=font)
        assert canvas.textbox((40, 100, 300, 160), "Boxed", font=font).fits

    data = doc.to_bytes(compress=False)
    assert f"/BaseFont/{name}".encode() in data
    text = pylopdf.open(data).pages[0].text()
    assert all(word in text for word in ("Named", "Boxed"))


def test_symbol_fonts_draw_with_their_built_in_encoding() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new()

    with page.draw() as canvas:
        assert canvas.textbox((40, 40, 300, 100), "abc", font=pylopdf.Font.standard("Symbol")).fits
        canvas.text("abc", (40, 140), font=pylopdf.Font.standard("ZapfDingbats"))

    data = doc.to_bytes(compress=False)
    assert b"/BaseFont/Symbol" in data
    assert b"/BaseFont/ZapfDingbats" in data
    assert b"WinAnsiEncoding" not in data


NOTO_SANS_HEBREW = FONT_ROOT / "pylopdf-fonts-he" / "src" / "pylopdf_fonts_he" / "NotoSansHebrew-Variable.ttf"


def test_an_opentype_face_reports_its_box_glyph_count_and_coverage(regular: pylopdf.Font) -> None:
    # pymupdf 2.0 reports the same box, glyph count, and number of code points.
    assert tuple(regular.bbox) == pytest.approx((-1.002, -1.048, 2.928, 1.808))
    assert regular.glyph_count == 17936
    assert regular.is_monospaced is False
    chars = regular.chars()
    assert len(chars) == 16732
    assert list(chars) == sorted(chars)
    assert "A" in chars
    assert "髙" in chars
    assert "🦀" not in chars
    assert all(regular.has_glyph(char) for char in chars)


def test_char_widths_are_the_advances_of_characters_drawn_alone(regular: pylopdf.Font) -> None:
    assert regular.char_widths("Aあ", 10) == pytest.approx((6.08, 10.0))
    assert regular.char_widths("A", 1) == pytest.approx((0.608,))
    for char in "AVofi見。":
        assert regular.char_widths(char, 7)[0] == pytest.approx(regular.text_width(char, 7))
    # Shaping kerns "AV", which the widths of the characters alone do not.
    assert sum(regular.char_widths("AV", 10)) > regular.text_width("AV", 10)
    assert regular.char_widths("", 10) == ()


def test_char_bbox_is_the_ink_box_that_drawing_paints(regular: pylopdf.Font) -> None:
    _doc, page = _new_page()
    with page.draw() as canvas:
        canvas.text("Aあg", (40, 150), font=regular, size=100)
    for glyph, char in zip(_glyphs(page), "Aあg", strict=True):
        box = regular.char_bbox(char)
        assert glyph.bbox is not None
        expected = (
            glyph.origin.x + box.x0 * 100,
            glyph.origin.y - box.y1 * 100,
            glyph.origin.x + box.x1 * 100,
            glyph.origin.y - box.y0 * 100,
        )
        assert tuple(glyph.bbox) == pytest.approx(expected, abs=0.01)
    assert regular.char_bbox("g").y0 < 0
    assert regular.char_bbox(" ") == pylopdf.Rect(0, 0, 0, 0)


@pytest.mark.parametrize("name", get_args(pylopdf.StandardFontName))
def test_standard_14_fonts_report_their_urw_box_and_afm_metrics(name: StandardFontName) -> None:
    font = pylopdf.Font.standard(name)
    assert font.bbox.y1 == pytest.approx(font.ascender)
    assert font.bbox.y0 == pytest.approx(font.descender)
    assert font.is_monospaced is name.startswith("Courier")
    assert font.program() is None
    chars = font.chars()
    assert chars
    assert all(font.has_glyph(char) for char in chars)
    assert sum(font.char_widths(chars, 9)) == pytest.approx(font.text_width(chars, 9))


def test_standard_14_metrics_are_adobes() -> None:
    helvetica = pylopdf.Font.standard("Helvetica")
    # The URW box, as pymupdf reports it; Adobe's FontBBox is -166 -225 1000 931.
    assert tuple(helvetica.bbox) == pytest.approx((-0.21, -0.299, 1.032, 1.075))
    assert helvetica.glyph_count == 315
    assert helvetica.char_widths("Hi!", 10) == pytest.approx((7.22, 2.22, 2.78))
    assert tuple(helvetica.char_bbox("A")) == pytest.approx((0.014, 0.0, 0.654, 0.718))
    assert "é" in helvetica.chars()
    assert "Ā" not in helvetica.chars()
    symbol = pylopdf.Font.standard("Symbol")
    # Symbol draws its built-in encoding, addressed through WinAnsi bytes: "a" is alpha.
    assert symbol.char_widths("a", 10) == pytest.approx((6.31,))


def test_a_variable_instance_measures_at_its_weight() -> None:
    thin = pylopdf.Font.from_file(NOTO_SANS_HEBREW)
    bold = pylopdf.Font.from_file(NOTO_SANS_HEBREW, weight=700)
    assert thin.glyph_count == bold.glyph_count
    assert thin.chars() == bold.chars()
    for font in (thin, bold):
        assert font.char_widths("ש", 20)[0] == pytest.approx(font.text_width("ש", 20))
    assert bold.char_widths("ש", 20)[0] > thin.char_widths("ש", 20)[0]
    assert bold.char_bbox("ש") != thin.char_bbox("ש")
    assert bold.program() == thin.program() == NOTO_SANS_HEBREW.read_bytes()


def test_program_returns_the_bytes_the_face_was_loaded_from(regular: pylopdf.Font) -> None:
    data = NOTO_SANS_JP.read_bytes()
    assert regular.program() == data
    assert pylopdf.Font.from_bytes(data).program() == data


def test_character_metrics_validate_their_input(regular: pylopdf.Font) -> None:
    with pytest.raises(pylopdf.PdfError, match=r"no glyph for '🦀' \(U\+1F980\)"):
        regular.char_widths("A🦀", 10)
    with pytest.raises(pylopdf.PdfError, match="no glyph"):
        regular.char_bbox("🦀")
    with pytest.raises(pylopdf.PdfError, match="no glyph"):
        pylopdf.Font.standard("Helvetica").char_widths("Ā", 10)
    for value in ("ab", ""):
        with pytest.raises(ValueError, match="one-character"):
            regular.char_bbox(value)
    with pytest.raises(TypeError, match="one-character"):
        regular.char_bbox(65)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="text must be a string"):
        regular.char_widths(b"A", 10)  # ty: ignore[invalid-argument-type]
    for size in (0, -1, math.nan):
        with pytest.raises(ValueError, match="size"):
            regular.char_widths("A", size)
    with pytest.raises(pylopdf.LimitError) as excinfo:
        regular.char_widths("A" * 11, 10, max_text_size=10)
    assert excinfo.value.code == "text_input_size"
    assert len(regular.char_widths("A" * 11, 10, max_text_size=None)) == 11


def test_glyph_names_follow_the_adobe_glyph_list(regular: pylopdf.Font) -> None:
    assert pylopdf.glyph_name("A") == "A"
    assert pylopdf.glyph_name("é") == "eacute"
    # The AGL names U+2192 arrowright; the ITC Zapf Dingbats list names it a161.
    assert pylopdf.glyph_name("→") == "arrowright"
    assert pylopdf.glyph_name("✔") == "a20"
    assert pylopdf.glyph_name("Δ") == "Deltagreek"
    assert pylopdf.glyph_name("∆") == "Delta"
    assert pylopdf.glyph_name("一") == "uni4E00"
    assert pylopdf.glyph_name("😀") == "u1F600"
    assert pylopdf.glyph_text("f_f_i") == "ffi"
    assert pylopdf.glyph_text("uni4E00") == "一"
    assert pylopdf.glyph_text("u1F600") == "😀"
    assert pylopdf.glyph_text("A.swash") == "A"
    assert pylopdf.glyph_text("a161") == "→"
    assert pylopdf.glyph_text("nonsense") == ""
    for font in (regular, pylopdf.Font.standard("Helvetica"), pylopdf.Font.standard("Symbol")):
        assert all(pylopdf.glyph_text(pylopdf.glyph_name(char)) == char for char in font.chars())
    with pytest.raises(ValueError, match="one-character"):
        pylopdf.glyph_name("ab")
    with pytest.raises(TypeError, match="name must be a string"):
        pylopdf.glyph_text(None)  # ty: ignore[invalid-argument-type]
