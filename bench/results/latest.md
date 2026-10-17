# pylopdf benchmark results

- Run at: 2026-10-17 17:29 UTC
- Environment: macOS-27.0.1-arm64-arm-64bit-Mach-O / Python 3.14.8 / CPU Apple M2 Pro (12 logical CPUs)
- Versions: pylopdf 0.14.0, pymupdf 1.28.0, pypdf 6.15.0, pdfplumber 0.11.10
- Repetitions: one warmup + median of 7 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 71.3 | 298.5 | 407.8 | 4295.0 |
| bunka-kokugo-series-019-p4.pdf | 0.17 | 0.25 | 0.30 | 0.45 |
| f1040.pdf | 8.9 | 28.2 | 68.6 | 201.8 |
| mhlw-doc.pdf | 8.0 | 9.1 | 34.0 | 70.1 |
| nics-background-checks-2015-11.pdf | 4.8 | 7.5 | 51.4 | 119.7 |
| patent-us223898.pdf | 16.9 | 11.3 | 39.6 | 165.3 |
| pdf20-simple.pdf | 0.17 | 0.62 | 0.54 | 0.80 |
| pdfium-jpx-lzw.pdf | 0.14 | 0.26 | 0.16 | 0.31 |
| pdfium-links-highlights-annots.pdf | 0.33 | 0.69 | 0.32 | 1.4 |
| pdfium-smask-blend.pdf | 0.07 | 0.27 | 0.18 | 0.51 |
| pdfium-type3.pdf | 0.08 | 0.29 | 0.29 | 0.48 |
| senate-expenditures.pdf | 2.8 | 6.6 | 49.9 | 110.3 |
| usrguide.pdf | 27.4 | 64.5 | 279.1 | 799.3 |
| wdl6812-manuscript.pdf | 0.15 | 0.58 | 0.53 | 0.93 |
| Total | 141.2 | 428.6 | 932.6 | 5766.4 |

## Extracted-content comparison (quality proxy)

| File | pylopdf characters | pymupdf characters | Similarity after whitespace normalization |
|---|---|---|---|
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

Similarity approaches 1.0 as output converges with PyMuPDF.
Low scores can reflect the PyMuPDF reference convention rather than pylopdf quality:
on rotated table reports PyMuPDF emits unsorted content order (its own sort=True
scores lower still there), and on dense forms pylopdf matches PyMuPDF's sorted
mode while the default reference stays in content order.
A zero-character row is image-only with no text layer, so zero is correct for both.

## Merge (all corpus files into one document, ms)

| Task | pylopdf | pymupdf | pypdf |
|---|---|---|---|
| merge x14 | 15.4 | 111.2 | 141.5 |

## Rendering end to end (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 19.4 | 57.0 |
| bunka-kokugo-series-019-p4.pdf | 19.6 | 82.9 |
| f1040.pdf | 25.7 | 65.3 |
| mhlw-doc.pdf | 18.4 | 48.5 |
| nics-background-checks-2015-11.pdf | 28.8 | 50.6 |
| patent-us223898.pdf | 15.4 | 50.4 |
| pdf20-simple.pdf | 3.3 | 13.4 |
| pdfium-jpx-lzw.pdf | 10.0 | 37.3 |
| pdfium-links-highlights-annots.pdf | 6.0 | 26.1 |
| pdfium-smask-blend.pdf | 0.77 | 3.2 |
| pdfium-type3.pdf | 0.55 | 1.7 |
| senate-expenditures.pdf | 28.9 | 38.3 |
| usrguide.pdf | 15.9 | 37.0 |
| wdl6812-manuscript.pdf | 20.1 | 62.3 |
| Total | 212.9 | 573.9 |

Each run opens a fresh document, rasterizes the first page at 2x, and encodes
PNG with library defaults. The next two tables split that work.

## Rasterization only (first page to a 2x Pixmap, ms)

| File | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---|---|---|---|
| bill-hr815.pdf | 12.9 | 8.3 | 11.5 | 4.0 |
| bunka-kokugo-series-019-p4.pdf | 15.2 | 49.6 | 14.4 | 14.4 |
| f1040.pdf | 19.3 | 26.1 | 15.8 | 15.6 |
| mhlw-doc.pdf | 14.0 | 12.5 | 9.8 | 3.3 |
| nics-background-checks-2015-11.pdf | 22.9 | 7.3 | 21.5 | 4.8 |
| patent-us223898.pdf | 11.3 | 21.9 | 11.0 | 10.9 |
| pdf20-simple.pdf | 2.1 | 0.71 | 2.0 | 0.26 |
| pdfium-jpx-lzw.pdf | 8.5 | 14.2 | 8.1 | 9.5 |
| pdfium-links-highlights-annots.pdf | 4.3 | 2.0 | 3.9 | 1.2 |
| pdfium-smask-blend.pdf | 0.60 | 0.57 | 0.47 | 0.36 |
| pdfium-type3.pdf | 0.42 | 0.25 | 0.26 | 0.03 |
| senate-expenditures.pdf | 24.4 | 5.0 | 23.1 | 3.3 |
| usrguide.pdf | 12.5 | 7.4 | 8.0 | 0.67 |
| wdl6812-manuscript.pdf | 16.4 | 21.1 | 16.2 | 10.7 |
| Total | 164.7 | 177.1 | 145.9 | 78.8 |

Cold runs open a fresh document from bytes for every repetition; warm runs
rasterize the same page of one open document again. Caches that outlive a
document, such as process-wide glyph caches, still apply to cold runs.
pylopdf returns straight-alpha RGBA8 on a transparent background; pymupdf
returns RGB on white.

## PNG encoding only (2x first-page Pixmap)

| File | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---|---|---|---|
| bill-hr815.pdf | 5.7 | 49.2 | 328.3 | 276.1 |
| bunka-kokugo-series-019-p4.pdf | 4.0 | 33.1 | 438.8 | 249.4 |
| f1040.pdf | 5.8 | 39.5 | 841.3 | 360.9 |
| mhlw-doc.pdf | 4.1 | 36.2 | 340.5 | 301.4 |
| nics-background-checks-2015-11.pdf | 5.8 | 43.0 | 737.1 | 343.0 |
| patent-us223898.pdf | 3.9 | 27.7 | 245.0 | 154.6 |
| pdf20-simple.pdf | 1.1 | 12.0 | 28.4 | 14.6 |
| pdfium-jpx-lzw.pdf | 1.3 | 23.1 | 39.9 | 9.4 |
| pdfium-links-highlights-annots.pdf | 1.6 | 23.8 | 53.5 | 21.5 |
| pdfium-smask-blend.pdf | 0.12 | 2.6 | 4.2 | 2.6 |
| pdfium-type3.pdf | 0.10 | 1.4 | 3.5 | 1.1 |
| senate-expenditures.pdf | 4.0 | 33.1 | 332.8 | 253.8 |
| usrguide.pdf | 3.3 | 29.5 | 188.8 | 138.0 |
| wdl6812-manuscript.pdf | 3.6 | 43.4 | 1062.9 | 1191.3 |
| Total | 44.4 | 397.4 | 4645.1 | 3317.7 |

Both encoders receive an already rendered Pixmap. pylopdf writes RGBA8 with
fast fdeflate compression; pymupdf writes RGB with zlib's default level.
KiB is 1,024 bytes of encoded PNG.

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 163.7 | 1.00x |
| 2 | 88.4 | 1.85x |
| 4 | 47.4 | 3.45x |
| 8 | 34.6 | 4.74x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.
For reference, pymupdf renders and encodes the same pages sequentially on one fresh document in 425.3 ms.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
