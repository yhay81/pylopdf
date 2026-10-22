Reading text has one method per result. `Page.text()`, `Page.words()`, and
`Page.layout(*, chars=False)` replace `get_text()` and its `"text"`,
`"words"`, `"blocks"`, `"dict"`, and `"rawdict"` options; `Page.search(text)`
replaces `search_for`; `Page.markdown()`, `Document.markdown()`, and
`Table.markdown()` replace `to_markdown()`; and `Document.text(pages)`
replaces `Document.get_text(pages)`. `words()` returns `Word` records with
`text`, `bbox`, `block`, `line`, and `index`, and `layout()` returns a
`Layout` of `Block`, `Line`, and `Span` records, with `Char` records for every
glyph when `chars=True`; these frozen dataclasses replace the `WordEntry` and
`BlockEntry` tuples and the `TextPage`, `RawTextPage`, `TextBlock`,
`TextLine`, `TextSpan`, and `TextChar` dictionaries. `Layout.to_dict()`
returns pymupdf's dictionary shape for JSON. The codemod rewrites
`get_text(...)`, `search_for`, and `to_markdown` calls and marks code that
reads word and block tuples.
