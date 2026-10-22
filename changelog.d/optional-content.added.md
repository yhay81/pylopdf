`Document.get_ocgs`, `add_ocg`, `get_oc`, `set_oc`, `get_ocmd`, and
`set_ocmd`, `Page.get_oc_items`, and `Annot.get_oc` with pymupdf's meaning:
read and edit optional content groups (layers) and membership dictionaries,
typed by the new `OcgInfo`, `OcmdInfo`, and `VisibilityExpression`.
Rendering and extraction follow the edits, and `on` reports the default
configuration's current state, where pymupdf reports the state at opening.
