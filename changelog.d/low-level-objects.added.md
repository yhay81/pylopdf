A typed low-level object model replaces pymupdf's string-based `xref_*`
methods (roadmap F5). `Document.objects` maps object numbers to indirect
objects, with `add()` and `add_stream()` creating new ones;
`Document.trailer`, `Document.catalog`, and `Page.obj` are live dictionary
handles; and `Document.page_xref()` and `Page.xref` return page object
numbers. Values are `None`, `bool`, `int`, `float`, `Name`, `String`, and
live `Array`, `Dictionary`, and `Stream` handles whose writes take effect at
once. `Stream.read()` decodes through every filter under a 64 MiB default
bound (`stream_data_size`), and handles raise `StaleObjectError` once their
object is removed or renumbered.
