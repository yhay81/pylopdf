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

| | pylopdf | pymupdf | pypdf | pypdfium2 | pdf_oxide | pikepdf |
|---|---|---|---|---|---|---|
| ライセンス | **MIT** | AGPL / 商用 | BSD | Apache/BSD | MIT/Apache-2.0 | MPL-2.0 |
| wheel サイズ | **約 3.5 MB** | 約 40 MB+ | 軽量（純 Python） | 約 8 MB | 約 10〜11 MB | 約 2〜5 MB |
| 編集（結合・分割・回転・しおり） | ✅ | ✅ | ✅ | 限定的 | ✅ | ✅（構造操作特化） |
| レンダリング（PNG / SVG） | ✅ | ✅ | ❌ | ✅（PNG） | ❌ | ❌（公式に他ツールを推奨） |
| テキスト抽出 | ✅（基本） | ✅（高精度） | ✅ | ✅ | ✅（高精度・表検出/Markdown変換） | ❌（公式に他ツールを推奨） |
| 暗号化（AES-256） | ✅ 読み書き | ✅ | ✅ | ❌ | 未文書化 | ✅（qpdf 経由） |
| CJK フォント fallback | ✅（[cjk] extra） | ✅ | — | 手動 | — | — |
| 実装 | **純 Rust** | C | Python | C++ (PDFium) | Rust | C++ (qpdf) |

