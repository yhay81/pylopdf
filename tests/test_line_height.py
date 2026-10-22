"""Line placement of insert_text and insert_textbox, pinned to pymupdf 1.28.2.

pymupdf is not a test dependency. Every expected value below was measured with
pymupdf 1.28.2 from the ``bench`` dependency group: ``Font.ascender`` and
``Font.descender``, and the baselines and return values of
``Page.insert_text`` and ``Page.insert_textbox`` at ``fontsize=10``, read back
through ``get_text("dict")``. pymupdf reports its metrics as single-precision
floats, so they are recorded here in the font units they come from.
"""

from __future__ import annotations

import math
import re
import struct
from pathlib import Path
from typing import Any

import pytest

import pylopdf

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
NOTO_SANS_HEBREW = FONT_ROOT / "pylopdf-fonts-he" / "src" / "pylopdf_fonts_he" / "NotoSansHebrew-Variable.ttf"

# pymupdf's Font(name) metrics of the Standard 14 fonts: the bounding boxes of
# the URW fonts that MuPDF draws them with, not Adobe's AFM metrics.
STANDARD_METRICS = {
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
        for block in page.get_text("dict")["blocks"]
        for line in block["lines"]
        for span in line["spans"]
    ]


def _insert_text(page: pylopdf.Page, via: str, text: str, **options: Any) -> int:  # noqa: ANN401
    options.setdefault("fontsize", 10)
    if via == "page":
        return page.insert_text(POINT, text, **options)
    with page.new_shape() as shape:
        return shape.insert_text(POINT, text, **options)


def _insert_textbox(page: pylopdf.Page, via: str, rect: tuple[float, ...], text: str, **options: Any) -> float:  # noqa: ANN401
    options.setdefault("fontsize", 10)
    if via == "page":
        return page.insert_textbox(rect, text, **options)
    with page.new_shape() as shape:
        return shape.insert_textbox(rect, text, **options)


@pytest.mark.parametrize(("name", "metrics"), STANDARD_METRICS.items())
def test_standard_14_fonts_report_pymupdfs_metrics(name: str, metrics: tuple[float, float]) -> None:
    font = pylopdf.Font(name)

    assert (font.ascender, font.descender) == pytest.approx(metrics, abs=1e-9)


@pytest.mark.parametrize(("path", "metrics"), FACE_METRICS.items())
def test_bundled_faces_report_pymupdfs_metrics(path: str, metrics: tuple[float, float]) -> None:
    font = pylopdf.Font(fontfile=FONT_ROOT / path)

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
    font = pylopdf.Font(fontbuffer=_patched(**patch))

    assert (font.ascender, font.descender) == pytest.approx(metrics, abs=1e-9)


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    ("text", "options", "baselines"),
    [
        # Helvetica's ascender minus its descender is 1.374 em.
        pytest.param(LATIN, {}, [100, 113.74, 127.48], id="helv"),
        pytest.param(LATIN, {"lineheight": 1.5}, [100, 115, 130], id="lineheight"),
        pytest.param(LATIN, {"lineheight": 2}, [100, 120, 140], id="integer-lineheight"),
        pytest.param(LATIN, {"fontname": "cour"}, [100, 112.49, 124.98], id="cour"),
        pytest.param(LATIN, {"fontname": "tibo"}, [100, 113.85, 127.7], id="tibo"),
        pytest.param(JAPANESE, {"fontfile": NOTO_SANS_JP}, [100, 114.48, 128.96], id="noto-sans-jp"),
        pytest.param(JAPANESE, {"fontfile": NOTO_SANS_JP, "lineheight": 1.5}, [100, 115, 130], id="jp-lineheight"),
        pytest.param(HEBREW, {"fontfile": NOTO_SANS_HEBREW}, [100, 113.6, 127.2], id="noto-sans-hebrew"),
        # An ascender minus descender of at most 1 em falls back to 1.2 em.
        pytest.param(JAPANESE, {"fontbuffer": _patched(hhea=(700, -200))}, [100, 112, 124], id="small-extent"),
        # pymupdf compares MuPDF's single-precision metrics: 0.75 and -0.25
        # span exactly 1 em and 0.88 and -0.12 slightly less, while 0.8 and
        # -0.2, also MuPDF's replacements for untrusted metrics, span
        # 1.000000015 em and keep lines 1 em apart.
        pytest.param(JAPANESE, {"fontbuffer": _patched(hhea=(750, -250))}, [100, 112, 124], id="exact-1-em"),
        pytest.param(JAPANESE, {"fontbuffer": _patched(hhea=(880, -120))}, [100, 112, 124], id="below-1-em"),
        pytest.param(JAPANESE, {"fontbuffer": _patched(hhea=(800, -200))}, [100, 110, 120], id="above-1-em"),
        pytest.param(
            JAPANESE,
            {"fontbuffer": _patched(hhea=(8001, -3001))},
            [100, 110, 120],
            id="untrusted-metrics",
        ),
    ],
)
def test_insert_text_places_lines_as_pymupdf_does(
    via: str,
    text: str,
    options: dict[str, Any],
    baselines: list[float],
) -> None:
    page = _page()

    count = _insert_text(page, via, text, **options)

    assert count == 3
    assert _baselines(page) == pytest.approx(baselines, abs=1e-3)


