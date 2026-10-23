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
    with pylopdf.open(data) as doc:
        return bytes(doc.pages[pno].pixmap(matrix=pylopdf.Matrix(scale, scale), alpha=True))


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
    doc = pylopdf.open(data)
    pages = list(range(min(3, len(doc.pages))))
    expected = [_fresh_samples(data, pno, 0.5) for pno in pages]

    for _ in range(2):
        assert [bytes(doc.pages[pno].pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True)) for pno in pages] == expected
    with pylopdf.open(data) as fresh:
        expected_png = [
            fresh.pages[pno].pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True).to_bytes() for pno in pages
        ]
    assert [
        doc.pages[pno].pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True).to_bytes() for pno in pages
    ] == expected_png
    assert doc.render(pages, matrix=pylopdf.Matrix(0.5, 0.5), alpha=True, workers=1) == expected_png
    assert doc.render(pages, matrix=pylopdf.Matrix(0.5, 0.5), alpha=True, workers=2) == expected_png


def test_edits_after_retained_renders_are_visible() -> None:
    data = _text_pdf(["One", "Two"])
    doc = pylopdf.open(data)
    before = bytes(doc.pages[0].pixmap(alpha=True))
    assert bytes(doc.pages[0].pixmap(alpha=True)) == before

    doc.pages[0].insert_text((20, 100), "Edited", fontsize=24)
    after = bytes(doc.pages[0].pixmap(alpha=True))

    assert after != before
    assert after == _fresh_samples(doc.to_bytes(), 0)
    assert doc.render([0], alpha=True, workers=1) == [doc.pages[0].pixmap(alpha=True).to_bytes()]


def test_structural_edits_after_retained_renders_are_visible() -> None:
    data = _text_pdf(["First", "Second", "Third"])
    doc = pylopdf.open(data)
    rendered = [bytes(doc.pages[pno].pixmap(alpha=True)) for pno in range(len(doc.pages))]

    del doc.pages[0:1]

    assert [bytes(doc.pages[pno].pixmap(alpha=True)) for pno in range(len(doc.pages))] == rendered[1:]


def test_fallback_font_changes_reset_retained_fonts() -> None:
    """Cached hayro fonts depend on fallback configuration, not only the snapshot."""
    data = build_nonembedded_cjk_pdf()
    doc = pylopdf.open(data)
    doc.fallback_fonts = {}
    blank = doc.pages[0].pixmap(alpha=True).to_bytes()
    assert doc.pages[0].pixmap(alpha=True).to_bytes() == blank

    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(pylopdf.Font.from_file(SANS))}
    with_font = doc.pages[0].pixmap(alpha=True).to_bytes()
    fresh = pylopdf.open(data)
    fresh.fallback_fonts = {"ja": pylopdf.FallbackFont(pylopdf.Font.from_file(SANS))}

    assert with_font != blank
    assert with_font == fresh.pages[0].pixmap(alpha=True).to_bytes()

    doc.fallback_fonts = {}
    assert doc.pages[0].pixmap(alpha=True).to_bytes() == blank


def test_alternating_documents_on_one_thread() -> None:
    first = pylopdf.open(_text_pdf(["Alpha"]))
    second = pylopdf.open(_text_pdf(["Beta"]))
    expected_first = bytes(first.pages[0].pixmap(alpha=True))
    expected_second = bytes(second.pages[0].pixmap(alpha=True))
    assert expected_first != expected_second

    for _ in range(2):
        assert bytes(first.pages[0].pixmap(alpha=True)) == expected_first
        assert bytes(second.pages[0].pixmap(alpha=True)) == expected_second
    del first
    assert bytes(second.pages[0].pixmap(alpha=True)) == expected_second


def test_other_threads_render_and_edit_consistently() -> None:
    """A cache retained on the creating thread must not survive edits elsewhere."""
    doc = pylopdf.open(_text_pdf(["One"]))
    retained = bytes(doc.pages[0].pixmap(alpha=True))

    def render_then_edit() -> tuple[bytes, bytes]:
        rendered = bytes(doc.pages[0].pixmap(alpha=True))
        doc.pages[0].insert_text((20, 100), "Edited elsewhere", fontsize=18)
        return rendered, bytes(doc.pages[0].pixmap(alpha=True))

    with ThreadPoolExecutor(max_workers=1) as executor:
        rendered, edited_elsewhere = executor.submit(render_then_edit).result()

    assert rendered == retained
    edited = bytes(doc.pages[0].pixmap(alpha=True))
    assert edited != retained
    assert edited == edited_elsewhere == _fresh_samples(doc.to_bytes(), 0)


def test_documents_created_on_other_threads_render_correctly() -> None:
    data = _text_pdf(["Created elsewhere"])
    with ThreadPoolExecutor(max_workers=1) as executor:
        doc = executor.submit(pylopdf.open, data).result()

    expected = _fresh_samples(data, 0)
    assert bytes(doc.pages[0].pixmap(alpha=True)) == expected
    assert bytes(doc.pages[0].pixmap(alpha=True)) == expected


def test_renders_beyond_the_retained_page_bound() -> None:
    """Long single-page loops and batches keep matching fresh rasters."""
    data = _text_pdf([f"Page {index}" for index in range(70)])
    with pylopdf.open(data) as reference:
        expected = reference.render(matrix=pylopdf.Matrix(0.25, 0.25), alpha=True, workers=2)
    assert len(set(expected)) == len(expected)

    doc = pylopdf.open(data)
    for _ in range(2):
        assert [
            doc.pages[pno].pixmap(matrix=pylopdf.Matrix(0.25, 0.25), alpha=True).to_bytes()
            for pno in range(len(doc.pages))
        ] == expected
    assert doc.render(matrix=pylopdf.Matrix(0.25, 0.25), alpha=True, workers=1) == expected
    assert doc.pages[3].pixmap(matrix=pylopdf.Matrix(0.25, 0.25), alpha=True).to_bytes() == expected[3]


def test_closing_after_retained_renders() -> None:
    data = _text_pdf(["Closed"])
    doc = pylopdf.open(data)
    expected = bytes(doc.pages[0].pixmap(alpha=True))

    doc.close()
    doc.close()

    with pytest.raises(pylopdf.DocumentClosedError):
        doc.pages[0].pixmap(alpha=True).to_bytes()
    assert _fresh_samples(data, 0) == expected
