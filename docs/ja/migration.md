---
title: pymupdfからの移行
description: 移行ツール、名前ごとの対応表、注意すべき挙動の違いを使って、pymupdfのコードをpylopdfへ移植します。
---

# pymupdfからの移行

pylopdfはpymupdfのAPIに倣っています。同じ名前は同じ意味を持ち、`"words"`タプル、`"dict"`の構造、矩形を返す`search_for()`など、多くのコードが依存するデータの形も一致しています。ただし、まだそのまま置き換えられるわけではありません。pymupdfのAPIの一部しかカバーしておらず、開けるのは**PDFファイルだけ**で（XPS、EPUB、画像は不可）、いくつかの挙動は意図的に変えています。このページでは、そのまま使えるもの、異なるもの、pylopdfが実装していない部分の代わりに使うものをまとめます。

## 自動移行 { #codemod }

`python -m pylopdf.migrate`はpymupdfのプログラムをその場で書き換えます。`migrate` extraでインストールします。

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

`import fitz`と`import pymupdf`を置き換え、機械的に置き換えられるもの（ページ番号を含む`get_toc`/`set_toc`、`embfile_*`、`xref_*`のストリーム読み出し、`copy_page`、その場で値を変える`Rect`/`Point`/`Matrix`のメソッドなど）を書き換え、人手が必要な文の上には`# TODO(pylopdf): …`コメントを付けます。最後に、それらの箇所を`path:line: message`の形で一覧表示します。

- `--diff`は書き込まずに変更内容を表示します。
- `--check`は変更されるファイルがあると終了コード1を返します。CI向けです。

このツールは変数の型を知ることができないので、差分を確認してください。実行時に、pylopdfにないpymupdfの名前を使うと`AttributeError`が発生し、そのメッセージに置き換え先か、まだ対応していないことが示されます。

## 名前の対応表 { #mapping }

「同じ」は、その呼び出しが変更なしで動くことを意味します。備考には違いを記載しています。

