Replace `Page.set_rotation()`, `mediabox`, `cropbox`, `set_mediabox()`, and
`set_cropbox()` with an assignable `Page.rotation` and `Page.boxes`, a
`PageBoxes` view whose `media`, `crop`, `bleed`, `trim`, and `art` boxes are
read, assigned, and deleted in PDF user space as stored. The BleedBox,
TrimBox, and ArtBox are new, and default to the CropBox; only the MediaBox
and CropBox inherit from the page tree. `crop` no longer flips y against the
top of the MediaBox as pymupdf does, and assigning `media` removes the other
boxes as pymupdf's `set_mediabox` does. The new `Page.user_to_display` is the
matrix from PDF user space to display space, in place of pymupdf's
`transformation_matrix`, `rotation_matrix`, and `derotation_matrix`. The
codemod rewrites these calls and marks pymupdf's flipped boxes for review.
