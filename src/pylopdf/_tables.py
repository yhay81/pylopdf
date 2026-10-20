"""Detected tables and their Markdown conversion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple, cast, overload

from pylopdf import _markdown
from pylopdf._limits import _DEFAULT_MAX_MARKDOWN_SIZE, _validate_optional_positive_int
from pylopdf.pylopdf_core import LimitError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pylopdf._page import Page
    from pylopdf._types import Rect, TextPage, WordEntry


def _markdown_output_limit_error(max_size: int) -> LimitError:
    """Build the public error for any bounded Markdown conversion path."""
    return LimitError(
        "markdown_output_size",
        f"Markdown output exceeds the {max_size}-byte UTF-8 limit",
    )


class TableDiagnostics(NamedTuple):
    """Inspectable geometric evidence for one detected table.

    ``confidence`` is a deterministic ranking heuristic in the range 0–1, not
    a calibrated probability. The em-normalized metrics are present for the
    ``"text"`` strategy and ``None`` for vector-grid strategies.
    """

    strategy: Literal["lines", "text"]
    confidence: float
    alignment_error_em: float | None
    minimum_gutter_em: float | None
    row_gap_variation_em: float | None


class Table:
    """A detected table with row-major cells, owned text, and diagnostics.

    A merged cell occupies its top-left slot. Continuation slots are ``None``.
    """

    page: Page
    bbox: Rect
    row_count: int
    col_count: int
    cells: list[Rect | None]
    diagnostics: TableDiagnostics
    strategy: Literal["lines", "text"]
    confidence: float

    def __init__(  # noqa: PLR0913, PLR0917 - internal owned result constructor
        self,
        page: Page,
        bbox: Rect,
        row_count: int,
        col_count: int,
        cells: list[tuple[Rect, str] | None],
        cell_anchors: list[int],
        diagnostics: TableDiagnostics,
    ) -> None:
        """Build a table from the internal geometry detector."""
        self.page = page
        self.bbox = bbox
        self.row_count = row_count
        self.col_count = col_count
        self.cells = [cell[0] if cell is not None else None for cell in cells]
        self._values = [cell[1] if cell is not None else None for cell in cells]
        self._cell_anchors = cell_anchors
        self.diagnostics = diagnostics
        self.strategy = diagnostics.strategy
        self.confidence = diagnostics.confidence

    def extract(self) -> list[list[str | None]]:
        """Return copied cell text as rows.

        Embedded line breaks are preserved. Slots covered by a merged cell are
        ``None``.
        """
        return [
            self._values[offset : offset + self.col_count].copy()
            for offset in range(0, len(self._values), self.col_count)
        ]

    def to_markdown(
        self,
        *,
        fill_empty: bool = True,
        max_size: int | None = _DEFAULT_MAX_MARKDOWN_SIZE,
    ) -> str:
        """Render the table as Markdown, treating the first row as the header.

        When ``fill_empty`` is true, merged-cell continuation slots inherit the
        anchor value because Markdown has no row/column spans. ``max_size``
        caps UTF-8 output before escaped cell strings are allocated and defaults
        to 64 MiB; ``None`` explicitly opts out.
        """
        _validate_optional_positive_int("max_size", max_size)
        try:
            return _markdown.table_to_markdown(
                self.extract(),
                fill_empty=fill_empty,
                cell_anchors=self._cell_anchors,
                max_size=max_size,
            )
        except _markdown.MarkdownOutputLimitError:
            raise _markdown_output_limit_error(cast("int", max_size)) from None


class TableFinder:
    """Sequence-like result with the strategy and optional clip used."""

    page: Page
    tables: list[Table]
    cells: list[Rect | None]
    strategy: Literal["lines", "text"]
    clip: Rect | None

    def __init__(
        self,
        page: Page,
        tables: list[Table],
        strategy: Literal["lines", "text"],
        clip: Rect | None,
    ) -> None:
        """Store table results independently of the Rust interpretation cache."""
        self.page = page
        self.tables = tables
        self.cells = [cell for table in tables for cell in table.cells]
        self.strategy = strategy
        self.clip = clip

    def __len__(self) -> int:
        """Return the number of detected tables."""
        return len(self.tables)

    @overload
    def __getitem__(self, index: int) -> Table: ...
    @overload
    def __getitem__(self, index: slice) -> list[Table]: ...
    def __getitem__(self, index: int | slice) -> Table | list[Table]:
        """Return one table or a sliced list."""
        return self.tables[index]

    def __iter__(self) -> Iterator[Table]:
        """Iterate over detected tables in page order."""
        return iter(self.tables)


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
    tables = list(page.find_tables(strategy="lines"))
    if strategy == "text":
        for candidate in page.find_tables(strategy="text"):
            if all(
                _table_overlap_ratio(candidate.bbox, table.bbox) < _MARKDOWN_TABLE_OVERLAP_LIMIT for table in tables
            ):
                tables.append(candidate)
    tables.sort(key=lambda table: (table.bbox.y0, table.bbox.x0))
    return tables


def _markdown_table_output(
    layout: TextPage,
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
                table.extract(),
                orientation=_markdown.table_orientation(layout, bbox),
                cell_anchors=table._cell_anchors,
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
    layout: TextPage,
    levels: dict[float, int],
    tables: list[tuple[tuple[float, float, float, float], str]],
    words: list[WordEntry] | None,
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
for _public in (
    TableDiagnostics,
    Table,
    TableFinder,
):
    _public.__module__ = "pylopdf"
