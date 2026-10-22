Make encryption explicit: `Document.save` and
`Document.tobytes` take pymupdf's `encryption=` with the new
`EncryptionMethod` members and `PDF_ENCRYPT_*` constants, defaulting to
`PDF_ENCRYPT_NONE`. `PDF_ENCRYPT_AES_256` writes AES-256 encryption, the
other methods raise `PdfError`, and `user_pw=` or `owner_pw=` without
`PDF_ENCRYPT_AES_256` now raises instead of encrypting implicitly.
