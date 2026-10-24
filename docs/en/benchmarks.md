---
title: Benchmarks
description: Reproducible pylopdf benchmarks for extraction, merging, rasterization and PNG encoding, with wins and losses published together.
---

# Benchmarks

These benchmarks compare pylopdf with pymupdf, pypdf, and pdfplumber on 14
real-world PDFs, and show the cases where pylopdf is slower as well as those
where it is faster. They come from one machine and one corpus, so treat them
as a guide to what to measure on your own documents, not as a ranking.

!!! info "Latest run"
    **2026-10-24 00:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro (arm64)<br>
    pylopdf built with the thin-LTO release profile from the Rendering milestone branch (hayro 0.8.0) · pymupdf 2.0 (MuPDF 1.28.5) · pypdf 6.21.1 · pdfplumber 0.11.10<br>
    One warm-up plus seven measured runs; tables show median milliseconds.

## At a glance { #overview }

| Workload | What the latest corpus shows |
|---|---|
| Merge 14 real-world PDFs | pylopdf **19.7 ms**, pymupdf 136.0 ms, pypdf 120.7 ms |
| First page to 2× PNG, end to end | pylopdf led on all 14 files, **143.5 ms** versus 514.5 ms in total |
| Rasterize only | pylopdf led on 10 of 14 files cold, with 2 ties, and on 5 warm; totals were **103.4** / 186.1 ms cold and 87.8 / **81.0** ms warm (pylopdf / pymupdf) |
| Encode PNG | pylopdf encoded about 8× faster (**38.6 ms** versus 319.0 ms) but wrote 55% more bytes (5,132 versus **3,318 KiB**) |
| Render 12 pages at 2× | `doc.render()` went from 109.4 ms with 1 worker to 33.1 ms with 8, a **3.30× speedup**; pymupdf took 332.7 ms sequentially |
| Extract all text | pylopdf led on all 14 files, **117.1 ms** versus 229.7 ms in total |
| Extraction fidelity proxy | Similarity ranged from 0.443 to 1.000 where both libraries extract text; pylopdf extracts no text from the Type 3 fixture |
| Linux and Windows CI runners | Measured on pylopdf 0.14.0; pylopdf's extraction lead was smaller, and MuPDF also rasterized faster cold; see [Other platforms](#other-platforms) |

## Text extraction { #text-extraction }

All pages, milliseconds; lower is faster.

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **59.4** | 127.5 | 376.9 | 4865.3 |
| bunka-kokugo-series-019-p4.pdf | **0.05** | 0.29 | 0.44 | 0.52 |
| f1040.pdf | **17.3** | 34.6 | 156.1 | 414.1 |
| mhlw-doc.pdf | **9.6** | 9.9 | 49.9 | 76.9 |
| nics-background-checks-2015-11.pdf | **3.4** | 5.6 | 56.8 | 131.1 |
| patent-us223898.pdf | **4.7** | 6.1 | 46.6 | 168.3 |
| pdf20-simple.pdf | **0.05** | 0.48 | 0.69 | 0.84 |
| pdfium-jpx-lzw.pdf | **0.02** | 0.28 | 0.25 | 0.34 |
| pdfium-links-highlights-annots.pdf | **0.12** | 0.74 | 0.45 | 1.4 |
| pdfium-smask-blend.pdf | **0.04** | 0.31 | 0.29 | 0.55 |
| pdfium-type3.pdf | **0.03** | 0.32 | 0.39 | 0.50 |
| senate-expenditures.pdf | **2.4** | 4.7 | 52.0 | 114.2 |
| usrguide.pdf | **19.7** | 38.1 | 312.9 | 821.2 |
| wdl6812-manuscript.pdf | **0.21** | 0.68 | 0.77 | 0.97 |
| **Total** | **117.1** | 229.7 | 1054.4 | 6596.2 |

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
| Merge all 14 corpus files | **19.7** | 136.0 | 120.7 |

## Rendering { #rendering }

First page at 2×, milliseconds; lower is faster. Both libraries use their
defaults: opaque RGB on white.

### End to end { #rendering-end-to-end }

Each run opens a fresh document, rasterizes the page, and encodes PNG.

| File | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **13.2** | 45.4 |
| bunka-kokugo-series-019-p4.pdf | **15.8** | 78.5 |
| f1040.pdf | **18.5** | 62.1 |
| mhlw-doc.pdf | **13.3** | 41.9 |
| nics-background-checks-2015-11.pdf | **16.7** | 44.0 |
| patent-us223898.pdf | **10.4** | 43.6 |
| pdf20-simple.pdf | **1.6** | 9.2 |
| pdfium-jpx-lzw.pdf | **7.3** | 30.3 |
| pdfium-links-highlights-annots.pdf | **2.8** | 16.7 |
| pdfium-smask-blend.pdf | **0.34** | 2.6 |
| pdfium-type3.pdf | **0.24** | 1.2 |
| senate-expenditures.pdf | **15.5** | 32.9 |
| usrguide.pdf | **12.5** | 39.1 |
| wdl6812-manuscript.pdf | **15.3** | 66.8 |
| **Total** | **143.5** | 514.5 |

