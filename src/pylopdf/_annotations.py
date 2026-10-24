"""Annotations: ``Page.annotations``, its ``Annotation`` handles, and annotation specifications."""

from __future__ import annotations

import enum
import math
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast, overload

from pylopdf._attachments import _FileFields, _now
from pylopdf._canvas import _canvas_font
from pylopdf._content import BlendMode, Color
from pylopdf._errors import StalePageError
from pylopdf._fonts import Font
from pylopdf._geometry import Point, Quad, Rect
from pylopdf._hints import hinted_getattr
from pylopdf._limits import _validate_cumulative_utf8_input, _validate_optional_positive_int, _validate_rect
from pylopdf._objects import Dictionary
from pylopdf.pylopdf_core import LimitError, StaleObjectError

if TYPE_CHECKING:
    from pylopdf._page import Page

    # The core's form of one annotation: index, xref, subtype, display rect,
    # flags, the texts of the ``_CONTENTS`` slots, the ``/C`` and ``/IC``
    # components, and the ``/CA`` opacity.
    from pylopdf.pylopdf_core import _Annotation as _AnnotationTuple

#: The texts that one annotation may write: its subtype and its contents.
_MAX_ANNOTATION_INPUT_TEXT_SIZE = 1024 * 1024
_MAX_HIGHLIGHT_RECTS = 4096
_RECT_COORDINATE_COUNT = 4
#: The most dash lengths that a border pattern holds.
_MAX_BORDER_DASHES = 32
#: The most points that one annotation's geometry holds.
_MAX_ANNOTATION_POINTS = 65536


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


class BorderStyle(enum.StrEnum):
    """The style of an annotation's border, ``/BS /S`` (ISO 32000-2 Table 168)."""

    SOLID = "solid"
    DASHED = "dashed"
    BEVELED = "beveled"
    """Embossed, as a widget's border; other annotations draw it solid."""
    INSET = "inset"
    """Engraved, as a widget's border; other annotations draw it solid."""
    UNDERLINE = "underline"
    """Only the bottom edge, as a widget's border; other annotations draw it solid."""


#: The greatest intensity of a cloudy border (ISO 32000-2 Table 167).
_MAX_CLOUDS = 2

#: The ``/S`` name of each border style.
_BORDER_STYLE_NAMES = {
    BorderStyle.SOLID: "S",
    BorderStyle.DASHED: "D",
    BorderStyle.BEVELED: "B",
    BorderStyle.INSET: "I",
    BorderStyle.UNDERLINE: "U",
}
_BORDER_STYLES = {name: style for style, name in _BORDER_STYLE_NAMES.items()}


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{name} must be a number: {value!r}"
        raise TypeError(msg)
    if not math.isfinite(value):
        msg = f"{name} must be finite: {value!r}"
        raise ValueError(msg)
    return float(value)


@dataclass(frozen=True, slots=True, init=False)
class Border:
    """The border of an annotation: its line width, style, and dash pattern.

    ``dashes`` lists 1 to 32 dash and gap lengths in points, which a
    :attr:`BorderStyle.DASHED` border needs and no other style takes; giving
    ``dashes`` without a style selects the dashed style. A width of 0 draws
    no border.
    """

    width: float
    style: BorderStyle
    dashes: tuple[float, ...]

    def __init__(
        self,
        width: float = 1.0,
        *,
        style: BorderStyle | None = None,
        dashes: Sequence[float] = (),
    ) -> None:
        """Validate the border."""
        width = _number(width, "a border width")
        if width < 0:
            msg = f"a border width is not negative: {width!r}"
            raise ValueError(msg)
        if isinstance(dashes, (str, bytes)) or not isinstance(dashes, Sequence):
            msg = f"dashes must be a sequence of numbers: {dashes!r}"
            raise TypeError(msg)
        if len(dashes) > _MAX_BORDER_DASHES:
            msg = f"a border has at most {_MAX_BORDER_DASHES} dash lengths"
            raise ValueError(msg)
        lengths = tuple(_number(dash, "a dash length") for dash in dashes)
        if any(dash < 0 for dash in lengths) or (lengths and sum(lengths) <= 0):
            msg = f"dash lengths are not negative and do not sum to 0: {lengths!r}"
            raise ValueError(msg)
        if style is None:
            style = BorderStyle.DASHED if lengths else BorderStyle.SOLID
        style = BorderStyle(style)
        if (style is BorderStyle.DASHED) != bool(lengths):
            msg = "a dashed border needs dash lengths, and only a dashed border takes them"
            raise ValueError(msg)
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "style", style)
        object.__setattr__(self, "dashes", lengths)


class LineEnding(enum.StrEnum):
    """The shape at an end of a line or polyline, ``/LE`` (ISO 32000-2 Table 179).

    Closed shapes are filled with the annotation's interior color.
    """

    NONE = "none"
    SQUARE = "square"
    CIRCLE = "circle"
    DIAMOND = "diamond"
    OPEN_ARROW = "open_arrow"
    CLOSED_ARROW = "closed_arrow"
    BUTT = "butt"
    """A short line across the end."""
    REVERSED_OPEN_ARROW = "reversed_open_arrow"
    REVERSED_CLOSED_ARROW = "reversed_closed_arrow"
    SLASH = "slash"
    """A short line 30 degrees clockwise from across the end."""


#: The ``/LE`` name of each line ending.
_LINE_ENDING_NAMES = {
    LineEnding.NONE: "None",
    LineEnding.SQUARE: "Square",
    LineEnding.CIRCLE: "Circle",
    LineEnding.DIAMOND: "Diamond",
    LineEnding.OPEN_ARROW: "OpenArrow",
    LineEnding.CLOSED_ARROW: "ClosedArrow",
    LineEnding.BUTT: "Butt",
    LineEnding.REVERSED_OPEN_ARROW: "ROpenArrow",
    LineEnding.REVERSED_CLOSED_ARROW: "RClosedArrow",
    LineEnding.SLASH: "Slash",
}
_LINE_ENDINGS = {name: ending for ending, name in _LINE_ENDING_NAMES.items()}


class NoteIcon(enum.StrEnum):
    """The icon of a note, ``/Name`` (ISO 32000-2 §12.5.6.4)."""

    COMMENT = "comment"
    HELP = "help"
    INSERT = "insert"
    KEY = "key"
    NEW_PARAGRAPH = "new_paragraph"
    NOTE = "note"
    PARAGRAPH = "paragraph"


#: The ``/Name`` of each note icon.
_NOTE_ICON_NAMES = {icon: "".join(part.capitalize() for part in icon.value.split("_")) for icon in NoteIcon}

#: The side of a note's icon in points.
_NOTE_SIZE = 20.0


class StampIcon(enum.StrEnum):
    """The stamps of ISO 32000-2 §12.5.6.12, ``/Name``; pymupdf's ``STAMP_*`` codes in their order."""

    APPROVED = "approved"
    AS_IS = "as_is"
    CONFIDENTIAL = "confidential"
    DEPARTMENTAL = "departmental"
    EXPERIMENTAL = "experimental"
    EXPIRED = "expired"
    FINAL = "final"
    FOR_COMMENT = "for_comment"
    FOR_PUBLIC_RELEASE = "for_public_release"
    NOT_APPROVED = "not_approved"
    NOT_FOR_PUBLIC_RELEASE = "not_for_public_release"
    SOLD = "sold"
    TOP_SECRET = "top_secret"  # noqa: S105 - a stamp's name, not a password.
    DRAFT = "draft"


#: The ``/Name`` of each stamp.
_STAMP_NAMES = {icon: "".join(part.capitalize() for part in icon.value.split("_")) for icon in StampIcon}


class AttachmentIcon(enum.StrEnum):
    """The icon of a file attachment, ``/Name`` (ISO 32000-2 §12.5.6.15)."""

    GRAPH = "graph"
    PUSH_PIN = "push_pin"
    PAPERCLIP = "paperclip"
    TAG = "tag"


