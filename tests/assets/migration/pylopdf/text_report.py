"""Build a three-page report, then read its text, words, and outline back."""

import pylopdf

doc = pylopdf.Document()
for number in range(3):
    page = doc.pages.new((300, 200))
    with page.draw() as canvas:
        canvas.text(f"Chapter {number + 1}", (40, 60), size=14)
        canvas.text("The quick brown fox", (40, 90), size=11)

toc = [[1, "Chapter 1", 1], [2, "Details", 2], [1, "Chapter 3", 3]]
doc.outline = [pylopdf.OutlineEntry(item[0], item[1], pylopdf.Destination(item[2] - 1) if item[2] > 0 else None) for item in toc]
assert [[entry.level, entry.title, -1 if entry.page is None else entry.page + 1] for entry in doc.outline.entries()] == toc

for entry in [[entry.level, entry.title, -1 if entry.page is None else entry.page + 1] for entry in doc.outline.entries()]:
    level, title, page_number = entry
    assert 1 <= page_number <= len(doc.pages), entry

page = doc.pages[1]
assert "Chapter 2" in page.text()
hits = page.search("brown")
assert len(hits) == 1 and hits[0].width > 0
words = [word.text for word in page.words()]
assert words[:2] == ["Chapter", "2"], words
width = pylopdf.Font.standard("Helvetica").text_width("The quick brown fox", 11)
font = pylopdf.Font.standard("Helvetica")
assert font.text_width("The quick brown fox", 11) == width
assert 40 + width < page.rect.width, width

doc.metadata.update({"title": "Report", "author": "Example"})
reopened = pylopdf.open(doc.to_bytes())
assert reopened.metadata.get("title", "") == "Report"
assert [[entry.level, entry.title, -1 if entry.page is None else entry.page + 1] for entry in reopened.outline.entries()][1] == [2, "Details", 2]
print("text_report ok")
