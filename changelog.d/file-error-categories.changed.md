Raise pymupdf's error categories for unreadable input (roadmap M0): a missing
file raises Python's built-in `FileNotFoundError`, input that is not a
readable PDF raises the new `FileDataError`, which derives from both
`PdfError` and `RuntimeError`, and an empty file or stream raises the new
`EmptyFileError`, a `FileDataError`. They replace `PdfError` when opening a
document, probing metadata, and reading font, image, and OCR model paths;
password and limit failures keep `PasswordError` and `LimitError`.
