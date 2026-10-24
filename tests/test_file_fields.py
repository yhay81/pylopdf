"""The file fields that attachments and file attachment annotations share."""

from __future__ import annotations

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import FileAttachment, LimitError


def _attachment_pdf(*, inline: bool) -> bytes:
    filespec = "<< /Type /Filespec /F (data.csv) /UF (data.csv) /Desc (Rows) /EF << /F 4 0 R >> >>"
    names = f"[(data) {filespec}]" if inline else "[(data) 5 0 R]"
    content = b"a,b"
    return build_raw_pdf(
        {
            1: f"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names {names} >> >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
            4: (
                b"<< /Type /EmbeddedFile /Params << /Size 3 /CreationDate (D:20260101000000Z) >> /Length 3 >>\n"
                b"stream\n" + content + b"\nendstream"
            ),
            5: filespec,
        }
    )


@pytest.mark.parametrize("inline", [False, True])
def test_attachment_fields_are_assignable(*, inline: bool) -> None:
    doc = pylopdf.open(_attachment_pdf(inline=inline))
    attachment = doc.attachments["data"]

    assert (attachment.created, attachment.modified) == ("D:20260101000000Z", None)
    attachment.filename = "données.csv"
    attachment.description = "Zeilen"
    attachment.created = "D:20250101000000+09'00'"
    attachment.modified = "D:20260301000000Z"

    for current in (doc, pylopdf.open(doc.to_bytes())):
        edited = current.attachments["data"]
        assert (edited.filename, edited.description) == ("données.csv", "Zeilen")
        assert (edited.created, edited.modified) == ("D:20250101000000+09'00'", "D:20260301000000Z")
        assert (edited.size, edited.read()) == (3, b"a,b")
        assert list(current.attachments) == ["data"]

    del attachment.description
    del attachment.created
    del attachment.modified
    assert (attachment.description, attachment.created, attachment.modified) == (None, None, None)


def test_writing_sets_the_modification_date() -> None:
    doc = pylopdf.open(_attachment_pdf(inline=False))
    attachment = doc.attachments["data"]

    attachment.write(b"x")

    assert attachment.modified is not None
    assert attachment.modified.startswith("D:2")
    assert attachment.created == "D:20260101000000Z"


def test_annotation_file_fields_are_assignable() -> None:
    page = pylopdf.Document().pages.new((200, 200))
    annotation = page.annotations.add(FileAttachment((20, 30), b"payload", filename="data.bin", description="Raw"))
    attached = annotation.file
    assert attached is not None
    created = attached.created
    assert created is not None

    attached.filename = "renamed.bin"
    attached.modified = "D:20270101000000Z"
    del attached.description

    reopened = pylopdf.open(page.document.to_bytes()).pages[0].annotations[0].file
    assert reopened is not None
    assert (reopened.filename, reopened.description) == ("renamed.bin", None)
    assert (reopened.created, reopened.modified) == (created, "D:20270101000000Z")
    assert reopened.read() == b"payload"

    reopened.write(b"other")
    assert reopened.modified != "D:20270101000000Z"


def test_a_direct_file_specification_of_an_annotation_is_edited() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
                    "/Annots [<< /Type /Annot /Subtype /FileAttachment /Rect [10 10 30 30] "
                    "/FS << /Type /Filespec /F (a.txt) /EF << /F 4 0 R >> >> >>] >>"
                ),
                4: "<< /Type /EmbeddedFile /Length 1 >>\nstream\nA\nendstream",
            }
        )
    )
    annotation = doc.pages[0].annotations[0]
    attached = annotation.file
    assert attached is not None

    attached.created = "D:20200101000000Z"
    attached.filename = "b.txt"

    assert annotation.xref != 0
    assert (attached.filename, attached.created) == ("b.txt", "D:20200101000000Z")


def test_values_are_validated() -> None:
    doc = pylopdf.open(_attachment_pdf(inline=False))
    attachment = doc.attachments["data"]

    with pytest.raises(TypeError):
        attachment.created = 2026  # ty: ignore[invalid-assignment]
    with pytest.raises(ValueError, match="PDF date"):
        attachment.modified = "D:2026\n"
    with pytest.raises(ValueError, match="PDF date"):
        attachment.modified = "D" * 65
    with pytest.raises(ValueError, match="empty"):
        attachment.filename = ""
    with pytest.raises(TypeError):
        attachment.description = None  # ty: ignore[invalid-assignment]
    with pytest.raises(LimitError) as caught:
        attachment.description = "x" * (1024 * 1024 + 1)
    assert caught.value.code == "embedded_file_input_size"
    core = doc._doc  # noqa: SLF001 - the core repeats the checks.
    edits: list[dict[str, str | None]] = [{"filename": None}, {"unknown": "x"}, {"created": "bad\n"}, {"filename": ""}]
    for edit in edits:
        with pytest.raises(pylopdf.PdfError):
            core.embfile_edit("data", edit)
    assert not doc.is_dirty


def test_dates_need_an_embedded_file_stream() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: (
                    "<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names "
                    "[(bare) << /Type /Filespec /F (bare.txt) >>] >> >> >>"
                ),
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
            }
        )
    )
    attachment = doc.attachments["bare"]

    attachment.description = "No stream"
    with pytest.raises(pylopdf.PdfError):
        attachment.created = "D:20200101000000Z"
    assert attachment.description == "No stream"
