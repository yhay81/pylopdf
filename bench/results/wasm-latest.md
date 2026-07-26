# pylopdf PyEmscripten size and startup report

- Updated at: 2026-07-26 10:45 UTC
- Whole-program LTO comparison:
  [baseline run 30198436402](https://github.com/yhay81/pylopdf/actions/runs/30198436402)
  versus
  [LTO run 30198651300](https://github.com/yhay81/pylopdf/actions/runs/30198651300)
- LTO source head: `1b34373bdc0816764023cea9bed1c9adbb883c95`
- Original RTen-removal measurement:
  [run 30177351000](https://github.com/yhay81/pylopdf/actions/runs/30177351000),
  source `c82ee42059b1a660ceb0f80a2628e2a581e2437f`
- Runtime: Pyodide 0.28.3 / CPython 3.13.2 / Emscripten 4.0.9 /
  Emscripten Node.js 20.18.0
- Cloudflare tooling: workers-py 1.15.0 / Wrangler 4.114.0 /
  compatibility date 2026-07-26
- Representative document: public-domain IRS Form 1040 from
  `tests/assets/real_world/f1040.pdf`

## Whole-program LTO follow-up

The post-v0.11.1 Emscripten build applies fat LTO with one codegen unit to the
single extension module. This is a PyEmscripten-only build setting: native
maturin builds retain Cargo's default release profile and compile-time
tradeoff. The paired CI runs used the same pinned builder, corpus, compatibility
suites, and deployment harness.

| Measurement | Default release profile | Fat LTO | Change |
|---|---:|---:|---:|
| PyEmscripten wheel | 4,068,255 bytes (3.880 MiB) | 3,955,120 bytes (3.772 MiB) | -113,135 bytes (-2.78%) |
| Installed Wasm extension | 11,034,960 bytes (10.524 MiB) | 10,391,906 bytes (9.910 MiB) | -643,054 bytes (-5.83%) |
| Wasm code section | 8,507,042 bytes (8.113 MiB) | 8,104,630 bytes (7.729 MiB) | -402,412 bytes (-4.73%) |
| Wasm data section | 2,476,154 bytes (2.361 MiB) | 2,274,906 bytes (2.170 MiB) | -201,248 bytes (-8.13%) |
| Cloudflare total upload | 11,530,906 bytes (10.997 MiB) | 10,887,854 bytes (10.383 MiB) | -643,052 bytes (-5.58%) |
| Cloudflare gzip upload | 4,116,972 bytes (3.926 MiB) | 4,002,529 bytes (3.817 MiB) | -114,443 bytes (-2.78%) |
| Wasm linear-memory high water | 74,252,288 bytes (70.812 MiB) | 73,400,320 bytes (70.000 MiB) | -851,968 bytes (-1.15%) |

The full native/Pyodide compatibility hashes remained exact. Wheel metadata,
the publication tag, runtime resource checks, and the module-scope import under
local `workerd` all passed. The repeated Form 1040 open-and-extract median was
effectively unchanged at 28.618 ms before and 28.506 ms after. The first
open-and-extract measurement moved from 124.101 to 117.634 ms and pylopdf import
from 103.150 to 95.205 ms; these one-process observations are treated as trend
noise rather than claimed latency improvements.

The paired Pyodide CI job grew from 2m03s to 4m40s because the final link
performs whole-program optimization. That remains below the slowest native
release jobs while saving 113 KB of wheel transfer, 643 KB of installed
extension and Worker upload, and 0.812 MiB of observed Wasm linear capacity.
The complete artifact still exceeds Cloudflare Workers Free's compressed-size
limit, so the paid-plan deployment boundary is unchanged.

The remaining controlled comparison preserves the original RTen-removal
measurement that established the supported Emscripten capability boundary.

The change under test omits RTen and its tensor runtime from Emscripten. Native
builds retain the complete `pylopdf[ocr]` implementation. The Emscripten module
keeps an API-compatible `OcrEngine` boundary that raises `OcrError` and directs
applications to external OCR plus `Page.insert_ocr_text_layer()`.

## Artifact size

The previous artifact is the CI wheel from source commit
`bbd3d79a675e7d35f133041b4fb23fd70756fabb`. Both artifacts were built by the
same pinned Pyodide builder and inspected with
`tools/wasm_artifact_metrics.py`.

| Measurement | Before | After | Change |
|---|---:|---:|---:|
| PyEmscripten wheel | 4,742,137 bytes (4.522 MiB) | 4,020,675 bytes (3.834 MiB) | -721,462 bytes (-15.21%) |
| Installed Wasm extension | 13,429,867 bytes (12.808 MiB) | 10,909,749 bytes (10.404 MiB) | -2,520,118 bytes (-18.77%) |
| Wasm code section | 10,749,943 bytes (10.252 MiB) | 8,393,344 bytes (8.005 MiB) | -2,356,599 bytes (-21.92%) |
| Wasm data section | 2,550,490 bytes (2.432 MiB) | 2,465,418 bytes (2.351 MiB) | -85,072 bytes (-3.34%) |

The resulting wheel contains 13 files and expands to 11,276,055 bytes. The
installed pylopdf package reported by the runtime is 11,052,286 bytes across
five package files. The wheel SHA-256 is
`a046cb9cbe0820d281d94c0483b1dc994cdb85f03dab28b55c8fef5e63f25098`.

The code-section reduction accounts for most of the uncompressed win. This
matches the intended dependency boundary: PDF parsing, editing, extraction,
rendering, SVG, generation, forms, and raster compression stay present; only
native OCR inference and native parallel execution are absent from Wasm.

### Feature attribution

Linked size is not additive by crate after LLVM dead-code elimination, and the
release binary is stripped. The investigation therefore distinguishes an
isolated before/after measurement from architectural ownership:

| Feature group | Dependency boundary | Size conclusion |
|---|---|---|
| Native OCR inference | RTen and rten-tensor | Isolated experiment above: removing it saved 721,462 wheel bytes and 2,520,118 extension bytes. |
| Parallel batch rendering and native clocks | rayon and chrono | Already absent from both the before and after Emscripten artifacts; there is no remaining Wasm saving to claim. |
| Raster rendering and extraction | hayro, hayro-interpret, image, Vello | hayro 0.7 does not expose a rendering-free feature boundary, and the interpreter/font/CMap stack is shared with extraction. API-level removal is not an independent crate toggle. |
| SVG rendering | hayro-svg plus the shared hayro interpreter | A separate public method, but it shares the complete interpretation graph; its isolated linked contribution is not reliably additive. |
| Font shaping, generation, and form appearances | krilla, HarfRust, read-fonts, UAX helpers | Coupled to arbitrary-font text, textbox layout, and AcroForm appearances. Removing the group defines a materially different creation API. |
| Structure and editing | lopdf | Owns the document state used by load, save, merge, selection, encryption, and all mutations; it cannot be removed from an extraction-capable `Document` without a new architecture. |
| Raster editing and compression | Flate, zune-jpeg, jpeg-encoder | Flate and JPEG decoding are also shared with PDF parsing/rendering, so crate sizes cannot be assigned wholly to `compress_images`. |

Only RTen formed both a coherent unsupported Emscripten boundary and a clean
controlled measurement. The other proposed removals either had already
occurred (`rayon`, `chrono`) or cross capability boundaries. Producing
artificial stub builds for them would report non-additive numbers for variants
the project does not intend to support, so those numbers are deliberately not
presented as dependency costs.

## Cloudflare bundle size

Wrangler dry-ran the repository's exact
`examples/cloudflare-worker` source. `tools/smoke_cloudflare.py` replaced only
its public pylopdf requirement with the local wheel, synchronized dependencies
through workers-py, verified the vendored PyEmscripten tag, and recorded
Wrangler's upload report.

| Measurement | Before | After | Change |
|---|---:|---:|---:|
| Total upload | 13,580.03 KiB (13.262 MiB) | 11,104.28 KiB (10.844 MiB) | -18.23% |
| Gzip upload | 4,680.10 KiB (4.570 MiB) | 3,974.84 KiB (3.882 MiB) | -15.07% |
| Vendored Python modules | not previously recorded | 11,368,669 bytes / 43 files | — |
| Wrangler dry-run output | not previously recorded | 11,370,905 bytes / 46 files | — |

The 3.882 MiB compressed bundle is above Cloudflare Workers Free's current
3 MB compressed-script limit, but below the paid-plan 10 MB limit. The
10.844 MiB uncompressed bundle is below the shared 64 MB uncompressed limit.
See Cloudflare's
[current platform limits](https://developers.cloudflare.com/workers/platform/limits/).

The supported deployment target is therefore a paid Cloudflare Workers plan.
The dry run proves dependency resolution and bundle construction; it does not
authenticate or perform a production deployment.

## Startup and workload trend

`tools/smoke_pyodide.mjs` measured one process with `performance.now()`.
Document timings open the same Form 1040 bytes and extract page 0. Repeated
results are a median of five complete open-and-extract operations.

| Stage | Time |
|---|---:|
| Import Pyodide JavaScript module | 1.757 ms |
| Initialize Pyodide runtime | 1,735.314 ms |
| Load micropip | 237.679 ms |
| Install local wheel | 554.440 ms |
| Import pylopdf | 68.011 ms |
| First document open and extraction | 116.267 ms |
| Repeated document open and extraction, median | 26.893 ms |

The five repeated samples were 76.452, 33.584, 26.400, 26.267, and 26.893 ms.
The extracted text length was 5,457 characters.

These Node-hosted Pyodide timings are reproducible trends, not Cloudflare
request-latency measurements. Cloudflare Python Workers run module imports at
deployment and restore a snapshot of Python and WebAssembly linear memory for
requests, so the 1.735-second standalone `loadPyodide()` measurement is not a
Cloudflare Worker startup measurement. See
[how Python Workers work](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/).
A live authenticated deployment is required to record Cloudflare's actual
startup and request metrics.

## Memory checkpoints

| Checkpoint | Wasm linear memory |
|---|---:|
| Runtime initialized | 20,971,520 bytes (20.000 MiB) |
| Wheel installed | 42,336,256 bytes (40.375 MiB) |
| pylopdf imported | 42,336,256 bytes (40.375 MiB) |
| First document complete | 42,336,256 bytes (40.375 MiB) |
| Five repeated documents complete | 42,336,256 bytes (40.375 MiB) |
| Full compatibility suite complete | 74,055,680 bytes (70.625 MiB) |
| Resource-limit benchmark complete | 74,055,680 bytes (70.625 MiB) |

The maximum observed Wasm linear-memory capacity was 70.625 MiB. The Node
process RSS checkpoint high-water value was 355,635,200 bytes, but it includes
the Node host, JavaScript runtime, Pyodide, caches, and allocator behavior. It
must not be compared with Cloudflare's 128 MB isolate limit as though it were
the Worker's resident footprint.

The repository example uses a 4 MiB request cap and stricter decoded-data
budgets than `DocumentLimits.web()` because a Worker must fit the Python/JS
runtime, Wasm memory, request bytes, decoded PDF content, and response into one
isolate. This measurement is evidence for that starting policy, not proof that
every accepted PDF has the same peak memory.

## Extraction quality gate

The size change passed the complete shared native/Pyodide result comparison,
not only the timed Form 1040 call. That contract checks explicit expected
content plus full text and Markdown hashes for representative vector, scanned,
CJK, multicolumn, vertical-writing, bordered-table, and borderless-table
documents. It also checks positioned dictionaries, search, drawings, rotation,
and post-error runtime reuse. Removing RTen therefore did not change the
text/Markdown extraction surface used by the planned FolioMCP integration.

FolioMCP and its API repository are private at the time of this report, so the
public guide intentionally does not link them or copy private fixtures. Add the
consumer link only after its repository is public; the redistributable pylopdf
compatibility corpus remains the release gate.

## Distribution decision

Do not add a separate lightweight distribution now.

The optimized complete PDF core fits the paid Cloudflare compressed and
uncompressed bundle limits with substantial headroom. A Free-plan artifact
would need to remove at least another 1.00 MB from the compressed upload, and
the plan's 10 ms CPU budget is also below this Node trend's 28.506 ms warm
median for one representative extraction. Removing rendering, generation, or
extraction subcomponents merely to cross that boundary would fragment the
public API without establishing a credible PDF workload on the Free plan.

The project will keep one Wasm wheel, continue measuring wins and losses, and
reconsider a variant only if a real deployment class cannot use the complete
artifact or upstream component boundaries permit a clearly coherent feature
set. Further size reductions that preserve the single artifact remain welcome.

## Reproduce

The pinned CI path is:

```bash
bash tools/build_pyodide.sh
python tools/wasm_artifact_metrics.py \
  dist/pyodide-0.28.3/*.whl \
  --output pyodide-compat/artifact-metrics.json
python tools/smoke_cloudflare.py \
  --wheel dist/pyodide-0.28.3/*.whl \
  --metrics-output cloudflare-compat/bundle-metrics.json
```

`tools/build_pyodide.sh` calls `tools/smoke_pyodide.mjs` and writes
`runtime-metrics.json` when `PYLOPDF_PYODIDE_METRICS_OUTPUT` is set. Quote this
report together with its pinned versions, CI environment, corpus, and stated
limits.
