"""Public value types, type aliases, flags, and constants."""

from __future__ import annotations

import enum
from typing import Literal, NamedTuple, TypeAlias, TypedDict

from pylopdf._geometry import Matrix, Point, Rect


class LinkKind(enum.IntEnum):
    """Link kinds; the members are pymupdf's ``LINK_*`` values."""

    NONE = 0
    GOTO = 1
    URI = 2
    LAUNCH = 3
    NAMED = 4
    GOTOR = 5


LINK_NONE = LinkKind.NONE
LINK_GOTO = LinkKind.GOTO
LINK_URI = LinkKind.URI
LINK_LAUNCH = LinkKind.LAUNCH
LINK_NAMED = LinkKind.NAMED
LINK_GOTOR = LinkKind.GOTOR


class TextAlign(enum.IntEnum):
    """Text alignment; the members are pymupdf's ``TEXT_ALIGN_*`` values."""

    LEFT = 0
    CENTER = 1
    RIGHT = 2
    JUSTIFY = 3


TEXT_ALIGN_LEFT = TextAlign.LEFT
TEXT_ALIGN_CENTER = TextAlign.CENTER
TEXT_ALIGN_RIGHT = TextAlign.RIGHT
TEXT_ALIGN_JUSTIFY = TextAlign.JUSTIFY


class Permissions(enum.IntFlag):
    """What an encrypted document permits the user password, combined with ``|``.

    :attr:`Document.permissions` reports them and ``Encryption(permissions=)``
    sets them. The values are the ``/P`` bits of ISO 32000-2 Table 22, and
    ``ALL`` combines every member.
    """

    PRINT = 1 << 2
    MODIFY = 1 << 3
    COPY = 1 << 4
    ANNOTATE = 1 << 5
    FORM = 1 << 8
    ACCESSIBILITY = 1 << 9
    ASSEMBLE = 1 << 10
    PRINT_HQ = 1 << 11
    ALL = PRINT | MODIFY | COPY | ANNOTATE | FORM | ACCESSIBILITY | ASSEMBLE | PRINT_HQ


PDF_PERM_PRINT = Permissions.PRINT
PDF_PERM_MODIFY = Permissions.MODIFY
PDF_PERM_COPY = Permissions.COPY
PDF_PERM_ANNOTATE = Permissions.ANNOTATE
PDF_PERM_FORM = Permissions.FORM
PDF_PERM_ACCESSIBILITY = Permissions.ACCESSIBILITY
PDF_PERM_ASSEMBLE = Permissions.ASSEMBLE
PDF_PERM_PRINT_HQ = Permissions.PRINT_HQ


class Access(enum.IntEnum):
    """What a password opened: the user's restricted access, or the owner's full access."""

    USER = 1
    OWNER = 2


class OutlineEntry(NamedTuple):
    """One outline (bookmark) item of :meth:`Document.get_outline`.

    ``page`` is zero-based, unlike pymupdf's ``get_toc()`` page numbers.
    """

    level: int
    title: str
    page: int


#: Clockwise correction applied to rendered OCR input.
OcrRotation: TypeAlias = Literal[0, 90, 180, 270]
#: Script or locale used to select an optional bundled generation font.
FontLanguage: TypeAlias = Literal["ar", "he", "hi", "ja", "ko", "th", "zh-CN", "zh-TW"]
#: Locale slot used for non-embedded Adobe CJK font fallback.
CjkFontLanguage: TypeAlias = Literal["ja", "ko", "zh-CN", "zh-TW"]
#: One vector command: a line or a cubic Bézier in display coordinates.
DrawingItem: TypeAlias = tuple[Literal["l"], Point, Point] | tuple[Literal["c"], Point, Point, Point, Point]


class ImageItem(NamedTuple):
    """One image XObject of :meth:`Page.get_images`, in pymupdf's tuple order."""

    xref: int
    smask: int
    width: int
    height: int
    bpc: int
    colorspace: str
    alt_colorspace: str
    name: str
    filter: str


