`Document.subset_fonts()` also subsets the TrueType programs of simple
TrueType fonts: hayro resolves the codes that the document shows, and the
subset keeps a `cmap` with every original mapping to a kept glyph, so text
and rendering stay the same in hayro and in MuPDF. `mhlw-doc.pdf`, whose
fully embedded YuMincho serves a CID font and a WinAnsi TrueType font,
shrinks from 345 KB to 92 KB.
