`Page.operations` reads a page's content as an `Operations` sequence of
`Operation` and `InlineImage` values, tokenized as the renderer tokenizes it,
and assigning it writes them back; `Operations.parse(data)` and `to_bytes()`
do the same for any content stream. Unchanged operations keep their bytes, so
reading and writing back changes nothing. They replace pymupdf's
`read_contents`, `set_contents`, `get_contents`, and `wrap_contents`.
