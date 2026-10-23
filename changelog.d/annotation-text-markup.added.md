`page.annotations.add()` takes `Underline(rects, ...)`, `StrikeOut(rects,
...)`, and `Squiggly(rects, ...)` with the fields of `Highlight`, and every
created annotation records its creation time in `created` and `modified`.
Highlights and the new text markup are drawn by the same appearance engine
as the other annotation types, which also draws them for rendering when
another producer wrote them without an appearance. The codemod rewrites
`add_underline_annot()`, `add_strikeout_annot()`, and
`add_squiggly_annot()`.
