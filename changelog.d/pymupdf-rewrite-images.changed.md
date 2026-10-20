Replace `Document.compress_images(dpi=, quality=)` with pymupdf's
`Document.rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0,
lossy=True, lossless=True, bitonal=True, color=True, gray=True,
set_to_gray=False, options=None)` (roadmap M0): an image axis above
`dpi_threshold` is resampled to `dpi_target`, `quality=0` means no
recompression, and `lossy`, `lossless`, `color`, and `gray` select DCT, Flate,
RGB, and gray sources. pylopdf re-encodes rewritten images as JPEG only, so
resampling with `quality=0` raises, as do `set_to_gray=True` and `options`;
bitonal images count as skipped. The call still returns the
`ImageCompressionResult` counts, and its defaults now follow pymupdf, so
`rewrite_images()` without arguments changes nothing.
