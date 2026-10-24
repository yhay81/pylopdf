`Span` records carry `is_superscript`, and with `page.layout(styles=True)`
`is_underlined` and `is_struck` for text that a horizontal vector rule
underlines or strikes out, as pymupdf's `TEXT_COLLECT_STYLES` finds them;
`Layout.to_dict()` reports them as pymupdf's flag bits.
