`Attachment` and `Annotation.file` share assignable `filename`,
`description`, `created`, and `modified` fields; `del` removes the last
three, and `write()` sets `modified` to the current time. `creation_date` and
`mod_date` are now `created` and `modified`, and an absent description or
date reads as `None` rather than `""`.
