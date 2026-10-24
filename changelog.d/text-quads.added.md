`page.search(text, quads=True)` returns one `Quad` per line of each match, and
`Line`, `Span`, and `Char` records carry a `quad` along their baseline, which
replaces pymupdf's `recover_*` functions.
