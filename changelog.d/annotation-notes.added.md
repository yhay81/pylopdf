`page.annotations.add(Note(at, contents=..., icon=NoteIcon.NOTE))` adds a
note drawn as one of seven `NoteIcon` pictures. Annotations gain
`popup_rect`, assignable to add or move a popup and deletable to remove it,
`is_open`, `in_reply_to`, assignable to make an annotation answer another of
its page, and `replies`. The codemod rewrites `add_text_annot()`.
