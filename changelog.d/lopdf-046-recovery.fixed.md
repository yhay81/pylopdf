Damaged files that only have cross-reference streams now open: lopdf 0.46
corrects a slightly miswritten offset onto a nearby cross-reference stream and
rebuilds such files from their object headers, where lopdf 0.45 rejected them.
A file whose offsets were corrected onto a section that misses its later
objects, such as one that gained or lost a few bytes, is now rebuilt instead
of opening without those objects.
