---
title: WebAssembly
description: Run pylopdf in Cloudflare Python Workers and Pyodide, with the tested versions and the features that differ from native Python.
---

# WebAssembly

pylopdf publishes a WebAssembly wheel that runs in Cloudflare Python Workers
and Pyodide. It contains the same Rust code as the native wheels, so the API and
results are the same; it does not use a JavaScript PDF library.

## Supported environments { #supported-environments }

| Environment | Status |
|---|---|
| Cloudflare Python Workers | Supported. Every release is tested by building and starting a Worker from the published wheel |
| Pyodide 0.28.3 | Supported after changing the wheel's tag (below). The full compatibility test suite runs in it |
| Installing from PyPI with Pyodide 0.28.3's `micropip` | Not supported: that `micropip` does not recognize the wheel's `pyemscripten` platform tag |
| Other Pyodide or WebAssembly Python versions | Not tested |

The wheel targets Python 3.13 and the `pyemscripten_2025_0_wasm32` platform,
the runtime used by Pyodide 0.28.3 and by Cloudflare's `workers-py` 1.15.0.
There is no source-build fallback inside a browser or Worker.

## Cloudflare Worker { #cloudflare-worker }

The repository includes a
[tested example Worker](https://github.com/yhay81/pylopdf/tree/main/examples/cloudflare-worker)
that accepts a PDF and returns its page count and first-page text:

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf/examples/cloudflare-worker
uv sync
uv run pywrangler dev
```

In another terminal:

```bash
curl http://localhost:8787/health

curl --request POST \
  --header "content-type: application/pdf" \
  --data-binary @document.pdf \
  http://localhost:8787
```

Deploy with `uv run pywrangler deploy`. A Worker that bundles pylopdf is
larger than the 3 MB compressed limit of the Workers Free plan, so it needs
the Workers Paid plan.

The example limits input to 4 MiB and sets tighter
[`DocumentLimits`](security.md#untrusted-pdfs) than `DocumentLimits.web()`,
because a Worker's 128 MiB memory budget is shared with Python, JavaScript,
and the request itself. pylopdf reads the complete request body, so lower the
limits further if your own code needs more memory.

## Pyodide { #pyodide }

Pyodide 0.28.3's `micropip` does not accept the `pyemscripten` tag of the
published wheel, but the binary inside works in Pyodide 0.28.3: CI runs the
complete test suite on it under the `pyodide_2025_0_wasm32` tag. Download the
wheel and give it that tag with the `wheel` tool, which also updates the
metadata inside the wheel (renaming the file alone is not enough):

```bash
pip download pylopdf --no-deps --only-binary=:all: \
  --platform pyemscripten_2025_0_wasm32 --python-version 3.13 -d wheels
pip install wheel
wheel tags --platform-tag pyodide_2025_0_wasm32 --remove wheels/pylopdf-*.whl
```

Then serve the retagged wheel from a URL that Pyodide can reach and install it
with `micropip`:

```javascript
const pyodide = await loadPyodide();
await pyodide.loadPackage("micropip");
await pyodide.runPythonAsync(`
import micropip
await micropip.install(
    "https://example.com/pylopdf-0.15.0-cp311-abi3-pyodide_2025_0_wasm32.whl"
)
`);
```

A wheel built from source with `tools/build_pyodide.sh` (see
[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md#pyodide-0283-build))
has the same `pyemscripten` tag and needs the same step.

File paths refer to Pyodide's virtual file system, so pass browser data as
bytes:

```python
import pylopdf

with pylopdf.open(pdf_bytes, limits=pylopdf.DocumentLimits.web()) as doc:
    text = doc.pages[0].get_text() if doc.pages else ""
    output = doc.tobytes()
```

Check `sys.platform == "emscripten"` to detect the WebAssembly runtime.

## Differences from native Python { #differences }

Everything else, including extraction, rendering, editing, text generation,
forms, and encryption, works as on native Python.

| Feature | In WebAssembly |
|---|---|
| `render_pages(workers=...)` | Accepted, but pages are rendered one after another |
| Built-in OCR (`OcrEngine`, `get_text_ocr`, `apply_ocr`) | Not available; raises `OcrError`. Run OCR elsewhere and add its result with `Page.insert_ocr_text_layer()` |
| Font extras (`pylopdf[jp]` and the others) | Not tested. Pass font data explicitly with `fontbuffer=` or `set_fallback_font()` |

Rendering a large page at a high resolution needs the whole image in memory,
even when the PDF file itself is small, and resource limits do not stop a
long-running call: enforce time limits in the host, as on native Python.

## Size and testing { #size-and-testing }

Each release runs the same compatibility suite natively and inside Pyodide and
requires identical results. It covers opening from bytes, encrypted files,
text, words, search, Markdown, vertical Japanese, multi-column pages, tables,
vector drawings, rendering, generated text in standard and embedded fonts,
editing, saving, and error handling. Size, startup time, and memory use are
measured in CI and published in
[`bench/results/wasm-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md).

New Pyodide, Emscripten, `workers-py`, or Wrangler versions are supported only
after the complete suite passes on them.
