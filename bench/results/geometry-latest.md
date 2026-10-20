# F4 geometry measurements

Python 3.14.8, Darwin arm64; pylopdf 0.14.0, pymupdf 2.0.

Reproduce: `uv run --group bench python bench/geometry.py`.
Per-operation median of seven samples, each with 20,000 operations.

| Operation | pylopdf (us) | pymupdf (us) |
| --- | ---: | ---: |
| `Point(3, 4)` | 0.383 | 0.172 |
| `Matrix(2, 3)` | 0.557 | 0.256 |
| `point * matrix` | 1.661 | 2.510 |
| `matrix * matrix` | 2.966 | 2.945 |
| `~matrix` | 2.083 | 4.781 |
| `Rect(1, 2, 3, 4)` | 0.780 | 0.769 |
| `Rect(1.0, 2.0, 3.0, 4.0)` | 0.318 | 0.740 |
| `rect * matrix` | 0.941 | 1.829 |
| `rect \| rect` | 3.341 | 3.586 |
| `rect & rect` | 3.088 | 4.395 |
| `point in rect` | 1.266 | 1.248 |
| `rect.quad * matrix` | 10.105 | 14.703 |

The former two-field NamedTuple constructor takes 0.143 us, versus 0.390 us with finite-coordinate validation and overloaded constructors (2.72x).
For four floats, as extraction and search return them, the former four-field Rect constructor takes 0.165 us, versus 0.319 us with validation (1.94x).
This is a constructor cost, not a claim about whole-document performance.

30,483 affine, rectangle, and quad results match pymupdf 2.0 on 2,000 seeded small-integer cases (relative tolerance 2e-5, absolute tolerance 2e-4). The oracle uses float32 on some paths; pylopdf retains Python float precision. Near-singular arbitrary floating-point matrices can therefore differ more. Independent regressions check inverse round trips and extreme scale ranges.
