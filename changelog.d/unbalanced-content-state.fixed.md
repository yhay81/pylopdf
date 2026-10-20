Drawing on a page whose content leaves `q` and `Q` unbalanced starts from
the page's initial graphics state. `insert_text`, `insert_textbox`,
`insert_image`, `show_pdf_page`, `insert_ocr_text_layer`, the `Page.draw_*`
methods, and `Shape.commit` wrapped the existing content in one `q` and one
`Q`, so the wrapper's `Q` closed a `q` that the content left open, a `Q` of
the content closed the wrapper's `q`, and a transform that the content set
stayed in force for what was drawn next: text inserted at (36, 72) on pdf.js
`bug1795263.pdf` and `issue13971.pdf` landed at (3.6, 765) and (319, 1131).
The first drawing on a page now decodes its content once and counts `q` and
`Q` as hayro's content parser reads them, skipping strings, comments, and
inline image data, and wraps the content in one `q` more than the `Q` that
find no `q` and one `Q` more than the `q` that it leaves open. The closing
stream starts with a newline, so a comment that ends the content, as on
pdf.js `calgray.pdf`, can no longer swallow it. Both pages now place the text
at (36, 72), as pymupdf does, and MuPDF reads the result as balanced; text
drawn on the first three pages of 1,412 pdf.js corpus files moved on no
other page. Decoding and counting took a median of 57 µs per page: stamping
every page of the 27-page `usrguide.pdf` once took 1.67 ms instead of
0.54 ms, and a single page with 8.7 to 29 MB of content 42 to 115 ms more.
Later drawings on a page cost what they did.
