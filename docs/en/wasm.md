# WebAssembly compatibility

pylopdf builds a static PyEmscripten wheel for the Python 3.13 ABI used by
Pyodide 0.28.3 and Cloudflare Python Workers. The binary contains the same Rust
PDF engines as the native wheel; it does not use a JavaScript PDF
implementation or a wasm-bindgen shim.

!!! note "Release status"

    The WebAssembly wheel has shipped since pylopdf 0.11. v0.10.0 contains only
    native wheels.

## Tested environment

| Component | Pinned version or contract |
|---|---|
| Python runtime | CPython 3.13.2 |
| Pyodide | 0.28.3 |
| Emscripten toolchain | 4.0.9 |
| Wheel platform | PyEmscripten `2025_0`, `wasm32` |
| Build/smoke Node.js | 20.18.0 from the pinned Emscripten SDK |
| Cloudflare SDK | `workers-py` 1.15.0 |
| Cloudflare bundler | Wrangler 4.114.0 |
| Worker compatibility date | 2026-07-26 |

The published artifact uses
`cp310-abi3-pyemscripten_2025_0_wasm32`. The builder first runs the same binary
under the runtime-native `pyodide_2025_0_wasm32` tag, then deterministically
retags it for PEP 783 publication. Only the PyEmscripten-tagged artifact enters
PyPI, provenance attestation, and the release SBOM.

| Environment | Status | Detail |
|---|---|---|
| Cloudflare Python Workers | Supported, release-gated | CI resolves the PEP 783 wheel with the pinned SDK, builds the Wrangler bundle, starts local `workerd`, and verifies a module-scope import through `/health`. A tagged release repeats the check from PyPI before creating the GitHub release. |
| Pyodide 0.28.3 in Node.js | Runtime compatibility-gated | CI installs the locally built runtime-tagged wheel into the exact runtime and executes the full compatibility suite. |
| Direct browser installation from PyPI | Not supported with Pyodide 0.28.3 | Its `micropip` predates the PEP 783 `pyemscripten_*` tag required by PyPI. The binary is compatible; that frontend installation path is not. |
| Other Pyodide or Python/Wasm versions | Not tested | Platform and ABI compatibility must be established before widening the wheel tag or support claim. |

## Cloudflare Worker

