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
with page.draw() as canvas:
    canvas.text("Name", (50, 65), size=12)
    canvas.text("Value", (160, 65), size=12)
    canvas.text("Alpha", (50, 105), size=12)
    canvas.text("42", (160, 105), size=12)

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
