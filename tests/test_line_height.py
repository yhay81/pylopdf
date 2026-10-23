"""Font metrics and the line placement of canvas text and textboxes.

pymupdf is not a test dependency. The metrics below, ``Font.ascender`` and
``Font.descender``, and the baselines of canvas text, which spaces lines as
pymupdf 1.28.2's ``Page.insert_text`` did, were measured with pymupdf 1.28.2
from the ``bench`` dependency group at ``fontsize=10``. pymupdf reports its
metrics as single-precision floats, so they are recorded here in the font
units they come from. A canvas textbox places lines as canvas text does, the
first baseline one ascender below the top of its box.
"""

from __future__ import annotations

import math
import re
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import pylopdf

if TYPE_CHECKING:
    from pylopdf import StandardFontName

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
NOTO_SANS_HEBREW = FONT_ROOT / "pylopdf-fonts-he" / "src" / "pylopdf_fonts_he" / "NotoSansHebrew-Variable.ttf"

# pymupdf's Font(name) metrics of the Standard 14 fonts: the bounding boxes of
# the URW fonts that MuPDF draws them with, not Adobe's AFM metrics.
STANDARD_METRICS: dict[StandardFontName, tuple[float, float]] = {
    "Courier": (0.932, -0.317),
    "Courier-Oblique": (0.92, -0.317),
    "Courier-Bold": (1.007, -0.393),
    "Courier-BoldOblique": (0.997, -0.393),
    "Helvetica": (1.075, -0.299),
    "Helvetica-Oblique": (1.07, -0.284),
    "Helvetica-Bold": (1.07, -0.307),
    "Helvetica-BoldOblique": (1.073, -0.309),
    "Times-Roman": (1.053, -0.281),
    "Times-Italic": (0.951, -0.27),
    "Times-Bold": (1.044, -0.341),
    "Times-BoldItalic": (0.972, -0.324),
    "Symbol": (1.01, -0.293),
    "ZapfDingbats": (0.819, -0.144),
}

# pymupdf's Font(fontfile=...) metrics of the faces that the font extras install.
FACE_METRICS = {
    "pylopdf-fonts-ar/src/pylopdf_fonts_ar/NotoNaskhArabic-Variable.ttf": (1.069, -0.634),
    "pylopdf-fonts-ar/src/pylopdf_fonts_ar/NotoSansArabic-Variable.ttf": (1.374, -0.738),
    "pylopdf-fonts-he/src/pylopdf_fonts_he/NotoSansHebrew-Variable.ttf": (1.068, -0.292),
    "pylopdf-fonts-he/src/pylopdf_fonts_he/NotoSerifHebrew-Variable.ttf": (0.896, -0.29),
    "pylopdf-fonts-hi/src/pylopdf_fonts_hi/NotoSansDevanagari-Variable.ttf": (0.896, -0.408),
    "pylopdf-fonts-hi/src/pylopdf_fonts_hi/NotoSerifDevanagari-Variable.ttf": (0.93, -0.625),
    "pylopdf-fonts-jp/src/pylopdf_fonts_jp/NotoSansJP-Regular.otf": (1.16, -0.288),
    "pylopdf-fonts-jp/src/pylopdf_fonts_jp/NotoSerifJP-Regular.otf": (1.151, -0.286),
    "pylopdf-fonts-ko/src/pylopdf_fonts_ko/NotoSansKR-Regular.otf": (1.16, -0.288),
    "pylopdf-fonts-ko/src/pylopdf_fonts_ko/NotoSerifKR-Regular.otf": (1.151, -0.286),
    "pylopdf-fonts-th/src/pylopdf_fonts_th/NotoSansThai-Variable.ttf": (1.061, -0.45),
    "pylopdf-fonts-th/src/pylopdf_fonts_th/NotoSerifThai-Variable.ttf": (1.064, -0.534),
    "pylopdf-fonts-zh-cn/src/pylopdf_fonts_zh_cn/NotoSansSC-Regular.otf": (1.16, -0.288),
    "pylopdf-fonts-zh-cn/src/pylopdf_fonts_zh_cn/NotoSerifSC-Regular.otf": (1.151, -0.286),
    "pylopdf-fonts-zh-tw/src/pylopdf_fonts_zh_tw/NotoSansTC-Regular.otf": (1.16, -0.288),
    "pylopdf-fonts-zh-tw/src/pylopdf_fonts_zh_tw/NotoSerifTC-Regular.otf": (1.151, -0.286),
}

