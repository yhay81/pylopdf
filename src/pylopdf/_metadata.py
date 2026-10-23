"""Document properties: the Info dictionary, page mode and layout, MarkInfo, and the PDF/A claim."""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pylopdf._document import Document

#: The Info dictionary entries that :attr:`Document.metadata` holds, by their
#: names in pylopdf, and their PDF keys.
METADATA_KEYS: dict[str, str] = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creator": "Creator",
    "producer": "Producer",
    "created": "CreationDate",
    "modified": "ModDate",
    "trapped": "Trapped",
}
#: The values that ``trapped`` takes (ISO 32000-2 Table 349).
TRAPPED_VALUES = ("True", "False", "Unknown")


def _metadata_value(key: str, value: object) -> str:
    """Validate one value for ``key``: a non-empty string, and for ``trapped`` one of its names."""
    if key not in METADATA_KEYS:
        msg = f"unknown metadata key {key!r} (valid: {list(METADATA_KEYS)})"
        raise KeyError(msg)
    if not isinstance(value, str):
        msg = f"metadata values are strings: {key}={value!r}"
        raise TypeError(msg)
    if not value:
        msg = f"metadata values are not empty; delete {key!r} to remove it"
        raise ValueError(msg)
    if key == "trapped" and value not in TRAPPED_VALUES:
        msg = f"trapped is 'True', 'False', or 'Unknown': {value!r}"
        raise ValueError(msg)
    return value


