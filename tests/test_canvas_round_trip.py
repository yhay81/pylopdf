"""The Canvas round trip: redraw what ``Page.content()`` reads of the corpus.

Every path and image item of every page of the bundled corpus is drawn
again with ``Canvas.draw`` on a blank page of the same size, and the two
renderings agree within ``ROUND_TRIP_TOLERANCE`` levels per channel at
72 dpi outside the boxes of the items that a canvas cannot draw yet: text
runs (#372); items under a soft mask, pattern paints, inline images, and
image masks (#374). Those items are listed with the reason in
``_not_drawn``, and the documentation of ``Canvas.draw`` publishes the same
tolerance and exceptions.
"""

from __future__ import annotations

import math
import operator
from pathlib import Path as FilePath

import pytest

import pylopdf
from pylopdf import Color, Image, Path, TextRun

CORPUS = FilePath(__file__).parent / "assets" / "real_world"

# The largest difference of one channel, in levels of 255, outside the
# boxes of the items not drawn.
ROUND_TRIP_TOLERANCE = 16


def _not_drawn(item: TextRun | Path | Image) -> str | None:
    """Return why a canvas cannot draw an item yet, or ``None``."""
    if isinstance(item, TextRun):
        return "text run"
    if item.soft_mask is not None:
        return "soft mask"
    if isinstance(item, Image):
        return "inline image or image mask" if item.xref == 0 else None
    paints = (item.fill, None if item.stroke is None else item.stroke.paint)
    if any(paint is not None and not isinstance(paint, Color) for paint in paints):
        return "pattern paint"
    return None


def _largest_difference(
    original: bytes,
    redrawn: bytes,
    width: int,
    height: int,
    hidden: list[pylopdf.Rect],
) -> int:
    """Return the largest channel difference of two RGB rasters outside ``hidden``."""
    covered: list[list[tuple[int, int]]] = [[] for _ in range(height)]
    for box in hidden:
        x0, x1 = max(0, math.floor(box.x0) - 2), min(width, math.ceil(box.x1) + 2)
        for y in range(max(0, math.floor(box.y0) - 2), min(height, math.ceil(box.y1) + 2)):
            covered[y].append((x0, x1))
    largest = 0
    for y, spans in enumerate(covered):
        start = 0
        for x0, x1 in [*sorted(spans), (width, width)]:
            if x0 > start:
                a, b = (y * width + start) * 3, (y * width + x0) * 3
                if original[a:b] != redrawn[a:b]:
                    largest = max(largest, *map(abs, map(operator.sub, original[a:b], redrawn[a:b])))
            start = max(start, x1)
    return largest


@pytest.mark.parametrize("name", sorted(path.name for path in CORPUS.glob("*.pdf")))
def test_canvas_redraws_the_paths_and_images_of_every_page(name: str) -> None:
    doc = pylopdf.open(CORPUS / name)
    for page in doc.pages:
        target = pylopdf.Document()
        copy = target.pages.new((page.rect.width, page.rect.height))
        hidden = []
        with copy.draw() as canvas:
            for item in page.content():
                if isinstance(item, TextRun) or _not_drawn(item) is not None:
                    hidden.append(item.bbox)
                else:
                    canvas.draw(item)
        original = page.pixmap()
        redrawn = copy.pixmap()
        assert (redrawn.width, redrawn.height) == (original.width, original.height)
        difference = _largest_difference(bytes(original), bytes(redrawn), original.width, original.height, hidden)
        assert difference <= ROUND_TRIP_TOLERANCE, (name, page.index, difference)
