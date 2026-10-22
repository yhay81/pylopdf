Image objects are handles: `doc.images` maps object numbers to
`ImageObject` handles, and `page.resources.images` lists the ones that a
page's resources and their Form XObjects reach, once each in resource order,
in place of `Page.get_images()`, which decoded every drawn placement (now
`page.content(text_runs=False, paths=False).images`). A handle reads
`width`, `height`, `bits`, `colorspace`, `alternate_colorspace`, `filters`,
`soft_mask`, `mask`, and `is_stencil` from the document on every access;
`extract()` returns an `EncodedImage` with `format`, `data`, and
`resolution` (DCT data as JPEG, anything else as PNG, without masks), and
`to_pixmap(alpha=False)` renders the image at its pixel size, applying its
masks with `alpha=True`. The codemod rewrites pymupdf's `get_images()` and
`extract_image(xref)` calls and the keys read from the extracted image.
