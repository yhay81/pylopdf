`Page.replace_text()` replaces text in every font: simple, TrueType, Type 3,
and composite fonts with any CMap, in both writing modes. The replacement is
encoded through the font's own codes, `/ToUnicode` CMap, Unicode CMap, or
encoding, its advance comes from the font's widths, and a `TJ` adjustment
keeps the following text in place; a shared Form XObject is copied first and
`/ActualText` around the old glyphs goes. Text that the font cannot draw is
refused, or drawn in `fallback_font=`. The `default_char` and `max_size`
parameters are removed.
