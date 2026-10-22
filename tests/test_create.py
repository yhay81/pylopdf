"""New documents that krilla writes: pylopdf.create() and its page canvases."""

from __future__ import annotations

import base64
import datetime as dt
import gc
import warnings
from pathlib import Path
from typing import Any

import pytest

import pylopdf
from pylopdf import BlendMode, Color, Matrix, PathBuilder, Stroke, TagRole

FONT_ROOT = Path(__file__).parents[1] / "fonts"
NOTO_SANS_JP = FONT_ROOT / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
RED = Color(1.0, 0.0, 0.0)
BLUE = Color(0.0, 0.0, 1.0)
BLACK = Color(0.0, 0.0, 0.0)

# Generated 64x64 grayscale linear-gradient JPEG, as in test_shape.
PHOTO_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAME"
    "BgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/wAALCABAAEABAREA"
    "/8QAFgABAQEAAAAAAAAAAAAAAAAAAAgJ/8QAGhAAAgMBAQAAAAAAAAAAAAAAABcBZKFT"
    "kf/aAAgBAQAAPwDD5axwkLWOEhaxwkLWOEhaxwkLWOEhaxwkLWOElAravgW1fAtq+Bb"
    "V8C2r4FtXwLavgW1fCgFtXwLavgW1fAtq+BbV8C2r4FtXwLavhQC2r4FtXwLavgW1fA"
    "tq+BbV8C2r4FtXwoBbV8C2r4FtXwLavgW1fAtq+BbV8C2r4UAtq+BbV8C2r4FtXwLav"
    "gW1fAtq+BbV8KAW1fAtq+BbV8C2r4FtXwLavgW1fAtq+FALeOEeBbxwjwLeOEeBbxwj"
    "wLeOEeBbxwjwLeOEeBbxwjw//9k="
)


@pytest.fixture(scope="module")
def font() -> pylopdf.Font:
    return pylopdf.Font(fontfile=NOTO_SANS_JP)


def _items(page: pylopdf.Page) -> list[Any]:
    return list(page.content())


def _assert_rect(actual: pylopdf.Rect, expected: tuple[float, float, float, float], tolerance: float = 0.01) -> None:
    assert tuple(actual) == pytest.approx(expected, abs=tolerance)


def _png() -> bytes:
    doc = pylopdf.Document()
    page = doc.pages.new((4, 2))
    with page.draw() as canvas:
        canvas.rect((0, 0, 2, 2), fill=RED)
    return page.get_pixmap().tobytes()


def test_pages_join_in_order(font: pylopdf.Font) -> None:
    builder = pylopdf.create()
    with builder.page((300, 200)) as canvas:
        canvas.rect((10, 20, 110, 70), fill=RED, stroke=Stroke(paint=BLUE, width=2))
        canvas.text("First page", (20, 120), font=font, size=14)
    with builder.page((200, 400)) as canvas:
        canvas.text("Second page", (20, 50), font=font)
    assert builder.page_count == 2
    doc = builder.finish()
    assert len(doc.pages) == 2
    first, second = doc.pages
    assert tuple(first.rect) == (0, 0, 300, 200)
    assert tuple(second.rect) == (0, 0, 200, 400)
    rect, text = _items(first)
    _assert_rect(rect.bbox, (10, 20, 110, 70))
    assert rect.fill == Color(1.0, 0.0, 0.0)
    assert rect.stroke.width == pytest.approx(2)
    assert "NotoSansJP-Regular" in text.font.name
    assert text.glyphs[0].origin.y == pytest.approx(120, abs=0.01)
    assert first.text() == "First page\n"
    assert second.text() == "Second page\n"


def test_each_face_is_embedded_once(font: pylopdf.Font) -> None:
    builder = pylopdf.create()
    for index in range(3):
        with builder.page((200, 100)) as canvas:
            canvas.text(f"Page {index}", (10, 50), font=font)
            canvas.text("again", (10, 80), font=font)
    data = builder.to_bytes()
    assert data.count(b"/FontFile") == 1


def test_standard_14_fonts_are_refused() -> None:
    builder = pylopdf.create()
    with builder.page() as canvas, pytest.raises(pylopdf.PdfError, match="embeds every font"):
        canvas.text("Hello", (20, 40))


def test_text_without_a_font_uses_an_installed_extra() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    builder = pylopdf.create()
    with builder.page((200, 100)) as canvas:
        canvas.text("こんにちは", (10, 50))
    assert builder.finish().pages[0].text() == "こんにちは\n"


