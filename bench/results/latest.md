# pylopdf benchmark results

- Run at: 2026-07-28 04:52 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.13.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 112.3 | 143.4 | 606.5 | 8379.4 |
| bunka-kokugo-series-019-p4.pdf | 0.3 | 0.3 | 0.7 | 1.2 |
| f1040.pdf | 15.4 | 34.6 | 152.4 | 507.9 |
| mhlw-doc.pdf | 12.1 | 10.2 | 78.4 | 173.5 |
| nics-background-checks-2015-11.pdf | 8.0 | 6.4 | 118.2 | 294.6 |
| patent-us223898.pdf | 26.8 | 5.7 | 77.7 | 392.2 |
| pdf20-simple.pdf | 0.3 | 0.7 | 1.2 | 1.9 |
| pdfium-jpx-lzw.pdf | 0.2 | 0.3 | 0.4 | 0.7 |
| pdfium-links-highlights-annots.pdf | 0.5 | 0.9 | 0.6 | 2.9 |
| pdfium-smask-blend.pdf | 0.2 | 0.3 | 0.4 | 1.2 |
| pdfium-type3.pdf | 0.2 | 0.3 | 0.7 | 1.1 |
| senate-expenditures.pdf | 4.2 | 5.5 | 104.6 | 253.6 |
| usrguide.pdf | 43.4 | 40.2 | 552.6 | 1548.0 |
| wdl6812-manuscript.pdf | 0.3 | 1.0 | 1.4 | 2.2 |

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
| merge x14 | 44.1 | 126.0 | 322.6 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 39.3 | 81.4 |
| bunka-kokugo-series-019-p4.pdf | 41.5 | 102.9 |
| f1040.pdf | 48.5 | 88.8 |
| mhlw-doc.pdf | 34.2 | 67.2 |
| nics-background-checks-2015-11.pdf | 55.0 | 70.0 |
| patent-us223898.pdf | 36.1 | 62.4 |
| pdf20-simple.pdf | 10.4 | 19.2 |
| pdfium-jpx-lzw.pdf | 32.4 | 61.7 |
| pdfium-links-highlights-annots.pdf | 17.2 | 37.4 |
| pdfium-smask-blend.pdf | 7.7 | 4.5 |
| pdfium-type3.pdf | 4.5 | 2.3 |
| senate-expenditures.pdf | 54.9 | 53.8 |
| usrguide.pdf | 31.3 | 53.4 |
| wdl6812-manuscript.pdf | 42.6 | 82.7 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 303.6 | 1.00x |
| 2 | 169.3 | 1.79x |
| 4 | 101.2 | 3.00x |
| 8 | 79.7 | 3.81x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
