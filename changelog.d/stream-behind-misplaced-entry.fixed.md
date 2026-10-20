A stream whose wrong `/Length` hides its `endstream` behind another
cross-reference entry's offset keeps its data. lopdf and pylopdf's recovery
parse searched for `endstream` only up to the next entry offset, even one
that points into the stream's own data, and skipped the object, while hayro
reads up to the first `endstream`: page 2 of pdf.js `issue11678.pdf` lost
its content stream and extracted nothing. An entry offset at which no object
header starts no longer ends an object, and the recovery parse takes the
first `endstream`, as hayro does, when the declared length and the sole
`endstream` that `endobj` follows both fail. The page now extracts 1,014
characters and renders pixel for pixel as hayro renders the original bytes.
