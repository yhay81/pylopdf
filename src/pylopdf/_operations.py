"""Operator-level content: content streams as sequences of operations.

``Page.operations`` reads a page's content as hayro tokenizes it and writes it
back. Every operation that a parse produced remembers its bytes, so writing an
unchanged operation writes those bytes again, and only new or replaced
operations are written from their values.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, overload

from pylopdf._limits import _validate_optional_positive_int
from pylopdf._objects import Name, String, _name_bytes, _name_text
from pylopdf.pylopdf_core import LimitError, _encode_text_string, _parse_operations

#: Operations that a parse returns by default.
_DEFAULT_MAX_OPERATIONS = 1 << 20

#: Bytes of content that ``Operations.parse`` accepts by default, and that a
#: page's content may hold when it is read or written.
_DEFAULT_MAX_CONTENT_SIZE = 64 * 1024 * 1024

#: Nesting of arrays and dictionaries written in one operand, the depth that
#: hayro reads.
_MAX_OPERAND_DEPTH = 64

_WHITESPACE = b"\x00\t\n\x0c\r "
_DELIMITERS = b"()<>[]{}/%"
_REGULAR_OPERATOR_BYTES = frozenset(range(0x21, 0x7F)) - frozenset(_DELIMITERS)


@dataclass(frozen=True, slots=True)
class Operation:
    """One content-stream operation: an operator and its operands.

    ``operator`` is the operator's text, such as ``"Tj"`` or ``"cm"``.
    ``operands`` holds the values that precede it, as the object model reads
    them: ``None``, ``bool``, ``int``, ``float``, :class:`Name`,
    :class:`String`, tuples for arrays, and dictionaries keyed by name text.
    A plain ``str`` operand is written as a text string, as in the object
    model; write :class:`String` or ``bytes`` for the bytes that a font
    shows.
    """

    operator: str
    operands: tuple[Any, ...] = ()
    _source: bytes | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Check the operator and turn the operands into a tuple."""
        if not isinstance(self.operator, str):
            msg = f"operator must be a str: {self.operator!r}"
            raise TypeError(msg)
        data = _name_bytes(self.operator)
        if not data or not all(byte in _REGULAR_OPERATOR_BYTES for byte in data):
            msg = f"an operator is a run of regular characters, such as 'Tj': {self.operator!r}"
            raise ValueError(msg)
        if data == b"BI":
            msg = "write an inline image as InlineImage(dictionary, data)"
            raise ValueError(msg)
        if isinstance(self.operands, (str, bytes)) or not isinstance(self.operands, Iterable):
            msg = f"operands must be a sequence of values: {self.operands!r}"
            raise TypeError(msg)
        operands = tuple(self.operands)
        for operand in operands:
            _check_value(operand, 0)
        object.__setattr__(self, "operands", operands)


