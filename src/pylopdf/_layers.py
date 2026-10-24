"""Optional content: ``Document.layers`` and its :class:`Layer` handles."""

from __future__ import annotations

import enum
import itertools
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, overload

from pylopdf._limits import _validate_utf8_text_input
from pylopdf._objects import Dictionary
from pylopdf.pylopdf_core import LimitError, StaleObjectError

if TYPE_CHECKING:
    from pylopdf._document import Document
    from pylopdf.pylopdf_core import _Document

#: The longest group name that a write takes, in UTF-8 bytes; the core's
#: ``layer_input_size`` boundary.
_MAX_LAYER_NAME_SIZE = 1024 * 1024
#: The most intents that a group takes, and the longest of them in bytes.
_MAX_LAYER_INTENTS = 64
_MAX_LAYER_INTENT_SIZE = 127
#: The most groups that one array of a configuration lists; the core's
#: ``layer_count`` boundary.
_MAX_LAYER_ENTRIES = 65_536
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


class LayerBaseState(enum.StrEnum):
    """The state a configuration gives a group that ``on`` and ``off`` do not list (``/BaseState``)."""

    ON = "ON"
    """Shown."""
    OFF = "OFF"
    """Hidden."""
    UNCHANGED = "Unchanged"
    """As it was before the configuration applied; hayro shows it."""


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


def _layer_ids(document: Document, layers: Iterable[Layer], label: str) -> list[tuple[int, int]]:
    """Return the object IDs of current handles on this document's groups."""
    if isinstance(layers, Layer) or not isinstance(layers, Iterable):
        msg = f"{label} takes an iterable of Layer handles: {layers!r}"
        raise TypeError(msg)
    ids: list[tuple[int, int]] = []
    for layer in itertools.islice(layers, _MAX_LAYER_ENTRIES + 1):
        if not isinstance(layer, Layer):
            msg = f"{label} takes Layer handles: {layer!r}"
            raise TypeError(msg)
        if layer._document is not document:
            msg = f"{label} takes layers of this document: {layer!r}"
            raise ValueError(msg)
        layer._core()
        ids.append(layer._id)
    if len(ids) > _MAX_LAYER_ENTRIES:
        code = "layer_count"
        msg = f"{label} lists more than {_MAX_LAYER_ENTRIES} layers"
        raise LimitError(code, msg)
    return ids


@dataclass(frozen=True, slots=True)
class LayerOrder:
    """A nested array of a configuration's ``order`` (``/Order``).

    With a ``label``, it is a labeled group of entries that a viewer shows
    under the label. Without one, it holds the children of the layer that
    precedes it in the enclosing tuple, as ISO 32000-2 Table 101 nests them.
    """

    label: str | None
    items: tuple[Layer | LayerOrder, ...] = ()

    def __post_init__(self) -> None:
        """Validate the label and the items, and keep the items as a tuple."""
        if self.label is not None and not isinstance(self.label, str):
            msg = f"a LayerOrder label must be a str or None: {self.label!r}"
            raise TypeError(msg)
        items = tuple(self.items)
        for item in items:
            if not isinstance(item, (Layer, LayerOrder)):
                msg = f"a LayerOrder holds Layer handles and LayerOrder values: {item!r}"
                raise TypeError(msg)
        object.__setattr__(self, "items", items)


_OrderEntry = tuple[int, bool, tuple[int, int], str | None]


def _order_from_core(document: Document, entries: list[_OrderEntry]) -> tuple[Layer | LayerOrder, ...]:
    """Build the tree of ``order`` from the core's preorder entries."""
    position = 0

    def level(depth: int) -> tuple[Layer | LayerOrder, ...]:
        nonlocal position
        items: list[Layer | LayerOrder] = []
        while position < len(entries) and entries[position][0] == depth:
            _, is_group, (xref, generation), label = entries[position]
            position += 1
            if is_group:
                items.append(LayerOrder(label, level(depth + 1)))
            else:
                items.append(Layer(document, xref, generation))
        return tuple(items)

    return level(0)


