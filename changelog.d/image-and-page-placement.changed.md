`Page.insert_image()` and `Page.show_pdf_page()` go: inside
`with page.draw() as canvas:`, `canvas.image(rect, source)` draws a JPEG or
PNG path, encoded bytes, or a `Pixmap`, with `keep_aspect=` in place of
`keep_proportion=`, and `canvas.page(rect, docsrc.pages[pno])` draws a page of
any open document as vectors. `page.draw(underlay=True)` replaces
`overlay=False`, and `page.content().images` reports the xref of each placed
image. A canvas that places a page of its own document now shares that page's
Form with later placements on other pages, as `show_pdf_page` did.
`python -m pylopdf.migrate` rewrites both calls, and assignments of the image
xref, into canvas blocks; pymupdf's anticlockwise `rotate=` becomes the
canvas's clockwise angle.
