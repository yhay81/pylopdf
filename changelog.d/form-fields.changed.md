Replace `Document.get_form_fields()` and `Document.set_form_field()` with
`doc.form`, a mapping from full field names to `Field` handles with `name`,
`type`, a `FieldType` member, `value`, `flags`, a `FieldFlags` value
inherited from parent fields, `choices`, the `FieldChoice(value=, label=)`
options of a choice field, `xref`, and `obj`. Assigning
`doc.form[name].value` fills the field and regenerates its appearances, and
`doc.form.font` sets the OpenType `Font` that fills draw with, which
replaces the `font`, `fontfile`, `fontbuffer`, `fontindex`, `font_language`,
and `max_font_size` arguments; `Font.installed(language=...)` selects an
installed extra's face. `FormFieldInfo` and `FormFieldType` are gone, and a
pushbutton's type is `FieldType.PUSHBUTTON`, where it was `"button"`. The
codemod rewrites `get_form_fields()` and `set_form_field(name, value)` and
marks calls with font arguments.
