`with doc.transaction(name):` makes a group of edits atomic: when the block
raises, the document returns to its state before it. Each transaction that
changed the document becomes one step of `doc.history`, a `History` with
`undo()`, `redo()`, `can_undo`, `can_redo`, `steps`, `position`, `clear()`,
and a `max_size` memory budget, 256 MiB by default, that drops the oldest
steps first. An edit outside a transaction ends the history. The codemod
rewrites pymupdf's `journal_undo`, `journal_redo`, `journal_can_do`,
`journal_position`, and `journal_op_name`.
