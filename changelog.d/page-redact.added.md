`Page.redact()` applies the page's `Redaction` annotations, or areas given
directly, and returns a `RedactionReport`. It removes the glyphs whose boxes
meet an area in every font and writing mode, invisible OCR text included,
with `TJ` adjustments that keep the following text in place; blanks the
covered pixels of images in a copy of each, or removes their placements;
removes the paths and shadings that an area touches or covers; copies a Form
XObject placement that loses something; removes overlapping annotations and
widgets; draws the fills and overlay text; and verifies the page with hayro.
It replaces pymupdf's `Page.apply_redactions` and the `PDF_REDACT_*`
constants.
