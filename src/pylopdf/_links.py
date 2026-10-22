"""Links: ``Page.links`` and its ``Link`` handles."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, overload

from pylopdf._errors import StalePageError
from pylopdf._geometry import Rect
from pylopdf._hints import hinted_getattr
from pylopdf._limits import _validate_cumulative_utf8_input, _validate_rect
from pylopdf._targets import Target, _target_from_core, _target_to_core
from pylopdf.pylopdf_core import StaleObjectError

if TYPE_CHECKING:
    from pylopdf._objects import Dictionary
    from pylopdf._page import Page
    from pylopdf._targets import _TargetTuple

#: The texts that one link edit may write: its subtype and target texts.
_MAX_LINK_INPUT_TEXT_SIZE = 1024 * 1024


def _link_target(target: object, page: Page) -> _TargetTuple:
    """Return the core's tuple for a link target, with its texts bounded."""
    if target is None:
        msg = "a link needs a target: a Destination, RemoteDestination, Launch, NamedAction, or URI string"
        raise TypeError(msg)
    spec = _target_to_core(target, page._document._page_count())
    _validate_cumulative_utf8_input(
        ("Link", spec[4] or "", spec[5] or ""),
        _MAX_LINK_INPUT_TEXT_SIZE,
        limit_code="annotation_input_size",
        input_label="link subtype and target input",
    )
    return spec


class Link:
    """A handle on one link annotation of a page, as ``page.links`` lists it.

    ``rect`` is the area that follows the link, in the page's display
    coordinates, and ``target`` where it leads; assigning either edits the
    annotation. ``target`` is ``None`` for an action that pylopdf does not
    read, such as JavaScript. A handle reads the document on every access and
    finds its annotation again after an edit moved it; it raises
    :class:`StaleObjectError` once the annotation left the page and
    :class:`StalePageError` once the page did.
    """

    __slots__ = ("_index", "_page", "_xref")

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, page: Page, xref: int, index: int) -> None:
        """Create a handle; ``page.links`` does this."""
        self._page = page
        self._xref = xref
        self._index = index

    def _read(self) -> tuple[int, int, tuple[float, float, float, float], _TargetTuple]:
        page = self._page
        entry = page._document._doc.read_link(page._page_number(), self._index, self._xref)
        self._index = entry[0]
        return entry

    @property
    def page(self) -> Page:
        """Return the page that holds the link."""
        return self._page

    @property
    def xref(self) -> int:
        """Return the annotation's object number, or 0 for a dictionary stored in ``/Annots`` itself."""
        self._read()
        return self._xref

    @property
    def obj(self) -> Dictionary:
        """Return the annotation dictionary as a live low-level handle."""
        self._read()
        if self._xref:
            return self._page._document.objects[self._xref]
        return self._page.obj["Annots"][self._index]

    @property
    def rect(self) -> Rect:
        """Return the area that follows the link, in display coordinates."""
        return Rect(*self._read()[2])

    @rect.setter
    def rect(self, rect: Sequence[float]) -> None:
        validated = _validate_rect(rect)
        page = self._page
        self._read()
        page._document._doc.set_link(page._page_number(), self._index, self._xref, rect=validated)

    @property
    def target(self) -> Target | None:
        """Return where the link leads, or ``None`` for an action that pylopdf does not read."""
        return _target_from_core(self._read()[3])

    @target.setter
    def target(self, target: Target) -> None:
        page = self._page
        spec = _link_target(target, page)
        self._read()
        page._document._doc.set_link(page._page_number(), self._index, self._xref, target=spec)

    def __eq__(self, other: object) -> bool:
        """Compare the page and the annotation: its object number, or its index for a direct dictionary."""
        if not isinstance(other, Link):
            return NotImplemented
        return self._page == other._page and self._key() == other._key()

    def __hash__(self) -> int:
        """Hash consistently with equality."""
        return hash((self._page, self._key()))

    def _key(self) -> tuple[int, int]:
        return (self._xref, 0 if self._xref else self._index)

    def __repr__(self) -> str:
        """Return ``Link(rect=..., target=...)``, or a note for a stale handle."""
        try:
            _, _, rect, target = self._read()
        except (StaleObjectError, StalePageError):
            return "<stale Link>"
        return f"Link(rect={Rect(*rect)!r}, target={_target_from_core(target)!r})"


class Links(Sequence[Link]):
    """The link annotations of a page, in ``/Annots`` order, as ``page.links`` returns them.

    The sequence reads the page on every access. ``add(rect, target)`` adds
    a link and ``del page.links[i]`` removes one from the page.

    Reads reject more than 4,096 annotations or 1 MiB of target text per
    page. A named destination resolves through the ``/Names`` tree, which is
    indexed once until the next edit, or the legacy ``/Dests`` dictionary; a
    tree beyond 65,536 entries, 16 MiB of names, or 32 levels raises
    :class:`LimitError` rather than leaving the link unresolved.
    """

    __slots__ = ("_page",)

    def __init__(self, page: Page) -> None:
        """Wrap a page; use ``Page.links``."""
        self._page = page

    def _items(self) -> list[tuple[int, int, tuple[float, float, float, float], _TargetTuple]]:
        page = self._page
        return page._document._doc.read_links(page._page_number())

    def _handles(self) -> list[Link]:
        return [Link(self._page, xref, index) for index, xref, _, _ in self._items()]

    @overload
    def __getitem__(self, index: int) -> Link: ...
    @overload
    def __getitem__(self, index: slice) -> list[Link]: ...
    def __getitem__(self, index: int | slice) -> Link | list[Link]:
        """Return one link, or a list of them for a slice."""
        return self._handles()[index]

    def __len__(self) -> int:
        """Return the number of links on the page."""
        return len(self._items())

    def __iter__(self) -> Iterator[Link]:
        """Iterate over the links that the page lists now."""
        return iter(self._handles())

    def __delitem__(self, index: int | slice) -> None:
        """Remove links from the page; the annotation objects go at the next pruning save."""
        selected = self._handles()[index]
        links = selected if isinstance(selected, list) else [selected]
        page = self._page
        core = page._document._doc
        # Remove from the end, so that each removal keeps the earlier indexes.
        for link in sorted(links, key=lambda link: link._index, reverse=True):
            core.remove_annotation(page._page_number(), link._index, link._xref)

    def add(self, rect: Sequence[float], target: Target) -> Link:
        """Add a link over ``rect``, in display coordinates, that leads to ``target``, and return it.

        ``target`` is a URI string, a :class:`Destination` in this document, a
        :class:`RemoteDestination`, a :class:`Launch`, or a
        :class:`NamedAction`. The link draws no border. Its texts share a
        1 MiB budget, checked before any change with
        ``LimitError.code == "annotation_input_size"``.
        """
        page = self._page
        validated = _validate_rect(rect)
        spec = _link_target(target, page)
        xref = page._document._doc.add_link(page._page_number(), validated, spec)
        # The handle finds the new annotation by its object number.
        return Link(page, xref, 0)

    def __repr__(self) -> str:
        """Return ``Links([...])`` with the page's links."""
        return f"Links({list(self)!r})"


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Link, Links):
    _public.__module__ = "pylopdf"
