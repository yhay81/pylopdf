"""Quads of extracted text and of search results, which follow rotated baselines."""

from __future__ import annotations

import math

import pytest
from test_text_options import build_text_pdf

import pylopdf


def _rotated(degrees: float, text: str = "Rotated words") -> pylopdf.Page:
    """Return a page with ``text`` at 20 points, turned ``degrees`` counterclockwise in user space."""
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return pylopdf.open(
        build_text_pdf(f"BT /F1 20 Tf {cos:.6f} {sin:.6f} {-sin:.6f} {cos:.6f} 200 300 Tm ({text}) Tj ET")
    ).pages[0]


def _side(a: pylopdf.Point, b: pylopdf.Point) -> float:
    return math.dist(a, b)


def _inside(point: pylopdf.Point, quad: pylopdf.Quad, tolerance: float = 1e-6) -> bool:
    """Return whether ``point`` lies in a rectangular quad, up to rounding."""
    along = quad.ur - quad.ul
    across = quad.ll - quad.ul
    offset = point - quad.ul
    u = (offset.x * along.x + offset.y * along.y) / (along.x**2 + along.y**2)
    v = (offset.x * across.x + offset.y * across.y) / (across.x**2 + across.y**2)
    return -tolerance <= u <= 1 + tolerance and -tolerance <= v <= 1 + tolerance


@pytest.mark.parametrize("degrees", [0, 30, 90, 135, 180, 270])
def test_quads_follow_the_baseline_of_rotated_text(degrees: int) -> None:
    page = _rotated(degrees)
    (line,) = page.layout(chars=True).blocks[0].lines
    span = line.spans[0]

    # The quad's top edge runs along the baseline direction, one em tall.
    angle = math.atan2(line.quad.ur.y - line.quad.ul.y, line.quad.ur.x - line.quad.ul.x)
    assert math.atan2(line.direction.y, line.direction.x) == pytest.approx(angle, abs=1e-9)
    assert _side(line.quad.ul, line.quad.ll) == pytest.approx(20)
    assert _side(line.quad.ur, line.quad.lr) == pytest.approx(20)
    # Every glyph's origin lies on the quad's baseline, 16 points below the top.
    for char in span.chars:
        assert _inside(char.origin, line.quad)
        assert _side(char.quad.ul, char.quad.ll) == pytest.approx(20)
    assert line.quad.rect == pytest.approx(line.bbox)
    assert span.quad == line.quad


@pytest.mark.parametrize("degrees", [30, 135])
def test_search_quads_cover_rotated_text_tightly(degrees: int) -> None:
    page = _rotated(degrees)

    (quad,) = page.search("words", quads=True)
    (rect,) = page.search("words")
    word = page.words()[1]

    assert quad.rect == pytest.approx(rect)
    assert quad.rect == pytest.approx(word.bbox)
    # The quad holds the glyphs' boxes but much less area than the rectangle.
    chars = page.layout(chars=True).blocks[0].lines[0].spans[0].chars[-5:]
    assert [char.text for char in chars] == list("words")
    for char in chars:
        assert all(_inside(corner, quad) for corner in char.quad)
    quad_area = _side(quad.ul, quad.ur) * _side(quad.ul, quad.ll)
    assert quad_area < 0.75 * rect.width * rect.height


def test_horizontal_search_quads_are_the_rectangles() -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 20 Tf 72 700 Td (one two one) Tj ET")).pages[0]

    quads = page.search("one", quads=True)

    assert [quad.rect for quad in quads] == page.search("one")
    assert all(quad.is_rectangular for quad in quads)


def test_a_dehyphenated_match_gives_one_quad_per_line() -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 12 Tf 14 TL 72 700 Td (exam-) Tj T* (ple) Tj ET")).pages[0]

    quads = page.search("example", dehyphenate=True, quads=True)
    (rect,) = page.search("example", dehyphenate=True)

    assert len(quads) == 2
    assert quads[0].rect | quads[1].rect == rect
    assert quads[0].ul.y < quads[1].ul.y


def test_search_quads_validate_and_bound_their_input() -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 20 Tf 72 700 Td (a a a) Tj ET")).pages[0]

    with pytest.raises(TypeError, match="quads must be True or False"):
        page.search("a", quads=1)  # ty: ignore[no-matching-overload]
    with pytest.raises(pylopdf.LimitError) as error:
        page.search("a", quads=True, max_hits=2)
    assert error.value.code == "search_hit_count"
    assert len(page.search("a", quads=True, max_hits=None)) == 3
