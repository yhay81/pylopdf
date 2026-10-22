`markdown()` keeps the space between words that end and start adjacent
spans of one line, such as a word in another font or style, instead of
running them together (`Expandable\inputequivalent`). It inserts a space
where the gap between the spans exceeds the word gap that `text()` uses,
keeps emphasis markers outside that space, and keeps combining marks attached
to their base characters. Inline CJK gaps follow `text()`, while wrapped CJK
lines still join directly. Rotated spans account for the font-height padding
in their bounding boxes when measuring word gaps.
