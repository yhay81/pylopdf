"""Tests for fallback rendering with non-embedded CJK fonts.

The tests use the bundled Noto Sans/Serif JP fonts under
fonts/pylopdf-fonts-cjk/. Auto-discovery runs only when pylopdf_fonts_cjk is
installed, as it is under ``uv sync --all-extras`` and in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_nonembedded_cjk_pdf

import pylopdf

FONTS_DIR = Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-cjk" / "src" / "pylopdf_fonts_cjk"
SANS = FONTS_DIR / "NotoSansJP-Regular.otf"
SERIF = FONTS_DIR / "NotoSerifJP-Regular.otf"


def render_blank_baseline() -> bytes:
    """Render the blank baseline with fallback and auto-discovery disabled."""
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(None)
    return doc.render_page(0, 1.0)


def test_without_fallback_renders_blank() -> None:
    """Render without text when no fallback font is available."""
    png = render_blank_baseline()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_fallback_font_from_path_renders_text() -> None:
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(SANS)
    png = doc.render_page(0, 1.0)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # Rendered glyphs add pixels, so the PNG differs from the blank baseline.
    assert png != render_blank_baseline()
    assert len(png) > len(render_blank_baseline())


def test_fallback_font_from_bytes_renders_text() -> None:
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(SANS.read_bytes())
    assert doc.render_page(0, 1.0) != render_blank_baseline()


def test_serif_slot_used_for_mincho() -> None:
    """Use the serif slot for PDFs whose BaseFont is MS-Mincho."""
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(SERIF, kind="serif")
    assert doc.render_page(0, 1.0) != render_blank_baseline()


def test_svg_rendering_includes_glyphs() -> None:
    blank_doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    blank_doc.set_fallback_font(None)
    blank_svg = blank_doc.render_page_svg(0)

    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(SANS)
    svg = doc.render_page_svg(0)
    assert svg.startswith("<svg")
    assert len(svg) > len(blank_svg)


def test_invalid_kind_raises() -> None:
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    with pytest.raises(ValueError, match="kind"):
        doc.set_fallback_font(SANS, kind="bold")


def test_latin_rendering_unaffected_by_fallback(one_page_pdf: bytes) -> None:
    """Keep Latin rendering unchanged when a fallback font is configured."""
    plain = pylopdf.open(stream=one_page_pdf)
    plain.set_fallback_font(None)
    baseline = plain.render_page(0, 1.0)

    with_font = pylopdf.open(stream=one_page_pdf)
    with_font.set_fallback_font(SANS)
    assert with_font.render_page(0, 1.0) == baseline


def test_auto_discovery_via_extra() -> None:
    """Auto-render CJK when pylopdf[cjk] is installed."""
    pytest.importorskip("pylopdf_fonts_cjk")
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    png = doc.render_page(0, 1.0)
    assert png != render_blank_baseline()


def test_nonembedded_cjk_extract_text() -> None:
    """Extract text from non-embedded CJK using 90ms-RKSJ-H.

    Hayro resolves the predefined CMap, and Unicode comes from that map, so
    extraction does not require a fallback font.
    """
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(None)
    assert "こんにちは日本語" in doc.get_page_text(0)
