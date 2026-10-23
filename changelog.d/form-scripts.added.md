`field.scripts` and `widget.scripts` read, set, and remove the JavaScript
actions of form fields and widgets by `ScriptTrigger`, keeping the form's
calculation order; pylopdf stores scripts and never runs them.
Replacing a calculation preserves its order, and splitting a merged field
keeps widget actions on their widget. Unicode scripts round-trip at the
UTF-8 input limit, including UTF-16 streams. Writes preserve the aggregate
read limit, and saved mappings reject closed documents and stale fields.
