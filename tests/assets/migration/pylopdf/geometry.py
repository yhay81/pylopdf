"""Combine rectangles and matrices with pymupdf's in-place methods."""

import pylopdf

area = pylopdf.Rect(10, 10, 50, 40)
area = area | pylopdf.Rect(30, 30, 80, 90)
assert area == pylopdf.Rect(10, 10, 80, 90)

clip = pylopdf.Rect(0, 0, 60, 60)
clip = clip & area
assert clip == pylopdf.Rect(10, 10, 60, 60)

flipped = pylopdf.Rect(50, 50, 10, 10)
flipped = flipped.normalized()
assert flipped == pylopdf.Rect(10, 10, 50, 50)

shift = pylopdf.Matrix(1, 1)
shift = pylopdf.Matrix(1, 0, 0, 1, 5, 7) * shift
point = pylopdf.Point(1, 1)
point = point * shift
assert point == pylopdf.Point(6, 8)

scale = pylopdf.Matrix(2, 2)
moved = pylopdf.Rect(1, 1, 2, 2)
moved = moved * scale
assert moved == pylopdf.Rect(2, 2, 4, 4)

inverse = pylopdf.Matrix(4, 4)
inverse = ~inverse
assert inverse == pylopdf.Matrix(0.25, 0.25)
print("geometry ok")
