"""Check inferred Markdown word gaps on rotated styled text."""

from __future__ import annotations

import math

import pytest
from conftest import build_raw_pdf

import pylopdf


@pytest.mark.parametrize("rotation", [-30, -5, 0, 5, 30, 45, 90, 180, 270])
@pytest.mark.parametrize("gap", [0.0, 0.3])
@pytest.mark.parametrize("sizes", [(12, 12), (12, 14), (14, 12)])
def test_rotated_font_spans_preserve_word_gaps(
    rotation: int,
    gap: float,
    sizes: tuple[int, int],
) -> None:
    radians = math.radians(rotation)
    cosine, sine = math.cos(radians), math.sin(radians)
    # Short glyphs keep the complete rotated text within one extracted line.
    stream = (
        f"BT /F1 {sizes[0]} Tf "
        f"{cosine:.8f} {sine:.8f} {-sine:.8f} {cosine:.8f} 200 300 Tm "
        f"[(i) {-1000 * gap:.4f}] TJ /F2 {sizes[1]} Tf (l) Tj ET"
    )
    data = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [5 0 R] /Count 1 /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
            5: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R >>",
            6: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )
    with pylopdf.open(data) as document:
        expected = "i l" if gap else "il"
        assert document.pages[0].text() == expected + "\n"
        assert document.markdown() == expected
