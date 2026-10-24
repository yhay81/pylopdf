`Document.subset_fonts()` cuts the embedded TrueType programs of CID fonts
with the `Identity-H` or `Identity-V` encoding down to the glyphs that the
document can draw, found by following the text state through page content,
annotation appearances, Form XObjects, tiling patterns, Type 3 glyph
procedures, and soft masks, and returns a `FontSubsetResult`. Content,
widths, and `ToUnicode` stay as they are, so extracted text and rendering do
not change: with its simple font given a copy of the program, the fully
embedded YuMincho of `mhlw-doc.pdf` shrinks from 733,908 to 99,504 bytes and
the saved file from 666 KB to 413 KB. The codemod drops pymupdf's `verbose`.
