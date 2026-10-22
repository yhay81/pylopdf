`Page.tables(*, strategy="lines", clip=None)` replaces `find_tables()` and
returns a list of `Table` records instead of a `TableFinder`. A `Table` holds
`bbox`, `rows` (the cell text row by row, replacing `extract()`), `cells`
(each cell's `Rect` in the same shape, where 0.15 had a flat list), and
`diagnostics`; `len(table.rows)` replaces `row_count`, and
`table.diagnostics.confidence` and `strategy` replace the table's own.
`TableDiagnostics` is a frozen dataclass. `Page.ocr()` replaces
`get_text_ocr()`, and `Page.apply_ocr(words=...)` replaces
`insert_ocr_text_layer()`: it takes `OcrWord` records from another engine and,
like recognition, skips a page that already has text unless
`skip_existing=False`. `OcrWord` is a frozen dataclass with `text`, `bbox`,
and `confidence` instead of a dictionary. The codemod rewrites `find_tables`
calls, their `tables`, `extract()`, `row_count`, and `col_count`, and
`get_text_ocr` calls, and marks the rest.
