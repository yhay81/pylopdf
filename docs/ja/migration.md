---
title: pymupdfからの移行
description: pymupdfの処理をpylopdfへ移し、型・挙動・スコープの意図的な違いを理解します。
---

# pymupdf からの移行

pylopdf は pymupdf「風」であって、ドロップイン互換ではありません。ただし移行コストを決めるデータ形状 — `"words"` タプルの並び・`"dict"` の構造・`search_for → list[Rect]` — は pymupdf に合わせているため、抽出やページ操作のコードは小さな修正で移植できます。このページでは「そのまま動くもの」「変わったもの」「あえて実装せず連携で解決するもの」をまとめます。

!!! note
    pylopdf が扱うのは **PDF のみ**です。pymupdf の XPS / EPUB / 画像を開く機能は対象外です。

## 自動移行 { #codemod }

`python -m pylopdf.migrate` は pymupdf のプログラムをその場で書き換えます。`migrate` extra が入れる LibCST が必要です。

```bash
pip install "pylopdf[migrate]"
python -m pylopdf.migrate src/
```

`fitz` と `pymupdf` の import を改名し、機械的に置き換えられるもの — ページ番号をずらす `get_toc`/`set_toc`、`embfile_*`、`xref_*` のストリーム読み出し、`copy_page`、その場で値を変える `Rect`/`Point`/`Matrix` のメソッド — を書き換え、人手が要る文の上には `# TODO(pylopdf): …` コメントを付けて、その一覧を`path:line: message` の形で出力します。`--diff` は書き込まずに差分を表示し、`--check` は変更が生じるファイルがあると終了コード 1 を返します。受け手の型は分からないので差分を確認してください。実行時に pylopdf にない pymupdf の名前を参照すると、置き換え先、またはまだ対応していないことを示す `AttributeError` が発生します。

## 対応表 { #mapping }

