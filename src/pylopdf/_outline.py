"""The outline (bookmarks): ``Document.outline``, its item handles, and entry records."""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, overload

from pylopdf._content import Color
from pylopdf._limits import _validate_cumulative_pdf_text_input, _validate_cumulative_utf8_input
from pylopdf._objects import Dictionary
from pylopdf._targets import Destination, Target, _target_from_core, _target_to_core
from pylopdf.pylopdf_core import LimitError, StaleObjectError

if TYPE_CHECKING:
    from pylopdf._document import Document
    from pylopdf._targets import _TargetTuple
    from pylopdf.pylopdf_core import _Document

#: The outline limits that every read and write shares: items, levels, and
#: the UTF-8 or encoded bytes of their titles and target texts.
_MAX_OUTLINE_ITEMS = 65_536
_MAX_OUTLINE_LEVELS = 64
_MAX_OUTLINE_TEXT_SIZE = 16 * 1024 * 1024

#: `/F` flags of an outline item (ISO 32000-2 Table 151).
_ITALIC = 1
_BOLD = 2

#: The core's form of one item: level, title, target, color, flags, count,
#: and whether it has children.
_ItemTuple = tuple[int, str, "_TargetTuple", "tuple[float, float, float] | None", int, int, bool]


def _color(value: object) -> Color | None:
    """Validate an outline color: an opaque :class:`Color` or ``None``."""
    if value is None:
        return None
    if not isinstance(value, Color):
        msg = f"color must be a Color or None: {value!r}"
        raise TypeError(msg)
    components = (value.red, value.green, value.blue)
    if value.alpha != 1 or not all(math.isfinite(c) and 0 <= c <= 1 for c in components):
        msg = f"an outline color has components from 0 to 1 and no transparency: {value!r}"
        raise ValueError(msg)
    return value


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{name} must be a bool: {value!r}"
        raise TypeError(msg)
    return value


@dataclass(frozen=True, slots=True)
class OutlineEntry:
    """One item of an outline to write, as ``doc.outline = [...]`` takes it.

    Levels start at 1 and grow by at most one from one entry to the next; an
    entry's children are the entries of the next level that follow it.
    ``is_open`` shows the children when the document opens.
    """

    level: int
    title: str
    destination: Target | None = None
    _: KW_ONLY
    color: Color | None = None
    bold: bool = False
    italic: bool = False
    is_open: bool = False

    def __post_init__(self) -> None:
        """Validate the fields."""
        if isinstance(self.level, bool) or not isinstance(self.level, int):
            msg = f"level must be an int: {self.level!r}"
            raise TypeError(msg)
        if not 1 <= self.level <= _MAX_OUTLINE_LEVELS:
            msg = f"level must be from 1 to {_MAX_OUTLINE_LEVELS}: {self.level}"
            raise ValueError(msg)
        if not isinstance(self.title, str):
            msg = f"title must be a str: {self.title!r}"
            raise TypeError(msg)
        _target_to_core(self.destination, page_count=_UNBOUNDED_PAGES)
        _color(self.color)
        _bool(self.bold, "bold")
        _bool(self.italic, "italic")
        _bool(self.is_open, "is_open")

    @property
    def page(self) -> int | None:
        """Return the zero-based page of a :class:`Destination`, else ``None``."""
        return self.destination.page if isinstance(self.destination, Destination) else None


#: A page count that admits every page index while a record is validated
#: on its own; writing checks the document's real page count.
_UNBOUNDED_PAGES = 1 << 62


