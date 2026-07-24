"""Regression tests over real-world PDFs.

Run open, metadata, extraction, editing, saving, and rendering over every PDF
produced by real toolchains in ``tests/assets/real_world``. This catches lopdf
and hayro limitations early. The adjacent README records sources and licenses.
"""

from __future__ import annotations

import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


@dataclass(frozen=True)
class Case:
    """Expected values for one corpus file."""

    name: str
    pages: int
    version: str
    #: Text expected on page 0; None means tracked separately as a known limit.
    snippet: str | None


CASES = [
    Case("f1040.pdf", pages=2, version="PDF 1.7", snippet="U.S. Individual Income Tax Return"),
    Case("pdf20-simple.pdf", pages=1, version="PDF 2.0", snippet="Hello World"),
    Case("usrguide.pdf", pages=27, version="PDF 1.5", snippet="for authors"),
    Case("bill-hr815.pdf", pages=110, version="PDF 1.5", snippet="One Hundred Eighteenth Congress"),
    Case("mhlw-doc.pdf", pages=2, version="PDF 1.7", snippet="裁判例"),
    Case("patent-us223898.pdf", pages=4, version="PDF 1.3", snippet="Electric-Lamp"),
    Case("wdl6812-manuscript.pdf", pages=2, version="PDF 1.4", snippet=None),
]

ALL = pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
WITH_TEXT = pytest.mark.parametrize("case", [c for c in CASES if c.snippet is not None], ids=lambda c: c.name)


@ALL
def test_open_from_path_and_stream(case: Case) -> None:
    path = ASSETS / case.name
    assert pylopdf.open(path).page_count == case.pages
    assert pylopdf.open(stream=path.read_bytes()).page_count == case.pages


@ALL
def test_metadata_format(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    assert doc.metadata["format"] == case.version


@ALL
def test_peek_metadata_matches_full_load(case: Case) -> None:
    """peek_metadata returns the same page count as a full load."""
    meta = pylopdf.peek_metadata(ASSETS / case.name)
    assert meta["page_count"] == case.pages
    assert meta["encrypted"] is False


def test_max_decompressed_size_guards_against_bombs() -> None:
    """A tiny decompression limit rejects PDFs containing object streams."""
    path = ASSETS / "f1040.pdf"
    with pytest.raises(pylopdf.PdfError, match="limit"):
        pylopdf.open(path, max_decompressed_size=100)
    assert pylopdf.open(path, max_decompressed_size=50_000_000).page_count == 2


def test_recovered_pdf_avoids_slow_initial_reserialization() -> None:
    """Use original bytes before normalizing a damaged but readable PDF.

    This five-byte f1040 mutation is a minimized Atheris slow unit. Serializing
    the recovered lopdf object graph used to take about 9 seconds before hayro
    could start, while hayro can parse the original bytes directly.
    """
    data = bytearray((ASSETS / "f1040.pdf").read_bytes())
    data[29_909] = 244
    data[186_564:186_568] = bytes(4)

    start = time.perf_counter()
    text = pylopdf.open(
        stream=bytes(data),
        max_decompressed_size=16 * 1024 * 1024,
    ).get_page_text(0)
    elapsed = time.perf_counter() - start

    assert "U.S. Individual Income Tax Return" in text
    assert elapsed < 5.0, f"initial extraction took {elapsed:.2f}s"


@pytest.mark.parametrize("filter_name", ["FlateDecode", "Fl"])
def test_max_decompressed_size_guards_page_content_streams(filter_name: str) -> None:
    """Load-time limits cover page Contents that hayro decodes lazily."""
    expanded = b" " * 200_000
    compressed = zlib.compress(expanded)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
            4: (
                f"<< /Length {len(compressed)} /Filter /{filter_name} >>\nstream\n".encode()
                + compressed
                + b"\nendstream"
            ),
        }
    )
    with pytest.raises(pylopdf.PdfError, match="100-byte limit"):
        pylopdf.open(stream=pdf, max_decompressed_size=100)
    doc = pylopdf.open(stream=pdf, max_decompressed_size=len(expanded))
    assert doc.get_page_text(0) == ""


@WITH_TEXT
def test_extract_text_page0(case: Case) -> None:
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    assert case.snippet in doc.get_page_text(0)


