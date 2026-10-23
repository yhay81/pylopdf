`page.annotations.add(FileAttachment(at, data, filename=..., ...))` embeds a
file behind one of four `AttachmentIcon` pictures, and `Annotation.file`
returns an `AnnotationFile` whose `filename`, `description`, `size`,
`read()`, and `write(data)` work as those of `Attachment` do. The codemod
rewrites `add_file_annot()`.
