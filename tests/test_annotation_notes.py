"""Note annotations, popups, open states, and replies."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import AnnotationFlags, Color, Note, NoteIcon, Point, Rect, Square

WHITE = (255, 255, 255)


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * pixmap.channels
    red, green, blue = bytes(pixmap)[offset : offset + 3]
    return red, green, blue


def _page() -> pylopdf.Page:
    return pylopdf.Document().pages.new((300, 200))


@pytest.mark.parametrize("icon", list(NoteIcon))
def test_every_note_icon_draws_in_its_square(icon: NoteIcon) -> None:
    page = _page()

    note = page.annotations.add(Note((40, 40), icon=icon, contents="Look here", color=Color(0, 1, 0)))

    assert note.type is pylopdf.AnnotationType.TEXT
    assert note.rect == Rect(40, 40, 60, 60)
    assert note.contents == "Look here"
    assert note.icon == "".join(part.capitalize() for part in icon.value.split("_"))
    assert note.flags == AnnotationFlags.PRINT | AnnotationFlags.NO_ZOOM | AnnotationFlags.NO_ROTATE
    assert note.is_open is False
    pixmap = page.pixmap(dpi=72)
    inside = {_pixel(pixmap, x, y) for x in range(40, 60) for y in range(40, 60)}
    assert (0, 0, 0) in inside or any(red < 64 and blue < 64 for red, _, blue in inside)
    assert any(green > 200 and red < 64 for red, green, _ in inside)
    outside = {_pixel(pixmap, x, y) for x in range(0, 300, 7) for y in (30, 70)}
    assert outside == {WHITE}


def test_notes_survive_save_and_reopen() -> None:
    page = _page()
    page.annotations.add(Note(Point(100, 20), icon=NoteIcon.KEY, is_open=True, opacity=0.5))

    (note,) = pylopdf.open(page.document.to_bytes()).pages[0].annotations

    assert note.icon == "Key"
    assert note.is_open is True
    assert note.opacity == pytest.approx(0.5)
    assert note.color == Color(1, 1, 0)
    assert note.obj["Open"] is True


def test_popups_are_added_moved_opened_and_removed() -> None:
    page = _page()
    note = page.annotations.add(Note((10, 10), contents="first"))
    square = page.annotations.add(Square((100, 100, 150, 150)))

    assert note.popup_rect is None
    note.popup_rect = (40, 10, 160, 80)
    assert note.popup_rect == Rect(40, 10, 160, 80)
    # The popup belongs to its annotation, which page.annotations lists alone.
    assert len(page.annotations) == 2
    popup = note.obj["Popup"]
    assert popup["Subtype"] == "Popup"
    assert popup["Parent"] == note.obj
    assert [item["Subtype"] for item in page.obj["Annots"]] == ["Text", "Square", "Popup"]

    note.popup_rect = Rect(50, 20, 170, 90)
    assert note.popup_rect == Rect(50, 20, 170, 90)
    assert len(page.obj["Annots"]) == 3

    note.is_open = True
    assert note.is_open is True
    assert popup["Open"] is True
    assert note.obj["Open"] is True
    square.popup_rect = (160, 100, 260, 150)
    square.is_open = True
    assert square.is_open is True
    assert "Open" not in square.obj

    reopened = pylopdf.open(page.document.to_bytes()).pages[0]
    assert reopened.annotations[0].popup_rect == Rect(50, 20, 170, 90)
    assert reopened.annotations[1].is_open is True

    del note.popup_rect
    assert note.popup_rect is None
    assert "Popup" not in note.obj
    assert [item["Subtype"] for item in page.obj["Annots"]] == ["Text", "Square", "Popup"]
    # Removing an annotation removes its popup.
    del page.annotations[1]
    assert [item["Subtype"] for item in page.obj["Annots"]] == ["Text"]


def test_open_needs_a_popup_or_a_note() -> None:
    square = _page().annotations.add(Square((10, 10, 50, 50)))

    assert square.is_open is False
    with pytest.raises(pylopdf.PdfError, match="no popup"):
        square.is_open = True
    with pytest.raises(TypeError, match="bool"):
        square.is_open = cast("Any", 1)
    with pytest.raises((TypeError, ValueError)):
        square.popup_rect = cast("Any", (1, 2, 3))


def test_replies_name_the_annotation_they_answer() -> None:
    page = _page()
    question = page.annotations.add(Note((10, 10), contents="Why?"))
    answer = page.annotations.add(Note((40, 10), contents="Because."))
    other = page.annotations.add(Note((70, 10), contents="Unrelated"))

    assert answer.in_reply_to is None
    answer.in_reply_to = question
    assert answer.in_reply_to == question
    assert question.replies == [answer]
    assert other.replies == []
    assert answer.obj["IRT"] == question.obj

    reopened = pylopdf.open(page.document.to_bytes()).pages[0]
    assert reopened.annotations[1].in_reply_to == reopened.annotations[0]

    del answer.in_reply_to
    assert answer.in_reply_to is None
    assert question.replies == []
    with pytest.raises(pylopdf.PdfError, match="another annotation"):
        answer.in_reply_to = answer
    with pytest.raises(TypeError, match="Annotation or None"):
        answer.in_reply_to = cast("Any", 3)
    elsewhere = pylopdf.Document().pages.new((100, 100)).annotations.add(Note((1, 1)))
    with pytest.raises(ValueError, match="its own page"):
        answer.in_reply_to = elsewhere


def test_note_specifications_validate_their_fields() -> None:
    note = Note((1, 2))
    assert (note.at, note.icon, note.color, note.opacity, note.is_open) == (
        Point(1, 2),
        NoteIcon.NOTE,
        Color(1, 1, 0),
        1.0,
        False,
    )
    assert Note((1, 2), icon=cast("Any", "help")).icon is NoteIcon.HELP
    with pytest.raises(ValueError, match="'wave'"):
        Note((1, 2), icon=cast("Any", "wave"))
    with pytest.raises(TypeError, match="is_open"):
        Note((1, 2), is_open=cast("Any", 1))
    with pytest.raises(pylopdf.LimitError) as raised:
        Note((1, 2), contents="x" * (1024 * 1024))
    assert raised.value.code == "annotation_input_size"
    core = pylopdf.Document()
    core.pages.new((100, 100))
    with pytest.raises(pylopdf.PdfError, match="unknown note icon"):
        core._doc.add_annotation(1, {"subtype": "Text", "rect": (0.0, 0.0, 20.0, 20.0), "icon": "Wave"})  # noqa: SLF001
    with pytest.raises(pylopdf.PdfError, match="belong to a note"):
        core._doc.add_annotation(1, {"subtype": "Square", "rect": (0.0, 0.0, 20.0, 20.0), "open": True})  # noqa: SLF001


def test_direct_annotations_become_objects_for_popups() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
                    "/Annots [<< /Type /Annot /Subtype /Text /Rect [10 10 30 30] /Open true >>] >>"
                ),
            }
        )
    )
    (note,) = doc.pages[0].annotations
    assert note.xref == 0
    assert note.is_open is True

    note.popup_rect = (40, 10, 120, 60)

    assert note.xref > 0
    assert note.obj["Popup"]["Parent"] == note.obj
    assert note.popup_rect == Rect(40, 10, 120, 60)


def test_rendering_draws_a_missing_note_appearance() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [4 0 R] >>",
                4: "<< /Type /Annot /Subtype /Text /Rect [10 70 30 90] /C [0 0 1] /Name /Insert >>",
            }
        )
    )
    before = doc.to_bytes()

    pixmap = doc.pages[0].pixmap(dpi=72)

    assert _pixel(pixmap, 20, 22) == (0, 0, 255)
    assert doc.to_bytes() == before
