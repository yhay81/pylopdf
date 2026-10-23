Opening an RC4 or AES-128 document with its owner password now decrypts its
content, which lopdf 0.45 decrypted with a wrong key into empty text. Saving
with encryption now also encrypts the strings of stream dictionaries, except a
signature's `/Contents`, and opening decrypts them.
`probe` of an AES-128 document that opens without a password now counts its
pages instead of reporting none.
