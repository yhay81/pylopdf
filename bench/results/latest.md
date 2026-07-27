# pylopdf benchmark results

- Run at: 2026-07-27 01:22 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.12.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 201.9 | 167.7 | 811.0 | 10994.2 |
| bunka-kokugo-series-019-p4.pdf | 0.6 | 0.9 | 1.0 | 1.9 |
| f1040.pdf | 23.9 | 34.5 | 208.5 | 761.5 |
| mhlw-doc.pdf | 22.7 | 14.1 | 106.2 | 198.0 |
| nics-background-checks-2015-11.pdf | 11.5 | 7.1 | 167.0 | 404.0 |
| patent-us223898.pdf | 29.9 | 8.0 | 100.9 | 494.3 |
| pdf20-simple.pdf | 0.3 | 0.8 | 1.2 | 1.9 |
| pdfium-jpx-lzw.pdf | 0.2 | 0.7 | 0.6 | 0.8 |
| pdfium-links-highlights-annots.pdf | 0.6 | 1.5 | 0.7 | 3.7 |
| pdfium-smask-blend.pdf | 0.2 | 0.5 | 0.6 | 1.8 |
| pdfium-type3.pdf | 0.2 | 0.5 | 1.0 | 1.5 |
| senate-expenditures.pdf | 7.5 | 8.0 | 156.8 | 369.8 |
| usrguide.pdf | 147.2 | 55.0 | 725.4 | 2331.6 |
| wdl6812-manuscript.pdf | 0.4 | 0.9 | 2.2 | 2.5 |

## Extracted-content comparison (quality proxy)

| File | pylopdf characters | pymupdf characters | Similarity after whitespace normalization |
|---|---|---|---|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.121 |
| patent-us223898.pdf | 11218 | 11218 | 0.320 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| pdfium-jpx-lzw.pdf | 0 | 0 | 1.000 |
| pdfium-links-highlights-annots.pdf | 92 | 92 | 1.000 |
| pdfium-smask-blend.pdf | 0 | 0 | 1.000 |
| pdfium-type3.pdf | 0 | 5 | 0.000 |
| senate-expenditures.pdf | 4516 | 4516 | 0.443 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

Similarity approaches 1.0 as output converges with PyMuPDF.
Low scores for forms, table-heavy reports, and scanned OCR layers reflect different
reading-order and whitespace conventions despite similar character counts.
A zero-character row is image-only with no text layer, so zero is correct for both.

## Merge (all corpus files into one document, ms)

| Task | pylopdf | pymupdf | pypdf |
|---|---|---|---|
| merge x14 | 44.2 | 139.8 | 495.2 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 76.8 | 142.2 |
| bunka-kokugo-series-019-p4.pdf | 71.2 | 180.3 |
| f1040.pdf | 131.5 | 154.8 |
| mhlw-doc.pdf | 63.1 | 87.8 |
| nics-background-checks-2015-11.pdf | 80.5 | 98.3 |
| patent-us223898.pdf | 56.6 | 100.0 |
| pdf20-simple.pdf | 22.9 | 26.3 |
| pdfium-jpx-lzw.pdf | 61.1 | 109.6 |
| pdfium-links-highlights-annots.pdf | 34.6 | 57.1 |
| pdfium-smask-blend.pdf | 12.7 | 7.5 |
| pdfium-type3.pdf | 9.9 | 5.3 |
| senate-expenditures.pdf | 114.9 | 79.5 |
| usrguide.pdf | 56.6 | 75.0 |
| wdl6812-manuscript.pdf | 86.0 | 148.6 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 610.8 | 1.00x |
| 2 | 401.4 | 1.52x |
| 4 | 261.9 | 2.33x |
| 8 | 218.1 | 2.80x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
