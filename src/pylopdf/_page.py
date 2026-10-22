"""Page views and the page-label helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast, overload

from pylopdf._annots import _SUBTYPE_TYPES, Annot, AnnotType
from pylopdf._canvas import Canvas
from pylopdf._content import Content, _content_from_core
from pylopdf._errors import StalePageError
from pylopdf._fonts import (
    _SYMBOLIC_BASE14_NAMES,
    _package_face_weight,
    _require_winansi,
    _resolve_font_source,
    _resolve_generation_font,
    _validate_font_choice,
)
from pylopdf._geometry import Matrix, Quad
from pylopdf._hints import hinted_getattr
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    _DEFAULT_MAX_IMAGE_INPUT_SIZE,
    _DEFAULT_MAX_IMAGE_PIXELS,
    _DEFAULT_MAX_MARKDOWN_SIZE,
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
from pylopdf._objects import Dictionary
from pylopdf._ocr import OcrEngine, _default_ocr_engine, _validate_ocr_rotation
from pylopdf._pixmap import _render_background, _render_scale
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
    ImageItem,
    ImageItemFull,
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
        Block,
        DrawingInfo,
        DrawingItem,
        FontLanguage,
        ImageInfo,
        LinkInfo,
        OcrRotation,
        OcrWord,
        PageLabelInfo,
        RawTextPageDict,
        TextPageDict,
        Word,
    )


_DEFAULT_MAX_TEXT_REPLACEMENT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_CONTENT_ELEMENTS = 262_144
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


def _page_label(labels: Sequence[PageLabelInfo], pno: int) -> str:
    """Return the label of zero-based page ``pno`` under sorted ``labels``, or empty."""
    applicable: PageLabelInfo | None = None
    for label in labels:
        if label["startpage"] <= pno:
            applicable = label
        else:
            break
    if applicable is None:
        return ""
    number = pno - applicable["startpage"] + applicable["firstpagenum"]
    return _format_page_label(applicable["style"], applicable["prefix"], number)


def _normalized_box(box: Sequence[float]) -> Rect:
    """Return a stored page box with ``x0 <= x1`` and ``y0 <= y1``, as MuPDF reads it."""
    x0, y0, x1, y1 = box
    return Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _highlight_rects(
    quads: Sequence[float] | Quad | Sequence[Sequence[float] | Quad] | None,
) -> Sequence[float] | Sequence[Sequence[float]]:
    """Resolve pymupdf's ``quads`` argument to rectangles; a quad gives its bounding rectangle."""
    if quads is None:
        msg = "quads is required; text selection by start= and stop= is not supported"
        raise TypeError(msg)
    if isinstance(quads, Quad):
        return quads.rect
    if not isinstance(quads, (list, tuple)):
        # Map an iterator lazily, so the rectangle limit still stops it early.
        return cast(
            "Sequence[Sequence[float]]",
            (quad.rect if isinstance(quad, Quad) else quad for quad in quads),
        )
    if any(isinstance(quad, Quad) for quad in quads):
        return [quad.rect if isinstance(quad, Quad) else cast("Sequence[float]", quad) for quad in quads]
    return cast("Sequence[float] | Sequence[Sequence[float]]", quads)