@dataclass(frozen=True, slots=True)
class InlineImage:
    """An inline image: its dictionary, keyed by name text as written, and its data.

    The dictionary keeps the abbreviations of ISO 32000-2 §8.9.7 as the
    stream wrote them (``W``, ``H``, ``BPC``, ``CS``, ``F``), and ``data``
    holds the bytes between ``ID`` and ``EI``, still encoded with the
    dictionary's filters, without the white-space byte before ``EI``, which
    writing adds back.
    """

    dictionary: Mapping[str, Any]
    data: bytes
    _source: bytes | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Check the dictionary and copy it and the data."""
        if not isinstance(self.dictionary, Mapping):
            msg = f"dictionary must be a mapping: {self.dictionary!r}"
            raise TypeError(msg)
        if not isinstance(self.data, (bytes, bytearray, memoryview)):
            msg = f"data must be bytes: {type(self.data).__name__}"
            raise TypeError(msg)
        dictionary = dict(self.dictionary)
        _check_value(dictionary, 0)
        object.__setattr__(self, "dictionary", dictionary)
        object.__setattr__(self, "data", bytes(self.data))

    @property
    def operator(self) -> str:
        """Return ``"BI"``, so that every item of :class:`Operations` has an operator."""
        return "BI"

    @property
    def operands(self) -> tuple[Any, ...]:
        """Return ``()``: an inline image holds its dictionary and data instead."""
        return ()


class Operations(Sequence["Operation | InlineImage"]):
    """An immutable sequence of content-stream operations.

    ``Operations.parse(data)`` reads a content stream as hayro tokenizes it,
    and ``to_bytes()`` writes it back: operations that the parse produced
    write their original bytes, comments and white space included, so
    ``Operations.parse(data).to_bytes() == data``, and new operations are
    written from their values. Bytes after the last operation that hayro
    reads, such as content after an operation that it cannot read, stay in
    ``tail`` and are written last.
    """

    __slots__ = ("_items", "_tail")

    def __init__(self, items: Iterable[Operation | InlineImage] = (), *, tail: bytes = b"") -> None:
        """Collect the operations; each must be an :class:`Operation` or :class:`InlineImage`."""
        collected = tuple(items)
        for item in collected:
            if not isinstance(item, (Operation, InlineImage)):
                msg = f"an Operations item is an Operation or InlineImage: {item!r}"
                raise TypeError(msg)
        if not isinstance(tail, bytes):
            msg = f"tail must be bytes: {type(tail).__name__}"
            raise TypeError(msg)
        self._items = collected
        self._tail = tail

    @classmethod
    def parse(
        cls,
        data: bytes,
        /,
        *,
        max_operations: int | None = _DEFAULT_MAX_OPERATIONS,
        max_size: int | None = _DEFAULT_MAX_CONTENT_SIZE,
    ) -> Operations:
        """Read a decoded content stream, such as a Form XObject's ``Stream.read()``.

        ``max_size`` bounds the input in bytes (``content_input_size``) and
        ``max_operations`` the operations returned
        (``content_operation_count``); ``None`` lifts either for trusted
        input.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            msg = f"data must be bytes: {type(data).__name__}"
            raise TypeError(msg)
        _validate_optional_positive_int("max_operations", max_operations)
        _validate_optional_positive_int("max_size", max_size)
        data = bytes(data)
        if max_size is not None and len(data) > max_size:
            msg = f"content of {len(data)} bytes exceeds the {max_size}-byte limit"
            code = "content_input_size"
            raise LimitError(code, msg)
        return _from_core(_parse_operations(data, max_operations))

    @property
    def tail(self) -> bytes:
        """Return the bytes after the last operation."""
        return self._tail

    def to_bytes(self) -> bytes:
        """Write the operations as content-stream bytes."""
        out = bytearray()
        previous_image = False
        for item in self._items:
            chunk = item._source  # noqa: SLF001 - the module's own records.
            if chunk is None:
                chunk = _write_image(item) if isinstance(item, InlineImage) else _write_operation(item)
            _append(out, chunk, after_image=previous_image)
            previous_image = isinstance(item, InlineImage)
        if self._tail:
            _append(out, self._tail, after_image=previous_image)
        return bytes(out)

    @overload
    def __getitem__(self, index: int) -> Operation | InlineImage: ...
    @overload
    def __getitem__(self, index: slice) -> Operations: ...
    def __getitem__(self, index: int | slice) -> Operation | InlineImage | Operations:
        """Return one operation, or the operations of a slice without the tail."""
        if isinstance(index, slice):
            return Operations(self._items[index])
        return self._items[index]

    def __len__(self) -> int:
        """Return the number of operations."""
        return len(self._items)

    def __iter__(self) -> Iterator[Operation | InlineImage]:
        """Iterate over the operations."""
        return iter(self._items)

    def __eq__(self, other: object) -> bool:
        """Compare the operations and the tail."""
        if not isinstance(other, Operations):
            return NotImplemented
        return self._items == other._items and self._tail == other._tail

    def __hash__(self) -> int:
        """Hash the operations and the tail."""
        return hash((self._items, self._tail))

    def __repr__(self) -> str:
        """Return ``Operations(<count> operations)``."""
        return f"Operations({len(self._items)} operations)"


def _from_core(raw: tuple[list[Any], bytes]) -> Operations:
    """Build Operations from the core's tagged operations."""
    entries, tail = raw
    items: list[Operation | InlineImage] = []
    for entry in entries:
        if entry[0] == "i":
            item: Operation | InlineImage = InlineImage(_value(entry[1]), entry[2])
        else:
            item = Operation(_name_text(entry[1]), tuple(_value(value) for value in entry[2]))
        object.__setattr__(item, "_source", entry[3])
        items.append(item)
    return Operations(items, tail=tail)


