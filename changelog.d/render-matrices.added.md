`Page.pixmap(matrix=)` and `Document.render(matrix=)` render any invertible
matrix, including rotations, shears, and flips, which used to raise
`PdfError`. The pixmap covers the transformed page with its top-left corner on
a pixel corner, so a diagonal matrix renders exactly what the equivalent `dpi`
renders and a quarter turn of a scanned page turns its pixels; a translation
still moves no pixels. A singular matrix raises `ValueError`.
