On pages whose CropBox extends past the MediaBox, `insert_text`,
`insert_textbox`, `insert_image`, `show_pdf_page`, `Shape`, the OCR text
layer, and new annotations now place content where `get_text` and the
renderer show the same display point, and `Page.rect` is the size of the
rendered page. All of them used the whole CropBox, while rendering and
extraction show the CropBox clipped to the MediaBox, which is also pymupdf's
`Page.rect`. On pdf.js `bug956965.pdf`, `Page.rect` was 852.71 by 665.62
points for a page that renders 782 by 595 pixels at 72 dpi and is now 782.36
by 595.28 points, and text drawn at (36, 72) was extracted at (0.83, 36.83)
and now at (36, 72); on `issue9713.pdf` it was extracted at (36, -324).
Annotation rectangles, link rectangles, and link destinations read back in
the same space, and `show_pdf_page` places only what rendering shows of the
source page. A CropBox that misses the MediaBox leaves the A4 page that the
renderer then draws, empty because it shows nothing outside the MediaBox,
where pymupdf uses Letter, and a page without a MediaBox draws on the A4 page
that `Page.mediabox` reports instead of 595 by 842 points. `mediabox` and
`cropbox` still report the stored boxes.
