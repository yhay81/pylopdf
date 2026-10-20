# F4 Point and Matrix measurements

Python 3.14.8, Windows AMD64; pylopdf 0.14.0, pymupdf 2.0.

Reproduce: `uv run --group bench python bench/geometry.py`.
Per-operation median of seven samples, each with 20,000 operations.

| Operation | pylopdf (us) | pymupdf (us) |
| --- | ---: | ---: |
| `Point(3, 4)` | 0.927 | 0.446 |
| `Matrix(2, 3)` | 1.300 | 0.644 |
| `point * matrix` | 4.126 | 7.491 |
| `matrix * matrix` | 7.776 | 9.531 |
| `~matrix` | 5.259 | 18.506 |

The former two-field NamedTuple constructor takes 0.312 us, versus 0.957 us with finite-coordinate validation and overloaded constructors (3.07x).
This is a constructor cost, not a claim about whole-document performance.

3,954 affine results match pymupdf 2.0 on 1,000 seeded small-integer matrices and points (relative tolerance 2e-5, absolute tolerance 2e-4). The oracle uses float32 on some paths; pylopdf retains Python float precision. Near-singular arbitrary floating-point matrices can therefore differ more. Independent regressions check inverse round trips and extreme scale ranges.