Most of this lead comes from PNG encoding, not rasterization. The next two
tables split the same work.

### Rasterization { #rasterization }

Cold runs open a fresh document for every repetition; warm runs rasterize the
same page of a document that is already open. Caches that outlive a document,
such as MuPDF's process-wide glyph cache, still apply to cold runs. pylopdf
keeps one hayro render cache per document on the thread that opened it, so
fonts, glyph outlines, and images are parsed once; MuPDF also keeps rendered
glyph bitmaps, which is why it still leads warm on text-heavy pages
(`usrguide.pdf` 4.5 against 0.74 ms) while pylopdf leads on scanned and
image-heavy ones. The glyph cache that would close that gap is a scope
question upstream, [LaurenzV/hayro#1613](https://github.com/LaurenzV/hayro/issues/1613),
and skipping the crop-box clip that covers the whole page,
[LaurenzV/hayro#1612](https://github.com/LaurenzV/hayro/pull/1612), takes 9%
off warm totals in hayro alone.

| File | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **7.8** | **7.8** | 6.4 | **3.2** |
| bunka-kokugo-series-019-p4.pdf | **12.3** | 51.7 | **12.0** | 15.0 |
| f1040.pdf | **13.1** | 28.3 | **8.8** | 16.8 |
| mhlw-doc.pdf | **9.7** | 12.1 | 5.4 | **3.0** |
| nics-background-checks-2015-11.pdf | 11.3 | **8.0** | 9.8 | **4.8** |
| patent-us223898.pdf | **7.0** | 22.2 | **7.1** | 11.7 |
| pdf20-simple.pdf | **0.65** | 0.82 | 0.60 | **0.28** |
| pdfium-jpx-lzw.pdf | **6.0** | 14.9 | **5.8** | 9.9 |
| pdfium-links-highlights-annots.pdf | **1.4** | **1.4** | 1.2 | **0.56** |
| pdfium-smask-blend.pdf | **0.23** | 0.56 | **0.19** | 0.32 |
| pdfium-type3.pdf | **0.14** | 0.30 | 0.10 | **0.04** |
| senate-expenditures.pdf | 12.1 | **5.5** | 11.3 | **3.6** |
| usrguide.pdf | **8.8** | 9.6 | 4.5 | **0.74** |
| wdl6812-manuscript.pdf | **12.9** | 23.0 | 14.6 | **11.0** |
| **Total** | **103.4** | 186.1 | 87.8 | **81.0** |

### PNG encoding { #png-encoding }

Both encoders receive an already rendered raster. pylopdf uses fast fdeflate
compression, trading file size for speed; pymupdf uses zlib's default level. Recompress pylopdf output when file size matters
more than encoding time.

| File | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.8** | 36.8 | 535.8 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **3.5** | 25.9 | 420.7 | **249.4** |
| f1040.pdf | **5.0** | 34.0 | 816.8 | **360.9** |
| mhlw-doc.pdf | **3.8** | 30.0 | 581.9 | **301.4** |
| nics-background-checks-2015-11.pdf | **5.0** | 35.0 | 689.9 | **343.0** |
| patent-us223898.pdf | **3.2** | 21.1 | 232.4 | **154.6** |
| pdf20-simple.pdf | **0.85** | 8.1 | 27.2 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.1** | 15.3 | 31.8 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.2** | 15.0 | 54.9 | **21.5** |
| pdfium-smask-blend.pdf | **0.10** | 2.0 | 3.4 | **2.6** |
| pdfium-type3.pdf | **0.08** | 0.90 | 3.0 | **1.1** |
| senate-expenditures.pdf | **3.4** | 27.8 | 578.7 | **253.8** |
| usrguide.pdf | **2.8** | 22.6 | 253.3 | **138.0** |
| wdl6812-manuscript.pdf | **2.8** | 44.6 | **901.7** | 1191.3 |
| **Total** | **38.6** | 319.0 | 5131.6 | **3317.7** |

## Parallel rendering { #parallel-rendering }

First 12 pages of `usrguide.pdf` to 2× PNG, milliseconds; lower is faster.
The batch preserves input order and uses one immutable document snapshot.

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 109.4 | 1.00x |
| 2 | 63.3 | 1.73x |
| 4 | 45.5 | 2.41x |
| 8 | 33.1 | 3.30x |

Actual concurrency is bounded by both the requested worker count and an
estimated 512 MB of live rendering memory. For reference, pymupdf rendered and
encoded the same 12 pages sequentially on one fresh document in 332.7 ms.

## Other platforms { #other-platforms }

These runs measured pylopdf 0.14.0 on hayro 0.7, before the retained render
cache; the macOS snapshot above is newer.

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

The corpus is in
[`tests/assets/real_world`](https://github.com/yhay81/pylopdf/tree/main/tests/assets/real_world),
with each file's source and license. From a clone of the repository:

```bash
uv sync --all-extras --group bench
uv run python bench/run.py                 # writes bench/results/latest.md
python3.14t bench/free_threaded.py         # needs free-threaded CPython 3.14
```

The latest reports are committed as
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)
and
[`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md).
When you quote a number, include the environment and corpus it was measured
on.
