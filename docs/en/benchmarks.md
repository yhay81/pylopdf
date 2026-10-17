---
title: Benchmarks
description: Reproducible pylopdf benchmarks for extraction, merging, rasterization and PNG encoding, with wins and losses published together.
---

# Benchmarks

pylopdf publishes **wins and losses together**. These measurements are a
snapshot of one machine and corpus—not a universal ranking. Use them to choose
what to measure in your own workload.

!!! info "Latest run"
    **2026-10-17 17:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro (arm64)<br>
    pylopdf 0.14.0 built with the thin-LTO release profile · pymupdf 1.28.0 · pypdf 6.15.0 · pdfplumber 0.11.10<br>
    One warm-up plus seven measured runs; tables show median milliseconds.

## At a glance { #overview }

| Workload | What the latest corpus shows |
|---|---|
| Merge 14 real-world PDFs | pylopdf **15.4 ms**, pymupdf 111.2 ms, pypdf 141.5 ms |
| First page to 2× PNG, end to end | pylopdf led on all 14 files, **212.9 ms** versus 573.9 ms in total |
| Rasterize only | pymupdf led on 9 of 14 files cold and 12 of 14 warm, with one warm tie; warm totals were 145.9 ms versus **78.8 ms** |
| Encode PNG | pylopdf encoded about 9× faster (**44.4 ms** versus 397.4 ms) but wrote 40% more bytes (4,645 versus **3,318 KiB**) |
| Render 12 pages at 2× | `render_pages()` went from 163.7 ms with 1 worker to 34.6 ms with 8, a **4.74× speedup**; pymupdf took 425.3 ms sequentially |
| Extract all text | pylopdf led on 12 of 14 files; pymupdf and pypdf each led on one |
| Extraction fidelity proxy | Similarity ranged from 0.443 to 1.000 where both libraries extract text; pylopdf extracts no text from the Type 3 fixture |
| Linux and Windows CI runners | pylopdf's extraction lead was smaller, and MuPDF also rasterized faster cold; see [Other platforms](#other-platforms) |

## Text extraction { #text-extraction }

All pages, milliseconds; lower is faster.

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **71.3** | 298.5 | 407.8 | 4295.0 |
| bunka-kokugo-series-019-p4.pdf | **0.17** | 0.25 | 0.30 | 0.45 |
| f1040.pdf | **8.9** | 28.2 | 68.6 | 201.8 |
| mhlw-doc.pdf | **8.0** | 9.1 | 34.0 | 70.1 |
| nics-background-checks-2015-11.pdf | **4.8** | 7.5 | 51.4 | 119.7 |
| patent-us223898.pdf | 16.9 | **11.3** | 39.6 | 165.3 |
| pdf20-simple.pdf | **0.17** | 0.62 | 0.54 | 0.80 |
| pdfium-jpx-lzw.pdf | **0.14** | 0.26 | 0.16 | 0.31 |
| pdfium-links-highlights-annots.pdf | 0.33 | 0.69 | **0.32** | 1.4 |
| pdfium-smask-blend.pdf | **0.07** | 0.27 | 0.18 | 0.51 |
| pdfium-type3.pdf | **0.08** | 0.29 | 0.29 | 0.48 |
| senate-expenditures.pdf | **2.8** | 6.6 | 49.9 | 110.3 |
| usrguide.pdf | **27.4** | 64.5 | 279.1 | 799.3 |
| wdl6812-manuscript.pdf | **0.15** | 0.58 | 0.53 | 0.93 |
| **Total** | **141.2** | 428.6 | 932.6 | 5766.4 |

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
| Merge all 14 corpus files | **15.4** | 111.2 | 141.5 |

## Rendering { #rendering }

First page at 2×, milliseconds; lower is faster. Both libraries use their
defaults: pylopdf produces straight-alpha RGBA8 on a transparent background,
and pymupdf produces RGB on white.

### End to end { #rendering-end-to-end }

Each run opens a fresh document, rasterizes the page, and encodes PNG.

| File | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **19.4** | 57.0 |
| bunka-kokugo-series-019-p4.pdf | **19.6** | 82.9 |
| f1040.pdf | **25.7** | 65.3 |
| mhlw-doc.pdf | **18.4** | 48.5 |
| nics-background-checks-2015-11.pdf | **28.8** | 50.6 |
| patent-us223898.pdf | **15.4** | 50.4 |
| pdf20-simple.pdf | **3.3** | 13.4 |
| pdfium-jpx-lzw.pdf | **10.0** | 37.3 |
| pdfium-links-highlights-annots.pdf | **6.0** | 26.1 |
| pdfium-smask-blend.pdf | **0.77** | 3.2 |
| pdfium-type3.pdf | **0.55** | 1.7 |
| senate-expenditures.pdf | **28.9** | 38.3 |
| usrguide.pdf | **15.9** | 37.0 |
| wdl6812-manuscript.pdf | **20.1** | 62.3 |
| **Total** | **212.9** | 573.9 |

Most of this lead comes from PNG encoding, not rasterization. The next two
tables split the same work.

### Rasterization { #rasterization }

Cold runs open a fresh document for every repetition; warm runs rasterize the
same page of a document that is already open. Caches that outlive a document,
such as MuPDF's process-wide glyph cache, still apply to cold runs. MuPDF also
keeps loaded fonts and rendered glyphs for an open document, which matters most
on text-heavy pages. pylopdf reuses the fonts and glyph outlines it parsed on
the thread that opened the document, but it still rasterizes every glyph and
decodes every image again, so its warm runs gain far less than MuPDF's.