class Metadata(MutableMapping[str, str]):
    """The document's Info dictionary as a live mapping, as ``doc.metadata`` returns it.

    The keys are ``title``, ``author``, ``subject``, ``keywords``,
    ``creator``, ``producer``, ``created``, ``modified``, and ``trapped``;
    only the entries present are listed. Values are strings as stored, the
    dates as PDF date strings such as ``"D:20240101120000Z"``. Each
    assignment or deletion edits the document at once, and :meth:`update`
    applies all of its values in one edit. Reads and writes share a 1 MiB
    text budget.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.metadata``."""
        self._document = document

    def _entries(self) -> dict[str, str]:
        document = self._document
        document._ensure_open()
        raw = document._doc.get_metadata()
        return {name: raw[key] for name, key in METADATA_KEYS.items() if raw.get(key)}

    def __getitem__(self, key: str) -> str:
        """Return the entry ``key``; an absent or unknown key raises :class:`KeyError`."""
        return self._entries()[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate over the keys present, in the order of :data:`METADATA_KEYS`."""
        return iter(self._entries())

    def __len__(self) -> int:
        """Return the number of entries present."""
        return len(self._entries())

    def __setitem__(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        self.update({key: value})

    def __delitem__(self, key: str) -> None:
        """Remove ``key``; an absent key raises :class:`KeyError`."""
        if key not in self._entries():
            raise KeyError(key)
        self._write({key: ""})

    def update(self, other: object = (), /, **values: str) -> None:
        """Store every value of ``other`` and ``values`` in one edit, or none of them.

        ``other`` is a mapping or an iterable of key and value pairs, as for
        :meth:`dict.update`.
        """
        merged = dict(cast("Mapping[str, str] | Iterable[tuple[str, str]]", other))
        merged.update(values)
        self._write({key: _metadata_value(key, value) for key, value in merged.items()})

    def clear(self) -> None:
        """Remove the Info dictionary."""
        self._document._ensure_open()
        self._document._doc.remove_metadata()

    def _write(self, values: Mapping[str, str]) -> None:
        """Apply validated values, where ``""`` removes an entry."""
        from pylopdf._document import _write_metadata  # noqa: PLC0415 - the module imports this one.

        _write_metadata(self._document, values)

    def __repr__(self) -> str:
        """Return ``Metadata({...})`` with the entries present."""
        return f"Metadata({self._entries()!r})"


class PageMode(enum.StrEnum):
    """How a viewer opens the document (``/PageMode``, ISO 32000-2 Table 29)."""

    USE_NONE = "UseNone"
    USE_OUTLINES = "UseOutlines"
    USE_THUMBS = "UseThumbs"
    FULL_SCREEN = "FullScreen"
    USE_OC = "UseOC"
    USE_ATTACHMENTS = "UseAttachments"


class PageLayout(enum.StrEnum):
    """How a viewer lays pages out (``/PageLayout``, ISO 32000-2 Table 29)."""

    SINGLE_PAGE = "SinglePage"
    ONE_COLUMN = "OneColumn"
    TWO_COLUMN_LEFT = "TwoColumnLeft"
    TWO_COLUMN_RIGHT = "TwoColumnRight"
    TWO_PAGE_LEFT = "TwoPageLeft"
    TWO_PAGE_RIGHT = "TwoPageRight"


@dataclass(frozen=True, slots=True, kw_only=True)
class MarkInfo:
    """The catalog's ``/MarkInfo`` flags of a tagged document (ISO 32000-2 Table 321)."""

    marked: bool = False
    """Whether the document conforms to the tagged PDF conventions."""
    user_properties: bool = False
    """Whether structure elements carry user properties."""
    suspects: bool = False
    """Whether the tags may be unreliable."""

    def __post_init__(self) -> None:
        """Refuse a flag that is not a bool."""
        for name in ("marked", "user_properties", "suspects"):
            if not isinstance(getattr(self, name), bool):
                msg = f"{name} must be a bool: {getattr(self, name)!r}"
                raise TypeError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class PdfaClaim:
    """The PDF/A part and conformance that a document's XMP metadata claims."""

    part: int
    """The part of ISO 19005, such as 2 for PDF/A-2."""
    conformance: str
    """The conformance level, such as ``"B"``; empty for PDF/A-4 without one."""


#: A PDF date string (ISO 32000-2 §7.9.4): ``D:YYYYMMDDHHmmSSOHH'mm``, every
#: part after the year optional, and the apostrophes optional as PDF 2.0
#: allows.
_PDF_DATE = re.compile(
    r"(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:([Zz+-])(?:(\d{2})'?(?:(\d{2})'?)?)?)?",
    re.ASCII,
)
_MAX_PDF_DATE_SIZE = 64


def pdf_date(value: datetime | None = None) -> str:
    """Return ``value``, or the current local time, as a PDF date string.

    The string is ``D:YYYYMMDDHHmmSS`` followed by ``Z`` for UTC, by the
    offset as ``+HH'mm'`` for another aware time, and by nothing for a naive
    one; fractions of a second are left out. :class:`Metadata` and the date
    fields of annotations and attachments take such strings.
    """
    if value is None:
        value = datetime.now().astimezone()
    elif not isinstance(value, datetime):
        msg = f"pdf_date takes a datetime or None: {value!r}"
        raise TypeError(msg)
    text = f"D:{value.year:04d}{value:%m%d%H%M%S}"
    offset = value.utcoffset()
    if offset is None:
        return text
    if not offset:
        return text + "Z"
    minutes = int(offset.total_seconds()) // 60
    sign = "+" if minutes > 0 else "-"
    hours, minutes = divmod(abs(minutes), 60)
    return f"{text}{sign}{hours:02d}'{minutes:02d}'"


def parse_pdf_date(text: str) -> datetime:
    """Parse a PDF date string into a :class:`~datetime.datetime`.

    The ``D:`` prefix, every part after the year, and the apostrophes of
    the offset are optional, as ISO 32000-2 §7.9.4 allows; missing parts take
    their earliest value. The result is aware when the string gives ``Z``
    or an offset, and naive otherwise. A string that is no PDF date raises
    :class:`ValueError`.
    """
    if not isinstance(text, str):
        msg = f"parse_pdf_date takes a str: {text!r}"
        raise TypeError(msg)
    stripped = text.strip()
    match = _PDF_DATE.fullmatch(stripped) if len(stripped) <= _MAX_PDF_DATE_SIZE else None
    if match is None:
        msg = f"not a PDF date string: {text!r}"
        raise ValueError(msg)
    year, month, day, hour, minute, second, sign, offset_hours, offset_minutes = match.groups()
    zone: timezone | None = None
    if sign is not None:
        if sign in "Zz":
            zone = UTC
        else:
            hours, minutes = int(offset_hours or 0), int(offset_minutes or 0)
            if hours > 23 or minutes > 59:  # noqa: PLR2004 - the bounds of an offset.
                msg = f"not a PDF date string: {text!r}"
                raise ValueError(msg)
            delta = timedelta(hours=hours, minutes=minutes)
            zone = timezone(-delta if sign == "-" else delta)
    try:
        return datetime(
            int(year),
            int(month or 1),
            int(day or 1),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=zone,
        )
    except ValueError as exc:
        msg = f"not a PDF date string: {text!r}"
        raise ValueError(msg) from exc


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Metadata, PageMode, PageLayout, MarkInfo, PdfaClaim):
    _public.__module__ = "pylopdf"
