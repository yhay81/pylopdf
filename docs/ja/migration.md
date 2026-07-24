---
title: pymupdfからの移行
description: pymupdfの処理をpylopdfへ移し、型・挙動・スコープの意図的な違いを理解します。
---

# pymupdf からの移行

pylopdf は pymupdf「風」であって、ドロップイン互換ではありません。ただし移行
コストを決めるデータ形状 — `"words"` タプルの並び・`"dict"` の構造・
`search_for → list[Rect]`・TOC の 1 始まりページ番号 — は pymupdf に合わせて
いるため、抽出やページ操作のコードは小さな修正で移植できます。このページでは
「そのまま動くもの」「変わったもの」「あえて実装せず連携で解決するもの」を
まとめます。

!!! note
    pylopdf が扱うのは **PDF のみ**です。pymupdf の XPS / EPUB / 画像を開く
    機能は対象外です。

## 対応表 { #mapping }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `import fitz` / `import pymupdf` | `import pylopdf` | |
| `fitz.open(path)` / `open(stream=…)` | `pylopdf.open(path)` / `open(stream=…)` | `password=` も同じ |
| `doc[i]`・`len(doc)`・イテレーション | 同じ | 0 始まり・負数可 |
| `doc.metadata` / `set_metadata` | 同じ | キー名も同じ |
| `page.get_text()` | 同じ | オプションは `text` / `words` / `blocks` / `dict` |
| `page.search_for(t)` | 同じ | `list[Rect]`。`quads=` は無い |
| `page.get_pixmap(matrix=fitz.Matrix(2, 2))` | `page.get_pixmap(scale=2)` | `dpi=144` でも。Matrix クラスは無い |
| `pix.samples / width / height / stride` | 同じ | 常にストレートアルファ RGBA8。`tobytes()` → PNG |
| `page.get_images()` | `page.get_images()` | 描画位置 bbox 付き。JPEG はパススルー |
| `doc.select`・`delete_page(s)`・`copy_page`・`new_page` | 同じ | `select` の重複指定は複製になる |
| `doc.insert_pdf(src, from_page=, to_page=, start_at=)` | 同じ | |
| `doc.get_toc()` / `set_toc()` | 同じ | ページ番号は両者とも 1 始まり |
| `doc.save(garbage=4, deflate=True)` | `doc.save(garbage=True, deflate=True, object_streams=True)` | `garbage` は bool |
| `doc.save(encryption=…, user_pw=…)` | `doc.save(user_pw=…, owner_pw=…, permissions=…)` | AES-256 のみ |
| `doc.needs_pass` / `authenticate()` | 同じ | 戻り値の意味（0/1/2/4/6）も同じ |
| `page.rect / rotation / set_rotation` | 同じ | |
| `page.insert_image(rect, filename=)` | 同じ | JPEG/PNG のみ。`pixmap=` は無い（他形式は Pillow で変換） |
| `page.show_pdf_page(rect, src, pno)` | 同じ | 同一ドキュメントの重ねは不可（複製してから） |
| `page.insert_text(point, text, fontsize=, fontname=)` | 同じ | 標準 14 の略名（`helv` など）。WinAnsi の範囲のみ |
| `page.add_highlight_annot(...)` | 同じ | 外観ストリームを常に生成 |
| `doc.embfile_add / names / get / del` | 同じ | |
| `doc.get_page_labels / set_page_labels`・`page.get_label` | 同じ | |
| `page.widgets()` / Widget オブジェクト | `doc.get_form_fields()` / `doc.set_form_field(name, value)` | ドキュメント単位。NeedAppearances 方式 |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | 内蔵・MIT |

## 挙動の違い { #behavioral-differences }

- **座標系**はどちらも左上原点の表示空間。pylopdf では回転ページでも
  抽出・検索・描き込み・レンダリングが一貫して同じ座標になります。
- **型**: `Rect` は不変の `NamedTuple`（`x0, y0, x1, y1` + `width` / `height`）。
  `Point` / `Matrix` / `Quad` クラスは無く、API は素のタプルと `scale=` / `dpi=`
  を受けます。
- **古い Page**: 構造変更（削除・挿入・並べ替え）後に古い `Page` を使うと、
  黙って別ページを指す代わりに `StalePageError` になります。`doc[i]` で
  取得し直してください。
- **例外**: 基底は `PdfError`（`ValueError` のサブクラス）。`PasswordError` /
  `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` が
  それを細分化します。`except ValueError` は動き続けます。
- **`get_text` のオプション**は `text` / `words` / `blocks` / `dict` のみ
  （`html` / `rawdict` / `xml` は無し）。スパン辞書は埋め込みフォントについて
  `font` と pymupdf 互換の `flags`（bold/italic/serif/mono）を持ちます。
- **複数カラムのテキスト**は、決定的な空白ガター検出により、各カラム内を
  上から下へ、カラム間を左から右へ読みます。
- **`Page.find_tables()`** は軸平行の罫線で閉じた完全なグリッドを再構築します。
  罫線なし表と結合セルの推定はまだ行いません。
- **フォーム記入**は値 + `NeedAppearances` を設定し、見た目の描画はビューアが
  行います（pylopdf 自身のレンダラは外観を再生成しません）。
- **縦書き**の読み順はまだ再構築しません。

## あえて実装しないもの — エコシステムで解決 { #deliberate-scope }

| pymupdf の機能 | pylopdf での答え |
|---|---|
| Story API / `insert_htmlbox`（組版） | typst（typst-py 経由）— [レシピ](ecosystem.md) |
| OCR（`get_textpage_ocr`。Tesseract の外部インストール必須） | 任意の OCR エンジン + `insert_ocr_text_layer` |
| 電子署名 | pyHanko（MIT）— [レシピ](ecosystem.md) |
| インクリメンタル保存 | 非対応の方針（qpdf/pikepdf と同じ書き直し思想）。署名用途は pyHanko が担う |
| XPS / EPUB / CBZ / 画像を開く | 対象外 — PDF 専用 |

## 移植例 { #worked-example }

```python
# pymupdf
import fitz
doc = fitz.open("in.pdf")
page = doc[0]
for rect in page.search_for("合計"):
    page.add_highlight_annot(rect)
pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
pix.save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```

```python
# pylopdf
import pylopdf
doc = pylopdf.open("in.pdf")
page = doc[0]
page.add_highlight_annot(page.search_for("合計"))   # リストごと渡せる
with open("page.png", "wb") as f:
    f.write(page.get_pixmap(scale=2).tobytes())
doc.save("out.pdf", garbage=True, deflate=True)
```
