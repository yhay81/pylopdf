"""Read and rewrite low-level objects through pymupdf's xref methods."""

import fitz

doc = fitz.open()
page = doc.new_page(width=100, height=100)
pixmap = page.get_pixmap(dpi=18)
xref = page.insert_image(fitz.Rect(0, 0, 50, 50), pixmap=pixmap)

catalog = doc.pdf_catalog()
assert catalog > 0
assert doc.xref_length() > max(catalog, xref)

samples = doc.xref_stream(xref)
assert samples == pixmap.samples
assert doc.xref_stream_raw(xref) != samples

doc.update_stream(xref, bytes(len(samples)))
assert doc.xref_stream(xref) == bytes(len(samples))
print("objects ok")
