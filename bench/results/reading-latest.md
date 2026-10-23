# Reading oracle

pylopdf 0.15.0 against pymupdf 1.28.2, Python 3.15.0 on Darwin arm64.

Characters of `page.layout(chars=True)` on the first 6 pages of each corpus document, paired with the character of pymupdf's `rawdict` that has the same text and origin to a tenth of a point, both in display space. Unpaired characters are those either reader lacks or places elsewhere.

| Document | Paired | Unpaired | Same span color | Mean IoU | 5th percentile | IoU ≥ 0.8 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bill-hr815.pdf | 12554 | 97 | 100.00% | 0.966 | 0.965 | 100.00% |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | n/a | n/a | n/a | n/a |
| f1040.pdf | 7905 | 22 | 100.00% | 0.855 | 0.838 | 99.97% |
| mhlw-doc.pdf | 1143 | 45 | 100.00% | 0.907 | 0.907 | 100.00% |
| nics-background-checks-2015-11.pdf | 4083 | 64 | 100.00% | 1.000 | 0.999 | 100.00% |
| patent-us223898.pdf | 9233 | 13 | 100.00% | 0.801 | 0.801 | 100.00% |
| pdf20-simple.pdf | 10 | 0 | 100.00% | 0.954 | 0.954 | 100.00% |
| pdfium-jpx-lzw.pdf | 0 | 0 | n/a | n/a | n/a | n/a |
| pdfium-links-highlights-annots.pdf | 77 | 4 | 100.00% | 0.728 | 0.728 | 0.00% |
| pdfium-smask-blend.pdf | 0 | 0 | n/a | n/a | n/a | n/a |
| pdfium-type3.pdf | 0 | 0 | n/a | n/a | n/a | n/a |
| senate-expenditures.pdf | 3879 | 1 | 100.00% | 0.751 | 0.751 | 0.15% |
| usrguide.pdf | 8828 | 1691 | 100.00% | 0.975 | 0.871 | 100.00% |
| wdl6812-manuscript.pdf | 0 | 0 | n/a | n/a | n/a | n/a |
| **all** | 47712 | 1937 | 100.00% | 0.901 | 0.751 | 91.72% |

## Boxes

Characters whose font program hayro hands out take the program's ascender and descender, as MuPDF falls back to without a font descriptor. Over those 5232 characters the mean IoU, the 5th percentile, and the share at or above 0.8 are 0.979 | 0.907 | 100.00%. The published threshold is a mean IoU of at least 0.95 with at least 99% of them at 0.8 or more; the boxes meets it. The other characters, in Type 1 fonts and fonts that are not embedded, take MuPDF's defaults of 0.8 and -0.2, where MuPDF reads the font descriptor's `/Ascent` and `/Descent`; reading them waits for hayro to identify the font dictionary of a glyph (#338).