#: The ``/Name`` of each file attachment icon.
_ATTACHMENT_ICON_NAMES = {icon: "".join(part.capitalize() for part in icon.value.split("_")) for icon in AttachmentIcon}

#: Attached files stop at 64 MiB unless the caller passes another bound.
_DEFAULT_MAX_FILE_SIZE = 64 * 1024 * 1024


class AnnotationFile(_FileFields):
    """The file that a file attachment annotation embeds, as :attr:`Annotation.file` returns it.

    It reads the document on every access and has the members of
    :class:`Attachment`, which describes the document's own attachments:
    assigning ``filename``, ``description``, ``created``, or ``modified``
    edits the file specification at once.
    """

    __slots__ = ("_annotation",)

    def __init__(self, annotation: Annotation) -> None:
        """Wrap an annotation; :attr:`Annotation.file` does this."""
        self._annotation = annotation

    def _info(self) -> tuple[str, str, int, str, str]:
        info = self._annotation._file_info()
        if info is None:
            msg = "the annotation no longer embeds a file"
            raise StaleObjectError(msg)
        return info

    def _edit_file(self, edit: dict[str, str | None]) -> None:
        self._info()
        self._annotation._edit("annotation_file_edit", edit)

    def read(self, *, max_size: int | None = _DEFAULT_MAX_FILE_SIZE) -> bytes:
        """Return the file's contents; ``max_size`` bounds every decoding layer (``embedded_file_size``)."""
        self._info()
        _validate_optional_positive_int("max_size", max_size)
        annotation = self._annotation
        page = annotation._page
        return page._document._doc.annotation_file_read(
            page._page_number(), annotation._index, annotation._xref, max_size
        )

    def write(self, data: bytes | bytearray | memoryview, /, *, max_size: int | None = _DEFAULT_MAX_FILE_SIZE) -> None:
        """Replace the file's contents; ``size`` becomes the new length and ``modified`` the current time."""
        self._info()
        payload = _attached_data(data, max_size)
        self._annotation._edit("annotation_file_write", payload, max_size, _now())

    def __repr__(self) -> str:
        """Return ``AnnotationFile('name')``."""
        try:
            return f"AnnotationFile({self.filename!r})"
        except (StaleObjectError, StalePageError):
            return "<stale AnnotationFile>"


def _attached_data(data: object, max_size: int | None) -> bytes:
    """Check attached data against ``max_size`` before copying it."""
    _validate_optional_positive_int("max_size", max_size)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        msg = f"data must be bytes: {type(data).__name__}"
        raise TypeError(msg)
    size = data.nbytes if isinstance(data, memoryview) else len(data)
    if max_size is not None and size > max_size:
        code = "embedded_file_size"
        msg = f"attachment input is {size} bytes, exceeding the {max_size}-byte limit"
        raise LimitError(code, msg)
    return bytes(data)


