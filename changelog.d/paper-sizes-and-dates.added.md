`PaperSize` lists standard paper sizes as `(width, height)` pairs of points
that `doc.pages.new()` takes, and `pdf_date()` and `parse_pdf_date()` convert
between `datetime` values and PDF date strings. The codemod rewrites
pymupdf's `get_pdf_now()` and `paper_size()` and `paper_rect()` with a
literal name.