def test_images(tmp_path: Path) -> None:
    png = _png()
    path = tmp_path / "photo.jpg"
    path.write_bytes(PHOTO_JPEG)
    pixmap = pylopdf.Document().pages.new((3, 3)).get_pixmap()
    builder = pylopdf.create()
    with builder.page((300, 200)) as canvas:
        canvas.image((10, 10, 50, 30), png)
        canvas.image((60, 10, 100, 50), path)
        canvas.image((110, 10, 150, 50), pixmap)
        canvas.image((160, 10, 200, 90), png, rotate=90)
        canvas.image((10, 100, 50, 120), png)
    doc = builder.finish()
    images = doc.pages[0].content().images
    assert len(images) == 5
    _assert_rect(images[0].bbox, (10, 10, 50, 30))
    _assert_rect(images[1].bbox, (60, 10, 100, 50))
    _assert_rect(images[3].bbox, (160, 10, 200, 90))
    # The same image is stored once, and JPEG data passes through.
    assert images[0].xref == images[4].xref
    extracted = doc.images[images[1].xref].extract()
    assert (extracted.format, extracted.data) == ("jpeg", PHOTO_JPEG)
    pixels = doc.pages[0].get_pixmap()
    offset = 20 * pixels.stride + 15 * 3
    assert pixels.samples[offset : offset + 3] == b"\xff\x00\x00"


def test_scopes(font: pylopdf.Font) -> None:
    builder = pylopdf.create()
    with builder.page((300, 200)) as canvas:
        with canvas.transform(Matrix(1, 0, 0, 1, 100, 50)), canvas.transform(Matrix(2, 0, 0, 2, 0, 0)):
            canvas.rect((10, 10, 20, 20), fill=RED)
        with canvas.clip(PathBuilder().rect((0, 0, 100, 100))), canvas.opacity(0.5):
            canvas.circle((100, 100), 50, fill=BLACK)
        with canvas.blend(BlendMode.MULTIPLY):
            canvas.text("blend", (10, 180), font=font)
    scaled, clipped, text = _items(builder.finish().pages[0])
    _assert_rect(scaled.bbox, (120, 70, 140, 90))
    # krilla clips the transparency group to its content as well.
    assert any(tuple(clip.bbox) == (0, 0, 100, 100) for clip in clipped.context.clips)
    assert clipped.context.opacity == pytest.approx(0.5, abs=1 / 255)
    assert text.blend_mode is BlendMode.MULTIPLY


def test_placing_pages_is_refused() -> None:
    source = pylopdf.Document()
    source.pages.new()
    builder = pylopdf.create()
    with builder.page() as canvas, pytest.raises(ValueError, match="cannot place other pages"):
        canvas.page((0, 0, 100, 100), source.pages[0])


def test_a_failed_page_is_left_out(font: pylopdf.Font) -> None:
    builder = pylopdf.create()

    def fail() -> None:
        with builder.page() as canvas:
            canvas.text("lost", (10, 50), font=font)
            msg = "stop"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError):
        fail()
    assert builder.page_count == 0
    with builder.page((100, 100)) as canvas:
        canvas.rect((0, 0, 10, 10), fill=RED)
    assert builder.page_count == 1


def test_finishing_again_writes_later_pages(font: pylopdf.Font) -> None:
    builder = pylopdf.create()
    with builder.page((100, 100)) as canvas:
        canvas.text("one", (10, 50), font=font)
    assert len(builder.finish().pages) == 1
    with builder.page((100, 100)) as canvas:
        canvas.text("two", (10, 50), font=font)
    data = builder.to_bytes()
    assert data.startswith(b"%PDF-")
    assert len(pylopdf.open(data).pages) == 2


def test_metadata(font: pylopdf.Font) -> None:
    builder = pylopdf.create(
        language="en-US",
        title="Report",
        author="Ada",
        subject="Quarterly numbers",
        keywords=["finance", "q3"],
    )
    with builder.page((100, 100)) as canvas:
        canvas.text("x", (10, 50), font=font)
    doc = builder.finish()
    metadata = doc.metadata
    assert metadata["title"] == "Report"
    assert metadata["author"] == "Ada"
    assert metadata["subject"] == "Quarterly numbers"
    assert "finance" in metadata["keywords"]
    assert metadata["producer"].startswith("pylopdf ")
    assert doc.language_tag == "en-US"


def test_tagged_documents(font: pylopdf.Font) -> None:
    builder = pylopdf.create(tagged=True, language="en", title="Tagged")
    with builder.page((300, 300)) as canvas:
        canvas.rect((0, 0, 300, 20), fill=BLUE)
        with canvas.tag(TagRole.HEADING_1, title="Introduction"):
            canvas.text("Introduction", (20, 60), font=font, size=18)
        with canvas.tag(TagRole.PARAGRAPH):
            canvas.text("First line\nSecond line", (20, 100), font=font)
        with canvas.tag("Figure", alt="A red square"):
            canvas.rect((20, 150, 80, 210), fill=RED)
        with canvas.tag(TagRole.ARTIFACT):
            canvas.text("Page 1", (250, 290), font=font, size=8)
    doc = builder.finish()
    catalog = doc.catalog
    assert catalog["MarkInfo"]["Marked"] is True
    root = catalog["StructTreeRoot"]
    assert root["Type"] == "StructTreeRoot"
    items = _items(doc.pages[0])
    tags = [[mark.tag for mark in item.context.marked_content] for item in items]
    assert tags[0] == ["Artifact"]
    assert tags[1] == ["Span"]
    assert tags[2] == ["Span"]
    assert tags[3] == ["Span"]
    assert tags[5] == ["Artifact"]
    assert all(mark.mcid is not None for item in items[1:5] for mark in item.context.marked_content)
    elements = _structure_types(root)
    assert elements[:3] == ["H1", "P", "Figure"]


