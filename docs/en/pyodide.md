---
title: Pyodide and Cloudflare Workers
description: Install and use pylopdf in Pyodide-based runtimes, including Cloudflare Python Workers.
---

# Pyodide and Cloudflare Workers

Starting with the first release that contains this support, pylopdf publishes a
PyEmscripten wheel for the Python 3.13 ABI used by Pyodide 0.28.3. This is the
runtime line currently used by Cloudflare Python Workers. The release wheel is
built and smoke-tested in Pyodide before it is published.

## Install

Declare pylopdf like any other Python dependency:

```toml
[project]
dependencies = [
  "pylopdf>=0.11",
]
```

Cloudflare's build resolves the compatible PyEmscripten wheel from PyPI. A
source distribution or native Linux wheel cannot be used in a Python Worker.

## Bytes-first usage

Use bytes from R2, a request, or another Worker binding. This avoids depending
on a persistent filesystem:

```python
import pylopdf


def extract_pdf(pdf_bytes: bytes) -> str:
    with pylopdf.open(stream=pdf_bytes, max_decompressed_size=10 * 1024 * 1024) as document:
        if document.page_count > 200:
            raise ValueError("PDF exceeds the 200-page limit")
        return "\n".join(page.get_text() for page in document)
```

Set limits before processing untrusted files. `max_decompressed_size` bounds
each decoded stream during open; applications should also bound input bytes,
page count, extracted text, and queue retries.

## Runtime contract

| Component | Supported release target |
|---|---|
| Python | CPython 3.13 |
| Pyodide | 0.28.3 |
| Emscripten | 4.0.9 |
| Rust | nightly-2025-02-01 with the matching Wasm EH sysroot |
| Input | bytes/stream recommended |

The wheel preserves the native Python API for editing, extraction, and
rendering. It uses serial parsing and rendering because Cloudflare's runtime
does not expose pthreads; `render_pages()` automatically defaults to one worker
and rejects `workers>1`.

Subset-embedded OpenType generation is the one excluded capability. Calls that
pass `fontfile` or `fontbuffer` to text/form generation raise `PdfError` with a
clear compatibility message. Standard 14 font editing, opening existing PDFs,
text/image extraction, forms without a custom embedded font, saving, and
rendering remain available. Generate custom-font PDFs before upload when that
capability is required.

The virtual Emscripten filesystem is temporary; persist source and derived files
in application storage such as R2.

Only the versions exercised by release CI are supported. A newer Pyodide ABI
requires a new wheel and a passing compatibility test before it is added to
this table.

## Reproduce the wheel

On Linux or macOS with Rust and Python installed:

```bash
tools/install_pyodide_rust.sh
RUSTUP_TOOLCHAIN=nightly-2025-02-01 \
  RUSTC_WRAPPER=tools/pyodide_rustc_wrapper.sh \
  MATURIN_PEP517_ARGS="--no-default-features --ignore-rust-version -Zbuild-std=std,panic_unwind" \
  RUSTFLAGS="-C symbol-mangling-version=v0 -Zemscripten-wasm-eh" \
  CIBW_BUILD=cp313-pyodide_wasm32 \
  python -m cibuildwheel --platform pyodide --output-dir wheelhouse
```

The build uses `cibuildwheel`'s pinned Pyodide version from `pyproject.toml`.
The install script verifies the downloaded Rust sysroot by SHA-256. Cargo
rebuilds the standard library with WebAssembly-safe symbol mangling; the wrapper
limits compatibility feature gates to non-sysroot crates.

See Cloudflare's
[Python package support](https://developers.cloudflare.com/workers/languages/python/packages/)
and Pyodide's
[package build guide](https://pyodide.org/en/0.28.0/development/building-packages.html)
for the runtime constraints.