def _value(value: Any) -> Any:  # noqa: ANN401 - operands are any object-model value.
    """Convert one tagged operand that the core read."""
    if not isinstance(value, tuple):
        return value
    tag = value[0]
    if tag == "n":
        return str.__new__(Name, _name_text(value[1]))
    if tag == "s":
        return String(value[1])
    if tag == "a":
        return tuple(_value(item) for item in value[1])
    return {_name_text(key): _value(item) for key, item in value[1]}


def _check_value(value: Any, depth: int) -> None:  # noqa: ANN401 - any operand.
    """Refuse a value that cannot be written as a content operand."""
    if value is None or isinstance(value, (bool, int, str, bytes, bytearray, memoryview)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = f"a PDF number is finite: {value!r}"
            raise ValueError(msg)
        return
    if depth >= _MAX_OPERAND_DEPTH:
        msg = f"an operand nests arrays and dictionaries deeper than {_MAX_OPERAND_DEPTH} levels"
        code = "object_value_depth"
        raise LimitError(code, msg)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                msg = f"a dictionary key is non-empty name text: {key!r}"
                raise TypeError(msg)
            _check_value(item, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _check_value(item, depth + 1)
        return
    msg = f"cannot write a {type(value).__name__} as a content operand: {value!r}"
    raise TypeError(msg)


def _append(out: bytearray, chunk: bytes, *, after_image: bool) -> None:
    """Append a chunk, separating it where the previous token would run on."""
    if out and chunk:
        last, first = out[-1], chunk[0]
        if (after_image and first not in _WHITESPACE) or (
            last not in _WHITESPACE
            and last not in _DELIMITERS
            and first not in _WHITESPACE
            and first not in _DELIMITERS
        ):
            out += b"\n"
    out += chunk


def _write_operation(operation: Operation) -> bytes:
    out = bytearray()
    for operand in operation.operands:
        _write(out, operand)
        out += b" "
    out += _name_bytes(operation.operator)
    return bytes(out)


def _write_image(image: InlineImage) -> bytes:
    out = bytearray(b"BI")
    for key, value in image.dictionary.items():
        out += b" "
        _write(out, Name(key) if not isinstance(key, Name) else key)
        out += b" "
        _write(out, value)
    out += b" ID "
    out += image.data
    out += b"\nEI"
    return bytes(out)


def _write(out: bytearray, value: Any) -> None:  # noqa: ANN401, C901, PLR0912 - any operand; one branch per value kind.
    if value is None:
        out += b"null"
    elif value is True:
        out += b"true"
    elif value is False:
        out += b"false"
    elif isinstance(value, int):
        out += str(value).encode()
    elif isinstance(value, float):
        out += _real(value)
    elif isinstance(value, Name):
        out += b"/"
        for byte in _name_bytes(value):
            if byte in _REGULAR_OPERATOR_BYTES and byte != ord("#"):
                out.append(byte)
            else:
                out += b"#%02X" % byte
    elif isinstance(value, (str, bytes, bytearray, memoryview)):
        data = _encode_text_string(value) if isinstance(value, str) else bytes(value)
        out += b"("
        for byte in data:
            if byte in b"()\\":
                out += b"\\" + bytes((byte,))
            elif 0x20 <= byte < 0x7F:  # noqa: PLR2004 - printable ASCII.
                out.append(byte)
            else:
                out += b"\\%03o" % byte
        out += b")"
    elif isinstance(value, Mapping):
        out += b"<<"
        for key, item in value.items():
            _write(out, Name(key) if not isinstance(key, Name) else key)
            out += b" "
            _write(out, item)
            out += b" "
        out += b">>"
    else:
        out += b"["
        for index, item in enumerate(value):
            if index:
                out += b" "
            _write(out, item)
        out += b"]"


def _real(value: float) -> bytes:
    """Write a real without an exponent, which PDF numbers cannot hold."""
    text = repr(value)
    if "e" in text or "E" in text:
        text = format(Decimal(text), "f")
    text = text.removesuffix(".0")
    if text == "-0":
        text = "0"
    return text.encode()


for _public in (Operation, InlineImage, Operations):
    _public.__module__ = "pylopdf"
