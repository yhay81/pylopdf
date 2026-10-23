"""Resource budgets and the input validators that several modules share."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pylopdf.pylopdf_core import LimitError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from typing import NoReturn


_DEFAULT_MAX_RENDER_PAGE_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_MARKDOWN_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_SVG_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_FONT_INPUT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_GENERATED_TEXT_SIZE = 1024 * 1024
_DEFAULT_MAX_IMAGE_INPUT_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_IMAGE_PIXELS = 64_000_000
_MAX_GENERATED_TEXT_LINES = 4096
#: The characters that end a line for str.splitlines(), which also treats
#: "\r\n" as one line break.
_LINE_BREAKS = "\n\v\f\r\x1c\x1d\x1e\x85\u2028\u2029"
_UTF8_TWO_BYTE_MIN = 1 << 7
_UTF8_THREE_BYTE_MIN = 1 << 11
_UTF8_FOUR_BYTE_MIN = 1 << 16

#: Largest absolute value representable as a finite lopdf PDF real (f32).
_FLOAT32_MAX = float.fromhex("0x1.fffffep+127")
_UINT32_MAX = 0xFFFFFFFF


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


def _text_line_limit() -> LimitError:
    """Return the refusal of text input beyond the generated-line limit."""
    return LimitError(
        "text_line_count",
        f"text input exceeds the {_MAX_GENERATED_TEXT_LINES}-line safety limit",
    )


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


_UTF16_BMP_MAX = (1 << 16) - 1


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


# The package re-exports this class; reprs, pickles, and documentation name it.
DocumentLimits.__module__ = "pylopdf"
