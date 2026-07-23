"""PDF editing and rendering backed by Rust.

The :class:`Document` API follows pymupdf conventions. lopdf handles editing
and hayro handles rendering; both are pure Rust under permissive licenses.
"""

from __future__ import annotations

import enum
import functools
import math
import os
import warnings as _warnings
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, overload

from pylopdf import _markdown
from pylopdf.pylopdf_core import PasswordError, PdfError, Pixmap, _Document

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from types import TracebackType
    from typing import Any, Literal, Self

    #: One get_text("words") item: (x0, y0, x1, y1, word, block, line, word index).
    WordEntry = tuple[float, float, float, float, str, int, int, int]
    #: One get_text("blocks") item: (x0, y0, x1, y1, text, block, type=0).
    BlockEntry = tuple[float, float, float, float, str, int, int]

__version__ = "0.9.0"
__all__ = [
    "LINK_GOTO",
    "LINK_GOTOR",
    "LINK_LAUNCH",
    "LINK_NAMED",
    "LINK_NONE",
    "LINK_URI",
    "Document",
    "DocumentClosedError",
    "EncryptedDocumentError",
    "Page",
    "PasswordError",
    "PdfError",
    "Permissions",
    "Point",
    "Rect",
    "StalePageError",
    "open",
    "peek_metadata",
]
__all__ += ["Pixmap", "PylopdfWarning"]

# Link kinds with pymupdf-compatible values.
LINK_NONE = 0
LINK_GOTO = 1
LINK_URI = 2
LINK_LAUNCH = 3
LINK_NAMED = 4
LINK_GOTOR = 5


class PylopdfWarning(UserWarning):
    """A hayro rendering or extraction warning, such as an unresolved font."""


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


class DocumentClosedError(PdfError):
    """An operation on a closed :class:`Document`."""


class EncryptedDocumentError(PdfError):
    """An operation on an undecrypted PDF; provide a password or authenticate."""


class StalePageError(PdfError):
    """Use of a stale :class:`Page` after a structural document change.

    Fetch the page again with ``doc[i]``.
    """


class Point(NamedTuple):
    """A point ``(x, y)`` in display coordinates, such as a link destination."""

    x: float
    y: float


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


#: Portrait A4 in PDF units, used for damaged PDFs without a MediaBox.
_DEFAULT_MEDIABOX = (0.0, 0.0, 210.0 * 72.0 / 25.4, 297.0 * 72.0 / 25.4)


@functools.cache
def _bundled_cjk_fonts() -> tuple[tuple[str, bytes], ...]:
    """Load bundled fonts when ``pylopdf[cjk]`` is installed."""
    try:
        import pylopdf_fonts_cjk  # noqa: PLC0415  # Lazy optional dependency.
    except ImportError:
        return ()
    return (
        ("sans", pylopdf_fonts_cjk.sans_path().read_bytes()),
        ("serif", pylopdf_fonts_cjk.serif_path().read_bytes()),
    )


#: Maximum R/G/B/A component value.
_COLOR_MAX = 255

#: Largest absolute value representable as a finite lopdf PDF real (f32).
_FLOAT32_MAX = float.fromhex("0x1.fffffep+127")


