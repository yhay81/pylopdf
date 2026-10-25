`Canvas.draw(item)` paints the `Image` items that `Page.content()` reads:
on a page of the item's own document it draws the image object again, and on
another document's page it copies the object, with its masks and the objects
it reaches, when `draw` is called. Redrawn on a blank page, the paths and
images of every page of the test corpus render within 16 levels per channel
of the original. A stroke width of 0 now draws the thinnest line, as in PDF,
so hairlines that `Page.content()` reads can be drawn again.
