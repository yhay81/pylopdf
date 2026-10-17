---
title: Benchmarks
description: Reproducible pylopdf benchmarks for extraction, merging, rasterization and PNG encoding, with wins and losses published together.
---

# Benchmarks

pylopdf publishes **wins and losses together**. These measurements are a
snapshot of one machine and corpus—not a universal ranking. Use them to choose
what to measure in your own workload.

!!! info "Latest run"
    **2026-10-17 15:14 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro (arm64)<br>
    pylopdf 0.14.0 built with the thin-LTO release profile · pymupdf 1.28.0 · pypdf 6.15.0 · pdfplumber 0.11.10<br>
    One warm-up plus seven measured runs; tables show median milliseconds.

## At a glance { #overview }

| Workload | What the latest corpus shows |
|---|---|
| Merge 14 real-world PDFs | pylopdf **32.8 ms**, pymupdf 116.2 ms, pypdf 145.4 ms |
| First page to 2× PNG, end to end | pylopdf led on all 14 files, **216.1 ms** versus 591.3 ms in total |
| Rasterize only | pymupdf led on 9 of 14 files cold and 13 of 14 warm; warm totals were 160.8 ms versus **81.2 ms** |
| Encode PNG | pylopdf encoded about 9× faster (**45.4 ms** versus 407.4 ms) but wrote 40% more bytes (4,645 versus **3,318 KiB**) |
| Render 12 pages at 2× | `render_pages()` went from 165.3 ms with 1 worker to 34.9 ms with 8, a **4.74× speedup**; pymupdf took 423.1 ms sequentially |
| Extract all text | pylopdf led on 13 of 14 files; pymupdf led on the other |
| Extraction fidelity proxy | Similarity ranged from 0.443 to 1.000 where both libraries extract text; pylopdf extracts no text from the Type 3 fixture |
| Linux and Windows CI runners | pylopdf's extraction lead was smaller, and MuPDF also rasterized faster cold; see [Other platforms](#other-platforms) |

## Text extraction { #text-extraction }

All pages, milliseconds; lower is faster.

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **70.6** | 291.7 | 404.8 | 4531.3 |
| bunka-kokugo-series-019-p4.pdf | **0.19** | 0.25 | 0.30 | 0.47 |
| f1040.pdf | **8.8** | 30.0 | 69.4 | 223.6 |
| mhlw-doc.pdf | **7.9** | 9.2 | 36.0 | 71.2 |
| nics-background-checks-2015-11.pdf | **4.9** | 7.5 | 52.0 | 119.5 |
| patent-us223898.pdf | 16.8 | **11.3** | 40.3 | 164.8 |
| pdf20-simple.pdf | **0.21** | 0.59 | 0.56 | 0.82 |
| pdfium-jpx-lzw.pdf | **0.11** | 0.26 | 0.16 | 0.31 |
| pdfium-links-highlights-annots.pdf | **0.29** | 0.75 | 0.32 | 1.3 |
| pdfium-smask-blend.pdf | **0.10** | 0.26 | 0.19 | 0.55 |
| pdfium-type3.pdf | **0.12** | 0.27 | 0.29 | 0.50 |
| senate-expenditures.pdf | **2.6** | 6.8 | 49.4 | 110.0 |
| usrguide.pdf | **27.6** | 63.1 | 282.3 | 785.5 |
| wdl6812-manuscript.pdf | **0.21** | 0.63 | 0.57 | 0.95 |
| **Total** | **140.4** | 422.5 | 936.5 | 6010.8 |

## Extraction content { #extraction-content }

This is a proxy, not a correctness score. Text is whitespace-normalized and
compared with pymupdf. Lower similarity for forms and rotated tables can
reflect a different reading order or whitespace policy even when character
counts match. A zero-character row is image-only, except `pdfium-type3.pdf`:
pylopdf skips its glyphs because hayro reports no Unicode mapping for them.

| File | pylopdf characters | pymupdf characters | Similarity |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.855 |
| patent-us223898.pdf | 11219 | 11218 | 0.581 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| pdfium-jpx-lzw.pdf | 0 | 0 | 1.000 |
| pdfium-links-highlights-annots.pdf | 92 | 92 | 1.000 |
| pdfium-smask-blend.pdf | 0 | 0 | 1.000 |
| pdfium-type3.pdf | 0 | 5 | 0.000 |
| senate-expenditures.pdf | 4516 | 4516 | 0.443 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## Merge { #merge }

| Task | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| Merge all 14 corpus files | **32.8** | 116.2 | 145.4 |

## Rendering { #rendering }

