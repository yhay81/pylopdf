Replace `Page.get_links()`, `Page.insert_link()`, `Document.has_links()`,
the `LinkInfo` TypedDict, and the `LinkKind` enum with its `LINK_*`
constants by `page.links`, a sequence of `Link` handles whose `rect` and
`target` read the document on every access and edit the link when assigned.
`page.links.add(rect, target)` adds a link and `del page.links[i]` removes
one. Targets are the outline's: a URI string, `Destination(page, point,
zoom)`, `RemoteDestination(file, page, name)`, `Launch(file)`, or
`NamedAction(name)`, so links can now lead to pages, other files, files to
open, and viewer actions, where `insert_link` wrote web links only. A link
whose action pylopdf does not read, or whose destination names no page,
reads with the target `None` instead of being left out, and a destination
that leaves its coordinates unset reads without a point. `Link.xref` and
`Link.obj` give the annotation itself; editing or removing a page's links
first stores the links that `/Annots` holds as direct dictionaries as
objects of their own, as MuPDF does, so a handle never shifts onto another
link. The codemod rewrites `get_links()`,
`insert_link()` with a dictionary literal, `load_links()`, `first_link`,
`links()`, `has_links()`, and pylopdf 0.15's `add_link_annot()`, and marks
the rest.
