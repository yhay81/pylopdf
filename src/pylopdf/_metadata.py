"""Document properties: the Info dictionary, page mode and layout, MarkInfo, and the PDF/A claim."""

from __future__ import annotations

import enum
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pylopdf._objects import Array, Name

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


class ReadingDirection(enum.StrEnum):
    """The reading order of the text, which lays out side-by-side pages (``/Direction``)."""

    L2R = "L2R"
    """Left to right."""
    R2L = "R2L"
    """Right to left, and vertical writing."""


class PageBoundary(enum.StrEnum):
    """A page box that viewers and printers show or clip to (``/ViewArea`` and its kin)."""

    MEDIA = "MediaBox"
    CROP = "CropBox"
    BLEED = "BleedBox"
    TRIM = "TrimBox"
    ART = "ArtBox"


class PrintScaling(enum.StrEnum):
    """Whether a print dialog scales pages by default (``/PrintScaling``)."""

    NONE = "None"
    """No scaling."""
    APP_DEFAULT = "AppDefault"
    """The viewer's own default."""


class Duplex(enum.StrEnum):
    """How a print dialog prints both sides by default (``/Duplex``)."""

    SIMPLEX = "Simplex"
    FLIP_SHORT_EDGE = "DuplexFlipShortEdge"
    FLIP_LONG_EDGE = "DuplexFlipLongEdge"


#: The most page ranges and enforced names that a ``ViewerPreferences`` holds.
_MAX_PRINT_PAGE_RANGES = 4_096
_MAX_ENFORCED = 64
_MAX_NAME_SIZE = 127
#: The page modes that ``/NonFullScreenPageMode`` takes (ISO 32000-2 Table 147).
_NON_FULL_SCREEN_MODES = frozenset({PageMode.USE_NONE, PageMode.USE_OUTLINES, PageMode.USE_THUMBS, PageMode.USE_OC})

_BOOLEAN_PREFERENCES = (
    ("hide_toolbar", "HideToolbar"),
    ("hide_menubar", "HideMenubar"),
    ("hide_window_ui", "HideWindowUI"),
    ("fit_window", "FitWindow"),
    ("center_window", "CenterWindow"),
    ("display_title", "DisplayDocTitle"),
    ("pick_tray_by_pdf_size", "PickTrayByPDFSize"),
)
_NAME_PREFERENCES: tuple[tuple[str, str, type[enum.StrEnum]], ...] = (
    ("non_full_screen_page_mode", "NonFullScreenPageMode", PageMode),
    ("direction", "Direction", ReadingDirection),
    ("view_area", "ViewArea", PageBoundary),
    ("view_clip", "ViewClip", PageBoundary),
    ("print_area", "PrintArea", PageBoundary),
    ("print_clip", "PrintClip", PageBoundary),
    ("print_scaling", "PrintScaling", PrintScaling),
    ("duplex", "Duplex", Duplex),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ViewerPreferences:
    """The catalog's ``/ViewerPreferences`` (ISO 32000-2 Table 147).

    Each field is ``None`` when the dictionary leaves the entry out, so the
    viewer uses its default; reading and assigning a record round-trips what
    pylopdf understands. Page ranges are zero-based and inclusive.
    """

    hide_toolbar: bool | None = None
    """Whether to hide the viewer's toolbars."""
    hide_menubar: bool | None = None
    """Whether to hide the viewer's menu bar."""
    hide_window_ui: bool | None = None
    """Whether to hide scroll bars and navigation controls."""
    fit_window: bool | None = None
    """Whether to resize the window to the first page."""
    center_window: bool | None = None
    """Whether to center the window on the screen."""
    display_title: bool | None = None
    """Whether the title bar shows the document's title rather than its file name."""
    non_full_screen_page_mode: PageMode | None = None
    """The page mode on leaving full-screen mode: ``USE_NONE``, ``USE_OUTLINES``, ``USE_THUMBS``, or ``USE_OC``."""
    direction: ReadingDirection | None = None
    """The reading order of the text."""
    view_area: PageBoundary | None = None
    """The box that a viewer shows (deprecated in PDF 2.0)."""
    view_clip: PageBoundary | None = None
    """The box that a viewer clips to (deprecated in PDF 2.0)."""
    print_area: PageBoundary | None = None
    """The box that printing shows (deprecated in PDF 2.0)."""
    print_clip: PageBoundary | None = None
    """The box that printing clips to (deprecated in PDF 2.0)."""
    print_scaling: PrintScaling | None = None
    """The print dialog's default scaling."""
    duplex: Duplex | None = None
    """The print dialog's default duplex mode."""
    pick_tray_by_pdf_size: bool | None = None
    """Whether the paper tray follows the page size."""
    print_page_ranges: tuple[tuple[int, int], ...] | None = None
    """The page ranges that the print dialog selects, as zero-based ``(first, last)`` pairs."""
    copies: int | None = None
    """The number of copies that the print dialog selects."""
    enforce: tuple[str, ...] | None = None
    """The preferences that a viewer must not let the user change, such as ``("PrintScaling",)``."""

    def __post_init__(self) -> None:
        """Validate every field and normalize enums and tuples."""
        for field, _ in _BOOLEAN_PREFERENCES:
            value = getattr(self, field)
            if value is not None and not isinstance(value, bool):
                msg = f"{field} must be a bool or None: {value!r}"
                raise TypeError(msg)
        for field, _, kind in _NAME_PREFERENCES:
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, kind(value))
        if self.non_full_screen_page_mode is not None and self.non_full_screen_page_mode not in _NON_FULL_SCREEN_MODES:
            msg = f"non_full_screen_page_mode cannot be {self.non_full_screen_page_mode!r}"
            raise ValueError(msg)
        if self.print_page_ranges is not None:
            object.__setattr__(self, "print_page_ranges", _page_ranges(self.print_page_ranges))
        copies = self.copies
        if copies is not None and (isinstance(copies, bool) or not isinstance(copies, int) or copies < 1):
            msg = f"copies must be a positive int or None: {copies!r}"
            raise ValueError(msg)
        if self.enforce is not None:
            object.__setattr__(self, "enforce", _enforced_names(self.enforce))


