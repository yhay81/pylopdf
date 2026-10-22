Raise typed errors for unreadable input: input that is not a readable PDF,
empty input included, raises the new `MalformedPdfError`, a `PdfError`, and a
missing file raises Python's built-in `FileNotFoundError`. They replace
`PdfError` when opening a document, probing it, and reading font, image, and
OCR model paths; password and limit failures keep `PasswordError` and
`LimitError`.
