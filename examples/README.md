# Examples

## Glyph positions over a rendered page

`rawdict-overlay.py` reads every glyph with `page.layout(chars=True)`.
From a source checkout, run:

```sh
uv run python examples/rawdict-overlay.py --output /tmp/rawdict-overlay.html
uv run python examples/rawdict-overlay.py report.pdf --page 0 --scale 2 --output /tmp/report-overlay.html
```

Open the resulting HTML file in a browser. It contains its rendered PNG and
SVG overlays, so no server or additional Python dependency is needed. Omit
the PDF path to generate an English demo with Standard 14 text. Page numbers
are zero-based; the first 2,000 mapped glyph entries are shown to keep the
preview responsive. PDF input uses `DocumentLimits.web()`.

Blue rectangles show approximate glyph bounds, and red dots show baseline
origins. Check **Unicode labels** or hover an entry to inspect its mapping and
coordinates. Positions use the same top-left display points as rendering,
including page rotation and CropBox offsets. With the default `/UserUnit=1`,
one point is 1/72 inch; the current engine does not apply physical scaling for
non-default `/UserUnit`. A mapping can
contain several Unicode characters; entries do not split ligatures or add
geometry-inferred spaces. This is a visual inspection aid, not exact ink
geometry or OCR recognition.

## Cloudflare Workers

See [cloudflare-worker](cloudflare-worker/README.md) for a bounded PDF service
using the PyEmscripten wheel.