class Page:
    """A view of one page in a document, obtained through ``doc[i]``.

    Adding, deleting, or reordering pages invalidates existing views. Using an
    invalidated view raises :class:`StalePageError`; fetch it again with
    ``doc[i]``.
    """

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

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

    @property
    def xref(self) -> int:
        """Return the object number of the page dictionary."""
        return self._document._doc.page_object_id(self._page_number())[0]

    @property
    def obj(self) -> Dictionary:
        """Return the page dictionary as a live low-level handle.

        Entries that the page inherits from the page tree, such as
        ``/Resources`` or ``/MediaBox``, are read from its ancestors through
        ``/Parent`` and are not visible here.
        """
        number, generation = self._document._doc.page_object_id(self._page_number())
        return Dictionary(self._document, (number, generation))

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
        """Return the MediaBox in PDF coordinates, as stored, or A4 when absent.

        As in pymupdf, the corners are normalized so that ``x0 <= x1`` and
        ``y0 <= y1``.
        """
        box = self._document._doc.get_page_box(self._page_number(), "MediaBox")
        return _normalized_box(box if box is not None else _DEFAULT_MEDIABOX)

    @property
    def cropbox(self) -> Rect:
        """Return the CropBox in pymupdf's box convention, or the MediaBox.

        The x coordinates are the stored ones, and the y axis is flipped
        against the top of the MediaBox, so the CropBox of an uncropped page
        that starts at the origin equals its MediaBox.
        """
        mediabox = self.mediabox
        box = self._document._doc.get_page_box(self._page_number(), "CropBox")
        crop = mediabox if box is None else _normalized_box(box)
        return Rect(crop.x0, mediabox.y1 - crop.y1, crop.x1, mediabox.y1 - crop.y0)

    @property
    def rect(self) -> Rect:
        """Return the display rectangle with origin 0,0 and rotation resolved.

        The visible page is the CropBox clipped to the MediaBox, as pymupdf
        defines it, and the rectangle is the one that rendering, extraction,
        and drawing share. A CropBox that misses the MediaBox leaves the A4
        page that the renderer then draws, where pymupdf uses Letter.
        """
        width, height = self._document._doc.page_display_size(self._page_number())
        return Rect(0.0, 0.0, width, height)

    def set_mediabox(self, rect: Sequence[float]) -> None:
        """Set the MediaBox in PDF coordinates and remove the other page boxes.

        As in pymupdf, the CropBox, BleedBox, TrimBox, and ArtBox are removed,
        so the whole new MediaBox becomes visible; a CropBox that the page
        would inherit from the page tree is replaced by the MediaBox. An empty
        rectangle raises ``ValueError``.
        """
        x0, y0, x1, y1 = _validate_rect(rect, name="MediaBox")
        self._document._doc.set_mediabox(self._page_number(), x0, y0, x1, y1)

    def set_cropbox(self, rect: Sequence[float]) -> None:
        """Set the CropBox in pymupdf's box convention.

        ``rect`` uses the coordinates that :attr:`cropbox` returns: stored x,
        and y flipped against the top of the MediaBox. As in pymupdf, the box
        must have area and lie inside the MediaBox; otherwise ``ValueError``
        is raised and the page is unchanged.
        """
        x0, y0, x1, y1 = _validate_rect(rect, name="CropBox")
        mediabox = self.mediabox
        stored = Rect(x0, mediabox.y1 - y1, x1, mediabox.y1 - y0)
        if not (
            mediabox.x0 <= stored.x0 < stored.x1 <= mediabox.x1 and mediabox.y0 <= stored.y0 < stored.y1 <= mediabox.y1
        ):
            msg = (
                f"CropBox not in MediaBox: {rect!r} lies outside {Rect(mediabox.x0, 0, mediabox.x1, mediabox.height)!r}"
            )
            raise ValueError(msg)
        self._document._doc.set_page_box(self._page_number(), "CropBox", *stored)

    @overload
    def get_text(self, option: Literal["text"] = "text") -> str: ...
    @overload
    def get_text(self, option: Literal["words"]) -> list[Word]: ...
    @overload
    def get_text(self, option: Literal["blocks"]) -> list[Block]: ...
    @overload
    def get_text(self, option: Literal["dict"]) -> TextPageDict: ...
    @overload
    def get_text(self, option: Literal["rawdict"]) -> RawTextPageDict: ...
    def get_text(self, option: str = "text") -> str | list[Word] | list[Block] | TextPageDict | RawTextPageDict:
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
        return resolved_engine._recognize_page(
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
        text: str,
        *,
        max_hits: int | None = _DEFAULT_MAX_SEARCH_HITS,
    ) -> list[Rect]:
        """Search page text case-insensitively for ``text``.

        Return one :class:`Rect` per match. Search is line-based and does not
        detect matches spanning lines. ``text`` is limited to 4,096 UTF-8
        bytes. Results default to 4,096 matches; ``max_hits=None`` explicitly
        opts trusted workloads out.
        """
        if not text:
            msg = "text must be at least 1 character"
            raise ValueError(msg)
        _validate_optional_positive_int("max_hits", max_hits)
        _validate_utf8_text_input(
            text,
            _MAX_SEARCH_INPUT_BYTES,
            limit_code="search_input_size",
            input_label="search text",
        )
        hits = self._document._doc.search_page(self._page_number(), text, max_hits)
        self._document._emit_warnings()
        return [Rect(*hit) for hit in hits]

    def find_tables(
        self,
        *,
        clip: Sequence[float] | None = None,
        strategy: Literal["lines", "text"] | None = None,
    ) -> TableFinder:
        """Find high-confidence tables from vector borders or aligned text.

        ``strategy=None`` selects ``"lines"``, as in pymupdf. The ``"lines"``
        strategy is deterministic and does not rasterize
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
        if strategy is None:
            strategy = "lines"
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

    @overload
    def get_images(self, *, full: Literal[False] = False) -> list[ImageItem]: ...
    @overload
    def get_images(self, *, full: Literal[True]) -> list[ImageItemFull]: ...
    def get_images(self, *, full: bool = False) -> list[ImageItem] | list[ImageItemFull]:
        """List the image XObjects that the page's resources reach, as pymupdf does.

        Each item is an :class:`ImageItem` ``(xref, smask, width, height, bpc,
        colorspace, alt_colorspace, name, filter)``; ``full=True`` returns
        :class:`ImageItemFull` items that add ``referencer``, the xref of the
        Form XObject whose resources list the image, or 0 for the page. The
        page's resources and, depth first, those of the Form XObjects they
        list are read in resource order, and each image appears once. Inline
        images and images that only annotations draw are not listed, and an
        image need not be drawn to be listed. Use :meth:`get_image_info` for
        drawn placements and :meth:`Document.extract_image` for image data. A
        page that lists more than 4,096 images or 4,096 Form XObjects raises
        :class:`LimitError` with code ``image_inventory_count``.
        """
        raw = self._document._doc.page_image_inventory(self._page_number())
        if full:
            return [ImageItemFull(*item) for item in raw]
        return [ImageItem(*item[:9]) for item in raw]

    def get_image_info(self, *, hashes: bool = False, xrefs: bool = False) -> list[ImageInfo]:
        """Describe each image drawn on the page, in drawing order, as pymupdf does.

        Each dictionary has pymupdf's keys: ``number``, ``bbox`` in display
        coordinates, ``transform`` (the :class:`Matrix` that maps the unit
        square to ``bbox``), ``width`` and ``height`` in pixels,
        ``colorspace`` (its number of components), ``cs-name``, ``xres`` and
        ``yres``, ``bpc``, ``size`` (encoded bytes), and ``has-mask``.
        ``xrefs=True`` adds ``xref``, 0 for an inline image or an image mask.
        ``hashes=True`` raises :class:`PdfError` until pylopdf computes image
        digests. More than 4,096 placements on one page raise
        :class:`PdfError` instead of returning a partial list.
        """
        if hashes:
            msg = "hashes=True is not supported yet"
            raise PdfError(msg)
        raw = self._document._doc.page_image_info(self._page_number())
        self._document._emit_warnings()
        infos: list[ImageInfo] = []
        for number, (
            xref,
            bbox,
            transform,
            width,
            height,
            components,
            cs_name,
            bpc,
            size,
            has_mask,
            (xres, yres),
        ) in enumerate(raw):
            info: ImageInfo = {
                "number": number,
                "bbox": Rect(*bbox),
                "transform": Matrix(*transform),
                "width": width,
                "height": height,
                "colorspace": components,
                "cs-name": cs_name,
                "xres": xres,
                "yres": yres,
                "bpc": bpc,
                "size": size,
                "has-mask": has_mask,
            }
            if xrefs:
                info["xref"] = xref
            infos.append(info)
        return infos

    def content(
        self,
        *,
        clip: Sequence[float] | None = None,
        annotations: bool = True,
        max_elements: int | None = _DEFAULT_MAX_CONTENT_ELEMENTS,
    ) -> Content:
        """Return the text runs, paths, and images that the page paints.

        Items come in paint order, in display coordinates, as hayro
        interprets the page, with optional-content visibility applied. Each
        carries its paints, its blend mode and soft mask, and the
        :class:`GraphicsContext` of clips, transparency groups, and marked
        content that encloses it. Glyph boxes are the exact boxes of the glyph
        outlines. hayro paints every image inside a transparency group that
        holds the graphics state's opacity, blend mode, and soft mask, so an
        image's context ends with that group. A shading painted with ``sh``
        is a :class:`Path` that covers the clip region and fills with a
        :class:`ShadingPattern`.

        ``clip`` keeps the items whose box meets a display-coordinate
        rectangle. ``annotations=False`` leaves out annotation appearances,
        which otherwise follow the page content. Items whose geometry or paint
        is not finite, which hayro cannot paint either, are left out.

        ``max_elements`` bounds the glyphs, path and clip segments, and items
        of one interpretation, before ``clip`` applies; above it the call
        raises :class:`LimitError` with code ``content_element_count``.
        ``None`` removes the bound for trusted input.
        """
        if not isinstance(annotations, bool):
            msg = f"annotations must be a bool: {annotations!r}"
            raise TypeError(msg)
        _validate_optional_positive_int("max_elements", max_elements)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        raw = self._document._doc.page_content(self._page_number(), annotations, clip_rect, max_elements)
        self._document._emit_warnings()
        return _content_from_core(raw)

    def get_drawings(self) -> list[DrawingInfo]:
        """Extract interpreted vector paint operations in display coordinates.

        Results use pymupdf-style path dictionaries with ``type`` equal to
        ``"f"``, ``"s"``, or ``"fs"``. ``items`` contains self-contained line
        and cubic Bézier commands. Quadratic curves are converted exactly to
        cubics. Stroke colors use ``color``; fill colors use ``fill``. Exotic
        pattern paints do not have one representative RGB value and return
        ``None`` for color and opacity. ``seqno`` is the paint operation's
        zero-based position among the page's paint operations, which count
        paths, text runs, and images in drawing order, as pymupdf numbers them;
        a combined fill-and-stroke keeps the number of its fill.

        Clipping paths and clip-resolved visibility, transparency-group
        structure, optional-content layer names (pymupdf's ``layer`` key), text
        outlines, and images are not returned. The interpreter still applies optional-content visibility
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
            seqno,
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
                    "seqno": seqno,
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
    ) -> int:
        """Draw an image into ``rect`` and return the image XObject's xref.

        ``rect`` uses top-left-origin display coordinates.

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
        embedding another copy. :meth:`Document.rewrite_images`,
        :meth:`replace_text`, :meth:`Document.set_form_field`, and
        ``deflate=True`` saves end this sharing, so the next placement embeds
        the input again. A reopened document shares only its new placements.
        The returned xref is that of the shared XObject when the input was
        placed again, as pymupdf returns the xref of a reused image.
        """
        _validate_optional_positive_int("max_size", max_size)
        _validate_optional_positive_int("max_pixels", max_pixels)
        image_rotation = _validate_image_rotation(rotate)
        source = _resolve_image_source(filename, stream, pixmap)
        if isinstance(source, bytes) and max_size is not None and len(source) > max_size:
            raise _image_input_limit_error(len(source), max_size)
        x0, y0, x1, y1 = _validate_rect(rect)
        if isinstance(source, Pixmap):
            return self._document._doc.insert_pixmap(
                self._page_number(),
                (x0, y0, x1, y1),
                source,
                image_rotation,
                keep_proportion,
                overlay,
            )
        if isinstance(source, Path):
            return self._document._doc.insert_image_file(
                self._page_number(),
                (x0, y0, x1, y1),
                str(source),
                image_rotation,
                keep_proportion,
                overlay,
                max_size,
                max_pixels,
            )
        return self._document._doc.insert_image(
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
        docsrc: Document,
        pno: int = 0,
        *,
        keep_proportion: bool = True,
        overlay: bool = True,
    ) -> int:
        """Overlay page ``pno`` from ``docsrc`` into ``rect`` and return the Form xref.

        The source page becomes a Form XObject drawn as vector content, and the
        return value is its xref, as in pymupdf.

        ``pno`` is zero-based and may be negative. This is the pymupdf-style
        primitive for watermarks, stamps, and letterheads. The source page is
        embedded as a Form XObject, preserving text, vectors, and embedded
        fonts. Overlaying a one-page typst PDF enables CJK watermarks, headers,
        and footers; see the README ecosystem recipe. Source rotation and
        CropBox are resolved visually to fit ``rect``. ``docsrc`` may be the
        same document; pylopdf imports from a pre-edit snapshot so the source
        page, including the target page itself, remains stable during
        placement.

        Placing a page of ``docsrc`` again places the Form XObject of the
        earlier placement, with its fonts and images, instead of importing
        another copy, as long as ``docsrc`` has not been edited since; any
        edit of ``docsrc`` makes the next placement import the page again.
        Edits that end image sharing, as described in :meth:`insert_image`,
        end this sharing too. A same-document source page stays shared across
        consecutive placements on other pages.
        """
        x0, y0, x1, y1 = _validate_rect(rect)
        src_number = docsrc[pno]._page_number()
        if docsrc is self._document:
            return self._document._doc.show_pdf_page_self(
                self._page_number(),
                (x0, y0, x1, y1),
                src_number,
                keep_proportion,
                overlay,
            )
        return self._document._doc.show_pdf_page(
            self._page_number(),
            (x0, y0, x1, y1),
            docsrc._doc,
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
        /,
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
        expandtabs: int = 1,
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
        lines, never the final line of a paragraph. Tabs are expanded to
        ``expandtabs`` columns, 1 by default as in pymupdf, before Unicode line
        breaking; explicit newlines always start a new line.

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
            base_font not in _SYMBOLIC_BASE14_NAMES,
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

    def draw(self, *, underlay: bool = False) -> Canvas:
        """Return a :class:`Canvas` that draws on this page.

        Use it in a ``with`` block, which writes the drawing when it ends
        normally: above the page's content, or below it with ``underlay=True``.
        """
        return Canvas(self, underlay=underlay)

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
        return _page_label(self._document.get_page_labels(), self._page_number() - 1)

    def annots(self, *, types: Iterable[int] | None = None) -> list[Annot]:
        """Return the page's annotations as read-only :class:`Annot` handles.

        As in pymupdf, ``types`` limits the result to the given
        ``PDF_ANNOT_*`` types. Handles read the document on every access.
        Reads reject partial output above 4,096 annotations or 1 MiB of
        aggregate encoded or returned annotation text per call.
        """
        wanted = None if types is None else frozenset(int(kind) for kind in types)
        raw = self._document._doc.read_annotations(self._page_number())
        handles = [Annot(self, xref, index) for xref, index, *_ in raw]
        if wanted is None:
            return handles
        return [
            handle
            for handle, entry in zip(handles, raw, strict=True)
            if _SUBTYPE_TYPES.get(entry[2], AnnotType.UNKNOWN) in wanted
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
        for (kind, rect, uri, page, to, zoom, file, name), xref, link_id in raw:
            link: LinkInfo = {
                "kind": kind_map.get(kind, LINK_NONE),
                "from": Rect(*rect),
                "xref": xref,
                "id": link_id,
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
        quads: Sequence[float] | Quad | Sequence[Sequence[float] | Quad] | None = None,
        *,
        color: tuple[float, float, float] = (1.0, 1.0, 0.0),
        opacity: float = 0.4,
        content: str | None = None,
    ) -> Annot:
        """Add one highlight annotation and return its :class:`Annot`, as pymupdf does.

        ``quads`` is one rectangle or quad, or a sequence of them, in display
        coordinates; a :class:`Quad` contributes its bounding rectangle. Pass
        :meth:`search_for` output directly to highlight search results.
        ``color``, ``opacity``, and ``content`` are extensions.
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

        rects = _highlight_rects(quads)
        iterator = iter(rects)
        try:
            first = next(iterator)
        except StopIteration:
            msg = "quads must contain at least one rect or quad"
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
        xref = self._document._doc.add_highlight_annotation(
            self._page_number(), validated, rgb, float(opacity), content
        )
        return Annot(self, xref, -1)

    def insert_link(self, link: Mapping[str, Any], /) -> None:
        """Insert a link annotation described by a pymupdf link dictionary.

        ``link`` has the shape that :meth:`get_links` returns: ``kind`` and
        ``from`` (a display-coordinate rectangle), plus ``uri`` for
        :data:`LINK_URI`. Other kinds raise :class:`PdfError` until pylopdf
        writes their destinations. The subtype and URI share a 1 MiB text
        budget; oversized text is rejected before the Rust boundary with
        ``LimitError.code == "annotation_input_size"``.
        """
        if not isinstance(link, Mapping):
            msg = f"link must be a mapping: {type(link).__name__}"
            raise TypeError(msg)
        kind = link.get("kind")
        if kind != LINK_URI:
            msg = f"insert_link supports kind=LINK_URI only: {kind!r}"
            raise PdfError(msg)
        uri = link.get("uri")
        if not isinstance(uri, str):
            msg = f"a LINK_URI link needs a string uri: {uri!r}"
            raise TypeError(msg)
        if not uri:
            msg = "uri must be at least 1 character"
            raise ValueError(msg)
        if "from" not in link:
            msg = "link needs a 'from' rectangle"
            raise ValueError(msg)
        _validate_cumulative_utf8_input(
            ("Link", uri),
            _MAX_ANNOTATION_INPUT_TEXT_SIZE,
            limit_code="annotation_input_size",
            input_label="annotation subtype and URI input",
        )
        x0, y0, x1, y1 = _validate_rect(link["from"], name="from")
        self._document._doc.add_link_annotation(self._page_number(), (x0, y0, x1, y1), uri)

    def get_pixmap(
        self,
        *,
        matrix: Matrix | Sequence[float] | None = None,
        dpi: float | None = None,
        clip: Sequence[float] | None = None,
        alpha: bool = False,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
    ) -> Pixmap:
        """Render the page to a :class:`Pixmap`, as pymupdf does.

        The default is opaque RGB (``n == 3``) on white. ``alpha=True`` keeps
        transparency in straight-alpha RGBA (``n == 4``). ``matrix`` scales
        the page, where the identity is 72 dpi; ``dpi`` takes precedence over
        it. Only scaling matrices are supported: rotation, shear, and flips
        raise :class:`PdfError`. ``background`` is an extension: an RGB or RGBA
        tuple with components from 0 to 255 painted beneath the page, which
        must be opaque without ``alpha``.

        ``clip`` is an optional display-coordinate rectangle. It is intersected
        with the page and rounded outward to pixel boundaries. A clip that does
        not intersect the page raises :class:`PdfError`. Only the clipped
        region is rasterized, but full-page render-size limits still apply:
        65,535 pixels per side and 64,000,000 pixels in all.

        The pixmap exposes ``width``, ``height``, ``n``, ``alpha``,
        ``stride``, ``samples``, a zero-copy buffer, and ``tobytes()`` as PNG.
        Convert it to NumPy with
        ``np.frombuffer(pix, np.uint8).reshape(pix.height, pix.width, pix.n)``.
        """
        scale = _render_scale(matrix, dpi)
        rgba = _render_background(background, alpha=alpha)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        page_number = self._page_number()
        document = self._document
        document._ensure_fallback_fonts()
        result = document._doc.render_page_pixmap(page_number, scale, alpha, rgba, clip_rect)
        document._emit_warnings()
        return result

    def get_svg_image(
        self,
        *,
        matrix: Matrix | Sequence[float] | None = None,
        text_as_path: bool = True,
        max_size: int | None = _DEFAULT_MAX_SVG_SIZE,
    ) -> str:
        """Render the page to a bounded SVG string, as pymupdf does.

        ``matrix`` must be omitted or the identity, and ``text_as_path=False``
        raises :class:`PdfError`, because text is always drawn as paths.
        ``max_size`` caps UTF-8 output and defaults to 64 MiB; ``None``
        explicitly opts out. hayro-svg materializes its internal Rust string
        before this boundary is enforced, but over-limit output is rejected
        before conversion to a Python string.
        """
        if _render_scale(matrix, None) != (1.0, 1.0):
            msg = f"get_svg_image supports only the identity matrix: {matrix!r}"
            raise PdfError(msg)
        if not text_as_path:
            msg = "text_as_path=False is not supported; SVG text is drawn as paths"
            raise PdfError(msg)
        _validate_optional_positive_int("max_size", max_size)
        page_number = self._page_number()
        document = self._document
        document._ensure_fallback_fonts()
        result = document._doc.render_page_svg(page_number, max_size)
        document._emit_warnings()
        return result

    def __repr__(self) -> str:
        """Return a representation containing the page number and document."""
        return f"<Page {self._pno} of {self._document!r}>"


# The package re-exports this class; reprs, pickles, and documentation name it.
Page.__module__ = "pylopdf"
