A copy of a page within a document (`doc.pages.insert()` or `extend()` with
its own pages) gets annotations of its own instead of sharing the original's
annotation objects: popups and replies follow, widgets join their fields, and
in a tagged document the page's structure elements are copied with
parent-tree keys of their own.