def _structure_types(root: pylopdf.Dictionary) -> list[str]:
    """Return the structure types under the root, depth first."""
    found: list[str] = []
    stack = [root["K"]]
    while stack:
        node = stack.pop()
        if isinstance(node, pylopdf.Array):
            stack.extend(reversed(list(node)))
        elif isinstance(node, pylopdf.Dictionary) and "S" in node:
            found.append(str(node["S"]))
            if "K" in node:
                stack.append(node["K"])
    return [name for name in found if name not in {"Document", "Part"}]


def test_tag_scopes_need_a_tagged_new_document(font: pylopdf.Font) -> None:
    builder = pylopdf.create()
    with builder.page() as canvas, pytest.raises(ValueError, match="tagged document"):
        canvas.tag(TagRole.PARAGRAPH)
    doc = pylopdf.Document()
    page = doc.pages.new()
    with page.draw() as canvas, pytest.raises(ValueError, match="new document"):
        canvas.tag(TagRole.PARAGRAPH)
    tagged = pylopdf.create(tagged=True)
    with tagged.page() as canvas:
        with pytest.raises(ValueError, match="TagRole"):
            canvas.tag("Paragraph")
        with pytest.raises(ValueError, match="BCP 47"):
            canvas.tag(TagRole.SPAN, lang="not a tag")
        canvas.text("ok", (10, 50), font=font)


def test_pdf_a_documents_validate(font: pylopdf.Font) -> None:
    builder = pylopdf.create(standard="pdf/a-2b", title="Archive")
    with builder.page((200, 100)) as canvas:
        canvas.text("Archived", (10, 50), font=font)
        with canvas.opacity(0.5):
            canvas.rect((0, 0, 50, 50), fill=RED)
    doc = builder.finish()
    assert doc.get_pdfa_claim() == (2, "B")


def test_violations_are_refused(font: pylopdf.Font) -> None:
    builder = pylopdf.create(standard="pdf/a-1b")
    with builder.page((200, 100)) as canvas:
        canvas.text("Archived", (10, 50), font=font)
        with canvas.opacity(0.5):
            canvas.rect((0, 0, 50, 50), fill=RED)
    with pytest.raises(pylopdf.PdfError, match="violates the requested standard"):
        builder.finish()


def test_pdf_ua_documents(font: pylopdf.Font) -> None:
    builder = pylopdf.create(standard="pdf/ua-1", language="en", title="Accessible")
    assert builder.tagged
    with builder.page((200, 100)) as canvas:
        canvas.bookmark("Start")
        with canvas.tag(TagRole.PARAGRAPH):
            canvas.text("Readable", (10, 50), font=font)
    doc = builder.finish()
    assert "pdfuaid" in doc.get_xml_metadata()


def test_invalid_settings() -> None:
    with pytest.raises(ValueError, match="standard must be one of"):
        pylopdf.create(standard="pdf/x-4")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="BCP 47"):
        pylopdf.create(language="english language")
    with pytest.raises(TypeError, match="title"):
        pylopdf.create(title=3)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="size"):
        pylopdf.create().page((0, 100))


def test_an_unwritten_page_warns() -> None:
    builder = pylopdf.create()
    canvas = builder.page()
    canvas.rect((10, 10, 50, 50), fill=RED)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del canvas
        gc.collect()
    assert any(issubclass(warning.category, ResourceWarning) for warning in caught)
    assert builder.page_count == 0


def test_bookmarks_nest_by_level(font: pylopdf.Font) -> None:
    builder = pylopdf.create()
    with builder.page((200, 200)) as canvas:
        canvas.bookmark("Chapter 1")
        canvas.bookmark("Section 1.1", level=2, at=(0, 100))
        canvas.text("x", (10, 50), font=font)
    with builder.page((200, 200)) as canvas:
        canvas.bookmark("Section 1.2", level=2)
        canvas.bookmark("Chapter 2")
        canvas.text("y", (10, 50), font=font)
    outline = builder.finish().get_outline()
    assert [(entry.level, entry.title, entry.page) for entry in outline] == [
        (1, "Chapter 1", 0),
        (2, "Section 1.1", 0),
        (2, "Section 1.2", 1),
        (1, "Chapter 2", 1),
    ]
    skipping = pylopdf.create()
    with skipping.page() as canvas:
        canvas.bookmark("Deep", level=2)
    with pytest.raises(pylopdf.PdfError, match="levels grow one at a time"):
        skipping.finish()
    doc = pylopdf.Document()
    with doc.pages.new().draw() as canvas, pytest.raises(ValueError, match="outline"):
        canvas.bookmark("x")


def test_a_fixed_creation_date_makes_output_reproducible(font: pylopdf.Font) -> None:
    created = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)

    def build() -> bytes:
        builder = pylopdf.create(created=created, title="Same")
        with builder.page((100, 100)) as canvas:
            canvas.text("same", (10, 50), font=font)
        return builder.to_bytes()

    first = build()
    assert first == build()
    assert pylopdf.open(first).metadata["creationDate"].startswith("D:20260102030405")
