---
title: Concurrency and free-threaded Python
description: How to use pylopdf from several threads, render pages in parallel, and run on free-threaded CPython 3.14t and 3.15t.
---

# Concurrency and free-threaded Python

pylopdf releases the GIL during heavy work such as opening, saving,
rendering, extraction, merging, and compression, so threads can run in
parallel on regular CPython. Free-threaded CPython 3.14t and 3.15t have their
own wheels, which keep the GIL disabled.

## What is supported { #support-contract }

| Use | Supported |
|---|---|
| Different threads working on different `Document` objects | Yes |
| Several threads calling or editing the same `Document` at once | No: use a lock, or open the file once per thread |
| `Document.render(workers=...)` on one document | Yes; this is the supported way to render one document in parallel |
| Several threads reading the same `Pixmap` | Yes; a `Pixmap` cannot be changed |
| Free-threaded CPython 3.14t and 3.15t | Yes, tested with the GIL disabled |

A `Page` belongs to its `Document` and follows the same rule. pylopdf may
reject simultaneous calls on one document, but do not count on that as a
lock.

## Process many files { #many-files }

Give each worker its own document:

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.markdown()


paths = ["a.pdf", "b.pdf", "c.pdf"]
with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

## Render many pages of one document { #render-pages }

Use `doc.render()` instead of calling one document from several threads:

```python
with pylopdf.open("report.pdf") as document:
    png_pages = document.render(dpi=144, workers=4)
```

It returns PNG bytes in the order of the requested pages and keeps its
estimated working memory under about 512 MB by running fewer workers when
pages are large.

## Repeated rendering { #repeated-rendering }

Rendering pages of one document again and again, on the thread that opened it,
reuses the fonts and glyph outlines parsed by earlier calls. The output is the
same as from a freshly opened document. The cache covers up to 64 pages and is
freed when you edit or close the document.

## Pixmaps and NumPy { #pixmap-buffers }

A `Pixmap` exposes its pixels as a read-only buffer, so NumPy can use them
without copying:

```python
import numpy as np

pix = page.pixmap()
pixels = np.frombuffer(pix, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
```

`pix.samples` returns an independent `bytes` copy instead.
