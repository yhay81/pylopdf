Adopt pymupdf's page-structure semantics: `copy_page` becomes
`fullcopy_page`, whose `pno` lies in `0..page_count - 1` and `to` in
`-1..page_count - 1`. `delete_page` defaults to the last page, and
`delete_pages` takes pymupdf's forms: one page number, an inclusive
`(from_page, to_page)` pair in either order, the `from_page=` and `to_page=`
keywords, or an iterable of non-negative numbers; out-of-range numbers raise
`ValueError`. `insert_pdf` names its source `docsrc`, takes keyword-only
`from_page`, `to_page`, and `start_at`, and follows pymupdf's range rules:
a negative `from_page` means the first page and a negative `to_page` the
last, both clamp to the source, and a `start_at` beyond the end appends.
`select` takes its page numbers positionally.
