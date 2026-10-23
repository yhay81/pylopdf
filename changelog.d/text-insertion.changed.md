`Page.insert_text()` and `Page.insert_textbox()` go, with `TextAlign` and the
`TEXT_ALIGN_*` constants: inside `with page.draw() as canvas:`,
`canvas.text(text, at)` and `canvas.textbox(rect, text)` draw the same text,
with `size=`, `fill=Color(...)`, `line_height=`, and a `Font` passed as
`font=` in place of `fontsize=`, `color=`, `lineheight=`, and the font
arguments, and with `align="center"` and the other alignment strings.
`canvas.text` draws every line, where `insert_text` left out lines below the
page, and `canvas.textbox` places lines and decides whether they fit as
`Font.text_height` measures. `python -m pylopdf.migrate` rewrites both calls,
and assignments of a textbox's spare height, into canvas blocks, and joins
consecutive blocks on one page into one.
