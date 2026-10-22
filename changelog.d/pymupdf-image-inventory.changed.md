Adopt pymupdf's image inventory: `Page.get_images` now lists
the image XObjects that the page's resources and their Form XObjects reach
as `ImageItem` NamedTuples in pymupdf's order, with `full=True` adding the
`referencer` (`ImageItemFull`), instead of decoding drawn placements. The
new `Page.get_image_info` describes drawn placements with pymupdf's keys,
and `Document.extract_image(xref)` returns one image's data (DCT passing
through as JPEG, other filters decoded to PNG, without its soft mask).
