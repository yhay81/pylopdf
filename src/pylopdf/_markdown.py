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
- Conservative vertical CJK columns follow extracted top-to-bottom,
  right-to-left order. Ruby and mixed-orientation typography are not interpreted.
- Complete bordered tables become Markdown tables in page reading order.
  Conservative borderless detection remains opt-in.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pylopdf import TextLine, TextPage, TextSpan, WordEntry

_BBox = tuple[float, float, float, float]
_MarkdownTable = tuple[_BBox, str]
_AxisOrientation = Literal["right", "down", "left", "up"]

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


def _line_inside_bbox(line: TextLine, bbox: _BBox) -> bool:
    """Return whether a line center lies inside a display-coordinate bbox."""
    x0, y0, x1, y1 = line["bbox"]
    center_x = (x0 + x1) * 0.5
    center_y = (y0 + y1) * 0.5
    return bbox[0] <= center_x <= bbox[2] and bbox[1] <= center_y <= bbox[3]


def _word_inside_bbox(word: WordEntry, bbox: _BBox) -> bool:
    """Return whether a word center lies inside a display-coordinate bbox."""
    center_x = (word[0] + word[2]) * 0.5
    center_y = (word[1] + word[3]) * 0.5
    return bbox[0] <= center_x <= bbox[2] and bbox[1] <= center_y <= bbox[3]


def _line_orientation(line: TextLine) -> _AxisOrientation:
    """Return the nearest right-angle direction of one baseline."""
    x, y = line["dir"]
    if abs(x) >= abs(y):
        return "right" if x >= 0 else "left"
    return "down" if y >= 0 else "up"


def _dominant_orientation(lines: list[TextLine]) -> _AxisOrientation:
    """Return the character-weighted baseline direction of a line collection."""
    directions: Counter[_AxisOrientation] = Counter()
    for line in lines:
        directions[_line_orientation(line)] += max(1, len(_line_text(line)))
    return directions.most_common(1)[0][0] if directions else "right"


def table_orientation(layout: TextPage, bbox: _BBox) -> _AxisOrientation:
    """Return table text direction, falling back to the surrounding page."""
    lines = [line for block in layout["blocks"] for line in block["lines"]]
    inside = [line for line in lines if _line_inside_bbox(line, bbox)]
    return _dominant_orientation(inside or lines)


def table_to_markdown(
    source_rows: list[list[str | None]],
    *,
    fill_empty: bool = True,
    orientation: _AxisOrientation = "right",
) -> str:
    """Render table rows after normalizing their dominant text direction."""
    rows = [row.copy() for row in source_rows]
    if not rows:
        return ""

    if fill_empty:
        _fill_merged_cells(rows)
    rows = _orient_rows(rows, orientation)

    rendered = ["| " + " | ".join(_escape_table_cell(value) for value in rows[0]) + " |"]
    rendered.append("| " + " | ".join("---" for _ in rows[0]) + " |")
    rendered.extend("| " + " | ".join(_escape_table_cell(value) for value in row) + " |" for row in rows[1:])
    return "\n".join(rendered)


def _fill_merged_cells(rows: list[list[str | None]]) -> None:
    """Copy anchor text into covered merged-cell slots in place."""
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if value is not None:
                continue
            above = rows[row_index - 1][column_index] if row_index else None
            left = row[column_index - 1] if column_index else None
            row[column_index] = above if above is not None else left


def _orient_rows(
    rows: list[list[str | None]],
    orientation: _AxisOrientation,
) -> list[list[str | None]]:
    """Rotate display-coordinate rows into logical text reading order."""
    if orientation == "down":
        return [list(row) for row in zip(*rows, strict=True)][::-1]
    if orientation == "left":
        return [row[::-1] for row in rows[::-1]]
    if orientation == "up":
        return [list(row) for row in zip(*rows[::-1], strict=True)]
    return rows


def _escape_table_cell(value: str | None) -> str:
    """Escape one Markdown table cell."""
    if value is None:
        return ""
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def collect_sizes(
    layouts: list[TextPage],
    excluded_bboxes: list[list[_BBox]] | None = None,
) -> Counter[float]:
    """Count non-table characters by rounded font size across page dicts."""
    counter: Counter[float] = Counter()
    for page_index, layout in enumerate(layouts):
        bboxes = [] if excluded_bboxes is None else excluded_bboxes[page_index]
        for block in layout["blocks"]:
            for line in block["lines"]:
                if any(_line_inside_bbox(line, bbox) for bbox in bboxes):
                    continue
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


