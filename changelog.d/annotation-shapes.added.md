`page.annotations.add(Square(rect, ...))` and `Circle(rect, ...)` add a
rectangle or an ellipse with `color`, `interior_color`, a `Border(width,
style=, dashes=)`, `opacity`, `blend_mode`, and `contents`, written with an
appearance that pylopdf draws through the canvas, so every viewer shows it
alike. `Annotation.border` and `Annotation.blend_mode` read any annotation's
border and blend mode, and rendering draws squares and circles that other
producers wrote without an appearance, without changing the document. The
codemod rewrites `add_rect_annot()` and `add_circle_annot()`.
