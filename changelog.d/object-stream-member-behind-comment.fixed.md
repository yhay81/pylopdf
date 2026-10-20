Object-stream members that a comment precedes load instead of being dropped.
pdfTeX writes a `% 2 0 obj` line before each member, and lopdf skips only white
space there, so pdf.js `bug1539074.pdf` and `bug1539074.1.pdf` lost every
member, the catalog and the page tree among them, and raised
`object ID 15 0 not found` on every page operation, while hayro, MuPDF, and
pdf.js read the page. Loading now parses each member that lopdf did not load
from the slot that hayro reads, after white space and comments, in an object
stream that lopdf expanded and that both decode alike. Both files open with
their page, which renders pixel for pixel as hayro renders the original bytes.
`peek_metadata` still reads members with lopdf's parser and counts no pages
for them.