#: The PDF name of each blend mode, such as ``ColorDodge``.
_BLEND_MODE_NAMES: dict[BlendMode, str] = {
    mode: "".join(part.capitalize() for part in mode.value.split("_")) for mode in BlendMode
}
_BLEND_MODES: dict[str, BlendMode] = {name: mode for mode, name in _BLEND_MODE_NAMES.items()}


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

    @rect.setter
    def rect(self, rect: Sequence[float]) -> None:
        self._set(rect=_validate_rect(rect))

    @property
    def contents(self) -> str | None:
        """Return the text that the annotation shows or describes, ``/Contents``."""
        return self._text(_CONTENTS)

    @contents.setter
    def contents(self, value: str) -> None:
        self._set(contents=_text_value(value, "contents"))

    @contents.deleter
    def contents(self) -> None:
        self._set(clear=["contents"])

    @property
    def author(self) -> str | None:
        """Return the author, ``/T``."""
        return self._text(_AUTHOR)

    @author.setter
    def author(self, value: str) -> None:
        self._set(author=_text_value(value, "author"))

    @author.deleter
    def author(self) -> None:
        self._set(clear=["author"])

    @property
    def subject(self) -> str | None:
        """Return the subject, ``/Subj``."""
        return self._text(_SUBJECT)

    @subject.setter
    def subject(self, value: str) -> None:
        self._set(subject=_text_value(value, "subject"))

    @subject.deleter
    def subject(self) -> None:
        self._set(clear=["subject"])

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

    @name.setter
    def name(self, value: str) -> None:
        self._set(name=_text_value(value, "name"))

    @name.deleter
    def name(self) -> None:
        self._set(clear=["name"])

    @property
    def icon(self) -> str | None:
        """Return the icon name of a note, stamp, or file attachment, ``/Name``."""
        return self._text(_ICON)

    @icon.setter
    def icon(self, icon: NoteIcon | StampIcon | AttachmentIcon) -> None:
        names = {**_NOTE_ICON_NAMES, **_STAMP_NAMES, **_ATTACHMENT_ICON_NAMES}
        if icon not in names:
            msg = f"icon is a NoteIcon, StampIcon, or AttachmentIcon member: {icon!r}"
            raise ValueError(msg)
        self._set(icon=names[icon])

    @property
    def intent(self) -> str | None:
        """Return the intent, ``/IT``, such as ``"FreeTextCallout"``."""
        return self._text(_INTENT)

    @property
    def color(self) -> Color | None:
        """Return the color of the border, title bar, or text markup, ``/C``; CMYK converts to RGB."""
        return _color(self._read()[6])

    @color.setter
    def color(self, color: Color | None) -> None:
        if color is None:
            self._set(clear=["color"])
        else:
            self._set(color=_opaque_color(color).rgb)

    @color.deleter
    def color(self) -> None:
        self._set(clear=["color"])

    @property
    def interior_color(self) -> Color | None:
        """Return the fill color of a shape or line ending, ``/IC``; CMYK converts to RGB."""
        return _color(self._read()[7])

    @interior_color.setter
    def interior_color(self, color: Color | None) -> None:
        if color is None:
            self._set(clear=["interior_color"])
        else:
            self._set(interior_color=_opaque_color(color, "interior_color").rgb)

    @interior_color.deleter
    def interior_color(self) -> None:
        self._set(clear=["interior_color"])

    @property
    def opacity(self) -> float:
        """Return the constant opacity, ``/CA``, 1.0 when absent."""
        opacity = self._read()[8]
        return 1.0 if opacity is None else opacity

    @opacity.setter
    def opacity(self, opacity: float) -> None:
        self._set(opacity=_opacity(opacity))

    @property
    def flags(self) -> AnnotationFlags:
        """Return the ``/F`` flags."""
        return AnnotationFlags(self._read()[4] & 0x3FF)

    @flags.setter
    def flags(self, flags: AnnotationFlags) -> None:
        if isinstance(flags, bool) or not isinstance(flags, int):
            msg = f"flags is an AnnotationFlags value: {flags!r}"
            raise TypeError(msg)
        self._set(flags=int(AnnotationFlags(flags)))

    @property
    def border(self) -> Border | None:
        """Return the border that ``/BS``, else ``/Border``, describes, or ``None`` without either."""
        border = self._read()[9]
        if border is None:
            return None
        width, style, dashes = border
        return Border(width, style=_BORDER_STYLES[style], dashes=dashes)

    @border.setter
    def border(self, border: Border | None) -> None:
        if border is None:
            self._set(clear=["border"])
            return
        if not isinstance(border, Border):
            msg = f"border must be a Border or None: {border!r}"
            raise TypeError(msg)
        self._set(border=(border.width, _BORDER_STYLE_NAMES[border.style], list(border.dashes)))

    @border.deleter
    def border(self) -> None:
        self._set(clear=["border"])

    @property
    def clouds(self) -> float:
        """Return or set the intensity of a cloudy border, ``/BE``, from 0 (none) to 2.

        Squares, circles, and polygons take one; ``del`` and 0 remove it.
        """
        effect = self.obj.get("BE")
        if not isinstance(effect, Dictionary) or effect.get("S") != "C":
            return 0.0
        intensity = effect.get("I", 0)
        if isinstance(intensity, bool) or not isinstance(intensity, (int, float)) or not math.isfinite(intensity):
            return 0.0
        return min(max(float(intensity), 0.0), float(_MAX_CLOUDS))

    @clouds.setter
    def clouds(self, clouds: float) -> None:
        value = _clouds(clouds)
        if value:
            self._set(clouds=value)
        else:
            self._set(clear=["clouds"])

    @clouds.deleter
    def clouds(self) -> None:
        self._set(clear=["clouds"])

    @property
    def blend_mode(self) -> BlendMode | None:
        """Return the blend mode that the appearance is drawn with, ``/BM``, or ``None`` without one."""
        name = self._read()[10]
        return None if name is None else _BLEND_MODES[name]

    @blend_mode.setter
    def blend_mode(self, mode: BlendMode | None) -> None:
        if mode is None:
            self._set(clear=["blend_mode"])
        else:
            self._set(blend_mode=_BLEND_MODE_NAMES[BlendMode(mode)])

    @blend_mode.deleter
    def blend_mode(self) -> None:
        self._set(clear=["blend_mode"])

    def _geometry(self) -> tuple[list[list[tuple[float, float]]] | None, tuple[str, str] | None]:
        page = self._page
        self._read()
        return page._document._doc.read_annotation_geometry(page._page_number(), self._index, self._xref)

    @property
    def vertices(self) -> tuple[Point, ...] | None:
        """Return the display points of a line (its two ends), polygon, or polyline.

        ``None`` for another type or a missing or malformed geometry. Reads
        refuse more than 65,536 points (``annotation_point_count``).
        """
        if self.type not in (AnnotationType.LINE, AnnotationType.POLYGON, AnnotationType.POLY_LINE):
            return None
        points, _ = self._geometry()
        return None if points is None else tuple(Point(x, y) for x, y in points[0])

    @vertices.setter
    def vertices(self, vertices: Iterable[Point | Sequence[float]]) -> None:
        minimum = 2
        points = _points(vertices, "vertices", minimum, [0])
        if self.type is AnnotationType.LINE and len(points) != minimum:
            msg = "a line has two vertices"
            raise ValueError(msg)
        self._set(points=[[tuple(point) for point in points]])

    @property
    def strokes(self) -> tuple[tuple[Point, ...], ...] | None:
        """Return the display points of each stroke of an ink annotation, ``None`` for another type."""
        if self.type is not AnnotationType.INK:
            return None
        points, _ = self._geometry()
        return None if points is None else tuple(tuple(Point(x, y) for x, y in stroke) for stroke in points)

    @strokes.setter
    def strokes(self, strokes: Iterable[Iterable[Point | Sequence[float]]]) -> None:
        if isinstance(strokes, (str, bytes)) or not isinstance(strokes, Iterable):
            msg = f"strokes must be a sequence of point sequences: {strokes!r}"
            raise TypeError(msg)
        budget = [0]
        validated = [[tuple(point) for point in _points(stroke, "a stroke", 1, budget)] for stroke in strokes]
        if not validated:
            msg = "strokes must hold at least one stroke"
            raise ValueError(msg)
        self._set(points=validated)

    @property
    def line_endings(self) -> tuple[LineEnding, LineEnding] | None:
        """Return the start and end shapes of a line or polyline, ``None`` for another type."""
        _, endings = self._geometry()
        if endings is None:
            return None
        start, end = endings
        return _LINE_ENDINGS[start], _LINE_ENDINGS[end]

    @line_endings.setter
    def line_endings(self, endings: tuple[LineEnding, LineEnding]) -> None:
        start, end = _line_endings(endings)
        self._set(line_endings=(_LINE_ENDING_NAMES[start], _LINE_ENDING_NAMES[end]))

    @property
    def rotation(self) -> int:
        """Return how far the annotation's content is turned clockwise as the page is displayed.

        That is the page's rotation less the annotation's ``/Rotate``, which
        turns the content counterclockwise in PDF user space: a free text
        that :class:`FreeText` wrote upright reads 0 on any page.
        """
        self._read()
        stored = self.obj.get("Rotate")
        turn = stored if isinstance(stored, int) and not isinstance(stored, bool) and stored % 90 == 0 else 0
        return (self._page.rotation - turn) % 360

    @rotation.setter
    def rotation(self, rotate: int) -> None:
        if isinstance(rotate, bool) or not isinstance(rotate, int) or rotate % 90:
            msg = f"rotation must be a multiple of 90: {rotate!r}"
            raise ValueError(msg)
        self._set(rotate=rotate % 360)

    def _file_info(self) -> tuple[str, str, int, str, str] | None:
        page = self._page
        self._read()
        return page._document._doc.annotation_file_info(page._page_number(), self._index, self._xref)

    @property
    def file(self) -> AnnotationFile | None:
        """Return the file that a file attachment embeds, or ``None`` for another annotation."""
        return None if self._file_info() is None else AnnotationFile(self)

    @property
    def language(self) -> str | None:
        """Return or set the BCP 47 language of the annotation's text, ``/Lang``; ``del`` removes it."""
        self._read()
        value = self.obj.get("Lang")
        return value.text if isinstance(value, bytes) and hasattr(value, "text") else None

    @language.setter
    def language(self, value: str) -> None:
        self._set(language=_text_value(value, "language"))

    @language.deleter
    def language(self) -> None:
        self._set(clear=["language"])

    def _set(self, *, clear: list[str] | None = None, **entries: object) -> None:
        """Change properties and draw the appearance again, recording the time of the edit."""
        edit: dict[str, object] = {"clear": clear or [], "modified": _now(), **entries}
        page = self._page
        self._read()
        if self.type is AnnotationType.FREE_TEXT:
            stored = self.obj.get("DA")
            if isinstance(stored, bytes) and stored.startswith(b"/PyloF"):
                text = entries.get("contents", self.contents) or ""
                face = _canvas_font(cast("str", text), None)
                if face.standard_name is None:
                    edit["font"] = face
        self._xref = page._document._doc.edit_annotation(page._page_number(), self._index, self._xref, edit)

    def _edit(self, method: str, *args: object) -> None:
        page = self._page
        self._read()
        self._xref = getattr(page._document._doc, method)(page._page_number(), self._index, self._xref, *args)

    @property
    def popup_rect(self) -> Rect | None:
        """Return or set the display rectangle of the annotation's popup, ``None`` without one.

        Assigning a rectangle moves the popup, or adds one that the page
        lists after the annotation; ``del`` removes it. Links, widgets, and
        popups have none.
        """
        popup = self._read()[11][1]
        return None if popup is None else Rect(*popup)

    @popup_rect.setter
    def popup_rect(self, rect: Sequence[float]) -> None:
        self._edit("set_annotation_popup", _validate_rect(rect))

    @popup_rect.deleter
    def popup_rect(self) -> None:
        self._edit("set_annotation_popup", None)

    @property
    def is_open(self) -> bool:
        """Return or set whether the popup, or a note without one, opens when the page is shown.

        Assigning needs a popup or a note.
        """
        return self._read()[11][2]

    @is_open.setter
    def is_open(self, value: bool) -> None:
        if not isinstance(value, bool):
            msg = f"is_open is a bool: {value!r}"
            raise TypeError(msg)
        self._edit("set_annotation_open", value)

    @property
    def in_reply_to(self) -> Annotation | None:
        """Return or set the annotation that this one replies to, ``/IRT``, or ``None``.

        The annotation replied to belongs to the same page; a reply to an
        annotation that is not listed in ``page.annotations``, such as a
        link, raises :class:`StaleObjectError` on use.
        """
        target = self._read()[11][0]
        return None if target is None else Annotation(self._page, target, 0)

    @in_reply_to.setter
    def in_reply_to(self, annotation: Annotation | None) -> None:
        if annotation is None:
            self._edit("set_annotation_reply", None)
            return
        if not isinstance(annotation, Annotation):
            msg = f"in_reply_to is an Annotation or None: {annotation!r}"
            raise TypeError(msg)
        if annotation._page != self._page:
            msg = "an annotation replies to an annotation of its own page"
            raise ValueError(msg)
        self._edit("set_annotation_reply", annotation.xref)

    @in_reply_to.deleter
    def in_reply_to(self) -> None:
        self._edit("set_annotation_reply", None)

    @property
    def replies(self) -> list[Annotation]:
        """Return the annotations of the page that reply to this one, in ``/Annots`` order."""
        xref = self.xref
        page = self._page
        return [
            Annotation(page, entry[1], entry[0])
            for entry in page._document._doc.read_annotations(page._page_number())
            if xref and entry[11][0] == xref
        ]

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


