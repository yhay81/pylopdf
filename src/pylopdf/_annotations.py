"""Annotations: ``Page.annotations``, its ``Annotation`` handles, and annotation specifications."""

from __future__ import annotations

import enum
import math
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, overload

from pylopdf._content import Color
from pylopdf._errors import StalePageError
from pylopdf._geometry import Quad, Rect
from pylopdf._hints import hinted_getattr
from pylopdf._limits import _validate_cumulative_utf8_input, _validate_rect
from pylopdf.pylopdf_core import StaleObjectError

if TYPE_CHECKING:
    from pylopdf._objects import Dictionary
    from pylopdf._page import Page

    # The core's form of one annotation: index, xref, subtype, display rect,
    # flags, the texts of the ``_CONTENTS`` slots, the ``/C`` and ``/IC``
    # components, and the ``/CA`` opacity.
    from pylopdf.pylopdf_core import _Annotation as _AnnotationTuple

#: The texts that one annotation may write: its subtype and its contents.
_MAX_ANNOTATION_INPUT_TEXT_SIZE = 1024 * 1024
_MAX_HIGHLIGHT_RECTS = 4096
_RECT_COORDINATE_COUNT = 4


class AnnotationType(enum.IntEnum):
    """Annotation types by ``/Subtype``; the values are pymupdf's ``PDF_ANNOT_*`` codes."""

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


#: The PDF subtype name of each annotation type.
_SUBTYPE_TYPES: dict[str, AnnotationType] = {
    "Text": AnnotationType.TEXT,
    "Link": AnnotationType.LINK,
    "FreeText": AnnotationType.FREE_TEXT,
    "Line": AnnotationType.LINE,
    "Square": AnnotationType.SQUARE,
    "Circle": AnnotationType.CIRCLE,
    "Polygon": AnnotationType.POLYGON,
    "PolyLine": AnnotationType.POLY_LINE,
    "Highlight": AnnotationType.HIGHLIGHT,
    "Underline": AnnotationType.UNDERLINE,
    "Squiggly": AnnotationType.SQUIGGLY,
    "StrikeOut": AnnotationType.STRIKE_OUT,
    "Redact": AnnotationType.REDACT,
    "Stamp": AnnotationType.STAMP,
    "Caret": AnnotationType.CARET,
    "Ink": AnnotationType.INK,
    "Popup": AnnotationType.POPUP,
    "FileAttachment": AnnotationType.FILE_ATTACHMENT,
    "Sound": AnnotationType.SOUND,
    "Movie": AnnotationType.MOVIE,
    "RichMedia": AnnotationType.RICH_MEDIA,
    "Widget": AnnotationType.WIDGET,
    "Screen": AnnotationType.SCREEN,
    "PrinterMark": AnnotationType.PRINTER_MARK,
    "TrapNet": AnnotationType.TRAP_NET,
    "Watermark": AnnotationType.WATERMARK,
    "3D": AnnotationType.THREE_D,
    "Projection": AnnotationType.PROJECTION,
}


class AnnotationFlags(enum.IntFlag):
    """Annotation ``/F`` flags (ISO 32000-2 Table 167); the values are pymupdf's ``PDF_ANNOT_IS_*``."""

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


#: The positions of the text entries in the core's annotation tuple.
_CONTENTS, _ICON, _AUTHOR, _CREATED, _MODIFIED, _SUBJECT, _NAME, _INTENT = range(8)


def _color(components: Sequence[float]) -> Color | None:
    """Return a ``/C`` or ``/IC`` array as a color: gray, RGB, or CMYK, or ``None`` when empty."""
    values = [min(max(value, 0.0), 1.0) for value in components]
    if len(values) == 1:
        return Color(values[0], values[0], values[0])
    if len(values) == 3:  # noqa: PLR2004 - RGB components.
        return Color(*values)
    if len(values) == 4:  # noqa: PLR2004 - CMYK components, converted naively as MuPDF does.
        cyan, magenta, yellow, black = values
        return Color((1 - cyan) * (1 - black), (1 - magenta) * (1 - black), (1 - yellow) * (1 - black))
    return None