First page at 2×, milliseconds; lower is faster. Both libraries use their
defaults: pylopdf produces straight-alpha RGBA8 on a transparent background,
and pymupdf produces RGB on white.

### End to end { #rendering-end-to-end }

Each run opens a fresh document, rasterizes the page, and encodes PNG.

| File | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **19.5** | 58.0 |
| bunka-kokugo-series-019-p4.pdf | **21.0** | 84.9 |
| f1040.pdf | **25.2** | 67.5 |
| mhlw-doc.pdf | **18.8** | 50.4 |
| nics-background-checks-2015-11.pdf | **29.1** | 53.3 |
| patent-us223898.pdf | **15.7** | 52.2 |
| pdf20-simple.pdf | **3.4** | 13.1 |
| pdfium-jpx-lzw.pdf | **10.4** | 38.5 |
| pdfium-links-highlights-annots.pdf | **6.1** | 25.7 |
| pdfium-smask-blend.pdf | **0.92** | 3.5 |
| pdfium-type3.pdf | **0.66** | 1.9 |
| senate-expenditures.pdf | **29.6** | 39.7 |
| usrguide.pdf | **15.7** | 38.5 |
| wdl6812-manuscript.pdf | **20.2** | 64.1 |
| **Total** | **216.1** | 591.3 |

Most of this lead comes from PNG encoding, not rasterization. The next two
tables split the same work.

### Rasterization { #rasterization }

Cold runs open a fresh document for every repetition; warm runs rasterize the
same page of a document that is already open. Caches that outlive a document,
such as MuPDF's process-wide glyph cache, still apply to cold runs. MuPDF also
keeps loaded fonts and rendered glyphs for an open document, which matters most
on text-heavy pages. pylopdf currently creates a new hayro render cache,
including fonts and glyph outlines, for every render call, so its warm and cold
times are close.

| File | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 13.5 | **8.5** | 12.5 | **4.0** |
| bunka-kokugo-series-019-p4.pdf | **16.7** | 49.8 | 16.5 | **14.6** |
| f1040.pdf | **19.7** | 26.4 | 16.3 | **15.6** |
| mhlw-doc.pdf | 14.5 | **13.1** | 13.8 | **3.5** |
| nics-background-checks-2015-11.pdf | 23.1 | **7.4** | 22.6 | **5.6** |
| patent-us223898.pdf | **11.6** | 22.0 | 11.5 | **11.4** |
| pdf20-simple.pdf | 2.1 | **0.78** | 1.9 | **0.32** |
| pdfium-jpx-lzw.pdf | **8.8** | 14.9 | **8.6** | 9.7 |
| pdfium-links-highlights-annots.pdf | 4.4 | **2.1** | 4.0 | **1.2** |
| pdfium-smask-blend.pdf | 0.79 | **0.62** | 0.48 | **0.39** |
| pdfium-type3.pdf | 0.59 | **0.27** | 0.29 | **0.04** |
| senate-expenditures.pdf | 24.8 | **5.4** | 24.4 | **3.6** |
| usrguide.pdf | 12.6 | **8.1** | 11.4 | **0.71** |
| wdl6812-manuscript.pdf | **16.8** | 21.8 | 16.5 | **10.6** |
| **Total** | **169.9** | 181.1 | 160.8 | **81.2** |

### PNG encoding { #png-encoding }

Both encoders receive an already rendered raster. pylopdf uses fast fdeflate
compression for RGBA8 output, trading file size for speed; pymupdf uses zlib's
default level for RGB output. Recompress pylopdf output when file size matters
more than encoding time.

| File | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.8** | 51.1 | 328.3 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **4.2** | 33.7 | 438.8 | **249.4** |
| f1040.pdf | **5.8** | 39.9 | 841.3 | **360.9** |
| mhlw-doc.pdf | **4.2** | 37.3 | 340.5 | **301.4** |
| nics-background-checks-2015-11.pdf | **6.0** | 46.1 | 737.1 | **343.0** |
| patent-us223898.pdf | **3.9** | 28.6 | 245.0 | **154.6** |
| pdf20-simple.pdf | **1.1** | 12.5 | 28.4 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.4** | 23.7 | 39.9 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.7** | 23.9 | 53.5 | **21.5** |
| pdfium-smask-blend.pdf | **0.12** | 2.8 | 4.2 | **2.6** |
| pdfium-type3.pdf | **0.11** | 1.5 | 3.5 | **1.1** |
| senate-expenditures.pdf | **4.1** | 33.7 | 332.8 | **253.8** |
| usrguide.pdf | **3.4** | 30.5 | 188.8 | **138.0** |
| wdl6812-manuscript.pdf | **3.5** | 42.0 | **1062.9** | 1191.3 |
| **Total** | **45.4** | 407.4 | 4645.1 | **3317.7** |

