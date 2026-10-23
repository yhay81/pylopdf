"""Optional content: ``Document.layers`` and its :class:`Layer` handles."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING, overload

from pylopdf._limits import _validate_utf8_text_input
from pylopdf._objects import Dictionary
from pylopdf.pylopdf_core import StaleObjectError

if TYPE_CHECKING:
    from pylopdf._document import Document
    from pylopdf.pylopdf_core import _Document

#: The longest group name that a write takes, in UTF-8 bytes; the core's
#: ``layer_input_size`` boundary.
_MAX_LAYER_NAME_SIZE = 1024 * 1024
#: The most intents that a group takes, and the longest of them in bytes.
_MAX_LAYER_INTENTS = 64
_MAX_LAYER_INTENT_SIZE = 127
#: The PDF delimiters, which a name that pylopdf writes leaves out.
_NAME_DELIMITERS = frozenset("()<>[]{}/%#")


def _validate_name(name: object) -> str:
    if not isinstance(name, str):
        msg = f"a layer name must be a str: {name!r}"
        raise TypeError(msg)
    _validate_utf8_text_input(name, _MAX_LAYER_NAME_SIZE, limit_code="layer_input_size", input_label="layer name")
    return name


def _validate_intent(intent: object) -> list[str]:
    """Validate a group's intents: a non-empty iterable of PDF name texts."""
    if isinstance(intent, str):
        msg = f"intent takes a tuple of names, such as ({intent!r},), not a str"
        raise TypeError(msg)
    if not isinstance(intent, Iterable):
        msg = f"intent must be a tuple of names: {intent!r}"
        raise TypeError(msg)
    names: list[str] = []
    for name in intent:
        if len(names) == _MAX_LAYER_INTENTS:
            msg = f"a layer takes at most {_MAX_LAYER_INTENTS} intents"
            raise ValueError(msg)
        if not isinstance(name, str):
            msg = f"an intent must be a str: {name!r}"
            raise TypeError(msg)
        if not (
            0 < len(name) <= _MAX_LAYER_INTENT_SIZE
            and name.isascii()
            and name.isprintable()
            and " " not in name
            and _NAME_DELIMITERS.isdisjoint(name)
        ):
            msg = (
                f"an intent is a PDF name of 1 to {_MAX_LAYER_INTENT_SIZE} printable ASCII characters "
                f"other than delimiters: {name!r}"
            )
            raise ValueError(msg)
        names.append(name)
    if not names:
        msg = "a layer needs at least one intent, such as ('View',)"
        raise ValueError(msg)
    return names


