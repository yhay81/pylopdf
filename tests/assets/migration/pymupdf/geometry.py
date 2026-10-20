"""Combine rectangles and matrices with pymupdf's in-place methods."""

import fitz

area = fitz.Rect(10, 10, 50, 40)
area.include_rect(fitz.Rect(30, 30, 80, 90))
assert area == fitz.Rect(10, 10, 80, 90)

clip = fitz.Rect(0, 0, 60, 60)
clip.intersect(area)
assert clip == fitz.Rect(10, 10, 60, 60)

flipped = fitz.Rect(50, 50, 10, 10)
flipped.normalize()
assert flipped == fitz.Rect(10, 10, 50, 50)

shift = fitz.Matrix(1, 1)
shift.pretranslate(5, 7)
point = fitz.Point(1, 1)
point.transform(shift)
assert point == fitz.Point(6, 8)

scale = fitz.Matrix(2, 2)
moved = fitz.Rect(1, 1, 2, 2)
moved.transform(scale)
assert moved == fitz.Rect(2, 2, 4, 4)

inverse = fitz.Matrix(4, 4)
inverse.invert()
assert inverse == fitz.Matrix(0.25, 0.25)
print("geometry ok")
