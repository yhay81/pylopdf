"""The text records of :meth:`Page.words` and :meth:`Page.layout`."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, TypeAlias, TypedDict

from pylopdf._content import Color, TextRenderMode
from pylopdf._geometry import Point, Quad, Rect
from pylopdf._limits import _validate_rect

if TYPE_CHECKING:
    from collections.abc import Sequence

    #: The core's layout tuples: a block is ``(bbox, lines)``; a line is
    #: ``(bbox, spans, words, direction, writing mode, quad)``; a span is
    #: ``(bbox, text, size, origin, font, flags, style, quad)``, whose style
    #: is ``(color, render mode, clipped, ascender, descender)``; a word is
    #: ``(bbox, text)``; and a quad is four points.
    _Box = tuple[float, float, float, float]
    _Pt = tuple[float, float]
    _CoreQuad = tuple[_Pt, _Pt, _Pt, _Pt]
    _CoreStyle = tuple[tuple[float, float, float, float] | None, int, bool, float, float]
    _CoreSpan = tuple[_Box, str, float, _Pt, str, int, _CoreStyle, _CoreQuad]
    _CoreLine = tuple[_Box, list[_CoreSpan], list[tuple[_Box, str]], _Pt, int, _CoreQuad]
    _CoreBlock = tuple[_Box, list[_CoreLine]]
    #: The core's layout with glyphs: as above, except that a span holds
    #: ``(text, chars)`` in place of its text, and a char is
    #: ``(text, bbox, origin, synthetic)``.
    _CoreChar = tuple[str, _Box, _Pt, bool]
    _CoreRawSpan = tuple[_Box, tuple[str, list[_CoreChar]], float, _Pt, str, int, _CoreStyle, _CoreQuad]
    _CoreRawLine = tuple[_Box, list[_CoreRawSpan], list[tuple[_Box, str]], _Pt, int, _CoreQuad]
    _CoreRawBlock = tuple[_Box, list[_CoreRawLine]]

Unmapped: TypeAlias = Literal["skip", "replacement", "glyph_id"]
"""What text extraction makes of a glyph without Unicode: ``"skip"`` leaves it
out with a :class:`PylopdfWarning`, ``"replacement"`` gives U+FFFD, and
``"glyph_id"`` gives the character whose code point is the glyph ID."""

_UNMAPPED_CODES = {"skip": 0, "replacement": 1, "glyph_id": 2}

# The core's text options: (clip, keep clipped glyphs, unmapped code, exact
# boxes, expand ligatures, normalize whitespace, dehyphenate).
_TextOptions: TypeAlias = tuple[tuple[float, float, float, float] | None, bool, int, bool, bool, bool, bool]


def text_options(  # noqa: PLR0913 - the shared options of the text views.
    *,
    clip: Sequence[float] | None,
    clipped: bool,
    ligatures: bool,
    whitespace: bool,
    dehyphenate: bool,
    unmapped: Unmapped,
    exact_bboxes: bool = False,
) -> _TextOptions | None:
    """Validate the options of the text views; ``None`` stands for the defaults."""
    for name, value in (
        ("clipped", clipped),
        ("ligatures", ligatures),
        ("whitespace", whitespace),
        ("dehyphenate", dehyphenate),
        ("exact_bboxes", exact_bboxes),
    ):
        if not isinstance(value, bool):
            msg = f"{name} must be True or False: {value!r}"
            raise TypeError(msg)
    code = _UNMAPPED_CODES.get(unmapped) if isinstance(unmapped, str) else None
    if code is None:
        msg = f"unmapped must be 'skip', 'replacement', or 'glyph_id': {unmapped!r}"
        raise ValueError(msg)
    box = None if clip is None else _validate_rect(clip, name="clip")
    options = (box, clipped, code, exact_bboxes, not ligatures, not whitespace, dehyphenate)
    return None if options == _DEFAULT_TEXT_OPTIONS else options


_DEFAULT_TEXT_OPTIONS: _TextOptions = (None, False, 0, False, False, False, False)

# The span flags that pymupdf reports and that the core computes.
_ITALIC = 2
_SERIF = 4
_MONOSPACED = 8
_BOLD = 16
# The character flags of pymupdf's spans that the core computes.
_FILLED = 16
_STROKED = 32
_CLIPPED = 64


@dataclass(frozen=True, slots=True, kw_only=True)
class Word:
    """One word of :meth:`Page.words`, in reading order.

    ``block`` and ``line`` are the indexes of the :class:`Block` of
    :meth:`Page.layout` that holds the word and of the :class:`Line` in it, and
    ``index`` is the word's position in that line.
    """

    text: str
    bbox: Rect
    block: int
    line: int
    index: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Char:
    """One glyph of a :class:`Span`, from :meth:`Page.layout` with ``chars=True``.

    ``text`` is the glyph's complete Unicode mapping, which can hold several
    characters, such as the two of an ``fi`` ligature. ``origin`` is its point
    on the baseline; ``bbox`` spans the glyph's advance and the font's
    ascender and descender, or its outline with ``exact_bboxes=True``.
    """

    text: str
    bbox: Rect
    origin: Point
    is_synthetic: bool = False
    """Whether the char is a space that pylopdf inferred from the gap between
    two glyphs, as ``Span.text`` holds it, rather than a glyph; its box spans
    the gap."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Span:
    """A run of text in one font, size, paint, and render mode within a :class:`Line`.

    ``origin`` is the baseline point of the first glyph. ``chars`` holds the
    glyphs when :meth:`Page.layout` was called with ``chars=True`` and is empty
    otherwise.
    """

    text: str
    bbox: Rect
    origin: Point
    size: float
    font: str
    is_bold: bool
    is_italic: bool
    is_serif: bool
    is_monospaced: bool
    color: Color | None
    """The paint of the glyphs as hayro's sRGB approximation, whose ``alpha``
    is the opacity: the fill of filled text and the stroke of text that is
    only stroked; ``None`` for a pattern paint and for invisible text."""
    render_mode: TextRenderMode
    """How the glyphs are painted. hayro paints the clipping modes 4 to 6 as
    their painting part, and text in mode 7 is not extracted."""
    is_clipped: bool
    """Whether the clip paths leave no part of the glyphs' boxes visible, so
    that a viewer shows none of them."""
    ascender: float
    """The font's ascender in em, as MuPDF reads it from the font program,
    or 0.8 when hayro hands out no program, as for Type 1 fonts."""
    descender: float
    """The font's descender in em, negative below the baseline, or -0.2
    without a font program."""
    chars: tuple[Char, ...]
    quad: Quad
    """The span's box along its baseline: the smallest rectangle in the
    direction of the first glyph's baseline that holds every glyph's box, so
    that it follows rotated text, where ``bbox`` is axis-aligned."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """A line of a :class:`Block`.

    ``text`` joins the line's words with single spaces. ``direction`` is the
    unit vector of the baseline in display space, ``(1, 0)`` for horizontal
    text, and ``is_vertical`` is true for vertical writing, in which glyphs
    stack downward.
    """

    text: str
    bbox: Rect
    direction: Point
    is_vertical: bool
    spans: tuple[Span, ...]
    quad: Quad
    """The line's box along its baseline, as :attr:`Span.quad` is."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Block:
    """A block of lines of a :class:`Layout`; ``text`` joins the lines with newlines."""

    text: str
    bbox: Rect
    lines: tuple[Line, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Layout:
    """The text of a page as blocks, lines, spans, and, on request, characters.

    ``width`` and ``height`` are those of :attr:`Page.rect`; every box is in
    display space.
    """

    width: float
    height: float
    blocks: tuple[Block, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the layout as dictionaries, lists, and tuples for JSON.

        The shape is that of pymupdf's ``get_text("dict")``: blocks with
        ``number``, ``type`` (0 for text), ``bbox``, and ``lines``; lines with
        ``bbox``, ``wmode``, ``dir``, and ``spans``; and spans with ``bbox``,
        ``origin``, ``size``, ``font``, ``flags``, ``char_flags``, ``color``,
        ``alpha``, ``ascender``, ``descender``, and ``text``. When the layout
        holds characters, each span has ``chars`` in place of ``text``, a list
        of ``c``, ``bbox``, ``origin``, and ``synthetic``, as in pymupdf's
        ``"rawdict"``.
        """
        return {
            "width": self.width,
            "height": self.height,
            "blocks": [
                {
                    "number": number,
                    "type": 0,
                    "bbox": tuple(block.bbox),
                    "lines": [
                        {
                            "bbox": tuple(line.bbox),
                            "wmode": int(line.is_vertical),
                            "dir": tuple(line.direction),
                            "spans": [_span_dict(span) for span in line.spans],
                        }
                        for line in block.lines
                    ],
                }
                for number, block in enumerate(self.blocks)
            ],
        }


def _span_dict(span: Span) -> dict[str, Any]:
    """Return one span of :meth:`Layout.to_dict`."""
    flags = (
        (_ITALIC if span.is_italic else 0)
        | (_SERIF if span.is_serif else 0)
        | (_MONOSPACED if span.is_monospaced else 0)
        | (_BOLD if span.is_bold else 0)
    )
    char_flags = (
        (_FILLED if span.render_mode in _FILLED_MODES else 0)
        | (_STROKED if span.render_mode in _STROKED_MODES else 0)
        | (_CLIPPED if span.is_clipped else 0)
    )
    color = span.color
    result: dict[str, Any] = {
        "bbox": tuple(span.bbox),
        "origin": tuple(span.origin),
        "size": span.size,
        "font": span.font,
        "flags": flags,
        "char_flags": char_flags,
        "color": 0 if color is None else _srgb(color.red) << 16 | _srgb(color.green) << 8 | _srgb(color.blue),
        "alpha": 255 if color is None else _srgb(color.alpha),
        "ascender": span.ascender,
        "descender": span.descender,
    }
    if not span.chars:
        result["text"] = span.text
    else:
        result["chars"] = [
            {"c": char.text, "bbox": tuple(char.bbox), "origin": tuple(char.origin), "synthetic": char.is_synthetic}
            for char in span.chars
        ]
    return result


_FILLED_MODES = frozenset({TextRenderMode.FILL, TextRenderMode.FILL_STROKE})
_STROKED_MODES = frozenset({TextRenderMode.STROKE, TextRenderMode.FILL_STROKE})
_RENDER_MODES = tuple(TextRenderMode)


def _srgb(component: float) -> int:
    """Return a color component from 0 to 1 as a byte."""
    return round(min(max(component, 0.0), 1.0) * 255)


def _rect(box: Sequence[float]) -> Rect:
    """Return a core box of four floats as a :class:`Rect`.

    A sum of floats is finite only when every term is, so a finite sum skips
    the constructor's checks, which cost more than the rest of a record; a
    non-finite one takes the checked path.
    """
    return _new_tuple(Rect, box) if _isfinite(box[0] + box[1] + box[2] + box[3]) else Rect(*box)


def _point(point: Sequence[float]) -> Point:
    """Return a core point of two floats as a :class:`Point`, as :func:`_rect` does."""
    return _new_tuple(Point, point) if _isfinite(point[0] + point[1]) else Point(*point)


def _char(text: str, bbox: Rect, origin: Point, is_synthetic: bool) -> Char:  # noqa: FBT001 - one core tuple.
    """Return a :class:`Char` for every glyph of a layout with characters.

    The ``__init__`` of a frozen dataclass sets each field through
    ``object.__setattr__``, which costs as much as the glyph's two geometry
    values together; the slot descriptors set the same fields directly.
    """
    char = _new_object(Char)
    _set_char_text(char, text)
    _set_char_bbox(char, bbox)
    _set_char_origin(char, origin)
    _set_char_synthetic(char, is_synthetic)
    return char


_isfinite = math.isfinite
_new_tuple = tuple.__new__
_new_object = object.__new__
_set_char_text = Char.text.__set__
_set_char_bbox = Char.bbox.__set__
_set_char_origin = Char.origin.__set__
_set_char_synthetic = Char.is_synthetic.__set__


def _quad(quad: Sequence[Sequence[float]]) -> Quad:
    """Return a core quad of four points as a :class:`Quad`, as :func:`_rect` does."""
    ul, ur, ll, lr = quad
    if _isfinite(ul[0] + ul[1] + ur[0] + ur[1] + ll[0] + ll[1] + lr[0] + lr[1]):
        return _new_tuple(
            Quad, (_new_tuple(Point, ul), _new_tuple(Point, ur), _new_tuple(Point, ll), _new_tuple(Point, lr))
        )
    return Quad(ul, ur, ll, lr)


def build_words(blocks: Sequence[_CoreBlock]) -> list[Word]:
    """Build :meth:`Page.words` from the core's layout."""
    return [
        Word(text=text, bbox=_rect(bbox), block=block_index, line=line_index, index=index)
        for block_index, (_, lines) in enumerate(blocks)
        for line_index, (_, _, words, _, _, _) in enumerate(lines)
        for index, (bbox, text) in enumerate(words)
    ]


def build_layout(width: float, height: float, blocks: Sequence[_CoreBlock] | Sequence[_CoreRawBlock]) -> Layout:
    """Build :meth:`Page.layout` from the core's layout, with or without glyphs.

    Spans of one color share one :class:`Color`, which is immutable.
    """
    colors: dict[tuple[float, float, float, float], Color] = {}
    return Layout(
        width=width,
        height=height,
        blocks=tuple(_build_block(bbox, [_build_line(line, colors) for line in lines]) for bbox, lines in blocks),
    )


def _build_block(bbox: Sequence[float], lines: list[Line]) -> Block:
    """Build one :class:`Block` around its built lines."""
    return Block(text="\n".join(line.text for line in lines), bbox=_rect(bbox), lines=tuple(lines))


def _build_line(line: _CoreLine | _CoreRawLine, colors: dict[tuple[float, float, float, float], Color]) -> Line:
    """Build one :class:`Line`, with each span's glyphs when the core gave them."""
    bbox, spans, words, direction, writing_mode, quad = line
    return Line(
        text=" ".join(text for _, text in words),
        bbox=_rect(bbox),
        direction=_point(direction),
        is_vertical=writing_mode == 1,
        spans=tuple(_build_span(*span, colors) for span in spans),
        quad=_quad(quad),
    )


def _build_span(  # noqa: PLR0913, PLR0917 - unpacks one core span tuple.
    bbox: Sequence[float],
    content: str | tuple[str, list[_CoreChar]],
    size: float,
    origin: Sequence[float],
    font: str,
    flags: int,
    style: _CoreStyle,
    quad: _CoreQuad,
    colors: dict[tuple[float, float, float, float], Color],
) -> Span:
    """Build one :class:`Span`; ``content`` is its text, or its text and glyphs."""
    if isinstance(content, str):
        text, chars = content, ()
    else:
        text, glyphs = content
        chars = tuple(
            _char(char, _rect(char_bbox), _point(char_origin), synthetic)
            for char, char_bbox, char_origin, synthetic in glyphs
        )
    components, render_mode, is_clipped, ascender, descender = style
    if components is None:
        color = None
    elif (color := colors.get(components)) is None:
        color = colors[components] = Color(*components)
    return Span(
        text=text,
        bbox=_rect(bbox),
        origin=_point(origin),
        size=size,
        font=font,
        is_bold=bool(flags & _BOLD),
        is_italic=bool(flags & _ITALIC),
        is_serif=bool(flags & _SERIF),
        is_monospaced=bool(flags & _MONOSPACED),
        color=color,
        render_mode=_RENDER_MODES[render_mode],
        is_clipped=is_clipped,
        ascender=ascender,
        descender=descender,
        chars=chars,
        quad=_quad(quad),
    )


class TextWord(NamedTuple):
    """A word of the core's layout for Markdown and tables, which index it by position."""

    x0: float
    y0: float
    x1: float
    y1: float
    word: str
    block_no: int
    line_no: int
    word_no: int


class TextSpan(TypedDict):
    """One span of the dictionary layout that Markdown conversion reads."""

    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    size: float
    font: str
    flags: int
    text: str


class TextLine(TypedDict):
    """One line of the dictionary layout that Markdown conversion reads."""

    bbox: tuple[float, float, float, float]
    wmode: int
    dir: tuple[float, float]
    spans: list[TextSpan]


class TextBlock(TypedDict):
    """One block of the dictionary layout that Markdown conversion reads."""

    number: int
    type: int
    bbox: tuple[float, float, float, float]
    lines: list[TextLine]


class TextPageDict(TypedDict):
    """The dictionary layout that Markdown conversion reads."""

    width: float
    height: float
    blocks: list[TextBlock]


def text_words(blocks: Sequence[_CoreBlock]) -> list[TextWord]:
    """Return the words of the core's layout for Markdown and tables."""
    return [
        TextWord(x0, y0, x1, y1, text, block_index, line_index, index)
        for block_index, (_, lines) in enumerate(blocks)
        for line_index, (_, _, words, _, _, _) in enumerate(lines)
        for index, ((x0, y0, x1, y1), text) in enumerate(words)
    ]


def text_page_dict(width: float, height: float, blocks: Sequence[_CoreBlock]) -> TextPageDict:
    """Return the dictionary layout of the core's layout for Markdown conversion."""
    return {
        "width": width,
        "height": height,
        "blocks": [
            {
                "number": number,
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
                            for span_bbox, text, size, origin, font, flags, _, _ in spans
                        ],
                    }
                    for line_bbox, spans, _, direction, writing_mode, _ in lines
                ],
            }
            for number, (bbox, lines) in enumerate(blocks)
        ],
    }


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (Word, Char, Span, Line, Block, Layout):
    _public.__module__ = "pylopdf"
