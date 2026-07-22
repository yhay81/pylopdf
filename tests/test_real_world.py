"""実世界 PDF に対する回帰テスト。

tests/assets/real_world/ に同梱した「実際のツールチェーンが生成した PDF」全種に対して
open / metadata / テキスト抽出 / 編集 / 保存 / レンダリングを一括で回し、
lopdf / hayro の限界を早期発見する。各ファイルの出典・ライセンス・既知の限界は
同ディレクトリの README.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

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
    Case("pdf20-simple.pdf", pages=1, version="PDF 2.0", snippet=None),
    Case("usrguide.pdf", pages=27, version="PDF 1.5", snippet="for authors"),
    Case("bill-hr815.pdf", pages=110, version="PDF 1.5", snippet="One Hundred Eighteenth Congress"),
    Case("mhlw-doc.pdf", pages=2, version="PDF 1.7", snippet="裁判例"),
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


@WITH_TEXT
def test_extract_text_page0(case: Case) -> None:
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    assert case.snippet in doc.get_page_text(0)


@pytest.mark.xfail(
    reason="lopdf は content stream 内の % コメントを解釈できず、コメントを含むページの抽出が空になる"
    "（/Encoding なし Helvetica 自体は StandardEncoding フォールバックで抽出できることを切り分けで確認済み）",
    strict=True,
)
def test_pdf20_extract_text_known_limit() -> None:
    doc = pylopdf.open(ASSETS / "pdf20-simple.pdf")
    assert "HelloWorld" in doc.get_page_text(0).replace(" ", "")


def test_f1040_metadata_title() -> None:
    doc = pylopdf.open(ASSETS / "f1040.pdf")
    assert doc.metadata["title"] == "2025 Form 1040"


@ALL
def test_select_first_page_and_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.select([0])
    assert doc.page_count == 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == 1


@ALL
def test_merge_self_and_roundtrip(case: Case) -> None:
    raw = (ASSETS / case.name).read_bytes()
    doc = pylopdf.open(stream=raw)
    doc.insert_pdf(pylopdf.open(stream=raw))
    assert doc.page_count == case.pages * 2
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages * 2


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
