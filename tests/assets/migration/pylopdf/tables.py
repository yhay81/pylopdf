"""Draw a ruled two-by-two table, then read its cells back."""

import pylopdf

doc = pylopdf.Document()
page = doc.pages.new((300, 200))
for y in (40, 80, 120):
    with page.draw() as canvas:
        canvas.line((40, y), (260, y))
for x in (40, 150, 260):
    with page.draw() as canvas:
        canvas.line((x, 40), (x, 120))
page.insert_text((50, 65), "Name", fontsize=12)
page.insert_text((160, 65), "Value", fontsize=12)
page.insert_text((50, 105), "Alpha", fontsize=12)
page.insert_text((160, 105), "42", fontsize=12)

tabs = page.tables()
assert len(tabs) == 1, tabs
table = tabs[0]
assert (len(table.rows), len(table.rows[0])) == (2, 2)
header, row = table.rows
assert list(header) == ["Name", "Value"], header
assert list(row) == ["Alpha", "42"], row

for tab in page.tables(strategy="lines"):
    assert tab.rows[1][1] == "42"
print("tables ok")
