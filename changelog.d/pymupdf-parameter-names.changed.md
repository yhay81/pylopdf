Adopt pymupdf's parameter names and keyword-only options:
`Page.show_pdf_page` takes `docsrc` instead of `src`, and `Pixmap.save`
takes `filename` instead of `path`. `Page.find_tables` takes keyword-only
`clip` and `strategy`, whose default is now pymupdf's `None`, meaning
`"lines"`.
