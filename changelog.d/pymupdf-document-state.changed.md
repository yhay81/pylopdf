Adopt pymupdf's document state: `authenticate` returns pymupdf's codes even
after an empty user password opened the document instead of always returning
1. `metadata` adds `trapped` and `encryption`, and `set_metadata` ignores
`format` and `encryption`, writes `trapped`, removes entries whose values are
false, `"none"`, or `"null"`, and removes the Info dictionary for an empty
dictionary.
