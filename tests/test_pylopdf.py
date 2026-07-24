"""Behavioral tests for the high-level ``pylopdf.Document`` API."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf


def test_open_from_stream(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    assert doc.page_count == 3
    assert len(doc) == 3


def test_open_from_file(tmp_path: Path, one_page_pdf: bytes) -> None:
    path = tmp_path / "sample.pdf"
    path.write_bytes(one_page_pdf)
    doc = pylopdf.Document(path)
    assert doc.page_count == 1


def test_open_alias(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    assert isinstance(doc, pylopdf.Document)
    assert doc.page_count == 1


def test_filename_and_stream_raises(one_page_pdf: bytes) -> None:
    with pytest.raises(ValueError, match="cannot both be specified"):
        pylopdf.Document("a.pdf", one_page_pdf)


def test_empty_document() -> None:
    doc = pylopdf.Document()
    assert doc.page_count == 0


def test_metadata_roundtrip(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.set_metadata({"title": "Report", "author": "Alice"})
    md = doc.metadata
    assert md["title"] == "Report"
    assert md["author"] == "Alice"
    assert md["subject"] == ""
    assert md["format"].startswith("PDF ")


def test_metadata_unknown_key_raises(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="unknown metadata key"):
        doc.set_metadata({"format": "PDF 2.0"})


def test_metadata_validation_is_atomic(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="unknown metadata key"):
        doc.set_metadata({"title": "変更されない", "format": "PDF 2.0"})
    assert doc.metadata["title"] == ""

    with pytest.raises(TypeError, match="must be a string"):
        doc.set_metadata({"author": "変更されない", "title": 42})  # type: ignore[dict-item]
    assert doc.metadata["author"] == ""


def test_get_page_text(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    assert "Page two" in doc.get_page_text(1)


def test_get_page_text_out_of_range(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(IndexError):
        doc.get_page_text(1)


def test_delete_page(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.delete_page(0)
    assert doc.page_count == 2
    assert "Page one" not in doc.get_page_text(0)


def test_delete_pages(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.delete_pages([0, 2])
    assert doc.page_count == 1
    assert "Page two" in doc.get_page_text(0)


def test_empty_page_lists(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.delete_pages([])
    assert doc.page_count == 1
    doc.select([])
    assert doc.page_count == 0


def test_delete_page_out_of_range(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(IndexError, match="out of range"):
        doc.delete_page(3)


def test_insert_pdf(tmp_path: Path, one_page_pdf: bytes, three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.insert_pdf(pylopdf.Document(stream=three_page_pdf))
    assert doc.page_count == 4

    out = tmp_path / "merged.pdf"
    doc.save(out)
    reopened = pylopdf.Document(out)
    assert reopened.page_count == 4
    assert "Page three" in reopened.get_page_text(3)


def test_split_workflow(three_page_pdf: bytes) -> None:
    # Split: create a new PDF containing selected source pages.
    part = pylopdf.Document(stream=three_page_pdf)
    part.delete_pages([0, 1])
    assert part.page_count == 1
    assert "Page three" in part.get_page_text(0)


def test_select_reorder(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.select([2, 0])
    assert doc.page_count == 2
    assert "Page three" in doc.get_page_text(0)
    assert "Page one" in doc.get_page_text(1)
    # The structure survives save and reload.
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.page_count == 2
    assert "Page three" in reloaded.get_page_text(0)


def test_select_duplicates_pages(three_page_pdf: bytes) -> None:
    """Repeating a page in select duplicates it."""
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.select([0, 0, 1])
    assert doc.page_count == 3
    assert "Page one" in doc.get_page_text(0)
    assert "Page one" in doc.get_page_text(1)
    assert "Page two" in doc.get_page_text(2)
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.page_count == 3
    assert "Page one" in reloaded.get_page_text(1)
    assert reloaded.render_page(0) == reloaded.render_page(1)


def test_select_out_of_range(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(IndexError):
        doc.select([0, 3])


def test_insert_self_raises(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="into itself"):
        doc.insert_pdf(doc)


def test_tobytes(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    data = doc.tobytes()
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF-")


def test_exception_hierarchy(one_page_pdf: bytes) -> None:
    """New exceptions remain compatible with existing ValueError handlers."""
    assert issubclass(pylopdf.PdfError, ValueError)
    assert issubclass(pylopdf.PasswordError, pylopdf.PdfError)
    assert issubclass(pylopdf.DocumentClosedError, pylopdf.PdfError)
    assert issubclass(pylopdf.EncryptedDocumentError, pylopdf.PdfError)
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.close()
    with pytest.raises(pylopdf.DocumentClosedError):
        _ = doc.page_count


def test_broken_pdf_raises_pdf_error() -> None:
    with pytest.raises(pylopdf.PdfError):
        pylopdf.Document(stream=b"%PDF-1.4 broken garbage")


def test_peek_metadata_stream(three_page_pdf: bytes) -> None:
    meta = pylopdf.peek_metadata(stream=three_page_pdf)
    assert meta["page_count"] == 3
    assert meta["encrypted"] is False
    assert meta["format"] == "PDF 1.4"


def test_peek_metadata_requires_exactly_one_source(one_page_pdf: bytes) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        pylopdf.peek_metadata()
    with pytest.raises(ValueError, match="exactly one"):
        pylopdf.peek_metadata("a.pdf", one_page_pdf)


def test_context_manager_closes(one_page_pdf: bytes) -> None:
    with pylopdf.Document(stream=one_page_pdf) as doc:
        assert doc.page_count == 1
    with pytest.raises(ValueError, match="document closed"):
        _ = doc.page_count


def test_empty_page_lists_reject_closed_document(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.close()
    with pytest.raises(ValueError, match="document closed"):
        doc.delete_pages([])
    with pytest.raises(ValueError, match="document closed"):
        doc.select([])


def test_closed_document_repr(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    assert repr(doc) == "<pylopdf.Document>"
    doc.close()
    assert repr(doc) == "<closed pylopdf.Document>"


def test_unicode_metadata(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.set_metadata({"title": "日本語タイトル", "author": "山田 太郎"})
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.metadata["title"] == "日本語タイトル"
    assert reloaded.metadata["author"] == "山田 太郎"


def _png_size(data: bytes) -> tuple[int, int]:
    """Read ``(width, height)`` from a PNG IHDR chunk."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def test_render_page_png(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    data = doc.render_page(0)
    width, height = _png_size(data)
    # Fixture MediaBox is 612×792 Letter at 72 dpi.
    assert (width, height) == (612, 792)


