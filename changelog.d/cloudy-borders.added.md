Squares, circles, and polygons take `clouds=`, a cloudy border (`/BE`) from 0
to 2 that `annotation.clouds` reads and sets; the codemod rewrites
`set_border(clouds=)` and the `PDF_BORDER_STYLE_*` constants.
