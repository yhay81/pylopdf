Fallback fonts configured on a document opened with a password now keep
rendering after `unlock()` with the other password, which reads the document
again; they were lost before.