class Annotation:
    """A handle on one annotation of a page, as ``page.annotations`` lists it.

    Properties read the document on every access. A handle finds its
    annotation again after an edit moved it; it raises
    :class:`StaleObjectError` once the annotation left the page and
    :class:`StalePageError` once the page did.
    """

    __slots__ = ("_index", "_page", "_xref")

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, page: Page, xref: int, index: int) -> None:
        """Create a handle; ``page.annotations`` does this."""
        self._page = page
        self._xref = xref
        self._index = index

    def _read(self) -> _AnnotationTuple:
        page = self._page
        core = page._document._doc
        entry = core.read_annotation(page._page_number(), self._index, self._xref)
        self._index = entry[0]
        return entry

    def _text(self, slot: int) -> str | None:
        return self._read()[5][slot]

    @property
    def page(self) -> Page:
        """Return the page that holds the annotation."""
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
    def type(self) -> AnnotationType:
        """Return the annotation type, :attr:`AnnotationType.UNKNOWN` for a subtype pylopdf does not know."""
        subtype = self._read()[2]
        return AnnotationType.UNKNOWN if subtype is None else _SUBTYPE_TYPES.get(subtype, AnnotationType.UNKNOWN)

    @property
    def rect(self) -> Rect:
        """Return the annotation rectangle in display coordinates."""
        return Rect(*self._read()[3])

    @property
    def contents(self) -> str | None:
        """Return the text that the annotation shows or describes, ``/Contents``."""
        return self._text(_CONTENTS)

    @property
    def author(self) -> str | None:
        """Return the author, ``/T``."""
        return self._text(_AUTHOR)

    @property
    def subject(self) -> str | None:
        """Return the subject, ``/Subj``."""
        return self._text(_SUBJECT)

    @property
    def created(self) -> str | None:
        """Return the creation date as the PDF date string stored, ``/CreationDate``."""
        return self._text(_CREATED)

    @property
    def modified(self) -> str | None:
        """Return the modification date as the PDF date string stored, ``/M``."""
        return self._text(_MODIFIED)

    @property
    def name(self) -> str | None:
        """Return the annotation's unique name on its page, ``/NM``."""
        return self._text(_NAME)

    @property
    def icon(self) -> str | None:
        """Return the icon name of a note, stamp, or file attachment, ``/Name``."""
        return self._text(_ICON)

    @property
    def intent(self) -> str | None:
        """Return the intent, ``/IT``, such as ``"FreeTextCallout"``."""
        return self._text(_INTENT)

    @property
    def color(self) -> Color | None:
        """Return the color of the border, title bar, or text markup, ``/C``; CMYK converts to RGB."""
        return _color(self._read()[6])

    @property
    def interior_color(self) -> Color | None:
        """Return the fill color of a shape or line ending, ``/IC``; CMYK converts to RGB."""
        return _color(self._read()[7])

    @property
    def opacity(self) -> float:
        """Return the constant opacity, ``/CA``, 1.0 when absent."""
        opacity = self._read()[8]
        return 1.0 if opacity is None else opacity

    @property
    def flags(self) -> AnnotationFlags:
        """Return the ``/F`` flags."""
        return AnnotationFlags(self._read()[4] & 0x3FF)

    def __eq__(self, other: object) -> bool:
        """Compare the page and the annotation: its object number, or its index for a direct dictionary."""
        if not isinstance(other, Annotation):
            return NotImplemented
        return self._page == other._page and self._key() == other._key()

    def __hash__(self) -> int:
        """Hash consistently with equality."""
        return hash((self._page, self._key()))

    def _key(self) -> tuple[int, int]:
        return (self._xref, 0 if self._xref else self._index)

    def __repr__(self) -> str:
        """Return ``Annotation(type=..., rect=...)``, or a note for a stale handle."""
        try:
            entry = self._read()
        except (StaleObjectError, StalePageError):
            return "<stale Annotation>"
        kind = AnnotationType.UNKNOWN if entry[2] is None else _SUBTYPE_TYPES.get(entry[2], AnnotationType.UNKNOWN)
        return f"Annotation(type=AnnotationType.{kind.name}, rect={Rect(*entry[3])!r})"


def _highlight_rects(
    rects: Sequence[float] | Quad | Iterable[Sequence[float] | Quad],
) -> Iterator[Sequence[float]] | Sequence[float]:
    """Resolve one rectangle or quad, or an iterable of them, lazily; a quad gives its bounding rectangle."""
    if isinstance(rects, Quad):
        return rects.rect
    if isinstance(rects, (list, tuple)) and rects and isinstance(rects[0], (int, float)):
        return cast("Sequence[float]", rects)
    return (rect.rect if isinstance(rect, Quad) else cast("Sequence[float]", rect) for rect in rects)


def _validate_highlight_rects(rects: object) -> tuple[Rect, ...]:
    """Validate 1 to 4,096 rectangles, stopping an iterator at the first one too many."""
    if isinstance(rects, (str, bytes)) or not isinstance(rects, Iterable):
        msg = f"rects must be a rectangle, a quad, or a sequence of them: {rects!r}"
        raise TypeError(msg)
    resolved = _highlight_rects(cast("Sequence[float]", rects))
    if not isinstance(resolved, Iterator):
        return (Rect(*_validate_rect(resolved)),)
    validated: list[Rect] = []
    for rect in resolved:
        if len(validated) >= _MAX_HIGHLIGHT_RECTS:
            msg = f"rects cannot contain more than {_MAX_HIGHLIGHT_RECTS} rectangles"
            raise ValueError(msg)
        validated.append(Rect(*_validate_rect(rect)))
    if not validated:
        msg = "rects must contain at least one rectangle or quad"
        raise ValueError(msg)
    return tuple(validated)


