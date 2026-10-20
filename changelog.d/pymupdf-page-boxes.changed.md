Adopt pymupdf's page-box convention (roadmap M0): `Page.cropbox` and
`Page.set_cropbox` use the stored x coordinates and flip y against the top
of the MediaBox, `set_cropbox` raises `ValueError` for a box without area or
outside the MediaBox, and `Page.mediabox` normalizes its corners.
`Page.set_mediabox` now removes the CropBox, BleedBox, TrimBox, and ArtBox,
and replaces a CropBox that the page would inherit, so the whole new
MediaBox becomes visible.
