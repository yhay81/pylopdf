"""The HTML and XML serializations of a page's layout."""

from __future__ import annotations

import html.parser
import json
import xml.etree.ElementTree as ET

from test_layout import build_painted_text_pdf
from test_text_options import build_text_pdf

import pylopdf


class _Collector(html.parser.HTMLParser):
    """Collect the elements, styles, and text of an HTML fragment."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, str]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs).get("style") or ""))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _xml(text: str) -> ET.Element:
    """Parse pylopdf's own XML output, which holds no entity declarations."""
    return ET.fromstring(text)  # noqa: S314 - the input is pylopdf's own output.


def _parse(fragment: str) -> _Collector:
    collector = _Collector()
    collector.feed(fragment)
    collector.close()
    return collector


def test_html_places_one_paragraph_per_line_with_its_spans() -> None:
    page = pylopdf.open(build_painted_text_pdf()).pages[0]
    layout = page.layout()

    parsed = _parse(layout.to_html())

    (page_tag, page_style), *rest = parsed.tags
    assert page_tag == "div"
    assert "width:612pt;height:792pt" in page_style
    paragraphs = [style for tag, style in rest if tag == "p"]
    spans = [style for tag, style in rest if tag == "span"]
    lines = [line for block in layout.blocks for line in block.lines]
    assert len(paragraphs) == len(lines)
    assert len(spans) == sum(len(line.spans) for line in lines)
    assert "".join(parsed.text).split() == page.text().split()
    by_text = dict(zip((span.text for line in lines for span in line.spans), spans, strict=True))
    assert "color:#ff0000" in by_text["Red"]
    assert "opacity:0.502" in by_text["Half"]
    assert "opacity:0" in by_text["Hidden"]
    first = lines[0]
    assert f"left:{first.bbox.x0:g}pt;top:{first.bbox.y0:g}pt" in paragraphs[0]


def test_html_and_xml_escape_text() -> None:
    page = pylopdf.open(build_text_pdf(r"BT /F1 20 Tf 72 700 Td (<a> & \"b\") Tj ET")).pages[0]

    fragment = page.layout().to_html()
    root = _xml(page.layout(chars=True).to_xml())

    assert "&lt;a&gt; &amp; " in fragment
    assert "".join(_parse(fragment).text).strip() == '<a> & "b"'
    assert "".join(char.get("c", "") for char in root.iter("char")) == '<a> & "b"'


def test_html_turns_rotated_lines_along_their_baseline() -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 20 Tf 0 1 -1 0 300 100 Tm (Up) Tj ET")).pages[0]

    (line,) = page.layout().blocks[0].lines
    (_, (_, paragraph), _) = _parse(page.layout().to_html()).tags

    assert "transform:rotate(-90deg)" in paragraph
    assert f"left:{line.quad.ul.x:g}pt;top:{line.quad.ul.y:g}pt" in paragraph


def test_xml_lists_blocks_lines_fonts_and_chars() -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 20 Tf 72 700 Td (A) Tj 20 0 Td (gap) Tj ET")).pages[0]
    layout = page.layout(chars=True)

    root = _xml(layout.to_xml())

    assert root.tag == "page"
    assert (root.get("width"), root.get("height")) == ("612", "792")
    (block,) = root.findall("block")
    (line,) = block.findall("line")
    assert (line.get("wmode"), line.get("dir")) == ("0", "1 0")
    (font,) = line.findall("font")
    assert (font.get("size"), font.get("color")) == ("20", "#000000")
    chars = font.findall("char")
    assert [char.get("c") for char in chars] == list("A gap")
    assert [char.get("synthetic") for char in chars] == [None, "1", None, None, None]
    span = layout.blocks[0].lines[0].spans[0]
    assert chars[0].get("bbox") == " ".join(f"{value:.3f}".rstrip("0").rstrip(".") for value in span.chars[0].bbox)
    # Without characters, each font holds its span's text.
    (plain,) = _xml(page.layout().to_xml()).iter("font")
    assert plain.text == "A gap"


def test_a_layout_serializes_as_json_through_its_dictionary() -> None:
    layout = pylopdf.open(build_text_pdf("BT /F1 20 Tf 72 700 Td (Json) Tj ET")).pages[0].layout(chars=True)

    data = json.loads(json.dumps(layout.to_dict()))

    assert data["blocks"][0]["lines"][0]["spans"][0]["chars"][0]["c"] == "J"


def test_an_empty_page_serializes_to_empty_containers() -> None:
    layout = pylopdf.Document().pages.new((100, 50)).layout()

    assert layout.to_html() == '<div style="position:relative;width:100pt;height:50pt">\n</div>\n'
    assert _xml(layout.to_xml()).attrib == {"width": "100", "height": "50"}