## Parallel rendering { #parallel-rendering }

First 12 pages of `usrguide.pdf` to 2× PNG, milliseconds; lower is faster.
The batch preserves input order and uses one immutable document snapshot.

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 165.3 | 1.00× |
| 2 | 89.8 | 1.84× |
| 4 | 48.6 | 3.40× |
| 8 | 34.9 | 4.74× |

Actual concurrency is bounded by both the requested worker count and an
estimated 512 MB of live rendering memory. For reference, pymupdf rendered and
encoded the same 12 pages sequentially on one fresh document in 423.1 ms.

## Other platforms { #other-platforms }

The snapshot above comes from one macOS arm64 machine. On 2026-10-17 the same
`bench/run.py` ran once on each GitHub-hosted release runner with the thin-LTO
abi3 wheel (Python 3.14.8, or 3.14.7 on macOS x86_64; one warm-up plus five
measured runs). Cells show pylopdf / pymupdf corpus totals in milliseconds.
Shared runners are noisier than the snapshot, so treat small differences as
noise.

| Runner | Text extraction | Merge | End to end | Raster cold | Raster warm | PNG encode |
|---|---:|---:|---:|---:|---:|---:|
| Linux x86_64, AMD EPYC 9V74, 4 vCPU | **166.4** / 208.2 | **29.7** / 97.7 | **322.1** / 628.2 | 280.8 / **221.0** | 266.3 / **102.2** | **39.0** / 401.7 |
| Linux aarch64, 4 vCPU | **189.0** / 267.8 | **37.8** / 111.3 | **359.3** / 744.4 | 278.2 / **220.9** | 265.1 / **99.8** | **78.9** / 526.5 |
| macOS arm64, Apple M1 (virtual), 3 vCPU | **140.8** / 509.8 | **32.2** / 131.1 | **225.1** / 611.0 | **175.8** / 187.3 | 164.3 / **83.3** | **46.6** / 424.3 |
| macOS x86_64, Intel Core i7-8700B, 4 vCPU | **282.1** / 1307.9 | **67.8** / 398.6 | **553.4** / 823.1 | 461.9 / **351.6** | 440.1 / **156.3** | **60.8** / 473.1 |
| Windows Server 2025 x86_64, Intel, 4 vCPU | **245.0** / 277.8 | **53.8** / 130.9 | **593.0** / 909.8 | 521.5 / **307.0** | 505.3 / **160.1** | **61.8** / 599.2 |

pymupdf's text extraction was much slower on both macOS runners than on Linux
and Windows, so pylopdf's extraction lead on Linux and Windows is smaller than
in the macOS snapshot. MuPDF rasterized faster on every runner when warm, and on
every runner except the Apple M1 when cold. pylopdf still led end to end
because PNG encoding dominates pymupdf's total.

## Free-threaded extraction { #free-threaded-extraction }

Two independent copies of `bill-hr815.pdf`, all-page text extraction on
CPython 3.14.6 free-threaded for Windows 11. One warmup plus the median of seven
paired runs, alternating which mode runs first:

| Mode | Workers | Time | Speedup |
|---|---:|---:|---:|
| Sequential | 1 | 400.8 ms | 1.00× |
| Parallel | 2 | 235.5 ms | 1.70× |

Both copies produced exactly the same output in every run, and the interpreter
reported that the GIL remained disabled after import.

## Reproduce it { #reproduce }

The corpus lives in `tests/assets/real_world`; its sources and licenses are
recorded alongside the files.

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
uv run python tools/pyodide_compat.py --root . --benchmark-only \
  --benchmark-output .tmp/limits-benchmark.json
# With a free-threaded CPython 3.14 interpreter on Windows:
py -3.14t bench/free_threaded.py
# On POSIX:
python3.14t bench/free_threaded.py
```

The generated source report is committed at
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md).
The separately generated free-threaded report is committed at
[`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md);
rerunning `bench/run.py` cannot overwrite its CPython 3.14t evidence.
The native/Pyodide resource-policy baseline is committed separately at
[`bench/results/limits-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/limits-latest.md).
The second command measures bounded open/extract and controlled rejection.
CI runs the same cases inside Pyodide and records Wasm linear-memory growth;
those timing and memory values are trends, not native/Wasm performance claims.
When quoting a number, include the environment and corpus.
