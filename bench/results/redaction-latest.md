# Redaction against a pymupdf oracle

pylopdf 0.15.0, pymupdf 1.28.2, Python 3.15.0, Darwin arm64.

pylopdf redacts page 0 of every case of `tests/redaction_corpus.py` and saves it in full;
pymupdf reads the result. A leak is a secret in pymupdf's text of the page or of an area, a
character of a secret whose center lies in an area, a drawing or unblanked image placement in an
area, or a
pixel of MuPDF's rendering of an area (inset by 1.5 points) that is neither white nor black.

| Case | Areas | Glyphs | Images | Paths | Text leaks | Chars in areas | Drawings in areas | Images in areas | Inked pixels | Kept missing |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| plain | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| kerned-tj | 1 | 9 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| tj-with-spacing | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| quote-operators | 2 | 14 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| rotated-text-matrix | 1 | 10 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| shared-form-two-pages | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| form-drawn-twice | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| nested-forms | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| form-without-resources | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| rotated-page-90 | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| rotated-page-180 | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| rotated-page-270 | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| cropped-page | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| cid-horizontal | 1 | 4 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| cid-vertical | 1 | 4 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| type3 | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| invisible-ocr-layer | 1 | 7 | 1 | 0 | none | 0 | 0 | 0 | 0 | none |
| text-under-cover | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| white-and-tiny-text | 2 | 14 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| overprinted-twice | 2 | 14 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| clipped-out-text | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| actual-text | 2 | 14 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| after-hayro-stops | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| courier-and-helvetica-overlap | 2 | 14 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| image-pixels | 1 | 0 | 1 | 0 | none | 0 | 0 | 0 | 0 | none |
| image-with-soft-mask | 1 | 0 | 1 | 0 | none | 0 | 0 | 0 | 0 | none |
| stencil-mask | 1 | 0 | 1 | 0 | none | 0 | 0 | 0 | 0 | none |
| inline-image | 1 | 0 | 1 | 0 | none | 0 | 0 | 0 | 0 | none |
| inline-image-filtered | 1 | 0 | 1 | 0 | none | 0 | 0 | 0 | 0 | none |
| vector-art | 1 | 0 | 0 | 1 | none | 0 | 0 | 0 | 0 | none |
| pattern-text | 1 | 0 | 0 | 1 | none | 0 | 0 | 0 | 0 | none |
| annotation-appearance | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |
| redaction-annotations | 1 | 7 | 0 | 0 | none | 0 | 0 | 0 | 0 | none |

33 cases, 0 with a leak.
