Adopt pymupdf's document metadata: `metadata` adds `trapped` and
`encryption`, and `set_metadata` ignores `format` and `encryption`, writes
`trapped`, removes entries whose values are false, `"none"`, or `"null"`, and
removes the Info dictionary for an empty dictionary.
