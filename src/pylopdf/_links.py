"""Links: ``Page.links`` and its ``Link`` handles."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, overload

from pylopdf._errors import StalePageError
from pylopdf._geometry import Point, Rect
from pylopdf._hints import hinted_getattr
from pylopdf._layers import Layer, LayerMembership, _annotation_layer, _set_annotation_layer
from pylopdf._limits import _validate_cumulative_utf8_input, _validate_rect
from pylopdf._targets import Destination, Target, _target_from_core, _target_to_core
from pylopdf.pylopdf_core import StaleObjectError

if TYPE_CHECKING:
    from pylopdf._document import Document
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
    :class:`StalePageError` once the page did. A link stored directly in
    ``/Annots`` has no object number, so its handle finds it by position;
    editing or removing a page's links first stores such links as objects,
    and a handle made before then goes stale instead of naming another link.
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
    def layer(self) -> Layer | LayerMembership | None:
        """Return the optional-content group or membership that shows or hides the link (``/OC``), or ``None``.

        Assigning a :class:`Layer` or a :class:`LayerMembership` sets it, and
        ``del`` removes it. Viewers hide the link with its group; hayro 0.8
        draws it regardless.
        """
        return _annotation_layer(self)

    @layer.setter
    def layer(self, layer: Layer | LayerMembership | None) -> None:
        _set_annotation_layer(self, layer)

    @layer.deleter
    def layer(self) -> None:
        _set_annotation_layer(self, None)

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
        self._xref = page._document._doc.set_link(page._page_number(), self._index, self._xref, rect=validated)

    @property
    def target(self) -> Target | None:
        """Return where the link leads, or ``None`` for an action that pylopdf does not read."""
        return _target_from_core(self._read()[3])

    @target.setter
    def target(self, target: Target) -> None:
        page = self._page
        spec = _link_target(target, page)
        self._read()
        self._xref = page._document._doc.set_link(page._page_number(), self._index, self._xref, target=spec)

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
        page._document._doc.remove_annotations(page._page_number(), [(link._index, link._xref) for link in links])

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


class Destinations(Mapping[str, Destination | None]):
    """The named destinations of a document, as ``doc.destinations`` returns them.

    Keys are the names of the catalog's ``/Names /Dests`` tree, the first
    entry of a repeated name winning, then those of the legacy ``/Dests``
    dictionary that the tree lacks; a value is the :class:`Destination` the
    name leads to, or ``None`` when it names no page. Links and the outline
    already read named destinations as the destinations they name. The
    mapping is read-only and reads the document on every access, under the
    named-destination limits: 65,536 entries or nodes, 131,072 edges, 32
    levels (``named_destination_count``), and 16 MiB of names
    (``named_destination_text_size``).
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.destinations``."""
        self._document = document

    def _items(self) -> dict[str, Destination | None]:
        self._document._ensure_open()
        return {
            name: None
            if target is None
            else Destination(target[0] - 1, None if target[1] is None else Point(target[1]), target[2])
            for name, target in self._document._doc.named_destinations()
        }

    def __getitem__(self, name: str) -> Destination | None:
        """Return where ``name`` leads; an unknown name raises :class:`KeyError`."""
        items = self._items()
        if not isinstance(name, str) or name not in items:
            raise KeyError(name)
        return items[name]

    def __iter__(self) -> Iterator[str]:
        """Iterate over the names, tree entries first."""
        return iter(list(self._items()))

    def __len__(self) -> int:
        """Return the number of names."""
        return len(self._items())

    def __repr__(self) -> str:
        """Return ``Destinations({...})``."""
        return f"Destinations({self._items()!r})"


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Destinations, Link, Links):
    _public.__module__ = "pylopdf"