def test_render_page_png_scale(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    width, height = _png_size(doc.render_page(0, scale=2.0))
    assert (width, height) == (1224, 1584)


def test_render_pages_matches_sequential_and_preserves_order(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    pages = [2, 0, 2, 1]

    expected = [doc.render_page(page) for page in pages]
    assert doc.render_pages(pages, workers=2) == expected


def test_render_pages_defaults_to_every_page(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    assert doc.render_pages(workers=1) == [doc.render_page(page) for page in range(doc.page_count)]


def test_render_pages_supports_dpi_background_and_empty_input(
    three_page_pdf: bytes,
) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    expected = doc.render_page(1, dpi=144, background=(255, 255, 255))
    assert doc.render_pages(
        [1],
        dpi=144,
        background=(255, 255, 255),
        workers=2,
    ) == [expected]
    assert doc.render_pages([], workers=2) == []


@pytest.mark.parametrize("workers", [0, -1, 65])
def test_render_pages_rejects_invalid_worker_count(three_page_pdf: bytes, workers: int) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(ValueError, match="workers"):
        doc.render_pages(workers=workers)


@pytest.mark.parametrize("workers", [True, 1.5])
def test_render_pages_rejects_non_integer_workers(three_page_pdf: bytes, workers: object) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(TypeError, match="workers"):
        doc.render_pages(workers=workers)  # type: ignore[arg-type]


def test_render_pages_validates_all_page_numbers_before_rendering(
    three_page_pdf: bytes,
) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(IndexError):
        doc.render_pages([0, 3, 1], workers=2)


def test_render_pages_reflects_structural_edits(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    original_last_page = doc.render_page(2)

    doc.select([2, 0])

    assert doc.render_pages(workers=2) == [original_last_page, doc.render_page(1)]


def test_render_page_reflects_edits(three_page_pdf: bytes) -> None:
    # Rendering reflects state after deleting a page.
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.delete_pages([0, 1])
    assert doc.page_count == 1
    assert _png_size(doc.render_page(0))[0] == 612


def test_render_page_out_of_range(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(IndexError):
        doc.render_page(1)


@pytest.mark.parametrize("scale", [0.0, -1.0, float("nan"), float("inf")])
def test_render_page_invalid_scale(one_page_pdf: bytes, scale: float) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="scale"):
        doc.render_page(0, scale=scale)


def test_render_page_too_small_scale(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="scale"):
        doc.render_page(0, scale=0.0001)


@pytest.mark.parametrize(
    ("page_size", "message"),
    [((100_000, 100_000), "65535-pixel"), ((9_000, 9_000), "64000000-pixel")],
)
def test_render_page_rejects_oversized_page(page_size: tuple[int, int], message: str) -> None:
    doc = pylopdf.Document(stream=build_pdf(["x"], page_size=page_size))
    with pytest.raises(ValueError, match=message):
        doc.render_page(0)


def test_render_page_svg(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    svg = doc.render_page_svg(0)
    assert svg.lstrip().startswith("<")
    assert "svg" in svg[:200]


def test_render_page_cache_reflects_edits(three_page_pdf: bytes) -> None:
    """The render cache remains deterministic and reflects edits."""
    doc = pylopdf.Document(stream=three_page_pdf)
    assert doc.render_page(0) == doc.render_page(0)  # Consecutive renders match.
    page_two = doc.render_page(1)
    doc.delete_page(0)
    # New page 0 renders like old page 1 after deletion.
    assert doc.render_page(0) == page_two


def test_render_page_dpi(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    assert doc.render_page(0, dpi=144) == doc.render_page(0, scale=2.0)


def test_render_page_dpi_with_scale_raises(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="dpi"):
        doc.render_page(0, scale=2.0, dpi=144)


def test_render_page_background(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    transparent = doc.render_page(0)
    white = doc.render_page(0, background=(255, 255, 255))
    assert white.startswith(b"\x89PNG\r\n\x1a\n")
    assert white != transparent
    # RGB and opaque RGBA backgrounds produce the same result.
    assert white == doc.render_page(0, background=(255, 255, 255, 255))


@pytest.mark.parametrize("background", [(0, 0, 256), (0, 0, -1)])
def test_render_page_background_out_of_range(one_page_pdf: bytes, background: tuple[int, int, int]) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="background"):
        doc.render_page(0, background=background)


def test_render_page_background_wrong_length(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="background"):
        doc.render_page(0, background=(255, 255))  # type: ignore[arg-type]


def test_save_options_roundtrip(tmp_path: Path, three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    out = tmp_path / "optimized.pdf"
    doc.save(out, garbage=True, deflate=True, object_streams=True)
    reopened = pylopdf.Document(out)
    assert reopened.page_count == 3
    assert "Page two" in reopened.get_page_text(1)
    # Object streams require PDF 1.5+, so the version is raised.
    assert reopened.metadata["format"] == "PDF 1.5"


def test_tobytes_object_streams_render_consistent(three_page_pdf: bytes) -> None:
    """Rendering survives document mutation during object-stream saving."""
    doc = pylopdf.Document(stream=three_page_pdf)
    before = doc.render_page(0)
    data = doc.tobytes(object_streams=True)
    assert doc.render_page(0) == before
    assert pylopdf.Document(stream=data).render_page(0) == before


def test_multi_document_merge() -> None:
    # Merge three PDFs in order.
    merged = pylopdf.Document()
    for text in ["First", "Second", "Third"]:
        merged.insert_pdf(pylopdf.Document(stream=build_pdf([text])))
    assert merged.page_count == 3
    reloaded = pylopdf.Document(stream=merged.tobytes())
    assert "Second" in reloaded.get_page_text(1)


def test_inherited_page_parent_cycle_does_not_hang(one_page_pdf: bytes) -> None:
    """A damaged cyclic page Parent does not hang processing.

    Hayro extraction completes despite the cycle, though text may be empty when
    Resources is unreachable. pylopdf's own inheritance paths, such as Page
    mediabox, retain cycle detection and raise an explicit error.
    """
    raw = one_page_pdf.replace(b"/Parent 2 0 R", b"/Parent 4 0 R")
    doc = pylopdf.Document(stream=raw)
    assert doc.page_count == 1
    assert isinstance(doc.get_page_text(0), str)
    with pytest.raises(ValueError, match="reference cycle"):
        _ = doc[0].mediabox
