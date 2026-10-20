`StalePageError` derives from the new `StaleObjectError`, the base for every
stale document-backed handle, so `except StaleObjectError` catches stale pages
as well. Both remain `PdfError` subclasses.
