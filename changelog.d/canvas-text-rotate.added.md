`Canvas.text` and `Canvas.textbox` take `rotate`, which turns text clockwise
by 0, 90, 180, or 270 degrees in display space: `text` about its starting
point, and `textbox` inside its rectangle, whose height the lines then wrap
at for 90 and 270. The migration codemod rewrites pymupdf's anticlockwise
`insert_text` and `insert_textbox` `rotate=` to the canvas's angle.
