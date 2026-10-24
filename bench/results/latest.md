# pylopdf benchmark results

- Run at: 2026-10-24 00:29 UTC
- Environment: macOS-27.0.1-arm64-arm-64bit-Mach-O / Python 3.14.8 / CPU Apple M2 Pro (12 logical CPUs)
- Versions: pylopdf 0.15.0, pymupdf 2.0, pypdf 6.21.1, pdfplumber 0.11.10
- Repetitions: one warmup + median of 7 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 59.4 | 127.5 | 376.9 | 4865.3 |
| bunka-kokugo-series-019-p4.pdf | 0.05 | 0.29 | 0.44 | 0.52 |
| f1040.pdf | 17.3 | 34.6 | 156.1 | 414.1 |
| mhlw-doc.pdf | 9.6 | 9.9 | 49.9 | 76.9 |
| nics-background-checks-2015-11.pdf | 3.4 | 5.6 | 56.8 | 131.1 |
| patent-us223898.pdf | 4.7 | 6.1 | 46.6 | 168.3 |
| pdf20-simple.pdf | 0.05 | 0.48 | 0.69 | 0.84 |
| pdfium-jpx-lzw.pdf | 0.02 | 0.28 | 0.25 | 0.34 |
| pdfium-links-highlights-annots.pdf | 0.12 | 0.74 | 0.45 | 1.4 |
| pdfium-smask-blend.pdf | 0.04 | 0.31 | 0.29 | 0.55 |
| pdfium-type3.pdf | 0.03 | 0.32 | 0.39 | 0.50 |
| senate-expenditures.pdf | 2.4 | 4.7 | 52.0 | 114.2 |
| usrguide.pdf | 19.7 | 38.1 | 312.9 | 821.2 |
| wdl6812-manuscript.pdf | 0.21 | 0.68 | 0.77 | 0.97 |
| Total | 117.1 | 229.7 | 1054.4 | 6596.2 |

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
| merge x14 | 19.7 | 136.0 | 120.7 |

## Rendering end to end (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 13.2 | 45.4 |
| bunka-kokugo-series-019-p4.pdf | 15.8 | 78.5 |
| f1040.pdf | 18.5 | 62.1 |
| mhlw-doc.pdf | 13.3 | 41.9 |
| nics-background-checks-2015-11.pdf | 16.7 | 44.0 |
| patent-us223898.pdf | 10.4 | 43.6 |
| pdf20-simple.pdf | 1.6 | 9.2 |
| pdfium-jpx-lzw.pdf | 7.3 | 30.3 |
| pdfium-links-highlights-annots.pdf | 2.8 | 16.7 |
| pdfium-smask-blend.pdf | 0.34 | 2.6 |
| pdfium-type3.pdf | 0.24 | 1.2 |
| senate-expenditures.pdf | 15.5 | 32.9 |
| usrguide.pdf | 12.5 | 39.1 |
| wdl6812-manuscript.pdf | 15.3 | 66.8 |
| Total | 143.5 | 514.5 |

Each run opens a fresh document, rasterizes the first page at 2x, and encodes
PNG with library defaults. The next two tables split that work.

## Rasterization only (first page to a 2x Pixmap, ms)

| File | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---|---|---|---|
| bill-hr815.pdf | 7.8 | 7.8 | 6.4 | 3.2 |
| bunka-kokugo-series-019-p4.pdf | 12.3 | 51.7 | 12.0 | 15.0 |
| f1040.pdf | 13.1 | 28.3 | 8.8 | 16.8 |
| mhlw-doc.pdf | 9.7 | 12.1 | 5.4 | 3.0 |
| nics-background-checks-2015-11.pdf | 11.3 | 8.0 | 9.8 | 4.8 |
| patent-us223898.pdf | 7.0 | 22.2 | 7.1 | 11.7 |
| pdf20-simple.pdf | 0.65 | 0.82 | 0.60 | 0.28 |
| pdfium-jpx-lzw.pdf | 6.0 | 14.9 | 5.8 | 9.9 |
| pdfium-links-highlights-annots.pdf | 1.4 | 1.4 | 1.2 | 0.56 |
| pdfium-smask-blend.pdf | 0.23 | 0.56 | 0.19 | 0.32 |
| pdfium-type3.pdf | 0.14 | 0.30 | 0.10 | 0.04 |
| senate-expenditures.pdf | 12.1 | 5.5 | 11.3 | 3.6 |
| usrguide.pdf | 8.8 | 9.6 | 4.5 | 0.74 |
| wdl6812-manuscript.pdf | 12.9 | 23.0 | 14.6 | 11.0 |
| Total | 103.4 | 186.1 | 87.8 | 81.0 |

Cold runs open a fresh document from bytes for every repetition; warm runs
rasterize the same page of one open document again. Caches that outlive a
document, such as process-wide glyph caches, still apply to cold runs.
Both libraries return opaque RGB on white by default.

## PNG encoding only (2x first-page Pixmap)

| File | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---|---|---|---|
| bill-hr815.pdf | 5.8 | 36.8 | 535.8 | 276.1 |
| bunka-kokugo-series-019-p4.pdf | 3.5 | 25.9 | 420.7 | 249.4 |
| f1040.pdf | 5.0 | 34.0 | 816.8 | 360.9 |
| mhlw-doc.pdf | 3.8 | 30.0 | 581.9 | 301.4 |
| nics-background-checks-2015-11.pdf | 5.0 | 35.0 | 689.9 | 343.0 |
| patent-us223898.pdf | 3.2 | 21.1 | 232.4 | 154.6 |
| pdf20-simple.pdf | 0.85 | 8.1 | 27.2 | 14.6 |
| pdfium-jpx-lzw.pdf | 1.1 | 15.3 | 31.8 | 9.4 |
| pdfium-links-highlights-annots.pdf | 1.2 | 15.0 | 54.9 | 21.5 |
| pdfium-smask-blend.pdf | 0.10 | 2.0 | 3.4 | 2.6 |
| pdfium-type3.pdf | 0.08 | 0.90 | 3.0 | 1.1 |
| senate-expenditures.pdf | 3.4 | 27.8 | 578.7 | 253.8 |
| usrguide.pdf | 2.8 | 22.6 | 253.3 | 138.0 |
| wdl6812-manuscript.pdf | 2.8 | 44.6 | 901.7 | 1191.3 |
| Total | 38.6 | 319.0 | 5131.6 | 3317.7 |

Both encoders receive an already rendered RGB Pixmap. pylopdf writes it with
fast fdeflate compression; pymupdf with zlib's default level.
KiB is 1,024 bytes of encoded PNG.

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 109.4 | 1.00x |
| 2 | 63.3 | 1.73x |
| 4 | 45.5 | 2.41x |
| 8 | 33.1 | 3.30x |

`Document.render()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.
For reference, pymupdf renders and encodes the same pages sequentially on one fresh document in 332.7 ms.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
