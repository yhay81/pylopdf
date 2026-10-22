"""pymupdf features that a person has to migrate: the codemod marks each one."""

import pylopdf

# TODO(pylopdf): pylopdf does not provide pymupdf's TOOLS. Filter pylopdf's diagnostics as PylopdfWarning with the warnings module.
pylopdf.TOOLS.set_small_glyph_heights(True)
doc = pylopdf.Document()
page = doc.pages.new()
# TODO(pylopdf): pylopdf plans the capability of pymupdf's Page.get_textpage in the Reading milestone of ROADMAP.md.
textpage = page.get_textpage()
# TODO(pylopdf): pylopdf plans the capability of pymupdf's TextPage.extractBLOCKS in the Reading milestone of ROADMAP.md.
for block in textpage.extractBLOCKS():
    print(block)
# TODO(pylopdf): pylopdf extracts and inserts in display coordinates with the page rotation resolved; drop any rotation_matrix or derotation_matrix step.
point = pylopdf.Point(10, 20) * page.rotation_matrix