LATIN = "Aa\nAa\nAa"
JAPANESE = "日本\n日本\n日本"
HEBREW = "שלום\nשלום\nשלום"
POINT = (72.0, 100.0)
RECT = (72.0, 100.0, 300.0, 300.0)


def _patched(
    *,
    use_typo_metrics: bool = False,
    hhea: tuple[int, int] | None = None,
    typo: tuple[int, int] | None = None,
    win: tuple[int, int] | None = None,
) -> bytes:
    """Return Noto Sans JP with other vertical metrics.

    The face has hhea metrics of 1160 and -288, OS/2 typographic metrics of
    880 and -120, and Windows metrics of 1160 and 288, in a 1000-unit em, and
    does not set ``USE_TYPO_METRICS``.
    """
    data = bytearray(NOTO_SANS_JP.read_bytes())
    tables = {}
    for index in range(struct.unpack_from(">H", data, 4)[0]):
        tag, _checksum, offset, _length = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        tables[tag] = offset
    if use_typo_metrics:
        selection = tables[b"OS/2"] + 62
        struct.pack_into(">H", data, selection, struct.unpack_from(">H", data, selection)[0] | 0x80)
    if hhea is not None:
        struct.pack_into(">hh", data, tables[b"hhea"] + 4, *hhea)
    if typo is not None:
        struct.pack_into(">hh", data, tables[b"OS/2"] + 68, *typo)
    if win is not None:
        struct.pack_into(">HH", data, tables[b"OS/2"] + 74, *win)
    return bytes(data)


def _page() -> pylopdf.Page:
    doc = pylopdf.Document()
    return doc.pages.new((595, 842))


def _baselines(page: pylopdf.Page) -> list[float]:
    return [
        span["origin"][1]
        for block in page.layout().to_dict()["blocks"]
        for line in block["lines"]
        for span in line["spans"]
    ]


def _run_baselines(page: pylopdf.Page) -> list[float]:
    """Return the baseline of each painted line, whether or not its glyphs map to Unicode."""
    return [run.glyphs[0].origin.y for run in page.content(paths=False, images=False).text_runs]


def _font(options: dict[str, Any]) -> pylopdf.Font | None:
    """Return the font of a parameter set: a Standard 14 name, a file, or font bytes."""
    if "name" in options:
        return pylopdf.Font.standard(options["name"])
    if "path" in options:
        return pylopdf.Font.from_file(options["path"])
    if "data" in options:
        return pylopdf.Font.from_bytes(options["data"])
    return None


def _text(page: pylopdf.Page, text: str, *, line_height: float | None = None, **options: Any) -> None:  # noqa: ANN401
    with page.draw() as canvas:
        canvas.text(text, POINT, font=_font(options), size=10, line_height=line_height)


def _textbox(
    page: pylopdf.Page,
    rect: tuple[float, float, float, float],
    text: str,
    *,
    line_height: float | None = None,
    **options: Any,  # noqa: ANN401
) -> float:
    with page.draw() as canvas:
        return canvas.textbox(rect, text, font=_font(options), size=10, line_height=line_height).spare_height


@pytest.mark.parametrize(("name", "metrics"), STANDARD_METRICS.items())
def test_standard_14_fonts_report_pymupdfs_metrics(name: StandardFontName, metrics: tuple[float, float]) -> None:
    font = pylopdf.Font.standard(name)

    assert (font.ascender, font.descender) == pytest.approx(metrics, abs=1e-9)


@pytest.mark.parametrize(("path", "metrics"), FACE_METRICS.items())
def test_bundled_faces_report_pymupdfs_metrics(path: str, metrics: tuple[float, float]) -> None:
    font = pylopdf.Font.from_file(FONT_ROOT / path)

    assert (font.ascender, font.descender) == pytest.approx(metrics, abs=1e-9)


