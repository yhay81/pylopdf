`Canvas.link(rect, target, *, alt=None)` adds a link to a URI or to a
`Destination` of a new document's page from `pylopdf.create()`; in a tagged
document the link joins the tag scope it is drawn in, and the new
`TagRole.LINK` is the scope that PDF/UA expects around it.
Link rectangles follow canvas transforms, and URI and description input is
bounded together before copying it into Rust.