class OutlineItem:
    """A handle on one item of the outline, as ``doc.outline`` yields it.

    Properties read the document on every access, and assigning ``title``,
    ``destination``, ``color``, ``bold``, ``italic``, or ``is_open`` edits the
    item at once. A handle raises :class:`StaleObjectError` once its item no
    longer exists, as after the outline is replaced, or the document's
    objects were renumbered.
    """

    __slots__ = ("_document", "_epoch", "_id")

    def __init__(self, document: Document, xref: int, generation: int) -> None:
        """Create a handle; :class:`Outline` does this."""
        self._document = document
        self._epoch = document._object_epoch
        self._id = (xref, generation)

    def _core(self) -> _Document:
        document = self._document
        document._ensure_open()
        if self._epoch != document._object_epoch:
            msg = f"outline item {self._id[0]} was invalidated when the document's objects were renumbered or reloaded"
            raise StaleObjectError(msg)
        return document._doc

    def _read(self) -> _ItemTuple:
        return self._core().outline_item(self._id)

    @property
    def xref(self) -> int:
        """Return the item's object number."""
        return self._id[0]

    @property
    def obj(self) -> Dictionary:
        """Return the item's dictionary in the low-level object model."""
        self._read()
        return Dictionary(self._document, self._id)

    @property
    def level(self) -> int:
        """Return the item's depth: 1 for a top-level item."""
        return self._read()[0]

    @property
    def title(self) -> str:
        """Return the title shown in the viewer's outline."""
        return self._read()[1]

    @title.setter
    def title(self, title: str) -> None:
        if not isinstance(title, str):
            msg = f"title must be a str: {title!r}"
            raise TypeError(msg)
        _validate_titles([title])
        self._core().set_outline_title(self._id, title)

    @property
    def destination(self) -> Target | None:
        """Return where the item leads, or ``None``.

        A destination in this document that does not name an existing page
        reads as ``None``, and a named destination reads as the
        :class:`Destination` that it names.
        """
        return _target_from_core(self._read()[2])

    @destination.setter
    def destination(self, target: Target | None) -> None:
        core = self._core()
        spec = _target_to_core(target, self._document._page_count())
        _validate_titles([text for text in (spec[4], spec[5]) if text])
        core.set_outline_target(self._id, spec)

    @property
    def page(self) -> int | None:
        """Return the zero-based page of a :class:`Destination`, else ``None``."""
        destination = self.destination
        return destination.page if isinstance(destination, Destination) else None

    @property
    def color(self) -> Color | None:
        """Return the title's color, or ``None`` for the viewer's default."""
        color = self._read()[3]
        return None if color is None else Color(*color)

    @color.setter
    def color(self, color: Color | None) -> None:
        self._set_style(_color(color), self._read()[4])

    @property
    def bold(self) -> bool:
        """Return whether the title shows in bold."""
        return bool(self._read()[4] & _BOLD)

    @bold.setter
    def bold(self, bold: bool) -> None:
        self._set_flag(_BOLD, _bool(bold, "bold"))

    @property
    def italic(self) -> bool:
        """Return whether the title shows in italic."""
        return bool(self._read()[4] & _ITALIC)

    @italic.setter
    def italic(self, italic: bool) -> None:
        self._set_flag(_ITALIC, _bool(italic, "italic"))

    @property
    def is_open(self) -> bool:
        """Return whether the item's children show; ``False`` without children.

        Assigning recounts the outline's ``/Count`` entries; an item without
        children has no open state and refuses it.
        """
        _, _, _, _, _, count, has_children = self._read()
        return has_children and count > 0

    @is_open.setter
    def is_open(self, is_open: bool) -> None:
        self._core().set_outline_open(self._id, _bool(is_open, "is_open"))

    @property
    def children(self) -> tuple[OutlineItem, ...]:
        """Return the item's children in order."""
        ids = self._core().outline_children(self._id)
        return tuple(OutlineItem(self._document, xref, generation) for xref, generation in ids)

    def _set_flag(self, flag: int, value: bool) -> None:  # noqa: FBT001 - one flag of the setters above.
        _, _, _, color, flags, _, _ = self._read()
        flags = (flags | flag) if value else (flags & ~flag)
        self._set_style(None if color is None else Color(*color), flags)

    def _set_style(self, color: Color | None, flags: int) -> None:
        rgb = None if color is None else (color.red, color.green, color.blue)
        self._core().set_outline_style(self._id, rgb, flags & 0xFFFFFFFF)

    def to_entry(self) -> OutlineEntry:
        """Return the item, without its children, as an :class:`OutlineEntry`."""
        level, title, target, color, flags, count, has_children = self._read()
        return OutlineEntry(
            level,
            title,
            _target_from_core(target),
            color=None if color is None else Color(*color),
            bold=bool(flags & _BOLD),
            italic=bool(flags & _ITALIC),
            is_open=has_children and count > 0,
        )

    def __eq__(self, other: object) -> bool:
        """Compare document, object epoch, and object."""
        if not isinstance(other, OutlineItem):
            return NotImplemented
        return (self._document, self._epoch, self._id) == (other._document, other._epoch, other._id)

    def __hash__(self) -> int:
        """Hash document, object epoch, and object."""
        return hash((id(self._document), self._epoch, self._id))

    def __repr__(self) -> str:
        """Return ``OutlineItem(xref=..., title=...)``, or a stale marker."""
        try:
            return f"OutlineItem(xref={self._id[0]}, title={self.title!r})"
        except (StaleObjectError, ValueError):
            return f"<stale OutlineItem xref={self._id[0]}>"


