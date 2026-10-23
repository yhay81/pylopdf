`page.annotations.add(Redaction(rects, fill=..., overlay_text=..., ...))`
marks areas for removal with an appearance that outlines and crosses them
out; applying redactions comes with `page.redact`. `Redaction` fills with
black unless `fill=` says otherwise. The codemod rewrites
`add_redact_annot()`.
