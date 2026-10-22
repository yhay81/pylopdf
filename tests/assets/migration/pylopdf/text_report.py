"""Build a three-page report, then read its text, words, and outline back."""

import pylopdf

doc = pylopdf.Document()
for number in range(3):
    page = doc.pages.new((300, 200))
    page.insert_text((40, 60), f"Chapter {number + 1}", fontsize=14)
    page.insert_text((40, 90), "The quick brown fox", fontsize=11)

toc = [[1, "Chapter 1", 1], [2, "Details", 2], [1, "Chapter 3", 3]]
doc.set_outline([(entry[0], entry[1], entry[2] - 1) for entry in toc])
assert [[entry.level, entry.title, entry.page + 1] for entry in doc.get_outline()] == toc

for entry in [[entry.level, entry.title, entry.page + 1] for entry in doc.get_outline()]:
    level, title, page_number = entry
    assert 1 <= page_number <= len(doc.pages), entry

page = doc.pages[1]
assert "Chapter 2" in page.text()
hits = page.search("brown")
assert len(hits) == 1 and hits[0].width > 0
words = [word.text for word in page.words()]
assert words[:2] == ["Chapter", "2"], words

doc.metadata.update({"title": "Report", "author": "Example"})
reopened = pylopdf.open(doc.to_bytes())
assert reopened.metadata.get("title", "") == "Report"
assert [[entry.level, entry.title, entry.page + 1] for entry in reopened.get_outline()][1] == [2, "Details", 2]
print("text_report ok")