def _order_to_core(document: Document, order: Iterable[Layer | LayerOrder]) -> list[_OrderEntry]:
    """Flatten an ``order`` tree into the core's preorder entries."""
    if isinstance(order, (Layer, LayerOrder)) or not isinstance(order, Iterable):
        msg = f"order takes an iterable of Layer handles and LayerOrder values: {order!r}"
        raise TypeError(msg)
    entries: list[_OrderEntry] = []
    text = 0
    stack: list[tuple[Iterator[Layer | LayerOrder], int]] = [(iter(order), 0)]
    while stack:
        items, depth = stack[-1]
        item = next(items, None)
        if item is None:
            stack.pop()
            continue
        if len(entries) == _MAX_LAYER_ENTRIES:
            code = "layer_count"
            msg = f"order lists more than {_MAX_LAYER_ENTRIES} entries"
            raise LimitError(code, msg)
        if isinstance(item, LayerOrder):
            if item.label is not None:
                text += len(item.label.encode("utf-8", "surrogatepass"))
                if text > _MAX_LAYER_NAME_SIZE:
                    code = "layer_input_size"
                    msg = f"order labels exceed {_MAX_LAYER_NAME_SIZE} bytes"
                    raise LimitError(code, msg)
            entries.append((depth, True, (0, 0), item.label))
            stack.append((iter(item.items), depth + 1))
        elif isinstance(item, Layer):
            entries.append((depth, False, _layer_ids(document, (item,), "order")[0], None))
        else:
            msg = f"order holds Layer handles and LayerOrder values: {item!r}"
            raise TypeError(msg)
    return entries


class LayerConfiguration:
    """A handle on one optional-content configuration.

    ``doc.layers.configurations[0]`` is the default configuration
    (``/OCProperties /D``), which viewers apply when they open the document
    and which rendering and extraction use; the others are the alternatives
    of ``/Configs``. Properties read the document on every access and are
    assignable. :meth:`apply` makes an alternative the default.
    """

    __slots__ = ("_document", "_epoch", "_slot")

    def __init__(self, document: Document, slot: int) -> None:
        """Create a handle; :class:`LayerConfigurations` does this."""
        self._document = document
        self._epoch = document._object_epoch
        self._slot = slot

    def _core(self) -> _Document:
        document = self._document
        document._ensure_open()
        if self._epoch != document._object_epoch:
            msg = f"layer configuration {self._slot} was invalidated when the document's objects were renumbered"
            raise StaleObjectError(msg)
        return document._doc

    def _read(
        self,
    ) -> tuple[
        str | None,
        str | None,
        str,
        list[tuple[int, int]],
        list[tuple[int, int]],
        list[tuple[int, int]],
        list[list[tuple[int, int]]],
        list[_OrderEntry],
    ]:
        return self._core().layer_configuration(self._slot)

    def _layers(self, ids: Iterable[tuple[int, int]]) -> tuple[Layer, ...]:
        return tuple(Layer(self._document, xref, generation) for xref, generation in ids)

    @property
    def index(self) -> int:
        """Return the configuration's position: 0 for the default."""
        return self._slot

    @property
    def name(self) -> str | None:
        """Return the name a viewer shows (``/Name``), or ``None``; assignable."""
        return self._read()[0]

    @name.setter
    def name(self, name: str | None) -> None:
        self._set_text("Name", name)

    @property
    def creator(self) -> str | None:
        """Return the application that made it (``/Creator``), or ``None``; assignable."""
        return self._read()[1]

    @creator.setter
    def creator(self, creator: str | None) -> None:
        self._set_text("Creator", creator)

    def _set_text(self, key: str, value: str | None) -> None:
        if value is not None:
            value = _validate_name(value)
        self._core().set_layer_configuration_text(self._slot, key, value)

    @property
    def base_state(self) -> LayerBaseState:
        """Return the state of the groups that ``on`` and ``off`` leave out; assignable."""
        return LayerBaseState(self._read()[2])

    @base_state.setter
    def base_state(self, state: LayerBaseState | str) -> None:
        self._core().set_layer_configuration_base_state(self._slot, LayerBaseState(state).value)

    @property
    def on(self) -> tuple[Layer, ...]:
        """Return the groups that the configuration shows (``/ON``); assignable."""
        return self._layers(self._read()[3])

    @on.setter
    def on(self, layers: Iterable[Layer]) -> None:
        self._set_refs("ON", layers, "on")

    @property
    def off(self) -> tuple[Layer, ...]:
        """Return the groups that the configuration hides (``/OFF``); assignable.

        A group in both ``on`` and ``off`` is hidden, as hayro reads them.
        """
        return self._layers(self._read()[4])

    @off.setter
    def off(self, layers: Iterable[Layer]) -> None:
        self._set_refs("OFF", layers, "off")

    @property
    def locked(self) -> tuple[Layer, ...]:
        """Return the groups whose state a viewer must not let the user change (``/Locked``); assignable."""
        return self._layers(self._read()[5])

    @locked.setter
    def locked(self, layers: Iterable[Layer]) -> None:
        self._set_refs("Locked", layers, "locked")

    def _set_refs(self, key: str, layers: Iterable[Layer], label: str) -> None:
        ids = _layer_ids(self._document, layers, label)
        self._core().set_layer_configuration_refs(self._slot, key, ids)

    @property
    def radio_groups(self) -> tuple[tuple[Layer, ...], ...]:
        """Return the radio-button groups (``/RBGroups``): at most one group of each is shown; assignable."""
        return tuple(self._layers(group) for group in self._read()[6])

    @radio_groups.setter
    def radio_groups(self, groups: Iterable[Iterable[Layer]]) -> None:
        if not isinstance(groups, Iterable):
            msg = f"radio_groups takes an iterable of layer tuples: {groups!r}"
            raise TypeError(msg)
        ids: list[list[tuple[int, int]]] = []
        total = 0
        for group in groups:
            members = _layer_ids(self._document, group, "a radio-button group")
            total += len(members)
            if len(ids) == _MAX_LAYER_ENTRIES or total > _MAX_LAYER_ENTRIES:
                code = "layer_count"
                msg = f"radio_groups list more than {_MAX_LAYER_ENTRIES} layers"
                raise LimitError(code, msg)
            ids.append(members)
        self._core().set_layer_configuration_radio_groups(self._slot, ids)

    @property
    def order(self) -> tuple[Layer | LayerOrder, ...]:
        """Return the tree a viewer's layer panel shows (``/Order``); assignable.

        Entries are :class:`Layer` handles and :class:`LayerOrder` values; an
        unlabeled ``LayerOrder`` after a layer holds its children.
        """
        return _order_from_core(self._document, self._read()[7])

    @order.setter
    def order(self, order: Iterable[Layer | LayerOrder]) -> None:
        entries = _order_to_core(self._document, order)
        self._core().set_layer_configuration_order(self._slot, entries)

    def apply(self) -> None:
        """Make this configuration the default, which rendering and extraction use.

        The default configuration takes a copy of this one's entries; it
        keeps its own ``order`` when this one has none.
        """
        self._core().apply_layer_configuration(self._slot)

    def __eq__(self, other: object) -> bool:
        """Compare document, object epoch, and position."""
        if not isinstance(other, LayerConfiguration):
            return NotImplemented
        return (self._document, self._epoch, self._slot) == (other._document, other._epoch, other._slot)

    def __hash__(self) -> int:
        """Hash document, object epoch, and position."""
        return hash((id(self._document), self._epoch, self._slot))

    def __repr__(self) -> str:
        """Return ``LayerConfiguration(index=..., name=...)``, or a stale marker."""
        try:
            return f"LayerConfiguration(index={self._slot}, name={self.name!r})"
        except (StaleObjectError, ValueError):
            return f"<stale LayerConfiguration index={self._slot}>"


