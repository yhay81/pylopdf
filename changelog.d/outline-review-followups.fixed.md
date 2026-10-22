Outline file targets preserve Unicode filenames, and destination coordinates
and zoom that exceed PDF's real-number range are refused before mutation.
Individual title and target edits enforce the complete outline's text limits;
invalid imported colors use the viewer's default. Item dictionary handles
retain the exact object generation. Open-state edits update direct and aliased
outline roots consistently, while null or missing children have no open state.

Unknown page-label styles in existing PDFs retain their prefix-only labels
when read as records. The migration tool preserves pylopdf's `Outline` class
when it appears alongside pymupdf imports.
