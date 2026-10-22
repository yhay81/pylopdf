Complete pymupdf's geometry types: `Rect` gains pymupdf's
constructors, corners, `contains`, `intersects`, `round`, `torect`, `morph`,
`get_area`, `normalized`, and the `+`, `-`, `*`, `/`, `|`, `&`, `in`,
`abs()`, and truth-testing operators, and its `width` and `height` no longer
go negative. New immutable `IRect` and `Quad` values, `IdentityMatrix` and
`Identity`, `planish_line`, the `EMPTY_*` and `INFINITE_*` constructors, and
`FZ_MIN_INF_RECT` and `FZ_MAX_INF_RECT` follow pymupdf as well. `Rect`
coordinates must now be finite, and tuple concatenation, repetition, and
membership on a `Rect` give way to the geometric meanings.
