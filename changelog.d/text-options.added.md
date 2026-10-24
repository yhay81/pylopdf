`page.text()`, `words()`, `layout()`, `search()`, and `doc.text()` take
keyword-only text options: `clip` (the text in a rectangle, which replaces
pymupdf's `get_textbox`), `clipped`, `ligatures`, `whitespace`,
`dehyphenate`, `unmapped` (skip a glyph without Unicode, or stand U+FFFD or
its glyph ID in for it), and, except on `text()`, `exact_bboxes` (glyph boxes
from outlines).
