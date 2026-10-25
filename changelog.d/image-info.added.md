`pylopdf.image_info(source)` reads a JPEG, PNG, or JPEG 2000 file's header
without decoding it and returns an `ImageInfo` record with the format, size,
color components, alpha, bits per component, resolution, and EXIF
orientation; it replaces pymupdf's `image_profile`. The ecosystem guide gains
tested recipes that turn images, CBZ archives, and other formats through
Pillow into PDF pages of the images' own size, which replace pymupdf's
`convert_to_pdf()` and `insert_file()` of images, and `pdfocr_save()` with
`apply_ocr()`.
