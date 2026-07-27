# pylopdf benchmark results

- Run at: 2026-07-27 01:36 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.12.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 271.2 | 217.8 | 832.8 | 14408.0 |
| bunka-kokugo-series-019-p4.pdf | 0.4 | 0.6 | 1.4 | 2.3 |
| f1040.pdf | 28.2 | 57.4 | 218.2 | 744.2 |
| mhlw-doc.pdf | 20.5 | 14.1 | 120.4 | 330.4 |
| nics-background-checks-2015-11.pdf | 15.1 | 11.7 | 203.0 | 1005.1 |
| patent-us223898.pdf | 69.9 | 11.4 | 360.7 | 1578.3 |
| pdf20-simple.pdf | 0.4 | 1.3 | 1.8 | 3.2 |
| pdfium-jpx-lzw.pdf | 0.1 | 0.5 | 0.7 | 1.1 |
| pdfium-links-highlights-annots.pdf | 0.6 | 1.5 | 1.1 | 4.9 |
| pdfium-smask-blend.pdf | 0.2 | 0.6 | 1.0 | 1.8 |
| pdfium-type3.pdf | 0.2 | 0.5 | 0.9 | 1.8 |
| senate-expenditures.pdf | 8.1 | 9.0 | 140.3 | 353.5 |
| usrguide.pdf | 150.0 | 54.3 | 717.1 | 2087.0 |
| wdl6812-manuscript.pdf | 0.4 | 0.9 | 1.6 | 3.5 |

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
| merge x14 | 179.6 | 324.7 | 421.1 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 66.0 | 117.1 |
| bunka-kokugo-series-019-p4.pdf | 61.1 | 120.3 |
| f1040.pdf | 95.8 | 120.8 |
| mhlw-doc.pdf | 60.5 | 89.8 |
| nics-background-checks-2015-11.pdf | 79.4 | 94.7 |
| patent-us223898.pdf | 47.3 | 73.3 |
| pdf20-simple.pdf | 15.4 | 21.1 |
| pdfium-jpx-lzw.pdf | 41.4 | 72.2 |
| pdfium-links-highlights-annots.pdf | 24.6 | 44.9 |
| pdfium-smask-blend.pdf | 9.7 | 5.2 |
| pdfium-type3.pdf | 6.4 | 2.6 |
| senate-expenditures.pdf | 65.6 | 61.6 |
| usrguide.pdf | 41.9 | 72.3 |
| wdl6812-manuscript.pdf | 89.7 | 156.4 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 428.4 | 1.00x |
| 2 | 210.3 | 2.04x |
| 4 | 140.3 | 3.05x |
| 8 | 116.8 | 3.67x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
