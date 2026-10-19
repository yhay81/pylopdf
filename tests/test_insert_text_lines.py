"""Which lines insert_text draws and counts, pinned to pymupdf 1.28.2.

pymupdf is not a test dependency. Unless a test says otherwise, every
expected count below is the return value of pymupdf 1.28.2's
``Page.insert_text`` or ``Shape.insert_text`` from the ``bench`` dependency
group, at ``fontsize=10`` on a page of 200 by 130 points, and the baselines are
those of the lines it drew. pymupdf splits a string with
``str.splitlines()``, always draws the first line, and draws each later one
only while the room left, from the first baseline down to the bottom of the
page less one line distance per line drawn, is at least one line distance. It
computes that distance from MuPDF's single-precision font metrics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import build_raw_pdf

import pylopdf

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"

POINT = (72.0, 100.0)
ABCD = "A\nB\nC\nD"
JAPANESE = "日本\n日本\n日本\n日本"
# Helvetica lines lie 13.74 points apart at size 10.
HELV_ABC = [(100.0, "A"), (113.74, "B"), (127.48, "C")]


def _page() -> pylopdf.Page:
    return pylopdf.Document().new_page(width=200, height=130)


def _spans(page: pylopdf.Page) -> list[tuple[float, str]]:
    return [
        (span["origin"][1], span["text"])
        for block in page.get_text("dict")["blocks"]
        for line in block["lines"]
        for span in line["spans"]
    ]


def _insert_text(
    page: pylopdf.Page,
    via: str,
    text: str | list[str] | tuple[str, ...],
    point: tuple[float, float] = POINT,
    **options: Any,  # noqa: ANN401
) -> int:
    options.setdefault("fontsize", 10)
    if via == "page":
        return page.insert_text(point, text, **options)
    with page.new_shape() as shape:
        return shape.insert_text(point, text, **options)


def _assert_spans(page: pylopdf.Page, spans: list[tuple[float, str]]) -> None:
    found = _spans(page)
    assert [text for _, text in found] == [text for _, text in spans]
    assert [baseline for baseline, _ in found] == pytest.approx([baseline for baseline, _ in spans], abs=1e-3)


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    ("text", "count", "spans"),
    [
        pytest.param("Aa\nBb", 2, [(100.0, "Aa"), (113.74, "Bb")], id="newline"),
        # A trailing line break adds no line.
        pytest.param("Aa\n", 1, [(100.0, "Aa")], id="trailing-newline"),
        pytest.param("Aa\n\n", 2, [(100.0, "Aa")], id="trailing-empty-line"),
        pytest.param("\n\nA", 3, [(127.48, "A")], id="leading-empty-lines"),
        pytest.param("\n\n", 2, [], id="empty-lines"),
        pytest.param("A\r\nB", 2, [(100.0, "A"), (113.74, "B")], id="crlf"),
        pytest.param("A\rB", 2, [(100.0, "A"), (113.74, "B")], id="cr"),
        pytest.param("A\n\rB", 3, [(100.0, "A"), (127.48, "B")], id="lf-cr"),
        pytest.param("A\r\n\r\nB", 3, [(100.0, "A"), (127.48, "B")], id="crlf-crlf"),
        pytest.param("A\vB", 2, [(100.0, "A"), (113.74, "B")], id="vertical-tab"),
        pytest.param("A\fB", 2, [(100.0, "A"), (113.74, "B")], id="form-feed"),
        pytest.param("A\x1cB", 2, [(100.0, "A"), (113.74, "B")], id="file-separator"),
        pytest.param("A\x1dB", 2, [(100.0, "A"), (113.74, "B")], id="group-separator"),
        pytest.param("A\x1eB", 2, [(100.0, "A"), (113.74, "B")], id="record-separator"),
        pytest.param("A\x85B", 2, [(100.0, "A"), (113.74, "B")], id="next-line"),
        pytest.param("A\u2028B", 2, [(100.0, "A"), (113.74, "B")], id="line-separator"),
        pytest.param("A\u2029B", 2, [(100.0, "A"), (113.74, "B")], id="paragraph-separator"),
        # A list or tuple is used as given.
        pytest.param(["A", "B"], 2, [(100.0, "A"), (113.74, "B")], id="list"),
        pytest.param(("A", "B"), 2, [(100.0, "A"), (113.74, "B")], id="tuple"),
        pytest.param(["A", ""], 2, [(100.0, "A")], id="list-with-empty-line"),
        pytest.param(["", ""], 2, [], id="list-of-empty-lines"),
    ],
)
def test_text_is_split_into_lines_as_pymupdf_splits_it(
    via: str,
    text: str | list[str] | tuple[str, ...],
    count: int,
    spans: list[tuple[float, str]],
) -> None:
    page = _page()

    assert _insert_text(page, via, text) == count
    _assert_spans(page, spans)


@pytest.mark.parametrize("via", ["page", "shape"])
def test_a_line_break_inside_a_list_item_does_not_start_a_line(via: str) -> None:
    page = _page()

    assert _insert_text(page, via, ["A\nB"]) == 1
    # The line feed is a character of the line, which Helvetica has no glyph
    # for, so everything lies on the first baseline.
    with pytest.warns(pylopdf.PylopdfWarning, match="no Unicode mapping"):
        baselines = {baseline for baseline, _ in _spans(page)}
    assert baselines == {100.0}


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty-string"),
        pytest.param("\n", id="newline"),
        pytest.param("\r\n", id="crlf"),
        pytest.param([], id="empty-list"),
        pytest.param([""], id="one-empty-line"),
        pytest.param(("",), id="tuple-of-one-empty-line"),
    ],
)
def test_text_with_nothing_to_draw_inserts_nothing(via: str, text: str | list[str] | tuple[str, ...]) -> None:
    page = _page()
    before = page.parent.tobytes()

    assert _insert_text(page, via, text) == 0
    assert page.parent.tobytes() == before


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    ("text", "point", "options", "spans"),
    [
        # The fourth baseline, 141.22, would fall below the 130-point page.
        pytest.param(ABCD, POINT, {}, HELV_ABC, id="helv"),
        pytest.param(ABCD, POINT, {"font": "Helvetica"}, HELV_ABC, id="standard-14-font"),
        pytest.param(["A", "B", "C", "D"], POINT, {}, HELV_ABC, id="list"),
        pytest.param("A\nB\nC\n", (72.0, 110.0), {}, [(110.0, "A"), (123.74, "B")], id="trailing-newline"),
        # A baseline on the bottom edge is drawn.
        pytest.param(
            ABCD,
            POINT,
            {"lineheight": 1.5},
            [(100.0, "A"), (115.0, "B"), (130.0, "C")],
            id="baseline-on-the-edge",
        ),
        # pymupdf's line distance from single-precision metrics, 13.7400004
        # points, leaves no room for a third baseline at 130.
        pytest.param(ABCD, (72.0, 102.52), {}, [(102.52, "A"), (116.26, "B")], id="single-precision-edge"),
        pytest.param(
            ABCD,
            (72.0, 102.5199),
            {},
            [(102.5199, "A"), (116.2599, "B"), (129.9999, "C")],
            id="above-the-edge",
        ),
        # The first line is always drawn.
        pytest.param("A\nB\nC", (72.0, 200.0), {}, [(200.0, "A")], id="first-line-below-the-page"),
        pytest.param("A\nB\nC", (72.0, 130.0), {}, [(130.0, "A")], id="first-line-on-the-edge"),
        # Lines above the page are drawn and counted.
        pytest.param(
            "\n".join(f"L{number}" for number in range(20)),
            (72.0, -20.0),
            {},
            [(-20.0 + 13.74 * number, f"L{number}") for number in range(11)],
            id="above-the-page",
        ),
    ],
)
def test_lines_below_the_page_are_left_out_as_in_pymupdf(
    via: str,
    text: str | list[str],
    point: tuple[float, float],
    options: dict[str, Any],
    spans: list[tuple[float, str]],
) -> None:
    page = _page()
    if "font" in options:
        options = {**options, "font": pylopdf.Font(options["font"])}

    assert _insert_text(page, via, text, point, **options) == len(spans)
    _assert_spans(page, spans)


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize("source", ["fontfile", "fontbuffer", "font"])
@pytest.mark.parametrize(
    ("point", "options", "baselines"),
    [
        # Noto Sans JP lines lie 14.48 points apart at size 10.
        pytest.param(POINT, {}, [100.0, 114.48, 128.96], id="noto-sans-jp"),
        # The single-precision line distance, 14.4799995 points, leaves room
        # for a third baseline at 130, which pymupdf draws and counts.
        pytest.param((72.0, 101.04), {}, [101.04, 115.52, 130.0], id="single-precision-edge"),
        pytest.param(POINT, {"lineheight": 1.5}, [100.0, 115.0, 130.0], id="lineheight"),
    ],
)
def test_embedded_fonts_leave_out_lines_below_the_page_as_in_pymupdf(
    via: str,
    source: str,
    point: tuple[float, float],
    options: dict[str, Any],
    baselines: list[float],
) -> None:
    page = _page()
    sources: dict[str, Any] = {
        "fontfile": NOTO_SANS_JP,
        "fontbuffer": NOTO_SANS_JP.read_bytes(),
        "font": pylopdf.Font(fontfile=NOTO_SANS_JP),
    }

    assert _insert_text(page, via, JAPANESE, point, **{source: sources[source]}, **options) == 3
    _assert_spans(page, [(baseline, "日本") for baseline in baselines])


def _one_page(page_entries: str) -> bytes:
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: f"<< /Type /Page /Parent 2 0 R {page_entries} >>",
        }
    )


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    "page_entries",
    [
        pytest.param("/MediaBox [0 0 200 130]", id="plain"),
        pytest.param("/MediaBox [0 70 200 200]", id="mediabox-above-the-origin"),
        pytest.param("/MediaBox [0 0 200 200] /CropBox [0 70 200 200]", id="cropbox"),
        pytest.param("/MediaBox [0 0 130 200] /Rotate 90", id="rotated"),
    ],
)
def test_the_bottom_of_the_page_is_that_of_the_display_rectangle(via: str, page_entries: str) -> None:
    # Every page shows 200 by 130 points. pymupdf 1.28.2 draws 8 of these 10
    # lines on all but the first page, because it measures in unrotated space
    # down to user-space y = 0; pylopdf measures in display space down to the
    # bottom of page.rect (DESIGN.md G1).
    page = pylopdf.Document(stream=_one_page(page_entries))[0]
    assert tuple(page.rect) == (0.0, 0.0, 200.0, 130.0)

    assert _insert_text(page, via, "\n".join("ABCDEFGHIJ")) == 3
    _assert_spans(page, HELV_ABC)


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="standard-14"),
        pytest.param({"fontfile": NOTO_SANS_JP}, id="embedded"),
    ],
)
def test_the_font_checks_lines_below_the_page(via: str, options: dict[str, Any]) -> None:
    page = _page()
    before = page.parent.tobytes()

    # The fifth line would not be drawn, but neither font can draw it.
    with pytest.raises(ValueError, match=r"WinAnsi|no glyph for U\+1F980"):
        _insert_text(page, via, ["A", "B", "C", "D", "\U0001f980"], **options)
    assert page.parent.tobytes() == before


@pytest.mark.parametrize("via", ["page", "shape"])
def test_the_line_limit_counts_lines_as_str_splitlines_does(via: str) -> None:
    # 4,096 line breaks make 4,096 lines, the most that the limit accepts.
    assert _insert_text(_page(), via, "\n" * 4096, max_text_size=4096) == 3
    assert _insert_text(_page(), via, "x\n" * 4096, max_text_size=8192) == 3
    for text in ("\n" * 4097, "x\n" * 4096 + "x", ["x"] * 4097):
        page = _page()
        before = page.parent.tobytes()
        with pytest.raises(pylopdf.LimitError) as error:
            _insert_text(page, via, text, max_text_size=8193)
        assert error.value.code == "text_line_count"
        assert page.parent.tobytes() == before
    assert _insert_text(_page(), via, ["x"] * 4097, max_text_size=None) == 3


@pytest.mark.parametrize("via", ["page", "shape"])
def test_list_lines_share_the_text_budget(via: str) -> None:
    page = _page()

    with pytest.raises(pylopdf.LimitError) as error:
        _insert_text(page, via, ["é", "é"], max_text_size=3)
    assert error.value.code == "text_input_size"
    assert _insert_text(page, via, ["é", "é"], max_text_size=4) == 2


@pytest.mark.parametrize("via", ["page", "shape"])
@pytest.mark.parametrize("text", [5, None, ["A", 5], [b"A"]])
def test_text_must_be_a_string_or_a_list_of_strings(via: str, text: object) -> None:
    page = _page()

    with pytest.raises(TypeError, match="text must be a string or a list of strings"):
        _insert_text(page, via, text)  # ty: ignore[invalid-argument-type]
