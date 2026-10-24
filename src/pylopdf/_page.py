"""Page views and the page-label helpers."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal, overload

from pylopdf import _text
from pylopdf._annotations import Annotations
from pylopdf._boxes import PageBoxes
from pylopdf._canvas import Canvas
from pylopdf._content import Color, Content, _content_from_core
from pylopdf._errors import StalePageError
from pylopdf._geometry import Matrix, Quad, Rect
from pylopdf._hints import hinted_getattr
from pylopdf._images import Resources
from pylopdf._limits import (
    _DEFAULT_MAX_MARKDOWN_SIZE,
    _DEFAULT_MAX_SVG_SIZE,
    _bounded_utf8_size,
    _validate_cumulative_utf8_input,
    _validate_optional_positive_int,
    _validate_rect,
    _validate_utf8_text_input,
)
from pylopdf._links import Links
from pylopdf._objects import Dictionary
from pylopdf._ocr import OcrEngine, OcrWord, _default_ocr_engine, _validate_ocr_rotation
from pylopdf._operations import _DEFAULT_MAX_CONTENT_SIZE, InlineImage, Operation, Operations
from pylopdf._operations import _from_core as _operations_from_core
from pylopdf._page_labels import _label, _read_page_labels
from pylopdf._pixmap import Colorspace, _render_background, _render_gray, _render_matrix, _validate_bool
from pylopdf._redaction import RedactionReport
from pylopdf._redaction import clip_to_rect as _clip_to_rect
from pylopdf._redaction import redact as _redact
from pylopdf._tables import Table, build_tables
from pylopdf._widgets import Widgets
from pylopdf.pylopdf_core import LimitError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pylopdf._document import Document
    from pylopdf._geometry import Quad
    from pylopdf._text import Layout, Unmapped, Word
    from pylopdf._types import OcrRotation
    from pylopdf.pylopdf_core import Pixmap


_DEFAULT_MAX_TEXT_REPLACEMENT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_CONTENT_ELEMENTS = 262_144
_MAX_HIGHLIGHT_RECTS = 4096
_MAX_ANNOTATION_INPUT_TEXT_SIZE = 1024 * 1024
_RECT_COORDINATE_COUNT = 4
_MAX_SEARCH_INPUT_BYTES = 4096
_DEFAULT_MAX_SEARCH_HITS = 4096
_MAX_OCR_LAYER_WORDS = 4096
_MAX_OCR_LAYER_TEXT_BYTES = 1024 * 1024
#: The defaults of the recognition options of :meth:`Page.apply_ocr`, which ``words`` excludes.
_DEFAULT_OCR_RECOGNITION = (300, 1408, 192, 0.5)
_MAX_TEXT_REPLACEMENT_INPUT_BYTES = 4096


def _ocr_layer_words(words: Iterable[OcrWord]) -> list[OcrWord]:
    """Return the words with text of an OCR layer, stopping at the first word over the limits."""
    accepted: list[OcrWord] = []
    text_bytes = 0
    for word in words:
        if not isinstance(word, OcrWord):
            msg = f"words must be OcrWord records: {word!r}"
            raise TypeError(msg)
        _validate_rect(word.bbox, name="word bbox")
        if not isinstance(word.text, str):
            msg = f"word text must be a string: {word.text!r}"
            raise TypeError(msg)
        if not word.text:
            continue
        if len(accepted) >= _MAX_OCR_LAYER_WORDS:
            msg = f"words cannot contain more than {_MAX_OCR_LAYER_WORDS} entries with text"
            raise ValueError(msg)
        encoded_bytes = _bounded_utf8_size(word.text, _MAX_OCR_LAYER_TEXT_BYTES - text_bytes)
        if encoded_bytes is None:
            msg = f"word text cannot exceed {_MAX_OCR_LAYER_TEXT_BYTES} UTF-8 bytes per call"
            raise ValueError(msg)
        text_bytes += encoded_bytes
        accepted.append(word)
    return accepted


class Page:
    """A view of one page of a document, obtained through ``doc.pages[i]``.

    Adding, deleting, or reordering pages invalidates existing views. Using an
    invalidated view raises :class:`StalePageError`; fetch it again with
    ``doc.pages[i]``. Two views are equal when they are current views of the
    same page.
    """

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, document: Document, pno: int) -> None:
        """Initialize a view for :class:`Pages`; do not call directly."""
        self._document = document
        self._pno = pno
        self._generation = document._generation

    @property
    def index(self) -> int:
        """Return the zero-based index of the page in its document."""
        return self._pno

    @property
    def document(self) -> Document:
        """Return the document that holds the page."""
        return self._document

    def __eq__(self, other: object) -> bool:
        """Return whether ``other`` views the same page in the same document generation."""
        if not isinstance(other, Page):
            return NotImplemented
        return other._document is self._document and other._generation == self._generation and other._pno == self._pno

    def __hash__(self) -> int:
        """Hash the document, generation, and index that identify the view."""
        return hash((id(self._document), self._generation, self._pno))

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
                f"page {self._pno} was invalidated by a document structure change; "
                f"fetch it again via doc.pages[{self._pno}]"
            )
            raise StalePageError(msg)
        return self._pno + 1

    @property
    def rotation(self) -> int:
        """Return the page's ``/Rotate``, inherited and normalized to 0, 90, 180, or 270.

        Assigning a multiple of 90 rotates the page clockwise for display;
        another value raises ``ValueError``. Display space, ``rect``, and every
        position that pages read and take follow the rotation.
        """
        return self._document._doc.get_page_rotation(self._page_number())

    @rotation.setter
    def rotation(self, rotation: int) -> None:
        if isinstance(rotation, bool) or not isinstance(rotation, int):
            msg = f"rotation must be an int: {rotation!r}"
            raise TypeError(msg)
        if rotation % 90 != 0:
            msg = f"rotation must be a multiple of 90: {rotation!r}"
            raise ValueError(msg)
        self._document._doc.set_page_rotation(self._page_number(), rotation % 360)

    def remove_rotation(self) -> Matrix:
        """Set :attr:`rotation` to 0 while the page shows what it showed, and return the matrix used.

        The page's content is drawn under the returned :class:`Matrix`, which
        maps the old PDF user space to the new one, and the MediaBox,
        CropBox, BleedBox, TrimBox, and ArtBox, the annotations (their
        rectangles, points, and appearances, which turn with the page), the
        article beads and viewports, and every explicit destination of the
        page are mapped by it, so rendering, extraction, links, and the
        outline are unchanged. Annotations flagged NoRotate keep their
        upper-left corner and stay upright. A page without rotation is left as
        it is, and the identity is returned.
        """
        return Matrix(*self._document._doc.remove_page_rotation(self._page_number()))

    @property
    def boxes(self) -> PageBoxes:
        """Return the page's MediaBox, CropBox, BleedBox, TrimBox, and ArtBox in PDF user space."""
        self._page_number()
        return PageBoxes(self)

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

    @property
    def user_to_display(self) -> Matrix:
        """Return the matrix that maps PDF user space, such as a page box, to display space.

        It moves the visible area's top-left corner to the origin, turns y
        downward, and applies the rotation, so ``point * page.user_to_display``
        is where a user-space point shows, and ``~page.user_to_display`` maps
        back.
        """
        return Matrix(*self._document._doc.page_display_matrix(self._page_number()))

    def text(  # noqa: PLR0913 - the shared text options are keyword-only.
        self,
        *,
        clip: Sequence[float] | None = None,
        clipped: bool = False,
        ligatures: bool = True,
        whitespace: bool = True,
        dehyphenate: bool = False,
        unmapped: Unmapped = "skip",
    ) -> str:
        """Return the page's text in reading order.

        Reading order follows the geometry and script heuristics described in
        the documentation rather than the document's semantic order. Spaces
        between words are inferred from glyph positions, and lines end with a
        newline.

        ``clip`` keeps the glyphs whose box's center lies inside a
        display-space rectangle, in the page's reading order, which gives the
        text in a rectangle. Glyphs that the page boundary or the clip paths
        hide entirely are left out unless ``clipped=True``; invisible text,
        such as an OCR layer, is kept. ``ligatures=False`` expands the
        ligature characters U+FB00 to U+FB06 to their letters;
        ``whitespace=False`` makes every whitespace character a space;
        ``dehyphenate=True`` removes a hyphen that ends a line between two
        letters of a word that continues in lowercase on the next line of the
        block, and joins the two lines; and ``unmapped`` decides what a glyph
        without Unicode becomes (see :data:`Unmapped`).
        """
        options = _text.text_options(
            clip=clip,
            clipped=clipped,
            ligatures=ligatures,
            whitespace=whitespace,
            dehyphenate=dehyphenate,
            unmapped=unmapped,
        )
        self._page_number()
        return self._document._page_text(self._pno, options)

    def words(  # noqa: PLR0913 - the shared text options are keyword-only.
        self,
        *,
        clip: Sequence[float] | None = None,
        clipped: bool = False,
        ligatures: bool = True,
        whitespace: bool = True,
        dehyphenate: bool = False,
        unmapped: Unmapped = "skip",
        exact_bboxes: bool = False,
    ) -> list[Word]:
        """Return the page's words in reading order, as :class:`Word` records.

        Boxes are in display space and span the glyphs' advances and the
        font's ascender and descender, or their outlines with
        ``exact_bboxes=True``. The other options are those of :meth:`text`; a
        word that dehyphenation joins is one :class:`Word` on its first line
        whose box spans both parts.
        """
        options = _text.text_options(
            clip=clip,
            clipped=clipped,
            ligatures=ligatures,
            whitespace=whitespace,
            dehyphenate=dehyphenate,
            unmapped=unmapped,
            exact_bboxes=exact_bboxes,
        )
        width_height_blocks = self._document._page_layout(self._page_number(), options)
        return _text.build_words(width_height_blocks[2])

    def layout(  # noqa: PLR0913 - the shared text options are keyword-only.
        self,
        *,
        clip: Sequence[float] | None = None,
        chars: bool = False,
        clipped: bool = False,
        ligatures: bool = True,
        whitespace: bool = True,
        dehyphenate: bool = False,
        unmapped: Unmapped = "skip",
        exact_bboxes: bool = False,
        styles: bool = False,
    ) -> Layout:
        """Return the page's text as blocks, lines, and spans, in reading order.

        With ``chars=True``, every span also lists its characters as
        :class:`Char` records: each glyph keeps its complete Unicode mapping,
        ligatures included, and each space that pylopdf infers from a gap is a
        char with ``is_synthetic`` set, so that a span's chars join to its
        text. Boxes are in display space and span the glyphs' advances and the
        font's ascender and descender, or their outlines with
        ``exact_bboxes=True``. Dehyphenation removes the hyphen from the end
        of the line and keeps both lines. ``styles=True`` also reads the
        page's vector rules and sets :attr:`Span.is_underlined` and
        :attr:`Span.is_struck` where a horizontal rule underlines or strikes
        out horizontal text, as pymupdf's ``TEXT_COLLECT_STYLES`` does. The
        other options are those of :meth:`text`. :meth:`Layout.to_dict`
        serializes the result.
        """
        if not isinstance(chars, bool):
            msg = f"chars must be True or False: {chars!r}"
            raise TypeError(msg)
        options = _text.text_options(
            clip=clip,
            clipped=clipped,
            ligatures=ligatures,
            whitespace=whitespace,
            dehyphenate=dehyphenate,
            unmapped=unmapped,
            exact_bboxes=exact_bboxes,
            styles=styles,
        )
        page_number = self._page_number()
        if chars:
            return _text.build_layout(*self._document._page_raw_layout(page_number, options))
        return _text.build_layout(*self._document._page_layout(page_number, options))

    def selection(
        self,
        start: Sequence[float],
        end: Sequence[float],
        *,
        clip: Sequence[float] | None = None,
    ) -> str:
        """Return the text that a viewer selects by dragging from ``start`` to ``end``.

        The selection runs in reading order from the character nearest
        ``start`` to the one nearest ``end``, whichever comes first, with the
        first and last lines in part and the lines between them whole, as
        :meth:`text` assembles them, and without a final line break. Both
        points are in display space; ``clip`` limits the characters as
        :meth:`text` does. A page without text selects ``""``.
        """
        points = []
        for name, point in (("start", start), ("end", end)):
            try:
                x, y = (float(value) for value in point)
            except (TypeError, ValueError) as error:
                msg = f"{name} must be a point (x, y): {point!r}"
                raise ValueError(msg) from error
            if not (math.isfinite(x) and math.isfinite(y)):
                msg = f"{name} must be a finite point: {point!r}"
                raise ValueError(msg)
            points.append((x, y))
        options = _text.text_options(
            clip=clip,
            clipped=False,
            ligatures=True,
            whitespace=True,
            dehyphenate=False,
            unmapped="skip",
        )
        text = self._document._doc.select_text(self._page_number(), points[0], points[1], options)
        self._document._emit_warnings()
        return text

    def ocr(  # noqa: PLR0913 - OCR resource controls are keyword-only.
        self,
        *,
        engine: OcrEngine | None = None,
        dpi: float = 300,
        clip: Sequence[float] | None = None,
        rotation: OcrRotation = 0,
        tile_size: int = 1408,
        overlap: int = 192,
        min_confidence: float = 0.5,
    ) -> list[OcrWord]:
        """Recognize the rendered page's text without changing the document.

        The default engine is loaded lazily from ``pylopdf[ocr]`` and reused.
        Pass an explicit :class:`OcrEngine` to control the model set or its
        lifetime. ``clip`` recognizes one display-space region and returns
        boxes in full-page display space. ``rotation`` corrects sideways input
        clockwise without editing the page.
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
        words: Iterable[OcrWord] | None = None,
        engine: OcrEngine | None = None,
        dpi: float = 300,
        clip: Sequence[float] | None = None,
        rotation: OcrRotation = 0,
        skip_existing: bool = True,
        tile_size: int = 1408,
        overlap: int = 192,
        min_confidence: float = 0.5,
    ) -> list[OcrWord]:
        """Add an invisible, searchable text layer and return the words it holds.

        Without ``words``, the page is recognized as :meth:`ocr` recognizes it.
        ``words`` takes the :class:`OcrWord` records that another engine, such
        as Tesseract or a cloud service, recognized, with boxes in display
        space; the recognition options do not apply to them. The text is not
        drawn and appears only in extraction and search. An Identity-H
        reference font with ToUnicode is used without embedding font data, so
        any language, including CJK, adds almost no file size. One call takes
        at most 4,096 words with text and 1 MiB of UTF-8 text.

        By default, a page with extractable text is skipped, which makes
        repeated calls idempotent and avoids duplicate text layers; with
        ``clip``, only text that meets the region counts. Use
        ``skip_existing=False`` to add the layer anyway; existing text is kept.
        ``rotation`` is the clockwise correction that reads sideways input and
        orients the invisible baselines. An empty result changes nothing.
        """
        if not isinstance(skip_existing, bool):
            msg = f"skip_existing must be a bool: {skip_existing!r}"
            raise TypeError(msg)
        resolved_rotation = _validate_ocr_rotation(rotation)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        if words is not None and (
            engine is not None or (dpi, tile_size, overlap, min_confidence) != _DEFAULT_OCR_RECOGNITION
        ):
            msg = "engine, dpi, tile_size, overlap, and min_confidence apply to recognition, not to words"
            raise TypeError(msg)
        if skip_existing and self._has_text(clip_rect):
            return []
        if words is None:
            words = self.ocr(
                engine=engine,
                dpi=dpi,
                clip=clip_rect,
                rotation=resolved_rotation,
                tile_size=tile_size,
                overlap=overlap,
                min_confidence=min_confidence,
            )
        accepted = _ocr_layer_words(words)
        if accepted:
            payload = [(*word.bbox, word.text) for word in accepted]
            self._document._doc.insert_ocr_layer(self._page_number(), payload, resolved_rotation)
        return accepted

    def _has_text(self, clip: Sequence[float] | None) -> bool:
        """Return whether the page has extractable text, inside ``clip`` when given."""
        if clip is None:
            return bool(self.text().strip())
        return any(
            word.text.strip()
            and word.bbox.x0 < clip[2]
            and word.bbox.x1 > clip[0]
            and word.bbox.y0 < clip[3]
            and word.bbox.y1 > clip[1]
            for word in self.words()
        )

    def markdown(
        self,
        *,
        table_strategy: Literal["lines", "text"] | None = "lines",
        max_size: int | None = _DEFAULT_MAX_MARKDOWN_SIZE,
    ) -> str:
        """Convert this page to Markdown.

        This is the single-page form of :meth:`Document.markdown`; heading
        sizes are inferred from this page alone. Complete bordered tables are
        inserted by default; pass ``table_strategy="text"`` for conservative
        borderless candidates or ``None`` to disable table conversion.
        ``max_size`` caps UTF-8 output and defaults to 64 MiB; ``None``
        explicitly opts out.
        """
        self._page_number()
        return self._document.markdown(
            pages=[self._pno],
            table_strategy=table_strategy,
            max_size=max_size,
        )

    @overload
    def search(
        self,
        text: str,
        *,
        clip: Sequence[float] | None = ...,
        quads: Literal[False] = ...,
        max_hits: int | None = ...,
        clipped: bool = ...,
        ligatures: bool = ...,
        whitespace: bool = ...,
        dehyphenate: bool = ...,
        unmapped: Unmapped = ...,
        exact_bboxes: bool = ...,
    ) -> list[Rect]: ...

    @overload
    def search(
        self,
        text: str,
        *,
        clip: Sequence[float] | None = ...,
        quads: Literal[True],
        max_hits: int | None = ...,
        clipped: bool = ...,
        ligatures: bool = ...,
        whitespace: bool = ...,
        dehyphenate: bool = ...,
        unmapped: Unmapped = ...,
        exact_bboxes: bool = ...,
    ) -> list[Quad]: ...

    def search(  # noqa: PLR0913 - the shared text options are keyword-only.
        self,
        text: str,
        *,
        clip: Sequence[float] | None = None,
        quads: bool = False,
        max_hits: int | None = _DEFAULT_MAX_SEARCH_HITS,
        clipped: bool = False,
        ligatures: bool = True,
        whitespace: bool = True,
        dehyphenate: bool = False,
        unmapped: Unmapped = "skip",
        exact_bboxes: bool = False,
    ) -> list[Rect] | list[Quad]:
        """Search page text case-insensitively for ``text``.

        Return one :class:`Rect` per match, or with ``quads=True`` one
        :class:`Quad` per line of each match: a quad follows the baseline, so
        that it covers rotated text tightly where a rectangle is
        axis-aligned. Search is line-based and does not detect matches
        spanning lines, except that ``dehyphenate=True`` joins a hyphenated
        word's lines; a match across that join gives one rectangle that spans
        both parts, or one quad per line. ``text`` is limited to 4,096 UTF-8
        bytes. Results default to 4,096 matches; ``max_hits=None`` explicitly
        opts trusted workloads out. The other options are those of
        :meth:`words`.
        """
        if not text:
            msg = "text must be at least 1 character"
            raise ValueError(msg)
        if not isinstance(quads, bool):
            msg = f"quads must be True or False: {quads!r}"
            raise TypeError(msg)
        _validate_optional_positive_int("max_hits", max_hits)
        _validate_utf8_text_input(
            text,
            _MAX_SEARCH_INPUT_BYTES,
            limit_code="search_input_size",
            input_label="search text",
        )
        options = _text.text_options(
            clip=clip,
            clipped=clipped,
            ligatures=ligatures,
            whitespace=whitespace,
            dehyphenate=dehyphenate,
            unmapped=unmapped,
            exact_bboxes=exact_bboxes,
        )
        core = self._document._doc
        if quads:
            hits = core.search_page_quads(self._page_number(), text, max_hits, options)
            self._document._emit_warnings()
            return [_text._quad(hit) for hit in hits]
        boxes = core.search_page(self._page_number(), text, max_hits, options)
        self._document._emit_warnings()
        return [Rect(*hit) for hit in boxes]

    def tables(
        self,
        *,
        strategy: Literal["lines", "text"] = "lines",
        clip: Sequence[float] | None = None,
    ) -> list[Table]:
        """Find high-confidence tables from vector borders or aligned text.

        The ``"lines"`` strategy is deterministic and does not rasterize the
        page. It accepts stroked rules and thin filled rectangles, requires an
        outer grid with at least two rows and two columns, and reconstructs
        rectangular merged cells. Coarse grid spans are conservatively split
        when at least three evenly led text records occupy the same set of
        cross-axis cells.

        The ``"text"`` strategy detects borderless tables only when at least
        three consecutive rows have the same segment count, aligned column
        edges, compatible leading, and clear inter-column gaps. It may
        interpret aligned multicolumn prose as a table, so use it when the page
        region is known to contain tabular data.

        ``clip`` is an optional display-space rectangle. Only tables whose
        complete box lies inside it are returned; partial tables are not
        synthesized. Each table carries :class:`TableDiagnostics` with a
        ``confidence`` that ranks this deterministic detector's results and is
        not a statistical probability, and em-normalized alignment, gutter,
        and row-spacing evidence.
        """
        if strategy not in {"lines", "text"}:
            msg = f"strategy must be 'lines' or 'text': {strategy!r}"
            raise ValueError(msg)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        raw = self._document._doc.find_tables(self._page_number(), strategy, clip_rect)
        self._document._emit_warnings()
        return build_tables(raw, strategy)

    @property
    def resources(self) -> Resources:
        """Return what the page's resources reach; ``resources.images`` lists its image XObjects."""
        self._page_number()
        return Resources(self)

    def content(  # noqa: PLR0913 - the item kinds and the reading options are all keyword-only
        self,
        *,
        text_runs: bool = True,
        paths: bool = True,
        images: bool = True,
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

        ``text_runs=False``, ``paths=False``, and ``images=False`` leave out
        that kind of item without building it, which makes reading only the
        paths or only the images of a text-heavy page many times faster.
        ``clip`` keeps the items whose box meets a display-coordinate
        rectangle. ``annotations=False`` leaves out annotation appearances,
        which otherwise follow the page content. Items whose geometry or paint
        is not finite, which hayro cannot paint either, are left out.

        ``max_elements`` bounds the glyphs, path and clip segments, stroke
        dash lengths, and items of one interpretation, before ``clip``
        applies; above it the call raises :class:`LimitError` with code
        ``content_element_count``. ``None`` removes the bound for trusted
        input.
        """
        for name, value in (
            ("text_runs", text_runs),
            ("paths", paths),
            ("images", images),
            ("annotations", annotations),
        ):
            if not isinstance(value, bool):
                msg = f"{name} must be a bool: {value!r}"
                raise TypeError(msg)
        _validate_optional_positive_int("max_elements", max_elements)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        raw = self._document._doc.page_content(
            self._page_number(), (text_runs, paths, images), annotations, clip_rect, max_elements
        )
        self._document._emit_warnings()
        return _content_from_core(raw)

    def draw(self, *, underlay: bool = False) -> Canvas:
        """Return a :class:`Canvas` that draws on this page.

        Use it in a ``with`` block, which writes the drawing when it ends
        normally: above the page's content, or below it with ``underlay=True``.
        """
        return Canvas(self, underlay=underlay)

    @property
    def operations(self) -> Operations:
        """Return the page's content as :class:`Operations`, as hayro reads it.

        The content streams of ``/Contents`` are decoded and joined as the
        renderer joins them, each stream of an array followed by one space,
        and tokenized with hayro's parser, so the operations are the ones
        that rendering and extraction interpret. Assigning a sequence of
        :class:`Operation` and :class:`InlineImage` values writes them as one
        new content stream, Flate-compressed, that replaces ``/Contents``;
        operations read from a page or parsed keep their original bytes, so
        ``page.operations = page.operations`` keeps the content as hayro
        reads it. The page's resources are not changed: operations that name
        a font or XObject need the page's ``/Resources`` to hold it.

        Reading refuses more than 1,048,576 operations
        (``content_operation_count``), and reading or writing more than
        64 MiB of content raises ``content_stream_size`` or
        ``content_input_size``; use :meth:`Operations.parse` with
        ``max_operations=None`` on ``page.obj["Contents"].read()`` for larger
        trusted content.
        """
        return _operations_from_core(self._document._doc.page_operations(self._page_number()))

    @operations.setter
    def operations(self, operations: Iterable[Operation | InlineImage]) -> None:
        if not isinstance(operations, Operations):
            if isinstance(operations, (str, bytes)) or not isinstance(operations, Iterable):
                msg = f"assign a sequence of Operation and InlineImage values: {type(operations).__name__}"
                raise TypeError(msg)
            operations = Operations(operations)
        data = operations.to_bytes()
        if len(data) > _DEFAULT_MAX_CONTENT_SIZE:
            msg = f"page content of {len(data)} bytes exceeds the {_DEFAULT_MAX_CONTENT_SIZE}-byte limit"
            code = "content_input_size"
            raise LimitError(code, msg)
        self._document._doc.set_page_content(self._page_number(), data)

    def redact(
        self,
        areas: Sequence[float] | Quad | Iterable[Sequence[float] | Quad] | None = None,
        *,
        text: Literal["remove", "keep"] = "remove",
        images: Literal["pixels", "remove", "keep"] = "pixels",
        graphics: Literal["touched", "covered", "keep"] = "touched",
        fill: Color | None = Color(0.0, 0.0, 0.0),  # noqa: B008 - Color is immutable.
    ) -> RedactionReport:
        """Remove what the page's redaction annotations, or ``areas``, cover, and return a report.

        Without ``areas``, every :class:`Redaction` annotation of the page is
        applied with its own fill and overlay text, and removed. ``areas`` is
        a rectangle or quad, or a sequence of up to 4,096 of them, in display
        space, which are filled with ``fill`` (``None`` for no fill).

        - ``text="remove"`` removes every glyph whose box, from its advance
          and the font's ascent and descent, meets an area, in every font
          and writing mode and in every rendering mode, invisible OCR text
          included. The glyphs after it in the same string keep their
          positions through ``TJ`` adjustments, and ``/ActualText``,
          ``/Alt``, and ``/E`` of the marked content around a removed glyph
          go too.
        - ``images="pixels"`` blanks the covered pixels of every image that an
          area meets, in a new copy of the image that only that placement
          uses, with its masks merged into the copy's alpha; ``"remove"``
          removes such placements, inline images included.
        - ``graphics="touched"`` removes every path and shading that meets an
          area, and ``"covered"`` only those inside one. A path that also
          clips keeps clipping.
        - Every annotation and form widget that overlaps an area is removed,
          with its popup; a field goes with its last widget.

        A Form XObject placement that loses something is replaced by a copy,
        so other placements and other pages keep the original. Content after
        the point where the renderer stops reading a stream, which other
        readers may still draw, is dropped from every stream written again.
        After the edit, hayro interprets the page again; what it still
        paints or extracts inside an area is removed in another pass, and a
        page where that does not converge is refused with :class:`PdfError`,
        leaving the document unchanged, as every other refusal does.
        """
        return _redact(self, areas, text=text, images=images, graphics=graphics, fill=fill)

    def clip_to_rect(self, rect: Sequence[float]) -> RedactionReport:
        """Remove what lies outside ``rect``, in display space, and return a report.

        Glyphs that reach outside the rectangle go, the pixels of images
        outside it are blanked, paths and shadings that lie entirely outside
        it go, and annotations outside it go, all as :meth:`redact` removes
        them; the content is then clipped to the rectangle, so a path that
        crosses its edge shows only inside. The page boxes do not change.
        """
        return _clip_to_rect(self, rect)

    def clean_contents(self) -> None:
        """Write the page's content as one stream of normalized operations with only the resources it uses.

        The operations are those that the renderer reads, written from
        their values, so rendering and extraction do not change; comments,
        content after where the renderer stops reading, and ``Q`` operators
        that restore nothing go, and the states the content leaves saved are
        restored at its end. Resource entries that neither the content nor a
        Form XObject or annotation appearance that inherits the page's
        resources names are removed from a copy that the page owns.
        """
        self._document._doc.clean_page_contents(self._page_number())

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

    @property
    def label(self) -> str:
        """Return the page's label, such as ``"iv"`` or ``"A-2"``, or ``""`` without page labels."""
        return _label(_read_page_labels(self._document), self._page_number() - 1)

    @property
    def annotations(self) -> Annotations:
        """Return the page's annotations other than links and form widgets, as :class:`Annotation` handles."""
        return Annotations(self)

    @property
    def links(self) -> Links:
        """Return the page's link annotations as a :class:`Links` sequence of :class:`Link` handles."""
        return Links(self)

    @property
    def widgets(self) -> Widgets:
        """Return the page's form widgets as a :class:`Widgets` sequence of :class:`Widget` handles."""
        return Widgets(self)

    def pixmap(  # noqa: PLR0913 - rendering options are keyword-only.
        self,
        *,
        dpi: float | None = None,
        matrix: Matrix | Sequence[float] | None = None,
        clip: Sequence[float] | None = None,
        alpha: bool = False,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        annotations: bool = True,
        colorspace: Colorspace | Literal["gray", "rgb"] = Colorspace.RGB,
    ) -> Pixmap:
        """Render the page to a :class:`Pixmap`.

        The default is opaque RGB on white at 72 dpi. ``dpi`` sets the
        resolution, or ``matrix`` transforms the page, with the identity at
        72 dpi; pass one of them. Any invertible matrix renders: the pixmap
        covers the bounding box of the transformed page, with each side
        truncated to whole pixels, so a translation moves no pixels and
        ``Matrix(2, 2)`` renders exactly what ``dpi=144`` does.
        ``Matrix(90) * Matrix(2, 2)`` turns the page a quarter turn clockwise
        on the screen, as MuPDF does.
        ``alpha=True`` keeps transparency in a straight alpha channel.
        ``background`` is an RGB or RGBA tuple with components from 0 to 255
        painted beneath the page, which must be opaque without ``alpha``.
        ``annotations=False`` leaves out the appearances of annotations and
        form widgets. ``colorspace="gray"`` converts the rendered colors to
        gray with ITU-R BT.601 luma weights.

        ``clip`` is an optional display-coordinate rectangle. The bounding box
        of the clip under ``matrix`` is rounded outward to pixel boundaries
        and intersected with the page. A clip that does
        not intersect the page raises :class:`PdfError`. Only the clipped
        region is rasterized, but full-page render-size limits still apply:
        65,535 pixels per side and 64,000,000 pixels in all.

        The pixmap exposes its pixels through the buffer protocol, shaped
        ``(height, width, channels)``, so ``numpy.asarray(pix)`` reads them
        without a copy.
        """
        linear = _render_matrix(matrix, dpi)
        rgba = _render_background(background, alpha=alpha)
        _validate_bool("annotations", annotations)
        gray = _render_gray(colorspace)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        page_number = self._page_number()
        document = self._document
        document._ensure_fallback_fonts()
        result = document._doc.render_page_pixmap(page_number, linear, gray, alpha, rgba, clip_rect, annotations)
        document._emit_warnings()
        return result

    def svg(
        self,
        *,
        annotations: bool = True,
        max_size: int | None = _DEFAULT_MAX_SVG_SIZE,
    ) -> str:
        """Render the page to an SVG string at 72 dpi, with text drawn as paths.

        ``annotations=False`` leaves out the appearances of annotations and
        form widgets. ``max_size`` caps the UTF-8 output and defaults to
        64 MiB; ``None`` opts out. hayro-svg builds its whole string before
        the limit is checked, so the limit keeps an oversized result from
        becoming a Python string rather than bounding the converter's memory.
        """
        _validate_bool("annotations", annotations)
        _validate_optional_positive_int("max_size", max_size)
        page_number = self._page_number()
        document = self._document
        document._ensure_fallback_fonts()
        result = document._doc.render_page_svg(page_number, annotations, max_size)
        document._emit_warnings()
        return result

    def __repr__(self) -> str:
        """Return a representation containing the page number and document."""
        return f"<Page {self._pno} of {self._document!r}>"


# The package re-exports this class; reprs, pickles, and documentation name it.
Page.__module__ = "pylopdf"
