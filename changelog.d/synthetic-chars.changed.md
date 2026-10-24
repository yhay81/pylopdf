`page.layout(chars=True)` lists each space that pylopdf infers from a gap as
a `Char` with the new `is_synthetic` flag set, so a span's characters join to
its text, and `Layout.to_dict()` gives each character pymupdf's `synthetic`
key.
