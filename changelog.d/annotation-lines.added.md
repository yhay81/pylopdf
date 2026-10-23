`page.annotations.add()` takes `LineAnnotation(start, end, ...)`,
`Polygon(vertices, ...)`, `PolyLine(vertices, ...)`, and `Ink(strokes, ...)`,
drawn with their appearances; lines and polylines take `line_endings`, a
pair of `LineEnding` members such as `CLOSED_ARROW`. `Annotation.vertices`,
`strokes`, and `line_endings` read them back in display coordinates, and
rendering draws such annotations that other producers wrote without an
appearance. One annotation holds at most 65,536 points (`LimitError` code
`annotation_point_count`). The codemod rewrites `add_line_annot()`,
`add_polygon_annot()`, `add_polyline_annot()`, and `add_ink_annot()`.
