"""pymupdf's parameter names and keyword-only options."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf
from test_tables import _bordered_table_pdf

import pylopdf


def test_search_takes_the_text_keyword() -> None:
    page = pylopdf.open(build_pdf(["Hello world"])).pages[0]

    assert page.search(text="world") == page.search("world")
    assert len(page.search("world")) == 1
    with pytest.raises(TypeError, match="needle"):
        page.search(needle="world")  # ty: ignore[no-matching-overload]
    with pytest.raises(ValueError, match="text must be at least 1 character"):
        page.search("")


def test_tables_takes_keyword_only_strategy_with_lines_default() -> None:
    page = pylopdf.open(_bordered_table_pdf()).pages[0]

    default = page.tables()

    assert default == page.tables(strategy="lines")
    assert default[0].diagnostics.strategy == "lines"
    with pytest.raises(TypeError):
        page.tables("lines")  # ty: ignore[too-many-positional-arguments]
    with pytest.raises(ValueError, match="strategy"):
        page.tables(strategy=None)  # ty: ignore[invalid-argument-type]
    assert len(page.tables(clip=(0, 0, 340, 300), strategy="lines")) == 1


def test_pixmap_save_takes_its_path_positionally(tmp_path: Path) -> None:
    pixmap = pylopdf.open(build_pdf(["Pixels"])).pages[0].pixmap(matrix=pylopdf.Matrix(0.25, 0.25), alpha=True)
    target = tmp_path / "page.png"

    pixmap.save(target)

    assert target.read_bytes() == pixmap.to_bytes()
    with pytest.raises(TypeError):
        pixmap.save(filename=tmp_path / "other.png")  # ty: ignore[unknown-argument, missing-argument]
    with pytest.raises(TypeError):
        pixmap.save(path=tmp_path / "other.png")  # ty: ignore[positional-only-parameter-as-kwarg]
    assert not (tmp_path / "other.png").exists()
