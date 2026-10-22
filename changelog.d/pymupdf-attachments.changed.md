Replace the `embfile_add`, `embfile_get`, `embfile_del`, and `embfile_names`
methods with the `Document.attachments` mapping: iterate it for
the sorted names, `add(name, data, /, *, filename=None, description=None,
max_size=...)` returns an `Attachment`, `del doc.attachments[name]` deletes
one, and a missing name raises `KeyError`. An `Attachment` handle reports
pymupdf's `embfile_info` metadata as `filename`, `description`, `size`,
`creation_date`, and `mod_date`, reads with `read(*, max_size=...)`, and
replaces its data with `write(data, /, *, max_size=...)`, which pymupdf calls
`embfile_upd`. The limits are unchanged.
