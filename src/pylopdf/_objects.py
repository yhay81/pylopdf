"""The typed low-level object model: names, strings, and object handles.

``Document.objects``, ``Document.trailer``, ``Document.catalog``, and
``Page.obj`` expose the PDF object graph as Python values. Scalars are plain
values: ``None``, ``bool``, ``int``, ``float``, :class:`Name`, and
:class:`String`. Arrays, dictionaries, and streams are live handles that read
and write the document directly; a handle addresses one indirect object, or a
direct container inside one, by object number and a path of keys and
indexes. References resolve on read, and a reference to a missing object reads
as ``None``, as ISO 32000-2 §7.3.10 reads it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping, MutableSequence
from typing import TYPE_CHECKING, Any, Literal, Self, cast, overload

from pylopdf._limits import _validate_optional_positive_int
from pylopdf.pylopdf_core import LimitError, StaleObjectError, _decode_text_string, _encode_text_string

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pylopdf._document import Document

#: Default bound on the bytes that ``Stream.read`` returns.
_DEFAULT_MAX_STREAM_DATA_SIZE = 64 * 1024 * 1024

#: Nesting of lists and mappings accepted in one written value; the Rust
#: boundary repeats the check.
_MAX_VALUE_DEPTH = 32

_Root = tuple[int, int] | None
_Step = int | bytes
_Path = tuple[_Step, ...]


def _name_bytes(name: str) -> bytes:
    """Return the bytes of a name or dictionary key."""
    return name.encode("utf-8", "surrogateescape")


def _name_text(data: bytes) -> str:
    """Return a name's bytes as text, losslessly for non-UTF-8 bytes."""
    return data.decode("utf-8", "surrogateescape")


class Name(str):
    """A PDF name, such as ``/Type``, held without its leading slash.

    A name compares equal to the plain string of its text, so
    ``page.obj["Type"] == "Page"`` holds. Writing needs the type, though: a
    plain ``str`` value is written as a PDF text string, so write
    ``Name("Page")`` for a name. Bytes that are not UTF-8 are kept as
    surrogate escapes, so a name read from a file is written back unchanged.
    """

    __slots__ = ()

    def __new__(cls, name: str) -> Self:
        """Create a name from its text without the leading slash."""
        if not isinstance(name, str):
            msg = f"a Name is made from a str: {name!r}"
            raise TypeError(msg)
        if name.startswith("/"):
            msg = f"give a Name without its leading slash: Name({name[1:]!r})"
            raise ValueError(msg)
        return super().__new__(cls, name)

    def __repr__(self) -> str:
        """Return ``Name('Type')``."""
        return f"Name({str.__repr__(self)})"


class String(bytes):
    """A PDF string: its bytes, with :attr:`text` decoding a text string.

    Write a ``str`` to store a text string (ASCII as is, anything else as
    UTF-16BE), or ``bytes`` or a :class:`String` to store the bytes exactly.
    """

    __slots__ = ()

    @property
    def text(self) -> str:
        """Decode the bytes as a PDF text string.

        A UTF-16BE or UTF-8 byte-order mark selects that encoding; otherwise
        the bytes are PDFDocEncoding (ISO 32000-2 §7.9.2.2).
        """
        return _decode_text_string(bytes(self))

    def __repr__(self) -> str:
        """Return ``String(b'...')``."""
        return f"String({bytes.__repr__(self)})"


