"""Behavioral tests for the high-level ``pylopdf.Document`` API."""

from __future__ import annotations

import io
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


class _FspathOnlyPath(os.PathLike[str]):
    """A path provider whose string representation is not its filesystem path."""

    def __init__(self, path: Path) -> None:
        """Retain the path returned through the filesystem protocol."""
        self._path = path

    def __fspath__(self) -> str:
        """Return the actual filesystem path."""
        return os.fspath(self._path)


def _build_info_fixture(info: str | bytes) -> bytes:
    """Build one page whose trailer points to the caller-provided Info object."""
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: info,
        }
    )
    return pdf.replace(b"/Root 1 0 R >>", b"/Root 1 0 R /Info 4 0 R >>")


def test_open_from_stream(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    assert len(doc.pages) == 3
    assert len(doc.pages) == 3


def test_open_from_file(tmp_path: Path, one_page_pdf: bytes) -> None:
    path = tmp_path / "sample.pdf"
    path.write_bytes(one_page_pdf)
    doc = pylopdf.open(path)
    assert len(doc.pages) == 1


def test_open_uses_pathlike_protocol(tmp_path: Path, one_page_pdf: bytes) -> None:
    path = tmp_path / "sample.pdf"
    path.write_bytes(one_page_pdf)
    assert len(pylopdf.open(_FspathOnlyPath(path)).pages) == 1


def test_probe_uses_pathlike_protocol(tmp_path: Path, one_page_pdf: bytes) -> None:
    path = tmp_path / "sample.pdf"
    path.write_bytes(one_page_pdf)
    assert pylopdf.probe(_FspathOnlyPath(path)).page_count == 1


def test_open_returns_a_document(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    assert isinstance(doc, pylopdf.Document)
    assert len(doc.pages) == 1


def test_empty_document() -> None:
    doc = pylopdf.Document()
    assert len(doc.pages) == 0
    assert doc.is_repaired is False


def test_metadata_roundtrip(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    doc.metadata.update(title="Report", author="Alice")
    md = doc.metadata
    assert md["title"] == "Report"
    assert md["author"] == "Alice"
    assert "subject" not in md
    assert doc.version.startswith("1.")


def test_metadata_unknown_key_raises(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(KeyError, match="unknown metadata key"):
        doc.metadata["bogus"] = "x"


def test_metadata_validation_is_atomic(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(KeyError, match="unknown metadata key"):
        doc.metadata.update({"title": "変更されない", "bogus": "x"})
    assert "title" not in doc.metadata

    with pytest.raises(TypeError, match="metadata values are strings"):
        doc.metadata.update({"author": "変更されない", "title": 42})
    assert "author" not in doc.metadata


def test_metadata_ignores_large_custom_info_values() -> None:
    custom = "x" * (1024 * 1024 + 1)
    doc = pylopdf.open(_build_info_fixture(f"<< /Title (Report) /Custom ({custom}) >>"))
    assert doc.metadata["title"] == "Report"


def test_metadata_reads_reject_excessive_standard_text() -> None:
    title = "x" * (1024 * 1024 + 1)
    pdf = _build_info_fixture(f"<< /Title ({title}) >>")
    doc = pylopdf.open(pdf)
    with pytest.raises(pylopdf.PdfError, match="source text exceeds the 1048576-byte safety limit"):
        dict(doc.metadata)
    with pytest.raises(pylopdf.PdfError, match="returned text exceeds the 1048576-byte safety limit"):
        pylopdf.probe(pdf)


def test_metadata_text_limit_is_atomic(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    doc.metadata["title"] = "original"
    with pytest.raises(pylopdf.LimitError, match="1048576-byte encoded-text safety limit") as caught:
        doc.metadata.update(title="é" * 524288)
    assert caught.value.code == "metadata_input_size"
    assert dict(doc.metadata) == {"title": "original"}


def test_metadata_text_boundary_roundtrip(one_page_pdf: bytes) -> None:
    title = "x" * (1024 * 1024)
    doc = pylopdf.open(one_page_pdf)
    doc.metadata["title"] = title
    assert doc.metadata["title"] == title
    assert pylopdf.open(doc.to_bytes()).metadata["title"] == title


def test_get_page_text(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    assert "Page two" in doc.pages[1].text()


def test_get_page_text_out_of_range(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(IndexError):
        doc.pages[1].text()


def test_get_text_batches_every_page(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    expected = "".join(doc.pages[pno].text() for pno in range(len(doc.pages)))
    assert doc.text() == expected
    assert "Page one" in expected
    assert "Page three" in expected


def test_get_text_page_selection(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    reordered = doc.pages[2].text() + doc.pages[0].text() + doc.pages[2].text()
    assert doc.text([2, 0, 2]) == reordered
    assert doc.text([-1]) == doc.pages[2].text()
    assert doc.text([]) == ""


def test_get_text_out_of_range(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(IndexError):
        doc.text([1])


def test_get_text_rejects_oversized_batch(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    assert doc.text([0] * 4096) == doc.pages[0].text() * 4096
    with pytest.raises(pylopdf.LimitError, match="cannot contain more than 4096 entries") as caught:
        doc.text([0] * 4097)
    assert caught.value.code == "page_entry_count"


def test_get_text_on_closed_document(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    doc.close()
    with pytest.raises(pylopdf.DocumentClosedError):
        doc.text()


def test_delete_page(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    del doc.pages[0]
    assert len(doc.pages) == 2
    assert "Page one" not in doc.pages[0].text()


def test_delete_pages(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.pages.select(index for index in range(len(doc.pages)) if index not in {0, 2})
    assert len(doc.pages) == 1
    assert "Page two" in doc.pages[0].text()


def test_empty_page_lists(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    page = doc.pages[0]
    del doc.pages[:0]
    assert len(doc.pages) == 1
    assert "Hello PDF" in page.text()
    doc.pages.select([])
    assert len(doc.pages) == 0


def test_delete_page_out_of_range(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    with pytest.raises(IndexError, match="out of range for 3 pages"):
        del doc.pages[3]


def test_insert_pdf(tmp_path: Path, one_page_pdf: bytes, three_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    doc.pages.extend(pylopdf.open(three_page_pdf).pages)
    assert len(doc.pages) == 4

    out = tmp_path / "merged.pdf"
    doc.save(out)
    reopened = pylopdf.open(out)
    assert len(reopened.pages) == 4
    assert "Page three" in reopened.pages[3].text()


def test_save_atomically_replaces_an_existing_file(tmp_path: Path, one_page_pdf: bytes) -> None:
    target = tmp_path / "output.pdf"
    target.write_bytes(b"existing output")

    pylopdf.open(one_page_pdf).save(target)

    assert len(pylopdf.open(target).pages) == 1
    assert list(tmp_path.glob(".pylopdf-*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows mode bits do not expose the POSIX contract")
def test_save_preserves_existing_file_mode(tmp_path: Path, one_page_pdf: bytes) -> None:
    target = tmp_path / "output.pdf"
    target.write_bytes(b"existing output")
    target.chmod(0o640)

    pylopdf.open(one_page_pdf).save(target)

    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs host configuration")
def test_save_atomically_updates_a_symlink_target(tmp_path: Path, one_page_pdf: bytes) -> None:
    target = tmp_path / "target.pdf"
    target.write_bytes(b"existing output")
    link = tmp_path / "output.pdf"
    link.symlink_to(target.name)

    pylopdf.open(one_page_pdf).save(link)

    assert link.is_symlink()
    assert len(pylopdf.open(target).pages) == 1


def test_save_preserves_target_when_atomic_replace_fails(
    tmp_path: Path,
    one_page_pdf: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "output.pdf"
    original = b"existing output"
    target.write_bytes(original)

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise PermissionError

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(pylopdf.PdfError, match="failed to save"):
        pylopdf.open(one_page_pdf).save(target)

    assert target.read_bytes() == original
    assert list(tmp_path.glob(".pylopdf-*.tmp")) == []


def test_split_workflow(three_page_pdf: bytes) -> None:
    # Split: create a new PDF containing selected source pages.
    part = pylopdf.open(three_page_pdf)
    del part.pages[0:2]
    assert len(part.pages) == 1
    assert "Page three" in part.pages[0].text()


def test_select_reorder(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    doc.pages.select([2, 0])
    assert len(doc.pages) == 2
    assert "Page three" in doc.pages[0].text()
    assert "Page one" in doc.pages[1].text()
    # The structure survives save and reload.
    reloaded = pylopdf.open(doc.to_bytes())
    assert len(reloaded.pages) == 2
    assert "Page three" in reloaded.pages[0].text()


def test_select_duplicates_pages(three_page_pdf: bytes) -> None:
    """Repeating a page in select duplicates it."""
    doc = pylopdf.open(three_page_pdf)
    doc.pages.select([0, 0, 1])
    assert len(doc.pages) == 3
    assert "Page one" in doc.pages[0].text()
    assert "Page one" in doc.pages[1].text()
    assert "Page two" in doc.pages[2].text()
    reloaded = pylopdf.open(doc.to_bytes())
    assert len(reloaded.pages) == 3
    assert "Page one" in reloaded.pages[1].text()
    assert reloaded.pages[0].pixmap(alpha=True).to_bytes() == reloaded.pages[1].pixmap(alpha=True).to_bytes()


def test_select_out_of_range(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    with pytest.raises(IndexError):
        doc.pages.select([0, 3])


@pytest.mark.parametrize("method_name", ["select", "extend"])
def test_structural_page_iterables_stop_at_small_document_floor(
    one_page_pdf: bytes,
    method_name: str,
) -> None:
    doc = pylopdf.open(one_page_pdf)
    page = doc.pages[0]
    consumed = 0

    def entries() -> Iterator[int | pylopdf.Page]:
        nonlocal consumed
        for _ in range(5000):
            consumed += 1
            yield 0 if method_name == "select" else page

    method = getattr(doc.pages, method_name)
    with pytest.raises(pylopdf.LimitError, match="4096 entries") as caught:
        method(entries())
    assert caught.value.code == "page_entry_count"
    assert consumed == 4097
    assert "Hello PDF" in page.text()


def test_insert_pdf_accepts_ranges_beyond_the_batch_floor(one_page_pdf: bytes) -> None:
    target = pylopdf.open(one_page_pdf)
    source = pylopdf.open(build_pdf([""] * 4097))

    target.pages.insert(0, source.pages)
    assert len(target.pages) == 4098
    assert "Hello PDF" in target.pages[4097].text()


def test_extending_a_document_with_its_own_pages_copies_them(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    xrefs = [page.xref for page in doc.pages]

    doc.pages.extend(doc.pages)

    assert [page.xref for page in doc.pages[:3]] == xrefs
    assert not {page.xref for page in doc.pages[3:]} & set(xrefs)
    assert [page.text() for page in doc.pages[3:]] == [page.text() for page in doc.pages[:3]]


def test_tobytes(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    data = doc.to_bytes()
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF-")


def test_tobytes_output_limit_exact_boundary(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    expected = doc.to_bytes(max_size=None)

    assert doc.to_bytes(max_size=len(expected)) == expected
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.to_bytes(max_size=len(expected) - 1)
    assert caught.value.code == "pdf_output_size"


@pytest.mark.parametrize("max_size", [0, -1, True, 1.5])
def test_tobytes_validates_output_limit(one_page_pdf: bytes, max_size: object) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises((TypeError, ValueError), match="max_size"):
        doc.to_bytes(max_size=max_size)  # ty: ignore[invalid-argument-type]


def test_tobytes_bounds_object_stream_output(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.to_bytes(object_streams=True, max_size=16)
    assert caught.value.code == "pdf_output_size"


def test_exception_hierarchy(one_page_pdf: bytes) -> None:
    """New exceptions remain compatible with existing ValueError handlers."""
    assert issubclass(pylopdf.PdfError, ValueError)
    assert issubclass(pylopdf.PasswordError, pylopdf.PdfError)
    assert issubclass(pylopdf.DocumentClosedError, pylopdf.PdfError)
    assert issubclass(pylopdf.EncryptedDocumentError, pylopdf.PdfError)
    doc = pylopdf.open(one_page_pdf)
    doc.close()
    with pytest.raises(pylopdf.DocumentClosedError):
        _ = len(doc.pages)


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.4 broken garbage",
        b"startxref\n0\n%PDF-1.4\n%%EOF",
    ],
)
def test_broken_pdf_raises_pdf_error(data: bytes) -> None:
    with pytest.raises(pylopdf.PdfError):
        pylopdf.open(data)


def test_probe_stream(three_page_pdf: bytes) -> None:
    meta = pylopdf.probe(three_page_pdf)
    assert meta.page_count == 3
    assert meta.needs_password is False
    assert meta.is_repaired is False
    assert meta.version == "1.4"


def test_probe_file_size_limit(tmp_path: Path, three_page_pdf: bytes) -> None:
    path = tmp_path / "three-pages.pdf"
    path.write_bytes(three_page_pdf)
    limit = len(three_page_pdf)

    assert pylopdf.probe(path, max_file_size=limit).page_count == 3
    assert pylopdf.probe(three_page_pdf, max_file_size=limit).page_count == 3

    with pytest.raises(pylopdf.LimitError) as path_error:
        pylopdf.probe(path, max_file_size=limit - 1)
    with pytest.raises(pylopdf.LimitError) as stream_error:
        pylopdf.probe(three_page_pdf, max_file_size=limit - 1)
    assert path_error.value.code == "file_size"
    assert stream_error.value.code == "file_size"


@pytest.mark.parametrize("value", [True, 1.5, "1024"])
def test_probe_file_size_limit_rejects_non_integers(
    one_page_pdf: bytes,
    value: object,
) -> None:
    with pytest.raises(TypeError, match="max_file_size"):
        pylopdf.probe(one_page_pdf, max_file_size=value)  # ty: ignore[invalid-argument-type]


def test_probe_file_size_limit_must_be_positive(one_page_pdf: bytes) -> None:
    with pytest.raises(ValueError, match="max_file_size"):
        pylopdf.probe(one_page_pdf, max_file_size=0)


def test_probe_takes_a_path_or_bytes() -> None:
    with pytest.raises(TypeError, match="path"):
        pylopdf.probe(None)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="read the file object first"):
        pylopdf.probe(io.BytesIO(b"%PDF"))  # ty: ignore[invalid-argument-type]


def test_probe_reports_metadata_by_pylopdf_names() -> None:
    data = pylopdf.Document()
    data.pages.new()
    data.metadata.update(title="Report", author="Ann", created="D:20260101000000Z")

    probe = pylopdf.probe(data.to_bytes())

    assert probe.metadata == {"title": "Report", "author": "Ann", "created": "D:20260101000000Z"}
    assert probe == pylopdf.Probe(
        page_count=1, version=probe.version, needs_password=False, is_repaired=False, metadata=probe.metadata
    )
    with pytest.raises(TypeError):
        probe.metadata["title"] = "Changed"  # ty: ignore[invalid-assignment]


def test_context_manager_closes(one_page_pdf: bytes) -> None:
    with pylopdf.open(one_page_pdf) as doc:
        assert len(doc.pages) == 1
    with pytest.raises(ValueError, match="document closed"):
        _ = len(doc.pages)
    with pytest.raises(ValueError, match="document closed"):
        _ = doc.is_repaired


def test_empty_page_lists_reject_closed_document(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    pages = doc.pages
    doc.close()
    with pytest.raises(ValueError, match="document closed"):
        del pages[1:1]
    with pytest.raises(ValueError, match="document closed"):
        pages.select([])
    with pytest.raises(ValueError, match="document closed"):
        _ = doc.pages


def test_closed_document_repr(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    assert repr(doc) == "<pylopdf.Document>"
    doc.close()
    assert repr(doc) == "<closed pylopdf.Document>"


def test_unicode_metadata(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    doc.metadata.update(title="日本語タイトル", author="山田 太郎")
    reloaded = pylopdf.open(doc.to_bytes())
    assert reloaded.metadata["title"] == "日本語タイトル"
    assert reloaded.metadata["author"] == "山田 太郎"


def _png_size(data: bytes) -> tuple[int, int]:
    """Read ``(width, height)`` from a PNG IHDR chunk."""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def test_render_page_png(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    data = doc.pages[0].pixmap(alpha=True).to_bytes()
    width, height = _png_size(data)
    # Fixture MediaBox is 612×792 Letter at 72 dpi.
    assert (width, height) == (612, 792)


def test_render_page_png_bounds_encoded_output(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    expected = doc.pages[0].pixmap(alpha=True).to_bytes(max_size=None)

    assert doc.pages[0].pixmap(alpha=True).to_bytes(max_size=len(expected)) == expected
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.pages[0].pixmap(alpha=True).to_bytes(max_size=len(expected) - 1)
    assert caught.value.code == "pixmap_output_size"


@pytest.mark.parametrize("max_size", [0, -1, True, 1.5])
def test_render_page_png_validates_output_limit(one_page_pdf: bytes, max_size: object) -> None:
    doc = pylopdf.open(one_page_pdf)

    with pytest.raises((TypeError, ValueError), match="max_size"):
        doc.pages[0].pixmap(alpha=True).to_bytes(max_size=max_size)  # ty: ignore[invalid-argument-type]


def test_render_page_png_scale(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    width, height = _png_size(doc.pages[0].pixmap(matrix=pylopdf.Matrix(2.0, 2.0), alpha=True).to_bytes())
    assert (width, height) == (1224, 1584)


def test_render_pages_matches_sequential_and_preserves_order(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    pages = [2, 0, 2, 1]

    expected = [doc.pages[page].pixmap(alpha=True).to_bytes() for page in pages]
    assert doc.render(pages, alpha=True, workers=2) == expected


def test_render_pages_defaults_to_every_page(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    assert doc.render(alpha=True, workers=1) == [
        doc.pages[page].pixmap(alpha=True).to_bytes() for page in range(len(doc.pages))
    ]


def test_render_pages_supports_dpi_background_and_empty_input(
    three_page_pdf: bytes,
) -> None:
    doc = pylopdf.open(three_page_pdf)
    expected = doc.pages[1].pixmap(dpi=144, background=(255, 255, 255), alpha=True).to_bytes()
    assert doc.render([1], dpi=144, background=(255, 255, 255), alpha=True, workers=2) == [expected]
    assert doc.render([], alpha=True, workers=2) == []


@pytest.mark.parametrize("workers", [0, -1, 65])
def test_render_pages_rejects_invalid_worker_count(three_page_pdf: bytes, workers: int) -> None:
    doc = pylopdf.open(three_page_pdf)
    with pytest.raises(ValueError, match="workers"):
        doc.render(alpha=True, workers=workers)


@pytest.mark.parametrize("workers", [True, 1.5])
def test_render_pages_rejects_non_integer_workers(three_page_pdf: bytes, workers: object) -> None:
    doc = pylopdf.open(three_page_pdf)
    with pytest.raises(TypeError, match="workers"):
        doc.render(alpha=True, workers=workers)  # ty: ignore[invalid-argument-type]


def test_render_pages_validates_all_page_numbers_before_rendering(
    three_page_pdf: bytes,
) -> None:
    doc = pylopdf.open(three_page_pdf)
    with pytest.raises(IndexError):
        doc.render([0, 3, 1], alpha=True, workers=2)


def test_render_pages_reflects_structural_edits(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    original_last_page = doc.pages[2].pixmap(alpha=True).to_bytes()

    doc.pages.select([2, 0])

    assert doc.render(alpha=True, workers=2) == [
        original_last_page,
        doc.pages[1].pixmap(alpha=True).to_bytes(),
    ]


def test_render_pages_bounds_page_iterable_before_full_materialization(
    one_page_pdf: bytes,
) -> None:
    doc = pylopdf.open(one_page_pdf)
    consumed = 0

    def pages() -> Iterator[int]:
        nonlocal consumed
        for _ in range(5000):
            consumed += 1
            yield 0

    with pytest.raises(pylopdf.LimitError, match="4096 entries") as caught:
        doc.render(pages(), alpha=True, workers=2)
    assert caught.value.code == "page_entry_count"
    assert consumed == 4097


def test_render_pages_bounds_cumulative_png_output(three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    expected = [doc.pages[0].pixmap(alpha=True).to_bytes(), doc.pages[1].pixmap(alpha=True).to_bytes()]
    total_size = sum(map(len, expected))

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.render([0, 1], alpha=True, workers=2, max_size=total_size - 1)
    assert caught.value.code == "render_output_size"
    assert doc.render([0, 1], alpha=True, workers=2, max_size=total_size) == expected
    assert doc.render([0, 1], alpha=True, workers=2, max_size=None) == expected


@pytest.mark.parametrize("max_size", [True, 1.5, "1024"])
def test_render_pages_rejects_non_integer_size_limits(
    one_page_pdf: bytes,
    max_size: object,
) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(TypeError, match="max_size"):
        doc.render(alpha=True, max_size=max_size)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("max_size", [0, -1])
def test_render_pages_rejects_non_positive_size_limits(
    one_page_pdf: bytes,
    max_size: int,
) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match="max_size"):
        doc.render(alpha=True, max_size=max_size)


def test_render_page_reflects_edits(three_page_pdf: bytes) -> None:
    # Rendering reflects state after deleting a page.
    doc = pylopdf.open(three_page_pdf)
    del doc.pages[0:2]
    assert len(doc.pages) == 1
    assert _png_size(doc.pages[0].pixmap(alpha=True).to_bytes())[0] == 612


def test_render_page_out_of_range(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(IndexError):
        doc.pages[1].pixmap(alpha=True).to_bytes()


@pytest.mark.parametrize("scale", [0.0, -1.0, float("nan"), float("inf")])
def test_render_page_invalid_scale(one_page_pdf: bytes, scale: float) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match=r"finite|matrix must scale"):
        doc.pages[0].pixmap(matrix=(scale, 0, 0, scale, 0, 0), alpha=True).to_bytes()


def test_render_page_too_small_scale(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match="scale"):
        doc.pages[0].pixmap(matrix=pylopdf.Matrix(0.0001, 0.0001), alpha=True).to_bytes()


@pytest.mark.parametrize(
    ("page_size", "message"),
    [((100_000, 100_000), "65535-pixel"), ((9_000, 9_000), "64000000-pixel")],
)
def test_render_page_rejects_oversized_page(page_size: tuple[int, int], message: str) -> None:
    doc = pylopdf.open(build_pdf(["x"], page_size=page_size))
    with pytest.raises(ValueError, match=message):
        doc.pages[0].pixmap(alpha=True).to_bytes()


def test_render_page_svg(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    svg = doc.pages[0].svg(max_size=None)
    assert svg.lstrip().startswith("<")
    assert "svg" in svg[:200]
    exact_size = len(svg.encode())

    assert doc.pages[0].svg(max_size=exact_size) == svg
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.pages[0].svg(max_size=exact_size - 1)
    assert caught.value.code == "svg_output_size"


@pytest.mark.parametrize("max_size", [True, 1.5, "1024"])
def test_render_page_svg_rejects_non_integer_size_limits(
    one_page_pdf: bytes,
    max_size: object,
) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(TypeError, match="max_size"):
        doc.pages[0].svg(max_size=max_size)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("max_size", [0, -1])
def test_render_page_svg_rejects_non_positive_size_limits(
    one_page_pdf: bytes,
    max_size: int,
) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match="max_size"):
        doc.pages[0].svg(max_size=max_size)


def test_render_page_cache_reflects_edits(three_page_pdf: bytes) -> None:
    """The render cache remains deterministic and reflects edits."""
    doc = pylopdf.open(three_page_pdf)
    assert (
        doc.pages[0].pixmap(alpha=True).to_bytes() == doc.pages[0].pixmap(alpha=True).to_bytes()
    )  # Consecutive renders match.
    page_two = doc.pages[1].pixmap(alpha=True).to_bytes()
    del doc.pages[0]
    # New page 0 renders like old page 1 after deletion.
    assert doc.pages[0].pixmap(alpha=True).to_bytes() == page_two


def test_render_page_dpi(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    assert (
        doc.pages[0].pixmap(dpi=144, alpha=True).to_bytes()
        == doc.pages[0].pixmap(matrix=pylopdf.Matrix(2.0, 2.0), alpha=True).to_bytes()
    )


def test_render_page_refuses_dpi_with_matrix(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match="dpi or matrix, not both"):
        doc.pages[0].pixmap(matrix=pylopdf.Matrix(3.0, 3.0), dpi=144)


def test_render_page_background(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    transparent = doc.pages[0].pixmap(alpha=True).to_bytes()
    white = doc.pages[0].pixmap(background=(255, 255, 255), alpha=True).to_bytes()
    assert white.startswith(b"\x89PNG\r\n\x1a\n")
    assert white != transparent
    # RGB and opaque RGBA backgrounds produce the same result.
    assert white == doc.pages[0].pixmap(background=(255, 255, 255, 255), alpha=True).to_bytes()


@pytest.mark.parametrize("background", [(0, 0, 256), (0, 0, -1)])
def test_render_page_background_out_of_range(one_page_pdf: bytes, background: tuple[int, int, int]) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match="background"):
        doc.pages[0].pixmap(background=background, alpha=True).to_bytes()


def test_render_page_background_wrong_length(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    with pytest.raises(ValueError, match="background"):
        doc.pages[0].pixmap(background=(255, 255), alpha=True).to_bytes()  # ty: ignore[invalid-argument-type]


def test_save_options_roundtrip(tmp_path: Path, three_page_pdf: bytes) -> None:
    doc = pylopdf.open(three_page_pdf)
    out = tmp_path / "optimized.pdf"
    doc.save(out, object_streams=True)
    reopened = pylopdf.open(out)
    assert len(reopened.pages) == 3
    assert "Page two" in reopened.pages[1].text()
    # Object streams require PDF 1.5+, so the version is raised.
    assert reopened.version == "1.5"


def test_to_bytes_with_object_streams_renders_consistently(three_page_pdf: bytes) -> None:
    """Rendering survives document mutation during object-stream saving."""
    doc = pylopdf.open(three_page_pdf)
    before = doc.pages[0].pixmap(alpha=True).to_bytes()
    data = doc.to_bytes(object_streams=True)
    assert doc.pages[0].pixmap(alpha=True).to_bytes() == before
    assert pylopdf.open(data).pages[0].pixmap(alpha=True).to_bytes() == before


def test_multi_document_merge() -> None:
    # Merge three PDFs in order.
    merged = pylopdf.Document()
    for text in ["First", "Second", "Third"]:
        merged.pages.extend(pylopdf.open(build_pdf([text])).pages)
    assert len(merged.pages) == 3
    reloaded = pylopdf.open(merged.to_bytes())
    assert "Second" in reloaded.pages[1].text()


def test_inherited_page_parent_cycle_does_not_hang(one_page_pdf: bytes) -> None:
    """A damaged cyclic page Parent does not hang processing.

    Hayro extraction completes despite the cycle, though text may be empty when
    Resources is unreachable. pylopdf's own inheritance paths, such as Page
    mediabox, retain cycle detection and raise an explicit error.
    """
    raw = one_page_pdf.replace(b"/Parent 2 0 R", b"/Parent 4 0 R")
    doc = pylopdf.open(raw)
    assert len(doc.pages) == 1
    assert isinstance(doc.pages[0].text(), str)
    with pytest.raises(ValueError, match="reference cycle"):
        _ = doc.pages[0].mediabox
