`python -m pylopdf.migrate` rewrites pymupdf's `PDF_PAGE_LABEL_*` constants
to `PageLabelStyle` members, and `del doc[i]` raises a `TypeError` that points
at `del doc.pages[i]`. Hints cover `get_outline_xrefs`, `insert_page`,
`page_cropbox`, and `Document.stream`.
