A saved cross-reference table ends at the highest object that the save
keeps, so `/Size` no longer counts objects that pruning removed, which qpdf
reported; and a document read with a cross-reference stream and saved with
`cross_reference="table"` no longer carries the stream's entries into the
trailer.
