`Canvas.page` takes `clip`, a rectangle of the source page's display
coordinates whose part of the page fills the target rectangle, and `rotate`,
which turns the placed page clockwise by 0, 90, 180, or 270 degrees; a page
placed with several clips and rotations is stored once. The migration
codemod now rewrites pymupdf's `show_pdf_page` `clip=` and anticlockwise
`rotate=`.