@pytest.mark.parametrize("via", ["page", "shape"])
def test_a_loaded_font_places_lines_with_its_own_metrics(via: str) -> None:
    font = pylopdf.Font(fontfile=NOTO_SANS_JP)
    page = _page()

    _insert_text(page, via, JAPANESE, font=font)

    step = (font.ascender - font.descender) * 10
    assert _baselines(page) == pytest.approx([100, 100 + step, 100 + 2 * step], abs=1e-3)
    assert step == pytest.approx(14.48)


@pytest.mark.parametrize("via", ["page", "shape"])
def test_zapf_dingbats_falls_back_to_pymupdfs_default_leading(via: str) -> None:
    page = _page()

    _insert_text(page, via, LATIN, fontname="zadb")

    # ZapfDingbats spans 0.963 em, so pymupdf sets its lines 1.2 em apart.
    assert re.findall(rb"\b([\d.]+) TL\b", page.document.tobytes()) == [b"12"]


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    ("text", "options", "baselines", "spare"),
    [
        # The first baseline lies one ascender below the top, later ones the
        # line height times the ascender apart, while the spare height counts
        # one line height per line plus one descender.
        pytest.param(LATIN, {}, [110.75, 125.5205, 140.291], 155.79, id="helv"),
        pytest.param(LATIN, {"lineheight": 1}, [110.75, 121.5, 132.25], 167.01, id="lineheight-1"),
        pytest.param(LATIN, {"lineheight": 1.5}, [110.75, 126.875, 143.0], 152.01, id="lineheight-1.5"),
        pytest.param(LATIN, {"fontname": "cour"}, [109.32, 120.9607, 132.6014], 159.36, id="cour"),
        pytest.param(LATIN, {"fontname": "tiro"}, [110.53, 124.577, 138.624], 157.17, id="tiro"),
        pytest.param(JAPANESE, {"fontfile": NOTO_SANS_JP}, [111.6, 128.3968, 145.1936], 153.68, id="noto-sans-jp"),
        pytest.param(
            JAPANESE,
            {"fontfile": NOTO_SANS_JP, "lineheight": 1.5},
            [111.6, 129.0, 146.4],
            152.12,
            id="jp-lineheight",
        ),
        pytest.param(
            JAPANESE,
            {"fontbuffer": _patched(hhea=(700, -200))},
            [107.0, 115.4, 123.8],
            162.0,
            id="small-extent",
        ),
        pytest.param(
            JAPANESE,
            {"fontbuffer": _patched(hhea=(750, -250))},
            [107.5, 116.5, 125.5],
            161.5,
            id="exact-1-em",
        ),
        pytest.param(
            JAPANESE,
            {"fontbuffer": _patched(hhea=(880, -120))},
            [108.8, 119.36, 129.92],
            162.8,
            id="below-1-em",
        ),
        pytest.param(
            JAPANESE,
            {"fontbuffer": _patched(hhea=(800, -200))},
            [108.0, 116.0, 124.0],
            168.0,
            id="above-1-em",
        ),
    ],
)
def test_insert_textbox_places_lines_as_pymupdf_does(
    via: str,
    text: str,
    options: dict[str, Any],
    baselines: list[float],
    spare: float,
) -> None:
    page = _page()

    assert _insert_textbox(page, via, RECT, text, **options) == pytest.approx(spare, abs=1e-5)
    assert _baselines(page) == pytest.approx(baselines, abs=1e-3)


