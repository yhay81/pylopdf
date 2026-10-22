Adopt pymupdf's parameter names and keyword-only options:
`Page.show_pdf_page` takes `docsrc` instead of `src`, and `Pixmap.save`
takes `filename` instead of `path`. `Page.find_tables` takes keyword-only
`clip` and `strategy`, whose default is now pymupdf's `None`, meaning
`"lines"`, and `Page.insert_textbox` expands tabs to pymupdf's one column
instead of eight and takes `rect` and `text` positionally only.
