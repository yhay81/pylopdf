"""Save an encrypted copy, open it with the user password, and check its permissions."""

import fitz

doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 72), "Secret")
data = doc.tobytes(
    garbage=3,
    deflate=True,
    encryption=fitz.PDF_ENCRYPT_AES_256,
    user_pw="user",
    owner_pw="owner",
    permissions=fitz.PDF_PERM_PRINT,
)

locked = fitz.open(stream=data, filetype="pdf")
assert locked.needs_pass
assert locked.is_encrypted
locked.authenticate("user")
assert not locked.is_encrypted
assert locked.permissions & fitz.PDF_PERM_PRINT
assert not locked.permissions & fitz.PDF_PERM_COPY
assert locked[0].get_text().strip() == "Secret"
locked.save("encryption-copy.pdf", garbage=1)
print("encryption ok")
