`Font` reports its `bbox`, `glyph_count`, and `is_monospaced`, lists the
characters it has glyphs for with `chars()`, gives the advance and ink box of
characters drawn alone with `char_widths(text, size)` and `char_bbox(char)`,
and returns the bytes an OpenType face was loaded from with `program()`.
`pylopdf.glyph_name(char)` and `pylopdf.glyph_text(name)` translate between
characters and glyph names by the Adobe Glyph List.
