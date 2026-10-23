"""FileAttachment annotations and the files they embed."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import AttachmentIcon, Color, FileAttachment, Rect

WHITE = (255, 255, 255)


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * pixmap.channels
    red, green, blue = bytes(pixmap)[offset : offset + 3]
    return red, green, blue


@pytest.mark.parametrize("icon", list(AttachmentIcon))
def test_file_attachments_embed_their_file(icon: AttachmentIcon) -> None:
    page = pylopdf.Document().pages.new((200, 200))

    annotation = page.annotations.add(
        FileAttachment((20, 30), b"payload", filename="data.bin", description="Raw", icon=icon, color=Color(0, 1, 0))
    )

    assert annotation.type is pylopdf.AnnotationType.FILE_ATTACHMENT
    assert annotation.rect == Rect(20, 30, 40, 50)
    assert annotation.icon == "".join(part.capitalize() for part in icon.value.split("_"))
    attached = annotation.file
    assert attached is not None
    assert (attached.filename, attached.description, attached.size) == ("data.bin", "Raw", 7)
    assert attached.read() == b"payload"
    pixmap = page.pixmap(dpi=72)
    inside = {_pixel(pixmap, x, y) for x in range(20, 40) for y in range(30, 50)}
    assert inside != {WHITE}
    # An annotation's file is not one of the document's attachments.
    assert len(page.document.attachments) == 0


def test_attached_files_survive_save_and_are_replaced() -> None:
    page = pylopdf.Document().pages.new((200, 200))
    page.rotation = 270
    page.annotations.add(FileAttachment((20, 30), bytearray(b"first"), filename="note.txt"))

    reopened = pylopdf.open(page.document.to_bytes()).pages[0]
    (annotation,) = reopened.annotations
    assert annotation.rotation == 0
    attached = annotation.file
    assert attached is not None
    assert attached.read() == b"first"
    assert attached.description == ""

    attached.write(memoryview(b"second and longer"))

    assert attached.read() == b"second and longer"
    assert attached.size == 17
    assert attached.filename == "note.txt"
    again = pylopdf.open(reopened.document.to_bytes()).pages[0].annotations[0].file
    assert again is not None
    assert again.read() == b"second and longer"


def test_attached_files_are_bounded() -> None:
    page = pylopdf.Document().pages.new((200, 200))

    with pytest.raises(pylopdf.LimitError) as raised:
        FileAttachment((0, 0), b"12345", filename="x", max_size=4)
    assert raised.value.code == "embedded_file_size"
    with pytest.raises(pylopdf.LimitError) as raised:
        FileAttachment((0, 0), b"", filename="x" * (1024 * 1024 + 1))
    assert raised.value.code == "embedded_file_input_size"
    with pytest.raises(TypeError, match="data must be bytes"):
        FileAttachment((0, 0), cast("Any", "text"), filename="x")
    with pytest.raises(TypeError, match="filename"):
        FileAttachment((0, 0), b"", filename=cast("Any", None))
    with pytest.raises(ValueError, match="'clip'"):
        FileAttachment((0, 0), b"", filename="x", icon=cast("Any", "clip"))
    annotation = page.annotations.add(FileAttachment((0, 0), b"12345", filename="x"))
    attached = annotation.file
    assert attached is not None
    with pytest.raises(pylopdf.LimitError):
        attached.read(max_size=4)
    with pytest.raises(pylopdf.LimitError):
        attached.write(b"123456789", max_size=8)
    assert attached.read() == b"12345"
    core = page.document._doc  # noqa: SLF001
    spec = {"subtype": "FileAttachment", "rect": (0.0, 0.0, 9.0, 9.0), "data": b"123", "filename": "f"}
    with pytest.raises(pylopdf.LimitError):
        core.add_annotation(1, {**spec, "max_size": 2})
    with pytest.raises(pylopdf.PdfError, match="unknown file attachment icon"):
        core.add_annotation(1, {**spec, "icon": "Clip"})
    with pytest.raises(pylopdf.PdfError, match="embeds data with a file name"):
        core.add_annotation(1, {**spec, "subtype": "Square"})


def test_other_annotations_have_no_file() -> None:
    page = pylopdf.Document().pages.new((200, 200))
    note = page.annotations.add(pylopdf.Note((10, 10)))

    assert note.file is None


def test_a_direct_filespec_is_read_and_replaced() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [<< /Type /Annot "
                    "/Subtype /FileAttachment /Rect [10 10 30 30] /Name /Tag "
                    "/FS << /Type /Filespec /F (a.txt) /EF << /F 4 0 R >> >> >>] >>"
                ),
                4: "<< /Type /EmbeddedFile /Length 3 >>\nstream\nabc\nendstream",
            }
        )
    )
    (annotation,) = doc.pages[0].annotations
    attached = annotation.file
    assert attached is not None
    assert (attached.filename, attached.size, attached.read()) == ("a.txt", -1, b"abc")

    attached.write(b"xyz!")

    assert annotation.xref > 0
    assert attached.read() == b"xyz!"
    assert annotation.obj["FS"]["F"] == b"a.txt"
    # Rendering draws the icon that the file attachment has no appearance for.
    assert bytes(doc.pages[0].pixmap(dpi=72)) != b"\xff" * (200 * 100 * 3)
