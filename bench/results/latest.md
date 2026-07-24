# pylopdf benchmark results

- Run at: 2026-07-24 12:59 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.9.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 138.1 | 179.5 | 848.4 | 9850.9 |
| f1040.pdf | 16.8 | 33.4 | 176.3 | 572.9 |
| mhlw-doc.pdf | 18.4 | 11.3 | 109.3 | 195.3 |
| patent-us223898.pdf | 29.5 | 6.8 | 81.4 | 512.9 |
| pdf20-simple.pdf | 0.3 | 1.1 | 1.8 | 2.2 |
| usrguide.pdf | 144.9 | 50.7 | 665.2 | 1756.4 |
| wdl6812-manuscript.pdf | 0.3 | 0.7 | 1.4 | 2.2 |

## Extracted-content comparison (quality proxy)

| File | pylopdf characters | pymupdf characters | Similarity after whitespace normalization |
|---|---|---|---|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

Similarity approaches 1.0 as output converges with PyMuPDF.
Low scores for the form (f1040) and scanned OCR-layer patent reflect different
reading-order and whitespace conventions despite similar character counts.
A zero-character row is image-only with no text layer, so zero is correct for both.

## Merge (all corpus files into one document, ms)

| Task | pylopdf | pymupdf | pypdf |
|---|---|---|---|
| merge x7 | 40.6 | 127.1 | 366.3 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 38.2 | 86.2 |
| f1040.pdf | 53.1 | 94.7 |
| mhlw-doc.pdf | 35.7 | 70.1 |
| patent-us223898.pdf | 36.4 | 69.0 |
| pdf20-simple.pdf | 9.0 | 18.9 |
| usrguide.pdf | 31.8 | 56.6 |
| wdl6812-manuscript.pdf | 45.5 | 87.0 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 400.8 | 1.00x |
| 2 | 200.5 | 2.00x |
| 4 | 118.5 | 3.38x |
| 8 | 83.6 | 4.80x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
