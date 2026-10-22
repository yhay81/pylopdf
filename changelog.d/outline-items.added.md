`doc.outline` is a tree of `OutlineItem` handles: iterate over the top-level
items, follow `children`, or visit every item with `doc.outline.walk()`, and
assign an item's `title`, `destination`, `color`, `bold`, `italic`, or
`is_open` to edit it in place, with the outline's `/Count` entries recounted
for `is_open`. Destinations can be a `Destination(page, point, zoom)` in this
document, a `RemoteDestination` in another file, a `Launch`, a
`NamedAction`, or a URI string, and named destinations read as the
`Destination` they name.
