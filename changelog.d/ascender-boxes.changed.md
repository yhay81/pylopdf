Glyph, span, word, and line boxes span the font's ascender and descender,
scaled to one em together when they span less, as pymupdf's boxes do, instead
of a fixed 0.8 and 0.2 em; fonts whose program hayro does not hand out, such
as Type 1 fonts, keep 0.8 and -0.2. `bench/reading.py` publishes their IoU
against pymupdf's boxes on the bundled corpus.
