"""Build Markdown from ``get_text("dict")`` layout data.

Initial rules:

- Body size is the font size containing the most characters, rounded to 0.1 pt.
  Sufficiently larger sizes map in descending order to heading levels 1–4.
- Wrapped lines join without a space between CJK characters.
- Leading bullets such as ・, •, and ● plus ``1.``/``1)`` normalize to lists.
- Span flags derived from embedded-font weight and italic metadata become
  emphasis markers in body text. Heading text remains plain to avoid combining
  heading markers with emphasis. Standard 14 Type 1 fonts are excluded because
  hayro does not expose their metadata.
- Multicolumn text follows deterministic whitespace gutters.
- Tables and vertical-writing order are unsupported.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

#: Minimum size ratio relative to body text for a heading.
_HEADING_RATIO = 1.15
#: Maximum number of heading levels.
_MAX_HEADING_LEVELS = 4
#: Leading bullets recognized when followed by whitespace, including ASCII dashes.
_SPACED_BULLETS = "・•●○◦▪‣–—-*"
#: Leading bullets recognized without whitespace, common in CJK documents.
_TIGHT_BULLETS = "・•●○◦▪‣"
#: Numbered lists in ``1.`` or ``23)`` form.
_NUMBERED = re.compile(r"^(\d{1,3})[.)][ 　]+")


def _round_size(size: float) -> float:
    return round(size, 1)


def collect_sizes(layouts: list[dict[str, Any]]) -> Counter[float]:
    """Count characters by rounded font size across page dicts."""
    counter: Counter[float] = Counter()
    for layout in layouts:
        for block in layout["blocks"]:
            for line in block["lines"]:
                for span in line["spans"]:
                    counter[_round_size(span["size"])] += len(span["text"])
    return counter


def heading_levels(counter: Counter[float]) -> dict[float, int]:
    """Map heading sizes to levels 1–4, excluding body size and below."""
    if not counter:
        return {}
    body = counter.most_common(1)[0][0]
    bigger = sorted((size for size in counter if size > body * _HEADING_RATIO), reverse=True)
    return {size: min(rank + 1, _MAX_HEADING_LEVELS) for rank, size in enumerate(bigger)}


#: Unicode ranges that join without spaces: CJK punctuation, kana, unified and
#: compatibility ideographs, fullwidth forms, and halfwidth katakana.
_CJK_RANGES = ((0x3000, 0x30FF), (0x3400, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF))


def _is_cjk(ch: str) -> bool:
    """Return whether a character joins without a space in CJK text."""
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _join_lines(lines: list[str]) -> str:
    """Join paragraph lines with no CJK gap and one space otherwise."""
    out = lines[0]
    for line in lines[1:]:
        if out and line and _is_cjk(out[-1]) and _is_cjk(line[0]):
            out += line
        else:
            out += " " + line
    return out


#: pymupdf-compatible span flag bits: italic=2, serif=4, mono=8, bold=16.
_ITALIC = 2
_BOLD = 16


def _line_text(line: dict[str, Any]) -> str:
    return "".join(span["text"] for span in line["spans"]).strip()


def _span_markdown(span: dict[str, Any]) -> str:
    """Convert a span to emphasized Markdown, keeping outer whitespace outside."""
    text: str = span["text"]
    flags = int(span.get("flags", 0))
    bold = bool(flags & _BOLD)
    italic = bool(flags & _ITALIC)
    core = text.strip()
    if not core or not (bold or italic):
        return text
    marker = "***" if bold and italic else ("**" if bold else "*")
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{marker}{core}{marker}{trail}"


def _line_markdown(line: dict[str, Any]) -> str:
    """Build a body line with bold and italic markers."""
    return "".join(_span_markdown(span) for span in line["spans"]).strip()


def _line_size(line: dict[str, Any]) -> float:
    """Return the line's representative size by character count."""
    sizes: Counter[float] = Counter()
    for span in line["spans"]:
        sizes[_round_size(span["size"])] += len(span["text"])
    return sizes.most_common(1)[0][0] if sizes else 0.0


def _normalize_list_item(text: str) -> str | None:
    """Normalize a bullet or numbered line, returning ``None`` otherwise."""
    if text[:1] and text[0] in _SPACED_BULLETS and text[1:2] in (" ", "　"):
        return "- " + text[2:].lstrip(" 　")
    if text[:1] and text[0] in _TIGHT_BULLETS:
        return "- " + text[1:].lstrip(" 　")
    matched = _NUMBERED.match(text)
    if matched:
        return f"{matched.group(1)}. " + text[matched.end() :]
    return None


def page_to_markdown(layout: dict[str, Any], levels: dict[float, int]) -> str:
    """Convert one page's dict layout to Markdown."""
    # (kind, text): h=heading, li=list item, p=paragraph.
    entries: list[tuple[str, str]] = []
    for block in layout["blocks"]:
        paragraph: list[str] = []

        def flush(paragraph: list[str] = paragraph) -> None:
            if paragraph:
                entries.append(("p", _join_lines(paragraph)))
                paragraph.clear()

        for line in block["lines"]:
            text = _line_text(line)
            if not text:
                continue
            level = levels.get(_line_size(line))
            if level is not None:
                # Keep headings plain to avoid stacking # with emphasis markers.
                flush()
                entries.append(("h", "#" * level + " " + text))
                continue
            item = _normalize_list_item(text)
            if item is not None:
                flush()
                entries.append(("li", item))
                continue
            paragraph.append(_line_markdown(line))
        flush()

    # Keep consecutive list items in one list without blank lines.
    chunks: list[str] = []
    previous = ""
    for kind, text in entries:
        if kind == "li" and previous == "li":
            chunks[-1] += "\n" + text
        else:
            chunks.append(text)
        previous = kind
    return "\n\n".join(chunks)
