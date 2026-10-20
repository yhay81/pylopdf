"""pymupdf features that a person has to migrate: the codemod marks each one."""

import pylopdf

# TODO(pylopdf): pylopdf does not provide pymupdf's TOOLS. Filter pylopdf's diagnostics as PylopdfWarning with the warnings module.
pylopdf.TOOLS.set_small_glyph_heights(True)
doc = pylopdf.open()
page = doc.new_page()
# TODO(pylopdf): pylopdf plans pymupdf's Page.get_textpage for M1 (see ROADMAP.md).
textpage = page.get_textpage()
# TODO(pylopdf): pylopdf plans pymupdf's TextPage.extractBLOCKS for M1 (see ROADMAP.md).
for block in textpage.extractBLOCKS():
    print(block)
# TODO(pylopdf): pylopdf extracts and inserts in display coordinates with the page rotation resolved; drop any rotation_matrix or derotation_matrix step (api/DESIGN.md G1).
point = pylopdf.Point(10, 20) * page.rotation_matrix
