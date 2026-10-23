`page.widgets` lists the form widgets of a page and `field.widgets` those of
a field, as `Widget` handles with `field`, `rect`, `flags`, `border`,
`border_color`, `background_color`, `caption`, `text_color`, `font`,
`font_size`, `button_states`, and `on_state`; assigning a property draws the
widget again for its field's value. The codemod rewrites `page.widgets()`.
