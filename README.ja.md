# pylopdf

[![PyPI](https://img.shields.io/pypi/v/pylopdf)](https://pypi.org/project/pylopdf/)
[![CI](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml/badge.svg)](https://github.com/yhay81/pylopdf/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/pylopdf)](https://pypi.org/project/pylopdf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[English README is here](README.md)

Rust 製の PDF 編集・レンダリングライブラリ。編集は [lopdf](https://github.com/J-F-Liu/lopdf)、
レンダリングは [hayro](https://github.com/LaurenzV/hayro)（typst が採用する純 Rust PDF レンダラ）が担います。
**自由に使えるライセンス（MIT）・実行時依存ゼロ・軽量 wheel** で、pymupdf の主要ユースケースをカバーすることを目指しています。

## コンセプト

| | pylopdf | pymupdf | pypdf | pypdfium2 |
|---|---|---|---|---|
| ライセンス | **MIT** | AGPL / 商用 | BSD | Apache/BSD |
| wheel サイズ | **約 3.5 MB** | 約 40 MB+ | 軽量（純 Python） | 約 8 MB |
| 編集（結合・分割・メタデータ） | ✅ | ✅ | ✅ | 限定的 |
| レンダリング（PNG / SVG） | ✅ | ✅ | ❌ | ✅（PNG） |
| テキスト抽出 | ✅（基本） | ✅（高精度） | ✅ | ✅ |
| 実装 | **純 Rust** | C | Python | C++ (PDFium) |

- AWS Lambda などサイズ制約のある環境にそのまま載る
- AGPL を避けたい商用プロジェクトで使える
- abi3 対応: Python 3.10〜3.14 を単一 wheel でサポート
- [pymupdf](https://github.com/pymupdf/PyMuPDF) に近い操作感の API

**制約**: 精密なレイアウト解析、注釈・フォーム編集は未対応です。
これらが必要な場合は pymupdf を検討してください。

## インストール

```bash
pip install pylopdf
```

フォント非埋め込みの日本語 PDF をレンダリングする場合は、CJK フォント付きで
インストールする（Noto Sans/Serif JP を同梱、レンダリング時に自動検出）:

```bash
pip install pylopdf[cjk]
```

ソースからビルドする場合（要 Rust ツールチェーン）:

```bash
uv sync
```

## 使い方

```python
import pylopdf

# 開く（パス・バイト列のどちらからでも）
doc = pylopdf.open("input.pdf")
doc = pylopdf.open(stream=pdf_bytes)

# ページ数
print(doc.page_count)  # len(doc) でも同じ

# メタデータの読み書き
print(doc.metadata["title"])
doc.set_metadata({"title": "月次レポート", "author": "山田 太郎"})

# テキスト抽出（0 始まりのページ番号）
text = doc.get_page_text(0)

# レンダリング
png: bytes = doc.render_page(0)             # 72dpi 相当
png2x: bytes = doc.render_page(0, scale=2)  # 144dpi 相当
png300 = doc.render_page(0, dpi=300)        # 解像度で指定
png_bg = doc.render_page(0, background=(255, 255, 255))  # 白背景（既定は透明）
svg: str = doc.render_page_svg(0)

# ページ削除（split）
doc.delete_page(0)
doc.delete_pages([1, 2])

# ページの抽出・並べ替え
doc.select([2, 0])

# 結合（merge）
merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"))

# 保存
merged.save("merged.pdf")
data: bytes = merged.tobytes()

# サイズ最適化して保存（未参照削除 + 圧縮 + object stream 化）
merged.save("small.pdf", garbage=True, deflate=True, object_streams=True)

# コンテキストマネージャ
with pylopdf.open("input.pdf") as doc:
    print(doc.metadata)

# 暗号化 PDF（RC4-40/128・AES-128・AES-256。user password 空なら自動復号）
doc = pylopdf.open("locked.pdf", password="secret")
doc = pylopdf.open("locked.pdf")
if doc.needs_pass:
    doc.authenticate("secret")  # 0=失敗, 2=user, 4=owner, 6=両方

# 非埋め込み CJK フォントの代替フォント
# （pylopdf[cjk] なら自動。手持ちのフォントも指定できる）
doc.set_fallback_font("NotoSansJP-Regular.otf")
doc.set_fallback_font(font_bytes, kind="serif")
```

## API

`pylopdf.Document`（`pylopdf.open()` は別名コンストラクタ）:

| メソッド / プロパティ | 説明 |
|---|---|
| `Document(filename=None, stream=None, password=None)` | パスかバイト列から開く。両方 None で空ドキュメント |
| `needs_pass` / `is_encrypted` | 暗号化状態（pymupdf 互換の意味論） |
| `authenticate(password)` | パスワードで復号（戻り値 0/1/2/4/6、pymupdf 互換） |
| `page_count` / `len(doc)` | ページ数 |
| `metadata` | メタデータ辞書（title, author, subject, keywords, creator, producer, creationDate, modDate, format） |
| `set_metadata(dict)` | メタデータ設定（空文字列で項目削除） |
| `get_page_text(pno)` | ページのテキスト抽出 |
| `render_page(pno, scale=1.0, dpi=None, background=None)` | ページを PNG 画像（bytes）にレンダリング。dpi は scale の代替、background は背景色 RGB(A)（1辺65,535px・総64MPまで） |
| `render_page_svg(pno)` | ページを SVG 文字列にレンダリング |
| `set_fallback_font(font, kind="sans", index=0)` | 非埋め込み CJK 用の代替フォント（パス/bytes）を設定。`None` で自動検出も無効化 |
| `select(page_numbers)` | 指定ページだけを指定順で残す（並べ替え可） |
| `delete_page(pno)` / `delete_pages(iterable)` | ページ削除 |
| `insert_pdf(other)` | 別ドキュメントの全ページを末尾に結合 |
| `save(filename, garbage=False, deflate=False, object_streams=False)` / `tobytes(同)` | 保存。garbage=未参照オブジェクト削除、deflate=ストリーム圧縮、object_streams=PDF 1.5+ 形式でサイズ削減 |
| `close()` | 閉じる（with 文対応） |

低レベル API が必要な場合は `pylopdf.pylopdf_core._Document`（lopdf の薄いラッパー）を直接使えます。

## アーキテクチャ

2026 年の Rust PDF エコシステムの役割分担に沿った構成です:

```
pylopdf.Document (Python, pymupdf 風 API)
   └─ _Document (PyO3)
        ├─ lopdf 0.44   … 編集: 開く→変更→保存のフルサイクル
        └─ hayro 0.7    … レンダリング: PNG / SVG（標準フォント同梱）
```

```
rust/          # PyO3 バインディング
src/pylopdf/   # Python 高レベル API
tests/         # pytest（Rust 側の挙動も Python テストで検証）
```

```bash
uv sync                    # ビルド + 依存インストール
uv run pytest              # テスト
uv run ruff check .        # lint
uv run mypy src tests      # 型チェック
uv build --wheel           # wheel ビルド
```

Rust ソース変更は `uv sync` が検知して自動再ビルドします（`tool.uv.cache-keys` 設定済み）。

## ライセンス

MIT（依存する lopdf は MIT、hayro は MIT/Apache-2.0）
