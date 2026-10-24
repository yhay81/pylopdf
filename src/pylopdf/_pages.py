"""The pages of a document: :class:`Pages`, the live sequence behind ``doc.pages``."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, overload

from pylopdf._hints import hinted_getattr
from pylopdf._limits import _FLOAT32_MAX
from pylopdf._page import Page
from pylopdf._page_labels import _label, _read_page_labels
from pylopdf.pylopdf_core import LimitError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pylopdf._document import Document

#: The size of a new page in points: A4, rounded to whole points.
_DEFAULT_PAGE_SIZE = (595.0, 842.0)
#: Explicit page entries may repeat pages up to the larger of the page count
#: and this floor, so repetition never costs more than one full document pass.
_MIN_PAGE_ENTRY_LIMIT = 4096


def _resolve_index(index: object, count: int) -> int:
    """Resolve a page index, negative ones counting from the end, as a sequence does."""
    if not isinstance(index, int) or isinstance(index, bool):
        msg = f"page indexes must be integers, not {type(index).__name__}"
        raise TypeError(msg)
    resolved = index + count if index < 0 else index
    if not 0 <= resolved < count:
        msg = f"page index {index} is out of range for {count} pages"
        raise IndexError(msg)
    return resolved


def _insert_position(index: object, count: int) -> int:
    """Clamp an insertion index as ``list.insert`` does."""
    if not isinstance(index, int) or isinstance(index, bool):
        msg = f"the insertion index must be an integer, not {type(index).__name__}"
        raise TypeError(msg)
    position = int(index)
    if position < 0:
        return max(position + count, 0)
    return min(position, count)


def _entry_limit_error(limit: int, *, name: str) -> LimitError:
    msg = f"{name} cannot contain more than {limit} entries (the larger of the page count and {_MIN_PAGE_ENTRY_LIMIT})"
    return LimitError("page_entry_count", msg)


def _page_size(size: object) -> tuple[float, float]:
    """Validate a ``(width, height)`` page size in points."""
    try:
        width, height = (float(value) for value in size)  # ty: ignore[not-iterable]
    except (TypeError, ValueError) as exc:
        msg = f"size must be (width, height) in points: {size!r}"
        raise ValueError(msg) from exc
    for value in (width, height):
        if not (math.isfinite(value) and 0 < value <= _FLOAT32_MAX):
            msg = f"size must be two positive finite numbers within PDF's real-number range: {size!r}"
            raise ValueError(msg)
    return width, height


class Pages(Sequence[Page]):
    """The pages of a :class:`Document`, a live sequence of :class:`Page` views.

    ``doc.pages[i]`` takes negative indexes and slices, ``len(doc.pages)``
    counts the pages, and iteration visits them in order. ``del``,
    :meth:`new`, :meth:`insert`, :meth:`extend`, :meth:`select`, and
    :meth:`move` change the page order in one atomic edit, after which earlier
    :class:`Page` views raise :class:`StalePageError`.
    """

    __slots__ = ("_document",)

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, document: Document) -> None:
        """Wrap a document; ``doc.pages`` is the way to get one."""
        self._document = document

    def __len__(self) -> int:
        """Return the number of pages."""
        document = self._document
        document._ensure_open()
        return document._doc.page_count()

    @overload
    def __getitem__(self, index: int) -> Page: ...
    @overload
    def __getitem__(self, index: slice) -> list[Page]: ...
    def __getitem__(self, index: int | slice) -> Page | list[Page]:
        """Return a page, or a list of the pages that a slice selects."""
        count = len(self)
        if isinstance(index, slice):
            return [Page(self._document, position) for position in range(count)[index]]
        return Page(self._document, _resolve_index(index, count))

    def __iter__(self) -> Iterator[Page]:
        """Visit every page in order; changing the page order meanwhile raises ``RuntimeError``."""
        document = self._document
        count = len(self)
        generation = document._generation
        for position in range(count):
            if document._generation != generation:
                msg = "the pages changed during iteration"
                raise RuntimeError(msg)
            yield Page(document, position)

    def __contains__(self, page: object) -> bool:
        """Return whether ``page`` is a current view of one of these pages."""
        return (
            isinstance(page, Page)
            and page._document is self._document
            and page._generation == self._document._generation
            and 0 <= page._pno < len(self)
        )

    def __delitem__(self, index: int | slice) -> None:
        """Delete a page, or the pages that a slice selects.

        Links, outline entries, named destinations, and the open action that
        target a deleted page are removed; the form loses the widgets of
        deleted pages and the fields whose widgets all lay there; a tagged
        PDF's structure tree loses their marked content. Deleting no pages
        changes nothing.
        """
        document = self._document
        count = len(self)
        numbers = sorted(range(count)[index]) if isinstance(index, slice) else [_resolve_index(index, count)]
        if not numbers:
            return
        document._doc.delete_pages([number + 1 for number in numbers])
        document._bump_generation()

    def new(self, size: Sequence[float] = _DEFAULT_PAGE_SIZE, *, index: int | None = None) -> Page:
        """Add a blank page of ``size`` points and return it.

        The page is appended unless ``index`` is given, which is clamped as
        ``list.insert`` clamps it. ``size`` takes a :class:`PaperSize`
        member such as ``PaperSize.LETTER``. The default size is A4, rounded
        to 595 × 842 points.
        """
        document = self._document
        width, height = _page_size(size)
        count = len(self)
        position = count if index is None else _insert_position(index, count)
        document._doc.new_page(None if position == count else position, width, height)
        document._bump_generation()
        return Page(document, position)

    def insert(self, index: int, pages: Page | Iterable[Page]) -> None:
        """Insert copies of ``pages`` before ``index``, clamped as ``list.insert`` clamps it.

        ``pages`` is one :class:`Page` or an iterable of pages of one open
        document, this one or another. Pages of another document are imported
        with their links, annotations, and form fields; a field name that this
        document already uses gains the smallest unused ``+N`` suffix. Pages
        of this document are copied as new page objects that share their
        content streams and resources with the originals. A page of another
        document can appear once per call. The iterable may hold up to the
        larger of the source's page count and 4,096 pages.
        """
        document = self._document
        count = len(self)
        position = _insert_position(index, count)
        source, numbers = self._source_pages(pages)
        if source is None:
            return
        target = None if position == count else position
        if source is document:
            document._doc.copy_pages(numbers, target)
        else:
            if len(set(numbers)) != len(numbers):
                msg = "a page of another document can be inserted once per call"
                raise ValueError(msg)
            document._doc.merge_pages(source._doc, numbers, target)
        document._bump_generation()

    def extend(self, pages: Page | Iterable[Page]) -> None:
        """Append copies of ``pages``, as :meth:`insert` at the end inserts them."""
        self.insert(len(self), pages)

    def select(self, indexes: Iterable[int], /) -> None:
        """Keep the listed pages, in the listed order.

        A repeated index adds a copy that shares the page's content streams
        and resources. Pages left out are deleted as ``del`` deletes them. The
        iterable may hold up to the larger of the page count and 4,096
        indexes.
        """
        document = self._document
        count = len(self)
        limit = max(count, _MIN_PAGE_ENTRY_LIMIT)
        numbers: list[int] = []
        for index in indexes:
            if len(numbers) >= limit:
                raise _entry_limit_error(limit, name="indexes")
            numbers.append(_resolve_index(index, count) + 1)
        document._doc.select(numbers)
        document._bump_generation()

    def move(self, index: int, to: int) -> None:
        """Move the page at ``index`` so that it ends at index ``to``.

        Both indexes may be negative. The page keeps its page object, so
        links and outline entries that target it follow it.
        """
        document = self._document
        count = len(self)
        source = _resolve_index(index, count)
        destination = _resolve_index(to, count)
        if source == destination:
            return
        order = list(range(count))
        order.insert(destination, order.pop(source))
        document._doc.select([number + 1 for number in order])
        document._bump_generation()

    def labeled(self, label: str) -> list[int]:
        """Return the indexes of the pages whose page label is ``label``.

        An empty label, or a document without page labels, matches no page.
        """
        if not isinstance(label, str):
            msg = f"label must be a string: {label!r}"
            raise TypeError(msg)
        document = self._document
        count = len(self)
        if not label:
            return []
        ranges = _read_page_labels(document)
        if not ranges:
            return []
        return [index for index in range(count) if _label(ranges, index) == label]

    def _source_pages(self, pages: Page | Iterable[Page]) -> tuple[Document | None, list[int]]:
        """Return the document that ``pages`` belong to and their one-based page numbers."""
        if isinstance(pages, Page):
            items: Iterable[object] = (pages,)
        elif isinstance(pages, Iterable) and not isinstance(pages, (str, bytes)):
            items = pages
        else:
            msg = f"pages must be a Page or an iterable of pages: {pages!r}"
            raise TypeError(msg)
        source: Document | None = None
        limit = 0
        numbers: list[int] = []
        for page in items:
            if not isinstance(page, Page):
                msg = f"pages must be Page views: {page!r}"
                raise TypeError(msg)
            if source is None:
                source = page._document
                limit = max(len(source.pages), _MIN_PAGE_ENTRY_LIMIT)
            elif page._document is not source:
                msg = "the pages of one call must come from one document"
                raise ValueError(msg)
            if len(numbers) >= limit:
                raise _entry_limit_error(limit, name="pages")
            numbers.append(page._page_number())
        return source, numbers

    def __repr__(self) -> str:
        """Return a representation naming the document."""
        return f"<Pages of {self._document!r}>"


# The package re-exports this class; reprs, pickles, and documentation name it.
Pages.__module__ = "pylopdf"
