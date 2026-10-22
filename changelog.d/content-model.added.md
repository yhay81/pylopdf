Add `Page.content()`, the content model: every text run, path, and image a
page paints, in paint order and display coordinates, as immutable values.
Text runs carry their glyphs with glyph IDs, Unicode, matrices, advances, and
exact boxes from the glyph outlines; paths carry their segments, fill, and
stroke; images carry their object number, format, mask, and placement. Each
item has its blend mode, soft mask, and the clips, transparency groups, and
marked content around it. `clip=` keeps the items in a rectangle,
`annotations=False` leaves out annotation appearances, and `max_elements`
bounds one call. It replaces pymupdf's `get_drawings`, `get_image_info`,
`get_texttrace`, `get_bboxlog`, `get_cdrawings`, `get_displaylist`, `run`,
`get_image_rects`, and `get_image_bbox`.
