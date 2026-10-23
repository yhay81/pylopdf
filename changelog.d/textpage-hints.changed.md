Migration hints and `python -m pylopdf.migrate` name the page methods that
replace pymupdf's `TextPage`, `Page.get_textpage`, and the `extract*` methods
of a text page, because pylopdf caches each page's interpretation instead of
exposing a text-page object; `doc.search_page_for(i, text)` points at
`doc.pages[i].search(text)`.