def test_pdf20_comment_streams_extract() -> None:
    """Protect extraction from comment-plus-indentation regression lopdf#535.

    v0.7 fixed pylopdf by moving extraction to hayro. lopdf ``extract_text``
    remains affected, but pylopdf no longer uses it.
    """
    doc = pylopdf.open(ASSETS / "pdf20-simple.pdf")
    assert "Hello World" in doc.get_page_text(0)


def test_f1040_metadata_title() -> None:
    doc = pylopdf.open(ASSETS / "f1040.pdf")
    assert doc.metadata["title"] == "2025 Form 1040"


def test_f1040_bordered_table() -> None:
    """Extract a real stroked dependency grid without rasterization."""
    tables = pylopdf.open(ASSETS / "f1040.pdf")[0].find_tables()
    assert len(tables) >= 1
    table = tables[0]
    assert (table.row_count, table.col_count) == (2, 7)
    text = "\n".join(cell for row in table.extract() for cell in row if cell is not None)
    assert "Full-time\nstudent" in text
    assert "Child tax\ncredit" in text


def test_f1040_borderless_text_table() -> None:
    """Extract aligned form rows that have no complete surrounding grid."""
    tables = pylopdf.open(ASSETS / "f1040.pdf")[0].find_tables(strategy="text")
    extracted = [table.extract() for table in tables]

    assert any(any(cell == "Filing Status" for row in table for cell in row) for table in extracted)


def test_manuscript_scan_has_no_text_layer() -> None:
    """A pure scan without a text layer correctly extracts as empty."""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    assert doc.get_page_text(0).strip() == ""


@ALL
def test_select_first_page_and_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.select([0])
    assert doc.page_count == 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == 1


@ALL
def test_insert_subset_with_position_roundtrip(case: Case) -> None:
    """Importing page 0 at the front survives pruning without content loss."""
    doc = pylopdf.open(ASSETS / case.name)
    src = pylopdf.open(ASSETS / case.name)
    doc.insert_pdf(src, from_page=0, to_page=0, start_at=0)
    assert doc.page_count == case.pages + 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages + 1
    # Pruning preserves fonts/images referenced by the inserted page.
    assert reopened.render_page(0).startswith(b"\x89PNG")


@ALL
def test_merge_self_and_roundtrip(case: Case) -> None:
    raw = (ASSETS / case.name).read_bytes()
    doc = pylopdf.open(stream=raw)
    doc.insert_pdf(pylopdf.open(stream=raw))
    assert doc.page_count == case.pages * 2
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages * 2


@ALL
def test_merge_into_empty_and_roundtrip(case: Case) -> None:
    """Importing a real PDF into an empty document avoids Catalog/Pages ID collisions."""
    source = pylopdf.open(ASSETS / case.name)
    doc = pylopdf.Document()
    doc.insert_pdf(source)
    assert doc.page_count == case.pages
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages


@ALL
def test_delete_page_and_roundtrip(case: Case) -> None:
    if case.pages < 2:
        pytest.skip("deleting every page from a one-page document is out of scope")
    doc = pylopdf.open(ASSETS / case.name)
    doc.delete_page(0)
    assert doc.page_count == case.pages - 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages - 1


@ALL
def test_save_optimized_roundtrip(case: Case) -> None:
    """Garbage, deflate, and object-stream saves preserve readable content."""
    doc = pylopdf.open(ASSETS / case.name)
    data = doc.tobytes(garbage=True, deflate=True, object_streams=True)
    reopened = pylopdf.open(stream=data)
    assert reopened.page_count == case.pages


def test_object_streams_reduce_size() -> None:
    """Object-stream saving reduces a medium-size document."""
    doc = pylopdf.open(ASSETS / "bill-hr815.pdf")
    plain = doc.tobytes()
    optimized = doc.tobytes(garbage=True, deflate=True, object_streams=True)
    assert len(optimized) < len(plain)


@ALL
def test_set_metadata_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.set_metadata({"title": "回帰テスト", "author": "pylopdf"})
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.metadata["title"] == "回帰テスト"
    assert reopened.metadata["author"] == "pylopdf"


@ALL
def test_render_page_png(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    png = doc.render_page(0, scale=1.0)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 1000


@ALL
def test_render_page_svg(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    svg = doc.render_page_svg(0)
    assert svg.startswith("<svg")


@WITH_TEXT
def test_extract_text_survives_edit(case: Case) -> None:
    """Text extraction survives select with inherited attributes materialized."""
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    doc.select([0])
    reopened = pylopdf.open(stream=doc.tobytes())
    assert case.snippet in reopened.get_page_text(0)
