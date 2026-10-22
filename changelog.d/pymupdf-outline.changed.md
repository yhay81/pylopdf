Replace `Document.get_toc()` and `set_toc(toc)` with the zero-based
`Document.get_outline()` and `set_outline(entries)`: items are
`OutlineEntry(level, title, page)` NamedTuples whose `page` is zero-based
like every other pylopdf page number, where pymupdf's TOC pages are
one-based. The limits are unchanged.
