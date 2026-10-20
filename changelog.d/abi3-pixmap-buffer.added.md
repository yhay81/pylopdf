The abi3 and Pyodide wheels expose `Pixmap` pixels through the buffer
protocol, which only the free-threaded wheels did before: `memoryview(pixmap)`
and `np.frombuffer(pixmap, dtype=np.uint8)` read the immutable RGBA8 storage
without a copy, where `Pixmap.samples` copies about 33 MiB for an A4 page at
300 dpi. The view is read-only and keeps its Pixmap alive, and the type stubs
declare `Pixmap.__buffer__` so that type checkers accept it.
