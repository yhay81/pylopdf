`Page.clean_contents()` and `Annotation.clean_contents()` write content as
normalized, balanced operations, the page's streams joined into one, and
remove the resources that nothing uses, and `Page.clip_to_rect(rect)`
removes what lies outside a rectangle through redaction and clips the
content to it.