def _opaque_color(value: object) -> Color:
    if not isinstance(value, Color):
        msg = f"color must be a Color: {value!r}"
        raise TypeError(msg)
    components = (value.red, value.green, value.blue)
    if value.alpha != 1 or not all(math.isfinite(c) and 0 <= c <= 1 for c in components):
        msg = f"an annotation color has components from 0 to 1 and no transparency: {value!r}"
        raise ValueError(msg)
    return value


@dataclass(frozen=True, slots=True, init=False)
class Highlight:
    """A highlight over text, as ``page.annotations.add`` takes it.

    ``rects`` is one rectangle or quad, or a sequence of them, in display
    coordinates, such as the result of ``page.search``; a :class:`Quad`
    contributes its bounding rectangle, and one annotation holds at most
    4,096. ``color`` and ``opacity`` style the highlight, and ``contents`` is
    the text of its note. The annotation is written with its appearance, so
    every viewer draws it alike.
    """

    rects: tuple[Rect, ...]
    color: Color
    opacity: float
    contents: str | None

    def __init__(
        self,
        rects: Sequence[float] | Quad | Iterable[Sequence[float] | Quad],
        *,
        color: Color = Color(1.0, 1.0, 0.0),  # noqa: B008 - Color is immutable.
        opacity: float = 0.4,
        contents: str | None = None,
    ) -> None:
        """Validate the specification; the contents are bounded before the rectangles are read."""
        if contents is not None and not isinstance(contents, str):
            msg = f"contents must be a str or None: {type(contents).__name__}"
            raise TypeError(msg)
        _validate_cumulative_utf8_input(
            ("Highlight", "" if contents is None else contents),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and contents input",
        )
        validated = _validate_highlight_rects(rects)
        if isinstance(opacity, bool) or not isinstance(opacity, (int, float)):
            msg = f"opacity must be a number: {opacity!r}"
            raise TypeError(msg)
        if not (math.isfinite(opacity) and 0.0 < opacity <= 1.0):
            msg = f"opacity must be greater than 0 and at most 1: {opacity!r}"
            raise ValueError(msg)
        object.__setattr__(self, "rects", validated)
        object.__setattr__(self, "color", _opaque_color(color))
        object.__setattr__(self, "opacity", float(opacity))
        object.__setattr__(self, "contents", contents)


class Annotations(Sequence[Annotation]):
    """The annotations of a page other than links and form widgets, as ``page.annotations`` returns them.

    The sequence lists them in ``/Annots`` order and reads the page on every
    access; popups belong to the annotation that names them and are not
    listed. ``add(specification)`` adds an annotation and ``del
    page.annotations[i]`` removes one, with its popup.

    Reads reject more than 4,096 annotations or 1 MiB of annotation text per
    page.
    """

    __slots__ = ("_page",)

    def __init__(self, page: Page) -> None:
        """Wrap a page; use ``Page.annotations``."""
        self._page = page

    def _items(self) -> list[_AnnotationTuple]:
        page = self._page
        return page._document._doc.read_annotations(page._page_number())

    def _handles(self) -> list[Annotation]:
        return [Annotation(self._page, entry[1], entry[0]) for entry in self._items()]

    @overload
    def __getitem__(self, index: int) -> Annotation: ...
    @overload
    def __getitem__(self, index: slice) -> list[Annotation]: ...
    def __getitem__(self, index: int | slice) -> Annotation | list[Annotation]:
        """Return one annotation, or a list of them for a slice."""
        return self._handles()[index]

    def __len__(self) -> int:
        """Return the number of annotations on the page."""
        return len(self._items())

    def __iter__(self) -> Iterator[Annotation]:
        """Iterate over the annotations that the page lists now."""
        return iter(self._handles())

    def __delitem__(self, index: int | slice) -> None:
        """Remove annotations and their popups from the page; the objects go at the next pruning save."""
        selected = self._handles()[index]
        annotations = selected if isinstance(selected, list) else [selected]
        page = self._page
        handles = [(annotation._index, annotation._xref) for annotation in annotations]
        page._document._doc.remove_annotations(page._page_number(), handles)

    def add(self, specification: Highlight) -> Annotation:
        """Add the annotation that ``specification`` describes and return it."""
        if not isinstance(specification, Highlight):
            msg = f"page.annotations.add takes a Highlight specification: {type(specification).__name__}"
            raise TypeError(msg)
        page = self._page
        xref = page._document._doc.add_highlight_annotation(
            page._page_number(),
            [(rect.x0, rect.y0, rect.x1, rect.y1) for rect in specification.rects],
            specification.color.rgb,
            specification.opacity,
            specification.contents,
        )
        # The handle finds the new annotation by its object number.
        return Annotation(page, xref, 0)

    def __repr__(self) -> str:
        """Return ``Annotations([...])`` with the page's annotations."""
        return f"Annotations({list(self)!r})"


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Annotation, Annotations, AnnotationType, AnnotationFlags, Highlight):
    _public.__module__ = "pylopdf"
