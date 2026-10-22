`Pixmap` is a value with gray and RGB pixels, PNG and JPEG output, and NumPy
interchange. `page.pixmap(colorspace="gray")` and
`doc.render(colorspace="gray")` render gray pixels (ITU-R BT.601 luma), with
straight alpha under `alpha=True`. `Pixmap.channels` (1 to 4) replaces `n`,
and `Pixmap.colorspace` returns the new `Colorspace` enum, `GRAY` or `RGB`,
which replaces pymupdf's `Colorspace` values and the `csRGB`, `csGRAY`,
`csCMYK`, `CS_RGB`, `CS_GRAY`, and `CS_CMYK` constants. The buffer is shaped
`(height, width)` for gray and `(height, width, channels)` otherwise, so
`numpy.asarray(pix)` reads the pixels without a copy and `bytes(pix)`
replaces `samples`; `Pixmap.from_array(array)` builds a pixmap from a
`uint8` array. `to_bytes(format="png", *, quality=95, max_size=...)` replaces
`tobytes()` and writes PNG or JPEG, `save(path, /, *, format=None,
quality=95)` writes either, by the extension unless `format` names it, and
`doc.render(format="jpeg", quality=...)` encodes a batch as JPEG; JPEG
refuses alpha. A gray Pixmap placed with `insert_image(pixmap=)` becomes a
DeviceGray image, and an opaque one needs no soft mask. The codemod rewrites
`samples`, `samples_mv`, `tobytes(output, jpg_quality=...)`,
`save(path, output, jpg_quality=...)`, and the color space constants.