@pytest.mark.parametrize(
    ("patch", "metrics"),
    [
        pytest.param({"use_typo_metrics": True}, (0.88, -0.12), id="use-typo-metrics"),
        pytest.param({"hhea": (0, 0)}, (0.88, -0.12), id="zero-hhea"),
        pytest.param({"hhea": (0, 0), "typo": (0, 0), "win": (1100, 300)}, (1.1, -0.3), id="windows"),
        pytest.param({"hhea": (8001, -288)}, (0.8, -0.288), id="untrusted-ascender"),
        pytest.param({"hhea": (8000, -288)}, (8.0, -0.288), id="largest-ascender"),
        pytest.param({"hhea": (1160, 288)}, (1.16, -0.288), id="positive-descender"),
        pytest.param({"hhea": (1160, -3001)}, (1.16, -0.2), id="untrusted-descender"),
        pytest.param({"hhea": (1160, -3000)}, (1.16, -3.0), id="largest-descender"),
    ],
)
def test_faces_report_the_metrics_mupdf_reads(patch: dict[str, Any], metrics: tuple[float, float]) -> None:
    font = pylopdf.Font.from_bytes(_patched(**patch))

    assert (font.ascender, font.descender) == pytest.approx(metrics, abs=1e-9)


@pytest.mark.parametrize(
    ("text", "options", "baselines"),
    [
        # Helvetica's ascender minus its descender is 1.374 em.
        pytest.param(LATIN, {}, [100, 113.74, 127.48], id="helv"),
        pytest.param(LATIN, {"line_height": 1.5}, [100, 115, 130], id="line-height"),
        pytest.param(LATIN, {"line_height": 2}, [100, 120, 140], id="integer-line-height"),
        pytest.param(LATIN, {"name": "Courier"}, [100, 112.49, 124.98], id="cour"),
        pytest.param(LATIN, {"name": "Times-Bold"}, [100, 113.85, 127.7], id="tibo"),
        pytest.param(JAPANESE, {"path": NOTO_SANS_JP}, [100, 114.48, 128.96], id="noto-sans-jp"),
        pytest.param(JAPANESE, {"path": NOTO_SANS_JP, "line_height": 1.5}, [100, 115, 130], id="jp-line-height"),
        pytest.param(HEBREW, {"path": NOTO_SANS_HEBREW}, [100, 113.6, 127.2], id="noto-sans-hebrew"),
        # An ascender minus descender of at most 1 em falls back to 1.2 em.
        pytest.param(JAPANESE, {"data": _patched(hhea=(700, -200))}, [100, 112, 124], id="small-extent"),
        # pymupdf compares MuPDF's single-precision metrics: 0.75 and -0.25
        # span exactly 1 em and 0.88 and -0.12 slightly less, while 0.8 and
        # -0.2, also MuPDF's replacements for untrusted metrics, span
        # 1.000000015 em and keep lines 1 em apart.
        pytest.param(JAPANESE, {"data": _patched(hhea=(750, -250))}, [100, 112, 124], id="exact-1-em"),
        pytest.param(JAPANESE, {"data": _patched(hhea=(880, -120))}, [100, 112, 124], id="below-1-em"),
        pytest.param(JAPANESE, {"data": _patched(hhea=(800, -200))}, [100, 110, 120], id="above-1-em"),
        pytest.param(JAPANESE, {"data": _patched(hhea=(8001, -3001))}, [100, 110, 120], id="untrusted-metrics"),
    ],
)
def test_text_spaces_lines_as_pymupdf_insert_text_did(
    text: str,
    options: dict[str, Any],
    baselines: list[float],
) -> None:
    page = _page()

    _text(page, text, **options)

    assert _baselines(page) == pytest.approx(baselines, abs=1e-3)


def test_a_loaded_font_places_lines_with_its_own_metrics() -> None:
    font = pylopdf.Font.from_file(NOTO_SANS_JP)
    page = _page()

    with page.draw() as canvas:
        canvas.text(JAPANESE, POINT, font=font, size=10)

    step = (font.ascender - font.descender) * 10
    assert _baselines(page) == pytest.approx([100, 100 + step, 100 + 2 * step], abs=1e-3)
    assert step == pytest.approx(14.48)


