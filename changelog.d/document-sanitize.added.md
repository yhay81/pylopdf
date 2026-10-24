`Document.sanitize()` removes JavaScript, automatic and non-navigation
actions, embedded files, metadata, hidden text (through the redaction
engine, so the text around it stays in place), comments, page thumbnails,
and optionally links and form values, prunes the objects that nothing
reaches any more, and returns a `SanitizationReport`. The document is saved
in full afterwards. It replaces pymupdf's `Document.scrub`.
