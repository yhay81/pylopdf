`Page.recolor(colorspace)` and `Document.recolor(colorspace)` convert every
colour that pages draw to gray, RGB, or CMYK: colour operators in content,
Form XObjects, tiling patterns, Type 3 glyphs, and annotation appearances,
images and inline images, shadings with exponential or stitching functions,
and Indexed palettes. A page's recoloring copies what other pages share.
`Colorspace` gains `CMYK` as a recoloring target.
