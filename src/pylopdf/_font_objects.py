"""Font and Form XObject objects: ``Document.fonts``, ``Page.resources``, and their handles."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from pylopdf._geometry import Matrix, Rect
from pylopdf._limits import _validate_optional_positive_int
from pylopdf._objects import _object_number
from pylopdf.pylopdf_core import StaleObjectError

if TYPE_CHECKING:
    from pylopdf._document import Document
    from pylopdf._objects import Dictionary, Stream

#: The core's facts of one font: base font name, subtype, encoding name,
#: whether its glyphs are in the PDF, and the format of its program.
_Facts = tuple[str, str, str | None, bool, str | None]

FontFormat = Literal["type1", "truetype", "cff", "opentype"]
"""The format of an embedded font program: ``"type1"`` for ``/FontFile``,
``"truetype"`` for ``/FontFile2``, ``"cff"`` for a ``/FontFile3`` of subtype
``Type1C`` or ``CIDFontType0C``, and ``"opentype"`` for one of subtype
``OpenType``."""

_DEFAULT_MAX_FONT_PROGRAM_SIZE = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class FontProgram:
    """The embedded program of a font, as :meth:`FontObject.extract` returns it."""

    format: FontFormat
    data: bytes
    """The decoded program: a Type 1 font of the three segments of
    ``/Length1`` to ``/Length3``, a TrueType font, a bare CFF table, or an
    OpenType font."""


class FontObject:
    """A handle on one font dictionary of a document, as ``doc.fonts[xref]`` returns it.

    Properties read the document on every access. A handle raises
    :class:`StaleObjectError` once its object is no longer a font or the
    document's objects were renumbered.
    """

    __slots__ = ("_document", "_epoch", "_xref")

    def __init__(self, document: Document, xref: int) -> None:
        """Create a handle; :class:`Fonts` and ``Page.resources`` do this."""
        self._document = document
        self._epoch = document._object_epoch
        self._xref = xref

    def _facts(self) -> _Facts:
        """Return the core's current facts of this font."""
        document = self._document
        document._ensure_open()
        if self._epoch != document._object_epoch:
            msg = f"font {self._xref} was invalidated when the document's objects were renumbered or reloaded"
            raise StaleObjectError(msg)
        facts = document._doc.font_facts(self._xref)
        if facts is None:
            msg = f"object {self._xref} is no longer a font"
            raise StaleObjectError(msg)
        return facts

    @property
    def xref(self) -> int:
        """Return the font dictionary's object number."""
        return self._xref

    @property
    def obj(self) -> Dictionary:
        """Return the font dictionary in the low-level object model."""
        self._facts()
        return cast("Dictionary", self._document.objects[self._xref])

    @property
    def name(self) -> str:
        """Return ``/BaseFont`` with its subset prefix, such as ``"ABCDEF+Arial"``.

        A Type 3 font has none and gives its ``/Name`` or ``""``.
        """
        return self._facts()[0]

    @property
    def subtype(self) -> str:
        """Return ``/Subtype``: ``"Type1"``, ``"MMType1"``, ``"TrueType"``, ``"Type0"``, or ``"Type3"``."""
        return self._facts()[1]

    @property
    def encoding(self) -> str | None:
        """Return the encoding's name, such as ``"WinAnsiEncoding"`` or ``"Identity-H"``.

        An encoding dictionary gives its ``/BaseEncoding`` and an embedded
        CMap its ``/CMapName``; ``None`` when the font names none.
        """
        return self._facts()[2]

    @property
    def is_embedded(self) -> bool:
        """Return whether the PDF holds the font's glyphs: an embedded program, or Type 3 procedures."""
        return self._facts()[3]

    @property
    def widths(self) -> tuple[float, ...]:
        """Return the advance of every character code in em, indexed by code.

        A simple or Type 3 font has 256 codes and a Type 0 font 65,536
        two-byte codes, which its CMap maps to CIDs. The advances are those
        that text extraction and rendering move by: ``/Widths`` and
        ``/MissingWidth``, the AFM widths of a standard font that is not
        embedded, ``/W`` and ``/DW`` of a CIDFont, and Type 3 widths scaled
        by ``/FontMatrix``; a font that neither reads takes Helvetica's.
        """
        self._facts()
        return tuple(self._document._doc.font_widths(self._xref))

    def extract(self, *, max_size: int | None = _DEFAULT_MAX_FONT_PROGRAM_SIZE) -> FontProgram | None:
        """Return the font's embedded program, decoded, or ``None`` when the PDF holds none.

        Decoding stops at ``max_size`` bytes, 64 MiB by default, with
        :class:`LimitError` code ``font_program_size``; ``None`` lifts the
        limit for trusted input.
        """
        _validate_optional_positive_int("max_size", max_size)
        self._facts()
        program = self._document._doc.font_program(self._xref, max_size)
        if program is None:
            return None
        program_format, data = program
        return FontProgram(format=cast("FontFormat", program_format), data=data)

    def __eq__(self, other: object) -> bool:
        """Compare the document, the object numbering, and the object number."""
        if not isinstance(other, FontObject):
            return NotImplemented
        return self._document is other._document and self._epoch == other._epoch and self._xref == other._xref

    def __hash__(self) -> int:
        """Hash the document, the object numbering, and the object number."""
        return hash((id(self._document), self._epoch, self._xref))

    def __repr__(self) -> str:
        """Return ``<FontObject 12>``."""
        return f"<FontObject {self._xref}>"


