"""Undo and redo for the edits that :meth:`Document.transaction` groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pylopdf.pylopdf_core import PdfError

if TYPE_CHECKING:
    from pylopdf._document import Document
    from pylopdf.pylopdf_core import _DocumentSnapshot

#: The memory budget of a new history: 256 MiB of retained snapshots.
DEFAULT_HISTORY_SIZE = 256 * 1024 * 1024


@dataclass(slots=True)
class _Step:
    """One recorded transaction: its name and the state that undoing or redoing it restores."""

    name: str
    snapshot: _DocumentSnapshot


class History:
    """The undo and redo steps of a document, one per :meth:`Document.transaction`.

    Only transactions record steps. Each step keeps a copy of the document
    as it was before (for undo) or after (for redo) the transaction, and
    the copies together stay within :attr:`max_size` bytes: when a new step
    would pass it, the oldest steps are dropped, and a step larger than the
    budget is not kept at all. An edit outside a transaction ends the
    history, since undoing would discard that edit too; saving does not,
    though save options such as ``prune`` change the document. Undo and redo
    replace the document's objects, so pages and other handles taken before
    raise :class:`StalePageError` or :class:`StaleObjectError` afterwards.
    Undo and redo are unavailable inside an active transaction.
    """

    __slots__ = ("_document", "_max_size", "_redo", "_revision", "_undo")

    def __init__(self, document: Document) -> None:
        """Start an empty history; use :attr:`Document.history`."""
        self._document = document
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        # The document revision that the recorded steps lead to.
        self._revision: int | None = None
        self._max_size: int | None = DEFAULT_HISTORY_SIZE

    def _sync(self) -> None:
        """Drop the steps when the document changed outside a transaction."""
        self._document._ensure_not_closed()
        if self._document._transaction_depth:
            return
        if self._revision is not None and self._revision != self._document._doc.edit_revision():
            self.clear()

    @property
    def can_undo(self) -> bool:
        """Return whether :meth:`undo` has a step to undo."""
        self._sync()
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """Return whether :meth:`redo` has a step to redo."""
        self._sync()
        return bool(self._redo)

    @property
    def steps(self) -> tuple[str, ...]:
        """Return the names of the recorded steps, oldest first, the undone ones included."""
        self._sync()
        return (*(step.name for step in self._undo), *(step.name for step in reversed(self._redo)))

    @property
    def position(self) -> int:
        """Return how many of :attr:`steps` are applied; the rest can be redone."""
        self._sync()
        return len(self._undo)

    @property
    def max_size(self) -> int | None:
        """Return the bytes that the steps' copies may hold, 256 MiB by default; ``None`` is unbounded.

        Assigning a smaller budget drops the oldest steps until the rest fit.
        """
        return self._max_size

    @max_size.setter
    def max_size(self, value: int | None) -> None:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            msg = f"max_size must be a positive integer or None: {value!r}"
            raise ValueError(msg)
        self._max_size = value
        self._fit()

    def undo(self) -> None:
        """Return the document to its state before the latest applied step."""
        self._ensure_outside_transaction()
        self._sync()
        if not self._undo:
            msg = "there is nothing to undo"
            raise PdfError(msg)
        step = self._undo.pop()
        self._redo.append(_Step(step.name, self._swap(step.snapshot)))
        self._fit()

    def redo(self) -> None:
        """Apply the latest undone step again."""
        self._ensure_outside_transaction()
        self._sync()
        if not self._redo:
            msg = "there is nothing to redo"
            raise PdfError(msg)
        step = self._redo.pop()
        self._undo.append(_Step(step.name, self._swap(step.snapshot)))
        self._fit()

    def _ensure_outside_transaction(self) -> None:
        """Keep undo and redo from replacing an active transaction's graph."""
        if self._document._transaction_depth:
            msg = "undo and redo are unavailable inside a transaction"
            raise PdfError(msg)

    def clear(self) -> None:
        """Forget every step and the memory that their copies hold."""
        self._undo.clear()
        self._redo.clear()
        self._revision = None

    def _swap(self, snapshot: _DocumentSnapshot) -> _DocumentSnapshot:
        """Restore ``snapshot`` and return a copy of the state it replaced."""
        document = self._document
        current = document._doc.take_snapshot()
        document._restore(snapshot)
        self._revision = document._doc.edit_revision()
        return current

    def _record(self, name: str, before: _DocumentSnapshot, revision: int) -> None:
        """Record a transaction that changed the document from ``before``, taken at ``revision``."""
        if self._revision is not None and self._revision != revision:
            # The document changed outside a transaction before this one began.
            self.clear()
        self._redo.clear()
        self._undo.append(_Step(name, before))
        self._revision = self._document._doc.edit_revision()
        self._fit()

    def _follow(self, before: int) -> None:
        """Keep the steps across a change of revision that is no edit, such as a save."""
        if self._revision == before:
            self._revision = self._document._doc.edit_revision()

    def _fit(self) -> None:
        """Drop the oldest undo steps, then the farthest redo steps, until the copies fit the budget."""
        if self._max_size is None:
            return
        total = sum(step.snapshot.size for step in (*self._undo, *self._redo))
        while total > self._max_size and (self._undo or self._redo):
            dropped = self._undo.pop(0) if self._undo else self._redo.pop(0)
            total -= dropped.snapshot.size
        if not (self._undo or self._redo):
            self._revision = None

    def __repr__(self) -> str:
        """Return ``<History position/steps>``."""
        return f"<History {self.position}/{len(self.steps)}>"


# The package re-exports History; reprs, pickles, and documentation name it.
History.__module__ = "pylopdf"
