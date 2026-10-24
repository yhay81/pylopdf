`page.search(text, quads=True)` returns one `Quad` per line of each match, and
`Line` and `Span` records carry a `quad` along their baseline, which replaces
pymupdf's `recover_*` functions.
