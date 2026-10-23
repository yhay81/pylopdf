Geometry constants are class attributes: `Matrix.IDENTITY` replaces
`Identity` and `IdentityMatrix()`, `Rect.EMPTY` and `Rect.INFINITE` replace
`EMPTY_RECT()` and `INFINITE_RECT()`, `IRect` and `Quad` have the same pair
in place of the `EMPTY_IRECT()`, `INFINITE_IRECT()`, `EMPTY_QUAD()`, and
`INFINITE_QUAD()` functions, `Matrix.planish(p1, p2)` replaces
`planish_line(p1, p2)`, and the corners of `Rect.INFINITE` replace
`FZ_MIN_INF_RECT` and `FZ_MAX_INF_RECT`. The codemod rewrites each of them,
in pymupdf and pylopdf 0.15 code alike.