class _Handle:
    """A view of one container in a document's object graph."""

    __slots__ = ("_document", "_epoch", "_path", "_root")

    def __init__(self, document: Document, root: _Root, path: _Path = ()) -> None:
        """Address ``path`` inside ``root``; ``None`` is the trailer."""
        self._document = document
        self._epoch = document._object_epoch
        self._root = root
        self._path = path

    def _core(self) -> Any:
        """Return the Rust document after checking that the handle is current."""
        document = self._document
        document._ensure_open()
        if self._epoch != document._object_epoch:
            msg = (
                f"{self._describe()} was invalidated when the document's objects were renumbered "
                "or reloaded; look it up again"
            )
            raise StaleObjectError(msg)
        return document._doc

    def _describe(self) -> str:
        """Describe the addressed container for messages and reprs."""
        root = "trailer" if self._root is None else f"{self._root[0]} {self._root[1]} R"
        steps = "".join(f"[{step}]" if isinstance(step, int) else f"/{_name_text(step)}" for step in self._path)
        return root + steps

    @property
    def xref(self) -> int | None:
        """Return the object number of an indirect object, ``None`` for a direct value or the trailer."""
        if self._root is None or self._path:
            return None
        return self._root[0]

    @property
    def generation(self) -> int | None:
        """Return the generation number of an indirect object, ``None`` for a direct value or the trailer."""
        if self._root is None or self._path:
            return None
        return self._root[1]

    @property
    def is_indirect(self) -> bool:
        """Return whether this is an indirect object rather than a direct value inside one."""
        return self._root is not None and not self._path

    def _changed(self, page_order_changed: bool) -> None:  # noqa: FBT001 - Mirrors the Rust result.
        """Record a write; a changed page order invalidates existing pages."""
        if page_order_changed:
            self._document._bump_generation()

    def _decode(self, value: Any, step: _Step) -> Any:
        """Wrap a tagged child value read at ``step``."""
        return _decode(self._document, value, (*self._path, step), self._root)

    def __eq__(self, other: object) -> bool:
        """Compare the document and the addressed container."""
        if not isinstance(other, _Handle):
            return NotImplemented
        return (
            self._document is other._document
            and self._epoch == other._epoch
            and self._root == other._root
            and self._path == other._path
        )

    def __hash__(self) -> int:
        """Hash the document and the addressed container."""
        return hash((id(self._document), self._epoch, self._root, self._path))

    def __repr__(self) -> str:
        """Return ``<Dictionary 12 0 R>`` or ``<Array 12 0 R/Kids>``."""
        return f"<{type(self).__name__} {self._describe()}>"


class Dictionary(_Handle, MutableMapping[str, Any]):
    """A live PDF dictionary keyed by name text without the slash.

    Reading a key resolves references: an array, dictionary, or stream comes
    back as a handle, and anything else as a plain value. Assigning a value
    writes it immediately. Plain values, lists, and mappings are written as
    direct objects; an indirect handle from the same document is written as a
    reference to it; and a direct handle is copied.
    """

    __slots__ = ()

    def __getitem__(self, key: str) -> Any:
        """Return the resolved value of ``key``."""
        return self._decode(self._core().object_get(self._root, list(self._path), _key(key)), _key(key))

    def __setitem__(self, key: str, value: Any) -> None:
        """Write ``value`` under ``key``."""
        core = self._core()
        encoded = _encode(self._document, value, 1)
        self._changed(core.object_set(self._root, list(self._path), _key(key), encoded))

    def __delitem__(self, key: str) -> None:
        """Remove ``key``."""
        core = self._core()
        self._changed(core.object_delete(self._root, list(self._path), _key(key)))

    def __iter__(self) -> Iterator[str]:
        """Iterate over the keys as :class:`Name` values."""
        return iter([Name(_name_text(key)) for key in self._core().object_keys(self._root, list(self._path))])

    def __len__(self) -> int:
        """Return the number of entries."""
        return self._core().object_len(self._root, list(self._path))

    def __contains__(self, key: object) -> bool:
        """Return whether ``key`` is present, without resolving its value."""
        if not isinstance(key, str):
            return False
        return _key(key) in self._core().object_keys(self._root, list(self._path))


class Stream(Dictionary):
    """A live PDF stream: its dictionary, plus :meth:`read` and :meth:`write`."""

    __slots__ = ()

    def read(self, *, decode: bool = True, max_size: int | None = _DEFAULT_MAX_STREAM_DATA_SIZE) -> bytes:
        """Return the stream data, decoded through its filters unless ``decode`` is false.

        Decoding applies every filter that lopdf supports and raises
        :class:`PdfError` for any other, instead of returning encoded bytes.
        ``max_size`` bounds every decoded filter layer and the result;
        ``None`` explicitly opts out for trusted input. Refusal raises
        :class:`LimitError` with code ``stream_data_size``.
        """
        _validate_optional_positive_int("max_size", max_size)
        root = self._stream_root()
        return self._core().stream_read(root, list(self._path), decode, max_size)

    def write(self, data: bytes, /, *, filter: Literal["FlateDecode"] | None = "FlateDecode") -> None:  # noqa: A002 - pymupdf and pikepdf name it filter.
        """Replace the stream data with ``data``, given decoded.

        ``filter="FlateDecode"`` compresses it; ``None`` stores it
        uncompressed. Either way the previous ``/Filter`` and ``/DecodeParms``
        are removed and ``/Length`` is updated.
        """
        if filter not in ("FlateDecode", None):
            msg = f"filter must be 'FlateDecode' or None: {filter!r}"
            raise ValueError(msg)
        payload = bytes(data)
        root = self._stream_root()
        core = self._core()
        self._changed(core.stream_write(root, list(self._path), payload, filter is not None))

    def _stream_root(self) -> tuple[int, int]:
        """Return the object that holds this stream; the trailer holds none."""
        if self._root is None:
            msg = "the trailer holds no streams"
            raise StaleObjectError(msg)
        return self._root


