Replace `Document.set_fallback_font()` with `Document.fallback_fonts`, a
mapping from `"ja"`, `"ko"`, `"zh-CN"`, or `"zh-TW"` to a
`FallbackFont(sans, serif)` record of `Font` values, which a document shares
rather than copies. Until it is edited it lists the installed font extras;
assigning a language changes that language, assigning a mapping replaces all
of them (`{}` turns fallback rendering off), and `del doc.fallback_fonts`
returns to the installed extras. The codemod rewrites
`set_fallback_font(None)` and marks the other calls.
