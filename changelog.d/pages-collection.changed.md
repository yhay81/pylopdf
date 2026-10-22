`Document.pages`, a sequence of `Page` views, replaces the page methods of
`Document`: index and slice it (`doc.pages[i]`, `doc.pages[2:5]`), measure it
with `len(doc.pages)`, and iterate it instead of `doc[i]`, `len(doc)`,
iteration over the document, `page_count`, and `load_page`. `pages.new(size,
*, index=None)` replaces `new_page`, `pages.insert(index, pages)` and
`pages.extend(pages)` replace `insert_pdf` and `copy_page` for pages of
another document or of the same one, `del doc.pages[i]` and
`del doc.pages[a:b]` replace `delete_page` and `delete_pages`, and
`pages.select(indexes)`, `pages.move(index, to)`, and `pages.labeled(label)`
complete it. A copy of a page of the same document is an independent page
object that shares content streams and resources until either is edited.
`Page.index` and `Page.document` replace `number` and `parent`, and two `Page`
views are equal when they view the same page of the same document
generation. `doc.pages[i].xref`, `.text()`, and `.get_pixmap()` replace
`page_xref`, `get_page_text`, and `get_page_pixmap`. Indexing or iterating a
`Document` raises a `TypeError` that points to `doc.pages`.