def _line_text(line: TextLine) -> str:
    return "".join(span["text"] for span in line["spans"]).strip()


def _span_markdown(span: TextSpan) -> str:
    """Convert a span to emphasized Markdown, keeping outer whitespace outside."""
    text: str = span["text"]
    flags = span["flags"]
    bold = bool(flags & _BOLD)
    italic = bool(flags & _ITALIC)
    core = text.strip()
    if not core or not (bold or italic):
        return text
    marker = "***" if bold and italic else ("**" if bold else "*")
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{marker}{core}{marker}{trail}"


def _line_markdown(line: TextLine) -> str:
    """Build a body line with bold and italic markers."""
    return "".join(_span_markdown(span) for span in line["spans"]).strip()


def _line_size(line: TextLine) -> float:
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


def _table_events(
    layout: TextPage,
    tables: list[_MarkdownTable],
    words: list[WordEntry] | None,
) -> tuple[dict[int, list[int]], list[set[int]], dict[int, tuple[str, str]]]:
    """Place tables at their first contained line or nearest geometric slot."""
    indexed_lines = [
        ((block_index, line_index), line)
        for block_index, block in enumerate(layout["blocks"])
        for line_index, line in enumerate(block["lines"])
    ]
    lines = [line for _, line in indexed_lines]
    line_positions = {key: index for index, (key, _) in enumerate(indexed_lines)}
    line_words: dict[tuple[int, int], list[WordEntry]] = {}
    for word in [] if words is None else words:
        line_words.setdefault((word[5], word[6]), []).append(word)
    memberships: list[set[int]] = [set() for _ in lines]
    events: dict[int, list[int]] = {}
    page_orientation = _dominant_orientation(lines)
    for table_index, (bbox, _) in enumerate(tables):
        contained = _contained_line_positions(
            bbox,
            lines,
            line_positions,
            line_words,
            use_words=words is not None,
        )
        for index in contained:
            memberships[index].add(table_index)
        position = contained[0] if contained else _geometric_event_position(lines, bbox, page_orientation)
        events.setdefault(position, []).append(table_index)
    for table_indices in events.values():
        table_indices.sort(key=lambda index: (tables[index][0][1], tables[index][0][0]))
    residuals = _residual_line_texts(indexed_lines, line_words, memberships, tables)
    return events, memberships, residuals


def _contained_line_positions(
    bbox: _BBox,
    lines: list[TextLine],
    line_positions: dict[tuple[int, int], int],
    line_words: dict[tuple[int, int], list[WordEntry]],
    *,
    use_words: bool,
) -> list[int]:
    """Return layout positions containing text owned by one table."""
    if use_words:
        return sorted(
            {
                line_positions[key]
                for key, words in line_words.items()
                if key in line_positions and any(_word_inside_bbox(word, bbox) for word in words)
            }
        )
    return [index for index, line in enumerate(lines) if _line_inside_bbox(line, bbox)]


