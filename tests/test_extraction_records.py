"""Extraction records: words, the layout, page content, and links."""

from __future__ import annotations

import dataclasses
import json

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def test_words_are_word_records() -> None:
    page = pylopdf.open(build_pdf(["Hello world"])).pages[0]

    words = page.words()

    assert [word.text for word in words] == ["Hello", "world"]
    first = words[0]
    assert isinstance(first, pylopdf.Word)
    assert not isinstance(first, tuple)
    assert (first.block, first.line, first.index) == (0, 0, 0)
    assert words[1].index == 1
    assert isinstance(first.bbox, pylopdf.Rect)
    assert first.bbox.x0 < first.bbox.x1 < words[1].bbox.x0
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.text = "Changed"  # ty: ignore[invalid-assignment]


def test_layout_records_nest_blocks_lines_spans_and_chars() -> None:
    page = pylopdf.open(build_pdf(["Hello world"])).pages[0]

    (block,) = page.layout().blocks
    (line,) = block.lines

    assert isinstance(block, pylopdf.Block)
    assert block.text == line.text == "Hello world"
    assert line.direction == pylopdf.Point(1, 0)
    assert not line.is_vertical
    assert "".join(span.text for span in line.spans).replace(" ", "") == "Helloworld"
    assert all(span.chars == () for span in line.spans)
    chars = [char for span in page.layout(chars=True).blocks[0].lines[0].spans for char in span.chars]
    assert "".join(char.text for char in chars) == "Hello world"
    assert all(isinstance(char, pylopdf.Char) for char in chars)


def test_layout_dictionaries_keep_pymupdfs_shape() -> None:
    page = pylopdf.open(build_pdf(["Hello"])).pages[0]

    layout = page.layout().to_dict()
    raw = page.layout(chars=True).to_dict()

    span = layout["blocks"][0]["lines"][0]["spans"][0]
    raw_span = raw["blocks"][0]["lines"][0]["spans"][0]
    style = {"char_flags", "color", "alpha", "ascender", "descender"}
    assert set(span) == {"bbox", "origin", "size", "font", "flags", "text"} | style
    assert set(raw_span) == {"bbox", "origin", "size", "font", "flags", "chars"} | style
    assert set(raw_span["chars"][0]) == {"c", "bbox", "origin", "synthetic"}
    assert json.loads(json.dumps(raw))["blocks"][0]["number"] == 0
    # The names of pylopdf 0.15's records stay free.
    for name in ("TextPage", "TextPageDict", "RawTextPageDict", "WordEntry", "BlockEntry"):
        assert not hasattr(pylopdf, name)


def _drawing_page() -> pylopdf.Page:
    content = (
        "0 0 1 rg 10 10 50 50 re f "
        "BT /F1 12 Tf 100 100 Td (Text) Tj ET "
        "1 0 0 RG 2 w 70 10 50 50 re B "
        "0 1 0 RG 150 10 m 200 60 l S"
    )
    data = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            4: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        }
    )
    return pylopdf.open(data).pages[0]


def test_content_lists_paths_and_text_in_paint_order() -> None:
    content = _drawing_page().content()

    # B paints one path with a fill and a stroke.
    assert [type(item).__name__ for item in content] == ["Path", "TextRun", "Path", "Path"]
    assert [(path.fill is not None, path.stroke is not None) for path in content.paths] == [
        (True, False),
        (True, True),
        (False, True),
    ]


def _link_page(annotation: str, *, indirect: bool) -> pylopdf.Page:
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Annots [{'4 0 R' if indirect else annotation}] >>",
    }
    if indirect:
        objects[4] = annotation
    return pylopdf.open(build_raw_pdf(objects)).pages[0]


def test_links_report_their_annotation_xref_and_dictionary() -> None:
    annotation = (
        "<< /Type /Annot /Subtype /Link /Rect [10 10 60 30] /NM (link-1) /A << /S /URI /URI (https://e.test/) >> >>"
    )

    (link,) = _link_page(annotation, indirect=True).links

    assert link.xref == 4
    assert link.obj["NM"] == pylopdf.String(b"link-1")
    assert link.target == "https://e.test/"


def test_a_direct_link_annotation_has_xref_zero() -> None:
    annotation = (
        "<< /Type /Annot /Subtype /Link /Rect [10 10 60 30] /NM (direct) /A << /S /URI /URI (https://e.test/) >> >>"
    )

    (link,) = _link_page(annotation, indirect=False).links

    assert (link.xref, link.obj["NM"]) == (0, pylopdf.String(b"direct"))


def test_created_links_report_their_xref() -> None:
    doc = pylopdf.open(build_pdf(["Links"]))
    page = doc.pages[0]
    page.links.add((10, 10, 60, 30), "https://a.test/")
    page.links.add((10, 40, 60, 60), "https://b.test/")

    links = list(page.links)

    assert [link.target for link in links] == ["https://a.test/", "https://b.test/"]
    for link in links:
        assert doc.objects[link.xref]["Subtype"] == "Link"
    assert links[0].xref != links[1].xref
