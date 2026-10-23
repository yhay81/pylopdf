`doc.flatten()` draws annotation and widget appearances into page content
and removes them, keeping links, as pymupdf's `bake()` does; the codemod
rewrites `bake()` calls.