class Fonts(Mapping[int, FontObject]):
    """The font dictionaries of a document, keyed by object number, as ``doc.fonts`` returns them.

    Only fonts that ``/Font`` resources can select are listed, so a Type 0
    font's descendant CIDFont is not.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.fonts``."""
        self._document = document

    def __getitem__(self, xref: int) -> FontObject:
        """Return the font stored under ``xref``; another object raises :class:`KeyError`."""
        number = _object_number(xref)
        self._document._ensure_open()
        if self._document._doc.font_facts(number) is None:
            raise KeyError(xref)
        return FontObject(self._document, number)

    def __iter__(self) -> Iterator[int]:
        """Iterate over the object numbers of the fonts in ascending order."""
        self._document._ensure_open()
        return iter(self._document._doc.font_xrefs())

    def __len__(self) -> int:
        """Return the number of font dictionaries."""
        self._document._ensure_open()
        return len(self._document._doc.font_xrefs())

    def __contains__(self, xref: object) -> bool:
        """Return whether ``xref`` is a font dictionary."""
        if not isinstance(xref, int) or isinstance(xref, bool) or xref < 0:
            return False
        self._document._ensure_open()
        return self._document._doc.font_facts(xref) is not None


class FormObject:
    """A handle on one Form XObject, as ``page.resources.forms`` lists them.

    Properties read the document on every access, and a handle raises
    :class:`StaleObjectError` once its object is no longer a Form XObject or
    the document's objects were renumbered.
    """

    __slots__ = ("_document", "_epoch", "_xref")

    def __init__(self, document: Document, xref: int) -> None:
        """Create a handle; ``Page.resources`` does this."""
        self._document = document
        self._epoch = document._object_epoch
        self._xref = xref

    def _facts(self) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float, float, float]]:
        document = self._document
        document._ensure_open()
        if self._epoch != document._object_epoch:
            msg = f"Form XObject {self._xref} was invalidated when the document's objects were renumbered or reloaded"
            raise StaleObjectError(msg)
        facts = document._doc.form_facts(self._xref)
        if facts is None:
            msg = f"object {self._xref} is no longer a Form XObject"
            raise StaleObjectError(msg)
        return facts

    @property
    def xref(self) -> int:
        """Return the Form XObject's object number."""
        return self._xref

    @property
    def obj(self) -> Stream:
        """Return the Form XObject's stream in the low-level object model."""
        self._facts()
        return cast("Stream", self._document.objects[self._xref])

    @property
    def bbox(self) -> Rect:
        """Return ``/BBox`` in the form's own space, as stored; a missing one reads as zeros."""
        x0, y0, x1, y1 = self._facts()[0]
        return Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    @property
    def matrix(self) -> Matrix:
        """Return ``/Matrix``, the map from form space to the user space it is drawn in."""
        return Matrix(*self._facts()[1])

    def __eq__(self, other: object) -> bool:
        """Compare the document, the object numbering, and the object number."""
        if not isinstance(other, FormObject):
            return NotImplemented
        return self._document is other._document and self._epoch == other._epoch and self._xref == other._xref

    def __hash__(self) -> int:
        """Hash the document, the object numbering, and the object number."""
        return hash((id(self._document), self._epoch, self._xref))

    def __repr__(self) -> str:
        """Return ``<FormObject 12>``."""
        return f"<FormObject {self._xref}>"


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (FontProgram, FontObject, Fonts, FormObject):
    _public.__module__ = "pylopdf"