class LayerConfigurations(Sequence[LayerConfiguration]):
    """The optional-content configurations, as ``doc.layers.configurations`` returns it.

    The default configuration comes first, then the alternatives of
    ``/Configs``; a document without optional content has none.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Layers.configurations``."""
        self._document = document

    def _configurations(self) -> list[LayerConfiguration]:
        document = self._document
        document._ensure_open()
        count = document._doc.layer_configuration_count()
        return [LayerConfiguration(document, slot) for slot in range(count)]

    @overload
    def __getitem__(self, index: int) -> LayerConfiguration: ...
    @overload
    def __getitem__(self, index: slice) -> list[LayerConfiguration]: ...
    def __getitem__(self, index: int | slice) -> LayerConfiguration | list[LayerConfiguration]:
        """Return a configuration, or a list of them for a slice."""
        return self._configurations()[index]

    def __len__(self) -> int:
        """Return the number of configurations."""
        return len(self._configurations())

    def __iter__(self) -> Iterator[LayerConfiguration]:
        """Iterate over the configurations, the default first."""
        return iter(self._configurations())

    def add(
        self,
        name: str,
        /,
        *,
        creator: str | None = None,
        base_state: LayerBaseState | str = LayerBaseState.ON,
        on: Iterable[Layer] = (),
        off: Iterable[Layer] = (),
    ) -> LayerConfiguration:
        """Add an alternative configuration to ``/Configs`` and return its handle.

        A document without optional content gets an empty default
        configuration first, so the new one is at index 1 or later.
        """
        name = _validate_name(name)
        if creator is not None:
            creator = _validate_name(creator)
        state = LayerBaseState(base_state).value
        document = self._document
        on_ids = _layer_ids(document, on, "on")
        off_ids = _layer_ids(document, off, "off")
        document._ensure_open()
        slot = document._doc.add_layer_configuration(name, creator, state, on_ids, off_ids)
        return LayerConfiguration(document, slot)

    def __repr__(self) -> str:
        """Return ``LayerConfigurations([...])``."""
        return f"LayerConfigurations({self._configurations()!r})"


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

    @property
    def configurations(self) -> LayerConfigurations:
        """Return the configurations: the default first, then the alternatives."""
        return LayerConfigurations(self._document)

    def __repr__(self) -> str:
        """Return ``Layers([...])``."""
        return f"Layers({self._layers()!r})"


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (Layer, LayerBaseState, LayerConfiguration, LayerConfigurations, LayerOrder, Layers):
    _public.__module__ = "pylopdf"
