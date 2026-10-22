"""The text records of :meth:`Page.words` and :meth:`Page.layout`."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, TypedDict

from pylopdf._geometry import Point, Rect

if TYPE_CHECKING:
    from collections.abc import Sequence

    #: The core's layout tuples: a block is ``(bbox, lines)``; a line is
    #: ``(bbox, spans, words, direction, writing mode)``; a span is
    #: ``(bbox, text, size, origin, font, flags)``; and a word is ``(bbox, text)``.
    _Box = tuple[float, float, float, float]
    _CoreSpan = tuple[_Box, str, float, tuple[float, float], str, int]
    _CoreLine = tuple[_Box, list[_CoreSpan], list[tuple[_Box, str]], tuple[float, float], int]
    _CoreBlock = tuple[_Box, list[_CoreLine]]
    #: The core's layout with glyphs: as above, except that a span holds
    #: ``(text, chars)`` in place of its text, and a char is
    #: ``(text, bbox, origin)``.
    _CoreChar = tuple[str, _Box, tuple[float, float]]
    _CoreRawSpan = tuple[_Box, tuple[str, list[_CoreChar]], float, tuple[float, float], str, int]
    _CoreRawLine = tuple[_Box, list[_CoreRawSpan], list[tuple[_Box, str]], tuple[float, float], int]
    _CoreRawBlock = tuple[_Box, list[_CoreRawLine]]

# The span flags that pymupdf reports and that the core computes.
_ITALIC = 2
_SERIF = 4
_MONOSPACED = 8
_BOLD = 16


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
    on the baseline; ``bbox`` approximates the glyph from its advance and the
    font size rather than from its outline.
    """

    text: str
    bbox: Rect
    origin: Point


@dataclass(frozen=True, slots=True, kw_only=True)
class Span:
    """A run of text in one font and size within a :class:`Line`.

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
    chars: tuple[Char, ...]


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
        ``origin``, ``size``, ``font``, ``flags``, and ``text``. When the
        layout holds characters, each span has ``chars`` in place of ``text``,
        a list of ``c``, ``bbox``, and ``origin``, as in pymupdf's
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
    result: dict[str, Any] = {
        "bbox": tuple(span.bbox),
        "origin": tuple(span.origin),
        "size": span.size,
        "font": span.font,
        "flags": flags,
    }
    if not span.chars:
        result["text"] = span.text
    else:
        result["chars"] = [
            {"c": char.text, "bbox": tuple(char.bbox), "origin": tuple(char.origin)} for char in span.chars
        ]
    return result


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


def _char(text: str, bbox: Rect, origin: Point) -> Char:
    """Return a :class:`Char` for every glyph of a layout with characters.

    The ``__init__`` of a frozen dataclass sets each field through
    ``object.__setattr__``, which costs as much as the glyph's two geometry
    values together; the slot descriptors set the same fields directly.
    """
    char = _new_object(Char)
    _set_char_text(char, text)
    _set_char_bbox(char, bbox)
    _set_char_origin(char, origin)
    return char


_isfinite = math.isfinite
_new_tuple = tuple.__new__
_new_object = object.__new__
_set_char_text = Char.text.__set__
_set_char_bbox = Char.bbox.__set__
_set_char_origin = Char.origin.__set__


def build_words(blocks: Sequence[_CoreBlock]) -> list[Word]:
    """Build :meth:`Page.words` from the core's layout."""
    return [
        Word(text=text, bbox=_rect(bbox), block=block_index, line=line_index, index=index)
        for block_index, (_, lines) in enumerate(blocks)
        for line_index, (_, _, words, _, _) in enumerate(lines)
        for index, (bbox, text) in enumerate(words)
    ]


def build_layout(width: float, height: float, blocks: Sequence[_CoreBlock] | Sequence[_CoreRawBlock]) -> Layout:
    """Build :meth:`Page.layout` from the core's layout, with or without glyphs."""
    return Layout(
        width=width,
        height=height,
        blocks=tuple(_build_block(bbox, [_build_line(line) for line in lines]) for bbox, lines in blocks),
    )


def _build_block(bbox: Sequence[float], lines: list[Line]) -> Block:
    """Build one :class:`Block` around its built lines."""
    return Block(text="\n".join(line.text for line in lines), bbox=_rect(bbox), lines=tuple(lines))


def _build_line(line: _CoreLine | _CoreRawLine) -> Line:
    """Build one :class:`Line`, with each span's glyphs when the core gave them."""
    bbox, spans, words, direction, writing_mode = line
    return Line(
        text=" ".join(text for _, text in words),
        bbox=_rect(bbox),
        direction=_point(direction),
        is_vertical=writing_mode == 1,
        spans=tuple(_build_span(*span) for span in spans),
    )


def _build_span(  # noqa: PLR0913, PLR0917 - unpacks one core span tuple.
    bbox: Sequence[float],
    content: str | tuple[str, list[_CoreChar]],
    size: float,
    origin: Sequence[float],
    font: str,
    flags: int,
) -> Span:
    """Build one :class:`Span`; ``content`` is its text, or its text and glyphs."""
    if isinstance(content, str):
        text, chars = content, ()
    else:
        text, glyphs = content
        chars = tuple(_char(char, _rect(char_bbox), _point(char_origin)) for char, char_bbox, char_origin in glyphs)
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
        chars=chars,
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
        for line_index, (_, _, words, _, _) in enumerate(lines)
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
                            for span_bbox, text, size, origin, font, flags in spans
                        ],
                    }
                    for line_bbox, spans, _, direction, writing_mode in lines
                ],
            }
            for number, (bbox, lines) in enumerate(blocks)
        ],
    }


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (Word, Char, Span, Line, Block, Layout):
    _public.__module__ = "pylopdf"
