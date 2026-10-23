"""Rearrange pages, render one, and inspect the images that a page draws."""

import os
import tempfile

import pylopdf

doc = pylopdf.Document()
for label in ("A", "B", "C"):
    page = doc.pages.new((200, 200))
    with page.draw() as canvas:
        canvas.text(label, (50, 100), size=24)

doc.pages.extend(doc.pages[0])
assert len(doc.pages) == 4
del doc.pages[1]
doc.pages.select([2, 0, 1])
assert [page.text().strip() for page in doc.pages] == ["A", "A", "C"]

other = pylopdf.Document()
with other.pages.new((200, 200)).draw() as canvas:
    canvas.text("D", (50, 100), size=24)
doc.pages.extend(other.pages)
assert doc.pages[-1].text().strip() == "D"

doc.language = "zh-TW"
doc.page_mode = "UseThumbs"
assert doc.page_mode == "UseThumbs"

doc.pages[0].rotation = 90
pixmap = doc.pages[0].pixmap(dpi=36)
assert (pixmap.width, pixmap.height) == (100, 100)

with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "page.png")
    pixmap.save(path)
    page = doc.pages.new((200, 200))
    with page.draw() as canvas:
        canvas.image(pylopdf.Rect(20, 20, 120, 120), path)
    xref = page.content(text_runs=False, paths=False, annotations=False).images[-1].xref
    assert [image.xref for image in page.resources.images] == [xref]
    assert (doc.images[xref].width, doc.images[xref].height) == (100, 100)
    assert doc.images[xref].extract().format == "png"
print("pages ok")
