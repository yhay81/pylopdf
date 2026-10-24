An unlocked document with AES-128 or AES-256 (revision 6) encryption can be
saved incrementally: the update is encrypted with the document's own
encryption, so its passwords and permissions still apply. The document keeps
its original bytes and the password that unlocked it while it is open. RC4
and revision 5 inputs cannot, since pylopdf writes neither.