- AWS Lambda などサイズ制約のある環境にそのまま載る
- AGPL を避けたい商用プロジェクトで使える
- abi3 対応: Python 3.10〜3.14 を単一 wheel でサポート
- [pymupdf](https://github.com/pymupdf/PyMuPDF) に近い操作感の API

**制約**: 精密なレイアウト解析、フォーム（AcroForm）編集、注釈の高度な編集は未対応です。
これらが必要な場合は pymupdf を検討してください。
組版・PDF/A 生成・電子署名は後述の「エコシステム連携」で解決できます。

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

# 位置付きテキストと検索（pymupdf 風、左上原点）
words = doc[0].get_text("words")     # (x0, y0, x1, y1, 語, ブロック, 行, 語番号)
layout = doc[0].get_text("dict")     # blocks → lines → spans（bbox 付き）
rects = doc[0].search_for("税")      # 大文字小文字を区別しない。list[Rect]
images = doc[0].get_images()         # [{"width", "height", "bbox", "ext", "image"}]
pix = doc[0].get_pixmap(dpi=144)     # NumPy / PIL 向けの RGBA8 画素（pix.samples）

# レンダリング
png: bytes = doc.render_page(0)             # 72dpi 相当
png2x: bytes = doc.render_page(0, scale=2)  # 144dpi 相当
png300 = doc.render_page(0, dpi=300)        # 解像度で指定
png_bg = doc.render_page(0, background=(255, 255, 255))  # 白背景（既定は透明）
svg: str = doc.render_page_svg(0)

# ページ削除（split）
doc.delete_page(0)
doc.delete_pages([1, 2])

# ページの抽出・並べ替え（重複指定は複製になる）
doc.select([2, 0])

# ページオブジェクト（0 始まり。負数は末尾から）
page = doc[0]
for page in doc:
    print(page.number, page.rect)
page.set_rotation(90)                # 表示回転（90 の倍数）
page.set_mediabox((0, 0, 300, 400))  # ページボックス変更

# ページの挿入・複製
doc.new_page()          # 末尾に空ページ（既定 A4）
doc.copy_page(0, to=1)  # 0 ページ目の複製を 1 ページ目の位置へ

# 描き込み（座標は search_for / get_text と同じ左上原点の表示座標）
page.insert_image((72, 72, 200, 200), filename="logo.png")     # JPEG はそのまま、PNG は透過対応
page.insert_image(page.search_for("承認印")[0], stream=hanko)  # 検索した位置へ押印
page.show_pdf_page(page.rect, letterhead)  # 別 PDF のページをベクタのまま重ねる（透かし・レターヘッド）
page.replace_text("DRAFT", "FINAL")        # テキスト置換（単純エンコーディングのフォントのみ）

# ヘッダ / フッタ / ページ番号（標準 14 フォント、WinAnsi の範囲。日本語は typst レシピで）
for i, p in enumerate(doc):
    p.insert_text((p.rect.width - 90, p.rect.height - 30), f"Page {i + 1}", fontsize=9)

# 注釈: 検索してマーカー / リンク
page.add_highlight_annot(page.search_for("重要"))  # 外観ストリーム生成付き（どのビューアでも見える）
page.add_link_annot(page.search_for("Example")[0], "https://example.com/")
print(page.annots())  # [{"type", "rect", "contents", "uri"}]

# スキャン PDF を searchable に（外部 OCR の結果を不可視テキスト層として書き込む）
page.insert_ocr_text_layer(ocr_words)  # (x0, y0, x1, y1, テキスト, ...) の列。日本語もサイズ増ほぼゼロ

# PDF/A の自己宣言を読む（検証は veraPDF へ）
print(doc.get_pdfa_claim())  # 例: (2, "B") = PDF/A-2b 宣言。無ければ None

# ページラベル（前付き = ローマ数字、本文 = 算用数字のような表示番号）
doc.set_page_labels([{"startpage": 0, "style": "r"}, {"startpage": 3, "style": "D"}])
print(doc[4].get_label())  # "2"

# 添付ファイル（請求書 PDF へ XML を添付する等）
doc.embfile_add("invoice.xml", xml_bytes, filename="請求書データ.xml")
print(doc.embfile_names())  # ["invoice.xml"]
xml = doc.embfile_get("invoice.xml")

# しおり（目次）。ページ番号はここだけ 1 始まり（pymupdf 互換）
doc.set_toc([[1, "第 1 章", 1], [2, "1.1 節", 2]])
print(doc.get_toc())

# 結合（merge）。範囲・逆順・挿入位置も指定できる
merged = pylopdf.Document()
merged.insert_pdf(pylopdf.open("a.pdf"))
merged.insert_pdf(pylopdf.open("b.pdf"), from_page=0, to_page=2, start_at=0)

# 保存
merged.save("merged.pdf")
data: bytes = merged.tobytes()

# サイズ最適化して保存（未参照削除 + 圧縮 + object stream 化）
merged.save("small.pdf", garbage=True, deflate=True, object_streams=True)

# 暗号化して保存（AES-256。owner_pw だけなら閲覧自由・権限制限のみ）
merged.save("locked.pdf", user_pw="secret", permissions=pylopdf.Permissions.PRINT)

# メタデータとページ数だけ高速に読む（全体をパースしない）
info = pylopdf.peek_metadata("input.pdf")
print(info["title"], info["page_count"], info["encrypted"])

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

## エコシステム連携（組版・PDF/A・電子署名）

pylopdf は「編集・抽出・レンダリングの軽量コア」に集中し、隣接領域は実績ある
ライブラリとの連携で解決します。以下のレシピは統合テスト（tests/test_interop.py）で
動作を保証しています。

**組版・新規文書の生成 = [typst](https://typst.app/)**（[typst-py](https://pypi.org/project/typst/) 経由）。
レポートや帳票は typst で組版し、bytes をそのまま pylopdf へ:

```python
import typst
import pylopdf

pdf_bytes = typst.compile("report.typ")   # 組版は typst
doc = pylopdf.open(stream=pdf_bytes)      # 編集・抽出・結合は pylopdf
```

**新規文書の PDF/A** も typst に任せられます（krilla の検証付き出力。
PDF/A-1b〜4 / PDF/UA-1 対応）:

```python
pdf_a: bytes = typst.compile("report.typ", pdf_standards="a-2b")
```

**日本語の透かし・ヘッダ / フッタ** も typst との合わせ技で描けます。typst で
1 ページの透かしを組み（フォントはサブセット埋め込みされる）、`show_pdf_page` で
全ページへベクタのまま焼き込みます:

```python
from pylopdf_fonts_cjk import sans_path  # pip install pylopdf[cjk]（Noto フォントを再利用）

stamp_typ = """
#set page(width: 595pt, height: 842pt, fill: none)
#set text(font: "Noto Sans JP", size: 48pt, fill: rgb(255, 0, 0, 40%))
#align(center + horizon)[社外秘]
"""
stamp = pylopdf.open(stream=typst.compile(stamp_typ.encode(), font_paths=[str(sans_path().parent)]))
for page in doc:
    page.show_pdf_page((0, 0, page.rect.width, page.rect.height), stamp)
```

既存 PDF の PDF/A 変換・検証は別問題で、検証は [veraPDF](https://verapdf.org/)（Java）が
事実上の標準です。

**電子署名（PAdES）= [pyHanko](https://pypi.org/project/pyHanko/)**（MIT）。
増分更新で署名するため、pylopdf の出力バイト列は署名後も先頭に無加工で残ります:

```python
import io
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers

signer = signers.SimpleSigner.load("key.pem", "cert.pem")
out = signers.sign_pdf(
    IncrementalPdfFileWriter(io.BytesIO(doc.tobytes())),
    signers.PdfSignatureMetadata(field_name="Signature1"),
    signer=signer,
)
signed_pdf: bytes = out.getvalue()
```

## API

`pylopdf.Document`（`pylopdf.open()` は別名コンストラクタ）:

| メソッド / プロパティ | 説明 |
|---|---|
| `Document(filename=None, stream=None, password=None, max_decompressed_size=None)` | パスかバイト列から開く。両方 None で空ドキュメント。max_decompressed_size は解凍爆弾対策の展開上限 |
| `doc[i]` / `load_page(pno)` / `for page in doc` | Page ビューを取得（負数は末尾から。ページ構造の変更後は取得し直す） |
| `needs_pass` / `is_encrypted` | 暗号化状態（pymupdf 互換の意味論） |
| `authenticate(password)` | パスワードで復号（戻り値 0/1/2/4/6、pymupdf 互換） |
| `page_count` / `len(doc)` | ページ数 |
| `metadata` | メタデータ辞書（title, author, subject, keywords, creator, producer, creationDate, modDate, format） |
| `set_metadata(dict)` | メタデータ設定（空文字列で項目削除） |
| `get_page_text(pno, option="text")` | ページのテキスト抽出（`"words"` / `"blocks"` / `"dict"` で位置付き） |
| `render_page(pno, scale=1.0, dpi=None, background=None)` | ページを PNG 画像（bytes）にレンダリング。dpi は scale の代替、background は背景色 RGB(A)（1辺65,535px・総64MPまで） |
| `render_page_svg(pno)` | ページを SVG 文字列にレンダリング |
| `set_fallback_font(font, kind="sans", index=0)` | 非埋め込み CJK 用の代替フォント（パス/bytes）を設定。`None` で自動検出も無効化 |
| `select(page_numbers)` | 指定ページだけを指定順で残す（並べ替え可。重複指定は複製になる） |
| `delete_page(pno)` / `delete_pages(iterable)` | ページ削除 |
| `insert_pdf(other, from_page=0, to_page=-1, start_at=-1)` | ページ範囲の結合（負数・逆順可。start_at で挿入位置指定） |
| `new_page(pno=-1, width=595, height=842)` / `copy_page(pno, to=-1)` | 空ページの挿入・ページ複製 |
| `get_toc()` / `set_toc(toc)` | しおり（目次）の読み書き。`[[レベル, タイトル, ページ番号], ...]`（ページ番号はここだけ 1 始まり） |
| `get_pdfa_claim()` | XMP の PDF/A 宣言 `(part, conformance)` の読み取り（自己申告の読み取りで、検証ではない） |
| `embfile_add(name, data, filename=, desc=)` / `embfile_names()` / `embfile_get(name)` / `embfile_del(name)` | 添付ファイル（EmbeddedFiles）の追加・一覧・取得・削除 |
| `get_page_labels()` / `set_page_labels(labels)` | ページラベル定義の読み書き（`{"startpage", "style", "prefix", "firstpagenum"}`） |
| `save(filename, garbage=, deflate=, object_streams=, user_pw=, owner_pw=, permissions=)` / `tobytes(同)` | 保存。garbage=未参照削除、deflate=圧縮、object_streams=PDF 1.5+ 形式で削減、user_pw/owner_pw=AES-256 暗号化（元は平文のまま） |
| `close()` | 閉じる（with 文対応） |

`pylopdf.Page`（`doc[i]` で取得）:

| メソッド / プロパティ | 説明 |
|---|---|
| `number` / `parent` | 0 始まりのページ番号と所属 Document |
| `get_label()` | ページの表示ラベル（"iv"・"A-2" など。定義が無ければ空文字列） |
| `get_text(option="text")` | テキスト抽出。`"words"` / `"blocks"` / `"dict"` で位置付きレイアウト |
| `search_for(needle)` | ページ内検索（大文字小文字を区別しない）。`list[Rect]` |
| `get_images()` | ページ上の画像を抽出（JPEG は元バイト列をパススルー、他は PNG 化） |
| `get_pixmap(scale, dpi=, background=)` | `Pixmap`（ストレート RGBA8。`samples` / `width` / `height` / `stride` / `tobytes()`）へレンダリング |
| `insert_image(rect, filename=/stream=, keep_proportion=True, overlay=True)` | 画像の描き込み（JPEG は再圧縮なし、PNG は透過対応。rect は表示座標） |
| `show_pdf_page(rect, src, pno=0, keep_proportion=True, overlay=True)` | 別ドキュメントのページをベクタのまま重ねる（透かし・スタンプ・レターヘッド） |
| `insert_text(point, text, fontsize=11, fontname="helv", color=(0,0,0))` | 標準 14 フォントでテキスト印字（WinAnsi の範囲。`\n` で複数行、回転ページでも正立） |
| `insert_ocr_text_layer(words)` | OCR 結果を不可視テキスト層に（searchable PDF 化。フォント非埋め込みでサイズ増ほぼゼロ） |
| `annots()` | 注釈の読み取り（`{"type", "rect", "contents", "uri"}` の辞書。rect は表示座標） |
| `add_highlight_annot(rects, color=(1,1,0), opacity=0.4, content=None)` | ハイライト注釈。`search_for` の結果をそのまま渡せる。外観ストリーム生成付き |
| `add_link_annot(rect, uri)` | URI リンク注釈（枠線なし） |
| `replace_text(search, replacement, default_char=None)` | テキスト置換（単純エンコーディングのみ。置換数を返す。CJK 非対応） |
| `render(scale, dpi=, background=)` / `render_svg()` | レンダリング |
| `rotation` / `set_rotation(deg)` | 表示回転（90 の倍数。継承解決済み） |
| `mediabox` / `cropbox` / `rect` | ページボックス（`Rect`）。rect は回転を反映した表示矩形 |
| `set_mediabox(rect)` / `set_cropbox(rect)` | ページボックスの設定 |

モジュールレベル:

| 名前 | 説明 |
|---|---|
| `peek_metadata(filename/stream, password=None)` | 全体をパースしないメタデータ高速読み取り（page_count / encrypted 付き。大量走査向け） |
| `Permissions` | 暗号化の許可フラグ（IntFlag。PRINT や COPY を組み合わせる） |
| `Rect` | 矩形の NamedTuple（width / height プロパティ付き） |
| 例外 | `PdfError`（ValueError 互換の基底）、`PasswordError`、`DocumentClosedError`、`EncryptedDocumentError`、`StalePageError` |

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