class ImageItemFull(NamedTuple):
    """One item of ``get_images(full=True)``: :class:`ImageItem` plus ``referencer``.

    ``referencer`` is the xref of the Form XObject whose resources list the
    image, or 0 when the page's own resources do.
    """

    xref: int
    smask: int
    width: int
    height: int
    bpc: int
    colorspace: str
    alt_colorspace: str
    name: str
    filter: str
    referencer: int


_ImageInfoRequired = TypedDict(
    "_ImageInfoRequired",
    {
        "number": int,
        "bbox": Rect,
        "transform": Matrix,
        "width": int,
        "height": int,
        "colorspace": int,
        "cs-name": str,
        "xres": int,
        "yres": int,
        "bpc": int,
        "size": int,
        "has-mask": bool,
    },
)


class ImageInfo(_ImageInfoRequired, total=False):
    """One drawn image returned by :meth:`Page.get_image_info`, with pymupdf's keys.

    ``transform`` maps the unit square to ``bbox``. ``xref`` is present with
    ``xrefs=True`` and is 0 for an inline image or an image mask.
    """

    xref: int


ExtractedImage = TypedDict(
    "ExtractedImage",
    {
        "ext": Literal["jpeg", "png"],
        "smask": int,
        "width": int,
        "height": int,
        "colorspace": int,
        "cs-name": str,
        "bpc": int,
        "xres": int,
        "yres": int,
        "image": bytes,
    },
)
ExtractedImage.__doc__ = """The image that :meth:`Document.extract_image` returns, with pymupdf's keys."""


class ImageCompressionResult(TypedDict):
    """Summary returned by :meth:`Document.rewrite_images`."""

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
    seqno: int


_LinkRequired = TypedDict("_LinkRequired", {"kind": LinkKind, "from": Rect, "xref": int, "id": str})


class LinkInfo(_LinkRequired, total=False):
    """One link returned by :meth:`Page.get_links`.

    ``xref`` is the object number of the link annotation, or 0 for a direct
    dictionary, and ``id`` its ``/NM`` name, or ``""``. Keys other than
    ``kind``, ``from``, ``xref``, and ``id`` depend on ``kind``.
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


class _InfoMetadata(TypedDict):
    """The PDF version and the Info text fields, as pymupdf names them."""

    format: str
    title: str
    author: str
    subject: str
    keywords: str
    creator: str
    producer: str
    creationDate: str
    modDate: str


class DocumentMetadata(_InfoMetadata):
    """Metadata returned by :attr:`Document.metadata`, with pymupdf's keys.

    ``encryption`` describes the encryption the document was opened with, as
    in ``"Standard V5 R6 256-bit AES"``, or is ``None``.
    """

    trapped: str
    encryption: str | None


class MetadataUpdate(TypedDict, total=False):
    """Keys accepted by :meth:`Document.set_metadata`.

    ``format`` and ``encryption`` are read-only and ignored, so that
    ``doc.set_metadata(doc.metadata)`` works.
    """

    title: str | None
    author: str | None
    subject: str | None
    keywords: str | None
    creator: str | None
    producer: str | None
    creationDate: str | None
    modDate: str | None
    trapped: str | None
    format: str | None
    encryption: str | None


class MarkInfo(TypedDict, total=False):
    """The catalog's ``/MarkInfo`` flags, as :attr:`Document.markinfo` returns them.

    The keys are the PDF names. A document without ``/MarkInfo`` returns an
    empty dictionary; otherwise all three keys are present.
    """

    Marked: bool
    UserProperties: bool
    Suspects: bool


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
    OutlineEntry,
    LinkKind,
    TextAlign,
    Permissions,
    Access,
    ImageItem,
    ImageItemFull,
    ImageInfo,
    ExtractedImage,
    ImageCompressionResult,
    DrawingInfo,
    LinkInfo,
    FormFieldInfo,
    PageLabelInfo,
    PageLabelSpec,
    DocumentMetadata,
    MetadataUpdate,
    MarkInfo,
    DocumentComplexity,
    OcrWord,
):
    _public.__module__ = "pylopdf"
