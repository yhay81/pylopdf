`Encryption(method="aes-128")` writes AES-128 encryption (revision 4), and
`Encryption.KEEP` writes the encryption a document was opened with, keeping
its file key, passwords, and permissions, for AES-128 and AES-256 inputs.
Writing RC4 raises `ValueError`, and keeping RC4 or the deprecated revision 5
of AES-256 raises `PdfError`. Every standard security handler, RC4 40 and
128 bits, revision 4 with RC4 and AES crypt filters, metadata left
unencrypted, and both AES-256 revisions, now has a fixture that opens with
the user and the owner password. The codemod rewrites pymupdf's
`PDF_ENCRYPT_KEEP` and `PDF_ENCRYPT_AES_128`.
