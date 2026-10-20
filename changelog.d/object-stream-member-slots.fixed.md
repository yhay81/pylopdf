Rendering and extraction read each object-stream member from the slot that
editing and saving use. For a compressed cross-reference entry, hayro reads
the member that the object stream's table lists at the entry's index,
whatever number the table gives it there, and none past `/N`, while lopdf
stores every member under its number in the table; an uncompressed object
whose header names another number could likewise stand in for a member.
Loading kept the original bytes for such files, so a page could draw with one
font before any edit and another after it. Loading now reads every object
stream's table as both readers do, decoding it only as far as `/First`, and
renders from the serialized graph when they disagree, as for pdf.js
`bug1978317.pdf`, whose 65,542 members overflow a two-byte index field. On the
first six pages of the 1,455 pdf.js corpus files, text before and after a save
differed for two files and now differs for none, and cold first calls and
loads of the bundled corpus did not change.
