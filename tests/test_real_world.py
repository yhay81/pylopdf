"""実世界 PDF に対する回帰テスト。

tests/assets/real_world/ に同梱した「実際のツールチェーンが生成した PDF」全種に対して
open / metadata / テキスト抽出 / 編集 / 保存 / レンダリングを一括で回し、
lopdf / hayro の限界を早期発見する。各ファイルの出典・ライセンス・既知の限界は
同ディレクトリの README.md を参照。
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


@dataclass(frozen=True)
class Case:
    """1 ファイル分の期待値。"""

    name: str
    pages: int
    version: str
    #: 0 ページ目の抽出テキストに含まれるべき文字列（None = 既知の限界として別テストで追跡）
    snippet: str | None


CASES = [
    Case("f1040.pdf", pages=2, version="PDF 1.7", snippet="U.S. Individual Income Tax Return"),
    Case("pdf20-simple.pdf", pages=1, version="PDF 2.0", snippet="Hello World"),
    Case("usrguide.pdf", pages=27, version="PDF 1.5", snippet="for authors"),
    Case("bill-hr815.pdf", pages=110, version="PDF 1.5", snippet="One Hundred Eighteenth Congress"),
    Case("mhlw-doc.pdf", pages=2, version="PDF 1.7", snippet="裁判例"),
    Case("patent-us223898.pdf", pages=4, version="PDF 1.3", snippet="Electric-Lamp"),
    Case("wdl6812-manuscript.pdf", pages=2, version="PDF 1.4", snippet=None),
]

ALL = pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
WITH_TEXT = pytest.mark.parametrize("case", [c for c in CASES if c.snippet is not None], ids=lambda c: c.name)


@ALL
def test_open_from_path_and_stream(case: Case) -> None:
    path = ASSETS / case.name
    assert pylopdf.open(path).page_count == case.pages
    assert pylopdf.open(stream=path.read_bytes()).page_count == case.pages


@ALL
def test_metadata_format(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    assert doc.metadata["format"] == case.version


@ALL
def test_peek_metadata_matches_full_load(case: Case) -> None:
    """高速パス peek_metadata がフルロードと同じページ数を返す。"""
    meta = pylopdf.peek_metadata(ASSETS / case.name)
    assert meta["page_count"] == case.pages
    assert meta["encrypted"] is False


def test_max_decompressed_size_guards_against_bombs() -> None:
    """object stream を含む PDF は、極端に小さい展開上限だとロードを拒否する。"""
    path = ASSETS / "f1040.pdf"
    with pytest.raises(pylopdf.PdfError, match="limit"):
        pylopdf.open(path, max_decompressed_size=100)
    assert pylopdf.open(path, max_decompressed_size=50_000_000).page_count == 2


@pytest.mark.parametrize("filter_name", ["FlateDecode", "Fl"])
def test_max_decompressed_size_guards_page_content_streams(filter_name: str) -> None:
    """hayro が遅延展開するページ Contents にもロード時の上限を適用する。"""
    expanded = b" " * 200_000
    compressed = zlib.compress(expanded)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
            4: (
                f"<< /Length {len(compressed)} /Filter /{filter_name} >>\nstream\n".encode()
                + compressed
                + b"\nendstream"
            ),
        }
    )
    with pytest.raises(pylopdf.PdfError, match="100-byte limit"):
        pylopdf.open(stream=pdf, max_decompressed_size=100)
    doc = pylopdf.open(stream=pdf, max_decompressed_size=len(expanded))
    assert doc.get_page_text(0) == ""


@WITH_TEXT
def test_extract_text_page0(case: Case) -> None:
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    assert case.snippet in doc.get_page_text(0)


def test_pdf20_comment_streams_extract() -> None:
    """コメント + インデント入り content stream の抽出（lopdf#535 の回帰検知）。

    v0.7 で抽出を hayro エンジンへ置き換えたことで解消した。lopdf の
    extract_text には未修正のまま残っているが、pylopdf はもう影響を受けない。
    """
    doc = pylopdf.open(ASSETS / "pdf20-simple.pdf")
    assert "Hello World" in doc.get_page_text(0)


def test_f1040_metadata_title() -> None:
    doc = pylopdf.open(ASSETS / "f1040.pdf")
    assert doc.metadata["title"] == "2025 Form 1040"


def test_manuscript_scan_has_no_text_layer() -> None:
    """テキストレイヤーの無い純スキャン PDF は、抽出が空になるのが正しい挙動。"""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    assert doc.get_page_text(0).strip() == ""


@ALL
def test_select_first_page_and_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.select([0])
    assert doc.page_count == 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == 1


@ALL
def test_insert_subset_with_position_roundtrip(case: Case) -> None:
    """先頭 1 ページだけを先頭位置へ挿入し、prune 後も内容が壊れないこと。"""
    doc = pylopdf.open(ASSETS / case.name)
    src = pylopdf.open(ASSETS / case.name)
    doc.insert_pdf(src, from_page=0, to_page=0, start_at=0)
    assert doc.page_count == case.pages + 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages + 1
    # 挿入ページが参照する資産（フォント・画像）が prune で失われていないこと
    assert reopened.render_page(0).startswith(b"\x89PNG")


@ALL
def test_merge_self_and_roundtrip(case: Case) -> None:
    raw = (ASSETS / case.name).read_bytes()
    doc = pylopdf.open(stream=raw)
    doc.insert_pdf(pylopdf.open(stream=raw))
    assert doc.page_count == case.pages * 2
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages * 2


@ALL
def test_merge_into_empty_and_roundtrip(case: Case) -> None:
    """実世界 PDF を空文書へ挿入しても Catalog / Pages の ID が衝突しない。"""
    source = pylopdf.open(ASSETS / case.name)
    doc = pylopdf.Document()
    doc.insert_pdf(source)
    assert doc.page_count == case.pages
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages


@ALL
def test_delete_page_and_roundtrip(case: Case) -> None:
    if case.pages < 2:
        pytest.skip("1 ページ文書の全ページ削除は対象外")
    doc = pylopdf.open(ASSETS / case.name)
    doc.delete_page(0)
    assert doc.page_count == case.pages - 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages - 1


@ALL
def test_save_optimized_roundtrip(case: Case) -> None:
    """garbage + deflate + object_streams 保存後も開けて内容が保たれる。"""
    doc = pylopdf.open(ASSETS / case.name)
    data = doc.tobytes(garbage=True, deflate=True, object_streams=True)
    reopened = pylopdf.open(stream=data)
    assert reopened.page_count == case.pages


def test_object_streams_reduce_size() -> None:
    """object stream 保存が中規模文書のサイズを削減する。"""
    doc = pylopdf.open(ASSETS / "bill-hr815.pdf")
    plain = doc.tobytes()
    optimized = doc.tobytes(garbage=True, deflate=True, object_streams=True)
    assert len(optimized) < len(plain)


@ALL
def test_set_metadata_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.set_metadata({"title": "回帰テスト", "author": "pylopdf"})
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.metadata["title"] == "回帰テスト"
    assert reopened.metadata["author"] == "pylopdf"


@ALL
def test_render_page_png(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    png = doc.render_page(0, scale=1.0)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 1000


@ALL
def test_render_page_svg(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    svg = doc.render_page_svg(0)
    assert svg.startswith("<svg")


@WITH_TEXT
def test_extract_text_survives_edit(case: Case) -> None:
    """編集（select）後もテキスト抽出が壊れないこと（継承属性の焼き込み回帰検知）。"""
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    doc.select([0])
    reopened = pylopdf.open(stream=doc.tobytes())
    assert case.snippet in reopened.get_page_text(0)
