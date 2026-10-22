"""Save an encrypted copy, open it with the user password, and check its permissions."""

import pylopdf

doc = pylopdf.Document()
page = doc.pages.new()
page.insert_text((72, 72), "Secret")
data = doc.to_bytes(dedupe=True, renumber=True, encryption=pylopdf.Encryption(user_password="user", owner_password="owner", permissions=pylopdf.PDF_PERM_PRINT))

locked = pylopdf.open(data)
assert locked.needs_password
assert locked.needs_password
locked.unlock("user")
assert not locked.needs_password
assert locked.permissions & pylopdf.PDF_PERM_PRINT
assert not locked.permissions & pylopdf.PDF_PERM_COPY
assert locked.pages[0].get_text().strip() == "Secret"
locked.save("encryption-copy.pdf")
print("encryption ok")