def test_zapf_dingbats_falls_back_to_the_default_leading() -> None:
    page = _page()

    _text(page, LATIN, name="ZapfDingbats")

    # ZapfDingbats spans 0.963 em, so its lines lie 1.2 em apart.
    assert re.findall(rb"\b([\d.]+) TL\b", page.document.to_bytes(compress=False)) == [b"12"]


@pytest.mark.parametrize(
    ("text", "options", "ascender", "line_height"),
    [
        pytest.param(LATIN, {}, 1.075, 1.374, id="helv"),
        pytest.param(LATIN, {"line_height": 1}, 1.075, 1.0, id="line-height-1"),
        pytest.param(LATIN, {"line_height": 1.5}, 1.075, 1.5, id="line-height-1.5"),
        pytest.param(LATIN, {"name": "Courier"}, 0.932, 1.249, id="cour"),
        pytest.param(LATIN, {"name": "ZapfDingbats"}, 0.819, 1.2, id="zadb"),
        pytest.param(JAPANESE, {"path": NOTO_SANS_JP}, 1.16, 1.448, id="noto-sans-jp"),
        pytest.param(JAPANESE, {"data": _patched(hhea=(700, -200))}, 0.7, 1.2, id="small-extent"),
    ],
)
def test_textbox_places_lines_as_text_does(
    text: str,
    options: dict[str, Any],
    ascender: float,
    line_height: float,
) -> None:
    page = _page()
    font = _font(options) or pylopdf.Font.standard("Helvetica")

    spare = _textbox(page, RECT, text, **options)

    first = RECT[1] + ascender * 10
    assert _run_baselines(page) == pytest.approx([first, first + 10 * line_height, first + 20 * line_height], abs=1e-3)
    # The text takes the extent of its first and last lines plus two line steps.
    used = (font.ascender - font.descender) * 10 + 2 * 10 * line_height
    assert spare == pytest.approx(RECT[3] - RECT[1] - used, abs=1e-6)
    assert font.text_height(text, 10, width=RECT[2] - RECT[0], line_height=options.get("line_height")) == pytest.approx(
        used
    )


@pytest.mark.parametrize(
    ("bottom", "spare"),
    [
        pytest.param(140.0, -1.22, id="overflow"),
        pytest.param(141.21, -0.01, id="just-over"),
        pytest.param(141.22, 0.0, id="exact"),
    ],
)
def test_textbox_fits_text_down_to_its_last_descender(bottom: float, spare: float) -> None:
    page = _page()

    # Three Helvetica lines take 3 times 1.374 em at 10 points.
    result = _textbox(page, (RECT[0], RECT[1], RECT[2], bottom), LATIN)

    assert result == pytest.approx(spare, abs=1e-5)
    assert len(_baselines(page)) == (3 if spare >= 0 else 0)


@pytest.mark.parametrize("line_height", [0, -1.2, math.nan, math.inf])
def test_text_rejects_a_line_height_that_is_not_positive(line_height: float) -> None:
    page = _page()

    with pytest.raises(ValueError, match="line_height must be"):
        _text(page, LATIN, line_height=line_height)
    with pytest.raises(TypeError, match="line_height must be a number"):
        _text(page, LATIN, line_height="1.2x")  # ty: ignore[invalid-argument-type]
    assert _baselines(page) == []


@pytest.mark.parametrize(
    ("options", "error", "message"),
    [
        pytest.param({"size": 0}, ValueError, "size must be positive", id="size"),
        pytest.param({"size": "large"}, TypeError, "size must be a number", id="size-type"),
        pytest.param({"line_height": -1}, ValueError, "line_height must be positive", id="line-height"),
        pytest.param({"align": "middle"}, ValueError, "align must be one of", id="align"),
    ],
)
def test_textbox_refuses_unsupported_options(options: dict[str, Any], error: type[Exception], message: str) -> None:
    page = _page()

    with pytest.raises(error, match=message), page.draw() as canvas:
        canvas.textbox(RECT, LATIN, **options)
    assert _baselines(page) == []


def test_text_refuses_a_line_distance_beyond_pdf_real_range() -> None:
    page = _page()
    before = page.document.to_bytes()

    with pytest.raises(ValueError, match="line_height must be a finite number within PDF real range"):
        _text(page, LATIN, line_height=1e300)
    assert page.document.to_bytes() == before
