Add `pylopdf.create()`, which starts a new document that krilla writes as a
whole. `builder.page(size)` returns the same `Canvas` as `Page.draw()`, so
paths, OpenType text, images, and scopes draw the same way; `canvas.tag(role,
...)` scopes build the structure tree of a tagged document, and
`canvas.bookmark(title, level=...)` its outline. `standard="pdf/a-2b"`,
`"pdf/ua-1"`, or several standards together validate the document with
krilla's validators and raise `PdfError` listing every violation instead of
writing a non-conforming file. `builder.to_bytes()` returns the file and
`builder.finish()` opens it as a `Document`. pymupdf's `DocumentWriter` maps
onto it.
