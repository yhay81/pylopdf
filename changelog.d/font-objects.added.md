`doc.fonts` maps object numbers to `FontObject` handles with `name`,
`subtype`, `encoding`, `is_embedded`, `widths` (the advance of every
character code in em), and `extract()` for the embedded program as a
`FontProgram`; `page.resources.fonts` lists the fonts and
`page.resources.forms` the Form XObjects (`FormObject` handles with `bbox`
and `matrix`) that a page's resources reach. They replace pymupdf's
`get_fonts`, `get_page_fonts`, `extract_font`, `get_char_widths`,
`get_xobjects`, and `get_page_xobjects`.
