`ImageObject.replace(source)` puts a JPEG or PNG file, encoded bytes, or a
`Pixmap` in an image's place under its object number, so every placement of
the image, on any page and in any Form XObject, draws the new one; the image
keeps its layer and structure parent, and input is limited as
`canvas.image` limits it. This replaces pymupdf's `Page.replace_image`, and
the migration codemod rewrites it.
