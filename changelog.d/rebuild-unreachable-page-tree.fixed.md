Documents whose cross-reference entries miss the catalog or the root
page-tree node open with their pages instead of raising on every page
operation. lopdf trusts any cross-reference data that it resolves, and it
moves a `startxref` that misses by up to 64 bytes onto a nearby table, whose
entries can miss their objects in a file that lost bytes: pdf.js
`bug1130815.pdf` loaded 2 of its 66 objects and raised
`object ID 50 0 not found`. When the objects that lead to the page tree did
not load, loading now rebuilds the table by scanning object headers, as
hayro, MuPDF, and pdf.js do, keeps the rebuilt document when its page tree
is reachable, and reports the `rebuilt` cross-reference warning;
`probe` repeats a metadata read that counts no pages in the same way. Nine
pdf.js corpus files that raised on every page operation now open with hayro's
page count, and every page renders pixel for pixel as hayro renders the
original bytes.
