Codes and flags are enum members only: pylopdf has no `PDF_ANNOT_*`,
`PDF_ANNOT_IS_*`, or `PDF_PERM_*` module constants, and the codemod rewrites
pymupdf's, including names imported with `from fitz import ...`, into
`AnnotationType`, `AnnotationFlags`, and `Permissions` members, so that
`PDF_ANNOT_3D` becomes `AnnotationType.THREE_D`. A missing pymupdf constant's
`AttributeError` names the hint of its own group.
