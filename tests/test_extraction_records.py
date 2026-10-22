"""pymupdf's extraction record shapes: words, blocks, drawings, and links."""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def test_words_are_word_named_tuples_in_pymupdf_order() -> None:
    page = pylopdf.open(build_pdf(["Hello world"])).pages[0]

    words = page.get_text("words")

    assert [word.word for word in words] == ["Hello", "world"]
    first = words[0]
    assert isinstance(first, pylopdf.Word)
    assert isinstance(first, tuple)
    assert len(first) == 8
    x0, y0, x1, y1, text, block_no, line_no, word_no = first
    assert (text, block_no, line_no, word_no) == ("Hello", 0, 0, 0)
    assert (first.x0, first.y0, first.x1, first.y1) == (x0, y0, x1, y1)
    assert words[1].word_no == 1
    assert pylopdf.Word._fields == ("x0", "y0", "x1", "y1", "word", "block_no", "line_no", "word_no")


def test_blocks_are_block_named_tuples_in_pymupdf_order() -> None:
    doc = pylopdf.open(build_pdf(["Hello world"]))

    (block,) = doc.pages[0].get_text("blocks")

    assert isinstance(block, pylopdf.Block)
    assert len(block) == 7
    assert block.text == "Hello world"
    assert (block.block_no, block.block_type) == (0, 0)
    assert pylopdf.Block._fields == ("x0", "y0", "x1", "y1", "text", "block_no", "block_type")


def test_dict_layouts_keep_their_typed_dict_names() -> None:
    page = pylopdf.open(build_pdf(["Hello"])).pages[0]

    assert isinstance(page.get_text("dict"), dict)
    assert pylopdf.TextPageDict.__name__ == "TextPageDict"
    assert pylopdf.RawTextPageDict.__name__ == "RawTextPageDict"
    # The name TextPage stays free for a text page handle.
    assert not hasattr(pylopdf, "TextPage")
    assert not hasattr(pylopdf, "WordEntry")
    assert not hasattr(pylopdf, "BlockEntry")


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


def test_drawings_report_their_paint_order_as_seqno() -> None:
    drawings = _drawing_page().get_drawings()

    assert [drawing["type"] for drawing in drawings] == ["f", "fs", "s"]
    # The text run between the first two paths takes its own number, and the
    # fill and the stroke of B take one each, as MuPDF numbers them.
    assert [drawing["seqno"] for drawing in drawings] == [0, 2, 4]
    assert "layer" not in drawings[0]


def _link_page(annotation: str, *, indirect: bool) -> pylopdf.Page:
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Annots [{'4 0 R' if indirect else annotation}] >>",
    }
    if indirect:
        objects[4] = annotation
    return pylopdf.open(build_raw_pdf(objects)).pages[0]


@pytest.mark.parametrize(("name", "expected"), [("/NM (link-1)", "link-1"), ("", "")])
def test_links_report_their_annotation_xref_and_id(name: str, expected: str) -> None:
    annotation = f"<< /Type /Annot /Subtype /Link /Rect [10 10 60 30] {name} /A << /S /URI /URI (https://e.test/) >> >>"

    (link,) = _link_page(annotation, indirect=True).get_links()

    assert link["xref"] == 4
    assert link["id"] == expected
    assert link["uri"] == "https://e.test/"


def test_a_direct_link_annotation_has_xref_zero() -> None:
    annotation = (
        "<< /Type /Annot /Subtype /Link /Rect [10 10 60 30] /NM (direct) /A << /S /URI /URI (https://e.test/) >> >>"
    )

    (link,) = _link_page(annotation, indirect=False).get_links()

    assert (link["xref"], link["id"]) == (0, "direct")


def test_created_links_report_their_xref() -> None:
    doc = pylopdf.open(build_pdf(["Links"]))
    page = doc.pages[0]
    page.insert_link({"kind": pylopdf.LINK_URI, "from": (10, 10, 60, 30), "uri": "https://a.test/"})
    page.insert_link({"kind": pylopdf.LINK_URI, "from": (10, 40, 60, 60), "uri": "https://b.test/"})

    links = page.get_links()

    assert [link["uri"] for link in links] == ["https://a.test/", "https://b.test/"]
    for link in links:
        assert doc.objects[link["xref"]]["Subtype"] == "Link"
    assert links[0]["xref"] != links[1]["xref"]
