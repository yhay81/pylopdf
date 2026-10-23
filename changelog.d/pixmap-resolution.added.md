`Pixmap.resolution` gives a pixmap's dots per inch: the resolution that
`Page.pixmap()` rendered at, else 96. PNG output states it in a `pHYs` chunk
and JPEG output in its JFIF density, `Document.render()` included, and
`Pixmap.from_array(array, resolution=(x, y))` sets it, sharing the pixels of a
`Pixmap` passed as `array`.
