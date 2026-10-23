Replace `Page.annots()`, `Page.add_highlight_annot()`, and
`Document.has_annots()` with `page.annotations`, a sequence of `Annotation`
handles for every annotation but links, widgets, and popups, and replace the
`AnnotationInfo` dictionaries with typed properties: `type`, an
`AnnotationType` member, `rect`, `contents`, `author`, `subject`, `name`,
`icon`, `intent`, `created`, `modified`, `color` and `interior_color` as
`Color` records, `opacity`, `flags`, an `AnnotationFlags` value, `xref`,
`obj`, and `page`. `page.annotations.add(Highlight(rects, color=,
opacity=, contents=))` adds a highlight and returns its handle, and `del
page.annotations[i]` removes an annotation with its popup, which pylopdf
could not do before. The codemod rewrites `annots()`, `add_highlight_annot()`,
`has_annots()`, `first_annot`, `delete_annot()`, and `fitz.Annot`, and marks
the rest.
