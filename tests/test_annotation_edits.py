"""Editing annotation properties, which draws the appearance again."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import (
    AnnotationFlags,
    AttachmentIcon,
    BlendMode,
    Border,
    BorderStyle,
    Caret,
    Color,
    FileAttachment,
    FreeText,
    Ink,
    LineAnnotation,
    LineEnding,
    Note,
    NoteIcon,
    Point,
    Rect,
    Square,
    Stamp,
    StampIcon,
)

WHITE = (255, 255, 255)


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * pixmap.channels
    red, green, blue = bytes(pixmap)[offset : offset + 3]
    return red, green, blue


def _page() -> pylopdf.Page:
    return pylopdf.Document().pages.new((300, 300))


def test_style_edits_draw_the_appearance_again() -> None:
    page = _page()
    square = page.annotations.add(Square((10, 10, 100, 60)))
    modified = square.modified

    square.color = Color(0, 0, 1)
    square.interior_color = Color(1, 1, 0)
    square.border = Border(4, dashes=(3, 1))
    square.opacity = 0.5
    square.blend_mode = BlendMode.MULTIPLY
    square.rect = (20, 20, 120, 80)

    assert square.rect == Rect(20, 20, 120, 80)
    assert (square.color, square.interior_color) == (Color(0, 0, 1), Color(1, 1, 0))
    assert square.border == Border(4, dashes=(3, 1))
    assert square.opacity == pytest.approx(0.5)
    assert square.blend_mode is BlendMode.MULTIPLY
    assert square.modified is not None
    assert modified is not None
    assert square.modified >= modified
    pixmap = page.pixmap(dpi=72)
    red, green, blue = _pixel(pixmap, 70, 50)
    assert (red, green) == (255, 255)
    assert blue == pytest.approx(128, abs=1)
    assert _pixel(pixmap, 15, 15) == WHITE

    square.opacity = 1.0
    del square.interior_color
    square.border = None
    del square.blend_mode
    assert "CA" not in square.obj
    assert square.interior_color is None
    assert square.border is None
    assert square.blend_mode is None
    # Without /BS or /Border the border is one point wide.
    assert _pixel(page.pixmap(dpi=72), 70, 50) == WHITE

    reopened = pylopdf.open(page.document.to_bytes()).pages[0].annotations[0]
    assert reopened.color == Color(0, 0, 1)
    assert reopened.rect == Rect(20, 20, 120, 80)


def test_text_properties_are_set_and_removed() -> None:
    note = _page().annotations.add(Note((10, 10)))

    note.contents = "Body"
    note.author = "Author"
    note.subject = "Subject"
    note.name = "note-1"
    note.language = "ja-JP"
    note.icon = NoteIcon.KEY
    note.flags = AnnotationFlags.PRINT | AnnotationFlags.LOCKED

    assert (note.contents, note.author, note.subject, note.name, note.language) == (
        "Body",
        "Author",
        "Subject",
        "note-1",
        "ja-JP",
    )
    assert note.icon == "Key"
    assert note.flags == AnnotationFlags.PRINT | AnnotationFlags.LOCKED
    del note.contents, note.author, note.subject, note.name, note.language
    assert (note.contents, note.author, note.subject, note.name, note.language) == (None, None, None, None, None)
    with pytest.raises(TypeError, match="contents must be a str"):
        note.contents = cast("Any", 5)
    with pytest.raises(pylopdf.LimitError):
        note.contents = "x" * (1024 * 1024 + 1)
    with pytest.raises(ValueError, match="icon is a"):
        note.icon = cast("Any", "Key")
    with pytest.raises(pylopdf.PdfError, match="unknown icon"):
        note.icon = StampIcon.SOLD


def test_icons_belong_to_their_types() -> None:
    page = _page()
    stamp = page.annotations.add(Stamp((10, 10, 200, 60)))
    attachment = page.annotations.add(FileAttachment((10, 100), b"x", filename="x"))
    square = page.annotations.add(Square((10, 200, 50, 240)))

    stamp.icon = StampIcon.DRAFT
    attachment.icon = AttachmentIcon.TAG

    assert stamp.icon == "Draft"
    assert "DRAFT" in page.text()
    assert attachment.icon == "Tag"
    with pytest.raises(pylopdf.PdfError, match="has an icon"):
        square.icon = NoteIcon.NOTE


def test_points_and_endings_are_edited_and_the_rectangle_follows() -> None:
    page = _page()
    line = page.annotations.add(LineAnnotation((10, 10), (50, 10), border=Border(2)))
    ink = page.annotations.add(Ink([[(10, 100), (20, 110)]]))

    line.vertices = [Point(20, 200), (280, 200)]
    line.line_endings = (LineEnding.CLOSED_ARROW, LineEnding.NONE)
    ink.strokes = [[(100, 100), (200, 150)], [(150, 90)]]

    assert line.vertices == (Point(20, 200), Point(280, 200))
    assert line.line_endings == (LineEnding.CLOSED_ARROW, LineEnding.NONE)
    assert line.rect.contains(Rect(13, 193, 281, 207))
    assert len(ink.strokes or ()) == 2
    assert ink.rect.contains(Rect(100, 90, 200, 150))
    assert _pixel(page.pixmap(dpi=72), 150, 200) == (255, 0, 0)
    with pytest.raises(ValueError, match="two vertices"):
        line.vertices = [(0, 0), (1, 1), (2, 2)]
    with pytest.raises(pylopdf.PdfError, match="follows its points"):
        line.rect = (0, 0, 10, 10)
    with pytest.raises(pylopdf.PdfError, match="only lines and polylines"):
        ink.line_endings = (LineEnding.NONE, LineEnding.NONE)
    with pytest.raises(pylopdf.PdfError, match="only lines, polygons"):
        page.annotations.add(Square((0, 0, 9, 9))).vertices = [(0, 0), (1, 1)]


def test_free_text_and_stamps_turn_and_their_text_changes() -> None:
    page = _page()
    free_text = page.annotations.add(FreeText((20, 20, 200, 60), "Before", border=Border(1)))
    stamp = page.annotations.add(Stamp((240, 20, 290, 280), icon=StampIcon.FINAL))
    caret = page.annotations.add(Caret((20, 250)))

    free_text.contents = "After the edit"
    stamp.rotation = 90
    caret.rotation = 180

    assert "After the edit" in page.text()
    assert "Before" not in page.text()
    assert (stamp.rotation, caret.rotation) == (90, 180)
    lines = {line.text: tuple(line.direction) for block in page.layout().blocks for line in block.lines}
    assert lines["FINAL"] == pytest.approx((0, 1), abs=1e-6)
    with pytest.raises(pylopdf.PdfError, match="does not fit"):
        free_text.contents = "far too much text " * 20
    assert "After the edit" in page.text()
    with pytest.raises(ValueError, match="multiple of 90"):
        stamp.rotation = 45
    with pytest.raises(pylopdf.PdfError, match="turn by multiples"):
        page.annotations.add(Square((0, 0, 9, 9))).rotation = 90


def test_non_latin_free_text_keeps_its_installed_face() -> None:
    page = _page()
    try:
        free_text = page.annotations.add(FreeText((20, 20, 200, 60), "日本語"))
    except ValueError:
        pytest.skip("needs pylopdf[jp]")

    free_text.contents = "更新"

    assert page.text().strip() == "更新"


def test_edits_of_annotations_that_other_producers_wrote() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots ["
                    "<< /Type /Annot /Subtype /Square /Rect [10 10 50 50] /C [1 0 0] >> "
                    "<< /Type /Annot /Subtype /Sound /Rect [60 10 80 30] >>] >>"
                ),
            }
        )
    )
    square, sound = doc.pages[0].annotations

    square.interior_color = Color(0, 1, 0)
    # The edit stored the page's direct annotations as objects, so a handle
    # of another one found by its index goes stale; read the page again.
    with pytest.raises(pylopdf.StaleObjectError):
        _ = sound.contents
    sound = doc.pages[0].annotations[1]
    sound.contents = "A sound"

    assert square.xref > 0
    assert square.obj["AP"]["N"]["Subtype"] == "Form"
    assert _pixel(doc.pages[0].pixmap(dpi=72), 30, 70) == (0, 255, 0)
    # The engine does not draw sounds; the entry changes and nothing is drawn.
    assert sound.contents == "A sound"
    assert "AP" not in sound.obj
    with pytest.raises(TypeError, match="Border or None"):
        square.border = cast("Any", 2)
    with pytest.raises(TypeError, match="AnnotationFlags"):
        square.flags = cast("Any", True)
    with pytest.raises(ValueError, match="'odd'"):
        square.blend_mode = cast("Any", "odd")
    assert BorderStyle.SOLID


def test_the_core_refuses_bad_edits() -> None:
    page = _page()
    page.annotations.add(Square((10, 10, 50, 50)))
    core = page.document._doc  # noqa: SLF001

    for edit, message in [
        ({"opacity": 0.0}, "opacity"),
        ({"flags": 4096}, "bits 1 to 10"),
        ({"border": (1.0, "D", [])}, "border"),
        ({"blend_mode": "Odd"}, "blend mode"),
        ({"clear": ["rect"]}, "cannot be removed"),
        ({"modified": "2026"}, "PDF date"),
        ({"color": (2.0, 0.0, 0.0)}, "components"),
    ]:
        with pytest.raises(pylopdf.PdfError, match=message):
            core.edit_annotation(1, 0, page.annotations[0].xref, {"clear": [], **edit})
    assert page.annotations[0].color == Color(1, 0, 0)
