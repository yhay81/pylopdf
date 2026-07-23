# pylopdf benchmark results

- Run at: 2026-07-23 04:47 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.9.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 131.6 | 150.7 | 631.4 | 8652.7 |
| f1040.pdf | 16.0 | 32.9 | 155.6 | 506.2 |
| mhlw-doc.pdf | 11.8 | 10.3 | 84.2 | 175.7 |
| patent-us223898.pdf | 26.3 | 6.0 | 83.4 | 390.2 |
| pdf20-simple.pdf | 0.3 | 0.8 | 1.2 | 1.9 |
| usrguide.pdf | 108.2 | 42.7 | 579.3 | 1673.5 |
| wdl6812-manuscript.pdf | 0.4 | 1.0 | 1.4 | 2.6 |

## Extracted-content comparison (quality proxy)

| File | pylopdf characters | pymupdf characters | Similarity after whitespace normalization |
|---|---|---|---|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10158 | 10156 | 0.680 |
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
| merge x7 | 30.1 | 122.2 | 325.3 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 40.8 | 84.0 |
| f1040.pdf | 49.9 | 92.1 |
| mhlw-doc.pdf | 33.8 | 68.7 |
| patent-us223898.pdf | 34.7 | 64.1 |
| pdf20-simple.pdf | 9.0 | 18.9 |
| usrguide.pdf | 30.7 | 54.6 |
| wdl6812-manuscript.pdf | 43.4 | 83.8 |

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
