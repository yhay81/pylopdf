`Font` has no public constructor: `Font.standard(name)` takes the PostScript
name of a Standard 14 font, which `StandardFontName` lists in place of
`Base14_fontnames` and `Base14_fontdict`; `Font.from_file(path)` and
`Font.from_bytes(data)` load an OpenType face, with `index=`, `weight=`, and
`max_size=` in place of `fontindex=` and `max_font_size=`; and
`Font.installed(language=..., family=...)` selects the face of an installed
font extra, with `serif=`, `weight=`, and `italic=` in place of `is_serif=`,
`is_bold=`, and `is_italic=`, and without `allow_regular_fallback=`.
`text_width(text, size)` replaces `text_length(text, fontsize=11)`, and
`has_glyph(char)` takes one character and returns whether the face has a
glyph for it. `python -m pylopdf.migrate` rewrites these calls, and pymupdf's
`get_text_length()`.
