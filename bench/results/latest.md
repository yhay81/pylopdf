# pylopdf benchmark results

- Run at: 2026-07-27 12:00 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.12.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 206.5 | 181.0 | 802.5 | 10824.9 |
| bunka-kokugo-series-019-p4.pdf | 0.3 | 0.6 | 1.0 | 1.7 |
| f1040.pdf | 24.0 | 50.3 | 201.3 | 677.6 |
| mhlw-doc.pdf | 20.4 | 13.0 | 109.6 | 229.7 |
| nics-background-checks-2015-11.pdf | 13.8 | 9.2 | 156.0 | 363.2 |
| patent-us223898.pdf | 32.7 | 7.3 | 108.3 | 506.7 |
| pdf20-simple.pdf | 0.3 | 0.8 | 1.3 | 2.8 |
| pdfium-jpx-lzw.pdf | 0.2 | 0.4 | 0.4 | 0.8 |
| pdfium-links-highlights-annots.pdf | 0.5 | 1.0 | 0.7 | 3.4 |
| pdfium-smask-blend.pdf | 0.2 | 0.3 | 0.9 | 1.6 |
| pdfium-type3.pdf | 0.2 | 0.5 | 0.7 | 1.7 |
| senate-expenditures.pdf | 5.8 | 8.7 | 128.1 | 339.5 |
| usrguide.pdf | 167.4 | 54.3 | 715.7 | 2126.9 |
| wdl6812-manuscript.pdf | 0.3 | 1.0 | 1.5 | 2.4 |

## Extracted-content comparison (quality proxy)

| File | pylopdf characters | pymupdf characters | Similarity after whitespace normalization |
|---|---|---|---|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.121 |
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
Low scores for forms, table-heavy reports, and scanned OCR layers reflect different
reading-order and whitespace conventions despite similar character counts.
A zero-character row is image-only with no text layer, so zero is correct for both.

## Merge (all corpus files into one document, ms)

| Task | pylopdf | pymupdf | pypdf |
|---|---|---|---|
| merge x14 | 58.7 | 178.9 | 472.4 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 43.8 | 95.1 |
| bunka-kokugo-series-019-p4.pdf | 45.9 | 124.6 |
| f1040.pdf | 58.8 | 116.2 |
| mhlw-doc.pdf | 39.1 | 80.2 |
| nics-background-checks-2015-11.pdf | 61.9 | 83.2 |
| patent-us223898.pdf | 37.5 | 80.3 |
| pdf20-simple.pdf | 9.9 | 22.2 |
| pdfium-jpx-lzw.pdf | 34.1 | 87.0 |
| pdfium-links-highlights-annots.pdf | 26.8 | 52.4 |
| pdfium-smask-blend.pdf | 5.2 | 5.8 |
| pdfium-type3.pdf | 1.5 | 3.3 |
| senate-expenditures.pdf | 67.7 | 66.4 |
| usrguide.pdf | 39.0 | 67.1 |
| wdl6812-manuscript.pdf | 49.2 | 104.6 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 386.8 | 1.00x |
| 2 | 211.7 | 1.83x |
| 4 | 123.0 | 3.15x |
| 8 | 92.7 | 4.17x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
