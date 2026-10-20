Adopt pymupdf's rendering surface (roadmap M0): `Page.get_pixmap` now
returns opaque RGB (`n == 3`) on white by default and straight-alpha RGBA
with `alpha=True`, and takes `matrix=` (scaling matrices, with separate x
and y factors) and `dpi=` instead of `scale=`, `dpi` taking precedence as in
pymupdf. `Document.get_page_pixmap` replaces `Document.render_page`, and
`Page.get_svg_image` replaces `Document.render_page_svg` and
`Page.render_svg`; `Page.render` is removed in favor of
`get_pixmap().tobytes()`. `render_pages` takes the same `matrix=`, `dpi=`,
`alpha=`, and `background=` options. `OcrEngine.recognize` takes any
`Pixmap` and returns boxes in pixel coordinates; `Page.get_text_ocr`
recognizes pages.
