`page.widgets.add()` creates form fields from `TextField`, `CheckBox`,
`RadioButton`, `ComboBox`, `ListBox`, `PushButton`, and `SignatureField`
specifications, or adds a widget to an existing field of the same name;
`del page.widgets[i]` and `del doc.form[name]` remove widgets and fields.
Fields that pylopdf creates can be filled by pylopdf and by pymupdf.
Button creation keeps the field and all widget states synchronized. Removal
restores the document on failure, refuses cyclic parent chains, and keeps
calculation-order references consistent for nested fields.
