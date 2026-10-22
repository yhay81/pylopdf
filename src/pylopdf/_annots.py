"""Read-only annotation handles and pymupdf's annotation constants."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any

from pylopdf import _optional_content
from pylopdf._geometry import Rect
from pylopdf._hints import hinted_getattr
from pylopdf._objects import Dictionary
from pylopdf.pylopdf_core import StaleObjectError

if TYPE_CHECKING:
    from pylopdf._page import Page


class AnnotType(enum.IntEnum):
    """Annotation types; the members are pymupdf's ``PDF_ANNOT_*`` values."""

    UNKNOWN = -1
    TEXT = 0
    LINK = 1
    FREE_TEXT = 2
    LINE = 3
    SQUARE = 4
    CIRCLE = 5
    POLYGON = 6
    POLY_LINE = 7
    HIGHLIGHT = 8
    UNDERLINE = 9
    SQUIGGLY = 10
    STRIKE_OUT = 11
    REDACT = 12
    STAMP = 13
    CARET = 14
    INK = 15
    POPUP = 16
    FILE_ATTACHMENT = 17
    SOUND = 18
    MOVIE = 19
    RICH_MEDIA = 20
    WIDGET = 21
    SCREEN = 22
    PRINTER_MARK = 23
    TRAP_NET = 24
    WATERMARK = 25
    THREE_D = 26
    PROJECTION = 27


PDF_ANNOT_UNKNOWN = AnnotType.UNKNOWN
PDF_ANNOT_TEXT = AnnotType.TEXT
PDF_ANNOT_LINK = AnnotType.LINK
PDF_ANNOT_FREE_TEXT = AnnotType.FREE_TEXT
PDF_ANNOT_LINE = AnnotType.LINE
PDF_ANNOT_SQUARE = AnnotType.SQUARE
PDF_ANNOT_CIRCLE = AnnotType.CIRCLE
PDF_ANNOT_POLYGON = AnnotType.POLYGON
PDF_ANNOT_POLY_LINE = AnnotType.POLY_LINE
PDF_ANNOT_HIGHLIGHT = AnnotType.HIGHLIGHT
PDF_ANNOT_UNDERLINE = AnnotType.UNDERLINE
PDF_ANNOT_SQUIGGLY = AnnotType.SQUIGGLY
PDF_ANNOT_STRIKE_OUT = AnnotType.STRIKE_OUT
PDF_ANNOT_REDACT = AnnotType.REDACT
PDF_ANNOT_STAMP = AnnotType.STAMP
PDF_ANNOT_CARET = AnnotType.CARET
PDF_ANNOT_INK = AnnotType.INK
PDF_ANNOT_POPUP = AnnotType.POPUP
PDF_ANNOT_FILE_ATTACHMENT = AnnotType.FILE_ATTACHMENT
PDF_ANNOT_SOUND = AnnotType.SOUND
PDF_ANNOT_MOVIE = AnnotType.MOVIE
PDF_ANNOT_RICH_MEDIA = AnnotType.RICH_MEDIA
PDF_ANNOT_WIDGET = AnnotType.WIDGET
PDF_ANNOT_SCREEN = AnnotType.SCREEN
PDF_ANNOT_PRINTER_MARK = AnnotType.PRINTER_MARK
PDF_ANNOT_TRAP_NET = AnnotType.TRAP_NET
PDF_ANNOT_WATERMARK = AnnotType.WATERMARK
PDF_ANNOT_3D = AnnotType.THREE_D
PDF_ANNOT_PROJECTION = AnnotType.PROJECTION

#: The PDF subtype name of each annotation type.
_SUBTYPE_TYPES: dict[str, AnnotType] = {
    "Text": AnnotType.TEXT,
    "Link": AnnotType.LINK,
    "FreeText": AnnotType.FREE_TEXT,
    "Line": AnnotType.LINE,
    "Square": AnnotType.SQUARE,
    "Circle": AnnotType.CIRCLE,
    "Polygon": AnnotType.POLYGON,
    "PolyLine": AnnotType.POLY_LINE,
    "Highlight": AnnotType.HIGHLIGHT,
    "Underline": AnnotType.UNDERLINE,
    "Squiggly": AnnotType.SQUIGGLY,
    "StrikeOut": AnnotType.STRIKE_OUT,
    "Redact": AnnotType.REDACT,
    "Stamp": AnnotType.STAMP,
    "Caret": AnnotType.CARET,
    "Ink": AnnotType.INK,
    "Popup": AnnotType.POPUP,
    "FileAttachment": AnnotType.FILE_ATTACHMENT,
    "Sound": AnnotType.SOUND,
    "Movie": AnnotType.MOVIE,
    "RichMedia": AnnotType.RICH_MEDIA,
    "Widget": AnnotType.WIDGET,
    "Screen": AnnotType.SCREEN,
    "PrinterMark": AnnotType.PRINTER_MARK,
    "TrapNet": AnnotType.TRAP_NET,
    "Watermark": AnnotType.WATERMARK,
    "3D": AnnotType.THREE_D,
    "Projection": AnnotType.PROJECTION,
}


