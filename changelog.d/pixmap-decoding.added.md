`Pixmap.from_file(path)` and `Pixmap.from_bytes(data)` decode PNG and JPEG
into a `Pixmap`: PNG keeps gray, RGB, and alpha as 8-bit samples, JPEG decodes
to gray or RGB with CMYK converted, and the resolution comes from the file.
Input above 64 MiB or 64,000,000 pixels raises `LimitError` by default, as for
`canvas.image`.
