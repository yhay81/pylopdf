Python 3.10, which reaches end of life in October 2026, is no longer
supported. pylopdf now requires Python 3.11 or later, as pymupdf 2.0 does, and
its GIL-enabled wheels target the `abi3-py311` stable ABI (`cp311-abi3`, and
`cp311-abi3-pyemscripten_2025_0_wasm32` for Pyodide). pip on Python 3.10 keeps
installing pylopdf 0.14.0. The locale font and OCR model packages still
support Python 3.10.
