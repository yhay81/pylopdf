"""PDF editing and rendering backed by Rust.

The :class:`Document` API follows pymupdf conventions. lopdf handles editing
and hayro handles rendering; both are pure Rust under permissive licenses.
"""

from __future__ import annotations

import enum
import functools
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
from pylopdf.pylopdf_core import LimitError, OcrError, PasswordError, PdfError, Pixmap, _Document, _OcrEngine

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from types import TracebackType
    from typing import Any, Self

__version__ = "0.11.0"
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
    "Document",
    "DocumentClosedError",
    "DocumentComplexity",
    "DocumentLimits",
    "DocumentMetadata",
    "DrawingInfo",
    "DrawingItem",
    "EncryptedDocumentError",
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
    "Rect",
    "StalePageError",
    "Table",
    "TableDiagnostics",
    "TableFinder",
    "TextBlock",
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
_DEFAULT_MAX_RENDER_BATCH_SIZE = 512 * 1024 * 1024
_DEFAULT_MAX_MARKDOWN_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_SVG_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_TEXT_REPLACEMENT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_PDF_OUTPUT_SIZE = 512 * 1024 * 1024
_DEFAULT_MAX_IMAGE_INPUT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_IMAGE_PIXELS = 64_000_000
_MAX_PAGE_LABEL_RANGES = 4096
_MAX_HIGHLIGHT_RECTS = 4096
_MAX_TOC_ENTRIES = 4096
_MAX_OCR_LAYER_WORDS = 4096
_MAX_OCR_LAYER_TEXT_BYTES = 1024 * 1024
_MAX_RENDER_BATCH_PAGES = 4096
_MAX_MARKDOWN_PAGES = 4096
_MAX_STRUCTURAL_PAGE_BATCH = 4096
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
    """

    max_file_size: int | None = None
    max_pages: int | None = None
    max_objects: int | None = None
    max_decompressed_size: int | None = None
    max_page_content_size: int | None = None
    max_total_decompressed_size: int | None = None
    max_object_depth: int | None = None
    max_text_size: int | None = None

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
    return cast("OcrRotation", rotation)


class OcrEngine:
    """Reusable, pure-Rust PP-OCR engine.

    With no paths, the engine discovers models from the optional
    ``pylopdf[ocr]`` installation. Explicit paths are useful for evaluating a
    compatible RTen-format PP-OCR detector, recognizer, and character
    dictionary.
    """

    def __init__(
        self,
        detector_path: str | os.PathLike[str] | None = None,
        recognizer_path: str | os.PathLike[str] | None = None,
        dictionary_path: str | os.PathLike[str] | None = None,
        *,
        threads: int | None = None,
        max_concurrent: int = 1,
    ) -> None:
        """Load an OCR model set once for reuse across pages and documents.

        ``threads=None`` uses up to four logical CPUs. Lower it to reduce peak
        inference memory or raise it to at most 16 after measuring the target
        workload. ``max_concurrent`` bounds complete render-and-recognize calls
        sharing this engine; its memory-safe default is one.
        """
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
        )
        self.threads = resolved_threads
        self.max_concurrent = resolved_max_concurrent
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

    def __init__(  # noqa: PLR0913 - internal owned result constructor
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


@functools.cache
def _bundled_cjk_fonts() -> tuple[tuple[str, bytes], ...]:
    """Load bundled JP-subset fonts when ``pylopdf[cjk]`` is installed."""
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


def _validate_textbox_options(
    fontsize: float,
    lineheight: float | None,
    align: int,
    expandtabs: int,
) -> tuple[float, float]:
    """Validate textbox layout options and return normalized size and leading."""
    try:
        resolved_fontsize = float(fontsize)
    except (TypeError, ValueError) as exc:
        msg = f"fontsize must be a positive number: {fontsize!r}"
        raise ValueError(msg) from exc
    if not (math.isfinite(resolved_fontsize) and resolved_fontsize > 0):
        msg = f"fontsize must be a positive number: {fontsize!r}"
        raise ValueError(msg)
    try:
        resolved_lineheight = 1.2 if lineheight is None else float(lineheight)
    except (TypeError, ValueError) as exc:
        msg = f"lineheight must be a positive number or None: {lineheight!r}"
        raise ValueError(msg) from exc
    if not (math.isfinite(resolved_lineheight) and resolved_lineheight > 0):
        msg = f"lineheight must be a positive number or None: {lineheight!r}"
        raise ValueError(msg)
    if isinstance(align, bool) or not isinstance(align, int) or not TEXT_ALIGN_LEFT <= align <= TEXT_ALIGN_JUSTIFY:
        msg = f"align must be one of 0, 1, 2, or 3: {align!r}"
        raise ValueError(msg)
    if isinstance(expandtabs, bool) or not isinstance(expandtabs, int) or expandtabs < 0:
        msg = f"expandtabs must be a non-negative integer: {expandtabs!r}"
        raise ValueError(msg)
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


def _read_font_source(fontfile: str | os.PathLike[str] | None, fontbuffer: bytes | None) -> bytes | None:
    """Read optional font bytes from at most one font source."""
    if fontfile is not None:
        if fontbuffer is not None:
            msg = "fontfile and fontbuffer cannot both be specified"
            raise ValueError(msg)
        return Path(fontfile).read_bytes()
    if fontbuffer is not None:
        return bytes(fontbuffer)
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

#: Times aliases select the optional serif CJK generation font.
_SERIF_BASE14_FONTS = frozenset({"tiro", "tibo", "tiit", "tibi"})

#: Japanese and Han ranges that trigger optional JP-subset font selection.
_BUNDLED_CJK_TEXT_RANGES = (
    (0x2E80, 0x312F),
    (0x31A0, 0x31FF),
    (0x3400, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF65, 0xFF9F),
    (0x20000, 0x2FA1F),
)


def _bundled_generation_font(text: str, fontname: str) -> bytes | None:
    """Select one optional JP-subset font for Japanese or Han text."""
    if not any(start <= ord(character) <= end for character in text for start, end in _BUNDLED_CJK_TEXT_RANGES):
        return None
    kind = "serif" if fontname in _SERIF_BASE14_FONTS else "sans"
    return next((data for candidate, data in _bundled_cjk_fonts() if candidate == kind), None)


def _resolve_generation_font(
    operation: str,
    text: str,
    fontname: str,
    font_data: bytes | None,
    fontindex: int,
) -> tuple[str | None, bytes | None]:
    """Resolve explicit, optional CJK, or Standard 14 generation input."""
    if font_data is not None:
        if isinstance(fontindex, bool) or not isinstance(fontindex, int) or not 0 <= fontindex <= _UINT32_MAX:
            msg = f"fontindex must be an integer from 0 through 4294967295: {fontindex!r}"
            raise ValueError(msg)
        return None, font_data
    if fontindex != 0:
        msg = "fontindex requires fontfile or fontbuffer"
        raise ValueError(msg)

    base_font = _BASE14_FONTS.get(fontname)
    if base_font is None:
        msg = f"fontname must be a standard-14 font abbreviation ({sorted(_BASE14_FONTS)}): {fontname!r}"
        raise ValueError(msg)
    bundled_font = _bundled_generation_font(text, fontname)
    if bundled_font is not None:
        return None, bundled_font
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        msg = (
            f"{operation} can only print WinAnsi (Latin-1-equivalent) characters without an embedded font. "
            "For Japanese or Han text, install pylopdf[cjk]; otherwise pass fontfile or fontbuffer"
        )
        raise ValueError(msg) from exc
    return base_font, None


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
    def get_text(self, option: str = "text") -> str | list[WordEntry] | list[BlockEntry] | TextPage:
        """Extract page text or positioned layout data.

        ``option`` matches :meth:`Document.get_page_text`: ``"text"``,
        ``"words"``, ``"blocks"``, or ``"dict"``.
        """
        self._page_number()
        return self._document.get_page_text(self._pno, option)  # type: ignore[call-overload]

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
        text: str,
        *,
        fontsize: float = 11.0,
        fontname: str = "helv",
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
        overlay: bool = True,
    ) -> None:
        r"""Draw text at ``point``, the first line's baseline-left display point.

        ``fontname`` is a pymupdf-style Standard 14 alias: the ``"helv"``,
        ``"tiro"``, and ``"cour"`` families with ``bo``/``it`` variants, plus
        ``"symb"`` and ``"zadb"``. These fonts are not embedded and text is
        limited to WinAnsi, roughly Latin-1.

        Pass exactly one of ``fontfile`` or ``fontbuffer`` to subset-embed an
        arbitrary OpenType font through krilla. This enables Unicode text,
        including shaped CJK and RTL scripts. With ``pylopdf[cjk]`` installed,
        Japanese and Han text without an explicit source automatically uses
        its JP-subset sans font, or serif for a Times ``fontname``.
        ``fontindex`` selects a face in a TrueType/OpenType collection and
        ``fontname`` is otherwise ignored for embedded fonts. A single line
        should use one script and the selected font must contain all needed
        glyphs; no per-glyph fallback or paragraph layout is performed. RTL
        glyph shaping renders correctly, but extraction currently follows
        visual rather than logical order.

        ``\n`` starts a new line at 1.2 times ``fontsize``. Text remains
        visually upright on rotated pages. ``overlay=False`` draws below
        existing content. Loop over pages for headers, footers, page numbers,
        or Bates numbers.
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
        red, green, blue = _validate_unit_rgb(color)
        if not text:
            msg = "text must be at least 1 character"
            raise ValueError(msg)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        font_data = _read_font_source(fontfile, fontbuffer)
        base_font, font_data = _resolve_generation_font("insert_text", normalized, fontname, font_data, fontindex)
        if font_data is not None:
            self._document._doc.insert_embedded_text(
                self._page_number(),
                (x, y),
                normalized.split("\n"),
                font_data,
                fontindex,
                float(fontsize),
                (red, green, blue),
                overlay,
            )
            return

        base_font = cast("str", base_font)
        lines = [line.encode("cp1252") for line in normalized.split("\n")]
        self._document._doc.insert_page_text(
            self._page_number(),
            (x, y),
            lines,
            base_font,
            fontname not in _SYMBOLIC_FONTS,
            float(fontsize),
            (red, green, blue),
            overlay,
        )

    def insert_textbox(  # noqa: PLR0913 - mirrors pymupdf's keyword-oriented drawing API
        self,
        rect: Sequence[float],
        text: str,
        *,
        fontsize: float = 11.0,
        fontname: str = "helv",
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
        align: int = TEXT_ALIGN_LEFT,
        expandtabs: int = 8,
        lineheight: float | None = None,
        overlay: bool = True,
    ) -> float:
        r"""Wrap and draw text inside a display-coordinate rectangle.

        The return value is the unused vertical space. A negative value means
        the laid-out text does not fit; in that case the document is not
        modified. Empty text returns the rectangle height without modifying the
        document.

        ``align`` accepts :data:`TEXT_ALIGN_LEFT`,
        :data:`TEXT_ALIGN_CENTER`, :data:`TEXT_ALIGN_RIGHT`, or
        :data:`TEXT_ALIGN_JUSTIFY`. Justification expands spaces on soft-wrapped
        lines, never the final line of a paragraph. ``lineheight`` is a
        font-size multiplier and defaults to 1.2. Tabs are expanded before
        Unicode line breaking; explicit newlines always start a new line.

        Without a font source, Adobe Core 14 metrics provide exact WinAnsi
        wrapping. Pass exactly one of ``fontfile`` or ``fontbuffer`` for
        HarfRust-shaped Unicode and a krilla subset-embedded OpenType font.
        With ``pylopdf[cjk]`` installed, Japanese and Han text automatically
        uses its JP-subset sans font, or serif for a Times ``fontname``.
        Unicode line-break opportunities support CJK without requiring spaces,
        and an overlong word falls back to grapheme-safe wrapping.
        """
        x0, y0, x1, y1 = _validate_rect(rect)
        page_number = self._page_number()
        resolved_fontsize, resolved_lineheight = _validate_textbox_options(fontsize, lineheight, align, expandtabs)
        red, green, blue = _validate_unit_rgb(color)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(expandtabs)
        if not normalized:
            return y1 - y0

        font_data = _read_font_source(fontfile, fontbuffer)
        base_font, font_data = _resolve_generation_font("insert_textbox", normalized, fontname, font_data, fontindex)
        if font_data is not None:
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
                if len(text) > remaining:
                    msg = f"word text cannot exceed {_MAX_OCR_LAYER_TEXT_BYTES} UTF-8 bytes per call"
                    raise ValueError(msg)
                encoded_bytes = len(text.encode())
                if encoded_bytes > remaining:
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
        if not search:
            msg = "search must be at least 1 character"
            raise ValueError(msg)
        if default_char is not None and len(default_char) != 1:
            msg = "default_char must contain exactly one character"
            raise ValueError(msg)
        _validate_optional_positive_int("max_size", max_size)
        input_size = len(search.encode()) + len(replacement.encode())
        if default_char is not None:
            input_size += len(default_char.encode())
        if input_size > _MAX_TEXT_REPLACEMENT_INPUT_BYTES:
            limit_code = "replacement_input_size"
            msg = (
                f"text replacement inputs total {input_size} UTF-8 bytes, exceeding the "
                f"{_MAX_TEXT_REPLACEMENT_INPUT_BYTES}-byte safety limit"
            )
            raise LimitError(limit_code, msg)
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
        - ``LINK_GOTOR`` / ``LINK_LAUNCH``: ``file`` and optional ``nameddest``.
        - ``LINK_NAMED``: action ``name``, such as ``NextPage``.

        GoTo named destinations resolve from both the ``/Names`` name tree and
        the legacy ``/Dests`` dictionary. Reads share the 4,096-annotation and
        1 MiB aggregate metadata-text boundaries with :meth:`annots`. Name-tree
        resolution builds one borrowed index per call, is cycle-aware, and
        rejects excessive entries, nodes, edges, depth, or key bytes instead of
        returning a silent unresolved result.
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
        budget.
        """
        seq = list(rects)
        if not seq:
            msg = "rects must contain at least one rect"
            raise ValueError(msg)
        if len(seq) > _MAX_HIGHLIGHT_RECTS:
            msg = f"rects cannot contain more than {_MAX_HIGHLIGHT_RECTS} rectangles"
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
        README ecosystem recipe. Its subtype and URI share a 1 MiB text budget.
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
        clip: Sequence[float] | None = None,
    ) -> Pixmap:
        """Render the page to a straight-alpha RGBA8 :class:`Pixmap`.

        Arguments match :meth:`Document.render_page`. The pixmap exposes
        ``width``, ``height``, ``stride``, ``n``, ``samples`` as bytes, and
        ``tobytes()`` as PNG. Convert it to NumPy with
        ``np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 4)``.

        ``clip`` is an optional display-coordinate rectangle. It is intersected
        with the page and rounded outward to pixel boundaries. A clip that does
        not intersect the page raises :class:`PdfError`. hayro currently lacks
        an offset viewport, so clipping reduces the returned pixel data but not
        the full-page rasterization cost or render-size limits.
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
    ) -> bytes:
        """Render the page to PNG with :meth:`Document.render_page` arguments."""
        self._page_number()
        return self._document.render_page(self._pno, scale, dpi=dpi, background=background)

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
        ``limits`` applies structural, decompression, and interpreted-text
        budgets before expensive work. :meth:`DocumentLimits.web` is a
        conservative starting profile for untrusted uploads.
        ``max_decompressed_size`` remains as the compatible single-budget
        shorthand and cannot be combined with ``limits``.
        """
        if filename is not None and stream is not None:
            msg = "filename and stream cannot both be specified"
            raise ValueError(msg)
        if limits is not None and not isinstance(limits, DocumentLimits):
            msg = f"limits must be a DocumentLimits instance or None: {limits!r}"
            raise TypeError(msg)
        if limits is not None and max_decompressed_size is not None:
            msg = "limits and max_decompressed_size cannot both be specified"
            raise ValueError(msg)
        resolved_limits = limits or DocumentLimits(max_decompressed_size=max_decompressed_size)
        limit_args = _document_limit_args(resolved_limits)
        path = None if filename is None else str(filename)
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
            doc = _Document(resolved_limits.max_text_size)
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
        """Return whether opening repaired an incorrect classic startxref."""
        self._ensure_not_closed()
        return self._doc.is_repaired()

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
        updates are validated and applied atomically.
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
        order; ``None`` means every page. One call accepts at most 4,096 page
        entries. ``max_size`` caps aggregate UTF-8 Markdown output and defaults
        to 64 MiB; ``None`` explicitly opts out.
        """
        self._ensure_open()
        if table_strategy not in ("lines", "text", None):
            msg = f"table_strategy must be 'lines', 'text', or None: {table_strategy!r}"
            raise ValueError(msg)
        _validate_optional_positive_int("max_size", max_size)
        page_numbers: list[int] = []
        page_iter = range(self.page_count) if pages is None else pages
        for pno in page_iter:
            if len(page_numbers) >= _MAX_MARKDOWN_PAGES:
                msg = f"pages cannot contain more than {_MAX_MARKDOWN_PAGES} entries"
                raise ValueError(msg)
            self._lopdf_page_number(pno)
            page_numbers.append(pno)

        size_counts: Counter[float] = Counter()
        for pno in page_numbers:
            layout = self.get_page_text(pno, "dict")
            tables = [] if table_strategy is None else _markdown_page_tables(self[pno], table_strategy)
            bboxes = [(table.bbox.x0, table.bbox.y0, table.bbox.x1, table.bbox.y1) for table in tables]
            size_counts.update(_markdown.collect_sizes([layout], [bboxes]))
        levels = _markdown.heading_levels(size_counts)

        rendered: list[str] = []
        output_bytes = 0
        for pno in page_numbers:
            layout = self.get_page_text(pno, "dict")
            tables = [] if table_strategy is None else _markdown_page_tables(self[pno], table_strategy)
            separator_bytes = 2 if rendered else 0
            page_budget = None if max_size is None else max_size - output_bytes - separator_bytes
            bboxes, markdown_tables = _markdown_table_output(
                layout,
                tables,
                max_size=max_size,
                remaining_size=page_budget,
            )
            words = self.get_page_text(pno, "words") if tables else None
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

    def set_form_field(  # noqa: C901 - Keep validation adjacent to the public boundary.
        self,
        name: str,
        value: str | bool,  # noqa: FBT001 - Match pymupdf's bool API.
        *,
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
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
        and 1 MiB value limits are checked without leaving a partial update.
        Button handling additionally caps widgets, appearance states, and state
        names before resolving booleans or generating missing appearances.

        WinAnsi text uses Helvetica. Pass exactly one of ``fontfile`` or
        ``fontbuffer`` to subset-embed an arbitrary OpenType font. Unicode text
        automatically uses the sans font from ``pylopdf[cjk]`` when installed;
        otherwise the value is still stored with ``NeedAppearances`` for viewer
        compatibility, but its appearance cannot be rendered by pylopdf.
        Rich text, pushbuttons, and signature fields are unsupported; use the
        pyHanko integration for signatures.
        """
        self._ensure_open()
        if not name:
            msg = "name must be at least 1 character"
            raise ValueError(msg)
        font_data = _read_font_source(fontfile, fontbuffer)
        if font_data is not None:
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
            if not isinstance(value, str):
                msg = f"value must be a string or bool: {value!r}"
                raise TypeError(msg)
            resolved = value
            if font_data is None:
                try:
                    resolved.encode("cp1252")
                except UnicodeEncodeError:
                    bundled = _bundled_cjk_fonts()
                    if bundled:
                        font_data = bundled[0][1]
        self._doc.set_form_field(name, resolved, font_data, fontindex)

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
        Their aggregate input text is capped at 1 MiB. Existing inline
        FileSpecs are cloned only after bounded shape validation.
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
        """
        self._ensure_open()
        _validate_optional_positive_int("max_size", max_size)
        return self._doc.embfile_get(name, max_size)

    def embfile_del(self, name: str) -> None:
        """Delete an attachment, raising an error when absent."""
        self._ensure_open()
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

    @overload
    def get_page_text(self, pno: int, option: Literal["text"] = "text") -> str: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["blocks"]) -> list[BlockEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["dict"]) -> TextPage: ...
    def get_page_text(self, pno: int, option: str = "text") -> str | list[WordEntry] | list[BlockEntry] | TextPage:
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
        msg = f"option must be one of 'text' / 'words' / 'blocks' / 'dict': {option!r}"
        raise ValueError(msg)

    def delete_page(self, pno: int) -> None:
        """Delete zero-based page ``pno``; negative values count from the end."""
        page_number = self._lopdf_page_number(pno)
        self._doc.delete_pages([page_number])
        self._bump_generation()

    def delete_pages(self, page_numbers: Iterable[int]) -> None:
        """Delete up to 4,096 zero-based pages; negative values count from the end."""
        self._ensure_open()
        numbers = self._materialize_structural_pages(page_numbers, "page_numbers")
        if not numbers:
            return
        self._doc.delete_pages(numbers)
        self._bump_generation()

    def select(self, page_numbers: Iterable[int]) -> None:
        """Keep only the given zero-based pages in the given order.

        This also reorders pages. Repeating an index duplicates that page; the
        duplicate shares Contents and Resources objects with the original. One
        call accepts at most 4,096 entries.
        """
        self._ensure_open()
        numbers = self._materialize_structural_pages(page_numbers, "page_numbers")
        self._bump_generation()
        self._doc.select(numbers)

    def _materialize_structural_pages(
        self,
        page_numbers: Iterable[int],
        name: str,
    ) -> list[int]:
        """Validate one bounded iterable of zero-based structural page inputs."""
        numbers: list[int] = []
        for pno in page_numbers:
            if len(numbers) >= _MAX_STRUCTURAL_PAGE_BATCH:
                msg = f"{name} cannot contain more than {_MAX_STRUCTURAL_PAGE_BATCH} entries"
                raise ValueError(msg)
            numbers.append(self._lopdf_page_number(pno))
        return numbers

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
        count = abs(stop - start) + 1
        if count > _MAX_STRUCTURAL_PAGE_BATCH:
            msg = f"page range cannot contain more than {_MAX_STRUCTURAL_PAGE_BATCH} entries"
            raise ValueError(msg)
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
        other page APIs. Return an empty list when no TOC exists. Traversal is
        cycle-aware and rejects more than 4,096 nodes or entries, 8,192 edges,
        64 levels, 32 destination indirections, or 1 MiB of source/returned text.
        """
        self._ensure_open()
        return [[level, title, page] for level, title, page in self._doc.get_toc()]

    def set_toc(self, toc: Sequence[Sequence[int | str]]) -> None:
        """Replace bookmarks from ``[[level, title, page number], ...]``.

        An empty sequence removes them. At most 4,096 entries, 64 levels, and
        1 MiB each of input and encoded title text are accepted. Levels start at
        1 and can increase by at most one from the previous entry. Page numbers
        are one-based, matching :meth:`get_toc`. Validation is atomic.
        """
        self._ensure_open()
        if len(toc) > _MAX_TOC_ENTRIES:
            msg = f"toc cannot contain more than {_MAX_TOC_ENTRIES} entries"
            raise ValueError(msg)
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
        document while the GIL is released. One call accepts up to 4,096 page
        entries. ``max_size`` caps their cumulative encoded PNG bytes and
        defaults to 512 MiB; pass ``None`` only to accept unbounded output.

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
        page_numbers: list[int] = []
        page_iter = range(self.page_count) if pages is None else pages
        for pno in page_iter:
            if len(page_numbers) >= _MAX_RENDER_BATCH_PAGES:
                msg = f"pages cannot contain more than {_MAX_RENDER_BATCH_PAGES} entries"
                raise ValueError(msg)
            page_numbers.append(self._lopdf_page_number(pno))
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

    def _emit_warnings(self) -> None:
        """Emit recoverable interpretation warnings as ``PylopdfWarning``."""
        for message in self._doc.take_warnings():
            _warnings.warn(message, PylopdfWarning, stacklevel=3)

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
        the GIL, is atomic on decoding errors, rejects more than 16,384 unique
        image objects or 250 million eligible source pixels, and skips an
        individual source above 64 million pixels.

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
        unrestricted opening with permission controls. ``permissions`` combines
        :class:`Permissions` and defaults to all. Encryption cannot be combined
        with object streams. Every mode streams to a same-directory temporary
        file and replaces ``filename`` only after a complete successful write.
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
) -> MetadataProbe:
    """Read metadata and page count without parsing the entire document.

    Return the keys from :attr:`Document.metadata` plus integer ``page_count``
    and boolean ``encrypted`` / ``repaired`` facts. This is suitable for
    scanning many PDFs. Returned standard Info text is capped at 1 MiB.
    """
    if (filename is None) == (stream is None):
        msg = "specify exactly one of filename or stream"
        raise ValueError(msg)
    if stream is not None:
        raw, page_count, version, encrypted, repaired = _Document.load_metadata_bytes(stream, password)
    else:
        raw, page_count, version, encrypted, repaired = _Document.load_metadata(str(filename), password)
    if repaired:
        _warnings.warn(
            "recovered a PDF with an incorrect startxref offset",
            PylopdfWarning,
            stacklevel=2,
        )
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
        "repaired": repaired,
    }
