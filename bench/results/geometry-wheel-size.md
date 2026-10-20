# F4 Windows wheel size

Measured on Windows AMD64 with Python 3.14.8, Visual Studio 2026, and the
repository's thin-LTO release profile. Both are cp311-abi3 wheels.

| Artifact | Baseline aefd421 (bytes) | F4 (bytes) | Delta |
| --- | ---: | ---: | ---: |
| Wheel | 8602010 | 8605786 | +3776 |
| Uncompressed extension | 20568576 | 20568576 | +0 |
| Compressed extension | 8477692 | 8477689 | -3 |

The baseline was built from an archived copy of origin/main at aefd421;
the new wheel was built from the working tree with `uv build --wheel`.
The native sources are unchanged. The extension's compressed bytes vary
slightly with the build location; most of the wheel delta is the new Python
geometry module. No dependency was added. The packaged geometry source was
compared byte-for-byte with the tested source.
