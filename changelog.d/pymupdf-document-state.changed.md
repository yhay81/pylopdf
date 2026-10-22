Adopt pymupdf's `Document` constructor and document state:
`open` is now the same callable as `Document`, whose third positional
parameter is `filetype` (`"pdf"`; `open("pdf", data)` works as in pymupdf),
with `password`, `limits`, and `max_decompressed_size` keyword-only, and
`stream` also takes `bytearray`, `memoryview`, and `io.BytesIO`.
`authenticate` returns pymupdf's codes even after an empty user password
opened the document instead of always returning 1. `metadata` adds `trapped`
and `encryption`, and `set_metadata` ignores `format` and `encryption`,
writes `trapped`, removes entries whose values are false, `"none"`, or
`"null"`, and removes the Info dictionary for an empty dictionary.
