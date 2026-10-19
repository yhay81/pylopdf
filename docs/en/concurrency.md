---
title: Concurrency and free-threaded Python
description: The supported threading boundaries for Documents, Pages, Pixmaps, parallel rendering and CPython 3.14t and 3.15t.
---

# Concurrency and free-threaded Python

pylopdf publishes one `abi3` wheel per platform for GIL-enabled CPython
3.10–3.15 and version-specific wheels for free-threaded CPython:
`cp314-cp314t` since v0.10 and `cp315-cp315t` in releases after v0.14.0.
Importing a free-threaded wheel does not re-enable the GIL.

## Support contract { #support-contract }

| Use | Support |
|---|---|
| GIL-enabled CPython 3.10–3.15 | Supported by the `abi3-py310` wheel |
| Free-threaded CPython 3.14t and 3.15t | Supported by the `cp314-cp314t` and `cp315-cp315t` wheels and tested with the GIL disabled |
| Concurrent operations on distinct `Document` objects | Supported; heavy load, save, render, extraction, merge and compression work releases the GIL |
| Concurrent calls or edits on the same `Document` | Not supported; serialize access with a lock or use independent documents |
| `Document.render_pages(workers=...)` on one document | Supported; it is the bounded same-document parallel rendering boundary |
| Concurrent reads from a `Pixmap` | Supported; `Pixmap` is immutable |

A `Page` is a view into its parent `Document`, so it follows the same-document
rule. Simultaneous external access may be rejected by PyO3's runtime borrow
checks; it must not be used as a synchronization mechanism.

## Choose the right boundary { #choose-the-right-boundary }

For independent files, give each worker its own document:

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.to_markdown()


with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

For many pages of one document, use `render_pages()` instead of calling the
same document from external threads:

```python
png_pages = document.render_pages(scale=2, workers=4)
```

`render_pages()` uses one immutable renderer snapshot, preserves requested page
order and duplicates, and caps estimated concurrent working memory at roughly
512 MB.

## Repeated rendering { #repeated-rendering }

Repeated `render_page()`, `Page.get_pixmap()`, `Page.render()`, and serial
`render_pages()` calls on the thread that opened a document reuse fonts, color
spaces, and glyph outlines parsed by earlier calls, with output identical to a
freshly opened document. Each thread retains at most one such cache, covering
at most 64 distinct pages between calls. Editing the document, changing its
fallback fonts, or closing it invalidates the cache; on the opening thread this
frees it immediately, as does dropping the document. A cache invalidated from
another thread is freed when its thread renders again or exits. Calls from
other threads and parallel `render_pages()` workers use call-scoped caches.

## Pixmap buffers { #pixmap-buffers }

The free-threaded wheels expose the immutable RGBA8 storage through a
read-only, one-dimensional, zero-copy buffer:

```python
view = memoryview(page.get_pixmap())
assert view.readonly and view.format == "B"
```

The `abi3-py310` wheel cannot expose `Py_buffer` because that structure entered
the stable ABI in Python 3.11. Use `pixmap.samples`, a one-copy `bytes` value,
on that wheel. This keeps Python 3.10 compatibility without weakening buffer
lifetime or mutability guarantees.
