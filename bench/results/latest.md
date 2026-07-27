# pylopdf benchmark results

- Run at: 2026-07-27 22:12 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.13.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 159.9 | 151.1 | 638.6 | 8530.6 |
| bunka-kokugo-series-019-p4.pdf | 0.3 | 0.3 | 0.7 | 1.1 |
| f1040.pdf | 17.9 | 33.2 | 153.8 | 501.3 |
| mhlw-doc.pdf | 12.3 | 10.6 | 81.9 | 174.6 |
| nics-background-checks-2015-11.pdf | 9.2 | 6.4 | 115.1 | 304.5 |
| patent-us223898.pdf | 28.2 | 5.6 | 79.1 | 387.6 |
| pdf20-simple.pdf | 0.3 | 0.8 | 1.2 | 1.9 |
| pdfium-jpx-lzw.pdf | 0.2 | 0.4 | 0.4 | 0.8 |
| pdfium-links-highlights-annots.pdf | 0.5 | 1.3 | 0.7 | 3.3 |
| pdfium-smask-blend.pdf | 0.2 | 0.4 | 0.4 | 1.3 |
| pdfium-type3.pdf | 0.2 | 0.4 | 0.7 | 1.2 |
| senate-expenditures.pdf | 5.2 | 5.9 | 108.8 | 270.6 |
| usrguide.pdf | 53.0 | 43.2 | 588.0 | 1662.6 |
| wdl6812-manuscript.pdf | 0.4 | 0.8 | 1.4 | 2.4 |

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
| merge x14 | 47.4 | 131.4 | 350.6 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 37.4 | 83.7 |
| bunka-kokugo-series-019-p4.pdf | 39.3 | 107.0 |
| f1040.pdf | 47.6 | 94.4 |
| mhlw-doc.pdf | 33.3 | 69.0 |
| nics-background-checks-2015-11.pdf | 53.5 | 72.0 |
| patent-us223898.pdf | 31.9 | 65.8 |
| pdf20-simple.pdf | 7.9 | 19.4 |
| pdfium-jpx-lzw.pdf | 29.9 | 64.6 |
| pdfium-links-highlights-annots.pdf | 14.5 | 38.7 |
| pdfium-smask-blend.pdf | 4.0 | 4.7 |
| pdfium-type3.pdf | 1.1 | 2.4 |
| senate-expenditures.pdf | 52.6 | 55.3 |
| usrguide.pdf | 29.4 | 55.0 |
| wdl6812-manuscript.pdf | 41.0 | 85.0 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 310.7 | 1.00x |
| 2 | 210.1 | 1.48x |
| 4 | 121.5 | 2.56x |
| 8 | 87.4 | 3.55x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
