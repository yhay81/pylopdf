Annotation properties are assignable: `rect`, `contents`, `author`,
`subject`, `name`, `language` (new), `icon`, `color`, `interior_color`,
`opacity`, `flags`, `border`, `blend_mode`, `rotation`, `vertices`,
`strokes`, and `line_endings`, and `del` removes the optional ones. Each
assignment records the modification time and draws the appearance again,
replacing pymupdf's setter methods and `update()`; the codemod rewrites
`set_rect()`, `set_opacity()`, `set_flags()`, `set_blendmode()`,
`set_colors()`, `set_line_ends()`, `set_info()`, and `set_border()` in
pymupdf code.
