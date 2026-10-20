`Pixmap.alpha` and `Pixmap.colorspace` report the pixel layout, and the new
`Colorspace` values `csRGB`, `csGRAY`, and `csCMYK` with the `CS_RGB`,
`CS_GRAY`, and `CS_CMYK` codes follow pymupdf (roadmap M0). An RGB Pixmap
placed with `insert_image(pixmap=)` needs no soft mask.
