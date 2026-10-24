Text extraction leaves out glyphs that the page boundary or the clip paths
hide entirely, as pymupdf does by default; `clipped=True` keeps them.
Invisible text, such as an OCR layer, is still extracted.
