"""Rust バインディング pylopdf_core._Document の低レベル動作テスト。"""

from __future__ import annotations

from pylopdf.pylopdf_core import _Document


def test_new_document_is_empty() -> None:
    doc = _Document()
    assert doc.page_count() == 0
    assert doc.version() == "1.7"


def test_load_bytes_and_page_count(three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(three_page_pdf)
    assert doc.page_count() == 3


def test_save_bytes_roundtrip(three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(three_page_pdf)
    data = doc.save_bytes()
    assert data.startswith(b"%PDF-")
    reloaded = _Document.load_bytes(data)
    assert reloaded.page_count() == 3


def test_save_and_load_file(tmp_path, one_page_pdf: bytes) -> None:  # noqa: ANN001
    path = tmp_path / "out.pdf"
    doc = _Document.load_bytes(one_page_pdf)
    doc.save(str(path))
    reloaded = _Document.load(str(path))
    assert reloaded.page_count() == 1


def test_extract_text(three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(three_page_pdf)
    assert "Page one" in doc.extract_text([1])
    assert "Page three" in doc.extract_text([3])


def test_delete_pages(three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(three_page_pdf)
    doc.delete_pages([2])
    assert doc.page_count() == 2
    remaining = doc.extract_text([1, 2])
    assert "Page two" not in remaining


def test_metadata_set_and_get(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    assert doc.get_metadata() == {}
    doc.set_metadata("Title", "My Title")
    doc.set_metadata("Author", "Alice")
    assert doc.get_metadata() == {"Title": "My Title", "Author": "Alice"}
    # 空文字列で削除
    doc.set_metadata("Author", "")
    assert doc.get_metadata() == {"Title": "My Title"}


def test_metadata_unicode_roundtrip(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    doc.set_metadata("Title", "日本語のタイトル")
    # 保存 → 再読込しても UTF-16BE 経由で戻ること
    reloaded = _Document.load_bytes(doc.save_bytes())
    assert reloaded.get_metadata()["Title"] == "日本語のタイトル"


def test_metadata_pdfdocencoding(one_page_pdf: bytes) -> None:
    """BOM なしの PDF 文字列を PDFDocEncoding としてデコードする。"""
    raw = one_page_pdf.replace(
        b"trailer\n<< /Size 6 /Root 1 0 R >>",
        b"trailer\n<< /Size 6 /Root 1 0 R /Info << /Title <80> >> >>",
    )
    doc = _Document.load_bytes(raw)
    # PDFDocEncoding の 0x80 は bullet（U+2022）
    assert doc.get_metadata()["Title"] == "•"


def test_merge(one_page_pdf: bytes, three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    other = _Document.load_bytes(three_page_pdf)
    doc.merge(other)
    assert doc.page_count() == 4
    # merge 結果は保存 → 再読込しても壊れていないこと
    reloaded = _Document.load_bytes(doc.save_bytes())
    assert reloaded.page_count() == 4
    all_text = reloaded.extract_text([1, 2, 3, 4])
    for expected in ["Hello PDF", "Page one", "Page two", "Page three"]:
        assert expected in all_text


def test_merge_into_empty(three_page_pdf: bytes) -> None:
    doc = _Document()
    doc.merge(_Document.load_bytes(three_page_pdf))
    assert doc.page_count() == 3
    reloaded = _Document.load_bytes(doc.save_bytes())
    assert reloaded.page_count() == 3


def test_merge_empty_then_nonempty(three_page_pdf: bytes) -> None:
    """空文書の挿入後も max_id とページツリーが壊れない。"""
    doc = _Document()
    doc.merge(_Document())
    doc.merge(_Document.load_bytes(three_page_pdf))
    assert doc.page_count() == 3
    assert _Document.load_bytes(doc.save_bytes()).page_count() == 3
