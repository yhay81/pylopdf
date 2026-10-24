`Span` records of `page.layout()` carry `color` (the paint as an sRGB `Color`
whose `alpha` is the opacity, or `None` for a pattern paint and for invisible
text), `render_mode`, `is_clipped`, and the font's `ascender` and `descender`,
and `Layout.to_dict()` adds pymupdf's `color`, `alpha`, `ascender`,
`descender`, and `char_flags` to each span. Spans split where the paint, the
render mode, or the clipping changes.