class Array(_Handle, MutableSequence[Any]):
    """A live PDF array; elements resolve and write as :class:`Dictionary` values do."""

    __slots__ = ()

    @overload
    def __getitem__(self, index: int) -> Any: ...
    @overload
    def __getitem__(self, index: slice) -> list[Any]: ...
    def __getitem__(self, index: int | slice) -> Any:
        """Return the resolved element at ``index``, or a list for a slice."""
        if isinstance(index, slice):
            return [self[position] for position in range(*index.indices(len(self)))]
        position = self._position(index)
        return self._decode(self._core().object_get(self._root, list(self._path), position), position)

    @overload
    def __setitem__(self, index: int, value: Any) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[Any]) -> None: ...
    def __setitem__(self, index: int | slice, value: Any) -> None:
        """Write ``value`` at ``index``; slices are not supported."""
        if isinstance(index, slice):
            msg = "slice assignment is not supported; assign elements one by one"
            raise TypeError(msg)
        position = self._position(index)
        core = self._core()
        encoded = _encode(self._document, value, 1)
        self._changed(core.object_set(self._root, list(self._path), position, encoded))

    @overload
    def __delitem__(self, index: int) -> None: ...
    @overload
    def __delitem__(self, index: slice) -> None: ...
    def __delitem__(self, index: int | slice) -> None:
        """Remove the element at ``index``; slices are not supported."""
        if isinstance(index, slice):
            msg = "slice deletion is not supported; delete elements one by one"
            raise TypeError(msg)
        position = self._position(index)
        core = self._core()
        self._changed(core.object_delete(self._root, list(self._path), position))

    def __len__(self) -> int:
        """Return the number of elements."""
        return self._core().object_len(self._root, list(self._path))

    def insert(self, index: int, value: Any) -> None:
        """Insert ``value`` before ``index``, clamped as ``list.insert`` clamps it."""
        length = len(self)
        position = max(0, min(length, index + length if index < 0 else index))
        core = self._core()
        encoded = _encode(self._document, value, 1)
        self._changed(core.object_insert(self._root, list(self._path), position, encoded))

    def _position(self, index: int) -> int:
        """Resolve a negative index and check the range."""
        if not isinstance(index, int) or isinstance(index, bool):
            msg = f"array indexes are integers: {index!r}"
            raise TypeError(msg)
        length = len(self)
        position = index + length if index < 0 else index
        if not 0 <= position < length:
            msg = f"array index {index} out of range"
            raise IndexError(msg)
        return position


class Objects(Mapping[int, Any]):
    """The indirect objects of a document, keyed by object number.

    Look up ``doc.objects[xref]`` to read an object, assign to replace it,
    and call :meth:`add` or :meth:`add_stream` to create one. When a number
    is stored under several generations, the highest one is used. An object
    that is an array, dictionary, or stream comes back as a handle; any other
    as its value. Assigning a handle here copies what it addresses, so
    ``doc.objects[target] = doc.objects[source]`` copies an object, whereas
    assigning an indirect handle inside a container writes a reference.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.objects``."""
        self._document = document

    def _core(self) -> Any:
        self._document._ensure_open()
        return self._document._doc

    def __getitem__(self, xref: int) -> Any:
        """Return the object stored under ``xref``."""
        number = _object_number(xref)
        found = self._core().object_lookup(number)
        if found is None:
            raise KeyError(xref)
        generation, value = found
        return _decode_object(self._document, number, generation, value)

    def __setitem__(self, xref: int, value: Any) -> None:
        """Replace the object stored under ``xref``, which must exist."""
        number = _object_number(xref)
        core = self._core()
        encoded = _encode(self._document, value, 0, top_level=True)
        if core.object_replace(number, encoded):
            self._document._bump_generation()

    def __iter__(self) -> Iterator[int]:
        """Iterate over object numbers in ascending order."""
        return iter(self._core().object_numbers())

    def __len__(self) -> int:
        """Return the number of object numbers in use."""
        return len(self._core().object_numbers())

    def __contains__(self, xref: object) -> bool:
        """Return whether an object is stored under ``xref``."""
        if not isinstance(xref, int) or isinstance(xref, bool) or xref < 0:
            return False
        return self._core().object_lookup(xref) is not None

    def add(self, value: Any) -> int:
        """Add ``value`` as a new indirect object and return its object number."""
        core = self._core()
        encoded = _encode(self._document, value, 0, top_level=True)
        number, _generation, changed = core.object_add(encoded)
        if changed:
            self._document._bump_generation()
        return number

    def add_stream(
        self,
        data: bytes,
        /,
        dictionary: Mapping[str, Any] | None = None,
        *,
        filter: Literal["FlateDecode"] | None = "FlateDecode",  # noqa: A002 - pymupdf and pikepdf name it filter.
    ) -> int:
        """Add a stream of decoded ``data`` and return its object number.

        ``dictionary`` supplies further stream entries; ``/Length``,
        ``/Filter``, and ``/DecodeParms`` are set from the data and
        ``filter``, which compresses with ``"FlateDecode"`` or stores the
        data as given with ``None``.
        """
        if filter not in ("FlateDecode", None):
            msg = f"filter must be 'FlateDecode' or None: {filter!r}"
            raise ValueError(msg)
        core = self._core()
        encoded = _encode(self._document, {} if dictionary is None else dictionary, 0, top_level=True)
        number, _generation, changed = core.object_add_stream(encoded, bytes(data), filter is not None)
        if changed:
            self._document._bump_generation()
        return number

    def __repr__(self) -> str:
        """Return ``<Objects of N>``."""
        return f"<Objects of {len(self)}>"


