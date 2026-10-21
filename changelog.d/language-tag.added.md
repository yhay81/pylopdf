`Document.language_tag` and `Document.set_language_tag` read and write the
catalog's complete BCP 47 `/Lang` tag, such as `zh-TW` or `sr-Latn-RS`. They
replace pymupdf's `language` and `set_language`, which keep at most three
letters, so that `zh-TW` and `zh-HK` both become `zh-Hant`; the migration
codemod rewrites `set_language` calls.
