# Image fixtures

Images that the tests decode, made for this repository and released with it
under its license.

| File | Contents | Made with |
| --- | --- | --- |
| `cmyk-300dpi.jpg` | A 32 by 16 CMYK JPEG at 300 dpi: four 8-pixel columns of cyan, magenta, yellow, and black | Pillow 13, `Image.new("CMYK", (32, 16))` saved with `quality=95, dpi=(300, 300)`, which writes Adobe's inverted CMYK |
