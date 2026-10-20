Add immutable `Matrix` values and extend `Point` with constructors, vector
arithmetic, affine transformation, distances, and unit directions (roadmap F4).
`Point` addition and multiplication now perform geometry instead of tuple
concatenation/repetition, and the origin is false in truth testing. Coordinates
must be finite. Both types retain NamedTuple indexing, unpacking, equality,
hashing, and pickling. Mutating geometry methods are replaced by operators;
`Rect`, `IRect`, `Quad`, and rendering's `matrix=` option remain future work.
