"""pymupdf features that a person has to migrate: the codemod marks each one."""

import pylopdf

# TODO(pylopdf): pylopdf does not provide pymupdf's TOOLS. Filter pylopdf's diagnostics as PylopdfWarning with the warnings module.
pylopdf.TOOLS.set_small_glyph_heights(True)
doc = pylopdf.Document()
page = doc.pages.new()
# TODO(pylopdf): pylopdf does not provide pymupdf's Page.get_textpage. There is no text-page object: pylopdf caches each page's interpretation, so call page.text(), page.words(), page.layout(), or page.search() on the page itself.
textpage = page.get_textpage()
# TODO(pylopdf): TextPage.extractBLOCKS is Page.layout in pylopdf. Read page.layout().blocks, Block records with text, bbox, and lines.
for block in textpage.extractBLOCKS():
    print(block)
# TODO(pylopdf): pylopdf extracts and inserts in display coordinates with the page rotation resolved; drop any rotation_matrix or derotation_matrix step, and map PDF user space with page.user_to_display.
point = pylopdf.Point(10, 20) * page.rotation_matrix
