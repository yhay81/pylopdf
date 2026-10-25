`canvas.image(rect, image)` draws an `ImageObject` of any open document: an
image of the page's document is drawn again, with its masks, one of another
document is copied with the objects it reaches, and a new document's canvas
draws the pixels that `image.to_pixmap(alpha=True)` renders. A new
document's canvas now also draws the `Image` items of `Page.content()` with
`canvas.draw(item)`. The migration codemod rewrites pymupdf's
`insert_image(rect, xref=...)` to `canvas.image(rect, doc.images[xref])`.