def _object_number(xref: object) -> int:
    """Validate an object number."""
    if not isinstance(xref, int) or isinstance(xref, bool):
        msg = f"object numbers are integers: {xref!r}"
        raise TypeError(msg)
    if xref < 0:
        raise KeyError(xref)
    return xref


def _key(key: object) -> bytes:
    """Return the bytes of a dictionary key."""
    if not isinstance(key, str):
        msg = f"dictionary keys are str or Name: {key!r}"
        raise TypeError(msg)
    if key.startswith("/"):
        msg = f"give a key without its leading slash: {key[1:]!r}"
        raise ValueError(msg)
    return _name_bytes(key)


_CONTAINERS: dict[str, type[_Handle]] = {"a": Array, "d": Dictionary, "t": Stream}


def _decode(document: Document, value: Any, path: _Path, root: _Root = None) -> Any:
    """Wrap a tagged value that Rust read inside the container at ``path``."""
    if not isinstance(value, tuple):
        return value
    tag = value[0]
    if tag == "n":
        # A name read from a file may begin with an escaped slash, which
        # the constructor refuses as a likely mistake.
        return str.__new__(Name, _name_text(value[1]))
    if tag == "s":
        return String(value[1])
    indirect = value[1]
    kind = _CONTAINERS[tag]
    if indirect is not None:
        return kind(document, tuple(indirect))
    return kind(document, root, path)


def _decode_object(document: Document, number: int, generation: int, value: Any) -> Any:
    """Wrap the tagged value of an indirect object."""
    if isinstance(value, tuple) and value[0] in _CONTAINERS and value[1] is None:
        return _CONTAINERS[value[0]](document, (number, generation))
    return _decode(document, value, ())


def _encode(document: Document, value: Any, depth: int, *, top_level: bool = False) -> Any:  # noqa: C901, PLR0911 - one branch per value kind.
    """Convert a Python value to the tagged form that Rust writes."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Name):
        return ("n", _name_bytes(value))
    if isinstance(value, str):
        return ("s", _encode_text_string(value), False)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ("s", bytes(value), False)
    if isinstance(value, _Handle):
        if value._document is not document:
            msg = "a handle from another document cannot be written; copy the value instead"
            raise ValueError(msg)
        value._core()
        if value.is_indirect and not top_level:
            root = cast("tuple[int, int]", value._root)
            return ("r", root[0], root[1])
        return ("c", value._root, list(value._path))
    if depth >= _MAX_VALUE_DEPTH:
        msg = f"a written value nests lists and mappings deeper than {_MAX_VALUE_DEPTH} levels"
        code = "object_value_depth"
        raise LimitError(code, msg)
    if isinstance(value, Mapping):
        items = []
        for key, item in value.items():
            items.append((_key(key), _encode(document, item, depth + 1)))
        return ("d", items)
    if isinstance(value, (list, tuple)):
        return ("a", [_encode(document, item, depth + 1) for item in value])
    msg = f"cannot write a {type(value).__name__} as a PDF object: {value!r}"
    raise TypeError(msg)


for _public in (Name, String, Dictionary, Stream, Array, Objects):
    _public.__module__ = "pylopdf"
