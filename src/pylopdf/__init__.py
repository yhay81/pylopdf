"""PDF editing and rendering backed by Rust.

The :class:`Document` API follows pymupdf conventions. lopdf handles editing
and hayro handles rendering; both are pure Rust under permissive licenses.
"""

from __future__ import annotations

import enum
import functools
import importlib
import math
import os
import secrets
import stat
import threading
import warnings as _warnings
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, TypeAlias, TypedDict, cast, overload

from pylopdf import _markdown
from pylopdf.pylopdf_core import (
    LimitError,
    OcrError,
    PasswordError,
    PdfError,
    Pixmap,
    _Document,
    _Font,
    _OcrEngine,
    _Shape,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from types import TracebackType
    from typing import Any, NoReturn

    from typing_extensions import Self

__version__ = "0.14.0"
__all__ = [
    "LINK_GOTO",
    "LINK_GOTOR",
    "LINK_LAUNCH",
    "LINK_NAMED",
    "LINK_NONE",
    "LINK_URI",
    "TEXT_ALIGN_CENTER",
    "TEXT_ALIGN_JUSTIFY",
    "TEXT_ALIGN_LEFT",
    "TEXT_ALIGN_RIGHT",
    "AnnotationInfo",
    "BlockEntry",
    "CjkFontLanguage",
    "Document",
    "DocumentClosedError",
    "DocumentComplexity",
    "DocumentLimits",
    "DocumentMetadata",
    "DrawingInfo",
    "DrawingItem",
    "EncryptedDocumentError",
    "Font",
    "FontLanguage",
    "FormFieldInfo",
    "FormFieldType",
    "ImageCompressionResult",
    "ImageInfo",
    "LimitError",
    "LinkInfo",
    "MetadataProbe",
    "MetadataUpdate",
    "OcrEngine",
    "OcrError",
    "OcrRotation",
    "OcrWord",
    "Page",
    "PageLabelInfo",
    "PageLabelSpec",
    "PasswordError",
    "PdfError",
    "Permissions",
    "Point",
    "RawTextBlock",
    "RawTextLine",
    "RawTextPage",
    "RawTextSpan",
    "Rect",
    "Shape",
    "StalePageError",
    "Table",
    "TableDiagnostics",
    "TableFinder",
    "TextBlock",
    "TextChar",
    "TextLine",
    "TextPage",
    "TextSpan",
    "WordEntry",
    "open",
    "peek_metadata",
]
__all__ += ["Pixmap", "PylopdfWarning"]

_DEFAULT_MAX_EMBEDDED_FILE_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_XMP_METADATA_SIZE = 1024 * 1024
_DEFAULT_MAX_RENDER_PAGE_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_RENDER_BATCH_SIZE = 512 * 1024 * 1024
_DEFAULT_MAX_MARKDOWN_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_SVG_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_TEXT_REPLACEMENT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_PDF_OUTPUT_SIZE = 512 * 1024 * 1024
_DEFAULT_MAX_IMAGE_INPUT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_IMAGE_PIXELS = 64_000_000
_DEFAULT_MAX_FONT_INPUT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_GENERATED_TEXT_SIZE = 1024 * 1024
_MAX_GENERATED_TEXT_LINES = 4096
#: The characters that end a line for str.splitlines(), which also treats
#: "\r\n" as one line break.
_LINE_BREAKS = "\n\v\f\r\x1c\x1d\x1e\x85\u2028\u2029"
_MAX_FORM_FIELD_INPUT_SIZE = 1024 * 1024
_MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE = 1024 * 1024
_MAX_METADATA_INPUT_TEXT_SIZE = 1024 * 1024
_MAX_TOC_INPUT_TEXT_SIZE = 16 * 1024 * 1024
_MAX_PAGE_LABEL_INPUT_TEXT_SIZE = 1024 * 1024
_DEFAULT_MAX_OCR_MODEL_SIZE = 64 * 1024 * 1024
_UTF8_TWO_BYTE_MIN = 1 << 7
_UTF8_THREE_BYTE_MIN = 1 << 11
_UTF8_FOUR_BYTE_MIN = 1 << 16
_UTF16_BMP_MAX = (1 << 16) - 1
_MAX_PAGE_LABEL_RANGES = 4096
_MAX_HIGHLIGHT_RECTS = 4096
_MAX_ANNOTATION_INPUT_TEXT_SIZE = 1024 * 1024
_RECT_COORDINATE_COUNT = 4
_MAX_SEARCH_INPUT_BYTES = 4096
_DEFAULT_MAX_SEARCH_HITS = 4096
_MAX_PASSWORD_INPUT_BYTES = 127
_MAX_TOC_ENTRIES = 65_536
_MAX_OCR_LAYER_WORDS = 4096
_MAX_OCR_LAYER_TEXT_BYTES = 1024 * 1024
# Explicit page iterables may repeat pages up to the larger of the page count
# and this floor, so repetition never costs more than one full document pass.
_MIN_PAGE_ENTRY_LIMIT = 4096
_MAX_TEXT_REPLACEMENT_INPUT_BYTES = 4096
_TEMPORARY_FILE_ATTEMPTS = 100

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

_OCR_MAX_THREADS = 16
_OCR_MAX_CONCURRENT = 16
_OCR_MIN_TILE_SIZE = 256
_OCR_MAX_TILE_SIZE = 2048
_OCR_TILE_MULTIPLE = 32
_DRAWING_LINE_POINT_COUNT = 2
_DRAWING_CUBIC_POINT_COUNT = 4
_IMAGE_COMPRESSION_MIN_DPI = 36.0
_IMAGE_COMPRESSION_MAX_DPI = 2400.0
_IMAGE_COMPRESSION_MIN_QUALITY = 1
_IMAGE_COMPRESSION_MAX_QUALITY = 100


class PylopdfWarning(UserWarning):
    """A recoverable PDF interpretation warning for repair, font, or image issues."""


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


_LIMIT_ERROR_STR = LimitError.__str__
_LIMIT_ERROR_ARG_COUNT = 2


def _limit_error_code(error: LimitError) -> str:
    """Return the stable resource identifier from a core limit exception."""
    if len(error.args) >= _LIMIT_ERROR_ARG_COUNT and isinstance(error.args[0], str):
        return error.args[0]
    return "unknown"


def _limit_error_str(error: LimitError) -> str:
    """Render only the human-readable part of a core limit exception."""
    if len(error.args) >= _LIMIT_ERROR_ARG_COUNT and isinstance(error.args[1], str):
        return error.args[1]
    return _LIMIT_ERROR_STR(error)


_limit_error_type = cast("Any", LimitError)
_limit_error_type.code = property(_limit_error_code)
_limit_error_type.__str__ = _limit_error_str


def _validate_optional_positive_int(name: str, value: int | None) -> None:
    """Validate one optional positive integer resource budget."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} must be a positive integer or None: {value!r}"
        raise TypeError(msg)
    if value <= 0:
        msg = f"{name} must be a positive integer or None: {value!r}"
        raise ValueError(msg)


def _markdown_output_limit_error(max_size: int) -> LimitError:
    """Build the public error for any bounded Markdown conversion path."""
    return LimitError(
        "markdown_output_size",
        f"Markdown output exceeds the {max_size}-byte UTF-8 limit",
    )


def _resolve_page_index(pno: int, count: int) -> int:
    """Resolve negative indexing against one page-count snapshot."""
    normalized = pno + count if pno < 0 else pno
    if not 0 <= normalized < count:
        msg = f"page number {pno} is out of range (0..{count - 1})"
        raise IndexError(msg)
    return normalized


def _temporary_sibling_path(target: Path) -> Path:
    """Create one same-directory output path with normal umask permissions."""
    for _ in range(_TEMPORARY_FILE_ATTEMPTS):
        candidate = target.parent / f".pylopdf-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate
    msg = f"failed to create a unique temporary output beside {target}"
    raise FileExistsError(msg)


def _atomic_save_file(
    filename: str | os.PathLike[str],
    writer: Callable[[str], None],
) -> None:
    """Write a sibling file completely before atomically replacing the target."""
    requested = Path(filename)
    try:
        target = requested.resolve(strict=False) if requested.is_symlink() else requested
        try:
            target_metadata = target.stat()
        except FileNotFoundError:
            target_mode = None
        else:
            target_mode = stat.S_IMODE(target_metadata.st_mode) if stat.S_ISREG(target_metadata.st_mode) else None
        temporary = _temporary_sibling_path(target)
    except (OSError, RuntimeError) as exc:
        msg = f"failed to save {requested}: {exc}"
        raise PdfError(msg) from exc
    replaced = False
    try:
        try:
            writer(str(temporary))
        except PdfError as exc:
            message = str(exc).replace(str(temporary), str(requested))
            raise PdfError(message) from exc
        try:
            if target_mode is not None:
                temporary.chmod(target_mode)
            temporary.replace(target)
        except OSError as exc:
            msg = f"failed to save {requested}: {exc}"
            raise PdfError(msg) from exc
        replaced = True
    finally:
        if not replaced:
            with suppress(OSError):
                temporary.unlink()


@dataclass(frozen=True, slots=True)
class DocumentLimits:
    """Optional resource budgets for opening and interpreting untrusted PDFs.

    ``None`` leaves an individual budget unlimited. :meth:`web` supplies a
    conservative starting profile for memory-bounded web and queue workers.
    ``max_interpretation_size`` caps the complete PDF byte snapshot handed to
    rendering and extraction, including a reserialization after edits.
    ``max_text_size`` caps cumulative glyph UTF-8 payload and derives a bounded
    plain-text assembly size without constraining compatible ``None`` policies.
    ``max_text_glyphs`` caps cumulative positioned glyph records before cached
    layout or Python word/span structures can amplify short text.
    """

    max_file_size: int | None = None
    max_pages: int | None = None
    max_objects: int | None = None
    max_decompressed_size: int | None = None
    max_page_content_size: int | None = None
    max_total_decompressed_size: int | None = None
    max_object_depth: int | None = None
    max_text_size: int | None = None
    max_interpretation_size: int | None = None
    max_text_glyphs: int | None = None

    def __post_init__(self) -> None:
        """Reject booleans, non-integers, zero, and negative budgets."""
        values = (
            ("max_file_size", self.max_file_size),
            ("max_pages", self.max_pages),
            ("max_objects", self.max_objects),
            ("max_decompressed_size", self.max_decompressed_size),
            ("max_page_content_size", self.max_page_content_size),
            ("max_total_decompressed_size", self.max_total_decompressed_size),
            ("max_object_depth", self.max_object_depth),
            ("max_text_size", self.max_text_size),
            ("max_interpretation_size", self.max_interpretation_size),
            ("max_text_glyphs", self.max_text_glyphs),
        )
        for name, value in values:
            _validate_optional_positive_int(name, value)

    @classmethod
    def web(cls) -> DocumentLimits:
        """Return a conservative profile for user uploads in bounded workers."""
        mib = 1024 * 1024
        return cls(
            max_file_size=10 * mib,
            max_pages=200,
            max_objects=100_000,
            max_decompressed_size=64 * mib,
            max_page_content_size=10 * mib,
            max_total_decompressed_size=128 * mib,
            max_object_depth=64,
            max_text_size=mib,
            max_interpretation_size=64 * mib,
            max_text_glyphs=65_536,
        )


def _document_limit_args(limits: DocumentLimits) -> tuple[int | None, ...]:
    """Return private Rust arguments in their stable binding order."""
    return (
        limits.max_decompressed_size,
        limits.max_page_content_size,
        limits.max_file_size,
        limits.max_pages,
        limits.max_objects,
        limits.max_total_decompressed_size,
        limits.max_object_depth,
        limits.max_text_size,
        limits.max_interpretation_size,
        limits.max_text_glyphs,
    )


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


def _validate_ocr_dpi(dpi: float) -> float:
    """Resolve one positive finite OCR resolution."""
    if isinstance(dpi, bool):
        msg = f"dpi must be a positive finite number: {dpi!r}"
        raise TypeError(msg)
    try:
        resolved_dpi = float(dpi)
    except (TypeError, ValueError) as exc:
        msg = f"dpi must be a positive finite number: {dpi!r}"
        raise OcrError(msg) from exc
    if not (math.isfinite(resolved_dpi) and resolved_dpi > 0):
        msg = f"dpi must be a positive finite number: {dpi!r}"
        raise OcrError(msg)
    return resolved_dpi


def _validate_ocr_tiles(tile_size: int, overlap: int) -> None:
    """Validate bounded detector tile geometry."""
    if isinstance(tile_size, bool) or not isinstance(tile_size, int):
        msg = (
            f"tile_size must be an integer multiple of {_OCR_TILE_MULTIPLE} from "
            f"{_OCR_MIN_TILE_SIZE} through {_OCR_MAX_TILE_SIZE}: {tile_size!r}"
        )
        raise TypeError(msg)
    if not _OCR_MIN_TILE_SIZE <= tile_size <= _OCR_MAX_TILE_SIZE or tile_size % _OCR_TILE_MULTIPLE:
        msg = (
            f"tile_size must be a multiple of {_OCR_TILE_MULTIPLE} from "
            f"{_OCR_MIN_TILE_SIZE} through {_OCR_MAX_TILE_SIZE}: {tile_size}"
        )
        raise OcrError(msg)
    if isinstance(overlap, bool) or not isinstance(overlap, int):
        msg = f"overlap must be an integer from {_OCR_TILE_MULTIPLE} through half of tile_size: {overlap!r}"
        raise TypeError(msg)
    if not _OCR_TILE_MULTIPLE <= overlap <= tile_size // 2:
        msg = f"overlap must be from {_OCR_TILE_MULTIPLE} through half of tile_size: {overlap}"
        raise OcrError(msg)


def _validate_ocr_confidence(min_confidence: float) -> float:
    """Resolve one finite OCR confidence threshold."""
    if isinstance(min_confidence, bool):
        msg = f"min_confidence must be a finite number from 0 through 1: {min_confidence!r}"
        raise TypeError(msg)
    try:
        resolved_confidence = float(min_confidence)
    except (TypeError, ValueError) as exc:
        msg = f"min_confidence must be a finite number from 0 through 1: {min_confidence!r}"
        raise OcrError(msg) from exc
    if not (math.isfinite(resolved_confidence) and 0 <= resolved_confidence <= 1):
        msg = f"min_confidence must be a finite number from 0 through 1: {min_confidence!r}"
        raise OcrError(msg)
    return resolved_confidence


def _validate_ocr_concurrency(max_concurrent: int) -> int:
    """Validate the number of complete OCR calls admitted per engine."""
    if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int):
        msg = f"max_concurrent must be an integer from 1 through {_OCR_MAX_CONCURRENT}: {max_concurrent!r}"
        raise TypeError(msg)
    if not 1 <= max_concurrent <= _OCR_MAX_CONCURRENT:
        msg = f"max_concurrent must be from 1 through {_OCR_MAX_CONCURRENT}: {max_concurrent}"
        raise OcrError(msg)
    return max_concurrent


def _validate_ocr_rotation(rotation: int) -> OcrRotation:
    """Validate one clockwise OCR input correction."""
    if isinstance(rotation, bool) or not isinstance(rotation, int):
        msg = f"rotation must be 0, 90, 180, or 270: {rotation!r}"
        raise TypeError(msg)
    if rotation not in (0, 90, 180, 270):
        msg = f"rotation must be 0, 90, 180, or 270: {rotation}"
        raise OcrError(msg)
    return rotation


class OcrEngine:
    """Reusable, pure-Rust PP-OCR engine.

    With no paths, the engine discovers models from the optional
    ``pylopdf[ocr]`` installation. Explicit paths are useful for evaluating a
    compatible RTen-format PP-OCR detector, recognizer, and character
    dictionary.
    """

    def __init__(  # noqa: PLR0913 - model resource controls are keyword-only.
        self,
        detector_path: str | os.PathLike[str] | None = None,
        recognizer_path: str | os.PathLike[str] | None = None,
        dictionary_path: str | os.PathLike[str] | None = None,
        *,
        threads: int | None = None,
        max_concurrent: int = 1,
        max_model_size: int | None = _DEFAULT_MAX_OCR_MODEL_SIZE,
    ) -> None:
        """Load an OCR model set once for reuse across pages and documents.

        ``threads=None`` uses up to four logical CPUs. Lower it to reduce peak
        inference memory or raise it to at most 16 after measuring the target
        workload. ``max_concurrent`` bounds complete render-and-recognize calls
        sharing this engine; its memory-safe default is one. ``max_model_size``
        bounds the cumulative detector, recognizer, and dictionary input.
        Pass ``None`` only for trusted custom model sets.
        """
        _validate_optional_positive_int("max_model_size", max_model_size)
        if threads is None:
            resolved_threads = min(4, os.cpu_count() or 1)
        elif isinstance(threads, bool) or not isinstance(threads, int):
            msg = f"threads must be an integer from 1 through {_OCR_MAX_THREADS} or None: {threads!r}"
            raise TypeError(msg)
        elif not 1 <= threads <= _OCR_MAX_THREADS:
            msg = f"threads must be from 1 through {_OCR_MAX_THREADS}: {threads}"
            raise OcrError(msg)
        else:
            resolved_threads = threads
        resolved_max_concurrent = _validate_ocr_concurrency(max_concurrent)
        paths = (detector_path, recognizer_path, dictionary_path)
        if all(path is None for path in paths):
            try:
                import pylopdf_ocr_models  # noqa: PLC0415 - lazy optional dependency.
            except ImportError as exc:
                msg = "OCR models are not installed; install pylopdf[ocr] or pass explicit model paths"
                raise OcrError(msg) from exc
            try:
                discovered = pylopdf_ocr_models.model_paths()
                detector_path = discovered.detector
                recognizer_path = discovered.recognizer
                dictionary_path = discovered.dictionary
            except (AttributeError, TypeError) as exc:
                msg = "pylopdf-ocr-models does not expose a compatible model_paths() result"
                raise OcrError(msg) from exc
        elif any(path is None for path in paths):
            msg = "detector_path, recognizer_path, and dictionary_path must be provided together"
            raise OcrError(msg)
        if detector_path is None or recognizer_path is None or dictionary_path is None:
            msg = "OCR model discovery returned incomplete paths"
            raise OcrError(msg)
        self._engine = _OcrEngine(
            os.fspath(detector_path),
            os.fspath(recognizer_path),
            os.fspath(dictionary_path),
            resolved_threads,
            max_model_size,
        )
        self.threads = resolved_threads
        self.max_concurrent = resolved_max_concurrent
        self.max_model_size = max_model_size
        self._recognition_slots = threading.BoundedSemaphore(resolved_max_concurrent)

    def recognize(  # noqa: PLR0913 - OCR resource controls are keyword-only.
        self,
        page: Page,
        *,
        dpi: float = 300,
        tile_size: int = 1408,
        overlap: int = 192,
        min_confidence: float = 0.5,
        rotation: OcrRotation = 0,
        clip: Sequence[float] | None = None,
    ) -> list[OcrWord]:
        """Recognize one page without modifying it.

        The page is rendered on white at ``dpi``. Detection uses bounded,
        overlapping tiles so A4 scans do not require a gigabyte-scale detector
        tensor. Results use the same top-left display coordinates as rendering,
        extraction, and :meth:`Page.insert_ocr_text_layer`. ``clip`` limits
        recognition to one display-coordinate region while preserving page
        coordinates in the returned boxes. ``rotation`` turns the rendered
        input clockwise for recognition, then maps boxes back without changing
        the PDF.
        """
        if not isinstance(page, Page):
            msg = f"page must be a pylopdf.Page: {page!r}"
            raise TypeError(msg)
        resolved_dpi = _validate_ocr_dpi(dpi)
        _validate_ocr_tiles(tile_size, overlap)
        resolved_confidence = _validate_ocr_confidence(min_confidence)
        resolved_rotation = _validate_ocr_rotation(rotation)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        with self._recognition_slots:
            pixmap = page.get_pixmap(
                dpi=resolved_dpi,
                background=(255, 255, 255),
                clip=clip_rect,
            )
            raster_results = self._engine.recognize_pixmap(
                pixmap,
                tile_size=tile_size,
                overlap=overlap,
                min_confidence=resolved_confidence,
                rotation=resolved_rotation,
            )
            pixel_to_page = 72.0 / resolved_dpi
            if clip_rect is None:
                offset_x = offset_y = 0.0
            else:
                page_rect = page.rect
                offset_x = math.floor(max(clip_rect[0], page_rect.x0) / pixel_to_page) * pixel_to_page
                offset_y = math.floor(max(clip_rect[1], page_rect.y0) / pixel_to_page) * pixel_to_page
            return [
                {
                    "bbox": Rect(
                        offset_x + x0 * pixel_to_page,
                        offset_y + y0 * pixel_to_page,
                        offset_x + x1 * pixel_to_page,
                        offset_y + y1 * pixel_to_page,
                    ),
                    "text": text,
                    "confidence": confidence,
                }
                for x0, y0, x1, y1, text, confidence in raster_results
            ]


@functools.cache
def _default_ocr_engine() -> OcrEngine:
    """Load and cache the optional default OCR model set."""
    return OcrEngine()


class TableDiagnostics(NamedTuple):
    """Inspectable geometric evidence for one detected table.

    ``confidence`` is a deterministic ranking heuristic in the range 0–1, not
    a calibrated probability. The em-normalized metrics are present for the
    ``"text"`` strategy and ``None`` for vector-grid strategies.
    """

    strategy: Literal["lines", "text"]
    confidence: float
    alignment_error_em: float | None
    minimum_gutter_em: float | None
    row_gap_variation_em: float | None


class Table:
    """A detected table with row-major cells, owned text, and diagnostics.

    A merged cell occupies its top-left slot. Continuation slots are ``None``.
    """

    page: Page
    bbox: Rect
    row_count: int
    col_count: int
    cells: list[Rect | None]
    diagnostics: TableDiagnostics
    strategy: Literal["lines", "text"]
    confidence: float

    def __init__(  # noqa: PLR0913, PLR0917 - internal owned result constructor
        self,
        page: Page,
        bbox: Rect,
        row_count: int,
        col_count: int,
        cells: list[tuple[Rect, str] | None],
        cell_anchors: list[int],
        diagnostics: TableDiagnostics,
    ) -> None:
        """Build a table from the internal geometry detector."""
        self.page = page
        self.bbox = bbox
        self.row_count = row_count
        self.col_count = col_count
        self.cells = [cell[0] if cell is not None else None for cell in cells]
        self._values = [cell[1] if cell is not None else None for cell in cells]
        self._cell_anchors = cell_anchors
        self.diagnostics = diagnostics
        self.strategy = diagnostics.strategy
        self.confidence = diagnostics.confidence

    def extract(self) -> list[list[str | None]]:
        """Return copied cell text as rows.

        Embedded line breaks are preserved. Slots covered by a merged cell are
        ``None``.
        """
        return [
            self._values[offset : offset + self.col_count].copy()
            for offset in range(0, len(self._values), self.col_count)
        ]

    def to_markdown(
        self,
        *,
        fill_empty: bool = True,
        max_size: int | None = _DEFAULT_MAX_MARKDOWN_SIZE,
    ) -> str:
        """Render the table as Markdown, treating the first row as the header.

        When ``fill_empty`` is true, merged-cell continuation slots inherit the
        anchor value because Markdown has no row/column spans. ``max_size``
        caps UTF-8 output before escaped cell strings are allocated and defaults
        to 64 MiB; ``None`` explicitly opts out.
        """
        _validate_optional_positive_int("max_size", max_size)
        try:
            return _markdown.table_to_markdown(
                self.extract(),
                fill_empty=fill_empty,
                cell_anchors=self._cell_anchors,
                max_size=max_size,
            )
        except _markdown.MarkdownOutputLimitError:
            raise _markdown_output_limit_error(cast("int", max_size)) from None


class TableFinder:
    """Sequence-like result with the strategy and optional clip used."""

    page: Page
    tables: list[Table]
    cells: list[Rect | None]
    strategy: Literal["lines", "text"]
    clip: Rect | None

    def __init__(
        self,
        page: Page,
        tables: list[Table],
        strategy: Literal["lines", "text"],
        clip: Rect | None,
    ) -> None:
        """Store table results independently of the Rust interpretation cache."""
        self.page = page
        self.tables = tables
        self.cells = [cell for table in tables for cell in table.cells]
        self.strategy = strategy
        self.clip = clip

    def __len__(self) -> int:
        """Return the number of detected tables."""
        return len(self.tables)

    @overload
    def __getitem__(self, index: int) -> Table: ...
    @overload
    def __getitem__(self, index: slice) -> list[Table]: ...
    def __getitem__(self, index: int | slice) -> Table | list[Table]:
        """Return one table or a sliced list."""
        return self.tables[index]

    def __iter__(self) -> Iterator[Table]:
        """Iterate over detected tables in page order."""
        return iter(self.tables)


def _table_overlap_ratio(left: Rect, right: Rect) -> float:
    """Return intersection area divided by the smaller table area."""
    width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
    smaller_area = min(left.width * left.height, right.width * right.height)
    return 0.0 if smaller_area <= 0.0 else width * height / smaller_area


_MARKDOWN_TABLE_OVERLAP_LIMIT = 0.5


def _markdown_page_tables(
    page: Page,
    strategy: Literal["lines", "text"],
) -> list[Table]:
    """Return bordered tables, optionally extended by non-overlapping text tables."""
    tables = list(page.find_tables("lines"))
    if strategy == "text":
        for candidate in page.find_tables("text"):
            if all(
                _table_overlap_ratio(candidate.bbox, table.bbox) < _MARKDOWN_TABLE_OVERLAP_LIMIT for table in tables
            ):
                tables.append(candidate)
    tables.sort(key=lambda table: (table.bbox.y0, table.bbox.x0))
    return tables


def _markdown_table_output(
    layout: TextPage,
    tables: list[Table],
    *,
    max_size: int | None,
    remaining_size: int | None,
) -> tuple[list[tuple[float, float, float, float]], list[tuple[tuple[float, float, float, float], str]]]:
    """Render page tables without retaining strings beyond the remaining budget."""
    bboxes = [(table.bbox.x0, table.bbox.y0, table.bbox.x1, table.bbox.y1) for table in tables]
    rendered: list[tuple[tuple[float, float, float, float], str]] = []
    rendered_bytes = 0
    for bbox, table in zip(bboxes, tables, strict=True):
        table_budget = None if remaining_size is None else max(0, remaining_size - rendered_bytes)
        try:
            table_markdown = _markdown.table_to_markdown(
                table.extract(),
                orientation=_markdown.table_orientation(layout, bbox),
                cell_anchors=table._cell_anchors,
                max_size=table_budget,
            )
        except _markdown.MarkdownOutputLimitError:
            raise _markdown_output_limit_error(cast("int", max_size)) from None
        rendered_bytes += _markdown.utf8_size(table_markdown)
        rendered.append((bbox, table_markdown))
    return bboxes, rendered


class _MarkdownBudget(NamedTuple):
    """Public limit and remaining aggregate bytes for one page."""

    limit: int | None
    remaining: int | None


def _bounded_markdown_page_output(
    layout: TextPage,
    levels: dict[float, int],
    tables: list[tuple[tuple[float, float, float, float], str]],
    words: list[WordEntry] | None,
    budget: _MarkdownBudget,
) -> str:
    """Render one page while translating the internal output-limit signal."""
    try:
        return _markdown.page_to_markdown(
            layout,
            levels,
            tables,
            words,
            max_size=None if budget.remaining is None else max(0, budget.remaining),
        )
    except _markdown.MarkdownOutputLimitError:
        raise _markdown_output_limit_error(cast("int", budget.limit)) from None


#: Portrait A4 in PDF units, used for damaged PDFs without a MediaBox.
_DEFAULT_MEDIABOX = (0.0, 0.0, 210.0 * 72.0 / 25.4, 297.0 * 72.0 / 25.4)


@dataclass(frozen=True)
class _BundledFontProvider:
    """One installed locale-specific font package."""

    language: FontLanguage
    extra: str
    sans: Path | bytes
    serif: Path | bytes


_FONT_PROVIDER_SPECS: tuple[tuple[FontLanguage, str, str], ...] = (
    ("ar", "ar", "pylopdf_fonts_ar"),
    ("he", "he", "pylopdf_fonts_he"),
    ("hi", "hi", "pylopdf_fonts_hi"),
    ("ja", "jp", "pylopdf_fonts_jp"),
    ("ko", "ko", "pylopdf_fonts_ko"),
    ("th", "th", "pylopdf_fonts_th"),
    ("zh-CN", "zh-cn", "pylopdf_fonts_zh_cn"),
    ("zh-TW", "zh-tw", "pylopdf_fonts_zh_tw"),
)


def _import_optional_font_module(module_name: str) -> object | None:
    """Import one known data package without hiding its internal failures."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise


@functools.cache
def _bundled_font_providers() -> tuple[_BundledFontProvider, ...]:
    """Locate installed locale font packages."""
    providers: list[_BundledFontProvider] = []
    for language, extra, module_name in _FONT_PROVIDER_SPECS:
        module = _import_optional_font_module(module_name)
        if module is None:
            continue
        font_module = cast("Any", module)
        providers.append(_BundledFontProvider(language, extra, font_module.sans_path(), font_module.serif_path()))
    return tuple(providers)


#: Maximum R/G/B/A component value.
_COLOR_MAX = 255

#: Largest absolute value representable as a finite lopdf PDF real (f32).
_FLOAT32_MAX = float.fromhex("0x1.fffffep+127")
_UINT32_MAX = 0xFFFFFFFF
_MAX_RENDER_WORKERS = 64


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


def _validate_lineheight(lineheight: float | None) -> float | None:
    """Validate a line-height multiplier; ``None`` keeps the font's default line height."""
    if lineheight is None:
        return None
    try:
        resolved = float(lineheight)
    except (TypeError, ValueError) as exc:
        msg = f"lineheight must be a positive number or None: {lineheight!r}"
        raise PdfError(msg) from exc
    if not (math.isfinite(resolved) and resolved > 0):
        msg = f"lineheight must be a positive number or None: {lineheight!r}"
        raise PdfError(msg)
    return resolved


def _validate_textbox_options(
    fontsize: float,
    lineheight: float | None,
    align: int,
    expandtabs: int,
) -> tuple[float, float | None]:
    """Validate textbox layout options and return the normalized size and line height."""
    try:
        resolved_fontsize = float(fontsize)
    except (TypeError, ValueError) as exc:
        msg = f"fontsize must be a positive number: {fontsize!r}"
        raise PdfError(msg) from exc
    if not (math.isfinite(resolved_fontsize) and resolved_fontsize > 0):
        msg = f"fontsize must be a positive number: {fontsize!r}"
        raise PdfError(msg)
    resolved_lineheight = _validate_lineheight(lineheight)
    if isinstance(align, bool) or not isinstance(align, int) or not TEXT_ALIGN_LEFT <= align <= TEXT_ALIGN_JUSTIFY:
        msg = f"align must be one of 0, 1, 2, or 3: {align!r}"
        raise PdfError(msg)
    if isinstance(expandtabs, bool) or not isinstance(expandtabs, int) or expandtabs < 0:
        msg = f"expandtabs must be a non-negative integer: {expandtabs!r}"
        raise PdfError(msg)
    return resolved_fontsize, resolved_lineheight


def _image_input_limit_error(size: int, max_size: int) -> LimitError:
    """Build the public error for an oversized encoded image source."""
    return LimitError(
        "image_input_size",
        f"encoded image input is {size} bytes, exceeding the {max_size}-byte limit",
    )


def _resolve_image_source(
    filename: str | os.PathLike[str] | None,
    stream: bytes | None,
    pixmap: Pixmap | None,
) -> Path | bytes | Pixmap:
    """Resolve exactly one encoded-image or rendered-Pixmap source."""
    source_count = sum(source is not None for source in (filename, stream, pixmap))
    if source_count != 1:
        msg = "specify exactly one of filename, stream, or pixmap"
        raise ValueError(msg)
    if filename is not None:
        return Path(filename)
    if stream is not None:
        return bytes(stream)
    if not isinstance(pixmap, Pixmap):
        msg = f"pixmap must be a Pixmap, not {type(pixmap).__name__}"
        raise TypeError(msg)
    return pixmap


def _validate_image_rotation(rotate: int) -> int:
    """Validate and normalize clockwise image rotation."""
    if isinstance(rotate, bool) or not isinstance(rotate, int):
        msg = f"rotate must be a multiple of 90: {rotate!r}"
        raise TypeError(msg)
    if rotate % 90 != 0:
        msg = f"rotate must be a multiple of 90: {rotate!r}"
        raise ValueError(msg)
    return rotate % 360


def _font_input_limit_error(size: int, max_font_size: int) -> LimitError:
    """Build the public error for an oversized OpenType font source."""
    return LimitError(
        "font_input_size",
        f"font input is {size} bytes, exceeding the {max_font_size}-byte limit",
    )


def _validate_utf8_text_input(
    text: str,
    max_text_size: int | None,
    *,
    expandtabs: int | None = None,
    limit_code: str = "text_input_size",
    input_label: str = "text input",
) -> None:
    """Bound UTF-8 text, including tab expansion, without building encoded bytes."""
    _validate_optional_positive_int("max_text_size", max_text_size)
    if max_text_size is None:
        return

    def raise_limit() -> NoReturn:
        msg = f"{input_label} exceeds the {max_text_size}-byte UTF-8 limit"
        if expandtabs is not None:
            msg += " after tab expansion"
        raise LimitError(limit_code, msg)

    if text.isascii() and (expandtabs is None or "\t" not in text):
        if len(text) > max_text_size:
            raise_limit()
        return

    size = 0
    column = 0
    for char in text:
        if char == "\t" and expandtabs is not None:
            spaces = 0 if expandtabs == 0 else expandtabs - column % expandtabs
            size += spaces
            column += spaces
        else:
            codepoint = ord(char)
            size += (
                1
                if codepoint < _UTF8_TWO_BYTE_MIN
                else 2
                if codepoint < _UTF8_THREE_BYTE_MIN
                else 3
                if codepoint < _UTF8_FOUR_BYTE_MIN
                else 4
            )
            column = 0 if char in {"\n", "\r"} else column + 1
        if size > max_text_size:
            raise_limit()


def _validate_generated_text_lines(text: str, max_text_size: int | None) -> None:
    """Bound physical lines before split or layout object materialization."""
    if max_text_size is not None and text.count("\n") >= _MAX_GENERATED_TEXT_LINES:
        raise _text_line_limit()


def _text_line_limit() -> LimitError:
    """Return the refusal of text input beyond the generated-line limit."""
    return LimitError(
        "text_line_count",
        f"text input exceeds the {_MAX_GENERATED_TEXT_LINES}-line safety limit",
    )


def _insert_text_lines(text: str | Sequence[str], max_text_size: int | None) -> list[str]:
    r"""Return the lines of ``insert_text`` text, split as pymupdf splits them.

    A string is split with :meth:`str.splitlines`, so a trailing line break
    adds no line and ``\v``, ``\f``, ``\x1c`` to ``\x1e``, ``\x85``,
    ``\u2028``, and ``\u2029`` break lines as ``\n``, ``\r``, and ``\r\n`` do.
    A list or tuple holds one line per item, used as given. The text budgets
    apply before a string is split.
    """
    if isinstance(text, (list, tuple)):
        lines = list(text)
        _validate_text_line_list(lines, max_text_size)
        return lines
    if not isinstance(text, str):
        msg = f"text must be a string or a list of strings: {text!r}"
        raise TypeError(msg)
    _validate_utf8_text_input(text, max_text_size)
    if max_text_size is not None and _splitlines_count(text) > _MAX_GENERATED_TEXT_LINES:
        raise _text_line_limit()
    return text.splitlines()


def _splitlines_count(text: str) -> int:
    """Return how many lines :meth:`str.splitlines` makes of ``text`` without splitting it."""
    # One line per break, "\r\n" being a single break, and one more for the
    # text after the last break, when there is any.
    count = sum(map(text.count, _LINE_BREAKS)) - text.count("\r\n")
    return count + 1 if text and text[-1] not in _LINE_BREAKS else count


def _validate_text_line_list(lines: list[str], max_text_size: int | None) -> None:
    """Refuse list items that are not strings and lines beyond the text budgets."""
    for line in lines:
        if not isinstance(line, str):
            msg = f"text must be a string or a list of strings, not a list holding {type(line).__name__}"
            raise TypeError(msg)
    _validate_optional_positive_int("max_text_size", max_text_size)
    if max_text_size is None:
        return
    if len(lines) > _MAX_GENERATED_TEXT_LINES:
        raise _text_line_limit()
    remaining = max_text_size
    for line in lines:
        size = _bounded_utf8_size(line, remaining)
        if size is None:
            code = "text_input_size"
            raise LimitError(code, f"text input exceeds the {max_text_size}-byte UTF-8 limit")
        remaining -= size


def _bounded_utf8_size(text: str, remaining: int) -> int | None:
    """Return UTF-8 size through ``remaining`` without allocating encoded bytes."""
    if text.isascii():
        return len(text) if len(text) <= remaining else None
    size = 0
    for char in text:
        codepoint = ord(char)
        size += (
            1
            if codepoint < _UTF8_TWO_BYTE_MIN
            else 2
            if codepoint < _UTF8_THREE_BYTE_MIN
            else 3
            if codepoint < _UTF8_FOUR_BYTE_MIN
            else 4
        )
        if size > remaining:
            return None
    return size


def _validate_cumulative_utf8_input(
    values: Iterable[str],
    limit: int,
    *,
    limit_code: str,
    input_label: str,
) -> None:
    """Bound aggregate UTF-8 input without joining or encoding its strings."""
    remaining = limit
    for value in values:
        size = _bounded_utf8_size(value, remaining)
        if size is None:
            raise LimitError(
                limit_code,
                f"{input_label} exceeds the {limit}-byte UTF-8 safety limit",
            )
        remaining -= size


def _validate_cumulative_pdf_text_input(
    values: Iterable[str],
    limit: int,
    *,
    limit_code: str,
    input_label: str,
) -> None:
    """Bound aggregate ASCII or UTF-16BE PDF text strings without encoding."""
    remaining = limit
    for value in values:
        size: int | None
        if value.isascii():
            size = len(value)
            if size > remaining:
                size = None
        else:
            size = 2  # UTF-16BE byte-order mark.
            for character in value:
                size += 2 if ord(character) <= _UTF16_BMP_MAX else 4
                if size > remaining:
                    size = None
                    break
        if size is None:
            raise LimitError(
                limit_code,
                f"{input_label} exceeds the {limit}-byte encoded-text safety limit",
            )
        remaining -= size


def _validate_password_input(password: str | None, label: str = "password") -> None:
    """Bound password KDF input before crossing the Python/Rust boundary."""
    if password is not None:
        _validate_utf8_text_input(
            password,
            _MAX_PASSWORD_INPUT_BYTES,
            limit_code="password_input_size",
            input_label=label,
        )


def _resolve_font_source(
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    max_font_size: int | None,
) -> Path | bytes | None:
    """Resolve optional font input without reading a filesystem path."""
    if fontfile is not None:
        if fontbuffer is not None:
            msg = "fontfile and fontbuffer cannot both be specified"
            raise ValueError(msg)
        return Path(fontfile)
    if fontbuffer is not None:
        if max_font_size is not None and len(fontbuffer) > max_font_size:
            raise _font_input_limit_error(len(fontbuffer), max_font_size)
        data = bytes(fontbuffer)
        if max_font_size is not None and len(data) > max_font_size:
            raise _font_input_limit_error(len(data), max_font_size)
        return data
    return None


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

#: Times aliases select an optional locale-specific serif generation font.
_SERIF_BASE14_FONTS = frozenset({"tiro", "tibo", "tiit", "tibi"})

#: Script ranges used only to select an installed CJK locale font package.
_KANA_RANGES = ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0xFF65, 0xFF9F))
_HANGUL_RANGES = (
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7AF),
    (0xD7B0, 0xD7FF),
)
_BOPOMOFO_RANGES = ((0x3100, 0x312F), (0x31A0, 0x31BF))
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_HEBREW_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))
_DEVANAGARI_RANGES = ((0x0900, 0x097F), (0xA8E0, 0xA8FF))
_THAI_RANGES = ((0x0E00, 0x0E7F),)
_HAN_RANGES = (
    (0x2E80, 0x2FFF),
    (0x3400, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_FONT_LANGUAGE_ALIASES: dict[str, FontLanguage] = {
    "ar": "ar",
    "he": "he",
    "hi": "hi",
    "ja": "ja",
    "ko": "ko",
    "th": "th",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
}
_SCRIPT_PROVIDER_RANGES: tuple[tuple[FontLanguage, tuple[tuple[int, int], ...]], ...] = (
    ("ar", _ARABIC_RANGES),
    ("he", _HEBREW_RANGES),
    ("hi", _DEVANAGARI_RANGES),
    ("ja", _KANA_RANGES),
    ("ko", _HANGUL_RANGES),
    ("zh-TW", _BOPOMOFO_RANGES),
    ("th", _THAI_RANGES),
)


def _text_uses_ranges(text: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    """Return whether text contains a scalar in one of the supplied ranges."""
    return any(start <= ord(character) <= end for character in text for start, end in ranges)


def _normalize_font_language(language: str | None) -> FontLanguage | None:
    """Normalize supported BCP 47 font locales."""
    if language is None:
        return None
    if not isinstance(language, str):
        msg = f"font_language must be a string or None: {language!r}"
        raise TypeError(msg)
    normalized = _FONT_LANGUAGE_ALIASES.get(language.replace("_", "-").lower())
    if normalized is None:
        msg = "font_language must be 'ar', 'he', 'hi', 'ja', 'ko', 'th', 'zh-CN', 'zh-TW', or None"
        raise ValueError(msg)
    return normalized


def _normalize_cjk_font_language(language: str) -> CjkFontLanguage:
    """Normalize a locale accepted by the non-embedded CJK fallback API."""
    normalized = _normalize_font_language(language)
    if normalized not in {"ja", "ko", "zh-CN", "zh-TW"}:
        msg = "font_language for fallback must be 'ja', 'ko', 'zh-CN', or 'zh-TW'"
        raise ValueError(msg)
    return normalized


def _provider_for_language(language: FontLanguage) -> _BundledFontProvider | None:
    """Return the installed provider for one normalized locale."""
    return next((provider for provider in _bundled_font_providers() if provider.language == language), None)


def _detected_font_language(text: str) -> FontLanguage | None:
    """Infer an unambiguous script marker, then preserve the JP Han default."""
    markers = [language for language, ranges in _SCRIPT_PROVIDER_RANGES if _text_uses_ranges(text, ranges)]
    if len(markers) > 1:
        msg = "text spans multiple bundled font locales; pass an explicit fontfile or fontbuffer"
        raise ValueError(msg)
    if markers:
        return markers[0]
    if not _text_uses_ranges(text, _HAN_RANGES):
        return None
    installed = {provider.language for provider in _bundled_font_providers()}
    for candidate in ("ja", "zh-CN", "zh-TW", "ko"):
        if candidate in installed:
            return cast("FontLanguage", candidate)
    return None


def _bundled_generation_font(
    text: str,
    fontname: str,
    font_language: str | None,
) -> tuple[Path | bytes, tuple[FontLanguage, str]] | None:
    """Select one optional locale font for a complete generated run.

    Return the font source and its ``(language, kind)`` package face, which
    lets the core share an installed package file across calls.
    """
    language = _normalize_font_language(font_language)
    if language is None:
        language = _detected_font_language(text)
    if language is None:
        return None
    provider = _provider_for_language(language)
    if provider is None:
        extra = next(extra for candidate, extra, _ in _FONT_PROVIDER_SPECS if candidate == language)
        msg = f"font_language={language!r} requires pylopdf[{extra}] or an explicit fontfile/fontbuffer"
        raise ValueError(msg)
    kind = "serif" if fontname in _SERIF_BASE14_FONTS else "sans"
    return (provider.serif if kind == "serif" else provider.sans), (language, kind)


def _resolve_generation_font(
    text: str,
    fontname: str,
    font_data: Path | bytes | None,
    fontindex: int,
    font_language: str | None,
) -> tuple[str | None, Path | bytes | None, tuple[FontLanguage, str] | None]:
    """Resolve explicit, optional locale, or Standard 14 generation input.

    Return the Standard 14 font name, the embedded font source, and the
    package face of an automatically selected installed font.
    """
    normalized_language = _normalize_font_language(font_language)
    if font_data is not None:
        if normalized_language is not None:
            msg = "font_language cannot be combined with fontfile or fontbuffer"
            raise ValueError(msg)
        if isinstance(fontindex, bool) or not isinstance(fontindex, int) or not 0 <= fontindex <= _UINT32_MAX:
            msg = f"fontindex must be an integer from 0 through 4294967295: {fontindex!r}"
            raise ValueError(msg)
        return None, font_data, None
    if fontindex != 0:
        msg = "fontindex requires fontfile or fontbuffer"
        raise ValueError(msg)

    base_font = _BASE14_FONTS.get(fontname)
    if base_font is None:
        msg = f"fontname must be a standard-14 font abbreviation ({sorted(_BASE14_FONTS)}): {fontname!r}"
        raise ValueError(msg)
    bundled_font = _bundled_generation_font(text, fontname, normalized_language)
    if bundled_font is not None:
        source, package_font = bundled_font
        return None, source, package_font
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        msg = (
            "text generation can only print WinAnsi (Latin-1-equivalent) characters without an embedded font. "
            "Install a matching optional font extra or pass fontfile or fontbuffer"
        )
        raise ValueError(msg) from exc
    return base_font, None, None


def _form_generation_font(
    value: str,
    font_language: FontLanguage | None,
) -> tuple[Path | bytes, tuple[FontLanguage, str]] | None:
    """Select an installed font for a form value Helvetica cannot draw.

    An explicit ``font_language`` selects its package even for WinAnsi text.
    """
    if font_language is not None:
        return _bundled_generation_font(value, "helv", font_language)
    try:
        value.encode("cp1252")
    except UnicodeEncodeError:
        return _bundled_generation_font(value, "helv", None)
    return None


#: Standard 14 names that :class:`Font` accepts: pymupdf's short aliases and
#: the PostScript names, matched without regard to case.
_BASE14_FONT_NAMES: dict[str, str] = {
    **_BASE14_FONTS,
    **{name.lower(): name for name in _BASE14_FONTS.values()},
}
_SYMBOLIC_BASE14_NAMES = frozenset({"Symbol", "ZapfDingbats"})

#: Family names of the faces that each font extra installs: sans, then serif.
_FONT_EXTRA_FAMILIES: dict[FontLanguage, tuple[str, str]] = {
    "ar": ("Noto Sans Arabic", "Noto Naskh Arabic"),
    "he": ("Noto Sans Hebrew", "Noto Serif Hebrew"),
    "hi": ("Noto Sans Devanagari", "Noto Serif Devanagari"),
    "ja": ("Noto Sans JP", "Noto Serif JP"),
    "ko": ("Noto Sans KR", "Noto Serif KR"),
    "th": ("Noto Sans Thai", "Noto Serif Thai"),
    "zh-CN": ("Noto Sans SC", "Noto Serif SC"),
    "zh-TW": ("Noto Sans TC", "Noto Serif TC"),
}
_REGULAR_FONT_WEIGHT = 400
_MIN_BOLD_FONT_WEIGHT = 600
_BOLD_FONT_WEIGHT = 700
_MAX_FONT_WEIGHT = 1000
_MAX_UNICODE_CODEPOINT = 0x10FFFF


def _validate_font_weight(weight: object) -> int | None:
    """Validate an optional OS/2 weight class."""
    if weight is None:
        return None
    if isinstance(weight, bool) or not isinstance(weight, int):
        msg = f"weight must be an OS/2 weight class from 1 through {_MAX_FONT_WEIGHT} or None: {weight!r}"
        raise TypeError(msg)
    if not 1 <= weight <= _MAX_FONT_WEIGHT:
        msg = f"weight must be an OS/2 weight class from 1 through {_MAX_FONT_WEIGHT} or None: {weight!r}"
        raise ValueError(msg)
    return weight


def _describe_face(weight: int | None, *, italic: bool) -> str:
    """Describe a requested face for error and warning messages."""
    name = {None: "regular", 400: "regular", _BOLD_FONT_WEIGHT: "bold"}.get(weight, f"weight-{weight}")
    return f"{name} italic" if italic else name


def _standard_font_name(fontname: str) -> str:
    """Resolve a Standard 14 alias or name to its PostScript name."""
    if not isinstance(fontname, str):
        msg = f"fontname must be a string: {fontname!r}"
        raise TypeError(msg)
    name = _BASE14_FONT_NAMES.get(fontname) or _BASE14_FONT_NAMES.get(fontname.lower())
    if name is None:
        msg = (
            f"fontname must be a Standard 14 name or alias such as 'helv' or 'Times-Roman': {fontname!r}; "
            "pymupdf's other built-in fonts are not available, so pass fontfile, fontbuffer, or language"
        )
        raise PdfError(msg)
    return name


def _load_font(
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    fontindex: int,
    max_font_size: int | None,
) -> _Font:
    """Load one face from a bounded file or buffer."""
    if isinstance(fontindex, bool) or not isinstance(fontindex, int) or not 0 <= fontindex <= _UINT32_MAX:
        msg = f"fontindex must be an integer from 0 through {_UINT32_MAX}: {fontindex!r}"
        raise ValueError(msg)
    source = _resolve_font_source(fontfile, fontbuffer, max_font_size)
    if isinstance(source, Path):
        return _Font.from_file(os.fspath(source), fontindex, max_font_size)
    return _Font.from_bytes(cast("bytes", source), fontindex, max_font_size)


def _named_or_loaded_font(
    fontname: str | None,
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    fontindex: int,
    max_font_size: int | None,
) -> _Font:
    """Return a Standard 14 font by name, or load a face from a file or buffer."""
    if fontname is None:
        return _load_font(fontfile, fontbuffer, fontindex, max_font_size)
    if fontindex != 0:
        msg = "fontindex requires fontfile or fontbuffer"
        raise PdfError(msg)
    return _Font.standard(_standard_font_name(fontname))


def _requested_font_weight(weight: int | None, *, is_bold: bool) -> int | None:
    """Combine ``weight`` and ``is_bold`` into the weight class a lookup must match."""
    if not is_bold:
        return weight
    if weight is None:
        return _BOLD_FONT_WEIGHT
    if weight < _MIN_BOLD_FONT_WEIGHT:
        msg = f"weight={weight} contradicts is_bold=True"
        raise PdfError(msg)
    return weight


def _font_extra_family(family: str) -> tuple[FontLanguage, bool]:
    """Return the font extra and style that install a family."""
    if not isinstance(family, str):
        msg = f"family must be a string: {family!r}"
        raise TypeError(msg)
    for language, names in _FONT_EXTRA_FAMILIES.items():
        for serif, name in enumerate(names):
            if name.casefold() == family.casefold():
                return language, bool(serif)
    msg = f"no pylopdf font extra installs the family {family!r}; pass its font as fontfile or fontbuffer"
    raise PdfError(msg)


def _font_extra_face(  # noqa: PLR0913 - the lookup keeps every Font argument explicit.
    language: FontLanguage,
    *,
    serif: bool,
    weight: int | None,
    italic: bool,
    allow_regular_fallback: bool,
    max_font_size: int | None,
) -> _Font:
    """Select the face that an installed font extra provides for one style.

    An extra installs one face per style. A variable face, which the ``ar``,
    ``he``, ``hi``, and ``th`` extras install, provides every weight on its
    weight axis as an instance and is drawn at the regular weight when no
    weight is requested. A static face must match a requested weight, and
    any face must match a requested italic.
    """
    extra = next(extra for candidate, extra, _ in _FONT_PROVIDER_SPECS if candidate == language)
    provider = _provider_for_language(language)
    if provider is None:
        msg = f"language={language!r} requires pylopdf[{extra}]; pass fontfile or fontbuffer instead"
        raise PdfError(msg)
    kind = "serif" if serif else "sans"
    source = provider.serif if serif else provider.sans
    if isinstance(source, Path):
        face = _Font.from_file(os.fspath(source), 0, max_font_size, (language, kind))
    else:
        face = _Font.from_bytes(source, 0, max_font_size)
    weight_range = face.weight_range
    if not italic:
        if weight is None:
            return _regular_instance(face)
        if weight_range is None and weight == face.weight:
            return face
        if weight_range is not None and weight_range[0] <= weight <= weight_range[2]:
            return face.with_weight(weight)
    family = face.family or _FONT_EXTRA_FAMILIES[language][serif]
    if allow_regular_fallback:
        msg = f"pylopdf[{extra}] has no {_describe_face(weight, italic=italic)} {family!r} face; using its own face"
        # Point at the caller of Font(): this helper and __init__ sit between.
        _warnings.warn(msg, PylopdfWarning, stacklevel=3)
        return _regular_instance(face)
    described = _describe_face(weight, italic=italic)
    if weight_range is not None and not italic:
        low, _, high = weight_range
        msg = (
            f"pylopdf[{extra}] installs {family!r} with weights {low:g} through {high:g}; "
            f"pass its {described} face as fontfile or fontbuffer, or set allow_regular_fallback=True"
        )
    else:
        msg = (
            f"pylopdf[{extra}] installs one {family!r} face; pass its {described} "
            "face as fontfile or fontbuffer, or set allow_regular_fallback=True"
        )
    raise PdfError(msg)


def _regular_instance(face: _Font) -> _Font:
    """Return a face at the regular weight when it has a weight axis.

    The default instance of a variable face need not be regular: that of
    Noto Sans Hebrew in ``pylopdf[he]`` is Thin.
    """
    return face if face.weight_range is None else face.with_weight(_REGULAR_FONT_WEIGHT)


def _face_at_weight(face: _Font, weight: int) -> _Font:
    """Select ``weight`` on a face loaded from ``fontfile`` or ``fontbuffer``.

    A variable face provides the instance at any weight on its weight axis and
    refuses a weight outside it. Any other face has one weight, its OS/2
    weight class, which must be the one requested: pylopdf never imitates a
    weight that the font does not define.
    """
    if face.weight_range is not None:
        return face.with_weight(weight)
    if face.weight == weight:
        return face
    current = _describe_face(face.weight, italic=False)
    requested = _describe_face(weight, italic=False)
    msg = (
        f"the font has no weight axis, and its only face is {current}, not {requested}; "
        f"load the {requested} face's file instead"
    )
    raise PdfError(msg)


def _package_face_weight(package_font: tuple[FontLanguage, str] | None) -> int | None:
    """Return the weight that drawing selects on an installed package face.

    A face of an installed font extra is drawn at the regular weight, as
    ``Font(language=...)`` selects it; any other font input is drawn as given.
    """
    return None if package_font is None else _REGULAR_FONT_WEIGHT


class Font:
    """A font for generated text and its measurement, as pymupdf's ``Font``.

    Pass exactly one source. ``fontname`` names a Standard 14 font by its
    PostScript name or pymupdf alias, such as ``"Times-Bold"`` or ``"helv"``.
    ``fontfile`` or ``fontbuffer`` loads an OpenType face, with ``fontindex``
    selecting a face in a collection. ``language`` selects the face of an
    installed font extra such as ``pylopdf[jp]`` (``is_serif=True`` for its
    serif face), and ``family`` selects an installed extra's face by family
    name, such as ``"Noto Sans JP"``. pylopdf has no default font and keeps no
    font registry; load any other face, such as a bold weight, from its file.

    A face from ``fontfile`` or ``fontbuffer`` is used as given, a variable
    font at its default instance, which need not be regular: the variable
    Noto Sans CJK JP defaults to Thin. ``weight``, a weight class from 1
    through 1000, selects the instance at that weight on a variable font's
    weight axis instead and refuses a weight outside the axis, so
    ``weight=400`` differs from an omitted weight for such a font. A static
    face must already have the requested weight class.

    For ``language`` and ``family``, ``is_bold`` and ``is_italic`` request a
    style and ``weight`` a weight class. The extras install one face per
    style. The ``ar``, ``he``, ``hi``, and ``th`` extras
    install variable faces, which provide the weights on their weight axis as
    instances (100 through 900, or 400 through 700 for the Noto Naskh Arabic
    serif face) and are drawn at the regular weight unless another is
    requested; ``is_bold=True`` selects the bold instance. A request that the
    installed face does not provide, including a weight outside its axis,
    raises :class:`PdfError` instead of imitating a weight by stroking or
    overprinting glyphs; ``allow_regular_fallback=True`` explicitly uses the
    extra's regular face and emits a :class:`PylopdfWarning`. Apart from
    ``weight`` with ``fontfile`` and ``fontbuffer``, these options do not
    apply to the other sources and raise there, where pymupdf would ignore
    them.

    A Font reads its input once. Pass it as ``font`` to
    :meth:`Page.insert_text` and :meth:`Page.insert_textbox`, and an OpenType
    face also to :meth:`Document.set_form_field`, on any document, and share
    it between threads. :meth:`text_length` measures with the shaping and
    advances that drawing uses, so a measured line is as wide as the drawn
    line. Standard 14 fonts are drawn without embedding and measure with
    Adobe's AFM widths. Font input defaults to a 64 MiB boundary;
    ``max_font_size=None`` opts trusted input out, and refusal raises
    :class:`LimitError` with code ``font_input_size``.
    """

    def __init__(  # noqa: PLR0913 - pymupdf's sources plus keyword-only lookup options.
        self,
        fontname: str | None = None,
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        *,
        language: FontLanguage | None = None,
        is_bold: bool = False,
        is_italic: bool = False,
        is_serif: bool = False,
        weight: int | None = None,
        fontindex: int = 0,
        family: str | None = None,
        allow_regular_fallback: bool = False,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
    ) -> None:
        """Select a Standard 14 font, load a face, or select an installed extra's face."""
        _validate_optional_positive_int("max_font_size", max_font_size)
        resolved_weight = _validate_font_weight(weight)
        sources = [
            name
            for name, value in (
                ("fontname", fontname),
                ("fontfile", fontfile),
                ("fontbuffer", fontbuffer),
                ("family", family),
                ("language", language),
            )
            if value is not None
        ]
        if not sources:
            msg = "pylopdf has no default font; pass fontname, fontfile, fontbuffer, family, or language"
            raise PdfError(msg)
        if len(sources) > 1:
            msg = f"pass one font source, not {' and '.join(sources)}"
            raise PdfError(msg)
        if fontname is not None or fontfile is not None or fontbuffer is not None:
            if is_bold or is_italic or is_serif or allow_regular_fallback:
                msg = (
                    "is_bold, is_italic, is_serif, and allow_regular_fallback select the face of an installed "
                    "font extra; fontname names one Standard 14 variant and a font file is used as given, "
                    "or at the instance that weight selects on a variable font"
                )
                raise PdfError(msg)
            if fontname is not None and resolved_weight is not None:
                msg = "weight selects an instance of a variable font file; fontname names one Standard 14 variant"
                raise PdfError(msg)
            face = _named_or_loaded_font(fontname, fontfile, fontbuffer, fontindex, max_font_size)
            self._font = face if resolved_weight is None else _face_at_weight(face, resolved_weight)
            return
        if fontindex != 0:
            msg = "fontindex requires fontfile or fontbuffer"
            raise PdfError(msg)
        requested = _requested_font_weight(resolved_weight, is_bold=bool(is_bold))
        if family is not None:
            if is_serif:
                msg = "is_serif cannot be combined with family, which already names the face"
                raise PdfError(msg)
            extra_language, serif = _font_extra_family(family)
        else:
            extra_language = cast("FontLanguage", _normalize_font_language(language))
            serif = bool(is_serif)
        self._font = _font_extra_face(
            extra_language,
            serif=serif,
            weight=requested,
            italic=bool(is_italic),
            allow_regular_fallback=allow_regular_fallback,
            max_font_size=max_font_size,
        )

    @property
    def name(self) -> str | None:
        """The full name of an OpenType face or the PostScript name of a Standard 14 font.

        An instance of a variable face takes its style from the instance, as in
        ``Noto Sans Hebrew Bold``; generated text embeds it under the
        instance's PostScript name, ``NotoSansHebrew-Bold``.
        """
        return self._font.name

    @property
    def family(self) -> str | None:
        """The typographic family name (name ID 16, else 1), or ``None``."""
        return self._font.family

    @property
    def weight(self) -> int:
        """The weight: 400 for regular and 700 for bold faces.

        An instance selected on a variable face's weight axis reports the axis
        value it is drawn at; a face used as given reports its OS/2 weight
        class.
        """
        return self._font.weight

    @property
    def is_bold(self) -> bool:
        """Whether the face is bold by its weight class or style flags."""
        return self._font.bold

    @property
    def is_italic(self) -> bool:
        """Whether the face is marked italic or oblique."""
        return self._font.italic

    @property
    def is_serif(self) -> bool:
        """Whether the face has serifs, by its PANOSE class or family name."""
        return self._font.serif

    @property
    def units_per_em(self) -> int:
        """The design units per em of the face; 1000 for Standard 14 fonts."""
        return self._font.units_per_em

    @property
    def ascender(self) -> float:
        """Ascent above the baseline as a fraction of the font size, as pymupdf reports it.

        An OpenType face reports what FreeType reads for MuPDF: the OS/2
        typographic ascender when the face sets ``USE_TYPO_METRICS``, else
        the ``hhea`` ascender, or the typographic or Windows one when both
        ``hhea`` values are zero; MuPDF replaces a value that is not positive
        or exceeds 8 with 0.8. A Standard 14 font reports the bounding box of
        the URW font that MuPDF draws it with, such as 1.075 for Helvetica. A
        variable face reports its default instance. :meth:`Page.insert_text`
        and :meth:`Page.insert_textbox` place lines with :attr:`ascender` and
        :attr:`descender`.
        """
        return self._font.ascender

    @property
    def descender(self) -> float:
        """Descent below the baseline as a negative fraction of the font size, as pymupdf reports it.

        It comes from the same table as :attr:`ascender`. MuPDF negates a
        positive value and replaces one beyond 3 either way with -0.2.
        """
        return self._font.descender

    def text_length(
        self,
        text: str,
        fontsize: float = 11,
        *,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> float:
        """Return the width of one line of ``text`` at ``fontsize`` in PDF units.

        OpenType text is shaped with HarfRust exactly as drawing shapes it, so
        the result is the advance that :meth:`Page.insert_text` draws,
        including kerning and ligatures; Standard 14 text sums the AFM widths
        of its WinAnsi characters. Measure each line separately: line breaks
        raise :class:`PdfError`. A character the font has no glyph for raises
        :class:`PdfError`, as drawing does. Text input defaults to a 1 MiB
        UTF-8 boundary; ``max_text_size=None`` opts trusted input out, and
        refusal raises :class:`LimitError` with code ``text_input_size``.
        """
        if not isinstance(text, str):
            msg = f"text must be a string: {text!r}"
            raise TypeError(msg)
        _validate_utf8_text_input(text, max_text_size)
        if "\n" in text or "\r" in text:
            msg = "text_length measures one line; split text at line breaks first"
            raise PdfError(msg)
        try:
            size = float(fontsize)
        except (TypeError, ValueError) as exc:
            msg = f"fontsize must be a positive number: {fontsize!r}"
            raise ValueError(msg) from exc
        if not (math.isfinite(size) and size > 0):
            msg = f"fontsize must be a positive number: {fontsize!r}"
            raise ValueError(msg)
        return self._font.text_length(text, size, max_text_size)

    def has_glyph(self, chr: int | str) -> int:  # noqa: A002 - pymupdf's parameter name.
        """Return the glyph number of the code point ``chr``, or 0 when the font has none.

        ``chr`` is an integer code point, as in pymupdf, or a one-character
        string. A Standard 14 font has no glyph numbers because it is not
        embedded; for it, the number is the character's code in the font's
        encoding.
        """
        if isinstance(chr, str):
            if len(chr) != 1:
                msg = f"chr must be a code point or a one-character string: {chr!r}"
                raise ValueError(msg)
            codepoint = ord(chr)
        elif isinstance(chr, int) and not isinstance(chr, bool):
            if not 0 <= chr <= _MAX_UNICODE_CODEPOINT:
                msg = f"chr must be a Unicode code point: {chr!r}"
                raise ValueError(msg)
            codepoint = chr
        else:
            msg = f"chr must be a code point or a one-character string: {chr!r}"
            raise TypeError(msg)
        return self._font.has_glyph(codepoint)

    def __repr__(self) -> str:
        """Return the font's name."""
        return f"Font({self.name!r})"


def _require_winansi(text: str) -> None:
    """Refuse text that a Standard 14 font cannot print."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        msg = (
            "Standard 14 fonts can only print WinAnsi (Latin-1-equivalent) characters; "
            "pass an OpenType Font for other text"
        )
        raise ValueError(msg) from exc


def _validate_font_choice(
    font: Font,
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    fontindex: int,
    font_language: str | None,
) -> _Font:
    """Return the core face of a ``font`` argument that no other source contradicts."""
    if not isinstance(font, Font):
        msg = f"font must be a pylopdf.Font, not {type(font).__name__}"
        raise TypeError(msg)
    if fontfile is not None or fontbuffer is not None or fontindex != 0 or font_language is not None:
        msg = "font cannot be combined with fontfile, fontbuffer, fontindex, or font_language"
        raise ValueError(msg)
    return font._font


def _validate_form_font(
    font: Font | None,
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    fontindex: int,
    font_language: str | None,
) -> _Font | None:
    """Return the OpenType face of a form ``font`` argument, or ``None`` without one.

    Form appearances embed the face they draw; the Standard 14 path of form
    filling draws Helvetica only.
    """
    if font is None:
        return None
    core_font = _validate_font_choice(font, fontfile, fontbuffer, fontindex, font_language)
    if core_font.standard_name is not None:
        msg = (
            f"set_form_field draws with an OpenType Font, not the Standard 14 font {core_font.standard_name!r}; "
            "omit font to draw WinAnsi text in Helvetica"
        )
        raise PdfError(msg)
    return core_font


#: Path points that one :class:`Shape` may hold until it is committed.
_MAX_SHAPE_PATH_POINTS = 1_048_576
#: pymupdf's tolerance for zero lengths in Shape geometry.
_SHAPE_EPSILON = 1e-5
#: The control-point factor of pymupdf's ``draw_curve``.
_SHAPE_KAPPA = 0.55228474983
#: The height of ``draw_squiggle``'s helper points in units of its breadth.
_SQUIGGLE_HEIGHT = 2.4142135623765633
_SHAPE_CAPS_AND_JOINS = (0, 1, 2)


def _shape_number(value: object, name: str) -> float:
    """Return a finite number within PDF real range."""
    if isinstance(value, (bool, str, bytes)) or not hasattr(value, "__float__"):
        msg = f"{name} must be a number: {value!r}"
        raise PdfError(msg)
    number = float(cast("float", value))
    if not (math.isfinite(number) and abs(number) <= _FLOAT32_MAX):
        msg = f"{name} must be a finite number within PDF real range: {value!r}"
        raise PdfError(msg)
    return number


def _path_point_limit() -> LimitError:
    """Return the refusal for a shape that would hold too many path points."""
    code = "path_point_count"
    return LimitError(code, f"a shape exceeds the {_MAX_SHAPE_PATH_POINTS}-point path safety limit")


def _distance(a: Point, b: Point) -> float:
    """Return the distance between two points as pymupdf computes it."""
    dx, dy = a.x - b.x, a.y - b.y
    return math.sqrt(dx * dx + dy * dy)


def _shape_point(value: object, name: str) -> Point:
    """Return a display-coordinate point ``(x, y)``."""
    try:
        x, y = cast("Sequence[object]", value)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a point (x, y): {value!r}"
        raise PdfError(msg) from exc
    return Point(_shape_number(x, name), _shape_number(y, name))


def _shape_rect(value: object, name: str) -> Rect:
    """Return a display-coordinate rectangle ``(x0, y0, x1, y1)``."""
    try:
        x0, y0, x1, y1 = cast("Sequence[object]", value)
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a rectangle (x0, y0, x1, y1): {value!r}"
        raise PdfError(msg) from exc
    return Rect(*(_shape_number(v, name) for v in (x0, y0, x1, y1)))


def _shape_quad(value: object, name: str) -> tuple[Point, Point, Point, Point]:
    """Return pymupdf's quad corners ``(ul, ur, ll, lr)`` of a rectangle or quad."""
    try:
        items = list(cast("Iterable[object]", value))
    except TypeError as exc:
        msg = f"{name} must be a rectangle or four points: {value!r}"
        raise PdfError(msg) from exc
    if len(items) != 4:  # noqa: PLR2004 - a rectangle or a quad has four items.
        msg = f"{name} must be a rectangle or four points: {value!r}"
        raise PdfError(msg)
    if hasattr(items[0], "__float__"):
        x0, y0, x1, y1 = _shape_rect(items, name)
        return Point(x0, y0), Point(x1, y0), Point(x0, y1), Point(x1, y1)
    ul, ur, ll, lr = (_shape_point(item, name) for item in items)
    return ul, ur, ll, lr


def _shape_color(value: object, name: str) -> tuple[float, ...] | None:
    """Return a 1-, 3-, or 4-component colour from 0 to 1, or ``None``."""
    if value is None:
        return None
    channels = (value,) if hasattr(value, "__float__") and not isinstance(value, (bool, str)) else value
    try:
        values = tuple(float(channel) for channel in cast("Iterable[Any]", channels))
    except (TypeError, ValueError) as exc:
        msg = f"{name} must be a gray, RGB, or CMYK colour with components from 0 to 1: {value!r}"
        raise PdfError(msg) from exc
    if len(values) not in (1, 3, 4) or not all(0.0 <= channel <= 1.0 for channel in values):
        msg = f"{name} must be a gray, RGB, or CMYK colour with components from 0 to 1: {value!r}"
        raise PdfError(msg)
    return values


def _shape_unit(value: object, name: str) -> float:
    """Return a number from 0 to 1."""
    number = _shape_number(value, name)
    if not 0.0 <= number <= 1.0:
        msg = f"{name} must be a number from 0 to 1: {value!r}"
        raise PdfError(msg)
    return number


def _shape_line_style(value: object, name: str) -> int:
    """Return a line cap or join style: 0, 1, or 2."""
    if isinstance(value, bool) or value not in _SHAPE_CAPS_AND_JOINS:
        msg = f"{name} must be 0, 1, or 2: {value!r}"
        raise PdfError(msg)
    return cast("int", value)


def _shape_dashes(value: object) -> tuple[list[float], float] | None:
    """Parse pymupdf's PDF dash string, such as ``"[3 4] 0"``; ``None`` is solid."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        msg = f"dashes must be a PDF dash pattern such as '[3 4] 0': {value!r}"
        raise PdfError(msg)
    opening, _, rest = value.strip().partition("[")
    array, closing, phase = rest.partition("]")
    try:
        if opening or not closing:
            raise ValueError  # noqa: TRY301 - one message for every malformed pattern.
        lengths = [_shape_number(float(item), "dashes") for item in array.split()]
        offset = _shape_number(float(phase), "dashes")
    except (ValueError, PdfError) as exc:
        msg = f"dashes must be a PDF dash pattern such as '[3 4] 0': {value!r}"
        raise PdfError(msg) from exc
    if not lengths:
        return None
    if any(length < 0 for length in lengths) or not any(lengths):
        msg = f"dash lengths must be non-negative numbers, not all zero: {value!r}"
        raise PdfError(msg)
    return lengths, offset


class Shape:
    """Vector paths and text drawn on one page in one step, as pymupdf's ``Shape``.

    Create one with :meth:`Page.new_shape`. The ``draw_*`` methods add path
    segments in display coordinates and return pymupdf's end point;
    :meth:`finish` strokes and fills everything drawn since the previous
    ``finish``; :meth:`insert_text` and :meth:`insert_textbox` add text; and
    :meth:`commit` draws the whole shape on the page. As in pymupdf, the
    finished paths are drawn in order and all text above them, in insertion
    order. A shape can be committed again after more drawing.

    A commit places one Form XObject. Paths and text in embedded OpenType
    fonts are generated together, so each font is embedded once per commit
    however many lines use it. Coordinates follow pylopdf's display space with
    a top-left origin and the page rotation resolved, and colours are gray,
    RGB, or CMYK tuples from 0 to 1.

    ``with page.new_shape() as shape:`` commits when the block ends normally
    and draws nothing after an exception. A shape discarded with drawing that
    was never committed emits a :class:`ResourceWarning`. Where pymupdf
    silently drops or blackens drawing, pylopdf raises :class:`PdfError`:
    :meth:`commit` refuses drawing that :meth:`finish` did not style, and
    :meth:`finish` refuses a style that draws nothing. ``morph=`` and ``oc=``
    are not available. A shape holds at most 1,048,576 path points until it
    is committed; more raise :class:`LimitError` with code
    ``path_point_count``.
    """

    #: The page that :meth:`commit` draws on.
    page: Page
    #: The page's document.
    doc: Document
    #: The end point of the last ``draw_*`` call, or ``None`` after
    #: :meth:`finish` and :meth:`commit`.
    last_point: Point | None
    #: The rectangle that encloses every point that ``draw_*`` added since the
    #: last :meth:`commit`, or ``None``; as in pymupdf, ``draw_circle`` and
    #: ``draw_sector`` leave it unchanged.
    rect: Rect | None

    def __init__(self, page: Page) -> None:
        """Start an empty shape on ``page``."""
        if not isinstance(page, Page):
            msg = f"page must be a pylopdf.Page, not {type(page).__name__}"
            raise TypeError(msg)
        page._page_number()
        self.page = page
        self.doc = page.parent
        self.last_point = None
        self.rect = None
        self._record = _Shape()
        self._operators: list[str] = []
        self._coordinates: list[float] = []
        self._points = 0

    def __enter__(self) -> Self:
        """Return the shape; leaving the block commits it."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit after a normal exit; discard the drawing after an exception."""
        if exc_type is None:
            self.commit()
        else:
            self._discard()

    def __del__(self) -> None:
        """Warn when drawing is discarded without a commit."""
        try:
            pending = bool(self._operators) or not self._record.is_empty()
        except AttributeError:
            return
        if pending:
            with suppress(Exception):
                _warnings.warn(
                    "a Shape was discarded with drawing that commit() never wrote",
                    ResourceWarning,
                    stacklevel=2,
                    source=self,
                )

    def _discard(self) -> None:
        """Forget everything recorded since the last commit."""
        self._record.clear()
        self._operators.clear()
        self._coordinates.clear()
        self._points = 0
        self.last_point = None
        self.rect = None

    def _reserve_points(self, count: int) -> None:
        """Charge ``count`` path points against the shape's limit."""
        if self._points + count > _MAX_SHAPE_PATH_POINTS:
            raise _path_point_limit()
        self._points += count

    def _path(self, operator: str, *points: Point) -> None:
        """Append one path operator and its points."""
        self._reserve_points(len(points))
        self._operators.append(operator)
        for point in points:
            self._coordinates.extend(point)

    def _move_unless_at(self, point: Point) -> None:
        """Start a subpath at ``point`` unless the path already ends there."""
        if self.last_point != point:
            self._path("m", point)

    @staticmethod
    def horizontal_angle(C: Sequence[float], P: Sequence[float]) -> float:  # noqa: N803 - pymupdf's names.
        """Return the angle in radians between the horizontal and the line from ``C`` to ``P``."""
        c = _shape_point(C, "C")
        p = _shape_point(P, "P")
        sx, sy = p.x - c.x, p.y - c.y
        squared = sx * sx + sy * sy
        if squared < _SHAPE_EPSILON:
            sx = sy = 0.0
        else:
            length = math.sqrt(squared)
            sx, sy = sx / length, sy / length
        alfa = math.asin(abs(sy))
        if sx < 0:
            alfa = -(math.pi - alfa) if sy <= 0 else math.pi - alfa
        elif sy < 0:
            alfa = -alfa
        return alfa

    def updateRect(self, x: Sequence[float]) -> None:  # noqa: N802 - pymupdf's name.
        """Enlarge :attr:`rect` to enclose a point or a rectangle."""
        if len(x) == 2:  # noqa: PLR2004 - a point has two coordinates.
            point = _shape_point(x, "x")
            box = Rect(point.x, point.y, point.x, point.y)
        else:
            box = _shape_rect(x, "x")
        if self.rect is None:
            self.rect = box
        else:
            self.rect = Rect(
                min(self.rect.x0, box.x0),
                min(self.rect.y0, box.y0),
                max(self.rect.x1, box.x1),
                max(self.rect.y1, box.y1),
            )

    def draw_line(self, p1: Sequence[float], p2: Sequence[float]) -> Point:
        """Draw a line from ``p1`` to ``p2`` and return ``p2``."""
        start = _shape_point(p1, "p1")
        end = _shape_point(p2, "p2")
        if self.last_point != start:
            self._path("m", start)
            self.last_point = start
            self.updateRect(start)
        self._path("l", end)
        self.updateRect(end)
        self.last_point = end
        return end

    def draw_polyline(self, points: Iterable[Sequence[float]]) -> Point:
        """Draw connected straight segments through ``points`` and return the last point."""
        try:
            iterator = iter(points)
        except TypeError as exc:
            msg = f"points must be an iterable of points: {points!r}"
            raise PdfError(msg) from exc
        remaining = _MAX_SHAPE_PATH_POINTS - self._points
        polyline: list[Point] = []
        for item in iterator:
            if len(polyline) >= remaining:
                raise _path_point_limit()
            polyline.append(_shape_point(item, "points"))
        if len(polyline) < 2:  # noqa: PLR2004 - a segment needs two points.
            msg = "points must contain at least two points"
            raise PdfError(msg)
        first, *rest = polyline
        if self.last_point != first:
            self._path("m", first)
        self.updateRect(first)
        for point in rest:
            self._path("l", point)
            self.updateRect(point)
        self.last_point = polyline[-1]
        return self.last_point

    def draw_bezier(
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        p3: Sequence[float],
        p4: Sequence[float],
    ) -> Point:
        """Draw a cubic Bézier curve from ``p1`` to ``p4`` and return ``p4``."""
        points = [_shape_point(p, name) for p, name in ((p1, "p1"), (p2, "p2"), (p3, "p3"), (p4, "p4"))]
        start, control1, control2, end = points
        self._move_unless_at(start)
        self._path("c", control1, control2, end)
        for point in points:
            self.updateRect(point)
        self.last_point = end
        return end

    def draw_curve(self, p1: Sequence[float], p2: Sequence[float], p3: Sequence[float]) -> Point:
        """Draw a curve from ``p1`` to ``p3`` that ``p2`` pulls, and return ``p3``."""
        start = _shape_point(p1, "p1")
        corner = _shape_point(p2, "p2")
        end = _shape_point(p3, "p3")
        control1 = Point(
            start.x + (corner.x - start.x) * _SHAPE_KAPPA,
            start.y + (corner.y - start.y) * _SHAPE_KAPPA,
        )
        control2 = Point(
            end.x + (corner.x - end.x) * _SHAPE_KAPPA,
            end.y + (corner.y - end.y) * _SHAPE_KAPPA,
        )
        return self.draw_bezier(start, control1, control2, end)

    def draw_oval(self, tetra: Sequence[Any]) -> Point:
        """Draw the ellipse inside a rectangle or quad and return its left midpoint."""
        ul, ur, ll, lr = _shape_quad(tetra, "tetra")
        top = Point(ul.x + (ur.x - ul.x) * 0.5, ul.y + (ur.y - ul.y) * 0.5)
        right = Point(ur.x + (lr.x - ur.x) * 0.5, ur.y + (lr.y - ur.y) * 0.5)
        bottom = Point(ll.x + (lr.x - ll.x) * 0.5, ll.y + (lr.y - ll.y) * 0.5)
        left = Point(ul.x + (ll.x - ul.x) * 0.5, ul.y + (ll.y - ul.y) * 0.5)
        if self.last_point != left:
            self._path("m", left)
            self.last_point = left
        self.draw_curve(left, ll, bottom)
        self.draw_curve(bottom, lr, right)
        self.draw_curve(right, ur, top)
        self.draw_curve(top, ul, left)
        corners = (ul, ur, ll, lr)
        self.updateRect(
            (
                min(p.x for p in corners),
                min(p.y for p in corners),
                max(p.x for p in corners),
                max(p.y for p in corners),
            )
        )
        self.last_point = left
        return left

    def draw_circle(self, center: Sequence[float], radius: float) -> Point:
        """Draw a circle and return its leftmost point."""
        middle = _shape_point(center, "center")
        size = _shape_number(radius, "radius")
        if not size > _SHAPE_EPSILON:
            msg = f"radius must be positive: {radius!r}"
            raise PdfError(msg)
        return self.draw_sector(middle, Point(middle.x - size, middle.y), 360, fullSector=False)

    def draw_sector(
        self,
        center: Sequence[float],
        point: Sequence[float],
        beta: float,
        fullSector: bool = True,  # noqa: FBT001, FBT002, N803 - pymupdf's positional parameter.
    ) -> Point:
        """Draw an arc of ``beta`` degrees around ``center`` from ``point``.

        A positive angle runs counter-clockwise on the page. With
        ``fullSector`` the arc's ends are joined to the centre. Return the
        arc's end point.
        """
        c = _shape_point(center, "center")
        start = _shape_point(point, "point")
        betar = math.radians(-_shape_number(beta, "beta"))
        rad = _distance(start, c)
        if not rad > _SHAPE_EPSILON:
            msg = "radius must be positive: point is the centre"
            raise PdfError(msg)
        w360 = math.radians(math.copysign(360, betar)) * (-1)
        w90 = math.radians(math.copysign(90, betar))
        w45 = w90 / 2
        while abs(betar) > 2 * math.pi:
            betar += w360
        self._move_unless_at(start)
        self.last_point = start
        end = Point(0.0, 0.0)
        p = start
        alfa = self.horizontal_angle(c, start)
        while abs(betar) > abs(w90):
            end = Point(c.x + math.cos(alfa + w90) * rad, c.y + math.sin(alfa + w90) * rad)
            r = Point(
                c.x + math.cos(alfa + w45) * rad / math.cos(w45),
                c.y + math.sin(alfa + w45) * rad / math.cos(w45),
            )
            kappah = (1 - math.cos(w45)) * 4 / 3 / _distance(r, end)
            kappa = kappah * _distance(p, end)
            self._path(
                "c",
                Point(p.x + (r.x - p.x) * kappa, p.y + (r.y - p.y) * kappa),
                Point(end.x + (r.x - end.x) * kappa, end.y + (r.y - end.y) * kappa),
                end,
            )
            betar -= w90
            alfa += w90
            p = end
        if abs(betar) > 1e-3:  # noqa: PLR2004 - pymupdf's threshold for a remaining arc.
            beta2 = betar / 2
            end = Point(c.x + math.cos(alfa + betar) * rad, c.y + math.sin(alfa + betar) * rad)
            r = Point(
                c.x + math.cos(alfa + beta2) * rad / math.cos(beta2),
                c.y + math.sin(alfa + beta2) * rad / math.cos(beta2),
            )
            kappah = (1 - math.cos(beta2)) * 4 / 3 / _distance(r, end)
            kappa = kappah * _distance(p, end) / (1 - math.cos(betar))
            self._path(
                "c",
                Point(p.x + (r.x - p.x) * kappa, p.y + (r.y - p.y) * kappa),
                Point(end.x + (r.x - end.x) * kappa, end.y + (r.y - end.y) * kappa),
                end,
            )
        if fullSector:
            self._path("m", start)
            self._path("l", c)
            self._path("l", end)
        self.last_point = end
        return end

    def draw_rect(self, rect: Sequence[float], *, radius: float | Sequence[float] | None = None) -> Point:
        """Draw a rectangle and return its top-left corner.

        ``radius`` rounds the corners: a fraction above 0 and at most 0.5 of
        the shorter side, or a pair of fractions of the width and height.
        """
        r = _shape_rect(rect, "rect")
        width = max(r.x1 - r.x0, 0.0)
        height = max(r.y1 - r.y0, 0.0)
        top_left, top_right = Point(r.x0, r.y0), Point(r.x1, r.y0)
        bottom_left, bottom_right = Point(r.x0, r.y1), Point(r.x1, r.y1)
        if radius is None:
            self._path("m", bottom_left)
            self._path("l", Point(r.x0 + width, r.y1))
            self._path("l", Point(r.x0 + width, r.y1 - height))
            self._path("l", Point(r.x0, r.y1 - height))
            self._path("h")
            self.updateRect(r)
            self.last_point = top_left
            return top_left
        if isinstance(radius, (int, float)) and not isinstance(radius, bool):
            if not 0 < radius <= 0.5:  # noqa: PLR2004 - pymupdf's largest radius.
                msg = f"bad radius value {radius}."
                raise PdfError(msg)
            d = min(width, height) * radius
            px, py = d, d
        else:
            try:
                rx, ry = (_shape_number(v, "radius") for v in cast("Sequence[object]", radius))
            except (TypeError, ValueError) as exc:
                msg = f"bad radius value {radius}."
                raise PdfError(msg) from exc
            if min(rx, ry) <= 0 or max(rx, ry) > 0.5:  # noqa: PLR2004 - pymupdf's largest radius.
                msg = f"bad radius value {radius}."
                raise PdfError(msg)
            px, py = rx * width, ry * height
        point = self.draw_line(Point(top_left.x, top_left.y + py), Point(bottom_left.x, bottom_left.y - py))
        point = self.draw_curve(point, bottom_left, Point(bottom_left.x + px, bottom_left.y))
        point = self.draw_line(point, Point(bottom_right.x - px, bottom_right.y))
        point = self.draw_curve(point, bottom_right, Point(bottom_right.x, bottom_right.y - py))
        point = self.draw_line(point, Point(top_right.x, top_right.y + py))
        point = self.draw_curve(point, top_right, Point(top_right.x - px, top_right.y))
        point = self.draw_line(point, Point(top_left.x + px, top_left.y))
        self.last_point = self.draw_curve(point, top_left, Point(top_left.x, top_left.y + py))
        self.updateRect(r)
        return self.last_point

    def draw_quad(self, quad: Sequence[Any]) -> Point:
        """Draw the outline of a quad, or of a rectangle, and return its upper-left corner."""
        ul, ur, ll, lr = _shape_quad(quad, "quad")
        return self.draw_polyline([ul, ll, lr, ur, ul])

    @staticmethod
    def _wave_points(p1: Point, p2: Point, breadth: float, height: float, *, on_line: bool) -> list[Point]:
        """Return the turning points of a wave from ``p1`` to ``p2``.

        Points alternate ``height`` breadths on either side of the line, with
        the points on the line between them when ``on_line`` is set.
        """
        if not breadth > 0:
            msg = f"breadth must be positive: {breadth!r}"
            raise PdfError(msg)
        rad = _distance(p2, p1)
        count = 4 * round(rad / (4 * breadth))
        if count < 4:  # noqa: PLR2004 - one full phase has four steps.
            msg = "points too close"
            raise PdfError(msg)
        step = rad / count
        ux, uy = (p2.x - p1.x) / rad, (p2.y - p1.y) / rad
        points = []
        for i in range(1, count):
            if i % 4 == 1:
                offset = -height
            elif i % 4 == 3:  # noqa: PLR2004 - the second turn of each phase.
                offset = height
            elif on_line:
                offset = 0.0
            else:
                continue
            u, v = i * step, offset * step
            points.append(Point(p1.x + u * ux - v * uy, p1.y + u * uy + v * ux))
        return points

    def draw_zigzag(
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        breadth: float = 2,
    ) -> Point:
        """Draw a zigzag line from ``p1`` to ``p2`` and return ``p2``."""
        start = _shape_point(p1, "p1")
        end = _shape_point(p2, "p2")
        turns = self._wave_points(start, end, _shape_number(breadth, "breadth"), 1.0, on_line=False)
        self.draw_polyline([start, *turns, end])
        return end

    def draw_squiggle(
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        breadth: float = 2,
    ) -> Point:
        """Draw a wavy line from ``p1`` to ``p2`` and return ``p2``."""
        start = _shape_point(p1, "p1")
        end = _shape_point(p2, "p2")
        turns = self._wave_points(start, end, _shape_number(breadth, "breadth"), _SQUIGGLE_HEIGHT, on_line=True)
        points = [start, *turns, end]
        for i in range(0, len(points) - 2, 2):
            self.draw_curve(points[i], points[i + 1], points[i + 2])
        return end

    def finish(  # noqa: PLR0913 - pymupdf's styling options.
        self,
        *,
        width: float = 1,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        dashes: str | None = None,
        even_odd: bool = False,
        closePath: bool = True,  # noqa: N803 - pymupdf's name.
        fill_opacity: float = 1,
        stroke_opacity: float = 1,
    ) -> None:
        """Stroke and fill everything drawn since the previous ``finish``.

        ``color`` strokes with ``width``, and ``fill`` fills with the
        non-zero rule, or the even-odd rule with ``even_odd``. ``closePath``
        joins the last point to the start of its subpath. ``dashes`` is a PDF
        dash pattern such as ``"[3 4] 0"``. Nothing drawn is a no-op, as in
        pymupdf; a style that would draw nothing, without ``fill`` and with
        ``color=None`` or ``width=0``, raises :class:`PdfError`.
        """
        if not self._operators:
            return
        line_width = _shape_number(width, "width")
        if line_width < 0:
            msg = f"width must not be negative: {width!r}"
            raise PdfError(msg)
        stroke_color = _shape_color(color, "color")
        fill_color = _shape_color(fill, "fill")
        stroke = None
        if stroke_color is not None and line_width > 0:
            stroke = (
                list(stroke_color),
                line_width,
                _shape_line_style(lineCap, "lineCap"),
                _shape_line_style(lineJoin, "lineJoin"),
                _shape_dashes(dashes),
                _shape_unit(stroke_opacity, "stroke_opacity"),
            )
        filled = None
        if fill_color is not None:
            filled = (list(fill_color), bool(even_odd), _shape_unit(fill_opacity, "fill_opacity"))
        if stroke is None and filled is None:
            msg = "finish() draws nothing: pass color with a positive width, or fill"
            raise PdfError(msg)
        self._record.add_path("".join(self._operators), self._coordinates, bool(closePath), filled, stroke)
        self._operators.clear()
        self._coordinates.clear()
        self.last_point = None

    def _font_for_text(  # noqa: PLR0913 - the text font sources.
        self,
        text: str,
        *,
        font: Font | None,
        fontname: str,
        fontfile: str | os.PathLike[str] | None,
        fontbuffer: bytes | None,
        fontindex: int,
        font_language: FontLanguage | None,
        max_font_size: int | None,
    ) -> _Font:
        """Resolve the font that :meth:`insert_text` and :meth:`insert_textbox` draw with."""
        if font is not None:
            core_font = _validate_font_choice(font, fontfile, fontbuffer, fontindex, font_language)
        else:
            font_data = _resolve_font_source(fontfile, fontbuffer, max_font_size)
            base_font, font_data, package_font = _resolve_generation_font(
                text,
                fontname,
                font_data,
                fontindex,
                font_language,
            )
            if isinstance(font_data, Path):
                core_font = _Font.from_file(os.fspath(font_data), fontindex, max_font_size, package_font)
            elif isinstance(font_data, bytes):
                core_font = _Font.from_bytes(font_data, fontindex, max_font_size)
            else:
                core_font = _Font.standard(cast("str", base_font))
            if package_font is not None:
                # As Page.insert_text does, an installed extra's face is drawn
                # at the regular weight.
                core_font = _regular_instance(core_font)
        if core_font.standard_name is not None:
            _require_winansi(text)
        return core_font

    def insert_text(  # noqa: PLR0913 - mirrors Page.insert_text.
        self,
        point: Sequence[float],
        text: str | Sequence[str],
        /,
        *,
        fontsize: float = 11,
        lineheight: float | None = None,
        font: Font | None = None,
        fontname: str = "helv",
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        font_language: FontLanguage | None = None,
        color: Sequence[float] | float | None = None,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> int:
        r"""Add text whose first baseline starts at ``point`` and return its line count.

        ``text`` is a string, split into lines with :meth:`str.splitlines`, or
        a list of lines used as given. Lines are split, placed, and left out
        below the page as :meth:`Page.insert_text` does, ``lineheight``
        included, and the returned count counts the lines added; the font
        options and limits are its own. ``color`` defaults to black. Text with
        nothing to draw adds nothing and returns 0.
        """
        _validate_optional_positive_int("max_font_size", max_font_size)
        lines = _insert_text_lines(text, max_text_size)
        if not lines or lines == [""]:
            return 0
        origin = _shape_point(point, "point")
        size = _shape_number(fontsize, "fontsize")
        if not size > 0:
            msg = f"fontsize must be a positive number: {fontsize!r}"
            raise PdfError(msg)
        resolved_lineheight = _validate_lineheight(lineheight)
        text_color = _shape_color(color, "color") or (0.0,)
        core_font = self._font_for_text(
            "\n".join(lines),
            font=font,
            fontname=fontname,
            fontfile=fontfile,
            fontbuffer=fontbuffer,
            fontindex=fontindex,
            font_language=font_language,
            max_font_size=max_font_size,
        )
        _, page_height = self.doc._doc.page_display_size(self.page._page_number())
        return self._record.insert_text(
            tuple(origin),
            lines,
            core_font,
            size,
            resolved_lineheight,
            list(text_color),
            page_height,
            max_text_size,
        )

    def insert_textbox(  # noqa: PLR0913 - mirrors Page.insert_textbox.
        self,
        rect: Sequence[float],
        text: str | Sequence[str],
        /,
        *,
        fontsize: float = 11,
        font: Font | None = None,
        fontname: str = "helv",
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        font_language: FontLanguage | None = None,
        color: Sequence[float] | float | None = None,
        align: int = TEXT_ALIGN_LEFT,
        expandtabs: int = 1,
        lineheight: float | None = None,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> float:
        """Wrap text inside a display rectangle and return the unused height.

        Layout follows :meth:`Page.insert_textbox`, with pymupdf's
        ``expandtabs=1``. A negative result means that the text does not
        fit, and then nothing is added. ``color`` defaults to black.
        """
        _validate_optional_positive_int("max_font_size", max_font_size)
        x0, y0, x1, y1 = _validate_rect(rect)
        resolved_fontsize, resolved_lineheight = _validate_textbox_options(fontsize, lineheight, align, expandtabs)
        joined = "\n".join(text) if isinstance(text, (list, tuple)) else text
        if not isinstance(joined, str):
            msg = f"text must be a string or a list of strings: {text!r}"
            raise TypeError(msg)
        text_color = _shape_color(color, "color") or (0.0,)
        _validate_utf8_text_input(joined, max_text_size)
        normalized = joined.replace("\r\n", "\n").replace("\r", "\n")
        _validate_generated_text_lines(normalized, max_text_size)
        _validate_utf8_text_input(normalized, max_text_size, expandtabs=expandtabs)
        normalized = normalized.expandtabs(expandtabs)
        if not normalized:
            return y1 - y0
        core_font = self._font_for_text(
            normalized,
            font=font,
            fontname=fontname,
            fontfile=fontfile,
            fontbuffer=fontbuffer,
            fontindex=fontindex,
            font_language=font_language,
            max_font_size=max_font_size,
        )
        return self._record.insert_textbox(
            (x0, y0, x1, y1),
            normalized,
            core_font,
            resolved_fontsize,
            resolved_lineheight,
            align,
            list(text_color),
            max_text_size,
        )

    def commit(self, overlay: bool = True) -> None:  # noqa: FBT001, FBT002 - pymupdf passes overlay positionally.
        """Draw the shape on the page, above existing content or, with ``overlay=False``, below it.

        The shape is then empty and can draw more. Drawing that
        :meth:`finish` has not styled raises :class:`PdfError`, and the page
        stays unchanged.
        """
        if self._operators:
            msg = "the shape has drawing that finish() has not styled; call finish() before commit()"
            raise PdfError(msg)
        if not self._record.is_empty():
            self.doc._doc.commit_shape(self.page._page_number(), self._record, bool(overlay))
        self._discard()


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
    def get_text(self, option: Literal["dict"]) -> TextPage: ...
    @overload
    def get_text(self, option: Literal["rawdict"]) -> RawTextPage: ...
    def get_text(self, option: str = "text") -> str | list[WordEntry] | list[BlockEntry] | TextPage | RawTextPage:
        """Extract page text or positioned layout data.

        ``option`` matches :meth:`Document.get_page_text`: ``"text"``,
        ``"words"``, ``"blocks"``, ``"dict"``, or ``"rawdict"``. The last
        exposes glyph Unicode, approximate bboxes, and baseline origins in
        the same display coordinates; see :class:`TextChar` for its limits.
        """
        self._page_number()
        return self._document.get_page_text(self._pno, option)  # ty: ignore[no-matching-overload]

    def get_text_ocr(  # noqa: PLR0913 - OCR resource controls are keyword-only.
        self,
        *,
        dpi: float = 300,
        engine: OcrEngine | None = None,
        tile_size: int = 1408,
        overlap: int = 192,
        min_confidence: float = 0.5,
        rotation: OcrRotation = 0,
        clip: Sequence[float] | None = None,
    ) -> list[OcrWord]:
        """Recognize rasterized page text without modifying the document.

        The default engine is loaded lazily from ``pylopdf[ocr]`` and reused.
        Pass an explicit :class:`OcrEngine` to control the model set or its
        lifetime. ``clip`` recognizes one display-coordinate region and
        returns boxes in full-page coordinates. ``rotation`` corrects sideways
        input clockwise without editing the page.
        """
        resolved_engine = _default_ocr_engine() if engine is None else engine
        if not isinstance(resolved_engine, OcrEngine):
            msg = f"engine must be an OcrEngine or None: {engine!r}"
            raise TypeError(msg)
        return resolved_engine.recognize(
            self,
            dpi=dpi,
            tile_size=tile_size,
            overlap=overlap,
            min_confidence=min_confidence,
            rotation=rotation,
            clip=clip,
        )

    def apply_ocr(  # noqa: PLR0913 - OCR resource controls are keyword-only.
        self,
        *,
        dpi: float = 300,
        engine: OcrEngine | None = None,
        tile_size: int = 1408,
        overlap: int = 192,
        min_confidence: float = 0.5,
        rotation: OcrRotation = 0,
        clip: Sequence[float] | None = None,
        skip_existing: bool = True,
    ) -> list[OcrWord]:
        """Recognize the page and insert an invisible searchable text layer.

        The recognized words are returned after insertion. An empty result is
        a no-op. By default, a page with extractable text is skipped, making
        repeated calls idempotent and avoiding duplicate text layers.
        ``clip`` can select a scanned region on a mixed-content page; only
        extractable text intersecting that region triggers the default skip.
        Use ``skip_existing=False`` to append despite such text. Existing text
        is preserved. ``rotation`` also orients the invisible text baseline
        after correcting sideways input.
        """
        if not isinstance(skip_existing, bool):
            msg = f"skip_existing must be a bool: {skip_existing!r}"
            raise TypeError(msg)
        resolved_rotation = _validate_ocr_rotation(rotation)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        if skip_existing:
            if clip_rect is None:
                if self.get_text().strip():
                    return []
            elif any(
                word[4].strip()
                and word[0] < clip_rect[2]
                and word[2] > clip_rect[0]
                and word[1] < clip_rect[3]
                and word[3] > clip_rect[1]
                for word in self.get_text("words")
            ):
                return []
        words = self.get_text_ocr(
            dpi=dpi,
            engine=engine,
            tile_size=tile_size,
            overlap=overlap,
            min_confidence=min_confidence,
            rotation=resolved_rotation,
            clip=clip_rect,
        )
        if words:
            self.insert_ocr_text_layer(
                [(*word["bbox"], word["text"]) for word in words],
                rotation=resolved_rotation,
            )
        return words

    def to_markdown(
        self,
        *,
        table_strategy: Literal["lines", "text"] | None = "lines",
        max_size: int | None = _DEFAULT_MAX_MARKDOWN_SIZE,
    ) -> str:
        """Convert this page to Markdown.

        This is the single-page form of :meth:`Document.to_markdown`; heading
        sizes are inferred from this page alone. Complete bordered tables are
        inserted by default; pass ``table_strategy="text"`` for conservative
        borderless candidates or ``None`` to disable table conversion.
        ``max_size`` caps UTF-8 output and defaults to 64 MiB; ``None``
        explicitly opts out.
        """
        self._page_number()
        return self._document.to_markdown(
            pages=[self._pno],
            table_strategy=table_strategy,
            max_size=max_size,
        )

    def search_for(
        self,
        needle: str,
        *,
        max_hits: int | None = _DEFAULT_MAX_SEARCH_HITS,
    ) -> list[Rect]:
        """Search page text case-insensitively.

        Return one :class:`Rect` per match. Search is line-based and does not
        detect matches spanning lines. The search term is limited to 4,096
        UTF-8 bytes. Results default to 4,096 matches; ``max_hits=None``
        explicitly opts trusted workloads out.
        """
        if not needle:
            msg = "needle must be at least 1 character"
            raise ValueError(msg)
        _validate_optional_positive_int("max_hits", max_hits)
        _validate_utf8_text_input(
            needle,
            _MAX_SEARCH_INPUT_BYTES,
            limit_code="search_input_size",
            input_label="search needle",
        )
        hits = self._document._doc.search_page(self._page_number(), needle, max_hits)
        self._document._emit_warnings()
        return [Rect(*hit) for hit in hits]

    def find_tables(
        self,
        strategy: Literal["lines", "text"] = "lines",
        *,
        clip: Sequence[float] | None = None,
    ) -> TableFinder:
        """Find high-confidence tables from vector borders or aligned text.

        The default ``"lines"`` strategy is deterministic and does not rasterize
        the page. It accepts stroked rules and thin filled rectangles, requires
        an outer grid with at least two rows and two columns, and reconstructs
        rectangular merged cells. Coarse grid spans are conservatively split
        when at least three evenly led text records occupy the same set of
        cross-axis cells.

        The opt-in ``"text"`` strategy detects borderless tables only when at
        least three consecutive rows have the same segment count, aligned
        column edges, compatible leading, and clear inter-column gaps. It may
        interpret aligned multicolumn prose as a table, so use it when the page
        region is known to contain tabular data.

        ``clip`` is an optional display-coordinate rectangle. Only tables whose
        complete bounding box lies inside it are returned; partial tables are
        not synthesized. Each result exposes ``confidence`` plus inspectable
        em-normalized alignment, gutter, and row-spacing evidence through
        :class:`TableDiagnostics`. Confidence ranks this deterministic
        detector's results and is not a statistical probability.
        """
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        raw = self._document._doc.find_tables(self._page_number(), strategy, clip_rect)
        self._document._emit_warnings()
        tables = []
        for bbox, row_count, col_count, cells, cell_anchors, diagnostic_values in raw:
            confidence, alignment_error, minimum_gutter, row_gap_variation = diagnostic_values
            diagnostics = TableDiagnostics(
                strategy,
                confidence,
                alignment_error,
                minimum_gutter,
                row_gap_variation,
            )
            tables.append(
                Table(
                    self,
                    Rect(*bbox),
                    row_count,
                    col_count,
                    [None if cell is None else (Rect(*cell[0]), cell[1]) for cell in cells],
                    cell_anchors,
                    diagnostics,
                )
            )
        return TableFinder(self, tables, strategy, None if clip_rect is None else Rect(*clip_rect))

    def get_images(self) -> list[ImageInfo]:
        """Extract images drawn on the page.

        Each item is a ``{"width", "height", "bbox", "ext", "image"}`` dict.
        An image filtered by DCTDecode alone, or by FlateDecode followed by
        DCTDecode, returns its JPEG payload with ``ext="jpeg"``. Other formats,
        including CCITT, JBIG2, and Flate, are decoded to PNG with
        ``ext="png"``. ``bbox`` is the drawn location as a top-left-origin
        :class:`Rect`. Extraction rejects rather than returns a partial result
        above 4,096 placements, 64,000,000 cumulative source pixels, or 64 MiB
        of encoded image payloads on one page.
        """
        raw = self._document._doc.extract_images(self._page_number())
        self._document._emit_warnings()
        return [
            {
                "width": width,
                "height": height,
                "bbox": Rect(*bbox),
                "ext": cast("Literal['jpeg', 'png']", ext),
                "image": data,
            }
            for width, height, bbox, ext, data in raw
        ]

    def get_drawings(self) -> list[DrawingInfo]:
        """Extract interpreted vector paint operations in display coordinates.

        Results use pymupdf-style path dictionaries with ``type`` equal to
        ``"f"``, ``"s"``, or ``"fs"``. ``items`` contains self-contained line
        and cubic Bézier commands. Quadratic curves are converted exactly to
        cubics. Stroke colors use ``color``; fill colors use ``fill``. Exotic
        pattern paints do not have one representative RGB value and return
        ``None`` for color and opacity.

        Clipping paths and clip-resolved visibility, transparency-group
        structure, optional-content layer names, text outlines, and images are
        not returned. The interpreter still applies optional-content visibility
        before reporting a paint operation. Output is bounded to 8,192 paths and
        131,072 commands; exceeding either limit raises :class:`PdfError` instead
        of returning partial results.
        """
        raw = self._document._doc.extract_drawings(self._page_number())
        self._document._emit_warnings()
        drawings: list[DrawingInfo] = []
        for (
            bbox,
            kind,
            raw_items,
            close_path,
            stroke,
            fill,
        ) in raw:
            stroke_color, stroke_opacity, width, line_cap, line_join, dashes = stroke
            fill_color, fill_opacity, even_odd = fill
            items: list[DrawingItem] = []
            for command, points in raw_items:
                converted = [Point(*point) for point in points]
                if command == "l" and len(converted) == _DRAWING_LINE_POINT_COUNT:
                    items.append(("l", converted[0], converted[1]))
                elif command == "c" and len(converted) == _DRAWING_CUBIC_POINT_COUNT:
                    items.append(
                        (
                            "c",
                            converted[0],
                            converted[1],
                            converted[2],
                            converted[3],
                        )
                    )
                else:  # pragma: no cover - guarded by the native extractor.
                    msg = f"invalid native drawing command: {command!r}"
                    raise PdfError(msg)
            drawings.append(
                {
                    "rect": Rect(*bbox),
                    "type": cast("Literal['f', 's', 'fs']", kind),
                    "items": items,
                    "closePath": close_path,
                    "color": stroke_color,
                    "fill": fill_color,
                    "stroke_opacity": stroke_opacity,
                    "fill_opacity": fill_opacity,
                    "even_odd": even_odd,
                    "width": width,
                    "lineCap": line_cap,
                    "lineJoin": line_join,
                    "dashes": dashes,
                }
            )
        return drawings

    def insert_image(  # noqa: PLR0913 - mirrors pymupdf's keyword-oriented drawing API
        self,
        rect: Sequence[float],
        *,
        filename: str | os.PathLike[str] | None = None,
        stream: bytes | None = None,
        pixmap: Pixmap | None = None,
        rotate: int = 0,
        keep_proportion: bool = True,
        overlay: bool = True,
        max_size: int | None = _DEFAULT_MAX_IMAGE_INPUT_SIZE,
        max_pixels: int | None = _DEFAULT_MAX_IMAGE_PIXELS,
    ) -> None:
        """Draw an image into ``rect`` in top-left-origin display coordinates.

        Specify exactly one source. JPEG is embedded unchanged through DCTDecode
        passthrough. PNG is decoded and embedded, preserving transparency as a
        soft mask. ``pixmap=`` embeds a rendered :class:`Pixmap` directly from
        its straight-alpha RGBA8 storage without a PNG encode/decode round trip.
        Convert other encoded formats to JPEG or PNG with Pillow or a similar
        library. ``rotate`` turns the image clockwise in multiples of 90 and is
        normalized to 0–359. ``rect`` uses the same coordinate space as
        :meth:`search_for` and :meth:`get_text`, so search results can be used
        directly. ``keep_proportion`` centers the rotated image while preserving
        its aspect ratio. ``overlay=False`` draws below existing content.
        Existing page content is never rewritten. Encoded ``filename`` and
        ``stream`` input defaults to a 64 MiB limit, and decoded PNG input
        defaults to 64,000,000 pixels. ``None`` explicitly opts out of either
        boundary for trusted workloads. Refusals raise :class:`LimitError` with
        code ``image_input_size`` or ``image_pixel_count``. The limits do not
        apply to an already bounded :class:`Pixmap`.

        Input identical to an earlier placement in this document, encoded
        bytes or Pixmap samples, places the image XObject and soft mask of that
        placement again, whatever the rectangle and rotation, instead of
        embedding another copy. :meth:`Document.compress_images`,
        :meth:`replace_text`, :meth:`Document.set_form_field`, and
        ``deflate=True`` saves end this sharing, so the next placement embeds
        the input again. A reopened document shares only its new placements.
        """
        _validate_optional_positive_int("max_size", max_size)
        _validate_optional_positive_int("max_pixels", max_pixels)
        image_rotation = _validate_image_rotation(rotate)
        source = _resolve_image_source(filename, stream, pixmap)
        if isinstance(source, bytes) and max_size is not None and len(source) > max_size:
            raise _image_input_limit_error(len(source), max_size)
        x0, y0, x1, y1 = _validate_rect(rect)
        if isinstance(source, Pixmap):
            self._document._doc.insert_pixmap(
                self._page_number(),
                (x0, y0, x1, y1),
                source,
                image_rotation,
                keep_proportion,
                overlay,
            )
        elif isinstance(source, Path):
            self._document._doc.insert_image_file(
                self._page_number(),
                (x0, y0, x1, y1),
                str(source),
                image_rotation,
                keep_proportion,
                overlay,
                max_size,
                max_pixels,
            )
        else:
            self._document._doc.insert_image(
                self._page_number(),
                (x0, y0, x1, y1),
                source,
                image_rotation,
                keep_proportion,
                overlay,
                max_size,
                max_pixels,
            )

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
        CropBox are resolved visually to fit ``rect``. ``src`` may be the same
        document; pylopdf imports from a pre-edit snapshot so the source page,
        including the target page itself, remains stable during placement.

        Placing a page of ``src`` again places the Form XObject of the earlier
        placement, with its fonts and images, instead of importing another
        copy, as long as ``src`` has not been edited since; any edit of ``src``
        makes the next placement import the page again. Edits that end
        image sharing, as described in :meth:`insert_image`, end this sharing
        too. A same-document source page stays shared across consecutive
        placements on other pages.
        """
        x0, y0, x1, y1 = _validate_rect(rect)
        src_number = src[pno]._page_number()
        if src is self._document:
            self._document._doc.show_pdf_page_self(
                self._page_number(),
                (x0, y0, x1, y1),
                src_number,
                keep_proportion,
                overlay,
            )
        else:
            self._document._doc.show_pdf_page(
                self._page_number(),
                (x0, y0, x1, y1),
                src._doc,
                src_number,
                keep_proportion,
                overlay,
            )

    def insert_text(  # noqa: PLR0913 - mirrors pymupdf's keyword-oriented drawing API
        self,
        point: Sequence[float],
        text: str | Sequence[str],
        *,
        fontsize: float = 11.0,
        lineheight: float | None = None,
        font: Font | None = None,
        fontname: str = "helv",
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        font_language: FontLanguage | None = None,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
        overlay: bool = True,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> int:
        r"""Draw text at ``point``, the first line's baseline-left display point, and return its line count.

        ``fontname`` is a pymupdf-style Standard 14 alias: the ``"helv"``,
        ``"tiro"``, and ``"cour"`` families with ``bo``/``it`` variants, plus
        ``"symb"`` and ``"zadb"``. These fonts are not embedded and text is
        limited to WinAnsi, roughly Latin-1.

        Pass a :class:`Font` as ``font`` to draw with a face loaded once, such as
        a bold face; :meth:`Font.text_length` measures what this call draws.
        ``font`` cannot be combined with ``fontfile``, ``fontbuffer``,
        ``fontindex``, or ``font_language``, and ``fontname`` is then ignored.

        Pass exactly one of ``fontfile`` or ``fontbuffer`` to subset-embed an
        arbitrary OpenType font through krilla. This enables Unicode text,
        including shaped CJK and RTL scripts. A variable font is drawn at its
        default instance; ``font=Font(fontfile=..., weight=...)`` draws another
        instance on its weight axis. Script extras ``pylopdf[ar]``,
        ``[he]``, ``[hi]``, and ``[th]`` plus locale extras ``[jp]``, ``[ko]``,
        ``[zh-cn]``, and ``[zh-tw]`` provide matching fonts.
        Kana, Hangul, and Bopomofo select an installed locale automatically;
        pure Han uses the JP-first automatic default.
        ``font_language`` selects a specific installed locale for ambiguous
        Han text.
        ``fontindex`` selects a face in a TrueType/OpenType collection and
        ``fontname`` is otherwise ignored for embedded fonts. A single line
        should use one script and the selected font must contain all needed
        glyphs; no per-glyph fallback or paragraph layout is performed. RTL
        glyph shaping renders correctly. Pure RTL lines and lines with one
        Latin or numeric token bracketed by RTL text extract in logical Unicode
        order. Edge tokens, multiple tokens, and general mixed-direction
        paragraph layout remain in producer visual order. Complex scripts can
        require PDF ``/ActualText`` when logical text does not map one-to-one to
        glyphs. Generated PDFs preserve it for compatible viewers, but the
        current pylopdf extractor does not consume it, so some reordered or
        context-dependent clusters can extract approximately after reopening.

        ``text`` is a string, which is split into lines as pymupdf splits it,
        with :meth:`str.splitlines`, or a list or tuple of lines used as given.
        A trailing line break therefore adds no line, and ``\v``, ``\f``,
        ``\x1c`` to ``\x1e``, ``\x85``, ``\u2028``, and ``\u2029`` break
        lines as ``\n``, ``\r``, and ``\r\n`` do. Each line starts
        ``lineheight`` times ``fontsize`` below the previous one. Without
        ``lineheight``, lines are pymupdf's default distance apart: the font's
        :attr:`Font.ascender` minus its :attr:`Font.descender`, times
        ``fontsize``, or 1.2 times ``fontsize`` when that difference is at
        most 1. As in pymupdf, the first line is always drawn, every later
        line only while its baseline does not fall below the bottom of the
        page, and the return value counts the lines drawn. The bottom is that
        of :attr:`rect`, the page in display coordinates. Text with nothing to
        draw, an empty string or list or one empty line such as ``"\n"``,
        inserts nothing and returns 0, as in pymupdf.

        Text remains visually upright on rotated pages. ``overlay=False`` draws
        below existing content. Loop over pages for headers, footers, page
        numbers, or Bates numbers. Explicit and automatically selected font
        input defaults to a 64 MiB boundary; ``max_font_size=None`` opts
        trusted workloads out. Refusal raises :class:`LimitError` with code
        ``font_input_size``. Text input defaults to a 1 MiB UTF-8 and 4,096
        line boundary, and the font checks every line, also those below the
        page. ``max_text_size=None`` is the explicit trusted-input opt-out for
        both limits. Refusals use ``text_input_size`` or ``text_line_count``.
        """
        _validate_optional_positive_int("max_font_size", max_font_size)
        lines = _insert_text_lines(text, max_text_size)
        if not lines or lines == [""]:
            return 0
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
        resolved_lineheight = _validate_lineheight(lineheight)
        red, green, blue = _validate_unit_rgb(color)
        joined = "\n".join(lines)
        if font is not None:
            core_font = _validate_font_choice(font, fontfile, fontbuffer, fontindex, font_language)
            standard_name = core_font.standard_name
            if standard_name is None:
                return self._document._doc.insert_font_text(
                    self._page_number(),
                    (x, y),
                    lines,
                    core_font,
                    float(fontsize),
                    resolved_lineheight,
                    (red, green, blue),
                    overlay,
                    max_text_size,
                )
            _require_winansi(joined)
            return self._insert_standard_text(
                (x, y),
                lines,
                base_font=standard_name,
                fontsize=float(fontsize),
                lineheight=resolved_lineheight,
                color=(red, green, blue),
                overlay=overlay,
                max_text_size=max_text_size,
            )
        font_data = _resolve_font_source(fontfile, fontbuffer, max_font_size)
        base_font, font_data, package_font = _resolve_generation_font(
            joined,
            fontname,
            font_data,
            fontindex,
            font_language,
        )
        if isinstance(font_data, Path):
            return self._document._doc.insert_embedded_text_file(
                self._page_number(),
                (x, y),
                lines,
                str(font_data),
                fontindex,
                float(fontsize),
                resolved_lineheight,
                (red, green, blue),
                overlay,
                max_font_size,
                max_text_size,
                package_font,
                _package_face_weight(package_font),
            )
        if isinstance(font_data, bytes):
            return self._document._doc.insert_embedded_text(
                self._page_number(),
                (x, y),
                lines,
                font_data,
                fontindex,
                float(fontsize),
                resolved_lineheight,
                (red, green, blue),
                overlay,
                max_font_size,
                max_text_size,
                _package_face_weight(package_font),
            )

        return self._insert_standard_text(
            (x, y),
            lines,
            base_font=cast("str", base_font),
            fontsize=float(fontsize),
            lineheight=resolved_lineheight,
            color=(red, green, blue),
            overlay=overlay,
            max_text_size=max_text_size,
        )

    def _insert_standard_text(  # noqa: PLR0913 - the validated insert_text arguments.
        self,
        point: tuple[float, float],
        lines: list[str],
        *,
        base_font: str,
        fontsize: float,
        lineheight: float | None,
        color: tuple[float, float, float],
        overlay: bool,
        max_text_size: int | None,
    ) -> int:
        """Draw validated WinAnsi lines in a Standard 14 font and return how many were drawn."""
        return self._document._doc.insert_page_text(
            self._page_number(),
            point,
            [line.encode("cp1252") for line in lines],
            base_font,
            base_font not in _SYMBOLIC_BASE14_NAMES,
            fontsize,
            lineheight,
            color,
            overlay,
            max_text_size,
        )

    def insert_textbox(  # noqa: PLR0913 - mirrors pymupdf's keyword-oriented drawing API
        self,
        rect: Sequence[float],
        text: str,
        *,
        fontsize: float = 11.0,
        font: Font | None = None,
        fontname: str = "helv",
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        font_language: FontLanguage | None = None,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
        align: int = TEXT_ALIGN_LEFT,
        expandtabs: int = 8,
        lineheight: float | None = None,
        overlay: bool = True,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> float:
        r"""Wrap and draw text inside a display-coordinate rectangle.

        The return value is the unused vertical space. A negative value means
        the laid-out text does not fit; in that case the document is not
        modified. Empty text returns the rectangle height without modifying the
        document.

        ``align`` accepts :data:`TEXT_ALIGN_LEFT`,
        :data:`TEXT_ALIGN_CENTER`, :data:`TEXT_ALIGN_RIGHT`, or
        :data:`TEXT_ALIGN_JUSTIFY`. Justification expands spaces on soft-wrapped
        lines, never the final line of a paragraph. Tabs are expanded before
        Unicode line breaking; explicit newlines always start a new line.

        Lines are placed as pymupdf places them. ``lineheight`` is a font-size
        multiplier that defaults to the font's :attr:`Font.ascender` minus its
        :attr:`Font.descender`, or to 1.2 when that difference is at most 1.
        The first baseline lies one ascender below the top of ``rect``, and
        later baselines follow ``lineheight`` times the ascender apart. The
        text fits when ``lineheight`` times ``fontsize`` per line plus one
        descender fits the height of ``rect``, which is the measure the
        returned spare height uses.

        Without a font source, Adobe Core 14 metrics provide exact WinAnsi
        wrapping. Pass a :class:`Font` as ``font``, or exactly one of
        ``fontfile`` or ``fontbuffer``, for HarfRust-shaped Unicode and a krilla
        subset-embedded OpenType font. Lines are measured with the advances
        that :meth:`Font.text_length` returns. ``font`` cannot be combined with
        the other font sources or ``fontindex``. ``fontfile`` and
        ``fontbuffer`` draw a variable font at its default instance, and a
        :class:`Font` loaded with ``weight`` draws another instance.
        Installed script or CJK locale font extras provide matching fonts.
        ``font_language`` selects a specific provider, including for ambiguous
        Han text.
        Unicode line-break opportunities support CJK without requiring spaces,
        and an overlong word falls back to grapheme-safe wrapping. Explicit and
        automatically selected font input defaults to a 64 MiB boundary;
        ``max_font_size=None`` opts trusted workloads out. Refusal raises
        :class:`LimitError` with code ``font_input_size``. Text after tab
        expansion defaults to a 1 MiB UTF-8 boundary. Physical input and final
        wrapped layout each stop at 4,096 lines. ``max_text_size=None``
        explicitly opts trusted input out of both limits; refusals use
        ``text_input_size`` or ``text_line_count``.
        """
        _validate_optional_positive_int("max_font_size", max_font_size)
        x0, y0, x1, y1 = _validate_rect(rect)
        page_number = self._page_number()
        resolved_fontsize, resolved_lineheight = _validate_textbox_options(fontsize, lineheight, align, expandtabs)
        red, green, blue = _validate_unit_rgb(color)
        _validate_utf8_text_input(text, max_text_size)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        _validate_generated_text_lines(normalized, max_text_size)
        _validate_utf8_text_input(normalized, max_text_size, expandtabs=expandtabs)
        normalized = normalized.expandtabs(expandtabs)
        core_font = (
            None if font is None else _validate_font_choice(font, fontfile, fontbuffer, fontindex, font_language)
        )
        if not normalized:
            return y1 - y0
        standard_name = None if core_font is None else core_font.standard_name
        if core_font is not None and standard_name is None:
            return self._document._doc.insert_font_textbox(
                page_number,
                (x0, y0, x1, y1),
                normalized,
                core_font,
                resolved_fontsize,
                resolved_lineheight,
                align,
                (red, green, blue),
                overlay,
                max_text_size,
            )
        if standard_name is not None:
            _require_winansi(normalized)
            return self._document._doc.insert_page_textbox(
                page_number,
                (x0, y0, x1, y1),
                normalized,
                standard_name,
                standard_name not in _SYMBOLIC_BASE14_NAMES,
                resolved_fontsize,
                resolved_lineheight,
                align,
                (red, green, blue),
                overlay,
                max_text_size,
            )

        font_data = _resolve_font_source(fontfile, fontbuffer, max_font_size)
        base_font, font_data, package_font = _resolve_generation_font(
            normalized,
            fontname,
            font_data,
            fontindex,
            font_language,
        )
        if isinstance(font_data, Path):
            return self._document._doc.insert_embedded_textbox_file(
                page_number,
                (x0, y0, x1, y1),
                normalized,
                str(font_data),
                fontindex,
                resolved_fontsize,
                resolved_lineheight,
                align,
                (red, green, blue),
                overlay,
                max_font_size,
                max_text_size,
                package_font,
                _package_face_weight(package_font),
            )
        if isinstance(font_data, bytes):
            return self._document._doc.insert_embedded_textbox(
                page_number,
                (x0, y0, x1, y1),
                normalized,
                font_data,
                fontindex,
                resolved_fontsize,
                resolved_lineheight,
                align,
                (red, green, blue),
                overlay,
                max_font_size,
                max_text_size,
                _package_face_weight(package_font),
            )

        base_font = cast("str", base_font)
        return self._document._doc.insert_page_textbox(
            page_number,
            (x0, y0, x1, y1),
            normalized,
            base_font,
            fontname not in _SYMBOLIC_FONTS,
            resolved_fontsize,
            resolved_lineheight,
            align,
            (red, green, blue),
            overlay,
            max_text_size,
        )

    def new_shape(self) -> Shape:
        """Return an empty :class:`Shape` that draws on this page."""
        return Shape(self)

    def _draw_shape(  # noqa: PLR0913 - Shape.finish's options plus overlay.
        self,
        draw: Callable[[Shape], Point],
        *,
        color: Sequence[float] | float | None,
        fill: Sequence[float] | float | None,
        dashes: str | None,
        width: float,
        lineCap: int,  # noqa: N803 - pymupdf's name.
        lineJoin: int,  # noqa: N803 - pymupdf's name.
        closePath: bool,  # noqa: N803 - pymupdf's name.
        overlay: bool,
        stroke_opacity: float,
        fill_opacity: float,
    ) -> Point:
        """Draw one path with a :class:`Shape`, finish it, commit it, and return its end point."""
        shape = Shape(self)
        try:
            point = draw(shape)
            shape.finish(
                width=width,
                color=color,
                fill=fill,
                lineCap=lineCap,
                lineJoin=lineJoin,
                dashes=dashes,
                closePath=closePath,
                fill_opacity=fill_opacity,
                stroke_opacity=stroke_opacity,
            )
            shape.commit(overlay)
        except BaseException:
            shape._discard()
            raise
        return point

    def draw_line(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        *,
        color: Sequence[float] | float | None = (0,),
        dashes: str | None = None,
        width: float = 1,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw a line and return ``p2``; see :meth:`Shape.draw_line` and :meth:`Shape.finish`."""
        return self._draw_shape(
            lambda shape: shape.draw_line(p1, p2),
            color=color,
            fill=None,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=False,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_polyline(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        points: Iterable[Sequence[float]],
        *,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        dashes: str | None = None,
        width: float = 1,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        closePath: bool = False,  # noqa: N803 - pymupdf's name.
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw connected segments, a polygon with ``closePath``, and return the last point."""
        return self._draw_shape(
            lambda shape: shape.draw_polyline(points),
            color=color,
            fill=fill,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=closePath,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_bezier(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        p3: Sequence[float],
        p4: Sequence[float],
        *,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        dashes: str | None = None,
        width: float = 1,
        closePath: bool = False,  # noqa: N803 - pymupdf's name.
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw a cubic Bézier curve and return ``p4``; see :meth:`Shape.draw_bezier`."""
        return self._draw_shape(
            lambda shape: shape.draw_bezier(p1, p2, p3, p4),
            color=color,
            fill=fill,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=closePath,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_curve(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        p3: Sequence[float],
        *,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        dashes: str | None = None,
        width: float = 1,
        closePath: bool = False,  # noqa: N803 - pymupdf's name.
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw a curve that ``p2`` pulls and return ``p3``; see :meth:`Shape.draw_curve`."""
        return self._draw_shape(
            lambda shape: shape.draw_curve(p1, p2, p3),
            color=color,
            fill=fill,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=closePath,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_rect(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        rect: Sequence[float],
        *,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        dashes: str | None = None,
        width: float = 1,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
        radius: float | Sequence[float] | None = None,
    ) -> Point:
        """Draw a rectangle, with rounded corners for ``radius``, and return its top-left corner."""
        return self._draw_shape(
            lambda shape: shape.draw_rect(rect, radius=radius),
            color=color,
            fill=fill,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=True,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_quad(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        quad: Sequence[Any],
        *,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        dashes: str | None = None,
        width: float = 1,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw a quad, given as four points or a rectangle, and return its upper-left corner."""
        return self._draw_shape(
            lambda shape: shape.draw_quad(quad),
            color=color,
            fill=fill,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=True,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_circle(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        center: Sequence[float],
        radius: float,
        *,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        dashes: str | None = None,
        width: float = 1,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw a circle and return its leftmost point; see :meth:`Shape.draw_circle`."""
        return self._draw_shape(
            lambda shape: shape.draw_circle(center, radius),
            color=color,
            fill=fill,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=True,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_oval(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        rect: Sequence[Any],
        *,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        dashes: str | None = None,
        width: float = 1,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw the ellipse inside a rectangle or quad and return its left midpoint."""
        return self._draw_shape(
            lambda shape: shape.draw_oval(rect),
            color=color,
            fill=fill,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=True,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_sector(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        center: Sequence[float],
        point: Sequence[float],
        beta: float,
        *,
        color: Sequence[float] | float | None = (0,),
        fill: Sequence[float] | float | None = None,
        dashes: str | None = None,
        fullSector: bool = True,  # noqa: N803 - pymupdf's name.
        width: float = 1,
        closePath: bool = False,  # noqa: N803 - pymupdf's name.
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw a circle sector of ``beta`` degrees and return the arc's end; see :meth:`Shape.draw_sector`."""
        return self._draw_shape(
            lambda shape: shape.draw_sector(center, point, beta, fullSector=fullSector),
            color=color,
            fill=fill,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=closePath,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_squiggle(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        breadth: float = 2,
        *,
        color: Sequence[float] | float | None = (0,),
        dashes: str | None = None,
        width: float = 1,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw a wavy line and return ``p2``; see :meth:`Shape.draw_squiggle`."""
        return self._draw_shape(
            lambda shape: shape.draw_squiggle(p1, p2, breadth=breadth),
            color=color,
            fill=None,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=False,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def draw_zigzag(  # noqa: PLR0913 - pymupdf's drawing options.
        self,
        p1: Sequence[float],
        p2: Sequence[float],
        breadth: float = 2,
        *,
        color: Sequence[float] | float | None = (0,),
        dashes: str | None = None,
        width: float = 1,
        lineCap: int = 0,  # noqa: N803 - pymupdf's name.
        lineJoin: int = 0,  # noqa: N803 - pymupdf's name.
        overlay: bool = True,
        stroke_opacity: float = 1,
        fill_opacity: float = 1,
    ) -> Point:
        """Draw a zigzag line and return ``p2``; see :meth:`Shape.draw_zigzag`."""
        return self._draw_shape(
            lambda shape: shape.draw_zigzag(p1, p2, breadth=breadth),
            color=color,
            fill=None,
            dashes=dashes,
            width=width,
            lineCap=lineCap,
            lineJoin=lineJoin,
            closePath=False,
            overlay=overlay,
            stroke_opacity=stroke_opacity,
            fill_opacity=fill_opacity,
        )

    def insert_ocr_text_layer(
        self,
        words: Iterable[Sequence[Any]],
        *,
        rotation: OcrRotation = 0,
    ) -> None:
        """Insert OCR output as an invisible, searchable text layer.

        Each item in ``words`` begins with ``(x0, y0, x1, y1, text, ...)``;
        only the first five values are used. This accepts :meth:`get_text`
        ``"words"`` output and common OCR API results directly. Coordinates use
        top-left-origin display space. Text is not drawn and appears only in
        extraction and search. An Identity-H reference font with ToUnicode is
        used without embedding font data, so any language, including CJK, adds
        almost no file size. The primitive is engine-neutral and accepts cloud
        APIs, Tesseract, or any equivalent source. ``rotation`` describes the
        clockwise correction used to read sideways input and orients the
        invisible baseline while retaining the supplied display-coordinate
        boxes. One call accepts at most 4,096 non-empty words and 1 MiB of
        aggregate UTF-8 text.
        """
        payload: list[tuple[float, float, float, float, str]] = []
        text_bytes = 0
        for entry in words:
            x0, y0, x1, y1 = _validate_rect(entry[:4])
            text = str(entry[4])
            if text:
                if len(payload) >= _MAX_OCR_LAYER_WORDS:
                    msg = f"words cannot contain more than {_MAX_OCR_LAYER_WORDS} non-empty entries"
                    raise ValueError(msg)
                remaining = _MAX_OCR_LAYER_TEXT_BYTES - text_bytes
                encoded_bytes = _bounded_utf8_size(text, remaining)
                if encoded_bytes is None:
                    msg = f"word text cannot exceed {_MAX_OCR_LAYER_TEXT_BYTES} UTF-8 bytes per call"
                    raise ValueError(msg)
                text_bytes += encoded_bytes
                payload.append((x0, y0, x1, y1, text))
        if not payload:
            msg = "words must contain at least one word with text"
            raise ValueError(msg)
        resolved_rotation = _validate_ocr_rotation(rotation)
        self._document._doc.insert_ocr_layer(self._page_number(), payload, resolved_rotation)

    def replace_text(
        self,
        search: str,
        replacement: str,
        *,
        default_char: str | None = None,
        max_size: int | None = _DEFAULT_MAX_TEXT_REPLACEMENT_SIZE,
    ) -> int:
        """Replace text on the page and return the number of replacements.

        This follows lopdf's constrained simple-font replacement model. It
        works only with simply encoded fonts such as WinAnsi, not CID/CJK fonts.
        Characters absent from the font become ``default_char`` (``"?"`` by
        default). Widths are not recalculated, so differing text lengths may
        shift layout. ``max_size`` bounds decoded page/font data and the
        re-encoded content stream; ``None`` opts out for trusted input.

        Search, replacement, and fallback text are limited to 4,096 aggregate
        UTF-8 bytes. Resource refusals raise :class:`LimitError` with code
        ``replacement_input_size`` or ``replacement_output_size``. A no-match
        result and every refusal leave the document and its caches unchanged.
        """
        if not isinstance(search, str):
            msg = f"search must be a string: {type(search).__name__}"
            raise TypeError(msg)
        if not isinstance(replacement, str):
            msg = f"replacement must be a string: {type(replacement).__name__}"
            raise TypeError(msg)
        if default_char is not None and not isinstance(default_char, str):
            msg = f"default_char must be a string or None: {type(default_char).__name__}"
            raise TypeError(msg)
        if not search:
            msg = "search must be at least 1 character"
            raise ValueError(msg)
        if default_char is not None and len(default_char) != 1:
            msg = "default_char must contain exactly one character"
            raise ValueError(msg)
        _validate_optional_positive_int("max_size", max_size)
        _validate_cumulative_utf8_input(
            (search, replacement, "" if default_char is None else default_char),
            _MAX_TEXT_REPLACEMENT_INPUT_BYTES,
            limit_code="replacement_input_size",
            input_label="text replacement input",
        )
        return self._document._doc.replace_text_on_page(
            self._page_number(),
            search,
            replacement,
            default_char,
            max_size,
        )

    def get_label(self) -> str:
        """Return the display label, such as ``"iv"`` or ``"A-2"``, or empty."""
        pno = self._page_number() - 1
        applicable: PageLabelInfo | None = None
        for label in self._document.get_page_labels():
            if label["startpage"] <= pno:
                applicable = label
            else:
                break
        if applicable is None:
            return ""
        number = pno - applicable["startpage"] + applicable["firstpagenum"]
        return _format_page_label(applicable["style"], applicable["prefix"], number)

    def annots(self) -> list[AnnotationInfo]:
        """Read annotations on the page.

        Each item is a ``{"type", "rect", "contents", "uri"}`` dict.
        ``type`` is the PDF Subtype name, such as ``"Highlight"`` or
        ``"Link"``; ``rect`` is a display-coordinate :class:`Rect`;
        ``contents`` is annotation text; and ``uri`` is the URI action target or
        ``None``. Reads reject partial output above 4,096 annotations or 1 MiB
        of aggregate encoded or returned metadata text per call.
        """
        raw = self._document._doc.read_annotations(self._page_number())
        return [
            {"type": subtype, "rect": Rect(*rect), "contents": contents, "uri": uri}
            for subtype, rect, contents, uri in raw
        ]

    def get_links(self) -> list[LinkInfo]:
        """Read link annotations and resolve their destinations.

        Each item is a pymupdf-style dict with ``kind`` (for example
        :data:`LINK_GOTO`) and ``from`` (a display-coordinate :class:`Rect`).
        Additional keys depend on ``kind``:

        - ``LINK_URI``: ``uri``.
        - ``LINK_GOTO``: zero-based ``page`` or -1 when unresolved, plus
          optional ``to`` (:class:`Point`), ``zoom``, and ``nameddest``.
          Integer page indexes, which some producers write instead of page
          references, resolve only when they name an existing page.
        - ``LINK_GOTOR`` / ``LINK_LAUNCH``: ``file`` and optional ``nameddest``.
        - ``LINK_NAMED``: action ``name``, such as ``NextPage``.

        GoTo named destinations resolve from both the ``/Names`` name tree and
        the legacy ``/Dests`` dictionary. Reads share the 4,096-annotation and
        1 MiB aggregate metadata-text boundaries with :meth:`annots`. The first
        named lookup indexes the name tree once, cycle-aware, and later reads
        reuse that index and the page index until the next edit, so reading
        every page stays linear. Trees above 65,536 entries or nodes or 131,072
        edges raise :class:`LimitError` with code ``named_destination_count``,
        above 16 MiB of key text with ``named_destination_text_size``, and above
        32 levels with :class:`PdfError`, instead of returning a silent
        unresolved result. A refused tree is never cached, so every later named
        lookup raises again.
        """
        raw = self._document._doc.read_links(self._page_number())
        kind_map = {
            "uri": LINK_URI,
            "goto": LINK_GOTO,
            "gotor": LINK_GOTOR,
            "launch": LINK_LAUNCH,
            "named": LINK_NAMED,
        }
        links: list[LinkInfo] = []
        for kind, rect, uri, page, to, zoom, file, name in raw:
            link: LinkInfo = {
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
        other viewers. Multiple rectangles form one annotation, with at most
        4,096 rectangles; its subtype and popup ``content`` share a 1 MiB text
        budget. Oversized text is rejected before rectangle iteration or the
        Rust boundary with ``LimitError.code == "annotation_input_size"``.
        """
        if content is not None and not isinstance(content, str):
            msg = f"content must be a string or None: {type(content).__name__}"
            raise TypeError(msg)
        _validate_cumulative_utf8_input(
            ("Highlight", "" if content is None else content),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and content input",
        )

        iterator = iter(rects)
        try:
            first = next(iterator)
        except StopIteration:
            msg = "rects must contain at least one rect"
            raise ValueError(msg) from None

        if isinstance(first, (int, float)):
            coordinates = [first]
            for coordinate in iterator:
                if len(coordinates) >= _RECT_COORDINATE_COUNT:
                    msg = f"rect must be 4 numbers (x0, y0, x1, y1): {rects!r}"
                    raise ValueError(msg)
                coordinates.append(cast("float", coordinate))
            validated = [_validate_rect(coordinates)]
        else:
            validated = [_validate_rect(cast("Sequence[float]", first))]
            for rect in iterator:
                if len(validated) >= _MAX_HIGHLIGHT_RECTS:
                    msg = f"rects cannot contain more than {_MAX_HIGHLIGHT_RECTS} rectangles"
                    raise ValueError(msg)
                validated.append(_validate_rect(cast("Sequence[float]", rect)))
        rgb = _validate_unit_rgb(color)
        if not (math.isfinite(opacity) and 0.0 < opacity <= 1.0):
            msg = f"opacity must be greater than 0 and at most 1: {opacity!r}"
            raise ValueError(msg)
        self._document._doc.add_highlight_annotation(self._page_number(), validated, rgb, float(opacity), content)

    def add_link_annot(self, rect: Sequence[float], uri: str) -> None:
        """Add a borderless URI link annotation over a display rectangle.

        This supports search-then-link workflows using :meth:`search_for`.
        For new documents, links are usually better created in typst; see the
        README ecosystem recipe. Its subtype and URI share a 1 MiB text budget.
        Oversized text is rejected before the Rust boundary with
        ``LimitError.code == "annotation_input_size"``.
        """
        if not isinstance(uri, str):
            msg = f"uri must be a string: {type(uri).__name__}"
            raise TypeError(msg)
        if not uri:
            msg = "uri must be at least 1 character"
            raise ValueError(msg)
        _validate_cumulative_utf8_input(
            ("Link", uri),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and URI input",
        )
        x0, y0, x1, y1 = _validate_rect(rect)
        self._document._doc.add_link_annotation(self._page_number(), (x0, y0, x1, y1), uri)

    def get_pixmap(
        self,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        clip: Sequence[float] | None = None,
    ) -> Pixmap:
        """Render the page to a straight-alpha RGBA8 :class:`Pixmap`.

        Arguments match :meth:`Document.render_page`. The pixmap exposes
        ``width``, ``height``, ``stride``, ``n``, ``samples`` as bytes, and
        ``tobytes()`` as PNG. Convert it to NumPy with
        ``np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 4)``.

        ``clip`` is an optional display-coordinate rectangle. It is intersected
        with the page and rounded outward to pixel boundaries. A clip that does
        not intersect the page raises :class:`PdfError`. Only the clipped
        region is rasterized, but full-page render-size limits still apply.
        """
        if dpi is not None:
            if scale != 1.0:
                msg = "scale and dpi cannot both be specified"
                raise ValueError(msg)
            scale = dpi / 72.0
        rgba = _normalize_background(background)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        page_number = self._page_number()
        document = self._document
        document._ensure_fallback_fonts()
        result = document._doc.render_page_pixmap(page_number, scale, rgba, clip_rect)
        document._emit_warnings()
        return result

    def render(
        self,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        max_size: int | None = _DEFAULT_MAX_RENDER_PAGE_SIZE,
    ) -> bytes:
        """Render the page to bounded PNG with :meth:`Document.render_page` arguments."""
        self._page_number()
        return self._document.render_page(
            self._pno,
            scale,
            dpi=dpi,
            background=background,
            max_size=max_size,
        )

    def render_svg(
        self,
        *,
        max_size: int | None = _DEFAULT_MAX_SVG_SIZE,
    ) -> str:
        """Render the page to a bounded SVG string.

        ``max_size`` caps UTF-8 output and defaults to 64 MiB; ``None``
        explicitly opts out.
        """
        self._page_number()
        return self._document.render_page_svg(self._pno, max_size=max_size)

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
        *,
        limits: DocumentLimits | None = None,
    ) -> None:
        """Open from exactly one of a file path and byte stream, or create empty.

        PDFs with an empty user password decrypt automatically. Otherwise pass
        ``password`` or call :meth:`authenticate` after opening.
        Password input is capped at 127 UTF-8 bytes.
        ``limits`` applies structural, decompression, interpretation-snapshot,
        and interpreted-text budgets before expensive work.
        :meth:`DocumentLimits.web` is a conservative starting profile for
        untrusted uploads.
        ``max_decompressed_size`` remains as the compatible single-budget
        shorthand and cannot be combined with ``limits``.
        """
        if filename is not None and stream is not None:
            msg = "filename and stream cannot both be specified"
            raise ValueError(msg)
        _validate_password_input(password)
        if limits is not None and not isinstance(limits, DocumentLimits):
            msg = f"limits must be a DocumentLimits instance or None: {limits!r}"
            raise TypeError(msg)
        if limits is not None and max_decompressed_size is not None:
            msg = "limits and max_decompressed_size cannot both be specified"
            raise ValueError(msg)
        resolved_limits = limits or DocumentLimits(max_decompressed_size=max_decompressed_size)
        limit_args = _document_limit_args(resolved_limits)
        path = None if filename is None else os.fspath(filename)
        self._limits = resolved_limits
        if stream is not None:
            doc = _Document.load_bytes(stream, None, *limit_args)
            needs_pass = doc.is_encrypted()
            if needs_pass and password is not None:
                doc = _Document.load_bytes(stream, password, *limit_args)
        elif path is not None:
            doc = _Document.load(path, None, *limit_args)
            needs_pass = doc.is_encrypted()
            if needs_pass and password is not None:
                doc = _Document.load(path, password, *limit_args)
        else:
            doc = _Document(
                resolved_limits.max_text_size,
                resolved_limits.max_interpretation_size,
                resolved_limits.max_text_glyphs,
            )
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
        self._emit_warnings()

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

    @property
    def is_repaired(self) -> bool:
        """Return whether opening recovered damaged cross-reference data."""
        self._ensure_not_closed()
        return self._doc.is_repaired()

    def authenticate(self, password: str) -> int:
        """Authenticate and decrypt with a password.

        Return pymupdf-compatible codes: 0 for failure, 1 when authentication is
        unnecessary, 2 for a matching user password, 4 for a matching owner
        password, and 6 when both match. Password input is capped at 127 UTF-8
        bytes before the encryption KDF.
        """
        self._ensure_not_closed()
        if not self._doc.is_encrypted():
            return 1
        _validate_password_input(password)
        code = 0
        if self._doc.authenticate_user_password(password):
            code |= 2
        if self._doc.authenticate_owner_password(password):
            code |= 4
        if code == 0:
            return 0
        # Reopen with the password so objects inside object streams are readable.
        limit_args = _document_limit_args(self._limits)
        if self._source_path is not None:
            self._doc = _Document.load(self._source_path, password, *limit_args)
        elif self._source_bytes is not None:
            self._doc = _Document.load_bytes(self._source_bytes, password, *limit_args)
        self._source_path = None
        self._source_bytes = None
        self._emit_warnings()
        return code

    @property
    def page_count(self) -> int:
        """Return the number of pages."""
        self._ensure_open()
        return self._doc.page_count()

    @property
    def limits(self) -> DocumentLimits:
        """Return the immutable resource policy configured at open time."""
        self._ensure_not_closed()
        return self._limits

    @property
    def complexity(self) -> DocumentComplexity:
        """Return cheap structural facts without decoding streams or rendering."""
        self._ensure_not_closed()
        pages, objects, streams, encoded_bytes, depth = self._doc.complexity()
        return {
            "page_count": pages,
            "object_count": objects,
            "stream_count": streams,
            "encoded_stream_bytes": encoded_bytes,
            "max_object_depth": depth,
        }

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
    def metadata(self) -> DocumentMetadata:
        """Return title, author, subject, keywords, dates, producer, and format.

        Only the eight standard Info fields are decoded. Reads reject more than
        1 MiB of aggregate encoded or returned text.
        """
        self._ensure_open()
        raw = self._doc.get_metadata()
        return {
            "title": raw.get("Title", ""),
            "author": raw.get("Author", ""),
            "subject": raw.get("Subject", ""),
            "keywords": raw.get("Keywords", ""),
            "creator": raw.get("Creator", ""),
            "producer": raw.get("Producer", ""),
            "creationDate": raw.get("CreationDate", ""),
            "modDate": raw.get("ModDate", ""),
            "format": f"PDF {self._doc.version()}",
        }

    def set_metadata(self, metadata: MetadataUpdate) -> None:
        """Set metadata, deleting entries whose values are empty strings.

        Keys match :attr:`metadata`, except the read-only ``format`` key.
        The standard fields share 1 MiB input and encoded-text limits. All
        updates are validated before PyO3 copying and applied atomically.
        Caller refusals use ``LimitError.code == "metadata_input_size"``.
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
        values = [value for _, value in updates]
        _validate_cumulative_utf8_input(
            values,
            _MAX_METADATA_INPUT_TEXT_SIZE,
            limit_code="metadata_input_size",
            input_label="Info metadata input",
        )
        _validate_cumulative_pdf_text_input(
            (value for value in values if value),
            _MAX_METADATA_INPUT_TEXT_SIZE,
            limit_code="metadata_input_size",
            input_label="encoded Info metadata input",
        )
        self._doc.set_metadata_batch(updates)

    def to_markdown(
        self,
        pages: Iterable[int] | None = None,
        *,
        table_strategy: Literal["lines", "text"] | None = "lines",
        max_size: int | None = _DEFAULT_MAX_MARKDOWN_SIZE,
    ) -> str:
        """Convert the document to Markdown for RAG or LLM preprocessing.

        Headings are inferred from font sizes: the size containing the most text
        is body text, and larger sizes map in descending order to
        ``#`` through ``####``. Wrapped CJK lines join without spaces. Leading
        bullets and ``1.``/``1)`` forms normalize to Markdown lists. Scanned PDFs
        work after adding a layer with :meth:`Page.insert_ocr_text_layer`.
        Multicolumn text follows deterministic whitespace gutters.
        Conservatively detected vertical CJK columns follow top-to-bottom,
        right-to-left reading order; ruby and mixed-orientation typography are
        not interpreted. Complete bordered grids become Markdown tables by
        default. Pass ``table_strategy="text"`` to extend this with conservative
        borderless detection, or ``None`` to disable table conversion.
        ``pages`` is a sequence of zero-based page numbers emitted in the given
        order; ``None`` means every page. An explicit sequence may contain up to
        the larger of the page count and 4,096 entries; further entries raise
        :class:`LimitError` with code ``page_entry_count``. ``max_size`` caps
        aggregate UTF-8 Markdown output and defaults to 64 MiB; ``None``
        explicitly opts out.
        """
        self._ensure_open()
        if table_strategy not in ("lines", "text", None):
            msg = f"table_strategy must be 'lines', 'text', or None: {table_strategy!r}"
            raise ValueError(msg)
        _validate_optional_positive_int("max_size", max_size)
        count = self.page_count
        page_numbers: Sequence[int] = (
            range(1, count + 1) if pages is None else self._page_entries(pages, "pages", count)
        )

        # Both passes reuse the validated snapshot instead of re-walking the
        # page tree for every entry, keeping whole-document conversion linear.
        size_counts: Counter[float] = Counter()
        for page_number in page_numbers:
            layout = self._page_layout(page_number, "dict")
            page = Page(self, page_number - 1)
            tables = [] if table_strategy is None else _markdown_page_tables(page, table_strategy)
            bboxes = [(table.bbox.x0, table.bbox.y0, table.bbox.x1, table.bbox.y1) for table in tables]
            size_counts.update(_markdown.collect_sizes([layout], [bboxes]))
        levels = _markdown.heading_levels(size_counts)

        rendered: list[str] = []
        output_bytes = 0
        for page_number in page_numbers:
            layout = self._page_layout(page_number, "dict")
            page = Page(self, page_number - 1)
            tables = [] if table_strategy is None else _markdown_page_tables(page, table_strategy)
            separator_bytes = 2 if rendered else 0
            page_budget = None if max_size is None else max_size - output_bytes - separator_bytes
            bboxes, markdown_tables = _markdown_table_output(
                layout,
                tables,
                max_size=max_size,
                remaining_size=page_budget,
            )
            words = self._page_layout(page_number, "words") if tables else None
            page_markdown = _bounded_markdown_page_output(
                layout,
                levels,
                markdown_tables,
                words,
                _MarkdownBudget(max_size, page_budget),
            )
            if not page_markdown:
                continue
            if max_size is not None:
                remaining = max_size - output_bytes - separator_bytes
                if len(page_markdown) > remaining:
                    raise _markdown_output_limit_error(max_size)
                page_bytes = _markdown.utf8_size(page_markdown)
                if page_bytes > remaining:
                    raise _markdown_output_limit_error(max_size)
            else:
                page_bytes = 0
            output_bytes += separator_bytes + page_bytes
            rendered.append(page_markdown)
        return "\n\n".join(rendered)

    def get_form_fields(self) -> list[FormFieldInfo]:
        """Return AcroForm fields.

        Each item is ``{"name", "type", "value"}``. ``name`` is the fully
        qualified dotted name; ``type`` is text, checkbox, radio, button,
        combobox, listbox, or signature; and ``value`` is the current value.
        Button values are appearance state names such as ``"Yes"`` or ``"Off"``.
        Reads reject partial output above 4,096 entries/nodes, 8,192 edges,
        64 levels, 1 MiB of field names or values, or 4,096 choice-value items.
        """
        self._ensure_open()
        return [
            {"name": name, "type": cast("FormFieldType", kind), "value": value}
            for name, kind, value in self._doc.get_form_fields()
        ]

    def set_form_field(  # noqa: C901, PLR0912, PLR0913 - Keep validation and font bounds adjacent.
        self,
        name: str,
        value: str | bool,  # noqa: FBT001 - Match pymupdf's bool API.
        *,
        font: Font | None = None,
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        font_language: FontLanguage | None = None,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
    ) -> None:
        """Set an AcroForm field value and regenerate its widget appearance.

        Pass strings for text/choice fields. For checkboxes and radio buttons,
        pass an appearance state such as ``"Yes"``/``"Off"`` or a bool. ``True``
        resolves the on state from widget appearances; ``False`` becomes
        ``"Off"``. Text, choice, checkbox, and radio appearances render in
        pylopdf as well as external viewers. Existing non-empty button
        appearances are preserved; missing ones use native vector marks.
        Missing appearances on other WinAnsi fields are completed in the same
        operation, and ``NeedAppearances`` is cleared once every fillable widget
        has a usable normal appearance. Comb text fields honor inherited
        ``MaxLen`` and alignment, center Unicode graphemes in their positions,
        and reject overlength values without changing the document. Field-tree
        and 1 MiB name/value input limits are checked without leaving a partial
        update; caller input refusals use ``form_field_input_size``. Text and
        choice appearances stop at 4,096 layout lines. Button handling
        additionally caps widgets, appearance states, and state names before
        resolving booleans or generating missing appearances.

        WinAnsi text uses Helvetica. Pass exactly one of ``fontfile`` or
        ``fontbuffer`` to subset-embed an arbitrary OpenType font, a variable
        font at its default instance. Pass a :class:`Font` with an OpenType
        face as ``font`` to draw with a face loaded once, such as the bold
        instance that ``Font(fontfile=..., weight=700)`` selects on a variable
        font's weight axis; text is fitted, aligned, and placed in comb
        positions with the advances that :meth:`Font.text_length` returns.
        ``font`` cannot be combined with ``fontfile``, ``fontbuffer``,
        ``fontindex``, or ``font_language``, and a Standard 14 :class:`Font`
        raises :class:`PdfError`. Locale font extras can supply Unicode
        appearances automatically; use ``font_language`` for ambiguous Han
        text. Otherwise the value is still
        stored with ``NeedAppearances`` for viewer compatibility, but its
        appearance cannot be rendered by pylopdf.
        Explicit and automatically selected font input defaults to a 64 MiB
        boundary; ``max_font_size=None`` opts trusted workloads out. Refusal
        raises :class:`LimitError` with code ``font_input_size``.
        Rich text, pushbuttons, and signature fields are unsupported; use the
        pyHanko integration for signatures.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_font_size", max_font_size)
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        if not name:
            msg = "name must be at least 1 character"
            raise ValueError(msg)
        _validate_utf8_text_input(
            name,
            _MAX_FORM_FIELD_INPUT_SIZE,
            limit_code="form_field_input_size",
            input_label="form field name",
        )
        if not isinstance(value, (str, bool)):
            msg = f"value must be a string or bool: {value!r}"
            raise TypeError(msg)
        if isinstance(value, str):
            _validate_utf8_text_input(
                value,
                _MAX_FORM_FIELD_INPUT_SIZE,
                limit_code="form_field_input_size",
                input_label="form field value",
            )
        core_font = _validate_form_font(font, fontfile, fontbuffer, fontindex, font_language)
        normalized_font_language = _normalize_font_language(font_language)
        font_data = _resolve_font_source(fontfile, fontbuffer, max_font_size)
        package_font: tuple[FontLanguage, str] | None = None
        if font_data is not None:
            if normalized_font_language is not None:
                msg = "font_language cannot be combined with fontfile or fontbuffer"
                raise ValueError(msg)
            if isinstance(fontindex, bool) or not isinstance(fontindex, int) or not 0 <= fontindex <= _UINT32_MAX:
                msg = f"fontindex must be an integer from 0 through 4294967295: {fontindex!r}"
                raise ValueError(msg)
        elif fontindex != 0:
            msg = "fontindex requires fontfile or fontbuffer"
            raise ValueError(msg)
        if isinstance(value, bool):
            if value:
                states = self._doc.form_button_states(name)
                on_states = [s for s in states if s != "Off"]
                resolved = on_states[0] if on_states else "Yes"
            else:
                resolved = "Off"
        else:
            resolved = value
            if font_data is None and core_font is None:
                bundled_font = _form_generation_font(resolved, normalized_font_language)
                if bundled_font is not None:
                    font_data, package_font = bundled_font
        if core_font is not None:
            self._doc.set_form_field_font(name, resolved, core_font)
            return
        weight = _package_face_weight(package_font)
        if isinstance(font_data, Path):
            self._doc.set_form_field_file(
                name, resolved, str(font_data), fontindex, max_font_size, package_font, weight
            )
        else:
            self._doc.set_form_field(name, resolved, font_data, fontindex, max_font_size, weight)

    def get_page_labels(self) -> list[PageLabelInfo]:
        """Read page-label definitions.

        Each item has ``startpage``, ``style``, ``prefix``, and
        ``firstpagenum``. ``startpage`` is zero-based; ``style`` is
        ``"D"``, ``"R"``, ``"r"``, ``"A"``, ``"a"``, or empty for prefix only.
        Use :meth:`Page.get_label` for a page's rendered label. Number-tree reads
        reject partial output above 4,096 entries/nodes, 32 levels, or 1 MiB of
        encoded or decoded style/prefix text.
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

    def set_page_labels(self, labels: Sequence[PageLabelSpec]) -> None:
        """Set page labels in :meth:`get_page_labels` format; empty removes all.

        The PDF specification requires the first range to start at page 0.
        ``firstpagenum`` defaults to 1 for each range.
        Aggregate style and prefix input plus encoded PDF text stop at 1 MiB
        before PyO3 copying with ``page_label_input_size``.
        """
        self._ensure_open()
        if len(labels) > _MAX_PAGE_LABEL_RANGES:
            msg = f"labels cannot contain more than {_MAX_PAGE_LABEL_RANGES} ranges"
            raise ValueError(msg)
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
        _validate_cumulative_utf8_input(
            (
                text
                for _, style, prefix, _ in payload
                for text in ("" if style is None else style, "" if prefix is None else prefix)
            ),
            _MAX_PAGE_LABEL_INPUT_TEXT_SIZE,
            limit_code="page_label_input_size",
            input_label="page-label text input",
        )
        _validate_cumulative_pdf_text_input(
            (
                text
                for _, style, prefix, _ in payload
                for text in ("" if style is None else style, "" if prefix is None else prefix)
            ),
            _MAX_PAGE_LABEL_INPUT_TEXT_SIZE,
            limit_code="page_label_input_size",
            input_label="encoded page-label text input",
        )
        self._doc.set_page_labels(payload)

    def embfile_add(
        self,
        name: str,
        data: bytes,
        *,
        filename: str | None = None,
        desc: str | None = None,
        max_size: int | None = _DEFAULT_MAX_EMBEDDED_FILE_SIZE,
    ) -> None:
        """Add an EmbeddedFiles attachment.

        ``name`` is the unique key used for listing and retrieval. ``filename``
        is the viewer-facing file name and defaults to ``name``; ``desc`` is a
        description. Both support Unicode through UTF-16BE ``UF``/``Desc``.
        ``max_size`` bounds attachment data and defaults to 64 MiB, matching
        :meth:`embfile_get`; ``None`` explicitly accepts unbounded input.
        Their aggregate input text is capped at 1 MiB before data copying;
        refusals use ``embedded_file_input_size``. Existing inline FileSpecs are
        cloned only after bounded shape validation.
        This can build invoice-plus-XML structures such as ZUGFeRD/Factur-X.
        """
        self._ensure_open()
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        if not name:
            msg = "name must be at least 1 character"
            raise ValueError(msg)
        if filename is not None and not isinstance(filename, str):
            msg = f"filename must be a string or None: {type(filename).__name__}"
            raise TypeError(msg)
        if desc is not None and not isinstance(desc, str):
            msg = f"desc must be a string or None: {type(desc).__name__}"
            raise TypeError(msg)
        input_text = [name, name if filename is None else filename]
        if desc is not None:
            input_text.append(desc)
        _validate_cumulative_utf8_input(
            input_text,
            _MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE,
            limit_code="embedded_file_input_size",
            input_label="attachment name, filename, and description",
        )
        _validate_optional_positive_int("max_size", max_size)
        if max_size is not None and len(data) > max_size:
            limit_code = "embedded_file_size"
            msg = f"attachment input is {len(data)} bytes, exceeding the {max_size}-byte limit"
            raise LimitError(limit_code, msg)
        self._doc.embfile_add(name, bytes(data), filename, desc, max_size)

    def embfile_names(self) -> list[str]:
        """Return sorted attachment names."""
        self._ensure_open()
        return self._doc.embfile_names()

    def embfile_get(
        self,
        name: str,
        *,
        max_size: int | None = _DEFAULT_MAX_EMBEDDED_FILE_SIZE,
    ) -> bytes:
        """Return attachment contents as bytes.

        ``max_size`` bounds every decoding layer and defaults to 64 MiB. Pass a
        larger positive integer for a known large attachment, or ``None`` only
        when intentionally accepting unbounded materialization. Oversized
        content raises :class:`LimitError` with code ``embedded_file_size``.
        The lookup name stops at 1 MiB of UTF-8 before tree traversal, using
        code ``embedded_file_input_size``.
        """
        self._ensure_open()
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        _validate_utf8_text_input(
            name,
            _MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE,
            limit_code="embedded_file_input_size",
            input_label="attachment lookup name",
        )
        _validate_optional_positive_int("max_size", max_size)
        return self._doc.embfile_get(name, max_size)

    def embfile_del(self, name: str) -> None:
        """Delete an attachment, bounding its name before tree traversal."""
        self._ensure_open()
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        _validate_utf8_text_input(
            name,
            _MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE,
            limit_code="embedded_file_input_size",
            input_label="attachment lookup name",
        )
        self._doc.embfile_del(name)

    def get_pdfa_claim(
        self,
        *,
        max_size: int | None = _DEFAULT_MAX_XMP_METADATA_SIZE,
    ) -> tuple[int, str] | None:
        """Read the XMP PDF/A claim from ``pdfaid:part`` and conformance.

        Return ``(part, conformance)``, for example ``(2, "B")`` for a
        PDF/A-2b claim. PDF/A-4 without conformance uses an empty string. Return
        ``None`` when absent. This reads a self-declaration; it does not validate
        compliance. Use veraPDF or another external validator. ``max_size``
        bounds every XMP decoding layer and defaults to 1 MiB. Pass a larger
        positive integer for known large metadata or ``None`` only when
        intentionally accepting unbounded materialization.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_size", max_size)
        claim = self._doc.pdfa_claim(max_size)
        return None if claim is None else (int(claim[0]), claim[1])

    def get_text(self, pages: Iterable[int] | None = None) -> str:
        """Extract plain text from the given pages in one batched call.

        ``pages`` is a sequence of zero-based page numbers extracted in the
        given order; ``None`` means every page, negative values count from the
        end, and duplicates are allowed. An explicit sequence may contain up to
        the larger of the page count and 4,096 entries; further entries raise
        :class:`LimitError` with code ``page_entry_count``. Pages are processed
        in chunks of 4,096 entries, each sharing one interpreter font cache, so
        a font reused across pages is parsed once per chunk rather than once
        per page; the returned text equals joining single-page
        :meth:`get_page_text` output in the same order.
        """
        count = self.page_count
        page_numbers = list(range(1, count + 1)) if pages is None else self._page_entries(pages, "pages", count)
        text = self._doc.extract_text(page_numbers)
        self._emit_warnings()
        return text

    @overload
    def get_page_text(self, pno: int, option: Literal["text"] = "text") -> str: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["blocks"]) -> list[BlockEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["dict"]) -> TextPage: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["rawdict"]) -> RawTextPage: ...
    def get_page_text(
        self, pno: int, option: str = "text"
    ) -> str | list[WordEntry] | list[BlockEntry] | TextPage | RawTextPage:
        """Extract text or positioned layout from zero-based page ``pno``.

        ``option`` uses pymupdf-style names without promising full compatibility:

        - ``"text"``: plain text, the default.
        - ``"words"``: ``(x0, y0, x1, y1, word, block, line, word index)``.
        - ``"blocks"``: ``(x0, y0, x1, y1, text, block, 0)``.
        - ``"dict"``: nested width, height, and blocks with lines and spans.
        - ``"rawdict"``: the same text hierarchy with ``chars`` replacing span
          ``text``; each item has ``c``, ``bbox``, and baseline ``origin``.

        Coordinates are display points (1/72 inch at the default PDF UserUnit),
        with a top-left origin,
        downward y, and page rotation/CropBox offsets resolved. Bboxes use
        glyph advances and approximate baseline extents from font-size ratios,
        not exact outlines. Reading order follows the existing geometry and
        script heuristics rather than semantic document order.

        Raw entries preserve one glyph's complete Unicode mapping, including
        ligatures with several characters; no positions are invented by
        dividing span text. Inferred spaces have no raw entry. Unmapped glyphs
        retain the existing skip/warning behavior. Output is text-only and
        shares the existing text/glyph budgets and interpretation cache.
        """
        if option == "text":
            text = self._doc.extract_text([self._lopdf_page_number(pno)])
            self._emit_warnings()
            return text
        if option == "rawdict":
            return self._get_page_rawdict(pno)
        return self._page_layout(self._lopdf_page_number(pno), option)

    @overload
    def _page_layout(self, page_number: int, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def _page_layout(self, page_number: int, option: Literal["dict"]) -> TextPage: ...
    @overload
    def _page_layout(self, page_number: int, option: str) -> list[WordEntry] | list[BlockEntry] | TextPage: ...
    def _page_layout(self, page_number: int, option: str) -> list[WordEntry] | list[BlockEntry] | TextPage:
        """Build words, blocks, or dict layout for a validated one-based page."""
        width, height, blocks = self._doc.extract_layout(page_number)
        # Attribute warnings to the public caller rather than this helper.
        self._emit_warnings(stacklevel=4)
        if option == "words":
            words: list[WordEntry] = []
            for bno, (_, lines) in enumerate(blocks):
                for lno, (_, _, line_words, _, _) in enumerate(lines):
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
                    "\n".join(" ".join(text for _, text in line_words) for _, _, line_words, _, _ in lines),
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
                                "wmode": writing_mode,
                                "dir": direction,
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
                            for line_bbox, spans, _, direction, writing_mode in lines
                        ],
                    }
                    for bno, (bbox, lines) in enumerate(blocks)
                ],
            }
        msg = f"option must be one of 'text' / 'words' / 'blocks' / 'dict' / 'rawdict': {option!r}"
        raise ValueError(msg)

    def _get_page_rawdict(self, pno: int) -> RawTextPage:
        """Materialize glyph mappings only for an explicitly requested raw layout."""
        width, height, blocks = self._doc.extract_raw_layout(self._lopdf_page_number(pno))
        self._emit_warnings()
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
                            "wmode": writing_mode,
                            "dir": direction,
                            "spans": [
                                {
                                    "bbox": span_bbox,
                                    "origin": origin,
                                    "size": size,
                                    "font": font,
                                    "flags": flags,
                                    "chars": [
                                        {"c": text, "bbox": char_bbox, "origin": char_origin}
                                        for text, char_bbox, char_origin in chars
                                    ],
                                }
                                for span_bbox, chars, size, origin, font, flags in spans
                            ],
                        }
                        for line_bbox, spans, direction, writing_mode in lines
                    ],
                }
                for bno, (bbox, lines) in enumerate(blocks)
            ],
        }

    def delete_page(self, pno: int) -> None:
        """Delete zero-based page ``pno``; negative values count from the end."""
        page_number = self._lopdf_page_number(pno)
        self._doc.delete_pages([page_number])
        self._bump_generation()

    def delete_pages(self, page_numbers: Iterable[int]) -> None:
        """Delete zero-based pages; negative values count from the end.

        The iterable may contain up to the larger of the page count and 4,096
        entries; further entries raise :class:`LimitError` with code
        ``page_entry_count`` before the document changes. Links, outline
        destinations, the open action, and named destinations that target
        deleted pages are removed under the same limits as :meth:`get_toc` and
        :meth:`Page.get_links`. The AcroForm loses the widgets of deleted pages
        and the fields whose widgets all lay there, under the
        :meth:`get_form_fields` limits; fields that never had a widget stay.
        A tagged PDF's structure tree loses the marked content and annotation
        references of deleted pages, the elements left without kids, and
        their ``/ParentTree`` and ``/IDTree`` entries; a structure tree beyond
        1,048,576 nodes, 4,194,304 edges, or 256 levels raises
        :class:`LimitError` with code ``structure_tree_count``. A refusal
        leaves the document unchanged.
        """
        numbers = self._page_entries(page_numbers, "page_numbers", self.page_count)
        if not numbers:
            return
        self._doc.delete_pages(numbers)
        self._bump_generation()

    def select(self, page_numbers: Iterable[int]) -> None:
        """Keep only the given zero-based pages in the given order.

        This also reorders pages. Repeating an index duplicates that page; the
        duplicate shares Contents and Resources objects with the original. The
        iterable may contain up to the larger of the page count and 4,096
        entries, so a selection never grows a large document; further entries
        raise :class:`LimitError` with code ``page_entry_count``. Navigation to
        excluded pages, their AcroForm widgets and fields, and their
        structure-tree content are cleaned as in :meth:`delete_pages`.
        """
        numbers = self._page_entries(page_numbers, "page_numbers", self.page_count)
        self._doc.select(numbers)
        self._bump_generation()

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
        The range is bounded by the source document itself, so a complete
        document of any page count can be inserted. Source-local links,
        including named destinations, become explicit destinations or are
        omitted when their target page is not imported; source name trees share
        the :meth:`Page.get_links` limits.

        AcroForm fields with a widget on an imported page join this document's
        form with their values and appearances, so :meth:`get_form_fields` and
        :meth:`set_form_field` reach them. A top-level field name that this
        document already uses gains the smallest unused ``+N`` suffix, so
        importing one form twice yields ``name`` and ``name+1``; names are
        compared within the 1 MiB name budget of :meth:`get_form_fields`, and
        JavaScript that names a renamed field is not rewritten. Source default
        resources join ``/DR``: an identical resource is shared, and a different
        one under a name already in use gains the smallest unused ``_N`` suffix
        that imported default appearance strings follow. A partial range omits
        widgets of the other pages and the fields left without a widget, and
        raises :class:`PdfError` without changing this document when the source
        field tree exceeds the :meth:`get_form_fields` limits. A complete import
        keeps every field without walking the tree. The source ``/XFA`` and
        ``/SigFlags`` are not copied.
        """
        self._ensure_open()
        if other is self:
            msg = "cannot insert a document into itself"
            raise ValueError(msg)
        other._ensure_open()
        source_count = other.page_count
        if source_count == 0:
            return
        start = _resolve_page_index(from_page, source_count)
        stop = _resolve_page_index(to_page, source_count)
        step = 1 if start <= stop else -1
        position = None if start_at == -1 else self._insert_position(start_at, "start_at")
        self._doc.merge_pages(other._doc, list(range(start + 1, stop + 1 + step, step)), position)
        self._bump_generation()

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
        self._doc.new_page(position, width, height)
        self._bump_generation()
        return self[index]

    def copy_page(self, pno: int, to: int = -1) -> None:
        """Copy page ``pno`` to insertion position ``to``; -1 appends.

        The copied page shares Contents and Resources objects with the original.
        """
        self._ensure_open()
        page_number = self._lopdf_page_number(pno)
        position = None if to == -1 else self._insert_position(to, "to")
        self._doc.copy_page(page_number, position)
        self._bump_generation()

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
        other page APIs. Return an empty list when no TOC exists. Traversal is
        cycle-aware and rejects more than 65,536 nodes or entries or 131,072
        edges with ``LimitError.code == "toc_entry_count"``, more than 16 MiB of
        source/returned text with ``"toc_text_size"``, and more than 64 levels
        or 32 destination indirections. Named destinations resolve under the
        same name-tree limits as :meth:`Page.get_links`.
        """
        self._ensure_open()
        return [[level, title, page] for level, title, page in self._doc.get_toc()]

    def set_toc(self, toc: Sequence[Sequence[int | str]]) -> None:
        """Replace bookmarks from ``[[level, title, page number], ...]``.

        An empty sequence removes them. At most 65,536 entries, 64 levels, and
        16 MiB each of input and encoded title text are accepted, enough for
        large manuals while matching :meth:`get_toc`. Levels start at 1 and can
        increase by at most one from the previous entry. Page numbers are
        one-based, matching :meth:`get_toc`. Validation is atomic. Refusals occur
        before PyO3 copying with ``LimitError.code == "toc_entry_count"`` for
        entries or ``"toc_input_size"`` for title text.
        """
        self._ensure_open()
        if len(toc) > _MAX_TOC_ENTRIES:
            limit_code = "toc_entry_count"
            msg = f"toc cannot contain more than {_MAX_TOC_ENTRIES} entries"
            raise LimitError(limit_code, msg)
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
        titles = [title for _, title, _ in entries]
        _validate_cumulative_utf8_input(
            titles,
            _MAX_TOC_INPUT_TEXT_SIZE,
            limit_code="toc_input_size",
            input_label="TOC title input",
        )
        _validate_cumulative_pdf_text_input(
            titles,
            _MAX_TOC_INPUT_TEXT_SIZE,
            limit_code="toc_input_size",
            input_label="encoded TOC title input",
        )
        self._doc.set_toc(entries)

    def set_fallback_font(
        self,
        font: bytes | str | os.PathLike[str] | None,
        kind: str = "sans",
        index: int = 0,
        *,
        font_language: CjkFontLanguage = "ja",
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
    ) -> None:
        """Set a fallback font for rendering non-embedded CJK fonts.

        ``font`` is a TTF/OTF/TTC path or bytes. ``None`` clears the setting and
        disables automatic locale-font discovery. ``kind`` is ``"sans"``
        (default) or ``"serif"``; ``index`` selects a TTC face.
        ``font_language`` selects the Adobe-Japan1, Korea1, GB1, or CNS1 slot.
        Font input defaults to a 64 MiB boundary; ``max_font_size=None`` opts
        trusted workloads out. Refusal raises :class:`LimitError` with code
        ``font_input_size``.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_font_size", max_font_size)
        normalized_language = _normalize_cjk_font_language(font_language)
        if font is None:
            self._doc.clear_fallback_fonts()
            self._fallback_configured = True
            return
        if isinstance(font, bytes):
            if max_font_size is not None and len(font) > max_font_size:
                raise _font_input_limit_error(len(font), max_font_size)
            self._doc.set_fallback_font(kind, font, index, max_font_size, normalized_language)
        else:
            self._doc.set_fallback_font_file(kind, str(Path(font)), index, max_font_size, normalized_language)
        self._fallback_configured = True

    def _ensure_fallback_fonts(self) -> None:
        """Auto-configure installed locale fonts unless explicitly configured."""
        if self._fallback_configured:
            return
        providers = tuple(
            provider for provider in _bundled_font_providers() if provider.language in {"ja", "ko", "zh-CN", "zh-TW"}
        )
        if all(isinstance(provider.sans, Path) and isinstance(provider.serif, Path) for provider in providers):
            self._doc.set_fallback_font_locale_files(
                [(provider.language, str(provider.sans), str(provider.serif)) for provider in providers],
                _DEFAULT_MAX_FONT_INPUT_SIZE,
            )
            self._fallback_configured = True
            return
        for provider in providers:
            for data in (provider.sans, provider.serif):
                if isinstance(data, bytes) and len(data) > _DEFAULT_MAX_FONT_INPUT_SIZE:
                    raise _font_input_limit_error(len(data), _DEFAULT_MAX_FONT_INPUT_SIZE)
        for provider in providers:
            for kind, data in (("sans", provider.sans), ("serif", provider.serif)):
                if isinstance(data, Path):
                    self._doc.set_fallback_font_file(
                        kind,
                        str(data),
                        0,
                        _DEFAULT_MAX_FONT_INPUT_SIZE,
                        provider.language,
                    )
                else:
                    self._doc.set_fallback_font(
                        kind,
                        data,
                        0,
                        _DEFAULT_MAX_FONT_INPUT_SIZE,
                        provider.language,
                    )
        self._fallback_configured = True

    def render_page(
        self,
        pno: int,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        max_size: int | None = _DEFAULT_MAX_RENDER_PAGE_SIZE,
    ) -> bytes:
        """Render zero-based page ``pno`` to PNG.

        ``scale`` is a positive finite factor where 1.0 equals 72 dpi. ``dpi``
        may be used instead (144 equals scale 2.0) but not together with a
        nondefault scale. ``background`` is an RGB or RGBA tuple with components
        from 0 to 255; the default is transparent. Output is limited to 65,535
        pixels per side and 64,000,000 total pixels. ``max_size`` defaults to
        64 MiB of encoded PNG output; pass ``None`` only for trusted workloads.
        """
        _validate_optional_positive_int("max_size", max_size)
        if dpi is not None:
            if scale != 1.0:
                msg = "scale and dpi cannot both be specified"
                raise ValueError(msg)
            scale = dpi / 72.0
        rgba = _normalize_background(background)
        page_number = self._lopdf_page_number(pno)
        self._ensure_fallback_fonts()
        result = self._doc.render_page_png(page_number, scale, rgba, max_size)
        self._emit_warnings()
        return result

    def render_pages(  # noqa: PLR0913 - batch resource controls are keyword-only.
        self,
        pages: Iterable[int] | None = None,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        workers: int | None = None,
        max_size: int | None = _DEFAULT_MAX_RENDER_BATCH_SIZE,
    ) -> list[bytes]:
        """Render pages to PNG concurrently while preserving input order.

        ``pages`` contains zero-based page numbers and defaults to every page.
        Duplicates are allowed. ``workers=None`` uses up to four CPUs; explicit
        values must be between 1 and 64. Actual concurrency is also capped to
        roughly 512 MB of estimated live raster and conversion buffers. A
        dedicated bounded worker pool renders one immutable snapshot of the
        document while the GIL is released, replacing each worker's render
        cache after 4,096 pages. An explicit sequence may contain up to the
        larger of the page count and 4,096 entries; further entries raise
        :class:`LimitError` with code ``page_entry_count``. ``max_size`` caps
        their cumulative encoded PNG bytes and defaults to 512 MiB; pass
        ``None`` only to accept unbounded output.

        Do not edit or call other methods on this same :class:`Document` from
        another thread during the operation. Such calls are not part of the
        concurrency contract; use this method as the supported same-document
        parallel rendering boundary.
        """
        self._ensure_open()
        if dpi is not None:
            if scale != 1.0:
                msg = "scale and dpi cannot both be specified"
                raise ValueError(msg)
            scale = dpi / 72.0
        if workers is None:
            workers = min(4, os.cpu_count() or 1)
        elif isinstance(workers, bool) or not isinstance(workers, int):
            msg = f"workers must be an integer between 1 and {_MAX_RENDER_WORKERS}"
            raise TypeError(msg)
        if not 1 <= workers <= _MAX_RENDER_WORKERS:
            msg = f"workers must be between 1 and {_MAX_RENDER_WORKERS}"
            raise ValueError(msg)
        _validate_optional_positive_int("max_size", max_size)
        count = self.page_count
        page_numbers = list(range(1, count + 1)) if pages is None else self._page_entries(pages, "pages", count)
        rgba = _normalize_background(background)
        if not page_numbers:
            return []
        self._ensure_fallback_fonts()
        result = self._doc.render_pages_png(page_numbers, scale, rgba, workers, max_size)
        self._emit_warnings()
        return result

    def render_page_svg(
        self,
        pno: int,
        *,
        max_size: int | None = _DEFAULT_MAX_SVG_SIZE,
    ) -> str:
        """Render zero-based page ``pno`` to a bounded SVG string.

        ``max_size`` caps UTF-8 output and defaults to 64 MiB; ``None``
        explicitly opts out. hayro-svg currently materializes its internal
        Rust string before this boundary is enforced, but over-limit output is
        rejected before conversion to a Python string.
        """
        page_number = self._lopdf_page_number(pno)
        _validate_optional_positive_int("max_size", max_size)
        self._ensure_fallback_fonts()
        result = self._doc.render_page_svg(page_number, max_size)
        self._emit_warnings()
        return result

    def _emit_warnings(self, stacklevel: int = 3) -> None:
        """Emit recoverable interpretation warnings as ``PylopdfWarning``."""
        for message in self._doc.take_warnings():
            _warnings.warn(message, PylopdfWarning, stacklevel=stacklevel)

    def compress_images(
        self,
        *,
        dpi: float | None = 150,
        quality: int = 75,
    ) -> ImageCompressionResult:
        """Downsample and JPEG-recompress safe raster images in place.

        ``dpi`` limits the effective resolution of each source-image axis.
        Reused images retain the pixels required by their largest placement
        across the document. Pass ``None`` to disable downsampling
        while retaining JPEG quality recompression. ``quality`` is 1 through
        100; both operations are lossy.

        The conservative implementation rewrites only indirect, single-filter,
        8-bit DeviceGray/DeviceRGB DCT or Flate XObjects without masks or custom
        decode arrays. DCT decode parameters are excluded; Flate may use no
        predictor or a consistent PNG predictor. Unsupported interpreted
        indirect images and outputs that would not be smaller are counted as
        ``skipped``; inline images are not considered. The operation releases
        the GIL, is atomic on decoding errors, rejects more than 65,536
        interpreted indirect image placements, 16,384 unique image objects, or
        250 million eligible source pixels, and skips an individual source
        above 64 million pixels.

        Byte totals compare rewritten source payloads with their resulting JPEG
        payloads, not complete PDF serialization. Save to a new output and
        inspect it before replacing an original document.
        """
        self._ensure_open()
        if (
            isinstance(quality, bool)
            or not isinstance(quality, int)
            or not _IMAGE_COMPRESSION_MIN_QUALITY <= quality <= _IMAGE_COMPRESSION_MAX_QUALITY
        ):
            msg = "quality must be an integer from 1 through 100"
            raise PdfError(msg)
        if dpi is not None:
            if isinstance(dpi, bool) or not isinstance(dpi, (int, float)):
                msg = "dpi must be a finite number from 36 through 2400, or None"
                raise PdfError(msg)
            dpi = float(dpi)
            if not math.isfinite(dpi) or not _IMAGE_COMPRESSION_MIN_DPI <= dpi <= _IMAGE_COMPRESSION_MAX_DPI:
                msg = "dpi must be a finite number from 36 through 2400, or None"
                raise PdfError(msg)
        considered, rewritten, skipped, bytes_before, bytes_after = self._doc.compress_images(dpi, quality)
        self._emit_warnings()
        return {
            "considered": considered,
            "rewritten": rewritten,
            "skipped": skipped,
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
            "bytes_saved": bytes_before - bytes_after,
        }

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
        unrestricted opening with permission controls. Each password is capped
        at 127 UTF-8 bytes. ``permissions`` combines :class:`Permissions` and
        defaults to all. Encryption cannot be combined with object streams.
        Every mode streams to a same-directory temporary file and replaces
        ``filename`` only after a complete successful write.
        """
        self._ensure_open()
        encryption = self._encryption_args(user_pw, owner_pw, permissions, object_streams=object_streams)
        self._apply_save_options(garbage=garbage, deflate=deflate)
        writer: Callable[[str], None]
        if encryption is not None:
            user, owner, perms = encryption
            writer = functools.partial(
                self._doc.save_encrypted,
                user_password=user,
                owner_password=owner,
                permissions=perms,
                file_encryption_key=os.urandom(32),
            )
        elif object_streams:
            writer = self._doc.save_with_object_streams
        else:
            writer = self._doc.save
        _atomic_save_file(filename, writer)

    def tobytes(  # noqa: PLR0913  # Save options are keyword-only, like pymupdf.
        self,
        *,
        garbage: bool = False,
        deflate: bool = False,
        object_streams: bool = False,
        user_pw: str | None = None,
        owner_pw: str | None = None,
        permissions: int = Permissions.ALL,
        max_size: int | None = _DEFAULT_MAX_PDF_OUTPUT_SIZE,
    ) -> bytes:
        """Return bounded PDF bytes; save options have the same meaning as :meth:`save`.

        ``max_size`` limits serialization before converting the Rust buffer to
        Python ``bytes``. ``None`` explicitly opts out for trusted workloads.
        Refusal raises :class:`LimitError` with code ``pdf_output_size``.
        Encryption passwords are capped at 127 UTF-8 bytes.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_size", max_size)
        encryption = self._encryption_args(user_pw, owner_pw, permissions, object_streams=object_streams)
        self._apply_save_options(garbage=garbage, deflate=deflate)
        if encryption is not None:
            user, owner, perms = encryption
            return self._doc.save_bytes_encrypted(user, owner, perms, os.urandom(32), max_size)
        if object_streams:
            return self._doc.save_bytes_with_object_streams(max_size)
        return self._doc.save_bytes(max_size)

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
        _validate_password_input(user_pw, "user password")
        _validate_password_input(owner_pw, "owner password")
        user = user_pw if user_pw is not None else ""
        owner = owner_pw if owner_pw is not None else user
        return (user, owner, int(permissions))

    def close(self) -> None:
        """Close the document; subsequent operations raise an error."""
        self._closed = True
        self._doc.release_render_cache()

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
        return _resolve_page_index(pno, self._doc.page_count())

    def _lopdf_page_number(self, pno: int) -> int:
        """Validate a Python page index and convert it to one-based lopdf form."""
        return self._normalize_pno(pno) + 1

    def _page_entries(self, pages: Iterable[int], name: str, count: int) -> list[int]:
        """Validate explicit page entries against one page-count snapshot.

        Return one-based lopdf page numbers. Entries may repeat pages, but
        materialization stops at the first entry beyond the larger of
        ``count`` and 4,096, so repetition never costs more than one complete
        pass over a large document.
        """
        limit = max(count, _MIN_PAGE_ENTRY_LIMIT)
        numbers: list[int] = []
        for pno in pages:
            if len(numbers) >= limit:
                limit_code = "page_entry_count"
                msg = (
                    f"{name} cannot contain more than {limit} entries "
                    f"(the larger of the page count and {_MIN_PAGE_ENTRY_LIMIT})"
                )
                raise LimitError(limit_code, msg)
            numbers.append(_resolve_page_index(pno, count) + 1)
        return numbers

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
    *,
    limits: DocumentLimits | None = None,
) -> Document:
    """Open a :class:`Document`; equivalent to ``Document(...)``."""
    return Document(
        filename=filename,
        stream=stream,
        password=password,
        max_decompressed_size=max_decompressed_size,
        limits=limits,
    )


def peek_metadata(
    filename: str | os.PathLike[str] | None = None,
    stream: bytes | None = None,
    password: str | None = None,
    *,
    max_file_size: int | None = None,
) -> MetadataProbe:
    """Read metadata and page count without parsing the entire document.

    Return the keys from :attr:`Document.metadata` plus integer ``page_count``
    and boolean ``encrypted`` / ``repaired`` facts. This is suitable for
    scanning many PDFs. ``max_file_size`` rejects oversized path or byte input
    before metadata parsing; ``None`` leaves it unbounded. Returned standard
    Info text is capped at 1 MiB. Password input is capped at 127 UTF-8 bytes.
    """
    if (filename is None) == (stream is None):
        msg = "specify exactly one of filename or stream"
        raise ValueError(msg)
    _validate_optional_positive_int("max_file_size", max_file_size)
    _validate_password_input(password)
    if stream is not None:
        raw, page_count, version, encrypted, recovery = _Document.load_metadata_bytes(
            stream,
            password,
            max_file_size,
        )
    else:
        raw, page_count, version, encrypted, recovery = _Document.load_metadata(
            os.fspath(cast("os.PathLike[str] | str", filename)),
            password,
            max_file_size,
        )
    if recovery is not None:
        _warnings.warn(recovery, PylopdfWarning, stacklevel=2)
    return {
        "title": raw.get("Title", ""),
        "author": raw.get("Author", ""),
        "subject": raw.get("Subject", ""),
        "keywords": raw.get("Keywords", ""),
        "creator": raw.get("Creator", ""),
        "producer": raw.get("Producer", ""),
        "creationDate": raw.get("CreationDate", ""),
        "modDate": raw.get("ModDate", ""),
        "format": f"PDF {version}",
        "page_count": page_count,
        "encrypted": encrypted,
        "repaired": recovery is not None,
    }
