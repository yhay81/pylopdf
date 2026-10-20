"""pymupdf features that a person has to migrate: the codemod marks each one."""

import fitz

fitz.TOOLS.set_small_glyph_heights(True)
doc = fitz.open()
page = doc.new_page()
textpage = page.get_textpage()
for block in textpage.extractBLOCKS():
    print(block)
point = fitz.Point(10, 20) * page.rotation_matrix
