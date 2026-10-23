Metadata, outline titles, annotation contents, form values, and page labels
keep the tabs and line breaks of PDFDocEncoding text strings, which lopdf 0.45
dropped, a UTF-8 text string drops its byte order mark, and a UTF-16BE string
ignores an odd trailing byte instead of reading it as a character.
