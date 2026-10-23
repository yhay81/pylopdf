"""Read and rewrite low-level objects through pymupdf's xref methods."""

import pylopdf

doc = pylopdf.Document()
page = doc.pages.new((100, 100))
pixmap = page.pixmap(dpi=18)
with page.draw() as canvas:
    canvas.image(pylopdf.Rect(0, 0, 50, 50), pixmap)
xref = page.content(text_runs=False, paths=False, annotations=False).images[-1].xref

catalog = doc.catalog.xref
assert catalog > 0
assert max(doc.objects) + 1 > max(catalog, xref)

samples = doc.objects[xref].read()
assert samples == bytes(pixmap)
assert doc.objects[xref].read(decode=False) != samples

doc.objects[xref].write(bytes(len(samples)))
assert doc.objects[xref].read() == bytes(len(samples))
print("objects ok")
