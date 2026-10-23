"""Build a three-page report, then read its text, words, and outline back."""

import fitz

doc = fitz.open()
for number in range(3):
    page = doc.new_page(width=300, height=200)
    page.insert_text((40, 60), f"Chapter {number + 1}", fontsize=14)
    page.insert_text((40, 90), "The quick brown fox", fontsize=11)

toc = [[1, "Chapter 1", 1], [2, "Details", 2], [1, "Chapter 3", 3]]
doc.set_toc(toc)
assert doc.get_toc() == toc

for entry in doc.get_toc():
    level, title, page_number = entry
    assert 1 <= page_number <= doc.page_count, entry

page = doc[1]
assert "Chapter 2" in page.get_text()
hits = page.search_for("brown")
assert len(hits) == 1 and hits[0].width > 0
words = [word[4] for word in page.get_text("words")]
assert words[:2] == ["Chapter", "2"], words
width = fitz.get_text_length("The quick brown fox", fontname="helv", fontsize=11)
font = fitz.Font("helv")
assert font.text_length("The quick brown fox", fontsize=11) == width
assert 40 + width < page.rect.width, width

doc.set_metadata({"title": "Report", "author": "Example"})
reopened = fitz.open("pdf", doc.tobytes())
assert reopened.metadata["title"] == "Report"
assert reopened.get_toc()[1] == [2, "Details", 2]
print("text_report ok")