def _geometric_event_position(
    lines: list[TextLine],
    bbox: _BBox,
    orientation: _AxisOrientation,
) -> int:
    """Return the nearest reading-order insertion slot for a textless table."""
    inline = {
        "right": (1.0, 0.0),
        "down": (0.0, 1.0),
        "left": (-1.0, 0.0),
        "up": (0.0, -1.0),
    }[orientation]
    block = (-inline[1], inline[0])
    table_center = ((bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5)
    table_projection = table_center[0] * block[0] + table_center[1] * block[1]
    return next(
        (
            index
            for index, line in enumerate(lines)
            if (
                (line["bbox"][0] + line["bbox"][2]) * 0.5 * block[0]
                + (line["bbox"][1] + line["bbox"][3]) * 0.5 * block[1]
            )
            >= table_projection
        ),
        len(lines),
    )


def _join_words(words: list[str]) -> str:
    """Join positioned words without adding spaces between CJK runs."""
    if not words:
        return ""
    return _join_lines(words)


def _residual_line_texts(
    indexed_lines: list[tuple[tuple[int, int], TextLine]],
    line_words: dict[tuple[int, int], list[WordEntry]],
    memberships: list[set[int]],
    tables: list[_MarkdownTable],
) -> dict[int, tuple[str, str]]:
    """Keep words before and after tables when a line crosses a table edge."""
    residuals: dict[int, tuple[str, str]] = {}
    for position, (key, line) in enumerate(indexed_lines):
        if not memberships[position]:
            continue
        bboxes = [tables[index][0] for index in memberships[position]]
        direction = line["dir"]
        projections = [
            corner_x * direction[0] + corner_y * direction[1]
            for bbox in bboxes
            for corner_x in (bbox[0], bbox[2])
            for corner_y in (bbox[1], bbox[3])
        ]
        table_start = min(projections)
        before: list[str] = []
        after: list[str] = []
        for word in line_words.get(key, []):
            if any(_word_inside_bbox(word, bbox) for bbox in bboxes):
                continue
            center_x = (word[0] + word[2]) * 0.5
            center_y = (word[1] + word[3]) * 0.5
            target = before if center_x * direction[0] + center_y * direction[1] < table_start else after
            target.append(word[4])
        residuals[position] = (_join_words(before), _join_words(after))
    return residuals


def _flush_paragraph(entries: list[tuple[str, str]], paragraph: list[str]) -> None:
    """Append and clear a pending paragraph."""
    if paragraph:
        entries.append(("p", _join_lines(paragraph)))
        paragraph.clear()


def _append_tables(
    entries: list[tuple[str, str]],
    table_data: list[_MarkdownTable],
    table_indices: list[int],
) -> None:
    """Append non-empty table renderings for one reading-order event."""
    for table_index in table_indices:
        markdown = table_data[table_index][1]
        if markdown:
            entries.append(("table", markdown))


def _classify_line(
    line: TextLine,
    levels: dict[float, int],
    text_override: str | None = None,
) -> tuple[str, str]:
    """Classify a non-table line as heading, list item, paragraph, or empty."""
    text = _line_text(line) if text_override is None else text_override.strip()
    if not text:
        return "", ""
    level = levels.get(_line_size(line))
    if level is not None:
        # Keep headings plain to avoid stacking # with emphasis markers.
        return "h", "#" * level + " " + text
    item = _normalize_list_item(text)
    if item is not None:
        return "li", item
    return "p", _line_markdown(line) if text_override is None else text


def _join_entries(entries: list[tuple[str, str]]) -> str:
    """Join entries while keeping consecutive list items together."""
    chunks: list[str] = []
    previous = ""
    for kind, text in entries:
        if kind == "li" and previous == "li":
            chunks[-1] += "\n" + text
        else:
            chunks.append(text)
        previous = kind
    return "\n\n".join(chunks)


def _append_classified_line(
    entries: list[tuple[str, str]],
    paragraph: list[str],
    kind: str,
    text: str,
) -> None:
    """Append one classified line, retaining paragraph joining rules."""
    if kind == "p":
        paragraph.append(text)
    elif kind:
        _flush_paragraph(entries, paragraph)
        entries.append((kind, text))


def page_to_markdown(
    layout: TextPage,
    levels: dict[float, int],
    tables: list[_MarkdownTable] | None = None,
    words: list[WordEntry] | None = None,
) -> str:
    """Convert one page's dict layout and detected tables to Markdown."""
    table_data = [] if tables is None else tables
    table_events, table_memberships, residual_texts = _table_events(layout, table_data, words)
    # (kind, text): h=heading, li=list item, p=paragraph, table=Markdown table.
    entries: list[tuple[str, str]] = []
    line_position = 0
    for block in layout["blocks"]:
        paragraph: list[str] = []
        for line in block["lines"]:
            pending_tables = table_events.get(line_position, [])
            if table_memberships[line_position]:
                before, after = residual_texts.get(line_position, ("", ""))
                kind, text = _classify_line(line, levels, before)
                _append_classified_line(entries, paragraph, kind, text)
                if pending_tables:
                    _flush_paragraph(entries, paragraph)
                    _append_tables(entries, table_data, pending_tables)
                kind, text = _classify_line(line, levels, after)
                _append_classified_line(entries, paragraph, kind, text)
                line_position += 1
                continue
            if pending_tables:
                _flush_paragraph(entries, paragraph)
                _append_tables(entries, table_data, pending_tables)
            kind, text = _classify_line(line, levels)
            if not kind:
                line_position += 1
                continue
            _append_classified_line(entries, paragraph, kind, text)
            line_position += 1
        _flush_paragraph(entries, paragraph)
    _append_tables(entries, table_data, table_events.get(line_position, []))
    return _join_entries(entries)
