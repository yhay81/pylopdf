`Document.rewrite_images` takes `encoding="flate"` to write rewritten images
without further loss and `encoding="bitonal"` to write one bit per pixel,
and implements pymupdf's `set_to_gray=True`, which converts colour images to
gray; `"jpeg"` stays the default.
