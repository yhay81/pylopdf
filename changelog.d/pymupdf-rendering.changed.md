Rendering is `page.pixmap()`, `page.svg()`, and `doc.render()`.
`page.pixmap(*, dpi=None, matrix=None, clip=None, alpha=False,
background=None, annotations=True)` replaces `Page.render()`,
`Document.render_page()`, and pymupdf's `get_pixmap()`: it returns a
`Pixmap`, opaque RGB on white by default and straight-alpha RGBA with
`alpha=True`, takes `dpi` or a scaling `matrix` (not both) instead of
`scale`, and leaves annotation and widget appearances out with
`annotations=False`. `page.svg(*, annotations=True, max_size=...)` replaces
`Page.render_svg()`, `Document.render_page_svg()`, and pymupdf's
`get_svg_image()`, and `doc.render(pages=None, ...)` replaces
`render_pages()` with the options of `pixmap()`. `OcrEngine.recognize` takes
any `Pixmap` and returns boxes in pixel coordinates. The codemod rewrites
`get_pixmap`, `get_page_pixmap`, `get_svg_image`, `render_pages`,
`render_page`, `render_page_svg`, and `render_svg` calls.
