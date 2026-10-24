Saving gives a document without a file identifier one and renews the second
element of an edited document's identifier, as ISO 32000-2 §14.4 asks; an
incremental save renews it in its update when the update writes objects. The
new element is a digest of the content, so saving the same content twice
still writes the same bytes. Saving compresses streams at zlib level 6
instead of lopdf's 9: on the bundled corpus the compressed streams grow by
1.9% and compress about five times faster; `compression_level=9` restores
the previous effort.
