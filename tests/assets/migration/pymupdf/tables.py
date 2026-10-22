"""Draw a ruled two-by-two table, then read its cells back."""

import fitz

doc = fitz.open()
page = doc.new_page(width=300, height=200)
for y in (40, 80, 120):
    page.draw_line((40, y), (260, y))
for x in (40, 150, 260):
    page.draw_line((x, 40), (x, 120))
page.insert_text((50, 65), "Name", fontsize=12)
page.insert_text((160, 65), "Value", fontsize=12)
page.insert_text((50, 105), "Alpha", fontsize=12)
page.insert_text((160, 105), "42", fontsize=12)

tabs = page.find_tables()
assert len(tabs.tables) == 1, tabs.tables
table = tabs[0]
assert (table.row_count, table.col_count) == (2, 2)
header, row = table.extract()
assert list(header) == ["Name", "Value"], header
assert list(row) == ["Alpha", "42"], row

for tab in page.find_tables(strategy="lines").tables:
    assert tab.extract()[1][1] == "42"
print("tables ok")
