`Document.save(path, /, *, compress=True, prune=True, dedupe=False,
renumber=False, object_streams=False, encryption=None)` and
`Document.to_bytes(...)`, which replaces `tobytes`, take one boolean per
effect instead of `garbage=` levels and `deflate=`: streams are now
compressed and unreachable objects removed by default, `dedupe` merges
identical objects and streams, and `renumber` numbers objects consecutively.
`encryption=Encryption(user_password=..., owner_password=...,
permissions=...)` writes AES-256 encryption and replaces `user_pw=`,
`owner_pw=`, and `permissions=`. `Document.unlock(password)` replaces
`authenticate` and returns `Access.USER` or `Access.OWNER`, raising
`PasswordError` for a wrong password. `Document.needs_password` is now true
until the document is unlocked and replaces `is_encrypted`, and
`Document.permissions` returns `Permissions` flags. The codemod migrates
pymupdf's and pylopdf 0.15's save options and `authenticate` calls.
