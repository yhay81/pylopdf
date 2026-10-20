"""Page views and the page-label helpers."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast, overload

from pylopdf._errors import StalePageError
from pylopdf._fonts import (
    _SYMBOLIC_BASE14_NAMES,
    _SYMBOLIC_FONTS,
    _package_face_weight,
    _require_winansi,
    _resolve_font_source,
    _resolve_generation_font,
    _validate_font_choice,
)
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    _DEFAULT_MAX_MARKDOWN_SIZE,
    _DEFAULT_MAX_RENDER_PAGE_SIZE,
    _DEFAULT_MAX_SVG_SIZE,
    _bounded_utf8_size,
    _insert_text_lines,
    _validate_cumulative_utf8_input,
    _validate_generated_text_lines,
    _validate_lineheight,
    _validate_optional_positive_int,
    _validate_rect,
    _validate_textbox_options,
    _validate_utf8_text_input,
)
from pylopdf._ocr import OcrEngine, _default_ocr_engine, _validate_ocr_rotation
from pylopdf._pixmap import _normalize_background
from pylopdf._shape import Shape
from pylopdf._tables import Table, TableDiagnostics, TableFinder
from pylopdf._types import (
    LINK_GOTO,
    LINK_GOTOR,
    LINK_LAUNCH,
    LINK_NAMED,
    LINK_NONE,
    LINK_URI,
    TEXT_ALIGN_LEFT,
    Point,
    Rect,
)
from pylopdf.pylopdf_core import LimitError, PdfError, Pixmap

if TYPE_CHECKING:
    import os
    from collections.abc import Callable, Iterable, Sequence
    from typing import Any

    from pylopdf._document import Document
    from pylopdf._fonts import Font
    from pylopdf._types import (
        AnnotationInfo,
        BlockEntry,
        DrawingInfo,
        DrawingItem,
        FontLanguage,
        ImageInfo,
        LinkInfo,
        OcrRotation,
        OcrWord,
        PageLabelInfo,
        RawTextPage,
        TextPage,
        WordEntry,
    )


_DEFAULT_MAX_TEXT_REPLACEMENT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_IMAGE_INPUT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_IMAGE_PIXELS = 64_000_000
_MAX_HIGHLIGHT_RECTS = 4096
_MAX_ANNOTATION_INPUT_TEXT_SIZE = 1024 * 1024
_RECT_COORDINATE_COUNT = 4
_MAX_SEARCH_INPUT_BYTES = 4096
_DEFAULT_MAX_SEARCH_HITS = 4096
_MAX_OCR_LAYER_WORDS = 4096
_MAX_OCR_LAYER_TEXT_BYTES = 1024 * 1024
_MAX_TEXT_REPLACEMENT_INPUT_BYTES = 4096
_DRAWING_LINE_POINT_COUNT = 2
_DRAWING_CUBIC_POINT_COUNT = 4

#: Portrait A4 in PDF units, used for damaged PDFs without a MediaBox.
_DEFAULT_MEDIABOX = (0.0, 0.0, 210.0 * 72.0 / 25.4, 297.0 * 72.0 / 25.4)


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


# The package re-exports this class; reprs, pickles, and documentation name it.
Page.__module__ = "pylopdf"
