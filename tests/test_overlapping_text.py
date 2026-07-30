"""Regression tests for overlapping text paint order."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

import pylopdf

NOTO_SANS_JP = (
    Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
)


def _new_page() -> tuple[pylopdf.Document, pylopdf.Page]:
    doc = pylopdf.Document()
    return doc, doc.new_page(width=400, height=200)


def _dict_line_texts(page: pylopdf.Page) -> list[str]:
    layout = page.get_text("dict")
    return ["".join(span["text"] for span in line["spans"]) for block in layout["blocks"] for line in block["lines"]]


def _word_line_texts(page: pylopdf.Page) -> list[str]:
    lines: defaultdict[tuple[int, int], list[str]] = defaultdict(list)
    for *_, text, block_no, line_no, _word_no in page.get_text("words"):
        lines[(block_no, line_no)].append(text)
    return [" ".join(words) for words in lines.values()]


def _assert_text_views(
    doc: pylopdf.Document,
    expected_lines: list[str],
) -> None:
    page = doc[0]
    expected_text = "".join(f"{line}\n" for line in expected_lines)
    assert page.get_text("text") == expected_text
    assert doc.get_page_text(0) == expected_text
    assert _dict_line_texts(page) == expected_lines
    assert _word_line_texts(page) == expected_lines

    block_lines = [line for block in page.get_text("blocks") for line in block[4].splitlines()]
    assert block_lines == expected_lines

    markdown = page.to_markdown()
    for text in set(expected_lines):
        assert markdown.count(text) == expected_lines.count(text)


@pytest.mark.parametrize(
    ("text", "fontfile"),
    [
        ("Overlap text", None),
        ("A", None),
        ("重複テキスト", NOTO_SANS_JP),
        ("重", NOTO_SANS_JP),
    ],
)
def test_exact_overprint_preserves_each_complete_paint_run(
    text: str,
    fontfile: Path | None,
) -> None:
    """Keep identical Latin and CJK overprints as separate complete lines."""
    doc, page = _new_page()
    for _ in range(2):
        if fontfile is None:
            page.insert_text((30, 80), text, fontsize=20)
        else:
            page.insert_text((30, 80), text, fontsize=20, fontfile=fontfile)

    _assert_text_views(doc, [text, text])
    hits = page.search_for(text)
    assert len(hits) == 2
    assert hits[0] == hits[1]

    lines = page.get_text("dict")["blocks"][0]["lines"]
    assert lines[0]["bbox"] == pytest.approx(lines[1]["bbox"])


def test_same_origin_distinct_strings_are_not_interleaved_or_deleted() -> None:
    """Retain both source-order strings even when their geometry overlaps."""
    doc, page = _new_page()
    expected = ["Alpha overlay", "Bravo layer"]
    for text in expected:
        page.insert_text((30, 80), text, fontsize=20)

    _assert_text_views(doc, expected)
    assert len(page.search_for(expected[0])) == 1
    assert len(page.search_for(expected[1])) == 1


def test_partial_overlap_keeps_source_order_layers() -> None:
    """Split partially overlapping paint runs instead of interleaving glyphs."""
    doc, page = _new_page()
    page.insert_text((30, 80), "Left overlap", fontsize=20)
    page.insert_text((80, 80), "Right overlap", fontsize=20)

    _assert_text_views(doc, ["Left overlap", "Right overlap"])
    assert len(page.search_for("Left overlap")) == 1
    assert len(page.search_for("Right overlap")) == 1


def test_chained_partial_overlaps_do_not_rejoin_an_earlier_layer() -> None:
    """Do not move a later run before an intervening overlapping run."""
    doc, page = _new_page()
    expected = ["AAAAAA", "BBBBBB", "CCCCCC"]
    for x, text in zip((30, 70, 110), expected, strict=True):
        page.insert_text((x, 80), text, fontsize=20)

    _assert_text_views(doc, expected)


def test_separate_reverse_paint_runs_still_follow_geometry_order() -> None:
    """Merge non-overlapping runs on one baseline even if painted right first."""
    doc, page = _new_page()
    page.insert_text((180, 80), "Right", fontsize=20)
    page.insert_text((30, 80), "Left", fontsize=20)

    _assert_text_views(doc, ["Left Right"])


def test_slightly_offset_overprint_preserves_bboxes_and_search() -> None:
    """Treat near-identical baselines as distinct runs without merging bboxes."""
    doc, page = _new_page()
    text = "Offset overlay"
    page.insert_text((30, 80), text, fontsize=20)
    page.insert_text((30.5, 80.5), text, fontsize=20)

    _assert_text_views(doc, [text, text])
    hits = page.search_for(text)
    assert len(hits) == 2
    assert hits[0].x0 == pytest.approx(30.0)
    assert hits[1].x0 == pytest.approx(30.5)
    assert hits[0].y0 == pytest.approx(hits[1].y0 - 0.5)