@pytest.mark.parametrize("via", ["page", "shape"])
def test_insert_textbox_places_zapf_dingbats_as_pymupdf_does(via: str) -> None:
    page = _page()

    assert _insert_textbox(page, via, RECT, LATIN, fontname="zadb") == pytest.approx(162.56, abs=1e-5)

    # Display baselines 108.19, 118.018, and 127.846 on an 842-point page.
    origins = re.findall(rb"1 0 0 1 72 ([\d.]+) Tm", page.document.tobytes())
    assert [842 - float(origin) for origin in origins] == pytest.approx([108.19, 118.018, 127.846], abs=1e-3)


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    ("bottom", "spare"),
    [
        pytest.param(140.0, -4.21, id="overflow"),
        pytest.param(144.2, -0.01, id="just-over"),
        pytest.param(144.21, 0.0, id="exact"),
    ],
)
def test_insert_textbox_fits_text_as_pymupdf_does(via: str, bottom: float, spare: float) -> None:
    page = _page()

    result = _insert_textbox(page, via, (RECT[0], RECT[1], RECT[2], bottom), LATIN)

    assert result == pytest.approx(spare, abs=1e-5)
    assert len(_baselines(page)) == (3 if spare >= 0 else 0)


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize("lineheight", [0, -1.2, math.nan, math.inf, "1.2x"])
def test_insert_text_rejects_a_lineheight_that_is_not_positive(via: str, lineheight: object) -> None:
    page = _page()

    with pytest.raises(pylopdf.PdfError, match="lineheight must be a positive number or None"):
        _insert_text(page, via, LATIN, lineheight=lineheight)
    assert _baselines(page) == []


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    ("options", "message"),
    [
        pytest.param({"fontsize": 0}, "fontsize must be a positive number", id="fontsize"),
        pytest.param({"fontsize": "large"}, "fontsize must be a positive number", id="fontsize-type"),
        pytest.param({"lineheight": -1}, "lineheight must be a positive number or None", id="lineheight"),
        pytest.param({"align": 4}, "align must be one of", id="align"),
        pytest.param({"expandtabs": -1}, "expandtabs must be a non-negative integer", id="expandtabs"),
    ],
)
def test_insert_textbox_raises_pdf_error_for_unsupported_options(
    via: str,
    options: dict[str, Any],
    message: str,
) -> None:
    page = _page()

    # PdfError is a ValueError, so code that catches ValueError keeps working.
    with pytest.raises(pylopdf.PdfError, match=message) as error:
        _insert_textbox(page, via, RECT, LATIN, **options)
    assert isinstance(error.value, ValueError)
    assert _baselines(page) == []


@pytest.mark.parametrize("via", ["page", "shape"])
def test_insert_text_rejects_a_line_distance_beyond_floating_point(via: str) -> None:
    page = _page()
    before = page.document.tobytes()

    with pytest.raises(pylopdf.PdfError, match="lineheight times fontsize must be a finite number"):
        _insert_text(page, via, LATIN, lineheight=1e300, fontsize=1e10)
    assert page.document.tobytes() == before
