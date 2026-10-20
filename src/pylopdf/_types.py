"""Public value types, type aliases, flags, and constants."""

from __future__ import annotations

import enum
from typing import Literal, NamedTuple, TypeAlias, TypedDict

from pylopdf._geometry import Point

# Link kinds with pymupdf-compatible values.
LINK_NONE = 0
LINK_GOTO = 1
LINK_URI = 2
LINK_LAUNCH = 3
LINK_NAMED = 4
LINK_GOTOR = 5

# Text alignment values compatible with pymupdf.
TEXT_ALIGN_LEFT = 0
TEXT_ALIGN_CENTER = 1
TEXT_ALIGN_RIGHT = 2
TEXT_ALIGN_JUSTIFY = 3


class Permissions(enum.IntFlag):
    """Encrypted-PDF permission flags combined with ``|`` for ``save``.

    Values correspond to the ``/P`` bit positions in the PDF specification.
    """

    PRINT = 1 << 2
    MODIFY = 1 << 3
    COPY = 1 << 4
    ANNOTATE = 1 << 5
    FILL_FORMS = 1 << 8
    COPY_FOR_ACCESSIBILITY = 1 << 9
    ASSEMBLE = 1 << 10
    PRINT_HIGH_QUALITY = 1 << 11
    ALL = PRINT | MODIFY | COPY | ANNOTATE | FILL_FORMS | COPY_FOR_ACCESSIBILITY | ASSEMBLE | PRINT_HIGH_QUALITY


