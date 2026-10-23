`Page.draw_line()`, `draw_rect()`, `draw_circle()`, `draw_oval()`,
`draw_polyline()`, `draw_quad()`, `draw_bezier()`, `draw_curve()`,
`draw_sector()`, `draw_squiggle()`, and `draw_zigzag()`, `Page.new_shape()`,
and `Shape` go: `page.draw()` returns a `Canvas` whose `line`, `rect`,
`circle`, `ellipse`, `polygon`, `bezier`, and `path` with `PathBuilder` draw
the same paths, with a `Color` fill and a `Stroke` in place of pymupdf's
`color`, `width`, `dashes`, `lineCap`, `lineJoin`, and opacities.
`python -m pylopdf.migrate` rewrites `draw_line`, `draw_rect`,
`draw_circle`, `draw_oval`, `draw_polyline`, `draw_bezier`,
`draw_squiggle`, and `draw_zigzag` statements into canvas blocks and marks
the rest.
