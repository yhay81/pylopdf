`Document.complexity` and `Document.rewrite_images()` return the frozen
dataclasses `DocumentComplexity` and `ImageCompressionResult` instead of
dictionaries: read `doc.complexity.page_count` and `result.rewritten` where
0.15 read `["page_count"]` and `["rewritten"]`. Indexing a record raises a
`TypeError` that says so, and the codemod rewrites such reads of
`complexity`, `rewrite_images()`, and the names bound to them.
