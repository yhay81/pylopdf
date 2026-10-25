# Image fixtures

Images that the tests decode, made for this repository and released with it
under its license.

| File | Contents | Made with |
| --- | --- | --- |
| `cmyk-300dpi.jpg` | A 32 by 16 CMYK JPEG at 300 dpi: four 8-pixel columns of cyan, magenta, yellow, and black | Pillow 13, `Image.new("CMYK", (32, 16))` saved with `quality=95, dpi=(300, 300)`, which writes Adobe's inverted CMYK |
| `rgb.jp2` | A 32 by 16 RGB JP2 file: four 8-pixel columns of red, green, blue, and white, with a capture resolution box of 150 dpi across and 300 dpi down | Pillow 13 with OpenJPEG 2.5, `Image.new("RGB", (32, 16))` saved with `irreversible=False`; the `res ` box was added to the `jp2h` box by hand |
| `rgba.jp2` | A 16 by 16 RGBA JP2 file: blue, opaque on the left half and at alpha 64 on the right | Pillow 13 with OpenJPEG 2.5, `Image.new("RGBA", (16, 16))` saved with `irreversible=False` |
| `gray.j2k` | A 16 by 8 gray raw JPEG 2000 codestream: black on the left half and white on the right | Pillow 13 with OpenJPEG 2.5, `Image.new("L", (16, 8))` saved with `irreversible=False` |
| `palette.jp2` | A 32 by 16 JP2 file whose `pclr` and `cmap` boxes map the indices 0 to 3 of four 8-pixel columns to red, green, blue, and white in sRGB | Pillow 13 with OpenJPEG 2.5, a gray image of the indices saved with `irreversible=False`; the `colr` box was set to sRGB and the `pclr` and `cmap` boxes added by hand |
