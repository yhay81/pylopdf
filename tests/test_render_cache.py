"""Retained hayro render-cache reuse across render calls on one document."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import build_nonembedded_cjk_pdf, build_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"
SANS = Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"


def _text_pdf(page_texts: list[str]) -> bytes:
    """Build pages tall enough to contain the builder's text at y=720."""
    return build_pdf(page_texts, page_size=(320, 760))


def _fresh_samples(data: bytes, pno: int, scale: float = 1.0) -> bytes:
    """Rasterize one page of a newly opened document."""
    with pylopdf.open(stream=data) as doc:
        return doc[pno].get_pixmap(scale=scale).samples


@pytest.mark.parametrize(
    "name",
    [
        "usrguide.pdf",
        "f1040.pdf",
        "nics-background-checks-2015-11.pdf",
        "bunka-kokugo-series-019-p4.pdf",
        "pdfium-type3.pdf",
        "pdfium-smask-blend.pdf",
    ],
)
def test_repeated_renders_match_fresh_documents(name: str) -> None:
    """Reusing parsed fonts and glyph outlines must not change any pixel."""
    data = (ASSETS / name).read_bytes()
    doc = pylopdf.open(stream=data)
    pages = list(range(min(3, doc.page_count)))
    expected = [_fresh_samples(data, pno, 0.5) for pno in pages]

    for _ in range(2):
        assert [doc[pno].get_pixmap(scale=0.5).samples for pno in pages] == expected
    with pylopdf.open(stream=data) as fresh:
        expected_png = [fresh.render_page(pno, scale=0.5) for pno in pages]
    assert [doc.render_page(pno, scale=0.5) for pno in pages] == expected_png
    assert doc.render_pages(pages, scale=0.5, workers=1) == expected_png
    assert doc.render_pages(pages, scale=0.5, workers=2) == expected_png


def test_edits_after_retained_renders_are_visible() -> None:
    data = _text_pdf(["One", "Two"])
    doc = pylopdf.open(stream=data)
    before = doc[0].get_pixmap().samples
    assert doc[0].get_pixmap().samples == before

    doc[0].insert_text((20, 100), "Edited", fontsize=24)
    after = doc[0].get_pixmap().samples

    assert after != before
    assert after == _fresh_samples(doc.tobytes(), 0)
    assert doc.render_pages([0], workers=1) == [doc.render_page(0)]


def test_structural_edits_after_retained_renders_are_visible() -> None:
    data = _text_pdf(["First", "Second", "Third"])
    doc = pylopdf.open(stream=data)
    rendered = [doc[pno].get_pixmap().samples for pno in range(doc.page_count)]

    doc.delete_pages([0])

    assert [doc[pno].get_pixmap().samples for pno in range(doc.page_count)] == rendered[1:]


def test_fallback_font_changes_reset_retained_fonts() -> None:
    """Cached hayro fonts depend on fallback configuration, not only the snapshot."""
    data = build_nonembedded_cjk_pdf()
    doc = pylopdf.open(stream=data)
    doc.set_fallback_font(None)
    blank = doc.render_page(0)
    assert doc.render_page(0) == blank

    doc.set_fallback_font(SANS)
    with_font = doc.render_page(0)
    fresh = pylopdf.open(stream=data)
    fresh.set_fallback_font(SANS)

    assert with_font != blank
    assert with_font == fresh.render_page(0)

    doc.set_fallback_font(None)
    assert doc.render_page(0) == blank


def test_alternating_documents_on_one_thread() -> None:
    first = pylopdf.open(stream=_text_pdf(["Alpha"]))
    second = pylopdf.open(stream=_text_pdf(["Beta"]))
    expected_first = first[0].get_pixmap().samples
    expected_second = second[0].get_pixmap().samples
    assert expected_first != expected_second

    for _ in range(2):
        assert first[0].get_pixmap().samples == expected_first
        assert second[0].get_pixmap().samples == expected_second
    del first
    assert second[0].get_pixmap().samples == expected_second


def test_other_threads_render_and_edit_consistently() -> None:
    """A cache retained on the creating thread must not survive edits elsewhere."""
    doc = pylopdf.open(stream=_text_pdf(["One"]))
    retained = doc[0].get_pixmap().samples

    def render_then_edit() -> tuple[bytes, bytes]:
        rendered = doc[0].get_pixmap().samples
        doc[0].insert_text((20, 100), "Edited elsewhere", fontsize=18)
        return rendered, doc[0].get_pixmap().samples

    with ThreadPoolExecutor(max_workers=1) as executor:
        rendered, edited_elsewhere = executor.submit(render_then_edit).result()

    assert rendered == retained
    edited = doc[0].get_pixmap().samples
    assert edited != retained
    assert edited == edited_elsewhere == _fresh_samples(doc.tobytes(), 0)


def test_documents_created_on_other_threads_render_correctly() -> None:
    data = _text_pdf(["Created elsewhere"])
    with ThreadPoolExecutor(max_workers=1) as executor:
        doc = executor.submit(pylopdf.open, stream=data).result()

    expected = _fresh_samples(data, 0)
    assert doc[0].get_pixmap().samples == expected
    assert doc[0].get_pixmap().samples == expected


def test_renders_beyond_the_retained_page_bound() -> None:
    """Long single-page loops and batches keep matching fresh rasters."""
    data = _text_pdf([f"Page {index}" for index in range(70)])
    with pylopdf.open(stream=data) as reference:
        expected = reference.render_pages(scale=0.25, workers=2)
    assert len(set(expected)) == len(expected)

    doc = pylopdf.open(stream=data)
    for _ in range(2):
        assert [doc.render_page(pno, scale=0.25) for pno in range(doc.page_count)] == expected
    assert doc.render_pages(scale=0.25, workers=1) == expected
    assert doc.render_page(3, scale=0.25) == expected[3]


def test_closing_after_retained_renders() -> None:
    data = _text_pdf(["Closed"])
    doc = pylopdf.open(stream=data)
    expected = doc[0].get_pixmap().samples

    doc.close()
    doc.close()

    with pytest.raises(pylopdf.DocumentClosedError):
        doc.render_page(0)
    assert _fresh_samples(data, 0) == expected
