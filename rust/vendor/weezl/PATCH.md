# Pyodide compatibility patch

This directory vendors `weezl` 0.2.1 under its upstream MIT OR Apache-2.0
license. `lopdf` 0.44 uses this release for LZW streams.

Pyodide 0.28.3 pins Rust 1.86, while upstream `weezl` 0.2.1 uses three mutable
slice cursor methods stabilized in a later Rust release. The local patch
replaces those calls with equivalent `split_first_mut` and `split_at_mut`
operations. No codec behavior or public API is changed.

Remove this patch once the oldest supported Pyodide runtime ships a Rust
toolchain that satisfies `weezl`'s upstream requirement.
