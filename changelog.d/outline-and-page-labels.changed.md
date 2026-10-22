Replace `Document.get_toc()` and `set_toc(toc)` with `Document.outline`:
`doc.outline.entries()` returns `OutlineEntry(level, title, destination)`
records, assigning a list of them replaces the outline, and `del doc.outline`
removes it. Page numbers in a `Destination` start at 0, like every other
pylopdf page number, where `get_toc` counted from 1. Replace
`get_page_labels()`, `set_page_labels(labels)`, and `Page.get_label()` with
`Document.page_labels`, a list of `PageLabelRange(start, style, prefix,
first)` records with the new `PageLabelStyle` enum, and `Page.label`. The
codemod rewrites these calls, and renames pymupdf's `Outline` class to
`OutlineItem`. The limits are unchanged.
