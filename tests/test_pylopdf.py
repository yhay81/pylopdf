"""高レベル API pylopdf.Document の動作テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf


def test_open_from_stream(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    assert doc.page_count == 3
    assert len(doc) == 3


def test_open_from_file(tmp_path: Path, one_page_pdf: bytes) -> None:
    path = tmp_path / "sample.pdf"
    path.write_bytes(one_page_pdf)
    doc = pylopdf.Document(path)
    assert doc.page_count == 1


def test_open_alias(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    assert isinstance(doc, pylopdf.Document)
    assert doc.page_count == 1


def test_filename_and_stream_raises(one_page_pdf: bytes) -> None:
    with pytest.raises(ValueError, match="同時に指定"):
        pylopdf.Document("a.pdf", one_page_pdf)


def test_empty_document() -> None:
    doc = pylopdf.Document()
    assert doc.page_count == 0


def test_metadata_roundtrip(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.set_metadata({"title": "Report", "author": "Alice"})
    md = doc.metadata
    assert md["title"] == "Report"
    assert md["author"] == "Alice"
    assert md["subject"] == ""
    assert md["format"].startswith("PDF ")


def test_metadata_unknown_key_raises(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="不明なメタデータキー"):
        doc.set_metadata({"format": "PDF 2.0"})


def test_get_page_text(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    assert "Page two" in doc.get_page_text(1)


def test_get_page_text_out_of_range(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(IndexError):
        doc.get_page_text(1)


def test_delete_page(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.delete_page(0)
    assert doc.page_count == 2
    assert "Page one" not in doc.get_page_text(0)


def test_delete_pages(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.delete_pages([0, 2])
    assert doc.page_count == 1
    assert "Page two" in doc.get_page_text(0)


def test_empty_page_lists(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.delete_pages([])
    assert doc.page_count == 1
    doc.select([])
    assert doc.page_count == 0


def test_delete_page_out_of_range(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(IndexError, match="範囲外"):
        doc.delete_page(3)


def test_insert_pdf(tmp_path: Path, one_page_pdf: bytes, three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.insert_pdf(pylopdf.Document(stream=three_page_pdf))
    assert doc.page_count == 4

    out = tmp_path / "merged.pdf"
    doc.save(out)
    reopened = pylopdf.Document(out)
    assert reopened.page_count == 4
    assert "Page three" in reopened.get_page_text(3)


def test_split_workflow(three_page_pdf: bytes) -> None:
    # split: 元 PDF から特定ページだけの新 PDF を作る
    part = pylopdf.Document(stream=three_page_pdf)
    part.delete_pages([0, 1])
    assert part.page_count == 1
    assert "Page three" in part.get_page_text(0)


def test_select_reorder(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.select([2, 0])
    assert doc.page_count == 2
    assert "Page three" in doc.get_page_text(0)
    assert "Page one" in doc.get_page_text(1)
    # 保存 → 再読込しても構造が壊れていないこと
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.page_count == 2
    assert "Page three" in reloaded.get_page_text(0)


def test_select_duplicates_pages(three_page_pdf: bytes) -> None:
    """同一ページの重複指定は複製になる。"""
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.select([0, 0, 1])
    assert doc.page_count == 3
    assert "Page one" in doc.get_page_text(0)
    assert "Page one" in doc.get_page_text(1)
    assert "Page two" in doc.get_page_text(2)
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.page_count == 3
    assert "Page one" in reloaded.get_page_text(1)
    assert reloaded.render_page(0) == reloaded.render_page(1)


def test_select_out_of_range(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    with pytest.raises(IndexError):
        doc.select([0, 3])


def test_insert_self_raises(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="自分自身"):
        doc.insert_pdf(doc)


def test_tobytes(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    data = doc.tobytes()
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF-")


def test_exception_hierarchy(one_page_pdf: bytes) -> None:
    """新設の例外は既存の ValueError 捕捉と後方互換。"""
    assert issubclass(pylopdf.PdfError, ValueError)
    assert issubclass(pylopdf.PasswordError, pylopdf.PdfError)
    assert issubclass(pylopdf.DocumentClosedError, pylopdf.PdfError)
    assert issubclass(pylopdf.EncryptedDocumentError, pylopdf.PdfError)
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.close()
    with pytest.raises(pylopdf.DocumentClosedError):
        _ = doc.page_count


def test_broken_pdf_raises_pdf_error() -> None:
    with pytest.raises(pylopdf.PdfError):
        pylopdf.Document(stream=b"%PDF-1.4 broken garbage")


def test_peek_metadata_stream(three_page_pdf: bytes) -> None:
    meta = pylopdf.peek_metadata(stream=three_page_pdf)
    assert meta["page_count"] == 3
    assert meta["encrypted"] is False
    assert meta["format"] == "PDF 1.4"


def test_peek_metadata_requires_exactly_one_source(one_page_pdf: bytes) -> None:
    with pytest.raises(ValueError, match="どちらか一方"):
        pylopdf.peek_metadata()
    with pytest.raises(ValueError, match="どちらか一方"):
        pylopdf.peek_metadata("a.pdf", one_page_pdf)


def test_context_manager_closes(one_page_pdf: bytes) -> None:
    with pylopdf.Document(stream=one_page_pdf) as doc:
        assert doc.page_count == 1
    with pytest.raises(ValueError, match="document closed"):
        _ = doc.page_count


def test_empty_page_lists_reject_closed_document(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.close()
    with pytest.raises(ValueError, match="document closed"):
        doc.delete_pages([])
    with pytest.raises(ValueError, match="document closed"):
        doc.select([])


def test_closed_document_repr(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    assert repr(doc) == "<pylopdf.Document>"
    doc.close()
    assert repr(doc) == "<closed pylopdf.Document>"


def test_unicode_metadata(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc.set_metadata({"title": "日本語タイトル", "author": "山田 太郎"})
    reloaded = pylopdf.Document(stream=doc.tobytes())
    assert reloaded.metadata["title"] == "日本語タイトル"
    assert reloaded.metadata["author"] == "山田 太郎"


def _png_size(data: bytes) -> tuple[int, int]:
    """PNG の IHDR チャンクから (幅, 高さ) を読み取る。"""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def test_render_page_png(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    data = doc.render_page(0)
    width, height = _png_size(data)
    # fixture の MediaBox は 612x792（レターサイズ、72dpi 相当）
    assert (width, height) == (612, 792)


def test_render_page_png_scale(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    width, height = _png_size(doc.render_page(0, scale=2.0))
    assert (width, height) == (1224, 1584)


def test_render_page_reflects_edits(three_page_pdf: bytes) -> None:
    # 編集（ページ削除）後の状態がレンダリングに反映されること
    doc = pylopdf.Document(stream=three_page_pdf)
    doc.delete_pages([0, 1])
    assert doc.page_count == 1
    assert _png_size(doc.render_page(0))[0] == 612


def test_render_page_out_of_range(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(IndexError):
        doc.render_page(1)


@pytest.mark.parametrize("scale", [0.0, -1.0, float("nan"), float("inf")])
def test_render_page_invalid_scale(one_page_pdf: bytes, scale: float) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="scale"):
        doc.render_page(0, scale=scale)


def test_render_page_too_small_scale(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="scale"):
        doc.render_page(0, scale=0.0001)


@pytest.mark.parametrize(
    ("page_size", "message"),
    [((100_000, 100_000), "1辺65535"), ((9_000, 9_000), "64000000画素")],
)
def test_render_page_rejects_oversized_page(page_size: tuple[int, int], message: str) -> None:
    doc = pylopdf.Document(stream=build_pdf(["x"], page_size=page_size))
    with pytest.raises(ValueError, match=message):
        doc.render_page(0)


def test_render_page_svg(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    svg = doc.render_page_svg(0)
    assert svg.lstrip().startswith("<")
    assert "svg" in svg[:200]


def test_render_page_cache_reflects_edits(three_page_pdf: bytes) -> None:
    """レンダリングキャッシュ導入後も、編集が確実に反映される（決定性の確認込み）。"""
    doc = pylopdf.Document(stream=three_page_pdf)
    assert doc.render_page(0) == doc.render_page(0)  # 連続レンダリングは同一結果
    page_two = doc.render_page(1)
    doc.delete_page(0)
    # 削除後の 0 ページ目は、削除前の 1 ページ目と同じ描画結果になる
    assert doc.render_page(0) == page_two


def test_render_page_dpi(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    assert doc.render_page(0, dpi=144) == doc.render_page(0, scale=2.0)


def test_render_page_dpi_with_scale_raises(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="dpi"):
        doc.render_page(0, scale=2.0, dpi=144)


def test_render_page_background(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    transparent = doc.render_page(0)
    white = doc.render_page(0, background=(255, 255, 255))
    assert white.startswith(b"\x89PNG\r\n\x1a\n")
    assert white != transparent
    # RGB 指定と不透明 RGBA 指定は同じ結果になる
    assert white == doc.render_page(0, background=(255, 255, 255, 255))


@pytest.mark.parametrize("background", [(0, 0, 256), (0, 0, -1)])
def test_render_page_background_out_of_range(one_page_pdf: bytes, background: tuple[int, int, int]) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="background"):
        doc.render_page(0, background=background)


def test_render_page_background_wrong_length(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="background"):
        doc.render_page(0, background=(255, 255))  # type: ignore[arg-type]


def test_save_options_roundtrip(tmp_path: Path, three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    out = tmp_path / "optimized.pdf"
    doc.save(out, garbage=True, deflate=True, object_streams=True)
    reopened = pylopdf.Document(out)
    assert reopened.page_count == 3
    assert "Page two" in reopened.get_page_text(1)
    # object streams は PDF 1.5+ 形式なのでバージョンが引き上げられる
    assert reopened.metadata["format"] == "PDF 1.5"


def test_tobytes_object_streams_render_consistent(three_page_pdf: bytes) -> None:
    """object streams 保存でドキュメント状態が変わっても、レンダリングが壊れない。"""
    doc = pylopdf.Document(stream=three_page_pdf)
    before = doc.render_page(0)
    data = doc.tobytes(object_streams=True)
    assert doc.render_page(0) == before
    assert pylopdf.Document(stream=data).render_page(0) == before


def test_multi_document_merge() -> None:
    # 3 つの PDF を順に結合する
    merged = pylopdf.Document()
    for text in ["First", "Second", "Third"]:
        merged.insert_pdf(pylopdf.Document(stream=build_pdf([text])))
    assert merged.page_count == 3
    reloaded = pylopdf.Document(stream=merged.tobytes())
    assert "Second" in reloaded.get_page_text(1)


def test_inherited_page_parent_cycle_raises(one_page_pdf: bytes) -> None:
    """ページの Parent が循環する破損 PDF でも処理が停止しない。"""
    raw = one_page_pdf.replace(b"/Parent 2 0 R", b"/Parent 4 0 R")
    doc = pylopdf.Document(stream=raw)
    assert doc.page_count == 1
    with pytest.raises(ValueError, match="reference cycle"):
        doc.get_page_text(0)
