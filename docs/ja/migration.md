---
title: pymupdfからの移行
description: 移行ツール、名前ごとの対応表、注意すべき挙動の違いを使って、pymupdfのコードをpylopdfへ移植します。
---

# pymupdfからの移行

pylopdfは、pymupdfがPDFファイルでできることをすべて、独自のAPIで実現することを目指しています。ただし、そのまま置き換えられるものではありません。開けるのは**PDFファイルだけ**で（XPS、EPUB、画像は不可）、いくつかの挙動は意図的に変えており、次のリリースではAPIを作り直します。移行ツールも、作り直した部分が入るたびにそれに追従します。文書とそのページ、保存、テキストの読み取り、表、OCRはすでに新しいAPI（`pylopdf.open(source)`、`doc.pages`、`doc.save()`、`page.text()`、`page.layout()`、`page.tables()`、`page.ocr()`）を使っています。このページでは、そのまま使えるもの、異なるもの、pylopdfがまだ実装していない部分の代わりに使うものをまとめます。

## 自動移行 { #codemod }

`python -m pylopdf.migrate`はpymupdfのプログラムをその場で書き換えます。`migrate` extraでインストールします。

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

`import fitz`と`import pymupdf`を置き換え、機械的に置き換えられるもの（文書を開く処理、`doc.pages`に置き換わるページ操作、ページ番号を含む`get_toc`/`set_toc`、`embfile_*`、`xref_*`のストリーム読み出し、テキストと表の読み取り、ベクタ図形と画像の配置、その場で値を変える`Rect`/`Point`/`Matrix`のメソッドなど）を書き換え、人手が必要な文の上には`# TODO(pylopdf): …`コメントを付けます。最後に、それらの箇所を`path:line: message`の形で一覧表示します。同じコマンドで、pylopdf 0.15向けに書かれたプログラムも更新できます。

- `--diff`は書き込まずに変更内容を表示します。
- `--check`は変更されるファイルがあると終了コード1を返します。CI向けです。

このツールは変数の型を知ることができないので、差分を確認してください。変数のインデックス参照、長さの取得、イテレーションを`.pages`経由に書き換えるのは、`doc = fitz.open(path)`のように、その変数へのすべての代入が文書を開くものである場合だけです。関数の引数に対する`doc[0]`はそのまま残ります。実行時に、pylopdfにないpymupdfの名前を使うと`AttributeError`が発生し、そのメッセージに置き換え先か、まだ対応していないことが示されます。また、`doc[0]`や`for page in doc`は、`doc.pages`を案内する`TypeError`を送出します。

## 名前の対応表 { #mapping }

「同じ」は、その呼び出しが変更なしで動くことを意味します。備考には違いを記載しています。

