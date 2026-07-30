# pylopdf benchmark results

- Run at: 2026-07-30 17:30 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.13.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 7 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 134.5 | 166.8 | 766.2 | 9004.7 |
| bunka-kokugo-series-019-p4.pdf | 0.3 | 0.3 | 0.7 | 1.2 |
| f1040.pdf | 16.0 | 33.9 | 166.0 | 486.4 |
| mhlw-doc.pdf | 11.7 | 10.2 | 77.6 | 168.8 |
| nics-background-checks-2015-11.pdf | 8.2 | 6.2 | 112.8 | 284.8 |
| patent-us223898.pdf | 26.6 | 5.5 | 77.9 | 408.3 |
| pdf20-simple.pdf | 0.3 | 0.8 | 1.2 | 1.9 |
| pdfium-jpx-lzw.pdf | 0.2 | 0.4 | 0.4 | 0.7 |
| pdfium-links-highlights-annots.pdf | 0.6 | 0.9 | 0.6 | 3.3 |
| pdfium-smask-blend.pdf | 0.2 | 0.3 | 0.5 | 1.2 |
| pdfium-type3.pdf | 0.2 | 0.4 | 0.6 | 1.2 |
| senate-expenditures.pdf | 5.1 | 6.4 | 105.7 | 261.2 |
| usrguide.pdf | 58.4 | 49.3 | 634.9 | 1808.2 |
| wdl6812-manuscript.pdf | 0.3 | 1.0 | 1.6 | 2.8 |

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
| merge x14 | 44.0 | 129.3 | 346.8 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 37.5 | 83.8 |
| bunka-kokugo-series-019-p4.pdf | 39.3 | 107.1 |
| f1040.pdf | 48.3 | 91.5 |
| mhlw-doc.pdf | 32.9 | 70.1 |
| nics-background-checks-2015-11.pdf | 54.3 | 73.8 |
| patent-us223898.pdf | 32.2 | 65.8 |
| pdf20-simple.pdf | 7.3 | 19.1 |
| pdfium-jpx-lzw.pdf | 31.0 | 63.4 |
| pdfium-links-highlights-annots.pdf | 14.0 | 39.3 |
| pdfium-smask-blend.pdf | 4.1 | 4.8 |
| pdfium-type3.pdf | 1.6 | 2.5 |
| senate-expenditures.pdf | 52.9 | 55.7 |
| usrguide.pdf | 29.2 | 55.9 |
| wdl6812-manuscript.pdf | 41.8 | 85.2 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 319.2 | 1.00x |
| 2 | 199.1 | 1.60x |
| 4 | 110.4 | 2.89x |
| 8 | 85.9 | 3.72x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
