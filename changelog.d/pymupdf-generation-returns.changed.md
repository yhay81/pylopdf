Return pymupdf's values from page placement (roadmap M0):
`Page.insert_image` returns the image XObject's xref and
`Page.show_pdf_page` the Form XObject's xref instead of `None`; a repeated
placement returns the xref of the shared XObject.