| pymupdf | pylopdf | 備考 |
|---|---|---|
| `import pymupdf`（`fitz` は旧名） | `import pylopdf` | |
| `pymupdf.open(path)` / `open(stream=…)` / `open("pdf", data)` | 同じ | `open`は`Document`そのもの。`filetype=`は`"pdf"`のみ。`password=`はkeyword-onlyでUTF-8 127 byte上限 |
| `doc[i]`・`len(doc)`・イテレーション | 同じ | 0 始まり・負数可 |
| `doc.metadata` / `set_metadata` | 同じ | キー名も同じ |
| `page.get_text()` | 同じ | オプションは `text` / `words` / `blocks` / `dict` / `rawdict` |
| `page.search_for(t)` | 同じ。加えて`max_hits=4096` | 上限付き`list[Rect]`。検索語は4,096 byte、`max_hits=None`で解除、`quads=`は無い |
| `page.get_pixmap(matrix=pymupdf.Matrix(2, 2))` / `doc.get_page_pixmap(pno, ...)` | 同じ | 既定は不透明RGB、`alpha=True`でRGBA。`dpi=`が優先。拡大縮小のmatrixのみで、`colorspace=`と`annots=`はまだ無い |
| `page.get_svg_image()` | 同じ | `matrix`は恒等のみ。`text_as_path=False`は例外 |
| `Point(...)`, `Matrix(...)`, `p * m`, `m1 * m2`, `~m` | 同じ | 不変のNamedTuple。座標は有限値に限る |
| `p.transform(m)` / `m.invert()` | `p = p * m` / `m = ~m` | その場で値を変えるメソッドは無い。特異な行列で割ると例外 |
| `r.include_point(p)` / `r.include_rect(o)` / `r.intersect(o)` / `r.normalize()` / `r.transform(m)` | `r = r \| p` / `r = r \| o` / `r = r & o` / `r = r.normalized()` / `r = r * m` | 矩形とquadは不変。`q.transform(m)`は`q = q * m`になる |
| `m.prerotate(deg)` / `m.prescale(sx, sy)` / `m.preshear(sx, sy)` / `m.pretranslate(tx, ty)` | `m = Matrix(deg) * m` / `m = Matrix(sx, sy) * m` / `m = Matrix(sx, sy, 1) * m` / `m = Matrix(1, 0, 0, 1, tx, ty) * m` | 結果を代入する。合成では左の行列が先に適用される |
| `pix.samples / width / height / n / alpha / stride / colorspace / save()` | 同じ | RGB、`alpha=True`ではstraight-alpha RGBA。pylopdfの上限付き`tobytes(max_size=64 MiB)`とstreaming `save(filename)`はPNGを生成し、`save`は`.png`が必須 |
| `page.get_images(full=)` / `page.get_image_info(xrefs=)` / `doc.extract_image(xref)` | 同じ | `extract_image`はDCTをJPEGのまま返し、ほかのfilterはPNGに復号。`get_image_info(hashes=True)`はdigest対応まで例外 |
| `page.annots()` / `annot.type` / `annot.rect` / `annot.info` / `annot.colors` / `annot.flags` | 同じ | 現在は読み取り専用。`rect`は表示座標 |
| `page.add_highlight_annot(quads)` / `page.insert_link(link)` | 同じ | `Quad`は外接矩形として扱う。`insert_link`は`LINK_URI`のみ |
| `page.get_drawings()` | 同じ | 型付きpath辞書。line/cubicと主要paint/stroke属性。`extended=`のclip/group階層は非対応 |
| `doc.rewrite_images(dpi_threshold=, dpi_target=, quality=)` | 同じ | maskのない安全なDeviceGray/DeviceRGB DCT/Flate rasterをJPEGへのみ変換するため、縮小には`quality` 1–100が必要。`set_to_gray`と`options`は例外、bitonal画像はスキップ。`None`ではなく件数を返す |
| `doc.select`・`delete_page(s)`・`fullcopy_page`・`new_page` | 同じ | `delete_pages`はpymupdfの形式（番号1つ、番号2つ、`from_page=`/`to_page=`、iterable）を受け付ける。`select` の重複指定は複製になる。select／deleteは削除したページのフォームwidget、フィールド、タグ付き構造の内容も取り除く |
| `doc.copy_page(pno, to)` | `doc.fullcopy_page(pno, to)` | pylopdfはページオブジェクトを共有しない |
| `doc.insert_pdf(docsrc, from_page=, to_page=, start_at=)` | 同じ | pymupdfの範囲規則（負の`from_page`は最初のページ、負の`to_page`は最後のページで、どちらも範囲内に収める）。取り込んだページのフォームフィールドを登録し、衝突する最上位の名前には`+N`を付ける。`widgets=` / `join_duplicates=`はない |
| `doc.get_toc()` / `set_toc(toc)` | `doc.get_outline()` / `set_outline(entries)` | `OutlineEntry(level, title, page)` の `page` は 0 始まり（pymupdf は 1 始まり） |
| `doc.save(garbage=4, deflate=True)` | 同じ | pymupdfと同じ0〜4の段階。ページ、注釈、フォームフィールド、構造要素、レイヤーは統合しない |
| `doc.save(encryption=…, user_pw=…)` | 同じ | `PDF_ENCRYPT_NONE`（既定）と`PDF_ENCRYPT_AES_256`のみで、ほかの方式は例外。`PDF_ENCRYPT_NONE`でのpasswordは無視せず例外。各passwordはUTF-8 127 byte上限 |
| `doc.needs_pass` / `authenticate()` | 同じ | 戻り値の意味（0/1/2/4/6）も同じ |
| `page.rect / rotation / set_rotation` | 同じ | |
| `page.insert_image(rect, filename= / stream= / pixmap=, rotate=)` | 同じ | JPEGパススルー、PNG透過、RGBA `Pixmap`の直接再利用、時計回りの右角回転。その他の符号化形式はPillowで変換。同一入力は画像XObjectを共有するため`xref=`による再利用は不要 |
| `page.show_pdf_page(rect, docsrc, pno)` | 同じ | 同一文書はネイティブな編集前snapshotから取り込むため、serialize/openによる複製は不要。編集していない取り込み元の再配置はForm XObjectを共有 |
| `page.insert_text(point, text, fontsize=, lineheight=, fontname=, fontfile=)` | 同じ。加えて `fontbuffer=` / `fontindex=` / `font_language=` | 行の位置と数え方はpymupdfと同じ。[テキスト挿入](#behavioral-differences)を参照 |
| `page.insert_textbox(rect, text, align=, lineheight=)` | 同じ。任意の `fontfile=` / `fontbuffer=` / `font_language=` に対応 | 同じprovider自動選択とUAX #14折り返し。行の位置と収まるかどうかの判定はpymupdfと同じ。負の戻り値なら描画しない |
| `pymupdf.Font(fontname=, fontfile=, fontbuffer=)` / `font.text_length(text, fontsize)` / `font.has_glyph(chr)` | 同じ。加えて`language=`・`family=`・`weight=` | Standard 14の名前、OpenTypeのファイル、導入済みの書体extra。[書体](#behavioral-differences)を参照 |
| `page.add_highlight_annot(...)` | 同じ | 外観ストリームを常に生成 |
| `doc.embfile_add / names / get / del / upd / info / count` | `doc.attachments.add(name, data, description=…)`、`list(doc.attachments)`、`doc.attachments[name].read()`、`del doc.attachments[name]`、`.write(data)`、handle のプロパティ、`len(doc.attachments)` | 存在しない名前は `KeyError`。`desc=` は `description=` |
| `doc.get_page_labels / set_page_labels`・`page.get_label` | 同じ | |
| `page.widgets()` / Widget オブジェクト | `doc.get_form_fields()` / `doc.set_form_field(name, value, fontfile=)` | ドキュメント単位。テキスト／選択／checkbox／radioのネイティブ外観 |
| `page.get_textpage_ocr(...)` | `page.get_text_ocr(...)` / `page.apply_ocr(...)` | `pylopdf[ocr]`によるオフライン純Rust PP-OCR。任意の位置付き結果には`insert_ocr_text_layer`も利用可能 |
| `page.new_shape()` / `shape.draw_*` / `shape.finish()` / `shape.insert_text()` / `shape.commit()` | 同じ | 確定1回につきForm XObject 1つで、書体は1度だけ埋め込む。`with page.new_shape() as shape:`は正常に抜けると確定。`finish()`で仕上げていない経路の`commit()`と、何も描かない`finish()`は例外。`morph=`と`oc=`は使えない |
| `page.draw_line()` / `draw_rect()` / `draw_circle()` / `draw_polyline()` などの`draw_*` | 同じ | 線や色の指定はキーワード専用で、終点の`Point`を返す。`morph=`と`oc=`は使えない |
| `pymupdf4llm.to_markdown(doc)` | `doc.to_markdown()` | 内蔵・MIT |

## 挙動の違い { #behavioral-differences }

- **テキスト挿入**: 書体の指定がないとき、`insert_text`はWinAnsiエンコーディングのStandard 14書体、または導入済みのscript／locale書体providerを使います。その他の指定は字形処理してsubset埋め込みします。行の分け方・位置・頁の下にはみ出す行の除外はpymupdfと同じで、描いた行数を返します。頁の下端は表示座標の`page.rect`の下端で、pymupdfは回転前の座標で測ります。
- **書体**: `Font`はStandard 14の名前と略称、OpenTypeのファイル、`language=`や`family=`による導入済みの書体extraを受け付けます。pymupdfのほかの内蔵書体（`"cjk"`、`"figo"`など）と`script=`・`ordering=`・`embed=`は使えません。`is_bold=`・`is_italic=`・`is_serif=`は導入済みのextraの書体を完全一致で選び、pymupdfが無視する`fontname`や書体ファイルとの組み合わせでは例外になります。keyword-onlyの`weight=`は、可変フォントのファイルの太さの軸からインスタンスも選びます。書体は`insert_text` / `insert_textbox`に`font=`で渡します。`text_length`はカーニングを含み、`ascender`／`descender`はpymupdfと同じ値を返し、`insert_text`と`insert_textbox`はこの値で行を置きます。

- **座標系**はどちらも左上原点の表示空間。pylopdf では回転ページでも抽出・検索・描き込み・レンダリングが一貫して同じ座標になります。
- **型**: `Point`、`Matrix`、`Rect`、`IRect`、`Quad`は不変のNamedTupleです。`Point`と`Matrix`は幾何的な算術、行列の乗算と除算に対応し、タプルとしての添字、アンパック、等値比較、ハッシュも保ちます。特に`Point(0, 0)`は偽になり、`p + other`は座標を足し、`p * scalar`はタプルを繰り返す代わりに拡大縮小します。その場で値を変える幾何メソッドは無いため、演算子の結果を代入します。`Rect`、`IRect`、`Quad`も同じくpymupdfに従います。`r | p`は点を含め、`r & other`は交差を取り、`r * m`は変換し、`p in r`は包含を判定し、`width`と`height`は負になりません。`r.normalize()`は`r = r.normalized()`と書きます。タプルと違い、`r + (1, 2, 3, 4)`は座標を足し、`Rect(0, 0, 0, 0)`は偽です。`get_pixmap(matrix=)`は拡大縮小の行列を受け付け、回転とせん断は例外になります。
- **古い Page**: 構造変更（削除・挿入・並べ替え）後に古い `Page` を使うと、黙って別ページを指す代わりに `StalePageError` になります。`doc[i]` で取得し直してください。
- **例外**: 基底は `PdfError`（`ValueError` のサブクラス）。`PasswordError` /
  `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` がそれを細分化します。`except ValueError` は動き続けます。pymupdfの`FileDataError`と`EmptyFileError`は同じ名前で、`RuntimeError`も継承します。存在しないファイルはPython組み込みの`FileNotFoundError`になるため、`except pymupdf.FileNotFoundError`は`except FileNotFoundError`に書き換えます。
- **資源ポリシー**: `DocumentLimits.web()`はrendering／extractionへ渡す完全なPDF snapshotを64 MiB、累積位置付きtextを65,536 glyph recordに制限します。独自ポリシーでは`max_interpretation_size`と`max_text_glyphs`を指定でき、`None`は互換性のある無制限動作です。
- **`get_text` のオプション**は `text` / `words` / `blocks` / `dict` / `rawdict` のみ（`html` / `xml` は無し）。スパン辞書は埋め込みフォントについて`font` と pymupdf 互換の `flags`（bold/italic/serif/mono）を持ちます。
- **複数カラムのテキスト**は、決定的な空白ガター検出により、各カラム内を上から下へ、カラム間を左から右へ読みます。1 em未満の狭いガターは、両側に十分な本文を持つ行が密に反復するときだけ採用します。整列したラベル、ドットリーダー、区切りが複数ある密な行は行優先の順序を保ちます。確定した境界は、scan/OCR text layerの小さな水平方向のずれに追従します。
- **`Page.find_tables()`** は線または細い塗り矩形から軸平行の罫線グリッドを再構築し、矩形の結合セルにも対応します。`strategy="text"` を指定すると、高信頼な罫線なし表を検出します（整列した複数カラム本文との幾何的な曖昧さは残ります）。既知の表示座標領域に完全に収まる表だけを返すには`clip=`を使い、罫線なし結果の順位付けには`Table.confidence` / `Table.diagnostics`を確認します。
- **`to_markdown()`** は完全な罫線表を読み順に挿入し、周囲の本文からセル文字を除外します。`table_strategy="text"` で保守的な罫線なし候補を追加でき、`None` で表変換を無効にできます。
- **フォーム記入**は値とネイティブ外観を書き込み、pylopdfと外部ビューアの両方で描画されます。WinAnsiはHelveticaで自動縮小され、UnicodeはOpenTypeフォントを指定するかscript／locale font extraを導入します。地域別の漢字字形は`font_language=`で指定します。combテキスト欄は継承された`MaxLen`と整列を尊重します。リッチテキスト、pushbutton、署名は対象外です。
- **フォームの結合**では、`insert_pdf()`がpymupdfの既定`widgets=True`と同様に取り込んだページのフィールドを常に登録します。取り込み先で既に使われている最上位の名前はqpdfと同じく未使用で最小の`name+N`になり、同じフォームを結合した各コピーは独立した値を持ちます。それらを1つのフィールドに結合する`join_duplicates=`はありません。ページ範囲を指定すると、他のページのwidgetと、widgetがなくなったフィールドは取り込みません。
- **CJK 縦書き**は保守的に検出し、列内を上から下、列間を右から左へ読みます。ルビ、割注、縦中横などの混在組版は解釈しません。

## あえて実装しないもの — エコシステムで解決 { #deliberate-scope }

| pymupdf の機能 | pylopdf での答え |
|---|---|
| Story API / `insert_htmlbox`（組版） | typst（typst-py 経由）— [レシピ](ecosystem.md) |
| 電子署名 | pyHanko（MIT）— [レシピ](ecosystem.md) |
| インクリメンタル保存 | 現在は非対応。pylopdfはファイル全体を書き直す。インクリメンタル保存は対応予定。署名用途はpyHankoが担う |
| XPS / EPUB / CBZ / 画像を開く | 対象外 — PDF 専用 |

## 移植例 { #worked-example }

```python
# pymupdf
import pymupdf
doc = pymupdf.open("in.pdf")
page = doc[0]
for rect in page.search_for("合計"):
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
page.add_highlight_annot(page.search_for("合計"))   # リストごと渡せる
page.get_pixmap(matrix=pylopdf.Matrix(2, 2)).save("page.png")
doc.save("out.pdf", garbage=4, deflate=True)
```