class Rect(NamedTuple):
    """A rectangle ``(x0, y0, x1, y1)`` in the coordinate space of its API."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        """Return the width, ``x1 - x0``."""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """Return the height, ``y1 - y0``."""
        return self.y1 - self.y0


#: One get_text("words") item: (x0, y0, x1, y1, word, block, line, word index).
WordEntry: TypeAlias = tuple[float, float, float, float, str, int, int, int]
#: One get_text("blocks") item: (x0, y0, x1, y1, text, block, type=0).
BlockEntry: TypeAlias = tuple[float, float, float, float, str, int, int]
#: Clockwise correction applied to rendered OCR input.
OcrRotation: TypeAlias = Literal[0, 90, 180, 270]
#: Script or locale used to select an optional bundled generation font.
FontLanguage: TypeAlias = Literal["ar", "he", "hi", "ja", "ko", "th", "zh-CN", "zh-TW"]
#: Locale slot used for non-embedded Adobe CJK font fallback.
CjkFontLanguage: TypeAlias = Literal["ja", "ko", "zh-CN", "zh-TW"]
#: One vector command: a line or a cubic Bézier in display coordinates.
DrawingItem: TypeAlias = tuple[Literal["l"], Point, Point] | tuple[Literal["c"], Point, Point, Point, Point]


class TextSpan(TypedDict):
    """One styled text run in :class:`TextLine`."""

    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    size: float
    font: str
    flags: int
    text: str


class TextLine(TypedDict):
    """One positioned line in :class:`TextBlock`."""

    bbox: tuple[float, float, float, float]
    wmode: int
    dir: tuple[float, float]
    spans: list[TextSpan]


class TextBlock(TypedDict):
    """One text block returned by ``get_text("dict")``."""

    number: int
    type: int
    bbox: tuple[float, float, float, float]
    lines: list[TextLine]


class TextPage(TypedDict):
    """Nested positioned layout returned by ``get_text("dict")``."""

    width: float
    height: float
    blocks: list[TextBlock]


class TextChar(TypedDict):
    """One positioned Unicode glyph returned by ``get_text("rawdict")``.

    ``c`` preserves the glyph's complete Unicode mapping, which can contain
    several characters. ``origin`` is its baseline point. ``bbox`` uses the
    existing advance and font-size approximation, not the glyph's outline.
    Both geometries use rotation-resolved, top-left-origin display coordinates.
    """

    c: str
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]


class RawTextSpan(TypedDict):
    """One styled glyph run, with ``chars`` replacing :class:`TextSpan` text."""

    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    size: float
    font: str
    flags: int
    chars: list[TextChar]


class RawTextLine(TypedDict):
    """One positioned line with the same direction heuristics as :class:`TextLine`."""

    bbox: tuple[float, float, float, float]
    wmode: int
    dir: tuple[float, float]
    spans: list[RawTextSpan]


class RawTextBlock(TypedDict):
    """One text-only block returned by ``get_text("rawdict")``."""

    number: int
    type: int
    bbox: tuple[float, float, float, float]
    lines: list[RawTextLine]


class RawTextPage(TypedDict):
    """Nested glyph layout returned by ``get_text("rawdict")``.

    Geometry is in display points, with page rotation and CropBox offsets
    resolved. Reading order follows the existing extraction heuristics.
    """

    width: float
    height: float
    blocks: list[RawTextBlock]


class ImageInfo(TypedDict):
    """One image returned by :meth:`Page.get_images`."""

    width: int
    height: int
    bbox: Rect
    ext: Literal["jpeg", "png"]
    image: bytes


class ImageCompressionResult(TypedDict):
    """Summary returned by :meth:`Document.compress_images`."""

    considered: int
    rewritten: int
    skipped: int
    bytes_before: int
    bytes_after: int
    bytes_saved: int


class DrawingInfo(TypedDict):
    """One vector paint operation returned by :meth:`Page.get_drawings`.

    Keys follow the common pymupdf path dictionary where practical. A combined
    fill-stroke operator has ``type="fs"``. Pattern paints have no single RGB
    color or opacity and therefore expose ``None`` for those values.
    """

    rect: Rect
    type: Literal["f", "s", "fs"]
    items: list[DrawingItem]
    closePath: bool
    color: tuple[float, float, float] | None
    fill: tuple[float, float, float] | None
    stroke_opacity: float | None
    fill_opacity: float | None
    even_odd: bool | None
    width: float | None
    lineCap: tuple[int, int, int] | None
    lineJoin: int | None
    dashes: str | None


class AnnotationInfo(TypedDict):
    """One annotation returned by :meth:`Page.annots`."""

    type: str
    rect: Rect
    contents: str | None
    uri: str | None


_LinkRequired = TypedDict("_LinkRequired", {"kind": int, "from": Rect})


class LinkInfo(_LinkRequired, total=False):
    """One link returned by :meth:`Page.get_links`.

    Keys other than ``kind`` and ``from`` depend on ``kind``.
    """

    uri: str | None
    page: int
    to: Point
    zoom: float
    nameddest: str
    file: str | None
    name: str | None


FormFieldType: TypeAlias = Literal[
    "text",
    "checkbox",
    "radio",
    "button",
    "combobox",
    "listbox",
    "signature",
    "unknown",
]


class FormFieldInfo(TypedDict):
    """One AcroForm field returned by :meth:`Document.get_form_fields`."""

    name: str
    type: FormFieldType
    value: str | None


class PageLabelInfo(TypedDict):
    """One normalized page-label range returned by ``get_page_labels``."""

    startpage: int
    style: str
    prefix: str
    firstpagenum: int


class _PageLabelStart(TypedDict):
    """Required part of :class:`PageLabelSpec`."""

    startpage: int


class PageLabelSpec(_PageLabelStart, total=False):
    """One input range for :meth:`Document.set_page_labels`."""

    style: str
    prefix: str
    firstpagenum: int


class DocumentMetadata(TypedDict):
    """Normalized metadata returned by :attr:`Document.metadata`."""

    title: str
    author: str
    subject: str
    keywords: str
    creator: str
    producer: str
    creationDate: str
    modDate: str
    format: str


class MetadataUpdate(TypedDict, total=False):
    """Writable subset accepted by :meth:`Document.set_metadata`."""

    title: str
    author: str
    subject: str
    keywords: str
    creator: str
    producer: str
    creationDate: str
    modDate: str


class MetadataProbe(DocumentMetadata):
    """Metadata plus cheap structural facts returned by :func:`peek_metadata`."""

    page_count: int
    encrypted: bool
    repaired: bool


class DocumentComplexity(TypedDict):
    """Cheap structural facts that require no stream decoding or rendering."""

    page_count: int
    object_count: int
    stream_count: int
    encoded_stream_bytes: int
    max_object_depth: int


class OcrWord(TypedDict):
    """One OCR result in rotation-resolved page display coordinates."""

    bbox: Rect
    text: str
    confidence: float


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (
    Permissions,
    Point,
    Rect,
    TextSpan,
    TextLine,
    TextBlock,
    TextPage,
    TextChar,
    RawTextSpan,
    RawTextLine,
    RawTextBlock,
    RawTextPage,
    ImageInfo,
    ImageCompressionResult,
    DrawingInfo,
    AnnotationInfo,
    LinkInfo,
    FormFieldInfo,
    PageLabelInfo,
    PageLabelSpec,
    DocumentMetadata,
    MetadataUpdate,
    MetadataProbe,
    DocumentComplexity,
    OcrWord,
):
    _public.__module__ = "pylopdf"
