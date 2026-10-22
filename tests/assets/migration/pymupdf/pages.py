"""Rearrange pages, render one, and inspect the images that a page draws."""

import os
import tempfile

import fitz

doc = fitz.open()
for label in ("A", "B", "C"):
    page = doc.new_page(width=200, height=200)
    page.insert_text((50, 100), label, fontsize=24)

doc.copy_page(0)
assert doc.page_count == 4
doc.delete_page(1)
doc.select([2, 0, 1])
assert [page.get_text().strip() for page in doc] == ["A", "A", "C"]

other = fitz.open()
other.new_page(width=200, height=200).insert_text((50, 100), "D", fontsize=24)
doc.insert_pdf(other)
assert doc[-1].get_text().strip() == "D"

doc.set_language("zh-TW")
doc.set_pagemode("UseThumbs")
assert doc.pagemode == "UseThumbs"

doc[0].set_rotation(90)
pixmap = doc[0].get_pixmap(dpi=36)
assert (pixmap.width, pixmap.height) == (100, 100)

with tempfile.TemporaryDirectory() as folder:
    path = os.path.join(folder, "page.png")
    pixmap.save(path)
    page = doc.new_page(width=200, height=200)
    xref = page.insert_image(fitz.Rect(20, 20, 120, 120), filename=path)
    assert [image[0] for image in page.get_images()] == [xref]
    assert (doc.extract_image(xref)["width"], doc.extract_image(xref)["height"]) == (100, 100)
    assert doc.extract_image(xref)["ext"] == "png"
print("pages ok")
