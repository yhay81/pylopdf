"""Page labels: ``Document.page_labels`` ranges and ``Page.label``."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pylopdf._limits import _validate_cumulative_pdf_text_input, _validate_cumulative_utf8_input

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from pylopdf._document import Document

_MAX_PAGE_LABEL_RANGES = 4096
_MAX_PAGE_LABEL_TEXT_SIZE = 1024 * 1024


class PageLabelStyle(enum.StrEnum):
    """How a page-label range numbers its pages (``/S``, ISO 32000-2 Table 161)."""

    DECIMAL = "D"
    """1, 2, 3"""
    UPPER_ROMAN = "R"
    """I, II, III"""
    LOWER_ROMAN = "r"
    """i, ii, iii"""
    UPPER_LETTERS = "A"
    """A to Z, then AA to ZZ"""
    LOWER_LETTERS = "a"
    """a to z, then aa to zz"""


_PAGE_LABEL_STYLES = frozenset(style.value for style in PageLabelStyle)


@dataclass(frozen=True, slots=True, init=False)
class PageLabelRange:
    """The labels of the pages from ``start`` up to the next range.

    A page is labeled ``prefix`` followed by its number in ``style``,
    counting from ``first`` at ``start``; ``style=None`` labels it with the
    prefix alone.
    """

    start: int
    """The zero-based first page of the range."""
    style: PageLabelStyle | None
    prefix: str
    first: int
    """The number of the range's first page."""

    def __init__(
        self,
        start: int,
        style: PageLabelStyle | str | None = PageLabelStyle.DECIMAL,
        prefix: str = "",
        first: int = 1,
    ) -> None:
        """Validate the fields; ``style`` takes a member or its value, such as ``"r"``."""
        for name, value in (("start", start), ("first", first)):
            if isinstance(value, bool) or not isinstance(value, int):
                msg = f"{name} must be an int: {value!r}"
                raise TypeError(msg)
        if start < 0:
            msg = f"start must not be negative: {start}"
            raise ValueError(msg)
        if first < 1:
            msg = f"first must be at least 1: {first}"
            raise ValueError(msg)
        if style is not None:
            try:
                style = PageLabelStyle(style)
            except ValueError:
                msg = f"style must be a PageLabelStyle or None: {style!r}"
                raise ValueError(msg) from None
        if not isinstance(prefix, str):
            msg = f"prefix must be a str: {prefix!r}"
            raise TypeError(msg)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "style", style)
        object.__setattr__(self, "prefix", prefix)
        object.__setattr__(self, "first", first)

    def label(self, page: int) -> str:
        """Return the label of zero-based ``page``, which the range must cover."""
        number = page - self.start + self.first
        match self.style:
            case PageLabelStyle.DECIMAL:
                digits = str(number)
            case PageLabelStyle.UPPER_ROMAN:
                digits = _roman(number)
            case PageLabelStyle.LOWER_ROMAN:
                digits = _roman(number).lower()
            case PageLabelStyle.UPPER_LETTERS:
                digits = _letters(number)
            case PageLabelStyle.LOWER_LETTERS:
                digits = _letters(number).lower()
            case _:
                digits = ""
        return self.prefix + digits


def _roman(n: int) -> str:
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


def _letters(n: int) -> str:
    """Convert a positive integer to PDF's A..Z, AA..ZZ, AAA... form."""
    letter = chr(ord("A") + (n - 1) % 26)
    return letter * ((n - 1) // 26 + 1)


def _label(ranges: Sequence[PageLabelRange], page: int) -> str:
    """Return the label of zero-based ``page`` under sorted ``ranges``, or ``""``."""
    applicable = None
    for page_range in ranges:
        if page_range.start > page:
            break
        applicable = page_range
    return "" if applicable is None else applicable.label(page)


def _read_page_labels(document: Document) -> list[PageLabelRange]:
    """Return the document's page-label ranges in page order."""
    document._ensure_open()
    # An unsupported stored style keeps its prefix without adding a number.
    return [
        PageLabelRange(int(start), style if style in _PAGE_LABEL_STYLES else None, prefix or "", int(first))
        for start, style, prefix, first in document._doc.get_page_labels()
    ]


def _write_page_labels(document: Document, ranges: Iterable[PageLabelRange]) -> None:
    """Replace the page labels with ``ranges``; an empty iterable removes them."""
    document._ensure_open()
    items = []
    for page_range in ranges:
        if len(items) == _MAX_PAGE_LABEL_RANGES:
            msg = f"page labels cannot contain more than {_MAX_PAGE_LABEL_RANGES} ranges"
            raise ValueError(msg)
        if not isinstance(page_range, PageLabelRange):
            msg = f"page labels are PageLabelRange records: {type(page_range).__name__}"
            raise TypeError(msg)
        items.append(page_range)
    starts = [page_range.start for page_range in items]
    if len(set(starts)) != len(starts):
        msg = f"page-label ranges start on distinct pages: {sorted(starts)}"
        raise ValueError(msg)
    if items and min(starts) != 0:
        msg = "the first page-label range must start at page 0 (ISO 32000-2 §12.4.2)"
        raise ValueError(msg)
    items.sort(key=lambda page_range: page_range.start)
    texts = [text for page_range in items for text in (page_range.style or "", page_range.prefix)]
    _validate_cumulative_utf8_input(
        texts,
        _MAX_PAGE_LABEL_TEXT_SIZE,
        limit_code="page_label_input_size",
        input_label="page-label text input",
    )
    _validate_cumulative_pdf_text_input(
        texts,
        _MAX_PAGE_LABEL_TEXT_SIZE,
        limit_code="page_label_input_size",
        input_label="encoded page-label text input",
    )
    document._doc.set_page_labels(
        [
            (page_range.start, page_range.style and str(page_range.style), page_range.prefix or None, page_range.first)
            for page_range in items
        ]
    )


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (PageLabelStyle, PageLabelRange):
    _public.__module__ = "pylopdf"