def _normalize_background(
    background: tuple[int, int, int] | tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    """Validate ``render_page`` background and normalize it to an RGBA tuple."""
    if background is None:
        return None
    match background:
        case (r, g, b):
            rgba = (r, g, b, _COLOR_MAX)
        case (r, g, b, a):
            rgba = (r, g, b, a)
        case _:
            msg = f"background must be an (R, G, B) or (R, G, B, A) tuple: {background!r}"
            raise ValueError(msg)
    for value in rgba:
        if not isinstance(value, int) or not 0 <= value <= _COLOR_MAX:
            msg = f"each background component must be an integer in 0-{_COLOR_MAX}: {background!r}"
            raise ValueError(msg)
    return rgba


def _validate_rect(rect: Sequence[float], *, name: str = "rect") -> tuple[float, float, float, float]:
    """Validate an ``(x0, y0, x1, y1)`` rectangle and return float values."""
    try:
        x0, y0, x1, y1 = (float(v) for v in rect)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be 4 numbers (x0, y0, x1, y1): {rect!r}"
        raise ValueError(msg) from exc
    if not all(math.isfinite(v) and abs(v) <= _FLOAT32_MAX for v in (x0, y0, x1, y1)) or x0 >= x1 or y0 >= y1:
        msg = f"{name} must be a finite rect within PDF real-number range with x0 < x1 and y0 < y1: {rect!r}"
        raise ValueError(msg)
    return x0, y0, x1, y1


def _validate_unit_rgb(color: Sequence[float]) -> tuple[float, float, float]:
    """Validate an ``(r, g, b)`` color in the range 0–1."""
    try:
        red, green, blue = (float(c) for c in color)
    except (TypeError, ValueError) as exc:
        msg = f"color must be (r, g, b) in the range 0-1: {color!r}"
        raise ValueError(msg) from exc
    if not all(0.0 <= c <= 1.0 for c in (red, green, blue)):
        msg = f"color must be (r, g, b) in the range 0-1: {color!r}"
        raise ValueError(msg)
    return red, green, blue


def _read_image_source(filename: str | os.PathLike[str] | None, stream: bytes | None) -> bytes:
    """Read image bytes from exactly one of ``filename`` and ``stream``."""
    if filename is not None:
        if stream is not None:
            msg = "filename and stream cannot both be specified"
            raise ValueError(msg)
        return Path(filename).read_bytes()
    if stream is None:
        msg = "specify either filename or stream"
        raise ValueError(msg)
    return bytes(stream)


#: Map pymupdf-style insert_text aliases to Standard 14 font names.
_BASE14_FONTS: dict[str, str] = {
    "helv": "Helvetica",
    "hebo": "Helvetica-Bold",
    "heit": "Helvetica-Oblique",
    "hebi": "Helvetica-BoldOblique",
    "cour": "Courier",
    "cobo": "Courier-Bold",
    "coit": "Courier-Oblique",
    "cobi": "Courier-BoldOblique",
    "tiro": "Times-Roman",
    "tibo": "Times-Bold",
    "tiit": "Times-Italic",
    "tibi": "Times-BoldItalic",
    "symb": "Symbol",
    "zadb": "ZapfDingbats",
}

#: Standard fonts that use built-in encoding rather than WinAnsi.
_SYMBOLIC_FONTS = frozenset({"symb", "zadb"})


#: Page-label numbering styles (`/S`); an empty value means prefix only.
_PAGE_LABEL_STYLES = frozenset({"", "D", "R", "r", "A", "a"})


def _int_to_roman(n: int) -> str:
    """Convert a positive integer to uppercase Roman numerals."""
    pairs = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    out = []
    for value, symbol in pairs:
        count, n = divmod(n, value)
        out.append(symbol * count)
    return "".join(out)


def _int_to_letters(n: int) -> str:
    """Convert a positive integer to PDF's A..Z, AA..ZZ, AAA... form."""
    letter = chr(ord("A") + (n - 1) % 26)
    return letter * ((n - 1) // 26 + 1)


def _format_page_label(style: str, prefix: str, number: int) -> str:
    """Build the display label for one page-label rule."""
    match style:
        case "D":
            digits = str(number)
        case "R":
            digits = _int_to_roman(number)
        case "r":
            digits = _int_to_roman(number).lower()
        case "A":
            digits = _int_to_letters(number)
        case "a":
            digits = _int_to_letters(number).lower()
        case _:
            digits = ""
    return prefix + digits


#: Map pymupdf-style Python metadata keys to PDF Info keys.
_METADATA_KEYS: dict[str, str] = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creator": "Creator",
    "producer": "Producer",
    "creationDate": "CreationDate",
    "modDate": "ModDate",
}


class Page:
    """A view of one page in a document, obtained through ``doc[i]``.

    Adding, deleting, or reordering pages invalidates existing views. Using an
    invalidated view raises :class:`StalePageError`; fetch it again with
    ``doc[i]``.
    """

    def __init__(self, document: Document, pno: int) -> None:
        """Initialize a view for ``Document.__getitem__``; do not call directly."""
        self._document = document
        self._pno = pno
        self._generation = document._generation

    @property
    def number(self) -> int:
        """Return the zero-based page number."""
        return self._pno

    @property
    def parent(self) -> Document:
        """Return the parent document."""
        return self._document

    def _page_number(self) -> int:
        """Validate the view and return lopdf's one-based page number."""
        doc = self._document
        doc._ensure_open()
        if self._generation != doc._generation:
            msg = (
                f"page {self._pno} was invalidated by a document structure change; fetch it again via doc[{self._pno}]"
            )
            raise StalePageError(msg)
        return self._pno + 1

    @property
    def rotation(self) -> int:
        """Return the resolved display rotation: 0, 90, 180, or 270."""
        return self._document._doc.get_page_rotation(self._page_number())

    def set_rotation(self, rotation: int) -> None:
        """Set rotation in multiples of 90, normalized to the range 0–359."""
        if rotation % 90 != 0:
            msg = f"rotation must be a multiple of 90: {rotation!r}"
            raise ValueError(msg)
        self._document._doc.set_page_rotation(self._page_number(), rotation % 360)

    @property
    def mediabox(self) -> Rect:
        """Return the resolved MediaBox in PDF page-box coordinates, or A4 when absent."""
        box = self._document._doc.get_page_box(self._page_number(), "MediaBox")
        return Rect(*(box if box is not None else _DEFAULT_MEDIABOX))

    @property
    def cropbox(self) -> Rect:
        """Return the CropBox in PDF page-box coordinates, falling back to the MediaBox."""
        box = self._document._doc.get_page_box(self._page_number(), "CropBox")
        return Rect(*box) if box is not None else self.mediabox

    @property
    def rect(self) -> Rect:
        """Return the display rectangle with origin 0,0 and rotation resolved."""
        box = self.cropbox
        if self.rotation in (90, 270):
            return Rect(0.0, 0.0, box.height, box.width)
        return Rect(0.0, 0.0, box.width, box.height)

    def set_mediabox(self, rect: Sequence[float]) -> None:
        """Set the MediaBox as ``(x0, y0, x1, y1)``."""
        self._set_box("MediaBox", rect)

    def set_cropbox(self, rect: Sequence[float]) -> None:
        """Set the CropBox as ``(x0, y0, x1, y1)``."""
        self._set_box("CropBox", rect)

    def _set_box(self, key: str, rect: Sequence[float]) -> None:
        """Validate and set a page box."""
        x0, y0, x1, y1 = _validate_rect(rect, name=key)
        self._document._doc.set_page_box(self._page_number(), key, x0, y0, x1, y1)

    @overload
    def get_text(self, option: Literal["text"] = "text") -> str: ...
    @overload
    def get_text(self, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def get_text(self, option: Literal["blocks"]) -> list[BlockEntry]: ...
    @overload
    def get_text(self, option: Literal["dict"]) -> dict[str, Any]: ...
    def get_text(self, option: str = "text") -> str | list[WordEntry] | list[BlockEntry] | dict[str, Any]:
        """Extract page text or positioned layout data.

        ``option`` matches :meth:`Document.get_page_text`: ``"text"``,
        ``"words"``, ``"blocks"``, or ``"dict"``.
        """
        self._page_number()
        return self._document.get_page_text(self._pno, option)  # type: ignore[call-overload]

    def to_markdown(self) -> str:
        """Convert this page to Markdown.

        This is the single-page form of :meth:`Document.to_markdown`; heading
        sizes are inferred from this page alone.
        """
        self._page_number()
        return self._document.to_markdown(pages=[self._pno])

    def search_for(self, needle: str) -> list[Rect]:
        """Search page text case-insensitively.

        Return one :class:`Rect` per match. Search is line-based and does not
        detect matches spanning lines.
        """
        if not needle:
            msg = "needle must be at least 1 character"
            raise ValueError(msg)
        hits = self._document._doc.search_page(self._page_number(), needle)
        self._document._emit_warnings()
        return [Rect(*hit) for hit in hits]

    def get_images(self) -> list[dict[str, Any]]:
        """Extract images drawn on the page.

        Each item is a ``{"width", "height", "bbox", "ext", "image"}`` dict.
        An image filtered by DCTDecode alone, or by FlateDecode followed by
        DCTDecode, returns its JPEG payload with ``ext="jpeg"``. Other formats,
        including CCITT, JBIG2, and Flate, are decoded to PNG with
        ``ext="png"``. ``bbox`` is the drawn location as a top-left-origin
        :class:`Rect`.
        """
        raw = self._document._doc.extract_images(self._page_number())
        self._document._emit_warnings()
        return [
            {"width": width, "height": height, "bbox": Rect(*bbox), "ext": ext, "image": data}
            for width, height, bbox, ext, data in raw
        ]

    def insert_image(
        self,
        rect: Sequence[float],
        *,
        filename: str | os.PathLike[str] | None = None,
        stream: bytes | None = None,
        keep_proportion: bool = True,
        overlay: bool = True,
    ) -> None:
        """Draw an image into ``rect`` in top-left-origin display coordinates.

        JPEG is embedded unchanged through DCTDecode passthrough. PNG is decoded
        and embedded, preserving transparency as a soft mask. Convert other
        formats to JPEG or PNG with Pillow or a similar library. ``rect`` uses
        the same coordinate space as :meth:`search_for` and :meth:`get_text`, so
        search results can be used directly. ``keep_proportion`` centers the
        image while preserving its aspect ratio. ``overlay=False`` draws below
        existing content. Existing page content is never rewritten.
        """
        data = _read_image_source(filename, stream)
        x0, y0, x1, y1 = _validate_rect(rect)
        self._document._doc.insert_image(self._page_number(), (x0, y0, x1, y1), data, keep_proportion, overlay)

    def show_pdf_page(
        self,
        rect: Sequence[float],
        src: Document,
        pno: int = 0,
        *,
        keep_proportion: bool = True,
        overlay: bool = True,
    ) -> None:
        """Overlay page ``pno`` from ``src`` into ``rect`` as vector content.

        ``pno`` is zero-based and may be negative. This is the pymupdf-style
        primitive for watermarks, stamps, and letterheads. The source page is
        embedded as a Form XObject, preserving text, vectors, and embedded
        fonts. Overlaying a one-page typst PDF enables CJK watermarks, headers,
        and footers; see the README ecosystem recipe. Source rotation and
        CropBox are resolved visually to fit ``rect``. A document cannot overlay
        itself; clone it first with ``pylopdf.open(stream=doc.tobytes())``.
        """
        if src is self._document:
            msg = "cannot overlay pages from the same document (duplicate it first via open(stream=doc.tobytes()))"
            raise ValueError(msg)
        x0, y0, x1, y1 = _validate_rect(rect)
        src_number = src[pno]._page_number()
        self._document._doc.show_pdf_page(
            self._page_number(), (x0, y0, x1, y1), src._doc, src_number, keep_proportion, overlay
        )

    def insert_text(
        self,
        point: Sequence[float],
        text: str,
        *,
        fontsize: float = 11.0,
        fontname: str = "helv",
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        r"""Draw text at ``point``, the first line's baseline-left display point.

        ``fontname`` is a pymupdf-style Standard 14 alias: the ``"helv"``,
        ``"tiro"``, and ``"cour"`` families with ``bo``/``it`` variants, plus
        ``"symb"`` and ``"zadb"``. Fonts are not embedded and viewers provide
        the standard typeface. Text is limited to WinAnsi, roughly Latin-1; use
        the typst plus :meth:`show_pdf_page` ecosystem recipe for CJK. ``\n``
        starts a new line at 1.2 times ``fontsize``. Text remains visually
        upright on rotated pages. Loop over pages for headers, footers, page
        numbers, or Bates numbers.
        """
        try:
            x, y = (float(v) for v in point)
        except (TypeError, ValueError) as exc:
            msg = f"point must be 2 numbers (x, y): {point!r}"
            raise ValueError(msg) from exc
        if not (math.isfinite(x) and math.isfinite(y)):
            msg = f"point must have finite coordinates: {point!r}"
            raise ValueError(msg)
        if not (math.isfinite(fontsize) and fontsize > 0):
            msg = f"fontsize must be a positive number: {fontsize!r}"
            raise ValueError(msg)
        base_font = _BASE14_FONTS.get(fontname)
        if base_font is None:
            msg = f"fontname must be a standard-14 font abbreviation ({sorted(_BASE14_FONTS)}): {fontname!r}"
            raise ValueError(msg)
        red, green, blue = _validate_unit_rgb(color)
        if not text:
            msg = "text must be at least 1 character"
            raise ValueError(msg)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        try:
            lines = [line.encode("cp1252") for line in normalized.split("\n")]
        except UnicodeEncodeError as exc:
            msg = (
                "insert_text can only print WinAnsi (Latin-1-equivalent) characters. "
                "For CJK text such as Japanese, use the typst + show_pdf_page recipe "
                "(see the README's ecosystem integrations)"
            )
            raise ValueError(msg) from exc
        self._document._doc.insert_page_text(
            self._page_number(),
            (x, y),
            lines,
            base_font,
            fontname not in _SYMBOLIC_FONTS,
            float(fontsize),
            (red, green, blue),
        )

    def insert_ocr_text_layer(self, words: Iterable[Sequence[Any]]) -> None:
        """Insert OCR output as an invisible, searchable text layer.

        Each item in ``words`` begins with ``(x0, y0, x1, y1, text, ...)``;
        only the first five values are used. This accepts :meth:`get_text`
        ``"words"`` output and common OCR API results directly. Coordinates use
        top-left-origin display space. Text is not drawn and appears only in
        extraction and search. An Identity-H reference font with ToUnicode is
        used without embedding font data, so any language, including CJK, adds
        almost no file size. The primitive is engine-neutral and accepts cloud
        APIs, Tesseract, or any equivalent source.
        """
        payload: list[tuple[float, float, float, float, str]] = []
        for entry in words:
            x0, y0, x1, y1 = _validate_rect(entry[:4])
            text = str(entry[4])
            if text:
                payload.append((x0, y0, x1, y1, text))
        if not payload:
            msg = "words must contain at least one word with text"
            raise ValueError(msg)
        self._document._doc.insert_ocr_layer(self._page_number(), payload)

    def replace_text(self, search: str, replacement: str, *, default_char: str | None = None) -> int:
        """Replace text on the page and return the number of replacements.

        This is a thin wrapper over lopdf's constrained ``replace_partial_text``.
        It works only with simply encoded fonts such as WinAnsi, not CID/CJK
        fonts. Characters absent from the font become ``default_char`` (``"?"``
        by default). Widths are not recalculated, so differing text lengths may
        shift layout.
        """
        if not search:
            msg = "search must be at least 1 character"
            raise ValueError(msg)
        return self._document._doc.replace_text_on_page(self._page_number(), search, replacement, default_char)

    def get_label(self) -> str:
        """Return the display label, such as ``"iv"`` or ``"A-2"``, or empty."""
        pno = self._page_number() - 1
        applicable: dict[str, Any] | None = None
        for label in self._document.get_page_labels():
            if label["startpage"] <= pno:
                applicable = label
            else:
                break
        if applicable is None:
            return ""
        number = pno - applicable["startpage"] + applicable["firstpagenum"]
        return _format_page_label(applicable["style"], applicable["prefix"], number)

    def annots(self) -> list[dict[str, Any]]:
        """Read annotations on the page.

        Each item is a ``{"type", "rect", "contents", "uri"}`` dict.
        ``type`` is the PDF Subtype name, such as ``"Highlight"`` or
        ``"Link"``; ``rect`` is a display-coordinate :class:`Rect`;
        ``contents`` is annotation text; and ``uri`` is the URI action target or
        ``None``.
        """
        raw = self._document._doc.read_annotations(self._page_number())
        return [
            {"type": subtype, "rect": Rect(*rect), "contents": contents, "uri": uri}
            for subtype, rect, contents, uri in raw
        ]

    def get_links(self) -> list[dict[str, Any]]:
        """Read link annotations and resolve their destinations.

        Each item is a pymupdf-style dict with ``kind`` (for example
        :data:`LINK_GOTO`) and ``from`` (a display-coordinate :class:`Rect`).
        Additional keys depend on ``kind``:

        - ``LINK_URI``: ``uri``.
        - ``LINK_GOTO``: zero-based ``page`` or -1 when unresolved, plus
          optional ``to`` (:class:`Point`), ``zoom``, and ``nameddest``.
        - ``LINK_GOTOR`` / ``LINK_LAUNCH``: ``file`` and optional ``nameddest``.
        - ``LINK_NAMED``: action ``name``, such as ``NextPage``.

        GoTo named destinations resolve from both the ``/Names`` name tree and
        the legacy ``/Dests`` dictionary.
        """
        raw = self._document._doc.read_links(self._page_number())
        kind_map = {
            "uri": LINK_URI,
            "goto": LINK_GOTO,
            "gotor": LINK_GOTOR,
            "launch": LINK_LAUNCH,
            "named": LINK_NAMED,
        }
        links: list[dict[str, Any]] = []
        for kind, rect, uri, page, to, zoom, file, name in raw:
            link: dict[str, Any] = {
                "kind": kind_map.get(kind, LINK_NONE),
                "from": Rect(*rect),
            }
            if kind == "uri":
                link["uri"] = uri
            elif kind == "goto":
                link["page"] = page - 1 if page is not None else -1
                if to is not None:
                    link["to"] = Point(*to)
                if zoom is not None:
                    link["zoom"] = zoom
                if name is not None:
                    link["nameddest"] = name
            elif kind in ("gotor", "launch"):
                link["file"] = file
                if name is not None:
                    link["nameddest"] = name
            elif kind == "named":
                link["name"] = name
            links.append(link)
        return links

    def add_highlight_annot(
        self,
        rects: Sequence[float] | Sequence[Sequence[float]],
        *,
        color: tuple[float, float, float] = (1.0, 1.0, 0.0),
        opacity: float = 0.4,
        content: str | None = None,
    ) -> None:
        """Add one highlight annotation over one or more display rectangles.

        Pass :meth:`search_for` output directly to highlight search results.
        The annotation includes QuadPoints and an appearance stream using
        Multiply blending, so it looks consistent in pylopdf's renderer and
        other viewers. Multiple rectangles form one annotation. ``content`` is
        the popup annotation text.
        """
        seq = list(rects)
        if not seq:
            msg = "rects must contain at least one rect"
            raise ValueError(msg)
        rect_list = [seq] if isinstance(seq[0], (int, float)) else seq
        validated = [_validate_rect(r) for r in rect_list]  # type: ignore[arg-type]
        rgb = _validate_unit_rgb(color)
        if not (math.isfinite(opacity) and 0.0 < opacity <= 1.0):
            msg = f"opacity must be greater than 0 and at most 1: {opacity!r}"
            raise ValueError(msg)
        self._document._doc.add_highlight_annotation(self._page_number(), validated, rgb, float(opacity), content)

    def add_link_annot(self, rect: Sequence[float], uri: str) -> None:
        """Add a borderless URI link annotation over a display rectangle.

        This supports search-then-link workflows using :meth:`search_for`.
        For new documents, links are usually better created in typst; see the
        README ecosystem recipe.
        """
        if not uri:
            msg = "uri must be at least 1 character"
            raise ValueError(msg)
        x0, y0, x1, y1 = _validate_rect(rect)
        self._document._doc.add_link_annotation(self._page_number(), (x0, y0, x1, y1), uri)

    def get_pixmap(
        self,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
    ) -> Pixmap:
        """Render the page to a straight-alpha RGBA8 :class:`Pixmap`.

        Arguments match :meth:`Document.render_page`. The pixmap exposes
        ``width``, ``height``, ``stride``, ``n``, ``samples`` as bytes, and
        ``tobytes()`` as PNG. Convert it to NumPy with
        ``np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 4)``.
        """
        if dpi is not None:
            if scale != 1.0:
                msg = "scale and dpi cannot both be specified"
                raise ValueError(msg)
            scale = dpi / 72.0
        rgba = _normalize_background(background)
        page_number = self._page_number()
        document = self._document
        document._ensure_fallback_fonts()
        result = document._doc.render_page_pixmap(page_number, scale, rgba)
        document._emit_warnings()
        return result

    def render(
        self,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
    ) -> bytes:
        """Render the page to PNG with :meth:`Document.render_page` arguments."""
        self._page_number()
        return self._document.render_page(self._pno, scale, dpi=dpi, background=background)

    def render_svg(self) -> str:
        """Render the page to an SVG string."""
        self._page_number()
        return self._document.render_page_svg(self._pno)

    def __repr__(self) -> str:
        """Return a representation containing the page number and document."""
        return f"<Page {self._pno} of {self._document!r}>"


class Document:
    """A PDF document.

    Open from a path or byte string, or create an empty document without
    arguments. Documents are context managers and expose :class:`Page` objects
    through ``doc[i]`` and iteration.
    """

    def __init__(
        self,
        filename: str | os.PathLike[str] | None = None,
        stream: bytes | None = None,
        password: str | None = None,
        max_decompressed_size: int | None = None,
    ) -> None:
        """Open from exactly one of a file path and byte stream, or create empty.

        PDFs with an empty user password decrypt automatically. Otherwise pass
        ``password`` or call :meth:`authenticate` after opening.
        ``max_decompressed_size`` limits decompressed bytes per stream to defend
        against decompression bombs in untrusted PDFs; ``None`` is unlimited.
        Lazily decoded streams such as page content are validated during load,
        and filter chains that cannot be bounded safely are rejected.
        """
        if filename is not None and stream is not None:
            msg = "filename and stream cannot both be specified"
            raise ValueError(msg)
        if max_decompressed_size is not None and max_decompressed_size <= 0:
            msg = f"max_decompressed_size must be a positive integer: {max_decompressed_size!r}"
            raise ValueError(msg)
        path = None if filename is None else str(filename)
        self._max_decompressed_size = max_decompressed_size
        if stream is not None:
            doc = _Document.load_bytes(stream, None, max_decompressed_size)
            needs_pass = doc.is_encrypted()
            if needs_pass and password is not None:
                doc = _Document.load_bytes(stream, password, max_decompressed_size)
        elif path is not None:
            doc = _Document.load(path, None, max_decompressed_size)
            needs_pass = doc.is_encrypted()
            if needs_pass and password is not None:
                doc = _Document.load(path, password, max_decompressed_size)
        else:
            doc = _Document()
            needs_pass = False
        self._doc = doc
        self._closed = False
        self._fallback_configured = False
        # Structural generation; changes invalidate existing Page views.
        self._generation = 0
        # Whether opening initially required a password; remains true after auth.
        self._needs_pass = needs_pass
        # Retain input only while an undecrypted document may need reopening.
        self._source_path = path if self._doc.is_encrypted() else None
        self._source_bytes = stream if self._doc.is_encrypted() else None

    @property
    def needs_pass(self) -> bool:
        """Return whether opening required a password; remains true after auth."""
        self._ensure_not_closed()
        return self._needs_pass

    @property
    def is_encrypted(self) -> bool:
        """Return whether the document still requires authentication."""
        self._ensure_not_closed()
        return self._doc.is_encrypted()

    def authenticate(self, password: str) -> int:
        """Authenticate and decrypt with a password.

        Return pymupdf-compatible codes: 0 for failure, 1 when authentication is
        unnecessary, 2 for a matching user password, 4 for a matching owner
        password, and 6 when both match.
        """
        self._ensure_not_closed()
        if not self._doc.is_encrypted():
            return 1
        code = 0
        if self._doc.authenticate_user_password(password):
            code |= 2
        if self._doc.authenticate_owner_password(password):
            code |= 4
        if code == 0:
            return 0
        # Reopen with the password so objects inside object streams are readable.
        if self._source_path is not None:
            self._doc = _Document.load(self._source_path, password, self._max_decompressed_size)
        elif self._source_bytes is not None:
            self._doc = _Document.load_bytes(self._source_bytes, password, self._max_decompressed_size)
        self._source_path = None
        self._source_bytes = None
        return code

    @property
    def page_count(self) -> int:
        """Return the number of pages."""
        self._ensure_open()
        return self._doc.page_count()

    def __len__(self) -> int:
        """Return the number of pages."""
        return self.page_count

    def __getitem__(self, pno: int) -> Page:
        """Return a page by zero-based index; negative values count from the end."""
        return Page(self, self._normalize_pno(pno))

    def load_page(self, pno: int) -> Page:
        """Return ``doc[pno]`` through the pymupdf-compatible name."""
        return self[pno]

    def __iter__(self) -> Iterator[Page]:
        """Iterate over every page in order."""
        for pno in range(self.page_count):
            yield self[pno]

    def _bump_generation(self) -> None:
        """Record a structural change and invalidate existing page views."""
        self._generation += 1

    @property
    def metadata(self) -> dict[str, str]:
        """Return title, author, subject, keywords, dates, producer, and format."""
        self._ensure_open()
        raw = self._doc.get_metadata()
        result = {key: raw.get(pdf_key, "") for key, pdf_key in _METADATA_KEYS.items()}
        result["format"] = f"PDF {self._doc.version()}"
        return result

    def set_metadata(self, metadata: dict[str, str]) -> None:
        """Set metadata, deleting entries whose values are empty strings.

        Keys match :attr:`metadata`, except the read-only ``format`` key.
        """
        self._ensure_open()
        updates: list[tuple[str, str]] = []
        for key, value in metadata.items():
            pdf_key = _METADATA_KEYS.get(key)
            if pdf_key is None:
                msg = f"unknown metadata key: {key!r} (valid: {sorted(_METADATA_KEYS)})"
                raise ValueError(msg)
            if not isinstance(value, str):
                msg = f"metadata value must be a string: {key!r}={value!r}"
                raise TypeError(msg)
            updates.append((pdf_key, value))
        for pdf_key, value in updates:
            self._doc.set_metadata(pdf_key, value)

    def to_markdown(self, pages: Iterable[int] | None = None) -> str:
        """Convert the document to Markdown for RAG or LLM preprocessing.

        Headings are inferred from font sizes: the size containing the most text
        is body text, and larger sizes map in descending order to
        ``#`` through ``####``. Wrapped CJK lines join without spaces. Leading
        bullets and ``1.``/``1)`` forms normalize to Markdown lists. Scanned PDFs
        work after adding a layer with :meth:`Page.insert_ocr_text_layer`.
        Tables, multicolumn reading order, and vertical writing are unsupported.
        ``pages`` is a sequence of zero-based page numbers emitted in the given
        order; ``None`` means every page.
        """
        self._ensure_open()
        page_numbers = range(self.page_count) if pages is None else pages
        layouts = [self.get_page_text(pno, "dict") for pno in page_numbers]
        levels = _markdown.heading_levels(_markdown.collect_sizes(layouts))
        rendered = (_markdown.page_to_markdown(layout, levels) for layout in layouts)
        return "\n\n".join(md for md in rendered if md)

    def get_form_fields(self) -> list[dict[str, Any]]:
        """Return AcroForm fields.

        Each item is ``{"name", "type", "value"}``. ``name`` is the fully
        qualified dotted name; ``type`` is text, checkbox, radio, button,
        combobox, listbox, or signature; and ``value`` is the current value.
        Button values are appearance state names such as ``"Yes"`` or ``"Off"``.
        """
        self._ensure_open()
        return [{"name": name, "type": kind, "value": value} for name, kind, value in self._doc.get_form_fields()]

    def set_form_field(self, name: str, value: str | bool) -> None:  # noqa: FBT001  # Match pymupdf's bool API.
        """Set an AcroForm field value.

        Pass strings for text/choice fields. For checkboxes and radio buttons,
        pass an appearance state such as ``"Yes"``/``"Off"`` or a bool. ``True``
        resolves the on state from widget appearances; ``False`` becomes
        ``"Off"``. pylopdf sets ``NeedAppearances`` without regenerating
        appearance streams, so viewers draw values but pylopdf's renderer does
        not. Signature fields are unsupported; use the pyHanko integration.
        """
        self._ensure_open()
        if not name:
            msg = "name must be at least 1 character"
            raise ValueError(msg)
        if isinstance(value, bool):
            if value:
                states = self._doc.form_button_states(name)
                on_states = [s for s in states if s != "Off"]
                resolved = on_states[0] if on_states else "Yes"
            else:
                resolved = "Off"
            self._doc.set_form_field(name, resolved)
            return
        self._doc.set_form_field(name, value)

    def get_page_labels(self) -> list[dict[str, Any]]:
        """Read page-label definitions.

        Each item has ``startpage``, ``style``, ``prefix``, and
        ``firstpagenum``. ``startpage`` is zero-based; ``style`` is
        ``"D"``, ``"R"``, ``"r"``, ``"A"``, ``"a"``, or empty for prefix only.
        Use :meth:`Page.get_label` for a page's rendered label.
        """
        self._ensure_open()
        return [
            {
                "startpage": int(start),
                "style": style or "",
                "prefix": prefix or "",
                "firstpagenum": int(first),
            }
            for start, style, prefix, first in self._doc.get_page_labels()
        ]

    def set_page_labels(self, labels: Sequence[dict[str, Any]]) -> None:
        """Set page labels in :meth:`get_page_labels` format; empty removes all.

        The PDF specification requires the first range to start at page 0.
        ``firstpagenum`` defaults to 1 for each range.
        """
        self._ensure_open()
        payload: list[tuple[int, str | None, str | None, int]] = []
        seen: set[int] = set()
        for label in labels:
            start = int(label.get("startpage", -1))
            style = str(label.get("style", ""))
            prefix = str(label.get("prefix", ""))
            first = int(label.get("firstpagenum", 1))
            if start < 0 or start in seen:
                msg = f"startpage must be >= 0 and unique: {label!r}"
                raise ValueError(msg)
            if style not in _PAGE_LABEL_STYLES:
                msg = f"style must be one of {sorted(_PAGE_LABEL_STYLES)}: {style!r}"
                raise ValueError(msg)
            if first < 1:
                msg = f"firstpagenum must be >= 1: {label!r}"
                raise ValueError(msg)
            seen.add(start)
            payload.append((start, style or None, prefix or None, first))
        if payload and min(seen) != 0:
            msg = "the first page label range must start at startpage 0 (PDF spec requirement)"
            raise ValueError(msg)
        payload.sort(key=lambda item: item[0])
        self._doc.set_page_labels(payload)

    def embfile_add(
        self,
        name: str,
        data: bytes,
        *,
        filename: str | None = None,
        desc: str | None = None,
    ) -> None:
        """Add an EmbeddedFiles attachment.

        ``name`` is the unique key used for listing and retrieval. ``filename``
        is the viewer-facing file name and defaults to ``name``; ``desc`` is a
        description. Both support Unicode through UTF-16BE ``UF``/``Desc``.
        This can build invoice-plus-XML structures such as ZUGFeRD/Factur-X.
        """
        self._ensure_open()
        if not name:
            msg = "name must be at least 1 character"
            raise ValueError(msg)
        self._doc.embfile_add(name, bytes(data), filename, desc)

    def embfile_names(self) -> list[str]:
        """Return sorted attachment names."""
        self._ensure_open()
        return self._doc.embfile_names()

    def embfile_get(self, name: str) -> bytes:
        """Return attachment contents as bytes."""
        self._ensure_open()
        return self._doc.embfile_get(name)

    def embfile_del(self, name: str) -> None:
        """Delete an attachment, raising an error when absent."""
        self._ensure_open()
        self._doc.embfile_del(name)

    def get_pdfa_claim(self) -> tuple[int, str] | None:
        """Read the XMP PDF/A claim from ``pdfaid:part`` and conformance.

        Return ``(part, conformance)``, for example ``(2, "B")`` for a
        PDF/A-2b claim. PDF/A-4 without conformance uses an empty string. Return
        ``None`` when absent. This reads a self-declaration; it does not validate
        compliance. Use veraPDF or another external validator.
        """
        self._ensure_open()
        claim = self._doc.pdfa_claim()
        return None if claim is None else (int(claim[0]), claim[1])

    @overload
    def get_page_text(self, pno: int, option: Literal["text"] = "text") -> str: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["blocks"]) -> list[BlockEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["dict"]) -> dict[str, Any]: ...
    def get_page_text(
        self, pno: int, option: str = "text"
    ) -> str | list[WordEntry] | list[BlockEntry] | dict[str, Any]:
        """Extract text or positioned layout from zero-based page ``pno``.

        ``option`` follows pymupdf:

        - ``"text"``: plain text, the default.
        - ``"words"``: ``(x0, y0, x1, y1, word, block, line, word index)``.
        - ``"blocks"``: ``(x0, y0, x1, y1, text, block, 0)``.
        - ``"dict"``: nested width, height, and blocks with lines and spans.

        Coordinates have a top-left origin and downward y. Vertical bbox extents
        approximate baseline ± a font-size ratio rather than real metrics.
        """
        if option == "text":
            text = self._doc.extract_text([self._lopdf_page_number(pno)])
            self._emit_warnings()
            return text
        width, height, blocks = self._doc.extract_layout(self._lopdf_page_number(pno))
        self._emit_warnings()
        if option == "words":
            words: list[WordEntry] = []
            for bno, (_, lines) in enumerate(blocks):
                for lno, (_, _, line_words) in enumerate(lines):
                    words.extend(
                        (x0, y0, x1, y1, text, bno, lno, wno) for wno, ((x0, y0, x1, y1), text) in enumerate(line_words)
                    )
            return words
        if option == "blocks":
            return [
                (
                    x0,
                    y0,
                    x1,
                    y1,
                    "\n".join(" ".join(text for _, text in line_words) for _, _, line_words in lines),
                    bno,
                    0,
                )
                for bno, ((x0, y0, x1, y1), lines) in enumerate(blocks)
            ]
        if option == "dict":
            return {
                "width": width,
                "height": height,
                "blocks": [
                    {
                        "number": bno,
                        "type": 0,
                        "bbox": bbox,
                        "lines": [
                            {
                                "bbox": line_bbox,
                                "wmode": 0,
                                "dir": (1.0, 0.0),
                                "spans": [
                                    {
                                        "bbox": span_bbox,
                                        "origin": origin,
                                        "size": size,
                                        "font": font,
                                        "flags": flags,
                                        "text": text,
                                    }
                                    for span_bbox, text, size, origin, font, flags in spans
                                ],
                            }
                            for line_bbox, spans, _ in lines
                        ],
                    }
                    for bno, (bbox, lines) in enumerate(blocks)
                ],
            }
        msg = f"option must be one of 'text' / 'words' / 'blocks' / 'dict': {option!r}"
        raise ValueError(msg)

    def delete_page(self, pno: int) -> None:
        """Delete zero-based page ``pno``; negative values count from the end."""
        page_number = self._lopdf_page_number(pno)
        self._bump_generation()
        self._doc.delete_pages([page_number])

    def delete_pages(self, page_numbers: Iterable[int]) -> None:
        """Delete multiple zero-based pages; negative values count from the end."""
        self._ensure_open()
        numbers = [self._lopdf_page_number(pno) for pno in page_numbers]
        self._bump_generation()
        self._doc.delete_pages(numbers)

    def select(self, page_numbers: Iterable[int]) -> None:
        """Keep only the given zero-based pages in the given order.

        This also reorders pages. Repeating an index duplicates that page; the
        duplicate shares Contents and Resources objects with the original.
        """
        self._ensure_open()
        numbers = [self._lopdf_page_number(pno) for pno in page_numbers]
        self._bump_generation()
        self._doc.select(numbers)

    def insert_pdf(
        self,
        other: Document,
        from_page: int = 0,
        to_page: int = -1,
        start_at: int = -1,
    ) -> None:
        """Insert a page range from another document.

        Insert the inclusive, zero-based ``from_page..to_page`` range at
        zero-based ``start_at``; negative source indices count from the end and
        ``start_at=-1`` appends. A descending range imports in reverse order.
        """
        self._ensure_open()
        if other is self:
            msg = "cannot insert a document into itself"
            raise ValueError(msg)
        other._ensure_open()
        if other.page_count == 0:
            return
        start = other._normalize_pno(from_page)
        stop = other._normalize_pno(to_page)
        step = 1 if start <= stop else -1
        numbers = list(range(start, stop + step, step))
        position = None if start_at == -1 else self._insert_position(start_at, "start_at")
        self._bump_generation()
        self._doc.merge_pages(other._doc, [n + 1 for n in numbers], position)

    def new_page(self, pno: int = -1, width: float = 595.0, height: float = 842.0) -> Page:
        """Insert a blank page and return its :class:`Page`.

        ``pno`` is the zero-based insertion position; -1 appends. ``width`` and
        ``height`` are PDF units and default to 595×842 portrait A4.
        """
        self._ensure_open()
        if (
            not (math.isfinite(width) and math.isfinite(height))
            or not (0 < width <= _FLOAT32_MAX)
            or not (0 < height <= _FLOAT32_MAX)
        ):
            msg = f"width / height must be positive finite values within PDF real-number range: ({width!r}, {height!r})"
            raise ValueError(msg)
        if pno == -1:
            position = None
            index = self.page_count
        else:
            position = self._insert_position(pno, "pno")
            index = position
        self._bump_generation()
        self._doc.new_page(position, width, height)
        return self[index]

    def copy_page(self, pno: int, to: int = -1) -> None:
        """Copy page ``pno`` to insertion position ``to``; -1 appends.

        The copied page shares Contents and Resources objects with the original.
        """
        self._ensure_open()
        page_number = self._lopdf_page_number(pno)
        position = None if to == -1 else self._insert_position(to, "to")
        self._bump_generation()
        self._doc.copy_page(page_number, position)

    def _insert_position(self, value: int, name: str) -> int:
        """Validate an insertion position from 0 through ``page_count``."""
        count = self.page_count
        if not 0 <= value <= count:
            msg = f"{name} {value} is out of range (0..{count} or -1)"
            raise IndexError(msg)
        return value

    def get_toc(self) -> list[list[int | str]]:
        """Return bookmarks as ``[[level, title, page number], ...]``.

        Levels and page numbers are one-based for pymupdf compatibility, unlike
        other page APIs. Return an empty list when no TOC exists.
        """
        self._ensure_open()
        return [[level, title, page] for level, title, page in self._doc.get_toc()]

    def set_toc(self, toc: Sequence[Sequence[int | str]]) -> None:
        """Replace bookmarks from ``[[level, title, page number], ...]``.

        An empty sequence removes them. Levels start at 1 and can increase by at
        most one from the previous entry. Page numbers are one-based, matching
        :meth:`get_toc`.
        """
        self._ensure_open()
        count = self.page_count
        entries: list[tuple[int, str, int]] = []
        previous_level = 0
        for i, item in enumerate(toc):
            try:
                level_raw, title, page_raw = item
                level = int(level_raw)
                page = int(page_raw)
            except (TypeError, ValueError) as exc:
                msg = f"toc[{i}] must be 3 elements [level, title, page number]: {item!r}"
                raise ValueError(msg) from exc
            if level < 1 or level > previous_level + 1:
                msg = f"toc[{i}] has an invalid level {level} (must be >= 1 and at most the previous level + 1)"
                raise ValueError(msg)
            if not 1 <= page <= count:
                msg = f"toc[{i}] page number {page} is out of range (1..{count})"
                raise ValueError(msg)
            entries.append((level, str(title), page))
            previous_level = level
        self._doc.set_toc(entries)

    def set_fallback_font(
        self,
        font: bytes | str | os.PathLike[str] | None,
        kind: str = "sans",
        index: int = 0,
    ) -> None:
        """Set a fallback font for rendering non-embedded CJK fonts.

        ``font`` is a TTF/OTF/TTC path or bytes. ``None`` clears the setting and
        disables automatic discovery from ``pylopdf[cjk]``. ``kind`` is
        ``"sans"`` (default) or ``"serif"``; ``index`` selects a TTC face.
        """
        self._ensure_open()
        self._fallback_configured = True
        if font is None:
            self._doc.clear_fallback_fonts()
            return
        data = font if isinstance(font, bytes) else Path(font).read_bytes()
        self._doc.set_fallback_font(kind, data, index)

    def _ensure_fallback_fonts(self) -> None:
        """Auto-configure ``pylopdf[cjk]`` fonts unless explicitly configured."""
        if self._fallback_configured:
            return
        self._fallback_configured = True
        for kind, data in _bundled_cjk_fonts():
            self._doc.set_fallback_font(kind, data, 0)

    def render_page(
        self,
        pno: int,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
    ) -> bytes:
        """Render zero-based page ``pno`` to PNG.

        ``scale`` is a positive finite factor where 1.0 equals 72 dpi. ``dpi``
        may be used instead (144 equals scale 2.0) but not together with a
        nondefault scale. ``background`` is an RGB or RGBA tuple with components
        from 0 to 255; the default is transparent. Output is limited to 65,535
        pixels per side and 64,000,000 total pixels.
        """
        if dpi is not None:
            if scale != 1.0:
                msg = "scale and dpi cannot both be specified"
                raise ValueError(msg)
            scale = dpi / 72.0
        rgba = _normalize_background(background)
        page_number = self._lopdf_page_number(pno)
        self._ensure_fallback_fonts()
        result = self._doc.render_page_png(page_number, scale, rgba)
        self._emit_warnings()
        return result

    def render_page_svg(self, pno: int) -> str:
        """Render zero-based page ``pno`` to an SVG string."""
        page_number = self._lopdf_page_number(pno)
        self._ensure_fallback_fonts()
        result = self._doc.render_page_svg(page_number)
        self._emit_warnings()
        return result

    def _emit_warnings(self) -> None:
        """Emit hayro warnings from the latest operation as ``PylopdfWarning``."""
        for message in self._doc.take_warnings():
            _warnings.warn(message, PylopdfWarning, stacklevel=3)

    def save(  # noqa: PLR0913  # Save options are keyword-only, like pymupdf.
        self,
        filename: str | os.PathLike[str],
        *,
        garbage: bool = False,
        deflate: bool = False,
        object_streams: bool = False,
        user_pw: str | None = None,
        owner_pw: str | None = None,
        permissions: int = Permissions.ALL,
    ) -> None:
        """Save to a file.

        ``garbage=True`` removes unreferenced objects and ``deflate=True``
        applies Flate compression before saving; both mutate the document.
        ``object_streams=True`` writes PDF 1.5+ object and xref streams, often
        reducing size and raising the version to 1.5 when necessary.

        Providing ``user_pw`` or ``owner_pw`` writes AES-256 PDF 2.0 encryption
        while the in-memory document stays plaintext. ``owner_pw`` defaults to
        ``user_pw``. An empty user password plus an owner password permits
        unrestricted opening with permission controls. ``permissions`` combines
        :class:`Permissions` and defaults to all. Encryption cannot be combined
        with object streams.
        """
        self._ensure_open()
        encryption = self._encryption_args(user_pw, owner_pw, permissions, object_streams=object_streams)
        self._apply_save_options(garbage=garbage, deflate=deflate)
        if encryption is not None:
            user, owner, perms = encryption
            self._doc.save_encrypted(str(filename), user, owner, perms, os.urandom(32))
        elif object_streams:
            self._doc.save_with_object_streams(str(filename))
        else:
            self._doc.save(str(filename))

    def tobytes(  # noqa: PLR0913  # Save options are keyword-only, like pymupdf.
        self,
        *,
        garbage: bool = False,
        deflate: bool = False,
        object_streams: bool = False,
        user_pw: str | None = None,
        owner_pw: str | None = None,
        permissions: int = Permissions.ALL,
    ) -> bytes:
        """Return PDF bytes; options have the same meaning as :meth:`save`."""
        self._ensure_open()
        encryption = self._encryption_args(user_pw, owner_pw, permissions, object_streams=object_streams)
        self._apply_save_options(garbage=garbage, deflate=deflate)
        if encryption is not None:
            user, owner, perms = encryption
            return self._doc.save_bytes_encrypted(user, owner, perms, os.urandom(32))
        if object_streams:
            return self._doc.save_bytes_with_object_streams()
        return self._doc.save_bytes()

    def _apply_save_options(self, *, garbage: bool, deflate: bool) -> None:
        """Apply object pruning and stream compression before saving."""
        if garbage:
            self._doc.prune_objects()
        if deflate:
            self._doc.compress()

    @staticmethod
    def _encryption_args(
        user_pw: str | None,
        owner_pw: str | None,
        permissions: int,
        *,
        object_streams: bool,
    ) -> tuple[str, str, int] | None:
        """Validate encryption arguments, returning ``None`` when disabled."""
        if user_pw is None and owner_pw is None:
            return None
        if object_streams:
            msg = "encryption (user_pw / owner_pw) and object_streams cannot both be specified"
            raise ValueError(msg)
        user = user_pw if user_pw is not None else ""
        owner = owner_pw if owner_pw is not None else user
        return (user, owner, int(permissions))

    def close(self) -> None:
        """Close the document; subsequent operations raise an error."""
        self._closed = True

    def _ensure_not_closed(self) -> None:
        """Reject operations on a closed document."""
        if self._closed:
            msg = "document closed"
            raise DocumentClosedError(msg)

    def _ensure_open(self) -> None:
        """Reject operations on a closed or undecrypted document.

        lopdf makes undecrypted files look like empty zero-page documents, so
        report the encrypted state explicitly.
        """
        self._ensure_not_closed()
        if self._doc.is_encrypted():
            msg = "this PDF is encrypted; open it with the password argument or call authenticate()"
            raise EncryptedDocumentError(msg)

    def _normalize_pno(self, pno: int) -> int:
        """Resolve negative indexing and return a valid zero-based page number."""
        self._ensure_open()
        count = self._doc.page_count()
        normalized = pno + count if pno < 0 else pno
        if not 0 <= normalized < count:
            msg = f"page number {pno} is out of range (0..{count - 1})"
            raise IndexError(msg)
        return normalized

    def _lopdf_page_number(self, pno: int) -> int:
        """Validate a Python page index and convert it to one-based lopdf form."""
        return self._normalize_pno(pno) + 1

    def __enter__(self) -> Self:
        """Enter a context manager and return this document."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the document when leaving a context manager."""
        self.close()

    def __repr__(self) -> str:
        """Return a representation containing the open or closed state."""
        state = "closed " if self._closed else ""
        return f"<{state}pylopdf.Document>"


def open(  # noqa: A001
    filename: str | os.PathLike[str] | None = None,
    stream: bytes | None = None,
    password: str | None = None,
    max_decompressed_size: int | None = None,
) -> Document:
    """Open a :class:`Document`; equivalent to ``Document(...)``."""
    return Document(
        filename=filename,
        stream=stream,
        password=password,
        max_decompressed_size=max_decompressed_size,
    )


def peek_metadata(
    filename: str | os.PathLike[str] | None = None,
    stream: bytes | None = None,
    password: str | None = None,
) -> dict[str, str | int | bool]:
    """Read metadata and page count without parsing the entire document.

    Return the keys from :attr:`Document.metadata` plus integer ``page_count``
    and boolean ``encrypted``. This is suitable for scanning many PDFs.
    """
    if (filename is None) == (stream is None):
        msg = "specify exactly one of filename or stream"
        raise ValueError(msg)
    if stream is not None:
        raw, page_count, version, encrypted = _Document.load_metadata_bytes(stream, password)
    else:
        raw, page_count, version, encrypted = _Document.load_metadata(str(filename), password)
    result: dict[str, str | int | bool] = {key: raw.get(pdf_key, "") for key, pdf_key in _METADATA_KEYS.items()}
    result["format"] = f"PDF {version}"
    result["page_count"] = page_count
    result["encrypted"] = encrypted
    return result
