# pylopdf untrusted-input limit baseline

- Run at: 2026-07-25 21:51 UTC
- CI run: [GitHub Actions 30176435435](https://github.com/yhay81/pylopdf/actions/runs/30176435435)
- Source commit: `bbd3d79a675e7d35f133041b4fb23fd70756fabb`
- Native environment: GitHub-hosted Ubuntu / CPython 3.10.20
- Wasm environment: GitHub-hosted Ubuntu / Pyodide 0.28.3 / CPython 3.13.2
- Repetitions: median of 5 runs per task (ms; lower is faster)
- Corpus: `tests/assets/real_world` (sources and licenses are documented in its README)

## Policy under test

The benchmark opens representative vector and scanned PDFs with
`DocumentLimits.web()`. The profile is intentionally conservative for bounded
web workers:

| Budget | Limit |
|---|---:|
| Input file | 10 MiB |
| Pages | 200 |
| Indirect objects | 100,000 |
| One decoded stream or estimated image raster | 64 MiB |
| One decoded page-content stream | 10 MiB |
| Cumulative decoded or estimated streams | 128 MiB |
| Direct array/dictionary nesting | 64 |
| Cumulative interpreted glyph UTF-8 payload | 1 MiB |

## Timing trend

| Case | Native CPython 3.10.20 | Pyodide 0.28.3 |
|---|---:|---:|
| Open `f1040.pdf` (220,237 bytes) | 6.734 ms | 20.490 ms |
| Open Japanese scan (130,446 bytes) | 2.490 ms | 8.598 ms |
| Open and extract Form 1040 page 0 | 13.375 ms | 26.054 ms |
| Reject a 5,211-byte input by file size | 0.013 ms | 0.028 ms |
| Reject Form 1040 by page count | 4.897 ms | 13.691 ms |

These are environment-specific trends, not a native-versus-Wasm performance
claim. The runners use different Python runtimes, and the measurements include
runtime-specific allocation and scheduling behavior.

## Memory evidence

| Runtime | Measurement | Result |
|---|---|---:|
| Native | Whole-process peak RSS high-water mark | 60,506,112 bytes (57.7 MiB) |
| Pyodide | Wasm linear memory before the measured suite | 80,084,992 bytes (76.375 MiB) |
| Pyodide | Wasm linear memory after the measured suite | 80,084,992 bytes (76.375 MiB) |
| Pyodide | Wasm linear-memory growth | 0 bytes |

The native number is a whole-process high-water mark, while the Wasm number is
the linear-memory capacity visible to the Pyodide runtime. They are different
metrics and must not be compared as equivalent resident-memory measurements.
Zero Wasm growth means this bounded suite fit within the already allocated
linear memory; it does not mean the operations allocated no temporary memory.

## Correctness gates

The same native and Wasm suite compared its complete stable JSON result
exactly. It also asserted the `file_size`, `page_count`, `decompressed_size`,
`object_depth`, `decompression_unverifiable`, and `text_size` rejection codes,
accepted a reference cycle without recursive traversal, and opened a
representative bounded scan. The wider CI run passed Linux, macOS, Windows,
CPython 3.10, free-threaded CPython 3.14, RustSec, and Pyodide checks.

Resource budgets cannot provide a portable CPU deadline. Web services must
still enforce an outer request timeout and bounded admission concurrency.

## Reproduce

Run the native measurement from a synchronized checkout:

```bash
uv run python tools/pyodide_compat.py --root . --benchmark-only \
  --benchmark-output .tmp/limits-benchmark.json
```

The pinned Pyodide and Cloudflare path runs in CI through
`.github/workflows/ci.yml`. Quote this report together with its environments,
corpus, and limitations.
