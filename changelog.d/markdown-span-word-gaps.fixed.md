`to_markdown` keeps the space between words that end and start adjacent
spans of one line, such as a word in another font or style, instead of
running them together (`Expandable\inputequivalent`). It inserts a space
where the gap between the spans exceeds the word gap that `get_text` uses,
keeps emphasis markers outside that space, and still joins CJK and other
scripts written without spaces directly.
