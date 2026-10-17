# pylopdf benchmark results

- Run at: 2026-10-17 15:14 UTC
- Environment: macOS-27.0.1-arm64-arm-64bit-Mach-O / Python 3.14.8 / CPU Apple M2 Pro (12 logical CPUs)
- Versions: pylopdf 0.14.0, pymupdf 1.28.0, pypdf 6.15.0, pdfplumber 0.11.10
- Repetitions: one warmup + median of 7 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 70.6 | 291.7 | 404.8 | 4531.3 |
| bunka-kokugo-series-019-p4.pdf | 0.19 | 0.25 | 0.30 | 0.47 |
| f1040.pdf | 8.8 | 30.0 | 69.4 | 223.6 |
| mhlw-doc.pdf | 7.9 | 9.2 | 36.0 | 71.2 |
| nics-background-checks-2015-11.pdf | 4.9 | 7.5 | 52.0 | 119.5 |
| patent-us223898.pdf | 16.8 | 11.3 | 40.3 | 164.8 |
| pdf20-simple.pdf | 0.21 | 0.59 | 0.56 | 0.82 |
| pdfium-jpx-lzw.pdf | 0.11 | 0.26 | 0.16 | 0.31 |
| pdfium-links-highlights-annots.pdf | 0.29 | 0.75 | 0.32 | 1.3 |
| pdfium-smask-blend.pdf | 0.10 | 0.26 | 0.19 | 0.55 |
| pdfium-type3.pdf | 0.12 | 0.27 | 0.29 | 0.50 |
| senate-expenditures.pdf | 2.6 | 6.8 | 49.4 | 110.0 |
| usrguide.pdf | 27.6 | 63.1 | 282.3 | 785.5 |
| wdl6812-manuscript.pdf | 0.21 | 0.63 | 0.57 | 0.95 |
| Total | 140.4 | 422.5 | 936.5 | 6010.8 |

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
| merge x14 | 32.8 | 116.2 | 145.4 |

## Rendering end to end (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 19.5 | 58.0 |
| bunka-kokugo-series-019-p4.pdf | 21.0 | 84.9 |
| f1040.pdf | 25.2 | 67.5 |
| mhlw-doc.pdf | 18.8 | 50.4 |
| nics-background-checks-2015-11.pdf | 29.1 | 53.3 |
| patent-us223898.pdf | 15.7 | 52.2 |
| pdf20-simple.pdf | 3.4 | 13.1 |
| pdfium-jpx-lzw.pdf | 10.4 | 38.5 |
| pdfium-links-highlights-annots.pdf | 6.1 | 25.7 |
| pdfium-smask-blend.pdf | 0.92 | 3.5 |
| pdfium-type3.pdf | 0.66 | 1.9 |
| senate-expenditures.pdf | 29.6 | 39.7 |
| usrguide.pdf | 15.7 | 38.5 |
| wdl6812-manuscript.pdf | 20.2 | 64.1 |
| Total | 216.1 | 591.3 |

Each run opens a fresh document, rasterizes the first page at 2x, and encodes
PNG with library defaults. The next two tables split that work.

## Rasterization only (first page to a 2x Pixmap, ms)

| File | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---|---|---|---|
| bill-hr815.pdf | 13.5 | 8.5 | 12.5 | 4.0 |
| bunka-kokugo-series-019-p4.pdf | 16.7 | 49.8 | 16.5 | 14.6 |
| f1040.pdf | 19.7 | 26.4 | 16.3 | 15.6 |
| mhlw-doc.pdf | 14.5 | 13.1 | 13.8 | 3.5 |
| nics-background-checks-2015-11.pdf | 23.1 | 7.4 | 22.6 | 5.6 |
| patent-us223898.pdf | 11.6 | 22.0 | 11.5 | 11.4 |
| pdf20-simple.pdf | 2.1 | 0.78 | 1.9 | 0.32 |
| pdfium-jpx-lzw.pdf | 8.8 | 14.9 | 8.6 | 9.7 |
| pdfium-links-highlights-annots.pdf | 4.4 | 2.1 | 4.0 | 1.2 |
| pdfium-smask-blend.pdf | 0.79 | 0.62 | 0.48 | 0.39 |
| pdfium-type3.pdf | 0.59 | 0.27 | 0.29 | 0.04 |
| senate-expenditures.pdf | 24.8 | 5.4 | 24.4 | 3.6 |
| usrguide.pdf | 12.6 | 8.1 | 11.4 | 0.71 |
| wdl6812-manuscript.pdf | 16.8 | 21.8 | 16.5 | 10.6 |
| Total | 169.9 | 181.1 | 160.8 | 81.2 |

Cold runs open a fresh document from bytes for every repetition; warm runs
rasterize the same page of one open document again. Caches that outlive a
document, such as process-wide glyph caches, still apply to cold runs.
pylopdf returns straight-alpha RGBA8 on a transparent background; pymupdf
returns RGB on white.

## PNG encoding only (2x first-page Pixmap)

| File | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---|---|---|---|
| bill-hr815.pdf | 5.8 | 51.1 | 328.3 | 276.1 |
| bunka-kokugo-series-019-p4.pdf | 4.2 | 33.7 | 438.8 | 249.4 |
| f1040.pdf | 5.8 | 39.9 | 841.3 | 360.9 |
| mhlw-doc.pdf | 4.2 | 37.3 | 340.5 | 301.4 |
| nics-background-checks-2015-11.pdf | 6.0 | 46.1 | 737.1 | 343.0 |
| patent-us223898.pdf | 3.9 | 28.6 | 245.0 | 154.6 |
| pdf20-simple.pdf | 1.1 | 12.5 | 28.4 | 14.6 |
| pdfium-jpx-lzw.pdf | 1.4 | 23.7 | 39.9 | 9.4 |
| pdfium-links-highlights-annots.pdf | 1.7 | 23.9 | 53.5 | 21.5 |
| pdfium-smask-blend.pdf | 0.12 | 2.8 | 4.2 | 2.6 |
| pdfium-type3.pdf | 0.11 | 1.5 | 3.5 | 1.1 |
| senate-expenditures.pdf | 4.1 | 33.7 | 332.8 | 253.8 |
| usrguide.pdf | 3.4 | 30.5 | 188.8 | 138.0 |
| wdl6812-manuscript.pdf | 3.5 | 42.0 | 1062.9 | 1191.3 |
| Total | 45.4 | 407.4 | 4645.1 | 3317.7 |

Both encoders receive an already rendered Pixmap. pylopdf writes RGBA8 with
fast fdeflate compression; pymupdf writes RGB with zlib's default level.
KiB is 1,024 bytes of encoded PNG.

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 165.3 | 1.00x |
| 2 | 89.8 | 1.84x |
| 4 | 48.6 | 3.40x |
| 8 | 34.9 | 4.74x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.
For reference, pymupdf renders and encodes the same pages sequentially on one fresh document in 423.1 ms.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
