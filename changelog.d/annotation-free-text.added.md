`page.annotations.add(FreeText(rect, text, ...))` writes text in a box with
its appearance: `font`, `size`, `text_color`, a fill `color`, a `border`,
`align`, `rotate` (clockwise as the page is displayed), and a `callout` line
with a `callout_ending`. Without `font`, non-Latin text uses an installed
font extra, as on a canvas. `Annotation.rotation` reads how far an
annotation's content is turned as displayed. The codemod rewrites
`add_freetext_annot()`.
