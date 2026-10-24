"""Transactions group edits into atomic steps that ``doc.history`` undoes and redoes."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf, draw_text

import pylopdf

ENCRYPTED = Path(__file__).parent / "assets" / "encrypted"


def _texts(doc: pylopdf.Document) -> list[str]:
    return [page.text().strip() for page in doc.pages]


def _two_steps() -> pylopdf.Document:
    doc = pylopdf.open(build_pdf(["One", "Two"]))
    with doc.transaction("title"):
        doc.metadata["title"] = "Titled"
    with doc.transaction("delete"):
        del doc.pages[1]
    return doc


def test_a_transaction_records_one_step() -> None:
    doc = _two_steps()

    assert doc.history.steps == ("title", "delete")
    assert doc.history.position == 2
    assert doc.history.can_undo
    assert not doc.history.can_redo
    assert repr(doc.history) == "<History 2/2>"


def test_undo_and_redo_walk_the_steps() -> None:
    doc = _two_steps()

    doc.history.undo()
    assert _texts(doc) == ["One", "Two"]
    assert doc.metadata["title"] == "Titled"
    doc.history.undo()
    assert doc.metadata.get("title") is None
    assert doc.history.position == 0
    assert not doc.history.can_undo
    assert doc.history.can_redo

    doc.history.redo()
    assert doc.metadata["title"] == "Titled"
    assert _texts(doc) == ["One", "Two"]
    doc.history.redo()
    assert _texts(doc) == ["One"]
    assert doc.history.position == 2


def test_an_undone_state_saves_and_reopens() -> None:
    doc = _two_steps()
    doc.history.undo()

    with pylopdf.open(doc.to_bytes()) as reopened:
        assert _texts(reopened) == ["One", "Two"]
        assert reopened.metadata["title"] == "Titled"


def test_undoing_to_the_original_saves_incrementally_as_read() -> None:
    original = build_pdf(["One"])
    doc = pylopdf.open(original)
    with doc.transaction("draw"):
        draw_text(doc.pages[0], (72, 200), "Added")

    doc.history.undo()

    assert doc.to_bytes(incremental=True) == original


def test_a_failing_transaction_leaves_the_document_as_it_was() -> None:
    doc = pylopdf.open(build_pdf(["One", "Two"]))
    with doc.transaction("title"):
        doc.metadata["title"] = "Kept"
    page = doc.pages[0]

    def broken() -> None:
        with doc.transaction("broken"):
            doc.metadata["title"] = "Lost"
            del doc.pages[1]
            raise RuntimeError("stop")  # noqa: EM101 - the test's own exception.

    with pytest.raises(RuntimeError, match="stop"):
        broken()

    assert doc.metadata["title"] == "Kept"
    assert _texts(doc) == ["One", "Two"]
    assert doc.history.steps == ("title",)
    with pytest.raises(pylopdf.StalePageError):
        page.text()


def test_a_nested_transaction_restores_only_its_own_edits() -> None:
    doc = pylopdf.open(build_pdf(["One", "Two"]))

    def inner() -> None:
        with doc.transaction("inner"):
            del doc.pages[1]
            raise ValueError("inner")  # noqa: EM101 - the test's own exception.

    with doc.transaction("outer"):
        doc.metadata["title"] = "Outer"
        with pytest.raises(ValueError, match="inner"):
            inner()
        with doc.transaction("joined"):
            doc.metadata["subject"] = "Joined"

    assert _texts(doc) == ["One", "Two"]
    assert doc.history.steps == ("outer",)
    doc.history.undo()
    assert doc.metadata.get("title") is None
    assert doc.metadata.get("subject") is None


def test_a_transaction_without_edits_records_nothing() -> None:
    doc = pylopdf.open(build_pdf(["One"]))

    with doc.transaction("read"):
        doc.pages[0].text()

    assert doc.history.steps == ()


def test_an_edit_outside_a_transaction_ends_the_history() -> None:
    doc = _two_steps()

    doc.metadata["author"] = "Outside"

    assert doc.history.steps == ()
    assert not doc.history.can_undo
    with pytest.raises(pylopdf.PdfError, match="nothing to undo"):
        doc.history.undo()


def test_a_new_step_after_undo_drops_the_redo_steps() -> None:
    doc = _two_steps()
    doc.history.undo()

    with doc.transaction("subject"):
        doc.metadata["subject"] = "New"

    assert doc.history.steps == ("title", "subject")
    assert not doc.history.can_redo
    with pytest.raises(pylopdf.PdfError, match="nothing to redo"):
        doc.history.redo()


def test_saving_keeps_the_history(tmp_path: Path) -> None:
    doc = pylopdf.open(build_pdf(["One"]))
    with doc.transaction("draw"):
        draw_text(doc.pages[0], (72, 200), "Added")

    doc.to_bytes()
    doc.save(tmp_path / "out.pdf", renumber=True)

    assert doc.history.steps == ("draw",)
    doc.history.undo()
    assert "Added" not in doc.pages[0].text()


def test_undo_makes_pages_and_object_handles_stale() -> None:
    doc = _two_steps()
    page = doc.pages[0]
    catalog = doc.catalog

    doc.history.undo()

    with pytest.raises(pylopdf.StalePageError):
        page.text()
    with pytest.raises(pylopdf.StaleObjectError):
        catalog["Type"]


def test_the_budget_drops_the_oldest_steps() -> None:
    doc = pylopdf.open(build_pdf(["One"]))
    for index in range(4):
        with doc.transaction(f"step {index}"):
            doc.metadata["title"] = str(index)
    # Every step keeps a copy of about the same size.
    doc.history.max_size = 2 * doc._doc.take_snapshot().size + 64  # noqa: SLF001 - the size of one step.

    assert doc.history.steps == ("step 2", "step 3")
    doc.history.undo()
    doc.history.undo()
    assert doc.metadata["title"] == "1"
    assert not doc.history.can_undo


def test_a_step_larger_than_the_budget_is_not_kept() -> None:
    doc = pylopdf.open(build_pdf(["One"]))
    doc.history.max_size = 1

    with doc.transaction("big"):
        doc.metadata["title"] = "Big"

    assert doc.history.steps == ()
    assert doc.metadata["title"] == "Big"


def test_an_unbounded_history_keeps_every_step() -> None:
    doc = pylopdf.open(build_pdf(["One"]))
    doc.history.max_size = None
    for index in range(20):
        with doc.transaction(str(index)):
            doc.metadata["title"] = str(index)

    assert len(doc.history.steps) == 20


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "1"])
def test_the_budget_is_a_positive_integer_or_none(value: object) -> None:
    doc = pylopdf.open(build_pdf(["One"]))

    with pytest.raises(ValueError, match="max_size"):
        doc.history.max_size = value  # ty: ignore[invalid-assignment]


def test_clear_forgets_every_step() -> None:
    doc = _two_steps()

    doc.history.clear()

    assert doc.history.steps == ()
    assert _texts(doc) == ["One"]


def test_unlocking_an_unlocked_document_keeps_the_history() -> None:
    doc = pylopdf.open(ENCRYPTED / "user-aes-256.pdf")
    doc.unlock("userpw")
    with doc.transaction("title"):
        doc.metadata["title"] = "T"
    assert doc.history.steps == ("title",)

    doc.unlock("ownerpw")

    assert doc.history.steps == ("title",)


def test_a_locked_document_takes_no_transaction() -> None:
    doc = pylopdf.open(ENCRYPTED / "owneronly-aes-256.pdf")
    with doc.transaction("title"):
        doc.metadata["title"] = "T"
    locked = pylopdf.open(ENCRYPTED / "user-aes-256.pdf")

    with pytest.raises(pylopdf.EncryptedDocumentError):
        locked.transaction().__enter__()
    locked.unlock("userpw")

    assert locked.history.steps == ()


def test_the_name_must_be_text() -> None:
    doc = pylopdf.open(build_pdf(["One"]))

    with pytest.raises(TypeError, match="name must be a str"), doc.transaction(1):  # ty: ignore[invalid-argument-type]
        pass


def test_a_closed_document_has_no_history() -> None:
    doc = _two_steps()
    doc.close()

    with pytest.raises(pylopdf.DocumentClosedError):
        doc.history  # noqa: B018 - the property raises.


def test_reading_history_inside_a_transaction_keeps_previous_steps() -> None:
    doc = _two_steps()

    with doc.transaction("subject"):
        doc.metadata["subject"] = "New"
        assert doc.history.steps == ("title", "delete")
        assert doc.history.can_undo

    assert doc.history.steps == ("title", "delete", "subject")
    doc.history.undo()
    doc.history.undo()
    assert len(doc.pages) == 2


def test_a_failed_transaction_restores_the_clean_state() -> None:
    doc = pylopdf.open(build_pdf(["One"]))
    assert not doc.is_dirty

    def abort() -> None:
        with doc.transaction():
            doc.metadata["title"] = "Discarded"
            raise ValueError("abort")  # noqa: EM101 - the test's own exception.

    with pytest.raises(ValueError, match="abort"):
        abort()

    assert not doc.is_dirty
    assert doc.metadata.get("title") is None
    assert doc.history.steps == ()


@pytest.mark.parametrize("operation", ["undo", "redo"])
def test_history_cannot_replace_the_graph_inside_a_transaction(operation: str) -> None:
    doc = _two_steps()
    if operation == "redo":
        doc.history.undo()
    steps, position = doc.history.steps, doc.history.position

    with doc.transaction("read"), pytest.raises(pylopdf.PdfError, match="inside a transaction"):
        getattr(doc.history, operation)()

    assert doc.history.steps == steps
    assert doc.history.position == position