### 文書とページ { #mapping-documents }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `import pymupdf`（または`fitz`） | `import pylopdf` | |
| `pymupdf.open(path)`, `open(stream=…)`, `open("pdf", data)` | `pylopdf.open(path)`, `pylopdf.open(data)` | PDFのみ。`password=`はキーワード専用。ファイルオブジェクトは先に読み出してください |
| `pymupdf.open()`, `pymupdf.Document()` | `pylopdf.Document()` | 空の文書 |
| `doc[i]`, `doc.load_page(i)`, `len(doc)`, `doc.page_count`, イテレーション | `doc.pages[i]`, `len(doc.pages)`, `doc.pages`のイテレーション | `doc.pages`は、pymupdfの`doc.pages(start, stop)`と同じようにスライスにも対応します |
| `page.number`, `page.parent` | `page.index`, `page.document` | |
| `doc.metadata`, `set_metadata(m)` | `doc.metadata`, `doc.metadata.update(m)` | 存在する項目のライブなマッピングです。ない項目は`.get(key, "")`で読み、`del doc.metadata[key]`で削除します。`creationDate`と`modDate`は`created`と`modified`に、`format`と`encryption`は`doc.version`と`doc.encryption_method`になります |
| `doc.get_xml_metadata()`, `set_xml_metadata(xml)`, `del_xml_metadata()` | `doc.xmp`, `doc.xmp = xml`, `del doc.xmp` | パケットがないときは`None`です。読み書きとも1 MiBまでです |
| `doc.language`, `set_language(tag)` | `doc.language`, `doc.language = tag` | 完全なタグを扱います。pymupdfは3文字までしか保持しないため、`zh-TW`と`zh-HK`はどちらも`zh-Hant`になります。`del doc.language`で削除します |
| `doc.pagemode`, `pagelayout`, `markinfo`とその`set_*`メソッド | `doc.page_mode`, `page_layout`, `mark_info`への代入 | `PageMode`・`PageLayout`列挙型と`MarkInfo`レコードです。pymupdfは大文字と小文字を区別しませんでしたが、名前は正確に一致させます |
| `doc.has_links()` | `any(page.links for page in doc.pages)` | |
| `doc.has_annots()` | `any(page.annotations for page in doc.pages)` | |
| `doc.is_closed` | 同じ | |
| `doc.name` | `doc.path` | `pathlib.Path`。bytesから開いた文書や新しく作った空の文書では`None`。すべての文書がPDFなので、`is_pdf`はなくなりました |
| `doc.needs_pass`, `doc.is_encrypted` | `doc.needs_password` | 文書を復号するまでTrue |
| `doc.authenticate(password)` | `doc.unlock(password)` | `Access.USER`か`Access.OWNER`を返します。パスワードが違えば、0を返す代わりに`PasswordError`を送出します |
| `doc.permissions`, `is_dirty` | 同じ | `permissions`は`Permissions`フラグの値です。`-4`の代わりに`Permissions.ALL`になります |
| `doc.save(garbage=4, deflate=True)` | `doc.save(dedupe=True, renumber=True)` | 保存は既定でストリームを圧縮し、使われていないオブジェクトを削除します。`garbage=0`は`prune=False`に、`deflate=False`は`compress=False`になります。ページ、注釈、フォームフィールド、レイヤーが統合されることはありません |
| `doc.save(encryption=PDF_ENCRYPT_AES_256, user_pw=…, owner_pw=…, permissions=…)` | `doc.save(encryption=Encryption(user_password=…, owner_password=…, permissions=…))` | AES-256のみ |
| `doc.tobytes(...)`, `doc.write(...)` | `doc.to_bytes(...)` | `save()`と同じオプション |
| `doc.save(use_objstms=True)` | `doc.save(object_streams=True)` | |
| `doc.select(indexes)` | `doc.pages.select(indexes)` | 残さなかったページのフォームフィールドとタグ付きPDFの構造も削除します |
| `doc.delete_page(i)`, `delete_pages(a, b)` | `del doc.pages[i]`, `del doc.pages[a:b + 1]` | 削除するページをリストで指定していた場合は、残りのページを`doc.pages.select(...)`で残してください |
| `doc.new_page(pno, width=, height=)` | `doc.pages.new((width, height), index=pno)` | `index`を指定しなければ末尾に追加します |
| `doc.copy_page(pno, to)`, `fullcopy_page(pno, to)` | `doc.pages.insert(to, doc.pages[pno])` | pylopdfは1つのページオブジェクトを2つのページで共有しません |
| `doc.move_page(pno, to)` | `doc.pages.move(pno, index)` | `index`はページが移動した後の位置です。pymupdfはページ`to`の前に挿入していました |
| `doc.insert_pdf(docsrc, from_page=a, to_page=b, start_at=i)` | `doc.pages.insert(i, docsrc.pages[a:b + 1])`, `doc.pages.extend(docsrc.pages)` | 取り込んだフォームフィールドの名前が衝突すると`name+N`に改名します。`widgets=`と`join_duplicates=`はありません |
| `doc.get_toc()`, `set_toc(toc)` | `doc.outline.entries()`, `doc.outline = entries` | `[level, title, page]`のリストではなく、`destination`が0始まりのページ番号を持つ`Destination`である`OutlineEntry`レコード |
| `doc.outline`, `item.next`, `item.down` | `doc.outline`, `item.children`, `doc.outline.walk()` | `doc.outline`は最初の項目ではなく、最上位の`OutlineItem`ハンドルの並び |
| `doc.set_toc_item(i, ...)`, `del_toc_item(i)` | `OutlineItem`の`title`、`destination`、`color`、`bold`、`italic`、`is_open`に代入 | `del_toc_item`は項目を灰色にして行き先を外していました |
| `doc.get_page_labels()`, `set_page_labels(rules)`, `page.get_label()` | `doc.page_labels`, `page.label` | 辞書ではなく`PageLabelRange(start, style, prefix, first)`レコード |
| `doc.get_page_numbers(label)` | `doc.pages.labeled(label)` | |
| `page.set_rotation(deg)` | `page.rotation = deg` | |
| `page.mediabox`, `set_mediabox(r)` | `page.boxes.media`, `page.boxes.media = r` | |
| `page.cropbox`, `artbox`, `bleedbox`, `trimbox`, `set_cropbox(r)`, `set_artbox(r)`, ... | `page.boxes.crop`, `.art`, `.bleed`, `.trim`。それぞれ代入できます | 保存されているとおりのPDFのユーザー空間。pymupdfはMediaBoxの上端を基準にyを反転していました |
| `page.transformation_matrix`, `rotation_matrix`, `derotation_matrix` | `page.user_to_display` | 回転を含めて、PDFのユーザー空間を表示座標に写します |
| `doc.embfile_add/names/get/del/upd/info/count` | `doc.attachments` | マッピングです: `add(name, data, description=…)`、`list(…)`、`[name].read()`、`del …[name]`、`[name].write(data)`、`len(…)`。`desc=`は`description=`になります |
| `doc.xref_*`, `pdf_catalog()`, `pdf_trailer()` | `doc.objects`, `doc.catalog`, `doc.trailer` | PDF構文ではなく型付きのPythonの値。[低レベルオブジェクト](api.md#low-level-objects)を参照 |

### テキスト・レンダリング・画像 { #mapping-text }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `page.get_text()`, `get_text("text")` | `page.text()` | |
| `page.get_text("words")` | `page.words()` | タプルではなく、`text`、`bbox`、`block`、`line`、`index`を持つ`Word`レコード |
| `page.get_text("blocks")` | `page.layout().blocks` | タプルではなく、`text`、`bbox`、`lines`を持つ`Block`レコード |
| `page.get_text("dict")`, `get_text("rawdict")` | `page.layout().to_dict()`, `page.layout(chars=True).to_dict()` | 同じ辞書を返します。`page.layout()`自体はレコードを返します。`html`と`xml`はありません |
| `page.search_for(text)` | `page.search(text)` | 矩形を返します。`quads=`はありません。`max_hits=`を上げない限り結果は最大4,096件 |
| `page.find_tables()` | `page.tables()` | `Table`レコードのリスト。`table.rows`が`extract()`に、`len(table.rows)`が`row_count`に代わり、`table.cells`は各セルの矩形を行ごとに持ちます。調整用のキーワード、`header`、`to_pandas()`はまだありません |
| `page.get_textpage_ocr()` | `page.ocr()`, `page.apply_ocr()` | 組み込みのオフラインOCR。[オフラインOCR](ocr.md)を参照 |
| `pymupdf4llm.to_markdown(doc)` | `doc.markdown()` | 組み込み |
| `page.get_pixmap(matrix=…, dpi=…, annots=…)` | `page.pixmap(dpi=…, annotations=…)` | `dpi`と`matrix`はどちらか一方。拡大縮小の行列のみ。`colorspace=`は`Colorspace.GRAY`か`Colorspace.RGB`で、CMYKはありません。`doc.get_page_pixmap(i)`は`doc.pages[i].pixmap()`になります |
| `pix.samples`, `pix.samples_mv` | `bytes(pix)`, `memoryview(pix)` | `numpy.asarray(pix)`はコピーせずに形`(height, width, channels)`で画素を読みます |
| `pix.n` | `pix.channels` | |
| `pix.tobytes(output, jpg_quality=…)`, `pix.save(path, output, jpg_quality=…)` | `pix.to_bytes(format, quality=…)`, `pix.save(path, format=…, quality=…)` | PNGとJPEG。ほかの形式はPillowに任せます |
| `pix.width`, `height`, `alpha`, `stride`, `colorspace` | 同じ | `colorspace`は`Colorspace.GRAY`か`Colorspace.RGB`。`csRGB`と`csGRAY`は`Colorspace.RGB`と`Colorspace.GRAY`になります |
| `page.get_svg_image()` | `page.svg()` | テキストは常にパスとして、72 dpiで描きます |
| `page.get_images()` | `page.resources.images` | タプルの代わりに`xref`、`width`、`height`、`bits`、`colorspace`、`filters`、マスクを持つ`ImageObject`ハンドル |
| `doc.extract_image(xref)` | `doc.images[xref].extract()` | `format`、`data`、`resolution`を持つ`EncodedImage`。サイズ、色空間、マスクは`doc.images[xref]`のプロパティです |
| `pymupdf.Pixmap(doc, xref)` | `doc.images[xref].to_pixmap()` | `alpha=True`で画像のマスクを適用します |
| `page.get_image_info()` | `page.content(text_runs=False, paths=False).images` | `xref`、`bbox`、`matrix`（pymupdfの`transform`）、サイズ、色空間、マスク、解像度を持つ`Image`レコード。`hashes=`にはまだ対応していません |
| `page.get_drawings()`, `get_cdrawings()` | `page.content(text_runs=False, images=False).paths` | `segments`、`fill`、`fill_rule`、`stroke`、`bbox`を持つ`Path`レコード。`extended=True`が別の項目として返したクリップ、グループ、マーク付きコンテンツは、各パスの`context`が持ちます |
| `page.get_texttrace()`, `get_bboxlog()`, `get_displaylist()`, `run()`, `get_image_rects()` | `page.content()` | グリフを持つテキストラン、パス、画像を不変の値で返します。[ページの内容](api.md#content)を参照 |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | 同じ | JPEGへの再エンコードのみなので、再サンプリングには`quality`が必要です。`None`ではなく統計情報を返します |

### 描き込みと注釈 { #mapping-drawing }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `page.insert_text()`, `insert_textbox()` | 同じ | `font=`、`fontbuffer=`、`font_language=`も受け付けます。[テキストとフォント](api.md#fonts)を参照 |
| `pymupdf.Font(...)` | `Font.standard(name)`, `Font.from_file(path)`, `Font.from_bytes(data)`, `Font.installed(language=...)` | PostScript名で指定するStandard 14フォント（`"helv"`は`"Helvetica"`）、OpenTypeファイル、インストール済みのフォントextra。pymupdfのほかの内蔵フォントと、`script=`、`ordering=`、`embed=`は使えません |
| `font.text_length(text, fontsize)`, `pymupdf.get_text_length(text, fontname, fontsize)` | `font.text_width(text, size)` | `size`に既定値はありません。`font.text_height(text, size, width=...)`は、折り返したテキストを`canvas.textbox`が描くとおりに測ります |
| `font.has_glyph(ord(c))` | `font.has_glyph(c)` | 文字そのものを受け取り、グリフ番号ではなく真偽値を返します |
| `pymupdf.Base14_fontnames`, `Base14_fontdict` | `typing.get_args(pylopdf.StandardFontName)` | `Font.standard()`が受け付ける14のPostScript名 |
| `page.insert_image(rect, filename=/stream=/pixmap=)` | 同じ | JPEG、PNG、`Pixmap`。ほかの形式はPillowで変換してください。同じ画像を2回挿入しても1回だけ格納されるので、`xref=`は不要です |
| `page.show_pdf_page(rect, docsrc, pno)` | 同じ | 取り込み元は同じ文書でもかまいません |
| `page.new_shape()`, `shape.draw_*`, `finish()`, `insert_text()`, `commit()` | `with page.draw() as canvas:` | キャンバスはパスとテキストを呼び出した順に描き、ブロックが終わったときに書き込みます。`finish()`のスタイルは各呼び出しの`fill=`と`stroke=`になります。[キャンバス](api.md#canvas)を参照 |
| `page.draw_line()`, `draw_rect()`, `draw_circle()`, `draw_oval()`, `draw_polyline()`, `draw_bezier()` | `canvas.line()`, `rect()`, `circle()`, `ellipse()`, `polygon(close=False)`, `bezier()` | `color=`、`width=`、`dashes=`、`lineCap=`、`lineJoin=`は`stroke=Stroke(paint=Color(...), ...)`に、`fill=`は`Color`に、不透明度は`Color`のアルファになります。`rect(radius=)`はポイント単位で、pymupdfでは短い辺に対する割合です |
| `page.draw_quad()`, `draw_sector()`, `draw_curve()`, `draw_squiggle()`, `draw_zigzag()` | `canvas.polygon()`, `canvas.path(PathBuilder().sector(...))`, `canvas.bezier()`, `PathBuilder().wave()`, `PathBuilder().zigzag()` | 形状は`PathBuilder`で組み立てます。扇形の角度はx軸から時計回りの度数です |
| `page.annots()`, `page.first_annot`, `annot.type`, `rect`, `info`, `colors`, `flags`, `parent` | `page.annotations`、`annotation.type`、`rect`、`contents`などのテキスト、`color`、`interior_color`、`flags`、`page` | 型付きのプロパティを持つ`Annotation`ハンドルです。`type`は`AnnotationType`のメンバで、`info`と`colors`の辞書はプロパティになります。リンクとウィジェットは含まれず、ポップアップはその注釈に属します。プロパティは現在は読み取り専用です |
| `page.add_highlight_annot(quads)`, `page.delete_annot(annot)` | `page.annotations.add(Highlight(quads))`、`del page.annotations[i]` | `color`は`Color`で、`content`は`contents`になります。注釈を削除するとそのポップアップも削除されます |
| `page.get_links()`, `page.links()`, `page.first_link`, `page.load_links()` | `page.links` | 辞書や`Link`オブジェクトの連鎖ではなく、`rect`と`target`を持つ`Link`ハンドルのシーケンス |
| `page.insert_link(link)`, `page.update_link(link)`, `page.delete_link(link)` | `page.links.add(rect, target)`、`link.rect`と`link.target`への代入、`del page.links[i]` | リンク先はしおりと同じで、URI文字列、`Destination(page, point, zoom)`、`RemoteDestination(file, page)`、`Launch(file)`、`NamedAction(name)`のいずれかです。`LINK_*`の種類の代わりにリンク先に`isinstance`を使います |
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
- **古いページは例外を送出します。** ページを追加・削除・並べ替えた後、それ以前に取得した`Page`は、黙って別のページを指す代わりに`StalePageError`を送出します。`doc.pages[i]`で取得し直してください。
- **例外。** 基底クラスは`ValueError`のサブクラスである`PdfError`なので、`except ValueError`はそのまま動きます。読めるPDFではない入力は、空の入力も含め、pymupdfが`FileDataError`や`EmptyFileError`を送出するところで`MalformedPdfError`を送出します。存在しないファイルはPython組み込みの`FileNotFoundError`を送出するので、`except pymupdf.FileNotFoundError`は`except FileNotFoundError`に書き換えてください。
- **回転したページ。** 抽出・検索・描き込み・レンダリングはすべて、回転したページも含め、表示されたページの座標を使います。
- **ページ下端付近の`insert_text`。** pylopdfはどの行が収まるかをページの見える領域（`page.rect`）で判断しますが、pymupdfは回転前のページの下端までで測ります。通常の回転していないページでは両者は一致します。
- **フォントの扱いは厳格です。** pylopdfは黙って別のフォントを使わず、例外を送出します。たとえば、フォントに文字がない場合や、`is_bold=True`でインストールされていない太字を求めた場合です。
- **フォームの結合。** `doc.pages.insert()`と`extend()`は、pymupdfの既定の`widgets=True`と同じく、常にフォームフィールドをコピーします。受け取る側の文書に同じ名前のフィールドがあれば`name+1`、`name+2`のように改名するので、同じフォームのコピーはそれぞれ別の値を持ちます。
- **表とMarkdown。** `tables()`は既定で罫線で描かれた表を検出します。罫線のない表には`strategy="text"`を渡してください。`markdown()`は検出した表を読み順の位置に置き、周囲の段落からその表のテキストを取り除きます。

## 実装しない機能: 代わりに使うもの { #deliberate-scope }

| pymupdfの機能 | 代替 |
|---|---|
| Story API、`insert_htmlbox`（組版） | [typst](ecosystem.md#typesetting) |
| 電子署名 | [pyHanko](ecosystem.md#signatures) |
| 増分保存 | 予定しています。それまではpylopdfはファイル全体を書き直し、署名はpyHankoが扱います |
| 画像やCBZアーカイブをPDFに変換する | 予定しています。それまではPillowやimg2pdfで変換してください |
| XPSやEPUBを開く | 予定はありません。pylopdfはPDFのライブラリです |

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
page = doc.pages[0]
page.annotations.add(pylopdf.Highlight(page.search("total")))  # リスト全体を受け取る
page.pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", dedupe=True, renumber=True)
```
