Add `Page.draw()`, which returns a `Canvas` that draws paths, text, images,
and other pages in the order of the calls and writes them as one Form XObject
when its `with` block ends, or nothing after an exception. Paths take a
`Color` fill and a `Stroke`, from a `PathBuilder` or the shortcuts `rect`
(with rounded corners), `line`, `circle`, `ellipse`, `polygon`, and `bezier`;
`text` and `textbox` shape and embed OpenType faces once per canvas, fill or
stroke the glyphs, and report how a textbox fits with `TextFit`; `image` and
`page` store each image and page once. The scopes `clip`, `transform`,
`opacity` (a transparency group), and `blend` apply to everything drawn
inside them, `draw(item)` paints a `Path` item from `Page.content()` again,
and `underlay=True` writes below the page's content.
