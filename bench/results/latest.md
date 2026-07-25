# pylopdf benchmark results

- Run at: 2026-07-25 12:00 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.10.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 160.5 | 162.9 | 638.3 | 8842.6 |
| f1040.pdf | 16.1 | 33.4 | 155.8 | 499.3 |
| mhlw-doc.pdf | 14.6 | 10.0 | 80.7 | 173.2 |
| nics-background-checks-2015-11.pdf | 9.3 | 6.1 | 113.5 | 285.7 |
| patent-us223898.pdf | 32.5 | 6.9 | 76.4 | 394.5 |
| pdf20-simple.pdf | 0.2 | 0.7 | 1.2 | 1.8 |
| senate-expenditures.pdf | 4.8 | 6.2 | 110.8 | 282.1 |
| usrguide.pdf | 117.2 | 42.1 | 583.7 | 1667.3 |
| wdl6812-manuscript.pdf | 0.3 | 0.8 | 1.4 | 2.4 |

## Extracted-content comparison (quality proxy)

| File | pylopdf characters | pymupdf characters | Similarity after whitespace normalization |
|---|---|---|---|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.121 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
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
| merge x9 | 42.4 | 131.5 | 452.6 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 42.9 | 117.0 |
| f1040.pdf | 64.9 | 129.2 |
| mhlw-doc.pdf | 46.0 | 85.2 |
| nics-background-checks-2015-11.pdf | 75.4 | 95.8 |
| patent-us223898.pdf | 45.0 | 71.6 |
| pdf20-simple.pdf | 11.0 | 21.9 |
| senate-expenditures.pdf | 61.8 | 65.9 |
| usrguide.pdf | 37.1 | 64.4 |
| wdl6812-manuscript.pdf | 52.3 | 109.8 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 386.6 | 1.00x |
| 2 | 194.0 | 1.99x |
| 4 | 109.7 | 3.52x |
| 8 | 86.8 | 4.46x |

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
