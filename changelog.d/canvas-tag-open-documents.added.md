`Canvas.tag()` works on pages of open documents: its elements join the
document's structure tree, created when the document has none, under a
single `Document` element when the tree has one, and drawing outside every
tag scope of a tagged document is marked as an artifact.
