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
    Case("bunka-kokugo-series-019-p4.pdf", pages=1, version="PDF 1.7", snippet=None),
    Case("patent-us223898.pdf", pages=4, version="PDF 1.3", snippet="Electric-Lamp"),
    Case("wdl6812-manuscript.pdf", pages=2, version="PDF 1.4", snippet=None),
    Case(
        "nics-background-checks-2015-11.pdf",
        pages=1,
        version="PDF 1.3",
        snippet="NICS Firearm Background Checks",
    ),
    Case("senate-expenditures.pdf", pages=1, version="PDF 1.3", snippet="BAIN, J MATTHEW"),
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


@ALL
def test_extract_drawings_page0(case: Case) -> None:
    """Interpret real vector paths without returning malformed commands."""
    drawings = pylopdf.open(ASSETS / case.name)[0].get_drawings()
    assert all(drawing["type"] in {"f", "s", "fs"} for drawing in drawings)
    assert all(item[0] in {"l", "c"} for drawing in drawings for item in drawing["items"])


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


def test_f1040_drawing_extraction_retains_form_paths() -> None:
    """Expose the dense vector structure underlying a real tax form."""
    drawings = pylopdf.open(ASSETS / "f1040.pdf")[0].get_drawings()
    assert len(drawings) >= 400
    assert any(drawing["type"] == "fs" for drawing in drawings)


def test_f1040_markdown_integrates_bordered_table_once() -> None:
    """Place a real stroked dependency grid into page Markdown."""
    page = pylopdf.open(ASSETS / "f1040.pdf")[0]
    markdown = page.to_markdown()

    assert "| --- | --- | --- | --- | --- | --- | --- |" in markdown
    assert "Full-time<br>student" in markdown
    assert "Child tax<br>credit" in markdown
    assert "(6) Check if" in markdown
    assert "(7) Credits" in markdown
    assert markdown.count("Full-time") == page.get_text().count("Full-time")
    assert markdown.count("Child tax") == page.get_text().count("Child tax")


def test_f1040_borderless_text_table() -> None:
    """Extract aligned form rows that have no complete surrounding grid."""
    tables = pylopdf.open(ASSETS / "f1040.pdf")[0].find_tables(strategy="text")
    extracted = [table.extract() for table in tables]

    assert any(any(cell == "Filing Status" for row in table for cell in row) for table in extracted)


def test_nics_sparse_grid_infers_individual_rows() -> None:
    """Split dense records inside coarse vector row spans."""
    table = pylopdf.open(ASSETS / "nics-background-checks-2015-11.pdf")[0].find_tables()[0]
    rows = table.extract()

    assert (table.row_count, table.col_count) == (58, 25)
    assert table.confidence == 0.95
    assert rows[2] == [
        "Alabama",
        "18,870",
        "23,022",
        "22,650",
        "859",
        "1,178",
        "0",
        "14",
        "15",
        "0",
        "2,179",
        "2,307",
        "11",
        "0",
        "0",
        "0",
        "",
        "",
        "13",
        "14",
        "0",
        "3",
        "2",
        "0",
        "71,137",
    ]
    assert rows[56][0] == "Wyoming"
    assert rows[56][-1] == "5,017"
    assert rows[57][0] == "Totals"
    assert rows[57][-1] == "2,236,457"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_nics_markdown_preserves_sparse_grid_reading_order(rotation: int) -> None:
    """Expand merged headers without duplicating adjacent data after rotation."""
    document = pylopdf.open(ASSETS / "nics-background-checks-2015-11.pdf")
    document[0].set_rotation(rotation)
    table = document[0].find_tables()[0]

    expected_shape = (58, 25) if rotation in (0, 180) else (25, 58)
    assert (table.row_count, table.col_count) == expected_shape

    markdown = document.to_markdown()
    assert "| Alabama | 18,870 | 23,022 |" in markdown
    assert "| Wyoming | 383 | 1,745 |" in markdown
    assert markdown.count("Alabama") == 1
    assert markdown.count("Wyoming") == 1


def test_rotated_senate_header_and_borderless_body() -> None:
    """Keep a merged vector header separate from aligned expenditure rows."""
    page = pylopdf.open(ASSETS / "senate-expenditures.pdf")[0]

    header = page.find_tables()[0]
    assert (header.row_count, header.col_count) == (2, 7)
    assert header.confidence == 1.0

    body = page.find_tables(strategy="text")[0]
    assert (body.row_count, body.col_count) == (10, 3)
    assert body.extract()[0] == ["BAIN, J MATTHEW", "DISTRICT DIRECTOR", "37,499.96"]


def test_manuscript_scan_has_no_text_layer() -> None:
    """A pure scan without a text layer correctly extracts as empty."""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    assert doc.get_page_text(0).strip() == ""


def test_manuscript_compression_preserves_mismatched_masks() -> None:
    """Skip real JPEG/mask pairs rather than changing their compositing."""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    before = doc[0].get_pixmap().samples

    result = doc.compress_images(dpi=100, quality=50)

    assert result["considered"] >= 2
    assert result["rewritten"] == 0
    assert doc[0].get_pixmap().samples == before


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
