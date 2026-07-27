# pylopdf benchmark results

- Run at: 2026-07-27 19:37 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- Versions: pylopdf 0.13.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- Repetitions: one warmup + median of 5 runs per task (ms; lower is faster)
- Corpus: tests/assets/real_world (sources and licenses are documented in its README)
- Reproduce: `uv sync --all-extras --group bench && uv run python bench/run.py`

## Text extraction (all pages, ms)

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 158.5 | 139.3 | 592.8 | 8021.0 |
| bunka-kokugo-series-019-p4.pdf | 0.3 | 0.3 | 0.6 | 1.1 |
| f1040.pdf | 15.8 | 30.9 | 147.4 | 511.8 |
| mhlw-doc.pdf | 14.5 | 9.9 | 76.6 | 168.3 |
| nics-background-checks-2015-11.pdf | 8.5 | 6.1 | 108.8 | 278.3 |
| patent-us223898.pdf | 27.7 | 5.4 | 74.5 | 368.8 |
| pdf20-simple.pdf | 0.3 | 0.8 | 1.4 | 1.8 |
| pdfium-jpx-lzw.pdf | 0.2 | 0.4 | 0.4 | 0.7 |
| pdfium-links-highlights-annots.pdf | 0.5 | 0.9 | 0.6 | 3.0 |
| pdfium-smask-blend.pdf | 0.2 | 0.3 | 0.4 | 1.2 |
| pdfium-type3.pdf | 0.2 | 0.3 | 0.7 | 1.1 |
| senate-expenditures.pdf | 4.8 | 5.5 | 102.4 | 258.1 |
| usrguide.pdf | 128.9 | 40.4 | 549.3 | 1542.4 |
| wdl6812-manuscript.pdf | 0.3 | 0.8 | 1.4 | 2.3 |

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
| merge x14 | 44.0 | 125.5 | 318.3 |

## Rendering (first page to 2x PNG, ms)

| File | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 39.6 | 81.8 |
| bunka-kokugo-series-019-p4.pdf | 41.7 | 104.2 |
| f1040.pdf | 49.3 | 89.3 |
| mhlw-doc.pdf | 34.7 | 67.5 |
| nics-background-checks-2015-11.pdf | 54.8 | 70.1 |
| patent-us223898.pdf | 34.9 | 63.7 |
| pdf20-simple.pdf | 10.7 | 18.8 |
| pdfium-jpx-lzw.pdf | 32.3 | 63.5 |
| pdfium-links-highlights-annots.pdf | 17.1 | 37.9 |
| pdfium-smask-blend.pdf | 7.5 | 4.5 |
| pdfium-type3.pdf | 4.7 | 2.3 |
| senate-expenditures.pdf | 53.3 | 54.7 |
| usrguide.pdf | 31.7 | 53.2 |
| wdl6812-manuscript.pdf | 42.8 | 82.8 |

## Parallel rendering (first 12 usrguide pages to 2x PNG, ms)

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 297.4 | 1.00x |
| 2 | 171.0 | 1.74x |
| 4 | 114.3 | 2.60x |
| 8 | 81.6 | 3.64x |

`render_pages()` preserves input order, releases the GIL, and uses a dedicated worker pool bounded by both worker count and estimated live rendering memory.

This report publishes both wins and losses. Results depend on the environment,
so cite them together with the environment details above.
