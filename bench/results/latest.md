# pylopdf benchmark results

- Run at: 2026-07-26 08:18 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.11.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 191.6 | 183.2 | 689.7 | 9997.1 |
| bunka-kokugo-series-019-p4.pdf | 1.9 | 0.5 | 1.0 | 1.8 |
| f1040.pdf | 26.7 | 66.9 | 230.3 | 704.7 |
| mhlw-doc.pdf | 18.4 | 11.5 | 114.3 | 263.3 |
| nics-background-checks-2015-11.pdf | 16.1 | 10.8 | 177.5 | 524.7 |
| patent-us223898.pdf | 33.3 | 6.3 | 79.2 | 493.7 |
| pdf20-simple.pdf | 0.3 | 1.2 | 1.7 | 2.4 |
| senate-expenditures.pdf | 6.6 | 7.2 | 132.2 | 374.1 |
| usrguide.pdf | 163.0 | 54.5 | 673.6 | 2050.7 |
| wdl6812-manuscript.pdf | 0.3 | 0.8 | 1.3 | 2.3 |

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
| merge x10 | 36.6 | 131.8 | 426.3 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 41.1 | 88.9 |
| bunka-kokugo-series-019-p4.pdf | 48.2 | 110.5 |
| f1040.pdf | 49.5 | 97.1 |
| mhlw-doc.pdf | 35.5 | 71.2 |
| nics-background-checks-2015-11.pdf | 54.2 | 72.6 |
| patent-us223898.pdf | 32.3 | 68.8 |
| pdf20-simple.pdf | 8.0 | 19.9 |
| senate-expenditures.pdf | 55.2 | 56.8 |
| usrguide.pdf | 28.3 | 54.9 |
| wdl6812-manuscript.pdf | 42.9 | 83.3 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 317.4 | 1.00x |
| 2 | 179.6 | 1.77x |
| 4 | 99.1 | 3.20x |
| 8 | 81.5 | 3.89x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

## Free-threaded extraction (two independent documents, ms)

- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 free-threaded
- Input: `bill-hr815.pdf`, all-page text extraction
- Repetitions: one warmup + median of 7 paired, alternating-order runs

| Mode | Workers | Time | Speedup |
|---|---:|---:|---:|
| Sequential | 1 | 280.3 | 1.00x |
| Parallel | 2 | 160.8 | 1.74x |

Reproduce with a free-threaded interpreter:
`python3.14t bench/free_threaded.py`.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
