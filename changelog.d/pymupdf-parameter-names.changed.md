Adopt pymupdf's parameter names and keyword-only options:
`Page.search_for` takes `text` instead of `needle`, `Page.show_pdf_page`
takes `docsrc` instead of `src`, `Pixmap.save` takes `filename` instead of
`path`, and `Document.save` and `Document.tobytes` take `use_objstms`
instead of `object_streams`. `Page.find_tables` takes keyword-only `clip`
and `strategy`, whose default is now pymupdf's `None`, meaning `"lines"`;
`Document.new_page` takes keyword-only `width` and `height`; and
`Page.insert_textbox` expands tabs to pymupdf's one column instead of eight
and takes `rect` and `text` positionally only.
