`canvas.image` shows a JPEG or PNG as its EXIF orientation asks, mirrored or
turned by its placement while the data stays as it is, as image viewers and
browsers do; `rotate` then turns the upright image. pymupdf's `insert_image`
ignores the orientation, so a phone photo that it drew sideways now stands up.
