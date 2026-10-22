"""pymupdf's parameter names and keyword-only options."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf
from test_tables import _bordered_table_pdf

import pylopdf


def test_search_for_takes_pymupdf_text_keyword() -> None:
    page = pylopdf.open(stream=build_pdf(["Hello world"]))[0]

    assert page.search_for(text="world") == page.search_for("world")
    assert len(page.search_for(text="world")) == 1
    with pytest.raises(TypeError, match="needle"):
        page.search_for(needle="world")  # ty: ignore[unknown-argument, missing-argument]
    with pytest.raises(ValueError, match="text must be at least 1 character"):
        page.search_for(text="")


def test_find_tables_takes_keyword_only_strategy_with_none_default() -> None:
    page = pylopdf.open(stream=_bordered_table_pdf())[0]

    default = page.find_tables()
    explicit_none = page.find_tables(strategy=None)
    lines = page.find_tables(strategy="lines")

    assert default.strategy == explicit_none.strategy == lines.strategy == "lines"
    assert default[0].diagnostics.strategy == "lines"
    assert [table.extract() for table in default] == [table.extract() for table in lines]
    assert [table.extract() for table in explicit_none] == [table.extract() for table in lines]
    with pytest.raises(TypeError):
        page.find_tables("lines")  # ty: ignore[too-many-positional-arguments]
    clipped = page.find_tables(clip=(0, 0, 340, 300), strategy="lines")
    assert len(clipped) == 1


def test_show_pdf_page_takes_pymupdf_docsrc_keyword() -> None:
    source = pylopdf.open(stream=build_pdf(["Stamp"]))
    target = pylopdf.open(stream=build_pdf([""]))

    target[0].show_pdf_page((0, 0, 300, 200), docsrc=source)
    with pytest.raises(TypeError, match="src"):
        target[0].show_pdf_page((0, 0, 300, 200), src=source)  # ty: ignore[unknown-argument, missing-argument]

    reopened = pylopdf.open(stream=target.tobytes())
    assert "Stamp" in reopened[0].get_text()


def test_pixmap_save_takes_pymupdf_filename_keyword(tmp_path: Path) -> None:
    pixmap = pylopdf.open(stream=build_pdf(["Pixels"]))[0].get_pixmap(matrix=pylopdf.Matrix(0.25, 0.25), alpha=True)
    target = tmp_path / "page.png"

    pixmap.save(filename=target)

    assert target.read_bytes() == pixmap.tobytes()
    with pytest.raises(TypeError):
        pixmap.save(path=tmp_path / "other.png")  # ty: ignore[unknown-argument, missing-argument]
    assert not (tmp_path / "other.png").exists()


def test_new_page_takes_keyword_only_width_and_height() -> None:
    doc = pylopdf.open()

    page = doc.new_page(width=200, height=300)
    assert page.rect == pylopdf.Rect(0, 0, 200, 300)
    page = doc.new_page(0, width=100, height=150)
    assert page.number == 0
    assert page.rect == pylopdf.Rect(0, 0, 100, 150)
    with pytest.raises(TypeError):
        doc.new_page(0, 200, 300)  # ty: ignore[too-many-positional-arguments]
    assert doc.page_count == 2


@pytest.mark.parametrize("method", ["save", "tobytes"])
def test_save_takes_pymupdf_use_objstms_keyword(tmp_path: Path, method: str) -> None:
    doc = pylopdf.open(stream=build_pdf(["Compact"] * 3))

    if method == "save":
        target = tmp_path / "out.pdf"
        doc.save(target, use_objstms=True)
        data = target.read_bytes()
        with pytest.raises(TypeError, match="object_streams"):
            doc.save(tmp_path / "old.pdf", object_streams=True)  # ty: ignore[unknown-argument]
        assert not (tmp_path / "old.pdf").exists()
    else:
        data = doc.tobytes(use_objstms=True)
        with pytest.raises(TypeError, match="object_streams"):
            doc.tobytes(object_streams=True)  # ty: ignore[unknown-argument]

    assert b"/ObjStm" in data
    assert b"/ObjStm" not in doc.tobytes()
    reopened = pylopdf.open(stream=data)
    assert [page.get_text().strip() for page in reopened] == ["Compact"] * 3
    with pytest.raises(ValueError, match="use_objstms cannot both be specified"):
        doc.tobytes(encryption=pylopdf.PDF_ENCRYPT_AES_256, user_pw="secret", use_objstms=True)


def _word_left_edges(expandtabs: int | None) -> list[tuple[str, float]]:
    page = pylopdf.open().new_page(width=300, height=200)
    rect = (20, 20, 280, 180)
    if expandtabs is None:
        spare = page.insert_textbox(rect, "ab\tcd", fontsize=12)
    else:
        spare = page.insert_textbox(rect, "ab\tcd", fontsize=12, expandtabs=expandtabs)
    assert spare >= 0
    return [(word[4], round(word[0], 3)) for word in page.get_text("words")]


def test_insert_textbox_expands_tabs_to_one_column_by_default() -> None:
    default = _word_left_edges(None)

    assert default == _word_left_edges(1)
    assert default != _word_left_edges(8)
    assert [text for text, _ in default] == ["ab", "cd"]


def test_insert_textbox_text_is_positional_only() -> None:
    page = pylopdf.open().new_page()

    with pytest.raises(TypeError):
        page.insert_textbox((20, 20, 280, 180), text="x")  # ty: ignore[positional-only-parameter-as-kwarg]
    assert page.insert_textbox((20, 20, 280, 180), "x") > 0