def _text_value(value: object, name: str) -> str:
    """Check a text property's type and its 1 MiB bound before the core copies it."""
    if not isinstance(value, str):
        msg = f"{name} must be a str: {type(value).__name__}"
        raise TypeError(msg)
    _validate_cumulative_utf8_input(
        (value,),
        _MAX_ANNOTATION_INPUT_TEXT_SIZE,
        limit_code="annotation_input_size",
        input_label=f"annotation {name}",
    )
    return value


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


def _opaque_color(value: object, name: str = "color") -> Color:
    if not isinstance(value, Color):
        msg = f"{name} must be a Color: {value!r}"
        raise TypeError(msg)
    components = (value.red, value.green, value.blue)
    if value.alpha != 1 or not all(math.isfinite(c) and 0 <= c <= 1 for c in components):
        msg = f"an annotation color has components from 0 to 1 and no transparency: {value!r}"
        raise ValueError(msg)
    return value


class _TextMarkup:
    """The fields and validation that the text-markup specifications share."""

    __slots__ = ()

    rects: tuple[Rect, ...]
    color: Color
    opacity: float
    contents: str | None

    def _init(self, rects: object, color: object, opacity: object, contents: object) -> None:
        """Validate the specification; the contents are bounded before the rectangles are read."""
        if contents is not None and not isinstance(contents, str):
            msg = f"contents must be a str or None: {type(contents).__name__}"
            raise TypeError(msg)
        _validate_cumulative_utf8_input(
            (type(self).__name__, "" if contents is None else contents, _DATE_PLACEHOLDER, _DATE_PLACEHOLDER),
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

    def _payload(self) -> dict[str, Any]:
        return {
            "subtype": type(self).__name__,
            "rects": [(rect.x0, rect.y0, rect.x1, rect.y1) for rect in self.rects],
            "color": self.color.rgb,
            "opacity": self.opacity,
            "contents": self.contents,
            "created": _now(),
        }


@dataclass(frozen=True, slots=True, init=False)
class Highlight(_TextMarkup):
    """A highlight over text, as ``page.annotations.add`` takes it.

    ``rects`` is one rectangle or quad, or a sequence of them, in display
    coordinates, such as the result of ``page.search``; a :class:`Quad`
    contributes its bounding rectangle, and one annotation holds at most
    4,096. ``color`` fills them in multiply blending at ``opacity``, and
    ``contents`` is the text of its note. The annotation is written with its
    appearance, so every viewer draws it alike.
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
        self._init(rects, color, opacity, contents)


@dataclass(frozen=True, slots=True, init=False)
class Underline(_TextMarkup):
    """A line under text, as ``page.annotations.add`` takes it.

    The fields are those of :class:`Highlight`; ``color`` draws a one-point
    line just above the bottom edge of each rectangle.
    """

    rects: tuple[Rect, ...]
    color: Color
    opacity: float
    contents: str | None

    def __init__(
        self,
        rects: Sequence[float] | Quad | Iterable[Sequence[float] | Quad],
        *,
        color: Color = Color(0.0, 0.0, 1.0),  # noqa: B008 - Color is immutable.
        opacity: float = 1.0,
        contents: str | None = None,
    ) -> None:
        """Validate the specification; the contents are bounded before the rectangles are read."""
        self._init(rects, color, opacity, contents)


@dataclass(frozen=True, slots=True, init=False)
class StrikeOut(_TextMarkup):
    """A line through text, as ``page.annotations.add`` takes it.

    The fields are those of :class:`Highlight`; ``color`` draws a one-point
    line through the middle of each rectangle.
    """

    rects: tuple[Rect, ...]
    color: Color
    opacity: float
    contents: str | None

    def __init__(
        self,
        rects: Sequence[float] | Quad | Iterable[Sequence[float] | Quad],
        *,
        color: Color = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        opacity: float = 1.0,
        contents: str | None = None,
    ) -> None:
        """Validate the specification; the contents are bounded before the rectangles are read."""
        self._init(rects, color, opacity, contents)


@dataclass(frozen=True, slots=True, init=False)
class Squiggly(_TextMarkup):
    """A wavy line under text, as ``page.annotations.add`` takes it.

    The fields are those of :class:`Highlight`; ``color`` draws a zigzag
    two points wide along the bottom of each rectangle, at most 65,536 points
    in all (``annotation_point_count``).
    """

    rects: tuple[Rect, ...]
    color: Color
    opacity: float
    contents: str | None

    def __init__(
        self,
        rects: Sequence[float] | Quad | Iterable[Sequence[float] | Quad],
        *,
        color: Color = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        opacity: float = 1.0,
        contents: str | None = None,
    ) -> None:
        """Validate the specification; the contents are bounded before the rectangles are read."""
        self._init(rects, color, opacity, contents)


def _optional_color(value: object, name: str) -> Color | None:
    return None if value is None else _opaque_color(value, name)


def _opacity(value: object) -> float:
    opacity = _number(value, "opacity")
    if not 0.0 < opacity <= 1.0:
        msg = f"opacity must be greater than 0 and at most 1: {opacity!r}"
        raise ValueError(msg)
    return opacity


def _contents(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        msg = f"contents must be a str or None: {type(value).__name__}"
        raise TypeError(msg)
    return value


#: A PDF date as created annotations write it twice, for /CreationDate and
#: /M, which the input budget charges so that the annotation reads back
#: under the same budget.
_DATE_PLACEHOLDER = "D:00000000000000Z"


class _Shape:
    """The fields that square and circle specifications share."""

    __slots__ = ()

    rect: Rect
    color: Color | None
    interior_color: Color | None
    border: Border
    opacity: float
    blend_mode: BlendMode | None
    contents: str | None

    def _init(  # noqa: PLR0913, PLR0917 - the shared fields, as each __init__ received them.
        self,
        rect: Sequence[float],
        color: object,
        interior_color: object,
        border: object,
        opacity: object,
        blend_mode: object,
        contents: object,
    ) -> None:
        contents = _contents(contents)
        _validate_cumulative_utf8_input(
            (type(self).__name__, "" if contents is None else contents, _DATE_PLACEHOLDER, _DATE_PLACEHOLDER),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and contents input",
        )
        if not isinstance(border, Border):
            msg = f"border must be a Border: {border!r}"
            raise TypeError(msg)
        object.__setattr__(self, "rect", Rect(*_validate_rect(rect)))
        object.__setattr__(self, "color", _optional_color(color, "color"))
        object.__setattr__(self, "interior_color", _optional_color(interior_color, "interior_color"))
        object.__setattr__(self, "border", border)
        object.__setattr__(self, "opacity", _opacity(opacity))
        object.__setattr__(self, "blend_mode", None if blend_mode is None else BlendMode(blend_mode))
        object.__setattr__(self, "contents", contents)

    def _spec(self, subtype: str) -> dict[str, Any]:
        return {"rect": tuple(self.rect), **self._style(subtype)}

    def _payload(self) -> dict[str, Any]:
        return self._spec(type(self).__name__)

    def _style(self, subtype: str) -> dict[str, Any]:
        border = self.border
        return {
            "subtype": subtype,
            "color": None if self.color is None else self.color.rgb,
            "interior_color": None if (interior := getattr(self, "interior_color", None)) is None else interior.rgb,
            "border": (border.width, _BORDER_STYLE_NAMES[border.style], list(border.dashes)),
            "opacity": self.opacity,
            "blend_mode": None if self.blend_mode is None else _BLEND_MODE_NAMES[self.blend_mode],
            "contents": self.contents,
            "created": _now(),
            "clouds": getattr(self, "clouds", 0.0) or None,
        }


def _clouds(clouds: object) -> float:
    """Validate the intensity of a cloudy border: 0 for none, at most 2."""
    if isinstance(clouds, bool) or not isinstance(clouds, (int, float)):
        msg = f"clouds is a number from 0 to 2: {clouds!r}"
        raise TypeError(msg)
    value = float(clouds)
    if not 0 <= value <= _MAX_CLOUDS:
        msg = f"clouds is a number from 0 to 2: {clouds!r}"
        raise ValueError(msg)
    return value


@dataclass(frozen=True, slots=True, init=False)
class Square(_Shape):
    """A rectangle, as ``page.annotations.add`` takes it.

    ``rect`` is in display coordinates. ``color`` strokes the border and
    ``interior_color`` fills the inside; either may be ``None``. The border
    lies inside ``rect``, and ``clouds`` from 0 to 2 draws it as a cloud
    whose bumps grow with it, inside ``rect`` too. ``opacity`` and ``blend_mode`` apply to both
    paints, and ``contents`` is the text of the annotation's note. The
    annotation is written with its appearance, so every viewer draws it
    alike.
    """

    rect: Rect
    color: Color | None
    interior_color: Color | None
    border: Border
    clouds: float
    opacity: float
    blend_mode: BlendMode | None
    contents: str | None

    def __init__(  # noqa: PLR0913 - the annotation's style options.
        self,
        rect: Sequence[float],
        *,
        color: Color | None = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        interior_color: Color | None = None,
        border: Border = Border(),  # noqa: B008 - Border is immutable.
        clouds: float = 0,
        opacity: float = 1.0,
        blend_mode: BlendMode | None = None,
        contents: str | None = None,
    ) -> None:
        """Validate the specification."""
        self._init(rect, color, interior_color, border, opacity, blend_mode, contents)
        object.__setattr__(self, "clouds", _clouds(clouds))


@dataclass(frozen=True, slots=True, init=False)
class Circle(_Shape):
    """An ellipse inscribed in a rectangle, as ``page.annotations.add`` takes it.

    The fields are those of :class:`Square`; the ellipse and its border lie
    inside ``rect``.
    """

    rect: Rect
    color: Color | None
    interior_color: Color | None
    border: Border
    clouds: float
    opacity: float
    blend_mode: BlendMode | None
    contents: str | None

    def __init__(  # noqa: PLR0913 - the annotation's style options.
        self,
        rect: Sequence[float],
        *,
        color: Color | None = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        interior_color: Color | None = None,
        border: Border = Border(),  # noqa: B008 - Border is immutable.
        clouds: float = 0,
        opacity: float = 1.0,
        blend_mode: BlendMode | None = None,
        contents: str | None = None,
    ) -> None:
        """Validate the specification."""
        self._init(rect, color, interior_color, border, opacity, blend_mode, contents)
        object.__setattr__(self, "clouds", _clouds(clouds))


@dataclass(frozen=True, slots=True, init=False)
class Note:
    """A note shown as an icon, as ``page.annotations.add`` takes it.

    ``at`` is the display point of the icon's top-left corner; the icon is
    20 points square and shows upright on a turned page. ``contents`` is the
    note's text, ``icon`` its picture, ``color`` the icon's fill, and
    ``is_open`` whether a viewer opens the note when it shows the page. Add a
    popup by assigning :attr:`Annotation.popup_rect`.
    """

    at: Point
    contents: str | None
    icon: NoteIcon
    color: Color
    opacity: float
    is_open: bool

    def __init__(  # noqa: PLR0913 - the note's options.
        self,
        at: Point | Sequence[float],
        *,
        contents: str | None = None,
        icon: NoteIcon = NoteIcon.NOTE,
        color: Color = Color(1.0, 1.0, 0.0),  # noqa: B008 - Color is immutable.
        opacity: float = 1.0,
        is_open: bool = False,
    ) -> None:
        """Validate the specification."""
        contents = _contents(contents)
        _validate_cumulative_utf8_input(
            ("Text", "" if contents is None else contents, _DATE_PLACEHOLDER, _DATE_PLACEHOLDER),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and contents input",
        )
        if not isinstance(is_open, bool):
            msg = f"is_open is a bool: {is_open!r}"
            raise TypeError(msg)
        object.__setattr__(self, "at", Point(*_point(at)))
        object.__setattr__(self, "contents", contents)
        object.__setattr__(self, "icon", NoteIcon(icon))
        object.__setattr__(self, "color", _opaque_color(color))
        object.__setattr__(self, "opacity", _opacity(opacity))
        object.__setattr__(self, "is_open", is_open)

    def _payload(self) -> dict[str, Any]:
        x, y = self.at
        return {
            "subtype": "Text",
            "rect": (x, y, x + _NOTE_SIZE, y + _NOTE_SIZE),
            "color": self.color.rgb,
            "opacity": self.opacity,
            "contents": self.contents,
            "created": _now(),
            "icon": _NOTE_ICON_NAMES[self.icon],
            "open": self.is_open,
        }


@dataclass(frozen=True, slots=True, init=False)
class Stamp:
    """A rubber stamp, as ``page.annotations.add`` takes it.

    ``rect`` is in display coordinates; the stamp's label is drawn as large
    as fits, centred in a rounded frame, in ``color`` or the stamp's own
    color (green for approval, red for restriction, blue for the rest), and
    ``rotate`` turns it clockwise by a multiple of 90 degrees as the page is
    displayed.
    """

    rect: Rect
    icon: StampIcon
    color: Color | None
    opacity: float
    rotate: int
    contents: str | None

    def __init__(  # noqa: PLR0913 - the stamp's options.
        self,
        rect: Sequence[float],
        *,
        icon: StampIcon = StampIcon.APPROVED,
        color: Color | None = None,
        opacity: float = 1.0,
        rotate: int = 0,
        contents: str | None = None,
    ) -> None:
        """Validate the specification."""
        contents = _contents(contents)
        _validate_cumulative_utf8_input(
            ("Stamp", "" if contents is None else contents, _DATE_PLACEHOLDER, _DATE_PLACEHOLDER),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and contents input",
        )
        if isinstance(rotate, bool) or not isinstance(rotate, int) or rotate % 90:
            msg = f"rotate must be a multiple of 90: {rotate!r}"
            raise ValueError(msg)
        object.__setattr__(self, "rect", Rect(*_validate_rect(rect)))
        object.__setattr__(self, "icon", StampIcon(icon))
        object.__setattr__(self, "color", _optional_color(color, "color"))
        object.__setattr__(self, "opacity", _opacity(opacity))
        object.__setattr__(self, "rotate", rotate % 360)
        object.__setattr__(self, "contents", contents)

    def _payload(self) -> dict[str, Any]:
        return {
            "subtype": "Stamp",
            "rect": tuple(self.rect),
            "icon": _STAMP_NAMES[self.icon],
            "color": None if self.color is None else self.color.rgb,
            "opacity": self.opacity,
            "rotate": self.rotate,
            "contents": self.contents,
            "created": _now(),
        }


@dataclass(frozen=True, slots=True, init=False)
class Caret:
    """A caret that marks where text goes, as ``page.annotations.add`` takes it.

    ``at`` is the display point of the top-left corner of its 20-point
    square; ``color`` fills the caret, and ``contents`` is the text to
    insert.
    """

    at: Point
    color: Color
    opacity: float
    contents: str | None

    def __init__(
        self,
        at: Point | Sequence[float],
        *,
        color: Color = Color(0.0, 0.0, 1.0),  # noqa: B008 - Color is immutable.
        opacity: float = 1.0,
        contents: str | None = None,
    ) -> None:
        """Validate the specification."""
        contents = _contents(contents)
        _validate_cumulative_utf8_input(
            ("Caret", "" if contents is None else contents, _DATE_PLACEHOLDER, _DATE_PLACEHOLDER),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and contents input",
        )
        object.__setattr__(self, "at", Point(*_point(at)))
        object.__setattr__(self, "color", _opaque_color(color))
        object.__setattr__(self, "opacity", _opacity(opacity))
        object.__setattr__(self, "contents", contents)

    def _payload(self) -> dict[str, Any]:
        x, y = self.at
        return {
            "subtype": "Caret",
            "rect": (x, y, x + _NOTE_SIZE, y + _NOTE_SIZE),
            "color": self.color.rgb,
            "opacity": self.opacity,
            "contents": self.contents,
            "created": _now(),
        }


@dataclass(frozen=True, slots=True, init=False)
class FileAttachment:
    """A file embedded in the page behind an icon, as ``page.annotations.add`` takes it.

    ``at`` is the display point of the top-left corner of the 20-point icon,
    ``data`` the file and ``filename`` its name, which with ``description``
    stops at 1 MiB (``embedded_file_input_size``); ``max_size`` bounds
    ``data`` before it is copied and defaults to 64 MiB
    (``embedded_file_size``). Read the file back through
    :attr:`Annotation.file`.
    """

    at: Point
    data: bytes
    filename: str
    description: str | None
    icon: AttachmentIcon
    color: Color
    opacity: float
    contents: str | None

    def __init__(  # noqa: PLR0913 - the attachment's options.
        self,
        at: Point | Sequence[float],
        data: bytes | bytearray | memoryview,
        *,
        filename: str,
        description: str | None = None,
        icon: AttachmentIcon = AttachmentIcon.PUSH_PIN,
        color: Color = Color(1.0, 1.0, 0.0),  # noqa: B008 - Color is immutable.
        opacity: float = 1.0,
        contents: str | None = None,
        max_size: int | None = _DEFAULT_MAX_FILE_SIZE,
    ) -> None:
        """Validate the specification; texts and data are bounded before they are copied."""
        contents = _contents(contents)
        if not isinstance(filename, str) or (description is not None and not isinstance(description, str)):
            msg = "filename must be a str and description a str or None"
            raise TypeError(msg)
        _validate_cumulative_utf8_input(
            (filename, "" if description is None else description),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="embedded_file_input_size",
            input_label="attached file name and description",
        )
        _validate_cumulative_utf8_input(
            ("FileAttachment", "" if contents is None else contents, _DATE_PLACEHOLDER, _DATE_PLACEHOLDER),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and contents input",
        )
        object.__setattr__(self, "at", Point(*_point(at)))
        object.__setattr__(self, "data", _attached_data(data, max_size))
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "icon", AttachmentIcon(icon))
        object.__setattr__(self, "color", _opaque_color(color))
        object.__setattr__(self, "opacity", _opacity(opacity))
        object.__setattr__(self, "contents", contents)

    def _payload(self) -> dict[str, Any]:
        x, y = self.at
        return {
            "subtype": "FileAttachment",
            "rect": (x, y, x + _NOTE_SIZE, y + _NOTE_SIZE),
            "data": self.data,
            "filename": self.filename,
            "description": self.description,
            "icon": _ATTACHMENT_ICON_NAMES[self.icon],
            "color": self.color.rgb,
            "opacity": self.opacity,
            "contents": self.contents,
            "created": _now(),
            "max_size": None,
        }


@dataclass(frozen=True, slots=True, init=False)
class Redaction:
    """An area marked for removal, as ``page.annotations.add`` takes it.

    ``rects`` is one rectangle or quad, or a sequence of them, as for
    :class:`Highlight`. Until the redaction is applied, the areas are
    outlined and crossed out in ``color``; applying it (``page.redact``,
    the Rewriting milestone) removes what they cover and fills them with
    ``fill``, writing ``overlay_text`` in ``font`` (a Standard 14 font,
    Helvetica by default), ``size``, ``text_color``, and ``align``.
    """

    rects: tuple[Rect, ...]
    color: Color
    fill: Color | None
    overlay_text: str | None
    font: Font | None
    size: float
    text_color: Color
    align: Literal["left", "center", "right"]
    contents: str | None

    def __init__(  # noqa: PLR0913 - the redaction's options.
        self,
        rects: Sequence[float] | Quad | Iterable[Sequence[float] | Quad],
        *,
        color: Color = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        fill: Color | None = Color(0.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        overlay_text: str | None = None,
        font: Font | None = None,
        size: float = 11.0,
        text_color: Color = Color(0.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        align: Literal["left", "center", "right"] = "left",
        contents: str | None = None,
    ) -> None:
        """Validate the specification; the texts are bounded before the rectangles are read."""
        contents = _contents(contents)
        if overlay_text is not None and not isinstance(overlay_text, str):
            msg = f"overlay_text must be a str or None: {type(overlay_text).__name__}"
            raise TypeError(msg)
        _validate_cumulative_utf8_input(
            (
                "Redact",
                "" if contents is None else contents,
                "" if overlay_text is None else overlay_text,
                _DATE_PLACEHOLDER,
                _DATE_PLACEHOLDER,
            ),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype, contents, and overlay text input",
        )
        if font is not None and (not isinstance(font, Font) or font._font.standard_name is None):
            msg = "a redaction's overlay text uses a Standard 14 Font, such as Font.standard('Helvetica')"
            raise TypeError(msg)
        size = _number(size, "size")
        if not 0 < size <= 10000:  # noqa: PLR2004 - the core's bound.
            msg = f"size must be greater than 0 and at most 10000: {size!r}"
            raise ValueError(msg)
        if align not in _FREE_TEXT_ALIGNMENTS:
            msg = f"align must be one of {', '.join(_FREE_TEXT_ALIGNMENTS)}: {align!r}"
            raise ValueError(msg)
        object.__setattr__(self, "rects", _validate_highlight_rects(rects))
        object.__setattr__(self, "color", _opaque_color(color))
        object.__setattr__(self, "fill", _optional_color(fill, "fill"))
        object.__setattr__(self, "overlay_text", overlay_text)
        object.__setattr__(self, "font", font)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "text_color", _opaque_color(text_color, "text_color"))
        object.__setattr__(self, "align", align)
        object.__setattr__(self, "contents", contents)

    def _payload(self) -> dict[str, Any]:
        return {
            "subtype": "Redact",
            "rects": [(rect.x0, rect.y0, rect.x1, rect.y1) for rect in self.rects],
            "color": self.color.rgb,
            "interior_color": None if self.fill is None else self.fill.rgb,
            "overlay_text": self.overlay_text,
            "font_name": "Helvetica" if self.font is None else self.font._font.standard_name,
            "font_size": self.size,
            "text_color": self.text_color.rgb,
            "align": _FREE_TEXT_ALIGNMENTS.index(self.align),
            "contents": self.contents,
            "created": _now(),
        }


#: The ``/Q`` code of each free-text alignment.
_FREE_TEXT_ALIGNMENTS = ("left", "center", "right")


@dataclass(frozen=True, slots=True, init=False)
class FreeText:
    """Text written on the page in a box, as ``page.annotations.add`` takes it.

    ``rect`` is the box in display coordinates and ``text`` its text, laid
    out as :meth:`Canvas.textbox` lays it out: wrapped, ``align``-ed, and in
    ``font`` at ``size`` points and ``text_color``. Without ``font``, WinAnsi
    text is drawn in Helvetica and other text in the face of an installed
    font extra, as on a canvas; a Standard 14 font is named in the
    annotation, so other viewers can redraw it, and an OpenType face is
    embedded. ``color`` fills the box, ``border`` outlines it in the text
    color, and ``rotate`` turns the text clockwise by a multiple of 90
    degrees as the page is displayed. ``callout`` draws a line from the
    display point that the text explains, through an optional knee, to the
    box, with ``callout_ending`` at the explained point. Text that does not
    fit raises :class:`PdfError`.
    """

    rect: Rect
    text: str
    font: Font | None
    size: float
    text_color: Color
    color: Color | None
    border: Border
    align: Literal["left", "center", "right"]
    rotate: int
    callout: tuple[Point, ...] | None
    callout_ending: LineEnding
    opacity: float

    def __init__(  # noqa: PLR0913 - the text's style options.
        self,
        rect: Sequence[float],
        text: str,
        *,
        font: Font | None = None,
        size: float = 11.0,
        text_color: Color = Color(0.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        color: Color | None = None,
        border: Border = Border(0),  # noqa: B008 - Border is immutable.
        align: Literal["left", "center", "right"] = "left",
        rotate: int = 0,
        callout: Sequence[Point | Sequence[float]] | None = None,
        callout_ending: LineEnding = LineEnding.NONE,
        opacity: float = 1.0,
    ) -> None:
        """Validate the specification; the text is bounded before anything else is read."""
        if not isinstance(text, str):
            msg = f"text must be a str: {type(text).__name__}"
            raise TypeError(msg)
        _validate_cumulative_utf8_input(
            ("FreeText", text, _DATE_PLACEHOLDER, _DATE_PLACEHOLDER),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and contents input",
        )
        if font is not None and not isinstance(font, Font):
            msg = f"font must be a pylopdf.Font or None, not {type(font).__name__}"
            raise TypeError(msg)
        size = _number(size, "size")
        if not 0 < size <= 10000:  # noqa: PLR2004 - the core's bound.
            msg = f"size must be greater than 0 and at most 10000: {size!r}"
            raise ValueError(msg)
        if not isinstance(border, Border):
            msg = f"border must be a Border: {border!r}"
            raise TypeError(msg)
        if align not in _FREE_TEXT_ALIGNMENTS:
            msg = f"align must be one of {', '.join(_FREE_TEXT_ALIGNMENTS)}: {align!r}"
            raise ValueError(msg)
        if isinstance(rotate, bool) or not isinstance(rotate, int) or rotate % 90:
            msg = f"rotate must be a multiple of 90: {rotate!r}"
            raise ValueError(msg)
        points = None
        if callout is not None:
            if isinstance(callout, (str, bytes)) or not isinstance(callout, Sequence) or not 2 <= len(callout) <= 3:  # noqa: PLR2004
                msg = "callout must hold 2 or 3 points: the point it explains, an optional knee, and the box"
                raise ValueError(msg)
            points = tuple(Point(*_point(point)) for point in callout)
        object.__setattr__(self, "rect", Rect(*_validate_rect(rect)))
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "font", font)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "text_color", _opaque_color(text_color, "text_color"))
        object.__setattr__(self, "color", _optional_color(color, "color"))
        object.__setattr__(self, "border", border)
        object.__setattr__(self, "align", align)
        object.__setattr__(self, "rotate", rotate % 360)
        object.__setattr__(self, "callout", points)
        object.__setattr__(self, "callout_ending", LineEnding(callout_ending))
        object.__setattr__(self, "opacity", _opacity(opacity))

    def _payload(self) -> dict[str, Any]:
        face = _canvas_font(self.text, self.font)
        border = self.border
        spec: dict[str, Any] = {
            "subtype": "FreeText",
            "rect": tuple(self.rect),
            "contents": self.text,
            "created": _now(),
            "color": None if self.color is None else self.color.rgb,
            "border": (border.width, _BORDER_STYLE_NAMES[border.style], list(border.dashes)),
            "opacity": self.opacity,
            "text_color": self.text_color.rgb,
            "font_size": self.size,
            "align": _FREE_TEXT_ALIGNMENTS.index(self.align),
            "rotate": self.rotate,
        }
        if face.standard_name is None:
            spec["font"] = face
        else:
            spec["font_name"] = face.standard_name
        if self.callout is not None:
            spec["callout"] = [tuple(point) for point in self.callout]
            spec["callout_ending"] = _LINE_ENDING_NAMES[self.callout_ending]
        return spec


def _point(value: object) -> tuple[float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:  # noqa: PLR2004
        msg = f"a point is a Point or a pair of numbers: {value!r}"
        raise TypeError(msg)
    return _number(value[0], "a point coordinate"), _number(value[1], "a point coordinate")


def _points(values: object, name: str, minimum: int, budget: list[int]) -> tuple[Point, ...]:
    """Validate a sequence of points, stopping an iterable at the first point over the budget."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        msg = f"{name} must be a sequence of points: {values!r}"
        raise TypeError(msg)
    points: list[Point] = []
    for value in values:
        if budget[0] >= _MAX_ANNOTATION_POINTS:
            code = "annotation_point_count"
            msg = f"an annotation holds at most {_MAX_ANNOTATION_POINTS} points"
            raise LimitError(code, msg)
        budget[0] += 1
        points.append(Point(*_point(value)))
    if len(points) < minimum:
        msg = f"{name} must hold at least {minimum} points"
        raise ValueError(msg)
    return tuple(points)


def _line_endings(value: object) -> tuple[LineEnding, LineEnding]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:  # noqa: PLR2004
        msg = f"line_endings is a pair of LineEnding members: {value!r}"
        raise TypeError(msg)
    return LineEnding(value[0]), LineEnding(value[1])


class _Drawn(_Shape):
    """The fields of the specifications that draw lines through points."""

    __slots__ = ()

    def _init_drawn(  # noqa: PLR0913, PLR0917 - the shared fields, as each __init__ received them.
        self,
        color: object,
        interior_color: object,
        border: object,
        opacity: object,
        blend_mode: object,
        contents: object,
    ) -> None:
        contents = _contents(contents)
        _validate_cumulative_utf8_input(
            (type(self).__name__, "" if contents is None else contents, _DATE_PLACEHOLDER, _DATE_PLACEHOLDER),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and contents input",
        )
        if not isinstance(border, Border):
            msg = f"border must be a Border: {border!r}"
            raise TypeError(msg)
        object.__setattr__(self, "color", _optional_color(color, "color"))
        if not isinstance(self, Ink):
            object.__setattr__(self, "interior_color", _optional_color(interior_color, "interior_color"))
        object.__setattr__(self, "border", border)
        object.__setattr__(self, "opacity", _opacity(opacity))
        object.__setattr__(self, "blend_mode", None if blend_mode is None else BlendMode(blend_mode))
        object.__setattr__(self, "contents", contents)

    def _drawn_spec(self, subtype: str, points: list[list[tuple[float, float]]]) -> dict[str, Any]:
        spec = self._style(subtype)
        spec["points"] = points
        endings = getattr(self, "line_endings", None)
        if endings is not None:
            spec["line_endings"] = tuple(_LINE_ENDING_NAMES[ending] for ending in endings)
        return spec


@dataclass(frozen=True, slots=True, init=False)
class LineAnnotation(_Drawn):
    """A straight line between two display points, as ``page.annotations.add`` takes it.

    ``color`` strokes the line with the ``border`` width and dashes, and
    ``line_endings`` gives the shapes at its start and end, whose closed
    shapes ``interior_color`` fills. The annotation's rectangle is the line's
    bounds, widened for the stroke and the endings.
    """

    start: Point
    end: Point
    color: Color | None
    interior_color: Color | None
    border: Border
    line_endings: tuple[LineEnding, LineEnding]
    opacity: float
    blend_mode: BlendMode | None
    contents: str | None

    def __init__(  # noqa: PLR0913 - the annotation's style options.
        self,
        start: Point | Sequence[float],
        end: Point | Sequence[float],
        *,
        color: Color | None = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        interior_color: Color | None = None,
        border: Border = Border(),  # noqa: B008 - Border is immutable.
        line_endings: tuple[LineEnding, LineEnding] = (LineEnding.NONE, LineEnding.NONE),
        opacity: float = 1.0,
        blend_mode: BlendMode | None = None,
        contents: str | None = None,
    ) -> None:
        """Validate the specification."""
        self._init_drawn(color, interior_color, border, opacity, blend_mode, contents)
        object.__setattr__(self, "start", Point(*_point(start)))
        object.__setattr__(self, "end", Point(*_point(end)))
        object.__setattr__(self, "line_endings", _line_endings(line_endings))

    def _payload(self) -> dict[str, Any]:
        return self._drawn_spec("Line", [[tuple(self.start), tuple(self.end)]])


@dataclass(frozen=True, slots=True, init=False)
class Polygon(_Drawn):
    """A closed polygon through two or more display points, as ``page.annotations.add`` takes it.

    ``color`` strokes the outline and ``interior_color`` fills it; ``clouds``
    from 0 to 2 draws the outline as a cloud whose bumps grow with it. One
    annotation holds at most 65,536 points.
    """

    vertices: tuple[Point, ...]
    color: Color | None
    interior_color: Color | None
    border: Border
    clouds: float
    opacity: float
    blend_mode: BlendMode | None
    contents: str | None

    def __init__(  # noqa: PLR0913 - the annotation's style options.
        self,
        vertices: Iterable[Point | Sequence[float]],
        *,
        color: Color | None = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        interior_color: Color | None = None,
        border: Border = Border(),  # noqa: B008 - Border is immutable.
        clouds: float = 0,
        opacity: float = 1.0,
        blend_mode: BlendMode | None = None,
        contents: str | None = None,
    ) -> None:
        """Validate the specification; the contents are bounded before the points are read."""
        self._init_drawn(color, interior_color, border, opacity, blend_mode, contents)
        object.__setattr__(self, "clouds", _clouds(clouds))
        object.__setattr__(self, "vertices", _points(vertices, "vertices", 2, [0]))

    def _payload(self) -> dict[str, Any]:
        return self._drawn_spec("Polygon", [[tuple(point) for point in self.vertices]])


@dataclass(frozen=True, slots=True, init=False)
class PolyLine(_Drawn):
    """An open line through two or more display points, as ``page.annotations.add`` takes it.

    The fields are those of :class:`LineAnnotation`, with ``line_endings`` at the
    first and last points; one annotation holds at most 65,536 points.
    """

    vertices: tuple[Point, ...]
    color: Color | None
    interior_color: Color | None
    border: Border
    line_endings: tuple[LineEnding, LineEnding]
    opacity: float
    blend_mode: BlendMode | None
    contents: str | None

    def __init__(  # noqa: PLR0913 - the annotation's style options.
        self,
        vertices: Iterable[Point | Sequence[float]],
        *,
        color: Color | None = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        interior_color: Color | None = None,
        border: Border = Border(),  # noqa: B008 - Border is immutable.
        line_endings: tuple[LineEnding, LineEnding] = (LineEnding.NONE, LineEnding.NONE),
        opacity: float = 1.0,
        blend_mode: BlendMode | None = None,
        contents: str | None = None,
    ) -> None:
        """Validate the specification; the contents are bounded before the points are read."""
        self._init_drawn(color, interior_color, border, opacity, blend_mode, contents)
        object.__setattr__(self, "line_endings", _line_endings(line_endings))
        object.__setattr__(self, "vertices", _points(vertices, "vertices", 2, [0]))

    def _payload(self) -> dict[str, Any]:
        return self._drawn_spec("PolyLine", [[tuple(point) for point in self.vertices]])


@dataclass(frozen=True, slots=True, init=False)
class Ink(_Drawn):
    """Freehand strokes, each through one or more display points, as ``page.annotations.add`` takes it.

    ``color`` draws every stroke with round caps and joins at the ``border``
    width; one annotation holds at most 65,536 points in all.
    """

    strokes: tuple[tuple[Point, ...], ...]
    color: Color | None
    border: Border
    opacity: float
    blend_mode: BlendMode | None
    contents: str | None

    def __init__(  # noqa: PLR0913 - the annotation's style options.
        self,
        strokes: Iterable[Iterable[Point | Sequence[float]]],
        *,
        color: Color | None = Color(1.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
        border: Border = Border(),  # noqa: B008 - Border is immutable.
        opacity: float = 1.0,
        blend_mode: BlendMode | None = None,
        contents: str | None = None,
    ) -> None:
        """Validate the specification; the contents are bounded before the points are read."""
        self._init_drawn(color, None, border, opacity, blend_mode, contents)
        if isinstance(strokes, (str, bytes)) or not isinstance(strokes, Iterable):
            msg = f"strokes must be a sequence of point sequences: {strokes!r}"
            raise TypeError(msg)
        budget = [0]
        validated = tuple(_points(stroke, "a stroke", 1, budget) for stroke in strokes)
        if not validated:
            msg = "strokes must hold at least one stroke"
            raise ValueError(msg)
        object.__setattr__(self, "strokes", validated)

    def _payload(self) -> dict[str, Any]:
        return self._drawn_spec("Ink", [[tuple(point) for point in stroke] for stroke in self.strokes])


#: The specifications that ``page.annotations.add`` takes.
AnnotationSpecification: TypeAlias = (
    Highlight
    | Underline
    | StrikeOut
    | Squiggly
    | Note
    | FreeText
    | Stamp
    | Caret
    | FileAttachment
    | Redaction
    | Square
    | Circle
    | LineAnnotation
    | Polygon
    | PolyLine
    | Ink
)
_SPECIFICATIONS = (
    Highlight,
    Underline,
    StrikeOut,
    Squiggly,
    Note,
    FreeText,
    Stamp,
    Caret,
    FileAttachment,
    Redaction,
    Square,
    Circle,
    LineAnnotation,
    Polygon,
    PolyLine,
    Ink,
)


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

    def add(self, specification: AnnotationSpecification) -> Annotation:
        """Add the annotation that ``specification`` describes, with its appearance, and return it."""
        if not isinstance(specification, _SPECIFICATIONS):
            msg = f"page.annotations.add takes an annotation specification: {type(specification).__name__}"
            raise TypeError(msg)
        page = self._page
        xref = page._document._doc.add_annotation(page._page_number(), specification._payload())
        # The handle finds the new annotation by its object number.
        return Annotation(page, xref, 0)

    def __repr__(self) -> str:
        """Return ``Annotations([...])`` with the page's annotations."""
        return f"Annotations({list(self)!r})"


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (
    Annotation,
    AnnotationFile,
    AttachmentIcon,
    FileAttachment,
    Annotations,
    AnnotationType,
    AnnotationFlags,
    Border,
    BorderStyle,
    Caret,
    Circle,
    FreeText,
    Highlight,
    Ink,
    LineAnnotation,
    LineEnding,
    Note,
    NoteIcon,
    Polygon,
    PolyLine,
    Redaction,
    Square,
    Squiggly,
    Stamp,
    StampIcon,
    StrikeOut,
    Underline,
):
    _public.__module__ = "pylopdf"
