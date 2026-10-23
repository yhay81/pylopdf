"""Page views and the page-label helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pylopdf import _text
from pylopdf._annotations import Annotations
from pylopdf._boxes import PageBoxes
from pylopdf._canvas import Canvas
from pylopdf._content import Content, _content_from_core
from pylopdf._errors import StalePageError
from pylopdf._geometry import Matrix, Rect
from pylopdf._hints import hinted_getattr
from pylopdf._images import Resources
from pylopdf._limits import (
    _DEFAULT_MAX_IMAGE_INPUT_SIZE,
    _DEFAULT_MAX_IMAGE_PIXELS,
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
from pylopdf._page_labels import _label, _read_page_labels
from pylopdf._pixmap import Colorspace, _render_background, _render_gray, _render_scale, _validate_bool
from pylopdf._tables import Table, build_tables
from pylopdf.pylopdf_core import LimitError, Pixmap

if TYPE_CHECKING:
    import os
    from collections.abc import Iterable, Sequence

    from pylopdf._document import Document
    from pylopdf._text import Layout, Word
    from pylopdf._types import OcrRotation


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


def _validate_image_rotation(rotate: int) -> int:
    """Validate and normalize clockwise image rotation."""
    if isinstance(rotate, bool) or not isinstance(rotate, int):
        msg = f"rotate must be a multiple of 90: {rotate!r}"
        raise TypeError(msg)
    if rotate % 90 != 0:
        msg = f"rotate must be a multiple of 90: {rotate!r}"
        raise ValueError(msg)
    return rotate % 360


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

    def text(self) -> str:
        """Return the page's text in reading order.

        Reading order follows the geometry and script heuristics described in
        the documentation rather than the document's semantic order. Spaces
        between words are inferred from glyph positions, and lines end with a
        newline.
        """
        self._page_number()
        return self._document._page_text(self._pno)

    def words(self) -> list[Word]:
        """Return the page's words in reading order, as :class:`Word` records.

        Boxes are in display space and come from glyph advances and the font
        size rather than from glyph outlines.
        """
        width_height_blocks = self._document._page_layout(self._page_number())
        return _text.build_words(width_height_blocks[2])

    def layout(self, *, chars: bool = False) -> Layout:
        """Return the page's text as blocks, lines, and spans, in reading order.

        With ``chars=True``, every span also lists its glyphs as :class:`Char`
        records: each keeps the glyph's complete Unicode mapping, ligatures
        included, and inferred spaces have none. Boxes are in display space and
        come from glyph advances and the font size rather than from glyph
        outlines. :meth:`Layout.to_dict` serializes the result.
        """
        if not isinstance(chars, bool):
            msg = f"chars must be True or False: {chars!r}"
            raise TypeError(msg)
        page_number = self._page_number()
        if chars:
            return _text.build_layout(*self._document._page_raw_layout(page_number))
        return _text.build_layout(*self._document._page_layout(page_number))

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

    def search(
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
        :meth:`replace_text`, :meth:`Document.set_form_field`, and saves
        that compress a stream end this sharing, so the next placement embeds
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
        src_number = docsrc.pages[pno]._page_number()
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

    def draw(self, *, underlay: bool = False) -> Canvas:
        """Return a :class:`Canvas` that draws on this page.

        Use it in a ``with`` block, which writes the drawing when it ends
        normally: above the page's content, or below it with ``underlay=True``.
        """
        return Canvas(self, underlay=underlay)

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
        resolution, or ``matrix`` scales the page with the identity at 72 dpi;
        pass one of them. Only scaling matrices are supported: rotation, shear,
        and flips raise :class:`PdfError`, and a translation moves no pixels.
        ``alpha=True`` keeps transparency in a straight alpha channel.
        ``background`` is an RGB or RGBA tuple with components from 0 to 255
        painted beneath the page, which must be opaque without ``alpha``.
        ``annotations=False`` leaves out the appearances of annotations and
        form widgets. ``colorspace="gray"`` converts the rendered colors to
        gray with ITU-R BT.601 luma weights.

        ``clip`` is an optional display-coordinate rectangle. It is intersected
        with the page and rounded outward to pixel boundaries. A clip that does
        not intersect the page raises :class:`PdfError`. Only the clipped
        region is rasterized, but full-page render-size limits still apply:
        65,535 pixels per side and 64,000,000 pixels in all.

        The pixmap exposes its pixels through the buffer protocol, shaped
        ``(height, width, channels)``, so ``numpy.asarray(pix)`` reads them
        without a copy.
        """
        scale = _render_scale(matrix, dpi)
        rgba = _render_background(background, alpha=alpha)
        _validate_bool("annotations", annotations)
        gray = _render_gray(colorspace)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        page_number = self._page_number()
        document = self._document
        document._ensure_fallback_fonts()
        result = document._doc.render_page_pixmap(page_number, scale, gray, alpha, rgba, clip_rect, annotations)
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
