"""Detected tables and their Markdown conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NamedTuple, cast

from pylopdf import _markdown
from pylopdf._geometry import Rect
from pylopdf._hints import hinted_getattr
from pylopdf._limits import _DEFAULT_MAX_MARKDOWN_SIZE, _validate_optional_positive_int
from pylopdf.pylopdf_core import LimitError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pylopdf._page import Page
    from pylopdf._text import TextPageDict
    from pylopdf._text import TextWord as Word

    _Box = tuple[float, float, float, float]
    #: The core's table: ``(bbox, row count, column count, row-major cells,
    #: anchor of every slot, (confidence, alignment, gutter, row gap))``, where
    #: a cell is ``(bbox, text)`` or ``None`` for a slot that a merged cell covers.
    _CoreTable = tuple[
        _Box,
        int,
        int,
        list[tuple[_Box, str] | None],
        list[int],
        tuple[float, float | None, float | None, float | None],
    ]


def _markdown_output_limit_error(max_size: int) -> LimitError:
    """Build the public error for any bounded Markdown conversion path."""
    return LimitError(
        "markdown_output_size",
        f"Markdown output exceeds the {max_size}-byte UTF-8 limit",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TableDiagnostics:
    """Inspectable geometric evidence for one detected table.

    ``confidence`` is a deterministic ranking heuristic in the range 0–1, not
    a calibrated probability. The em-normalized metrics are present for the
    ``"text"`` strategy and ``None`` for vector grids.
    """

    strategy: Literal["lines", "text"]
    confidence: float
    alignment_error_em: float | None
    minimum_gutter_em: float | None
    row_gap_variation_em: float | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Table:
    """A table that :meth:`Page.tables` detected, in display space.

    ``rows`` holds the text of every cell and ``cells`` its box, both row by
    row. A merged cell keeps its text and its spanning box in its top-left
    slot; the slots it covers are ``None``.
    """

    bbox: Rect
    rows: tuple[tuple[str | None, ...], ...]
    cells: tuple[tuple[Rect | None, ...], ...]
    diagnostics: TableDiagnostics
    #: The row-major index of the top-left slot of the cell that covers each
    #: slot, from the detector; without it, Markdown repeats the text above or
    #: to the left of an empty slot.
    _anchors: tuple[int, ...] | None = field(default=None, repr=False, compare=False)

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def markdown(
        self,
        *,
        fill_empty: bool = True,
        max_size: int | None = _DEFAULT_MAX_MARKDOWN_SIZE,
    ) -> str:
        """Render the table as Markdown, treating the first row as the header.

        When ``fill_empty`` is true, the slots that a merged cell covers repeat
        its text, because Markdown has no row or column spans. ``max_size``
        caps UTF-8 output before escaped cell strings are allocated and defaults
        to 64 MiB; ``None`` explicitly opts out.
        """
        _validate_optional_positive_int("max_size", max_size)
        try:
            return _markdown.table_to_markdown(
                [list(row) for row in self.rows],
                fill_empty=fill_empty,
                cell_anchors=self._anchors,
                max_size=max_size,
            )
        except _markdown.MarkdownOutputLimitError:
            raise _markdown_output_limit_error(cast("int", max_size)) from None


def build_tables(raw: Sequence[_CoreTable], strategy: Literal["lines", "text"]) -> list[Table]:
    """Build :meth:`Page.tables` from the core's row-major tables."""
    tables = []
    for bbox, row_count, col_count, cells, anchors, (confidence, alignment, gutter, row_gap) in raw:
        boxes = [None if cell is None else Rect(*cell[0]) for cell in cells]
        texts = [None if cell is None else cell[1] for cell in cells]
        starts = range(0, row_count * col_count, col_count)
        tables.append(
            Table(
                bbox=Rect(*bbox),
                rows=tuple(tuple(texts[start : start + col_count]) for start in starts),
                cells=tuple(tuple(boxes[start : start + col_count]) for start in starts),
                diagnostics=TableDiagnostics(
                    strategy=strategy,
                    confidence=confidence,
                    alignment_error_em=alignment,
                    minimum_gutter_em=gutter,
                    row_gap_variation_em=row_gap,
                ),
                _anchors=tuple(anchors),
            )
        )
    return tables


def _table_overlap_ratio(left: Rect, right: Rect) -> float:
    """Return intersection area divided by the smaller table area."""
    width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
    smaller_area = min(left.width * left.height, right.width * right.height)
    return 0.0 if smaller_area <= 0.0 else width * height / smaller_area


_MARKDOWN_TABLE_OVERLAP_LIMIT = 0.5


def _markdown_page_tables(
    page: Page,
    strategy: Literal["lines", "text"],
) -> list[Table]:
    """Return bordered tables, optionally extended by non-overlapping text tables."""
    tables = page.tables(strategy="lines")
    if strategy == "text":
        for candidate in page.tables(strategy="text"):
            if all(
                _table_overlap_ratio(candidate.bbox, table.bbox) < _MARKDOWN_TABLE_OVERLAP_LIMIT for table in tables
            ):
                tables.append(candidate)
    tables.sort(key=lambda table: (table.bbox.y0, table.bbox.x0))
    return tables


def _markdown_table_output(
    layout: TextPageDict,
    tables: list[Table],
    *,
    max_size: int | None,
    remaining_size: int | None,
) -> tuple[list[tuple[float, float, float, float]], list[tuple[tuple[float, float, float, float], str]]]:
    """Render page tables without retaining strings beyond the remaining budget."""
    bboxes = [(table.bbox.x0, table.bbox.y0, table.bbox.x1, table.bbox.y1) for table in tables]
    rendered: list[tuple[tuple[float, float, float, float], str]] = []
    rendered_bytes = 0
    for bbox, table in zip(bboxes, tables, strict=True):
        table_budget = None if remaining_size is None else max(0, remaining_size - rendered_bytes)
        try:
            table_markdown = _markdown.table_to_markdown(
                [list(row) for row in table.rows],
                orientation=_markdown.table_orientation(layout, bbox),
                cell_anchors=table._anchors,
                max_size=table_budget,
            )
        except _markdown.MarkdownOutputLimitError:
            raise _markdown_output_limit_error(cast("int", max_size)) from None
        rendered_bytes += _markdown.utf8_size(table_markdown)
        rendered.append((bbox, table_markdown))
    return bboxes, rendered


class _MarkdownBudget(NamedTuple):
    """Public limit and remaining aggregate bytes for one page."""

    limit: int | None
    remaining: int | None


def _bounded_markdown_page_output(
    layout: TextPageDict,
    levels: dict[float, int],
    tables: list[tuple[tuple[float, float, float, float], str]],
    words: list[Word] | None,
    budget: _MarkdownBudget,
) -> str:
    """Render one page while translating the internal output-limit signal."""
    try:
        return _markdown.page_to_markdown(
            layout,
            levels,
            tables,
            words,
            max_size=None if budget.remaining is None else max(0, budget.remaining),
        )
    except _markdown.MarkdownOutputLimitError:
        raise _markdown_output_limit_error(cast("int", budget.limit)) from None


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (TableDiagnostics, Table):
    _public.__module__ = "pylopdf"
