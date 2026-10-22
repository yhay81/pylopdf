Adopt pymupdf's extraction record shapes: `get_text("words")`
returns `Word` and `get_text("blocks")` returns `Block` NamedTuples in
pymupdf's tuple order, replacing the `WordEntry` and `BlockEntry` aliases.
The `TextPage` and `RawTextPage` TypedDicts become `TextPageDict` and
`RawTextPageDict`, leaving `TextPage` to pymupdf's handle. `get_drawings`
adds `seqno`, the paint order among paths, text runs, and images, and
`get_links` adds the link annotation's `xref` and its `/NM` `id`.