class Layer:
    """A handle on one optional-content group, as ``doc.layers`` yields it.

    A group is a layer of content that a viewer can show or hide. Properties
    read the document on every access. ``is_visible`` is what the default
    configuration (``/OCProperties /D``) makes of the group, which is the
    state that rendering and extraction use; assigning it edits that
    configuration, so pages render and extract with the group shown or
    hidden. A handle raises :class:`StaleObjectError` once its group no
    longer exists or the document's objects were renumbered.
    """

    __slots__ = ("_document", "_epoch", "_id")

    def __init__(self, document: Document, xref: int, generation: int) -> None:
        """Create a handle; :class:`Layers` does this."""
        self._document = document
        self._epoch = document._object_epoch
        self._id = (xref, generation)

    def _core(self) -> _Document:
        document = self._document
        document._ensure_open()
        if self._epoch != document._object_epoch:
            msg = f"layer {self._id[0]} was invalidated when the document's objects were renumbered or reloaded"
            raise StaleObjectError(msg)
        return document._doc

    @property
    def xref(self) -> int:
        """Return the group's object number."""
        return self._id[0]

    @property
    def obj(self) -> Dictionary:
        """Return the group's dictionary in the low-level object model."""
        self._core().layer_info(self._id)
        return Dictionary(self._document, self._id)

    @property
    def name(self) -> str:
        """Return the group's name (``/Name``), which viewers show."""
        return self._core().layer_info(self._id)[0]

    @name.setter
    def name(self, name: str) -> None:
        name = _validate_name(name)
        self._core().set_layer_name(self._id, name)

    @property
    def intent(self) -> tuple[str, ...]:
        """Return the group's intents (``/Intent``), ``("View",)`` when unset.

        A viewer shows or hides only the groups whose intents meet its own,
        ``View`` by default; ``Design`` marks design-time content.
        """
        return tuple(self._core().layer_info(self._id)[1])

    @intent.setter
    def intent(self, intent: Iterable[str]) -> None:
        names = _validate_intent(intent)
        self._core().set_layer_intent(self._id, names)

    @property
    def is_visible(self) -> bool:
        """Return whether the default configuration shows the group.

        Assigning ``True`` also hides the other groups of any radio-button
        group (``/RBGroups``) that holds this one, as a viewer does.
        """
        return self._core().layer_info(self._id)[2]

    @is_visible.setter
    def is_visible(self, visible: bool) -> None:
        if not isinstance(visible, bool):
            msg = f"is_visible must be a bool: {visible!r}"
            raise TypeError(msg)
        self._core().set_layer_visible(self._id, visible)

    def __eq__(self, other: object) -> bool:
        """Compare document, object epoch, and object."""
        if not isinstance(other, Layer):
            return NotImplemented
        return (self._document, self._epoch, self._id) == (other._document, other._epoch, other._id)

    def __hash__(self) -> int:
        """Hash document, object epoch, and object."""
        return hash((id(self._document), self._epoch, self._id))

    def __repr__(self) -> str:
        """Return ``Layer(xref=..., name=...)``, or a stale marker."""
        try:
            return f"Layer(xref={self._id[0]}, name={self.name!r})"
        except (StaleObjectError, ValueError):
            return f"<stale Layer xref={self._id[0]}>"


class Layers(Sequence[Layer]):
    """The document's optional-content groups, as ``doc.layers`` returns it.

    The sequence lists the groups of ``/OCProperties /OCGs`` in that order,
    each once. The view reads the document on every access. An array of
    more than 65,536 entries raises :class:`LimitError` with code
    ``layer_count``, and a group whose name and intents decode to more than
    1 MiB raises it with ``layer_text_size``.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.layers``."""
        self._document = document

    def _layers(self) -> list[Layer]:
        document = self._document
        document._ensure_open()
        return [Layer(document, xref, generation) for xref, generation in document._doc.layers()]

    @overload
    def __getitem__(self, index: int) -> Layer: ...
    @overload
    def __getitem__(self, index: slice) -> list[Layer]: ...
    def __getitem__(self, index: int | slice) -> Layer | list[Layer]:
        """Return a group, or a list of them for a slice."""
        return self._layers()[index]

    def __len__(self) -> int:
        """Return the number of groups."""
        return len(self._layers())

    def __iter__(self) -> Iterator[Layer]:
        """Iterate over the groups."""
        return iter(self._layers())

    def add(self, name: str, /, *, visible: bool = True, intent: Iterable[str] = ("View",)) -> Layer:
        """Add a group and return its handle.

        The group joins ``/OCGs`` and the default configuration's ``/Order``,
        creating ``/OCProperties`` when the document has none, and starts
        shown or hidden as ``visible`` says. ``intent`` takes PDF names such
        as ``"View"`` and ``"Design"``. A name above 1 MiB of UTF-8 raises
        :class:`LimitError` with code ``layer_input_size``.
        """
        name = _validate_name(name)
        if not isinstance(visible, bool):
            msg = f"visible must be a bool: {visible!r}"
            raise TypeError(msg)
        names = _validate_intent(intent)
        document = self._document
        document._ensure_open()
        xref, generation = document._doc.add_layer(name, names, visible)
        return Layer(document, xref, generation)

    def __repr__(self) -> str:
        """Return ``Layers([...])``."""
        return f"Layers({self._layers()!r})"


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (Layer, Layers):
    _public.__module__ = "pylopdf"