class Outline(Sequence[OutlineItem]):
    """The document's outline as its top-level items, as ``doc.outline`` returns it.

    Each item's ``children`` give the rest of the tree, and :meth:`walk`
    visits every item in document order. Assigning a list of
    :class:`OutlineEntry` records to ``doc.outline`` replaces the outline,
    and ``del doc.outline`` removes it. The view reads the document on every
    access.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.outline``."""
        self._document = document

    def _items(self) -> list[OutlineItem]:
        document = self._document
        document._ensure_open()
        return [OutlineItem(document, xref, generation) for xref, generation in document._doc.outline_children()]

    @overload
    def __getitem__(self, index: int) -> OutlineItem: ...
    @overload
    def __getitem__(self, index: slice) -> list[OutlineItem]: ...
    def __getitem__(self, index: int | slice) -> OutlineItem | list[OutlineItem]:
        """Return a top-level item, or a list of them for a slice."""
        return self._items()[index]

    def __len__(self) -> int:
        """Return the number of top-level items."""
        return len(self._items())

    def __iter__(self) -> Iterator[OutlineItem]:
        """Iterate over the top-level items."""
        return iter(self._items())

    def walk(self) -> Iterator[OutlineItem]:
        """Iterate over every item in document order, each before its children.

        Items stored as direct dictionaries, which no handle can name, are
        left out; :meth:`entries` lists them.
        """
        document = self._document
        document._ensure_open()
        items = [
            OutlineItem(document, xref, generation) for xref, generation, *_ in document._doc.outline_entries() if xref
        ]
        return iter(items)

    def entries(self) -> list[OutlineEntry]:
        """Return every item in document order as :class:`OutlineEntry` records.

        Assigning the list, edited or not, to ``doc.outline`` writes the same
        outline again. Reads share the outline limits: 65,536 items or
        131,072 links between them (``LimitError.code == "toc_entry_count"``),
        16 MiB of title and target text (``"toc_text_size"``), and 64 levels.
        """
        document = self._document
        document._ensure_open()
        return [
            OutlineEntry(
                level,
                title,
                _target_from_core(target),
                color=None if color is None else Color(*color),
                bold=bool(flags & _BOLD),
                italic=bool(flags & _ITALIC),
                is_open=count > 0,
            )
            for _, _, level, title, target, color, flags, count in document._doc.outline_entries()
        ]

    def __repr__(self) -> str:
        """Return ``Outline([...])`` with the top-level items."""
        return f"Outline({self._items()!r})"


def _validate_titles(texts: Iterable[str]) -> None:
    """Bound outline text before it reaches the core."""
    values = list(texts)
    _validate_cumulative_utf8_input(
        values,
        _MAX_OUTLINE_TEXT_SIZE,
        limit_code="toc_input_size",
        input_label="outline text input",
    )
    _validate_cumulative_pdf_text_input(
        values,
        _MAX_OUTLINE_TEXT_SIZE,
        limit_code="toc_input_size",
        input_label="encoded outline text input",
    )


def _write_outline(document: Document, entries: Iterable[OutlineEntry]) -> None:
    """Replace the outline with ``entries``, all validated before any change."""
    document._ensure_open()
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Iterable):
        msg = f"the outline is an iterable of OutlineEntry records: {type(entries).__name__}"
        raise TypeError(msg)
    items = list(itertools.islice(entries, _MAX_OUTLINE_ITEMS + 1))
    if len(items) > _MAX_OUTLINE_ITEMS:
        code = "toc_entry_count"
        msg = f"the outline cannot contain more than {_MAX_OUTLINE_ITEMS} items"
        raise LimitError(code, msg)
    page_count = document._page_count()
    specs = []
    texts = []
    previous_level = 0
    for index, entry in enumerate(items):
        if not isinstance(entry, OutlineEntry):
            msg = f"outline item {index} must be an OutlineEntry: {type(entry).__name__}"
            raise TypeError(msg)
        if entry.level > previous_level + 1:
            msg = f"outline item {index} has level {entry.level}; levels start at 1 and grow by at most one"
            raise ValueError(msg)
        target = _target_to_core(entry.destination, page_count)
        flags = (_BOLD if entry.bold else 0) | (_ITALIC if entry.italic else 0)
        color = None if entry.color is None else (entry.color.red, entry.color.green, entry.color.blue)
        specs.append((entry.level, entry.title, target, color, flags, entry.is_open))
        texts.append(entry.title)
        texts.extend(text for text in (target[4], target[5]) if text)
        previous_level = entry.level
    _validate_titles(texts)
    document._doc.write_outline(specs)


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (OutlineEntry, OutlineItem, Outline):
    _public.__module__ = "pylopdf"
