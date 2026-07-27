# pylopdf benchmark results

- Run at: 2026-07-27 11:36 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.12.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 228.7 | 182.8 | 875.6 | 11931.3 |
| bunka-kokugo-series-019-p4.pdf | 0.3 | 0.3 | 0.8 | 1.2 |
| f1040.pdf | 23.4 | 42.3 | 366.3 | 586.0 |
| mhlw-doc.pdf | 15.9 | 11.0 | 105.5 | 260.7 |
| nics-background-checks-2015-11.pdf | 13.0 | 9.9 | 169.5 | 416.7 |
| patent-us223898.pdf | 34.4 | 9.4 | 106.4 | 596.8 |
| pdf20-simple.pdf | 0.8 | 1.5 | 2.5 | 4.7 |
| pdfium-jpx-lzw.pdf | 0.2 | 0.8 | 0.6 | 1.4 |
| pdfium-links-highlights-annots.pdf | 2.4 | 1.7 | 1.0 | 9.7 |
| pdfium-smask-blend.pdf | 0.1 | 0.4 | 1.2 | 2.2 |
| pdfium-type3.pdf | 2.7 | 0.7 | 1.1 | 1.8 |
| senate-expenditures.pdf | 12.2 | 10.7 | 202.3 | 470.8 |
| usrguide.pdf | 160.0 | 51.3 | 666.4 | 2130.9 |
| wdl6812-manuscript.pdf | 0.4 | 1.0 | 1.8 | 2.7 |

## Extracted-content comparison (quality proxy)

| File | pylopdf characters | pymupdf characters | Similarity after whitespace normalization |
|---|---|---|---|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.121 |
| patent-us223898.pdf | 11218 | 11218 | 0.578 |
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
| merge x14 | 60.7 | 148.8 | 528.4 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 63.9 | 388.7 |
| bunka-kokugo-series-019-p4.pdf | 132.1 | 209.4 |
| f1040.pdf | 244.1 | 162.6 |
| mhlw-doc.pdf | 73.5 | 172.0 |
| nics-background-checks-2015-11.pdf | 203.4 | 134.1 |
| patent-us223898.pdf | 115.6 | 118.4 |
| pdf20-simple.pdf | 40.6 | 32.4 |
| pdfium-jpx-lzw.pdf | 150.3 | 130.3 |
| pdfium-links-highlights-annots.pdf | 55.2 | 57.6 |
| pdfium-smask-blend.pdf | 7.9 | 10.4 |
| pdfium-type3.pdf | 35.0 | 3.4 |
| senate-expenditures.pdf | 222.6 | 88.9 |
| usrguide.pdf | 149.9 | 102.6 |
| wdl6812-manuscript.pdf | 115.7 | 147.4 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 996.1 | 1.00x |
| 2 | 446.5 | 2.23x |
| 4 | 158.1 | 6.30x |
| 8 | 126.0 | 7.90x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
