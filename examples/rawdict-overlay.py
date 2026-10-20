"""Overlay mapped PDF glyph bounds and baseline origins on a rendered page."""

# ruff: noqa: INP001 - standalone command-line example.

from __future__ import annotations

import argparse
import base64
import html
import math
from itertools import islice
from pathlib import Path

import pylopdf

_MAX_OVERLAY_GLYPHS = 2_000


def _demo_document() -> pylopdf.Document:
    document = pylopdf.Document()
    page = document.new_page(width=420, height=220)
    page.insert_text((24, 42), "Glyph positions", fontsize=22)
    page.insert_text((24, 78), "PDF glyphs carry Unicode and geometry.", fontsize=13)
    page.insert_text((24, 112), "Blue boxes approximate glyph bounds.", fontsize=13)
    page.insert_text((24, 146), "Red dots mark baseline start points.", fontsize=13)
    page.insert_text((24, 182), "Hover a glyph or turn on its Unicode label.", fontsize=11)
    return document


def _glyph_markup(char: pylopdf.TextChar, index: int) -> str:
    x0, y0, x1, y1 = char["bbox"]
    x, y = char["origin"]
    label = html.escape(f"{index}: {char['c']}")
    details = html.escape(f"{char['c']!r}; bbox={char['bbox']}; origin={char['origin']}")
    return (
        f'<g class="glyph"><title>{details}</title>'
        f'<rect class="bbox" x="{x0:.4f}" y="{y0:.4f}" '
        f'width="{max(0.0, x1 - x0):.4f}" height="{max(0.0, y1 - y0):.4f}" />'
        f'<circle class="origin" cx="{x:.4f}" cy="{y:.4f}" r="1.5" />'
        f'<text class="label" x="{x:.4f}" y="{y - 3:.4f}">{label}</text></g>'
    )


def _create_html(layout: pylopdf.RawTextPageDict, png: bytes, source: str) -> str:
    width, height = layout["width"], layout["height"]
    spans = [span for block in layout["blocks"] for line in block["lines"] for span in line["spans"]]
    total = sum(len(span["chars"]) for span in spans)
    chars = (char for span in spans for char in span["chars"])
    marks = [_glyph_markup(char, index) for index, char in enumerate(islice(chars, _MAX_OVERLAY_GLYPHS), start=1)]
    image = base64.b64encode(png).decode("ascii")
    title = html.escape(source)
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PDF glyph overlay: {title}</title>
<style>
body {{ font: 16px system-ui, sans-serif; margin: 24px; color: #172033; background: #f4f6fa; }}
h1 {{ font-size: 24px; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 20px; margin: 16px 0; }}
svg {{ display: block; width: min(100%, {width:.4f}px); height: auto; background: white; border: 1px solid #aeb8c8; }}
.bbox {{ fill: #165dcc; fill-opacity: .08; stroke: #165dcc; stroke-width: .6; }}
.origin {{ fill: #bf2031; }}
.label {{ display: none; fill: #10284b; font: 8px monospace; paint-order: stroke; stroke: white; stroke-width: 2px; }}
.glyph:hover .bbox {{ fill-opacity: .3; stroke-width: 1.2; }}
svg.hide-boxes .bbox, svg.hide-origins .origin {{ display: none; }}
svg.show-labels .label {{ display: block; }}
</style>
<h1>PDF glyph overlay: {title}</h1>
<p>{len(marks):,} of {total:,} mapped glyph entries shown. Coordinates are display-space
points, with rotation and CropBox offsets applied.</p>
<div class="controls">
<label><input id="boxes" type="checkbox" checked> Glyph bounds</label>
<label><input id="origins" type="checkbox" checked> Baseline origins</label>
<label><input id="labels" type="checkbox"> Unicode labels</label>
</div>
<svg id="overlay" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.4f} {height:.4f}"
     width="{width:.4f}" height="{height:.4f}" role="img" aria-label="Rendered page with glyph overlays">
<image width="{width:.4f}" height="{height:.4f}" preserveAspectRatio="none"
       href="data:image/png;base64,{image}" />
{"".join(marks)}
</svg>
<p>Bounds are approximate, not exact ink outlines. Each entry keeps a glyph's complete
Unicode mapping, which may contain several characters. Inferred spaces are not added.
Hover an entry for its mapping and coordinates. Labels follow extraction order.
With the default /UserUnit=1, one point is 1/72 inch; the current engine does not
apply physical scaling for non-default /UserUnit.</p>
<script>
const overlay = document.getElementById('overlay');
for (const [id, className, invert] of [
    ['boxes', 'hide-boxes', true], ['origins', 'hide-origins', true],
    ['labels', 'show-labels', false]
]) {{
    document.getElementById(id).addEventListener('change', event => {{
        overlay.classList.toggle(className, invert ? !event.target.checked : event.target.checked);
    }});
}}
</script>
</html>
"""


def main() -> None:
    """Write a standalone glyph-overlay HTML preview for a PDF or generated demo."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, nargs="?", help="PDF to inspect; omit for an English demo")
    parser.add_argument("--page", type=int, default=0, help="zero-based page number (default: 0)")
    parser.add_argument("--scale", type=float, default=1.5, help="PNG rendering scale (default: 1.5)")
    parser.add_argument("--output", type=Path, default=Path("rawdict-overlay.html"), help="standalone HTML destination")
    args = parser.parse_args()
    if not math.isfinite(args.scale) or args.scale <= 0:
        parser.error("--scale must be a positive finite number")
    document = pylopdf.open(args.pdf, limits=pylopdf.DocumentLimits.web()) if args.pdf is not None else _demo_document()
    with document:
        page = document[args.page]
        layout = page.get_text("rawdict")
        png = page.render(scale=args.scale, background=(255, 255, 255))
        source = f"{args.pdf.name if args.pdf is not None else 'Generated demo'} — page {page.number}"
        output = _create_html(layout, png, source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    print(f"Wrote {args.output.resolve()}")  # noqa: T201 - manual example reports its destination.


if __name__ == "__main__":
    main()