def _page_ranges(ranges: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Validate print page ranges: zero-based ``(first, last)`` pairs."""
    pairs = tuple(tuple(pair) for pair in ranges)
    if len(pairs) > _MAX_PRINT_PAGE_RANGES:
        msg = f"print_page_ranges holds at most {_MAX_PRINT_PAGE_RANGES} ranges"
        raise ValueError(msg)
    for pair in pairs:
        if (
            len(pair) != 2  # noqa: PLR2004 - a range is a pair.
            or not all(isinstance(page, int) and not isinstance(page, bool) for page in pair)
            or not 0 <= pair[0] <= pair[1]
        ):
            msg = f"a print page range is a pair of zero-based pages (first, last), first <= last: {pair!r}"
            raise ValueError(msg)
    return pairs


def _is_preference_name(name: object) -> bool:
    return isinstance(name, str) and 0 < len(name) <= _MAX_NAME_SIZE and name.isascii() and name.isidentifier()


def _enforced_names(names: Iterable[str]) -> tuple[str, ...]:
    """Validate the names of enforced preferences."""
    if isinstance(names, str):
        msg = "enforce takes a tuple of names, such as ('PrintScaling',)"
        raise TypeError(msg)
    enforced = tuple(names)
    if len(enforced) > _MAX_ENFORCED or not all(_is_preference_name(name) for name in enforced):
        msg = f"enforce takes up to {_MAX_ENFORCED} preference names, such as 'PrintScaling': {enforced!r}"
        raise ValueError(msg)
    return enforced


def _viewer_preferences(entries: Mapping[str, object]) -> ViewerPreferences:
    """Read a ``/ViewerPreferences`` dictionary, leaving out entries of the wrong type or value."""
    fields: dict[str, object] = {}
    for field, key in _BOOLEAN_PREFERENCES:
        value = entries.get(key)
        if isinstance(value, bool):
            fields[field] = value
    for field, key, kind in _NAME_PREFERENCES:
        value = entries.get(key)
        if isinstance(value, Name):
            try:
                fields[field] = kind(str(value))
            except ValueError:
                continue
    if fields.get("non_full_screen_page_mode") not in {None, *_NON_FULL_SCREEN_MODES}:
        del fields["non_full_screen_page_mode"]
    ranges = _read_page_ranges(entries.get("PrintPageRange"))
    if ranges is not None:
        fields["print_page_ranges"] = ranges
    copies = entries.get("NumCopies")
    if isinstance(copies, int) and not isinstance(copies, bool) and copies >= 1:
        fields["copies"] = copies
    enforce = _read_enforced(entries.get("Enforce"))
    if enforce is not None:
        fields["enforce"] = enforce
    return ViewerPreferences(**fields)  # ty: ignore[invalid-argument-type]


def _read_enforced(enforce: object) -> tuple[str, ...] | None:
    """Read ``/Enforce`` as names, or ``None`` when it is malformed."""
    if not isinstance(enforce, Array) or len(enforce) > _MAX_ENFORCED:
        return None
    names = tuple(str(name) for name in enforce if isinstance(name, Name))
    return names if all(_is_preference_name(name) for name in names) else None


def _read_page_ranges(ranges: object) -> tuple[tuple[int, int], ...] | None:
    """Read ``/PrintPageRange`` as zero-based pairs, or ``None`` when it is malformed."""
    if not isinstance(ranges, Array) or len(ranges) > 2 * _MAX_PRINT_PAGE_RANGES or len(ranges) % 2:
        return None
    pages = list(ranges)
    if not all(isinstance(page, int) and not isinstance(page, bool) for page in pages):
        return None
    pairs = tuple((first - 1, last - 1) for first, last in zip(pages[::2], pages[1::2], strict=True))
    return pairs if all(0 <= first <= last for first, last in pairs) else None


def _viewer_preference_entries(preferences: ViewerPreferences) -> list[tuple[str, object]]:
    """Return the ``/ViewerPreferences`` entries of a record for the core."""
    entries: list[tuple[str, object]] = []
    for field, key in _BOOLEAN_PREFERENCES:
        value = getattr(preferences, field)
        if value is not None:
            entries.append((key, value))
    for field, key, _ in _NAME_PREFERENCES:
        value = getattr(preferences, field)
        if value is not None:
            entries.append((key, str(value.value)))
    if preferences.print_page_ranges is not None:
        entries.append(("PrintPageRange", [(first + 1, last + 1) for first, last in preferences.print_page_ranges]))
    if preferences.copies is not None:
        entries.append(("NumCopies", preferences.copies))
    if preferences.enforce is not None:
        entries.append(("Enforce", list(preferences.enforce)))
    return entries


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (
    Metadata,
    PageMode,
    PageLayout,
    MarkInfo,
    PdfaClaim,
    ReadingDirection,
    PageBoundary,
    PrintScaling,
    Duplex,
    ViewerPreferences,
):
    _public.__module__ = "pylopdf"
