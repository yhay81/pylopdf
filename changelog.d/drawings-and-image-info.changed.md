`page.content(text_runs=False, images=False).paths` replaces
`Page.get_drawings()`, and `page.content(text_runs=False, paths=False).images`
replaces `Page.get_image_info()`. The new `text_runs`, `paths`, and `images`
options of `Page.content()` leave out a kind of item without building it, so
reading only the paths or the images of a page costs about what
`get_drawings` and `get_image_info` cost, and reading both takes one
interpretation instead of two. A `Path` record holds the segments (`MoveTo`,
`LineTo`, `CurveTo`, and `ClosePath`), `fill`, `fill_rule`, `stroke`, and
`bbox` that a `DrawingInfo` dictionary held as `items`, `fill`, `even_odd`,
`color`, `width`, `lineCap`, `lineJoin`, `dashes`, and `rect`, and an `Image`
record holds the object number, box, matrix, size, color space, mask, encoded
size, and resolution that `get_image_info` returned. The `DrawingInfo`,
`DrawingItem`, and `ImageInfo` types leave the public surface. A stroke joins
the fill before it only when it directly follows a fill of the same segments,
where `get_drawings` also joined a later stroke of the same geometry.
`Page.content(max_elements=...)` now also counts stroke dash lengths, so one
long dash array that many strokes repeat cannot multiply memory, and building
the records of a page full of text takes about half as long as before. The
codemod rewrites `get_drawings`, `get_cdrawings`, and `get_image_info` calls
and marks the code that reads their dictionaries.