| File | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 12.9 | **8.3** | 11.5 | **4.0** |
| bunka-kokugo-series-019-p4.pdf | **15.2** | 49.6 | **14.4** | **14.4** |
| f1040.pdf | **19.3** | 26.1 | 15.8 | **15.6** |
| mhlw-doc.pdf | 14.0 | **12.5** | 9.8 | **3.3** |
| nics-background-checks-2015-11.pdf | 22.9 | **7.3** | 21.5 | **4.8** |
| patent-us223898.pdf | **11.3** | 21.9 | 11.0 | **10.9** |
| pdf20-simple.pdf | 2.1 | **0.71** | 2.0 | **0.26** |
| pdfium-jpx-lzw.pdf | **8.5** | 14.2 | **8.1** | 9.5 |
| pdfium-links-highlights-annots.pdf | 4.3 | **2.0** | 3.9 | **1.2** |
| pdfium-smask-blend.pdf | 0.60 | **0.57** | 0.47 | **0.36** |
| pdfium-type3.pdf | 0.42 | **0.25** | 0.26 | **0.03** |
| senate-expenditures.pdf | 24.4 | **5.0** | 23.1 | **3.3** |
| usrguide.pdf | 12.5 | **7.4** | 8.0 | **0.67** |
| wdl6812-manuscript.pdf | **16.4** | 21.1 | 16.2 | **10.7** |
| **Total** | **164.7** | 177.1 | 145.9 | **78.8** |

### PNG encoding { #png-encoding }

Both encoders receive an already rendered raster. pylopdf uses fast fdeflate
compression for RGBA8 output, trading file size for speed; pymupdf uses zlib's
default level for RGB output. Recompress pylopdf output when file size matters
more than encoding time.

| File | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.7** | 49.2 | 328.3 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **4.0** | 33.1 | 438.8 | **249.4** |
| f1040.pdf | **5.8** | 39.5 | 841.3 | **360.9** |
| mhlw-doc.pdf | **4.1** | 36.2 | 340.5 | **301.4** |
| nics-background-checks-2015-11.pdf | **5.8** | 43.0 | 737.1 | **343.0** |
| patent-us223898.pdf | **3.9** | 27.7 | 245.0 | **154.6** |
| pdf20-simple.pdf | **1.1** | 12.0 | 28.4 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.3** | 23.1 | 39.9 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.6** | 23.8 | 53.5 | **21.5** |
| pdfium-smask-blend.pdf | **0.12** | 2.6 | 4.2 | **2.6** |
| pdfium-type3.pdf | **0.10** | 1.4 | 3.5 | **1.1** |
| senate-expenditures.pdf | **4.0** | 33.1 | 332.8 | **253.8** |
| usrguide.pdf | **3.3** | 29.5 | 188.8 | **138.0** |
| wdl6812-manuscript.pdf | **3.6** | 43.4 | **1062.9** | 1191.3 |
| **Total** | **44.4** | 397.4 | 4645.1 | **3317.7** |

## Parallel rendering { #parallel-rendering }

First 12 pages of `usrguide.pdf` to 2× PNG, milliseconds; lower is faster.
The batch preserves input order and uses one immutable document snapshot.

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 163.7 | 1.00× |
| 2 | 88.4 | 1.85× |
| 4 | 47.4 | 3.45× |
| 8 | 34.6 | 4.74× |

Actual concurrency is bounded by both the requested worker count and an
estimated 512 MB of live rendering memory. For reference, pymupdf rendered and
encoded the same 12 pages sequentially on one fresh document in 425.3 ms.

## Other platforms { #other-platforms }

The snapshot above comes from one macOS arm64 machine. On 2026-10-17 the same
`bench/run.py` ran once on each GitHub-hosted release runner with the thin-LTO
abi3 wheel (Python 3.14.8, or 3.14.7 on macOS x86_64; one warm-up plus five
measured runs). Cells show pylopdf / pymupdf corpus totals in milliseconds.
Shared runners are noisier than the snapshot, so treat small differences as
noise.

| Runner | Text extraction | Merge | End to end | Raster cold | Raster warm | PNG encode |
|---|---:|---:|---:|---:|---:|---:|
| Linux x86_64, AMD EPYC 9V45, 4 vCPU | **119.7** / 146.8 | **14.3** / 73.8 | **234.8** / 459.9 | 198.1 / **167.1** | 182.9 / **72.9** | **33.4** / 284.7 |
| Linux aarch64, 4 vCPU | **186.0** / 270.3 | **27.5** / 111.7 | **372.1** / 720.7 | 289.0 / **226.1** | 264.2 / **101.3** | **81.4** / 505.7 |
| macOS arm64, Apple M1 (virtual), 3 vCPU | **171.3** / 601.1 | **22.4** / 179.7 | **290.4** / 772.1 | **220.2** / 251.7 | 204.3 / **110.3** | **57.9** / 539.1 |
| macOS x86_64, Intel Core i7-8700B, 4 vCPU | **254.7** / 1188.0 | **87.0** / 430.4 | **651.3** / 922.2 | 535.8 / **392.0** | 444.7 / **178.6** | **62.2** / 490.5 |
| Windows Server 2025 x86_64, Intel, 4 vCPU | **243.1** / 269.7 | **37.5** / 132.9 | **533.2** / 874.6 | 470.5 / **293.7** | 434.2 / **147.5** | **57.8** / 579.6 |

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
