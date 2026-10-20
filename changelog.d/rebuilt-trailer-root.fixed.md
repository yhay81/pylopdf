A rebuilt cross-reference table takes the newest trailer whose `/Root` reaches
the page tree. lopdf keeps the newest trailer whose `/Root` names a scanned
object number, whatever that object is and whatever generation the reference
gives: pdf.js `issue9418.pdf` names an information dictionary in its newest
trailer and raised `object ID 1 0 not found` on every page operation, and
`issue15577.pdf` names generation 1 of a catalog written as generation 0 and
raised `object ID 1 1 not found`. Loading now reads the trailers that lopdf's
rebuild examines again, resolves each `/Root` by its object number, and keeps
the newest whose root is a catalog that reaches the page tree, as hayro and
pdf.js accept only such a trailer; the document's metadata comes from that
trailer. Both files open with hayro's page count, and every page renders pixel
for pixel as hayro renders the original bytes. `peek_metadata`, which loads no
object graph in which to resolve another trailer, keeps lopdf's and still
counts no pages for them.