The repository includes a
[tested extraction Worker](https://github.com/yhay81/pylopdf/tree/main/examples/cloudflare-worker).
It accepts a bounded PDF body and returns the page count and first-page text:

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

Use `uv run pywrangler deploy` after reviewing the compatibility date and the
limits for the selected Cloudflare plan. CI copies this exact example, replaces
its public pylopdf requirement with the just-built wheel, resolves it through
`workers-py`, runs `wrangler deploy --dry-run`, then starts local `workerd` and
requests `/health`. The module-scope `import pylopdf` must therefore complete
without startup-only entropy or other request-bound runtime state.

The example caps its input at 4 MiB, its complete rendering/extraction PDF
snapshot at 16 MiB, cumulative positioned text at 16,384 glyphs, and sets
tighter structural and decoded-data budgets than `DocumentLimits.web()`. The
complete request body is buffered because pylopdf currently accepts paths or
complete byte strings. Cloudflare's 128 MiB isolate budget also includes
Python, JavaScript, WebAssembly linear memory, and request buffers, so
production applications should reduce the file, interpretation, and text
budgets when their surrounding code needs more headroom.

## Direct Pyodide use

For Pyodide 0.28.3 development, build pylopdf with
`tools/build_pyodide.sh` and install the generated runtime-tagged wheel from a
URL visible to the runtime:

```javascript
const pyodide = await loadPyodide();
await pyodide.loadPackage("micropip");
await pyodide.runPythonAsync(`
import micropip
await micropip.install(
    "https://example.invalid/pylopdf-0.12.0-"
    "cp310-abi3-pyodide_2025_0_wasm32.whl"
)
`);
```

The URL is illustrative. Release artifacts use the PEP 783
`pyemscripten_2025_0_wasm32` tag for PyPI and Cloudflare; Pyodide 0.28.3's
older `micropip` does not accept that published tag. Do not rename a wheel
without also updating its embedded `WHEEL` metadata.

There is no supported sdist fallback inside a browser or Worker. Building the
extension requires the pinned Rust, Emscripten, Pyodide cross environment, and
retagging verifier. If no wheel matches a future ABI, treat that ABI as
unsupported instead of asking the runtime package installer to compile the
sdist.

Applications can detect this runtime with `sys.platform == "emscripten"`.
Paths refer to the virtual filesystem, not directly to browser files:

```python
import sys
import pylopdf

assert sys.platform == "emscripten"
with pylopdf.Document(stream=pdf_bytes, limits=pylopdf.DocumentLimits.web()) as doc:
    text = doc.get_page_text(0) if doc.page_count else ""
    output = doc.tobytes()
```

## Tested API surface

The shared native/Wasm suite currently covers:

- `bytes` input without a host filesystem, page counts, PDF 2.0 parsing, and
  encrypted AES-256 input;
- plain text, words, dictionaries, search, document Markdown, embedded Japanese
  text, inferred vertical CJK, sustained multicolumn order, image-only pages,
  and rotated pages;
- bordered and conservative borderless tables, including Markdown integration,
  plus vector drawing extraction;
- empty-document creation, Standard 14 and subset-embedded OpenType text,
  textbox layout, rendering, `Pixmap`, serialization, virtual-filesystem save,
  merge, reorder, duplicate, and select;
- `PdfError`, `LimitError` and its stable resource code, `PasswordError`,
  `EncryptedDocumentError`, `DocumentClosedError`, and `StalePageError`,
  followed by reuse of the runtime after malformed input; and
- `render_pages(workers=4)` input ordering and byte equality with
  `workers=1`.

The fixtures include a PDF 2.0 sample, an embedded-CJK Japanese government
document, IRS Form 1040, a rotated US Senate table, an image-only Japanese scan,
and a generated vertical-writing document. Every committed PDF is under 1 MiB
and has a redistributable license recorded in the corpus README.

The suite runs once with the native wheel and once inside Pyodide. Logical
results must match exactly. It checks explicit structure and expected text in
addition to full extraction and Markdown hashes, so a native/Wasm divergence
cannot hide behind a permissive smoke test.

## Capability and dependency map

The Wasm wheel remains one artifact rather than several partly compatible
variants. Feature ownership is:

| Capability | Rust component | Wasm status |
|---|---|---|
| PDF structure, editing, encryption | lopdf | Included |
| Text, tables, vector paths | hayro syntax/interpreter and CMap support | Included |
| PNG raster rendering | hayro, Vello and PNG encoding | Included |
| SVG rendering | hayro SVG backend | Included |
| Generated text and form appearances | krilla, HarfRust, read-fonts and UAX line breaking | Included |
| Images and JPEG compression | Flate, zune-jpeg and jpeg-encoder | Included |
| Same-document parallel rendering | rayon | Native only; Wasm executes serially |
| PP-OCRv6 inference | RTen plus external model wheel | Native only; omitted from the Wasm binary |
| Automatic CJK fallback discovery | external CJK font wheel and host paths | Not in the Wasm compatibility contract |

`OcrEngine()` remains present so capability probing is deterministic, but on
Emscripten it raises `OcrError` with guidance to run OCR outside Wasm and call
`Page.insert_ocr_text_layer()`. Removing the unused RTen inference runtime from
Wasm does not remove PDF extraction, rendering, generation, or insertion of
externally recognized OCR text.

## Measured deployment envelope

The pinned CI artifact is a 3.772 MiB wheel with a 9.910 MiB uncompressed Wasm
extension. The tested Worker bundle is 3.817 MiB compressed and 10.383 MiB
uncompressed. It therefore exceeds Cloudflare Workers Free's 3 MB compressed
limit but fits the paid-plan 10 MB compressed and shared 64 MB uncompressed
limits. pylopdf supports the paid-plan deployment path and does not publish a
separate reduced-feature distribution. The artifact uses fat LTO with one
codegen unit only for PyEmscripten; native release profiles are unchanged.

In the Node/Pyodide harness, the first Form 1040 open and extraction took
117.634 ms; five repeated runs had a 28.506 ms median. Wasm linear memory
reached 39.688 MiB after installation and 70.000 MiB after the full
compatibility and resource suites. These are reproducible CI trends, not
Cloudflare request latency or isolate resident-memory measurements. See the
[complete size and startup report](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md).

## Runtime limits

- Emscripten has no rayon worker pool in this build.
  `render_pages(workers=...)` accepts the normal public arguments but executes
  serially. Native builds keep bounded rayon parallelism.
- Rendering limits are unchanged. `clip=` reduces returned pixels but hayro
  still rasterizes the complete page internally.
- The current renderer buffers complete raster output. Large pages or high DPI
  can dominate memory even when the PDF file itself is small.
- Native OCR and the separately distributed OCR model package are outside the
  WebAssembly compatibility contract.
- Automatic external CJK fallback-font discovery is not covered. Embedded CJK
  PDFs are tested, and applications may supply font bytes explicitly.
- The current gate proves Cloudflare bundle construction and local `workerd`
  startup with a module-scope import. It does not prove an authenticated
  production deployment or workload-specific latency.

The same matrix exercises `DocumentLimits`, `doc.complexity`, representative
web-bounded vector and scan inputs, and stable file/page/text rejection codes on
native and Wasm. Scheduled native Atheris fuzzing adds the larger generated
hostile corpus. CPU deadlines remain a host responsibility. Do not infer a
larger memory budget merely because a PDF passes on native Python; use an
explicit policy on both runtimes.

## Support and release policy

Each pylopdf release that publishes a Wasm wheel must pass:

1. the reproducible build and wheel metadata/import verifier;
2. the shared native/Pyodide logical compatibility suite;
3. untrusted-input rejection and resource-trend checks;
4. wheel, Wasm-section, startup/workload, and linear-memory measurement;
5. dependency resolution, Cloudflare Wrangler dry-run, local `workerd` startup,
   and a module-scope-import health request from the local wheel;
6. the same resolution, bundle, and runtime health gate from PyPI before the
   GitHub release is finalized.

Runtime upgrades are evaluated as a new tested matrix, not assumed compatible.
The pinned versions stay supported for the corresponding pylopdf minor release;
new Pyodide, PyEmscripten, Emscripten, `workers-py`, or Wrangler versions enter
the claim only after the complete gate passes. Measurements and any regressions
are published together in
[`bench/results/wasm-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md).
