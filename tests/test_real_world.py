"""Regression tests over real-world and independent interoperability PDFs.

Run open, metadata, extraction, editing, saving, and rendering over every PDF
in ``tests/assets/real_world``. This catches lopdf and hayro limitations early.
The adjacent README records sources and licenses.
"""

from __future__ import annotations

import time
import warnings
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
    Case("pdfium-type3.pdf", pages=1, version="PDF 1.7", snippet=None),
    Case("pdfium-smask-blend.pdf", pages=1, version="PDF 1.7", snippet=None),
    Case("pdfium-jpx-lzw.pdf", pages=1, version="PDF 1.7", snippet=None),
    Case(
        "pdfium-links-highlights-annots.pdf",
        pages=1,
        version="PDF 1.7",
        snippet="Link Annotations",
    ),
]

ALL = pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
WITH_TEXT = pytest.mark.parametrize("case", [c for c in CASES if c.snippet is not None], ids=lambda c: c.name)


def _with_incorrect_startxref(data: bytes) -> bytes:
    """Replace the final classic startxref value without changing its table."""
    eof = data.rfind(b"%%EOF")
    startxref = data.rfind(b"startxref", 0, eof)
    assert startxref >= 0
    number_start = startxref + len(b"startxref")
    while data[number_start] in b"\x00\t\n\x0c\r ":
        number_start += 1
    number_end = number_start
    while data[number_end : number_end + 1].isdigit():
        number_end += 1
    assert number_end > number_start
    return data[:number_start] + b"0" + data[number_end:]


def _rgba_pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int, int]:
    """Return one rendered pixel over a white background."""
    pixmap = page.get_pixmap(background=(255, 255, 255))
    samples = pixmap.samples
    offset = (y * pixmap.width + x) * 4
    return (
        samples[offset],
        samples[offset + 1],
        samples[offset + 2],
        samples[offset + 3],
    )


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
    assert meta["repaired"] is False


def test_incorrect_classic_startxref_is_repaired_atomically() -> None:
    """Recover a real PDF 2.0 file only when its intact classic xref retries."""
    damaged = _with_incorrect_startxref((ASSETS / "pdf20-simple.pdf").read_bytes())

    with pytest.warns(pylopdf.PylopdfWarning, match="incorrect startxref"):
        doc = pylopdf.open(stream=damaged)

    assert doc.is_repaired is True
    assert doc.page_count == 1
    assert "Hello World" in doc.get_page_text(0)

    saved = doc.tobytes()
    with warnings.catch_warnings(record=True) as caught:
        reopened = pylopdf.open(stream=saved)
    assert not caught
    assert reopened.is_repaired is False
    assert "Hello World" in reopened.get_page_text(0)


def test_incorrect_startxref_path_probe_and_prefixed_stream(tmp_path: Path) -> None:
    """Expose repair in probes and keep startxref relative to the PDF header."""
    damaged = _with_incorrect_startxref((ASSETS / "pdf20-simple.pdf").read_bytes())
    path = tmp_path / "incorrect-startxref.pdf"
    path.write_bytes(damaged)

    with pytest.warns(pylopdf.PylopdfWarning, match="incorrect startxref"):
        metadata = pylopdf.peek_metadata(path)
    assert metadata["page_count"] == 1
    assert metadata["repaired"] is True

    with pytest.warns(pylopdf.PylopdfWarning, match="incorrect startxref"):
        prefixed = pylopdf.open(stream=b"transport prefix\n" + damaged)
    assert prefixed.is_repaired is True
    assert "Hello World" in prefixed.get_page_text(0)


def test_xref_stream_is_not_guessed_during_startxref_recovery() -> None:
    """Keep the fallback bounded to intact classic xref tables."""
    damaged = _with_incorrect_startxref((ASSETS / "f1040.pdf").read_bytes())
    with pytest.raises(pylopdf.PdfError):
        pylopdf.open(stream=damaged)


def test_previous_classic_xref_is_not_used_for_later_revision() -> None:
    """Never discard a later xref-stream revision by opening its predecessor."""
    base = (ASSETS / "pdf20-simple.pdf").read_bytes()
    later_revision = (
        base
        + b"\n999 0 obj\n"
        + b"<< /Type /XRef /Length 0 >>\nstream\n\nendstream\nendobj\n"
        + b"startxref\n0\n%%EOF\n"
    )

    with pytest.raises(pylopdf.PdfError):
        pylopdf.open(stream=later_revision)


def test_truncated_classic_xref_is_rejected() -> None:
    """Do not treat a stream cut inside the final xref table as a document."""
    raw = (ASSETS / "pdf20-simple.pdf").read_bytes()
    startxref = raw.rfind(b"startxref")
    xref = raw.rfind(b"xref\r\n", 0, startxref)
    entry = raw.find(b"0000000096 00000 n", xref, startxref)
    assert xref >= 0
    assert entry >= 0

    with pytest.raises(pylopdf.PdfError):
        pylopdf.open(stream=raw[: entry + 8])


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


def test_pdfium_type3_svg_retains_stencil_glyphs() -> None:
    """Exercise the independent Type 3 glyph program through the SVG device."""
    svg = pylopdf.open(ASSETS / "pdfium-type3.pdf").render_page_svg(0)

    assert '<defs id="type3-glyph">' in svg
    assert svg.count("<image ") == 3


def test_pdfium_type3_stencil_glyphs_render_to_raster() -> None:
    """Expose the current platform-dependent Type 3 raster behavior."""
    page = pylopdf.open(ASSETS / "pdfium-type3.pdf")[0]
    pixmap = page.get_pixmap(background=(255, 255, 255))
    samples = pixmap.samples
    has_ink = any(samples[offset : offset + 3] != b"\xff\xff\xff" for offset in range(0, len(samples), 4))

    if not has_ink:
        pytest.xfail("hayro 0.7.1 omits these Type 3 stencil glyphs on this renderer target")


def test_pdfium_jpx_inside_lzw_decodes_as_red_image() -> None:
    """Decode an ASCIIHex/LZW/JPX image chain through both image APIs."""
    page = pylopdf.open(ASSETS / "pdfium-jpx-lzw.pdf")[0]
    images = page.get_images()

    assert [(image["width"], image["height"], image["ext"]) for image in images] == [(612, 792, "png")]
    assert images[0]["image"].startswith(b"\x89PNG\r\n\x1a\n")
    assert _rgba_pixel(page, 300, 300) == (255, 0, 0, 255)


def test_pdfium_soft_mask_and_blend_render_expected_region() -> None:
    """Apply a form soft mask and Screen blend without losing its opacity."""
    page = pylopdf.open(ASSETS / "pdfium-smask-blend.pdf")[0]

    assert _rgba_pixel(page, 75, 150) == (102, 102, 102, 255)
    assert _rgba_pixel(page, 25, 150) == (255, 255, 255, 255)
    assert _rgba_pixel(page, 75, 50) == (255, 255, 255, 255)


def test_pdfium_existing_annotations_and_external_link_are_read() -> None:
    """Read independent Widget, Link, and Highlight dictionaries in source order."""
    page = pylopdf.open(ASSETS / "pdfium-links-highlights-annots.pdf")[0]
    annotations = page.annots()

    assert [annotation["type"] for annotation in annotations] == [
        "Widget",
        "Widget",
        "Widget",
        "Widget",
        "Link",
        "Highlight",
    ]
    assert annotations[4]["uri"] == "https://www.google.com/"
    assert page.get_links() == [
        {
            "kind": 2,
            "from": pylopdf.Rect(69.0, 139.0, 542.0, 159.0),
            "uri": "https://www.google.com/",
        }
    ]


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