class AnnotFlags(enum.IntFlag):
    """Annotation ``/F`` flags; the members are pymupdf's ``PDF_ANNOT_IS_*`` values."""

    INVISIBLE = 1 << 0
    HIDDEN = 1 << 1
    PRINT = 1 << 2
    NO_ZOOM = 1 << 3
    NO_ROTATE = 1 << 4
    NO_VIEW = 1 << 5
    READ_ONLY = 1 << 6
    LOCKED = 1 << 7
    TOGGLE_NO_VIEW = 1 << 8
    LOCKED_CONTENTS = 1 << 9


PDF_ANNOT_IS_INVISIBLE = AnnotFlags.INVISIBLE
PDF_ANNOT_IS_HIDDEN = AnnotFlags.HIDDEN
PDF_ANNOT_IS_PRINT = AnnotFlags.PRINT
PDF_ANNOT_IS_NO_ZOOM = AnnotFlags.NO_ZOOM
PDF_ANNOT_IS_NO_ROTATE = AnnotFlags.NO_ROTATE
PDF_ANNOT_IS_NO_VIEW = AnnotFlags.NO_VIEW
PDF_ANNOT_IS_READ_ONLY = AnnotFlags.READ_ONLY
PDF_ANNOT_IS_LOCKED = AnnotFlags.LOCKED
PDF_ANNOT_IS_TOGGLE_NO_VIEW = AnnotFlags.TOGGLE_NO_VIEW
PDF_ANNOT_IS_LOCKED_CONTENTS = AnnotFlags.LOCKED_CONTENTS

#: The keys of :attr:`Annot.info`, in the order of the core's text entries.
_INFO_KEYS = ("content", "name", "title", "creationDate", "modDate", "subject", "id")


class Annot:
    """A read-only handle on one annotation of a page, as pymupdf's ``Annot``.

    Properties read the document on every access. A handle raises
    :class:`StaleObjectError` once its page changed structurally or its
    annotation is no longer in the page's ``/Annots``.
    """

    __slots__ = ("_index", "_page", "_xref")

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, page: Page, xref: int, index: int) -> None:
        """Create a handle; :meth:`Page.annots` and the ``add_*`` methods do this."""
        self._page = page
        self._xref = xref
        self._index = index

    def _entry(self) -> tuple[Any, ...]:
        """Return the core's current record of this annotation."""
        page_number = self._page._page_number()
        for entry in self._page._document._doc.read_annotations(page_number):
            xref, index = entry[0], entry[1]
            if (self._xref and xref == self._xref) or (not self._xref and not xref and index == self._index):
                return entry
        msg = "the annotation is no longer on its page"
        raise StaleObjectError(msg)

    @property
    def parent(self) -> Page:
        """Return the page that holds the annotation."""
        return self._page

    @property
    def xref(self) -> int:
        """Return the annotation's object number, or 0 for a direct dictionary."""
        self._entry()
        return self._xref

    @property
    def type(self) -> tuple[AnnotType, str] | tuple[AnnotType, str, str]:
        """Return pymupdf's ``(type, subtype)``, plus the ``/IT`` intent when present."""
        entry = self._entry()
        subtype = entry[2]
        intent = entry[5][7]
        kind = _SUBTYPE_TYPES.get(subtype, AnnotType.UNKNOWN)
        return (kind, subtype) if intent is None else (kind, subtype, intent)

    @property
    def rect(self) -> Rect:
        """Return the annotation rectangle in display coordinates (DESIGN.md G1)."""
        return Rect(*self._entry()[3])

    @property
    def flags(self) -> AnnotFlags:
        """Return the ``/F`` flags."""
        return AnnotFlags(self._entry()[4] & 0x3FF)

    @property
    def info(self) -> dict[str, str]:
        """Return pymupdf's info dictionary: ``content``, ``name``, ``title``, ``creationDate``, ``modDate``, ``subject``, and ``id``."""  # noqa: E501
        texts = self._entry()[5]
        return {key: texts[index] or "" for index, key in enumerate(_INFO_KEYS)}

    @property
    def colors(self) -> dict[str, tuple[float, ...]]:
        """Return pymupdf's ``{"stroke": ..., "fill": ...}`` from ``/C`` and ``/IC``; absent colours are ``()``."""
        entry = self._entry()
        return {"stroke": tuple(entry[6]), "fill": tuple(entry[7])}

    def get_oc(self) -> int:
        """Return the object number of the group or membership dictionary that ``/OC`` refers to, or 0."""
        self._entry()
        page = self._page
        annotation = page._document.objects[self._xref] if self._xref else page.obj["Annots"][self._index]
        return _optional_content.oc_reference(annotation) if isinstance(annotation, Dictionary) else 0

    def __eq__(self, other: object) -> bool:
        """Compare handles by document, page, and annotation."""
        if not isinstance(other, Annot):
            return NotImplemented
        return (
            self._page._document is other._page._document
            and self._page._pno == other._page._pno
            and (self._xref, self._index if not self._xref else 0)
            == (other._xref, other._index if not other._xref else 0)
        )

    def __hash__(self) -> int:
        """Hash consistently with equality."""
        return hash((id(self._page._document), self._page._pno, self._xref, self._index if not self._xref else 0))

    def __repr__(self) -> str:
        """Return pymupdf's representation, such as ``'Highlight' annotation on page 0``."""
        try:
            subtype = self._entry()[2]
        except StaleObjectError:
            return "<stale annotation>"
        return f"'{subtype}' annotation on page {self._page._pno}"


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Annot, AnnotType, AnnotFlags):
    _public.__module__ = "pylopdf"