### 文書とページ { #mapping-documents }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `import pymupdf`（または`fitz`） | `import pylopdf` | |
| `pymupdf.open(path)`, `open(stream=…)`, `open("pdf", data)` | 同じ | PDFのみ。`password=`はキーワード専用 |
| `doc[i]`, `len(doc)`, イテレーション | 同じ | |
| `doc.metadata`, `set_metadata()` | 同じ | |
| `doc.needs_pass`, `authenticate()` | 同じ | 戻り値も同じ |
| `doc.save(garbage=4, deflate=True)` | 同じ | `garbage`がページ、注釈、フォームフィールド、レイヤーを統合することはありません |
| `doc.save(encryption=…, user_pw=…)` | 同じ | `PDF_ENCRYPT_NONE`と`PDF_ENCRYPT_AES_256`のみ。暗号化なしでパスワードを渡すと、無視せずに例外になります |
| `doc.select()`, `delete_page()`, `delete_pages()`, `fullcopy_page()`, `new_page()` | 同じ | 削除したページのフォームフィールドとタグ付きPDFの構造も削除します |
| `doc.copy_page(pno, to)` | `doc.fullcopy_page(pno, to)` | pylopdfは1つのページオブジェクトを2つのページで共有しません |
| `doc.insert_pdf(docsrc, from_page=, to_page=, start_at=)` | 同じ | 取り込んだフォームフィールドの名前が衝突すると`name+N`に改名します。`widgets=`と`join_duplicates=`はありません |
| `doc.get_toc()`, `set_toc(toc)` | `doc.get_outline()`, `set_outline(entries)` | ページ番号は1始まりではなく0始まり |
| `doc.get_page_labels()`, `set_page_labels()`, `page.get_label()` | 同じ | |
| `doc.embfile_add/names/get/del/upd/info/count` | `doc.attachments` | マッピングです: `add(name, data, description=…)`、`list(…)`、`[name].read()`、`del …[name]`、`[name].write(data)`、`len(…)`。`desc=`は`description=`になります |
| `doc.xref_*`, `pdf_catalog()`, `pdf_trailer()` | `doc.objects`, `doc.catalog`, `doc.trailer` | PDF構文ではなく型付きのPythonの値。[低レベルオブジェクト](api.md#low-level-objects)を参照 |

### テキスト・レンダリング・画像 { #mapping-text }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `page.get_text()` | 同じ | オプションは`text`、`words`、`blocks`、`dict`、`rawdict`。`html`と`xml`はありません |
| `page.search_for(text)` | 同じ | 矩形を返します。`quads=`はありません。`max_hits=`を上げない限り結果は最大4,096件 |
| `page.get_textpage_ocr()` | `page.get_text_ocr()`, `page.apply_ocr()` | 組み込みのオフラインOCR。[オフラインOCR](ocr.md)を参照 |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | 組み込み |
| `page.get_pixmap(matrix=…, dpi=…)`, `doc.get_page_pixmap()` | 同じ | 拡大縮小の行列のみ。`colorspace=`と`annots=`はまだありません |
| `pix.samples`, `width`, `height`, `n`, `alpha`, `stride`, `colorspace`, `save()` | 同じ | `save()`と`tobytes()`はPNGのみを書き出します |
| `page.get_svg_image()` | 同じ | テキストは常にパスとして描きます |
| `page.get_images()`, `get_image_info()`, `doc.extract_image()` | 同じ | `get_image_info(hashes=True)`にはまだ対応していません |
| `page.get_drawings()` | 同じ | `extended=`のクリッピングとグループの情報はありません |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | 同じ | JPEGへの再エンコードのみなので、再サンプリングには`quality`が必要です。`None`ではなく統計情報を返します |

### 描き込みと注釈 { #mapping-drawing }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `page.insert_text()`, `insert_textbox()` | 同じ | `font=`、`fontbuffer=`、`font_language=`も受け付けます。[テキストとフォント](api.md#fonts)を参照 |
| `pymupdf.Font(...)`, `font.text_length()`, `has_glyph()` | 同じ | Standard 14フォント、OpenTypeファイル、インストール済みのフォントextra。pymupdfのほかの内蔵フォントと、`script=`、`ordering=`、`embed=`は使えません |
| `page.insert_image(rect, filename=/stream=/pixmap=)` | 同じ | JPEG、PNG、`Pixmap`。ほかの形式はPillowで変換してください。同じ画像を2回挿入しても1回だけ格納されるので、`xref=`は不要です |
| `page.show_pdf_page(rect, docsrc, pno)` | 同じ | 取り込み元は同じ文書でもかまいません |
| `page.new_shape()`, `shape.draw_*`, `finish()`, `insert_text()`, `commit()` | 同じ | `with page.new_shape() as shape:`は自動で確定します。`morph=`と`oc=`にはまだ対応していません |
| `page.draw_line()`, `draw_rect()`, ほかの`draw_*` | 同じ | スタイルの指定はキーワード専用 |
| `page.annots()`, `annot.type`, `rect`, `info`, `colors`, `flags` | 同じ | 現在は読み取り専用 |
| `page.add_highlight_annot()`, `page.insert_link()` | 同じ | `insert_link`が作るのはWebリンク（`LINK_URI`）のみ |
| `page.widgets()` | `doc.get_form_fields()`, `doc.set_form_field(name, value)` | フォームは文書単位で扱います |

### 幾何型 { #mapping-geometry }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `Point`, `Matrix`, `Rect`, `IRect`, `Quad`とその演算子 | 同じ | 不変 |
| `p.transform(m)`, `m.invert()` | `p = p * m`, `m = ~m` | |
| `r.include_point(p)`, `include_rect(o)`, `intersect(o)`, `normalize()`, `transform(m)` | `r = r | p`, `r | o`, `r & o`, `r.normalized()`, `r * m` | |
| `m.prerotate(deg)`, `prescale(sx, sy)`, `preshear(sx, sy)`, `pretranslate(tx, ty)` | `m = Matrix(deg) * m`, `Matrix(sx, sy) * m`, `Matrix(sx, sy, 1) * m`, `Matrix(1, 0, 0, 1, tx, ty) * m` | |

## 挙動の違い { #behavioral-differences }

- **幾何型の値は変更できません。** `Point`、`Matrix`、`Rect`、`IRect`、`Quad`は不変の名前付きタプルです。インプレースのメソッドを呼ぶ代わりに、演算子の結果を代入してください。pymupdfと同じく、ただのタプルとは異なり、`p + q`は座標を足し、`Rect(0, 0, 0, 0)`は偽になります。
- **古いページは例外を送出します。** ページを追加・削除・並べ替えた後、それ以前に取得した`Page`は、黙って別のページを指す代わりに`StalePageError`を送出します。`doc[i]`で取得し直してください。
- **例外。** 基底クラスは`ValueError`のサブクラスである`PdfError`なので、`except ValueError`はそのまま動きます。`FileDataError`と`EmptyFileError`はpymupdfと同じ名前です。存在しないファイルはPython組み込みの`FileNotFoundError`を送出するので、`except pymupdf.FileNotFoundError`は`except FileNotFoundError`に書き換えてください。
- **回転したページ。** 抽出・検索・描き込み・レンダリングはすべて、回転したページも含め、表示されたページの座標を使います。
- **ページ下端付近の`insert_text`。** pylopdfはどの行が収まるかをページの見える領域（`page.rect`）で判断しますが、pymupdfは回転前のページの下端までで測ります。通常の回転していないページでは両者は一致します。
- **フォントの扱いは厳格です。** pylopdfは黙って別のフォントを使わず、例外を送出します。たとえば、フォントに文字がない場合や、`is_bold=True`でインストールされていない太字を求めた場合です。
- **フォームの結合。** `insert_pdf()`は、pymupdfの既定の`widgets=True`と同じく、常にフォームフィールドをコピーします。受け取る側の文書に同じ名前のフィールドがあれば`name+1`、`name+2`のように改名するので、同じフォームのコピーはそれぞれ別の値を持ちます。
- **表とMarkdown。** `find_tables()`は既定で罫線で描かれた表を検出します。罫線のない表には`strategy="text"`を渡してください。`to_markdown()`は検出した表を読み順の位置に置き、周囲の段落からその表のテキストを取り除きます。

## 実装しない機能: 代わりに使うもの { #deliberate-scope }

| pymupdfの機能 | 代替 |
|---|---|
| Story API、`insert_htmlbox`（組版） | [typst](ecosystem.md#typesetting) |
| 電子署名 | [pyHanko](ecosystem.md#signatures) |
| 増分保存 | 予定しています。それまではpylopdfはファイル全体を書き直し、署名はpyHankoが扱います |
| XPS、EPUB、CBZ、画像を開く | 予定はありません。pylopdfはPDF専用です |

## 移植例 { #worked-example }

```python
# pymupdf
import pymupdf
doc = pymupdf.open("in.pdf")
page = doc[0]
for rect in page.search_for("total"):
    page.add_highlight_annot(rect)
pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
pix.save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```

```python
# pylopdf
import pylopdf
doc = pylopdf.open("in.pdf")
page = doc[0]
page.add_highlight_annot(page.search_for("total"))   # リスト全体を受け取る
page.get_pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```
