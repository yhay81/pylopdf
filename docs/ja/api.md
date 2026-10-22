---
title: APIリファレンス
description: pylopdfのDocument、Page、Font、Shape、Pixmap、幾何型、低レベルオブジェクト、例外の一覧。
---

# APIリファレンス

このページでは、公開APIを用途別に一覧します。すべての関数とクラスにはdocstringもあります。詳細は`help(pylopdf.Document)`で確認してください。

全体に共通する約束事:

- **ページ番号は0始まり**です。しおりも同じです。負のインデックスは末尾から数えます。
- **座標**の単位はポイント（1/72インチ）で、表示されたページの左上が原点です。ページの回転と表示領域（CropBox）を適用した座標で、抽出・検索・描き込み・レンダリングが共通して使います。
- **矩形**は[`Rect`](#rectangles)で返します。矩形や点を受け取る引数には、ただのタプルも渡せます。
- **サイズの上限。** 大量のデータを読んだり生成したりしうるメソッドは、安全な既定値を持つ`max_size`（または同様の）引数を取ります。信頼できる入力では`None`を渡すと上限を外せます。拒否すると[`LimitError`](security.md#error-codes)を送出します。
- **pymupdfの名前はpymupdfの意味のまま。** pylopdfが異なる箇所は[移行ガイド](migration.md)に記載しています。

## Document { #document }

```python
pylopdf.open(source, /, *, password=None, limits=None)
pylopdf.Document(*, limits=None)
```

`open`はパス（`str`または`os.PathLike`）か、ファイルの中身（`bytes`、`bytearray`、`memoryview`）からPDFを読み込みます。ファイルオブジェクトは、`pylopdf.open(file.read())`のように先に読み出してから渡してください。`Document()`は空のPDFを作ります。`limits`には信頼できない入力向けの[`DocumentLimits`](security.md#untrusted-pdfs)ポリシーを渡します。文書はコンテキストマネージャとして使うか、`close()`を呼んでください。

### 開く・調べる・保存する { #document-io }

| メンバ | 用途 |
|---|---|
| `pages` | ページ。[ページ](#document-pages)を参照 |
| `metadata` / `set_metadata(m)` | タイトル・作成者などのInfo項目と、`format`・`encryption`。`set_metadata`は値が空のキーを削除し、`{}`ですべて削除します |
| `needs_password`, `unlock(password)` | 文書がロックされているかどうかと、その復号。`unlock`は`Access.USER`か`Access.OWNER`を返し、パスワードが違えば`PasswordError`を送出します |
| `is_repaired` | 開く際に壊れた相互参照データを修復したかどうか |
| `permissions` | 文書が許可する操作を表す`Permissions`フラグ。ロックされている間は何もなく、暗号化されていない文書やオーナーパスワードで開いた後は`Permissions.ALL`、それ以外は暗号化が許可するフラグです。フラグは`Permissions.PRINT in doc.permissions`で調べます |
| `is_dirty` | 開いてから文書を編集したかどうか。pymupdfと同じく、新しい空の文書は最初から編集済みで、保存してもリセットされません |
| `is_closed`, `path` | 文書が閉じられているかどうかと、開いたファイルを表す`pathlib.Path`。bytesから開いた文書と空の文書では`None` |
| `limits`, `complexity` | 文書を開いたときのポリシーと、処理するかどうかを判断するための軽量な構造情報（ページ、オブジェクト、ストリーム） |
| `save(path, /, *, compress=True, prune=True, dedupe=False, renumber=False, object_streams=False, encryption=None)` | PDFを書き出します。ファイルは書き込みがすべて成功してから置き換えます |
| `to_bytes(..., max_size=512 MiB)` | 同じ出力を`bytes`で |
| `close()` | 文書を解放します。`with`を使えば自動で行います |

保存は既定で、Flateで小さくなるストリームを圧縮し、どこからも参照されていないオブジェクトを削除します。`compress=False`と`prune=False`を指定すると、それらをそのまま残します。`dedupe=True`は重複するオブジェクトとストリームを統合し、`renumber=True`は残ったオブジェクトに連番を振り直し、`object_streams=True`はオブジェクトを圧縮オブジェクトストリームにまとめます。これらのオプションは出力だけでなく文書そのものを変更します。暗号化するには`encryption=Encryption(user_password=..., owner_password=..., permissions=...)`を渡します。AES-256で暗号化して書き出し、オーナーパスワードを省略するとユーザーパスワードと同じになります。メモリ上の文書は暗号化されないままです。

```python
doc.save("out.pdf", dedupe=True, object_streams=True)
doc.save(
    "locked.pdf",
    encryption=pylopdf.Encryption(
        user_password="secret",
        owner_password="owner-secret",
        permissions=pylopdf.Permissions.PRINT,
    ),
)
```

### ページ { #document-pages }

`doc.pages`は[`Page`](#page)ビューのシーケンスです。`len(doc.pages)`、`doc.pages[i]`（負のインデックスは末尾から数えます）、`doc.pages[2:5]`のようなスライス（リストを返します）、イテレーションに対応します。

| メンバ | 用途 |
|---|---|
| `doc.pages.new(size=(595, 842), *, index=None)` | 白紙ページを追加します。既定はA4で、`index`を指定しなければ末尾に追加します |
| `doc.pages.insert(index, pages)`, `doc.pages.extend(pages)` | 1ページまたはページのシーケンスのコピーを、リンクやフォームフィールドとともに挿入または末尾に追加します。ページはこの文書のものでも別の文書のものでもかまいません |
| `del doc.pages[i]`, `del doc.pages[a:b]` | ページを削除 |
| `doc.pages.select(indexes)` | 指定したページを指定した順に残します。同じインデックスを繰り返すとページを複製します |
| `doc.pages.move(index, to)` | ページを移動し、インデックス`to`の位置に置きます |
| `doc.pages.labeled(label)` | ラベルが`label`のページのインデックス |
| `get_outline()` / `set_outline(entries)` | 目次（しおり）を`OutlineEntry(level, title, page)`の項目として読み取り・置換 |
| `get_page_labels()` / `set_page_labels(labels)` | 前付けのローマ数字のようなページラベルの範囲 |

```python
merged = pylopdf.Document()
for path in ["a.pdf", "b.pdf"]:
    merged.pages.extend(pylopdf.open(path).pages)
del merged.pages[0]                         # 最初のページを削除
merged.pages.insert(0, merged.pages[-1])    # 最後のページを先頭にコピー
merged.pages.select(range(len(merged.pages) - 1, -1, -1))  # 順序を逆にする
```

`insert`と`extend`は`list.insert`と同じようにインデックスの位置を受け取るので、末尾を超えるインデックスを指定すると末尾に追加します。1回の呼び出しで渡すページはすべて1つの文書のもので、別の文書のページは1回の呼び出しにつき各ページ1度までです。別の文書のページは、リンク、注釈、フォームフィールドとともに取り込みます。すでに使われているフィールド名には、使われていない最小の`+N`の接尾辞を付けます。同じ文書のページのコピーは新しいページオブジェクトで、どちらかが編集されるまでは元のページとコンテンツストリームとリソースを共有します。

ページを追加・削除・並べ替えると、それ以前の`Page`ビューは古くなります。古いビューを使うと`StalePageError`を送出し、ページを変更しながら`doc.pages`をイテレーションすると`RuntimeError`を送出します。ページを削除・選択すると、削除したページだけを指していたリンク、しおりの項目、フォームフィールド、タグ付きPDFの構造も削除します。ただし、そのページの内容のコピーをすべて消すわけではないので、安全な墨消しにはなりません。

### テキストとMarkdown { #document-text }

| メンバ | 用途 |
|---|---|
| `text(pages=None)` | 全ページ、または指定したページのプレーンテキストを1つの文字列で |
| `markdown(pages=None, *, table_strategy="lines", max_size=64 MiB)` | 見出し、リスト、強調、段組みの読み順、CJKの縦書き、表を含むMarkdown |

`table_strategy="lines"`は罫線で描かれた表を変換し、`"text"`は罫線のない表も、明確に揃っていれば変換します。`None`で表の変換を無効にします。

同じ行の単語間の空白は、フォントや書体が変わっても`text()`と同じように扱います。空白は強調記号の外側に置き、結合文字は元の文字から切り離しません。

### レンダリングと画像 { #document-rendering }

| メンバ | 用途 |
|---|---|
| `render(pages=None, *, dpi=None, matrix=None, alpha=False, background=None, annotations=True, workers=None, max_size=512 MiB)` | 全ページ、または指定したページのPNGのbytesを順番どおりに。並列にレンダリングします |
| `images` | オブジェクト番号ごとの画像オブジェクト。[画像](#images)を参照 |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0, ...)` | 画像を縮小・再圧縮してファイルを小さくします |
| `set_fallback_font(font, kind="sans", index=0, *, font_language="ja")` | フォントが埋め込まれていない中国語・日本語・韓国語のテキストを表示するフォント。CJKのextraを入れると自動で設定されます |

`rewrite_images`はpymupdfと同じキーワードに従います。描かれた位置での解像度が`dpi_threshold`を超える画像を`dpi_target`に再サンプリングし、`quality`（1–100）でJPEGに再エンコードします。対象はマスクのない単純な8ビットのグレーまたはRGB画像だけで、再エンコードしても小さくならない画像はそのまま残し、統計情報を返します。

### フォーム・添付ファイル・その他のデータ { #document-data }

| メンバ | 用途 |
|---|---|
| `get_form_fields()` | すべてのフォームフィールドと、その名前・種類・値・選択肢 |
| `set_form_field(name, value, *, font=, fontfile=, fontbuffer=, fontindex=, font_language=)` | フィールドに記入し、見た目を生成し直します。[フォーム](#forms)を参照 |
| `attachments` | 埋め込みファイルのマッピング。[添付ファイル](#attachments)を参照 |
| `get_pdfa_claim()` | ファイルが宣言しているPDF/Aのパートと適合レベル（`(2, "B")`など）。宣言を読むだけで、検証はしません |
| `get_xml_metadata(*, max_size=1 MiB)` / `set_xml_metadata(metadata, *, max_size=1 MiB)` / `del_xml_metadata()` | XMPメタデータのパケットをテキストで（ない場合は`""`）。`set_metadata`はこれを変更しません |
| `language_tag` / `set_language_tag(tag)` | 文書の言語を表す`zh-TW`や`sr-Latn-RS`のような完全なBCP 47タグ、または`None`。`set_language_tag(None)`で削除し、形式の正しくないタグは例外になります |
| `pagemode` / `set_pagemode(mode)`, `pagelayout` / `set_pagelayout(layout)` | ビューアーが文書をどう開くか（`"UseOutlines"`、`"TwoColumnLeft"`など）。未設定のときは`"UseNone"`と`"SinglePage"` |
| `markinfo` / `set_markinfo(markinfo)` | タグ付きPDFのフラグを表す`MarkInfo`辞書（ない場合は`{}`）。設定するときは3つのフラグをすべて書き込み、省略したフラグは`False`になります |
| `has_links()`, `has_annots()` | いずれかのページにリンクがあるか、リンクとフォームウィジェット以外の注釈があるか |
| `objects`, `trailer`, `catalog` | PDFオブジェクトへの直接アクセス。[低レベルオブジェクト](#low-level-objects)を参照 |

`language_tag`はpymupdfの`language`に代わるものです。`language`はタグを3文字までしか保持しないため、`zh-TW`と`zh-HK`はどちらも`zh-Hant`として読まれます。XMPを書き込むと、保存時にストリームを圧縮してもパケットは圧縮されないので、ファイルの中からXMPを探すツールでも見つけられます。

## Page { #page }

`Page`は文書の1ページを表すビューです。`doc.pages[i]`で取得します。2つのビューは、同じ文書の同じページを表していて、その間にページの追加・削除・並べ替えがなければ等しくなります。

### テキストと検索 { #page-text }

| メンバ | 用途 |
|---|---|
| `text()` | ページのテキストを読み順で |
| `words()` | 読み順に並べた語を`Word`レコードで |
| `layout(*, chars=False)` | ブロック、行、スパンを`Layout`で。[位置付きテキスト](#positioned-text)を参照 |
| `search(text, *, max_hits=4096)` | 大文字小文字を区別しない検索。`Rect`のリストを返します |
| `tables(*, strategy="lines", clip=None)` | 表を`Table`レコードのリストで。[表](#tables)を参照 |
| `markdown(*, table_strategy="lines")` | このページのMarkdown |
| `ocr(...)`, `apply_ocr(*, words=None, ...)` | OCRと、その結果またはほかのエンジンの単語から作る不可視のテキスト層。[オフラインOCR](ocr.md)を参照 |
| `content(*, text_runs=True, paths=True, images=True, clip=None, annotations=True)` | ページが描くすべてのテキストラン、パス、画像。[ページの内容](#content)を参照 |
| `get_links()` | リンクとそのリンク先 |
| `get_label()` | `"iv"`のようなページラベル |

`Word`は`text`と`bbox`に加えて、ページのレイアウト内での位置を示す`block`、`line`、`index`を持ちます。それぞれ、属するブロックのインデックス、そのブロック内での行のインデックス、その行内での語のインデックスです。

読み順は行と段組みに従い、右から左に書くテキストや、日本語・中国語の縦書きにも対応します。ルビ、割注、向きが混在するテキストは解釈しません。

### レンダリング { #page-rendering }

| メンバ | 用途 |
|---|---|
| `pixmap(*, dpi=None, matrix=None, clip=None, alpha=False, background=None, annotations=True)` | [`Pixmap`](#pixmap)へレンダリング |
| `svg(*, annotations=True, max_size=64 MiB)` | テキストをパスで描いたSVG文字列へレンダリング |
| `resources` | ページのリソースが届くもの。`resources.images`が画像オブジェクトを並べます。[画像](#images)を参照 |

ピクセルマップは既定で72 dpi、白地のRGBで、`alpha=True`でRGBAです。`dpi`と`matrix`はどちらか一方を渡します。行列は`Matrix(2, 2)`のような拡大縮小だけのものでなければなりません。`clip`はページの一部だけをレンダリングし、`annotations=False`は注釈とフォームのウィジェットを描きません。

### ページの幾何情報 { #page-geometry }

| メンバ | 用途 |
|---|---|
| `index`, `document` | 文書内でのページのインデックスと、その文書 |
| `rect` | 回転を適用した、ページの見える領域 |
| `rotation` / `set_rotation(deg)` | 90の倍数で表す表示の回転 |
| `mediabox`, `cropbox` / `set_mediabox(r)`, `set_cropbox(r)` | pymupdfの規約に従ったページボックス |
| `xref`, `obj` | ページオブジェクトの番号と[低レベルハンドル](#low-level-objects) |

### 内容の追加 { #page-content }

| メンバ | 用途 |
|---|---|
| `insert_text(point, text, ...)` | 点を起点にテキストを書きます。[テキストとフォント](#fonts)を参照 |
| `insert_textbox(rect, text, ...)` | 矩形の中に折り返したテキストを書きます |
| `insert_image(rect, *, filename=, stream=, pixmap=, rotate=0, keep_proportion=True, overlay=True)` | JPEG、PNG、`Pixmap`を描き、そのオブジェクト番号を返します |
| `show_pdf_page(rect, docsrc, pno=0, *, keep_proportion=True, overlay=True)` | 別のPDF（または同じPDF）のページをベクタのまま描きます |
| `draw_line`, `draw_rect`, `draw_circle`, `draw_oval`, `draw_polyline`, `draw_quad`, `draw_sector`, `draw_curve`, `draw_bezier`, `draw_squiggle`, `draw_zigzag` | 図形を1つ描きます。`color`、`fill`、`width`、`dashes`などのスタイル指定はキーワード専用です |
| `draw(*, underlay=False)` | パス、テキスト、画像、ページを順に描く[キャンバス](#canvas) |
| `new_shape()` | 複数のパスとテキストを一度に描きます。[Shape](#shapes)を参照 |
| `replace_text(search, replacement, *, default_char=None)` | 単純な（1バイトの）フォントで描かれたテキストを置換 |

同じ画像やページを再び描いても、ファイルには1回だけ格納されます。

### 注釈とリンク { #page-annotations }

| メンバ | 用途 |
|---|---|
| `annots(*, types=None)` | `type`、`rect`、`info`、`colors`、`flags`、`xref`を持つ読み取り専用の`Annot`オブジェクト |
| `add_highlight_annot(quads, *, color=, opacity=0.4, content=None)` | 検索結果など、1つ以上の矩形や四辺形をハイライト |
| `insert_link(link)` | Webリンク（`kind=LINK_URI`）を追加 |

注釈の種類とフラグは`PDF_ANNOT_*`と`PDF_ANNOT_IS_*`の定数です。

## 位置付きテキストとグリフ { #positioned-text }

`page.layout()`は、ページの`width`、`height`、`blocks`を持つ`Layout`を返します。各レコードは1つ下の階層を持ち、範囲はすべて表示空間で表します。

| レコード | フィールド |
|---|---|
| `Block` | `text`、`bbox`、`lines` |
| `Line` | `text`、`bbox`、`direction`（ベースラインの単位ベクトル）、`is_vertical`、`spans` |
| `Span` | `text`、`bbox`、`origin`、`size`、`font`、`is_bold`、`is_italic`、`is_serif`、`is_monospaced`、`chars` |
| `Char` | `text`、`bbox`、`origin` |

`layout(chars=True)`は、各スパンの`chars`にそのグリフを入れます。`Char`はグリフ1つ分のテキストをすべて保持するため、"fi"のような合字は両方の文字を保持します。`origin`はグリフのベースラインの始点です。

```python
with pylopdf.open("report.pdf") as doc:
    layout = doc.pages[0].layout(chars=True)
    for block in layout.blocks:
        for line in block.lines:
            for span in line.spans:
                for char in span.chars:
                    print(char.text, char.bbox, char.origin)
```

範囲はグリフの輪郭ではなく、ベースラインとフォントサイズから推定します。pylopdfが間隔から推測した空白は文字として追加しないため、文字をつなげても`text()`と同じになるとは限りません。PDF内にUnicodeの対応がないグリフは、`PylopdfWarning`を出して読み飛ばします。

`layout.to_dict()`は、JSON向けのただの辞書を[pymupdfのdict（文字を含むレイアウトではrawdict）](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs)の形で返します。返すのはテキストブロックだけで、画像やベクタのブロック、色、不透明度、追加のフォントメトリクスは含みません。[オーバーレイの例](https://github.com/yhay81/pylopdf/blob/main/examples/rawdict-overlay.py)は、グリフの範囲と始点をHTMLのプレビューとして描きます。

## ページの内容 { #content }

`page.content()`は、ページが描くものをhayroの解釈どおりに描画順で返します。表示座標の`TextRun`、`Path`、`Image`を並べた`Content`シーケンスで、値はすべて不変です。

```python
with pylopdf.open("report.pdf") as doc:
    content = doc.pages[0].content()
    for run in content.text_runs:
        print(run.text, run.font.name, run.size, run.fill)
        for glyph in run.glyphs:
            print(glyph.text, glyph.origin, glyph.bbox)
    for path in content.paths:
        print(path.bbox, path.fill, path.stroke)
    for image in content.images:
        print(image.xref, image.bbox, image.width, image.height)
```

| 項目 | フィールド |
|---|---|
| `TextRun` | `glyphs`、`font`、`render_mode`、`fill`、`stroke`、`bbox`。`text`と`size`はこれらから求める |
| `Glyph` | `glyph_id`、`text`、`matrix`、`advance`、`bbox`。`origin`と`size`は行列から求める |
| `Path` | `segments`（`MoveTo`、`LineTo`、`CurveTo`、`ClosePath`）、`fill`、`fill_rule`、`stroke`、`bbox` |
| `Image` | `xref`、`width`、`height`、`bits`、`colorspace`、`components`、`is_stencil`、`mask`、`encoded_size`、`resolution`、`matrix`、`bbox` |

どの項目も`blend_mode`、`soft_mask`、`context`を持ちます。`context`は、その項目を囲むクリップ（`clips`）、透明グループ（`groups`）、マーク付きコンテンツ（`marked_content`）を外側から順に並べた`GraphicsContext`で、`opacity`と`clip_bbox`はそれらを合わせた値です。同じコンテキストで描かれた項目は、1つのコンテキストオブジェクトを共有します。

ペイントは`Color`（sRGBとアルファ）、`ShadingPattern`、`TilingPattern`のいずれかです。`Stroke`は`paint`、`width`、`cap`、`join`、`miter_limit`、`dashes`、`dash_offset`を表示空間の大きさで持ちます。テキストランの`font`は、フォントプログラムの`name`、`weight`、スタイルのフラグを持つ`FontRef`です。

- グリフの矩形はアウトラインの正確な外接矩形なので、空白には矩形がありません。Type 3グリフの矩形は、その手続きが描く範囲です。
- クリップを伴う描画モードのテキストは塗りまたは線として描かれ、クリップだけを行うテキストは項目になりません。同じパスの塗りの直後に線を描くと、`B`と同じく1つのパスになります。
- `sh`で描かれたシェーディングは、クリップされた範囲を覆い`ShadingPattern`で塗る`Path`です。
- hayroはすべての画像を、その時点の不透明度、ブレンドモード、ソフトマスクを持つ透明グループの中に描くため、画像のコンテキストはそのグループで終わります。表示範囲全体を覆うクリップは、hayroと同じく含めません。

`text_runs=False`、`paths=False`、`images=False`はその種類の項目を作らずに除きます。テキストの多いページのパスや画像だけを読むなら、何倍も速くなります。`clip=`は外接矩形が指定の矩形に接する項目だけを残し、`annotations=False`は注釈の外観を除きます。1回の呼び出しで読むグリフ、パスとクリップのセグメント、線の破線の長さ、項目は合わせて`max_elements`まで、既定では262,144です。

hayroがまだ報告しない情報はモデルにありません。色の色空間と成分、レイヤー名などマーク付きコンテンツのプロパティ、`FontRef`の元になるフォント辞書、画像マスクのオブジェクトです。これらは[エンジンの作業](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md#engine-work)に挙げています。

## 画像 { #images }

`doc.images`はオブジェクト番号から文書の画像オブジェクトへの対応で、`page.resources.images`はページのリソースが届く画像を、ページが使うForm XObjectのものも含めて1回ずつ並べます。どれもアクセスのたびに文書を読む`ImageObject`です。

| メンバー | 意味 |
|---|---|
| `xref`, `obj` | オブジェクト番号と、[オブジェクトモデル](#low-level-objects)での画像のストリーム |
| `width`, `height`, `bits` | ピクセル単位のサイズと成分あたりのビット数 |
| `colorspace`, `alternate_colorspace` | `"DeviceRGB"`や`"ICCBased"`などの色空間と、インデックス、ICCベース、分版の色空間のベースまたは代替の色空間 |
| `filters` | `("DCTDecode",)`のような、デコードする順のストリームのフィルター |
| `soft_mask`, `mask`, `is_stencil` | 画像オブジェクトとしてのソフトマスクとステンシルマスク、画像自体がステンシルマスクかどうか |
| `extract()` | 画像のデータを`format`、`data`、`resolution`を持つ`EncodedImage`で。JPEGデータは格納されたまま、ほかの画像はPNGで、マスクは含みません |
| `to_pixmap(*, alpha=False)` | 画像のピクセルを[`Pixmap`](#pixmap)で。マスクを含まないRGB、またはマスクが透明度を決めるRGBA |

```python
from pathlib import Path

with pylopdf.open("scan.pdf") as doc:
    for image in doc.pages[0].resources.images:
        encoded = image.extract()
        Path(f"image-{image.xref}.{encoded.format}").write_bytes(encoded.data)
```

画像が描かれる位置はページの内容に属します。`page.content(text_runs=False, paths=False).images`がすべての配置をボックスと行列とともに並べます。

## キャンバス { #canvas }

`page.draw()`は`Canvas`を返します。`with`ブロックの中で使ってください。ブロックの中で描いたものはすべて、ブロックが終わったときに呼び出した順で1つのForm XObjectとして書き込まれます。ブロックが例外を送出した場合は何も書き込まれません。座標はほかの箇所と同じく表示座標です。

```python
from pylopdf import Color, Matrix, PathBuilder, Stroke

navy = Color(0.1, 0.2, 0.4)
with page.draw() as canvas:
    canvas.rect((40, 40, 555, 120), radius=8, fill=Color(0.95, 0.96, 0.97))
    canvas.text("Inspection report", (56, 90), size=20, fill=navy)
    canvas.line((56, 105), (539, 105), stroke=Stroke(paint=navy, width=0.5))
    fit = canvas.textbox((56, 140, 539, 400), body, size=10.5)
    canvas.image((400, 420, 539, 520), "chart.png")
    with canvas.opacity(0.15), canvas.transform(Matrix(1, 0, 0, 1, 0, 600)):
        canvas.page((56, 0, 539, 200), letterhead[0])
```

| メソッド | 描くもの |
|---|---|
| `path(path, *, fill=None, stroke=None, fill_rule=FillRule.NONZERO)` | `PathBuilder`、`Path`項目の形状、または線分のリスト |
| `line`, `rect(rect, *, radius=0)`, `circle`, `ellipse`, `polygon`, `bezier` | それぞれパスを1つ。オプションは`path`と同じです |
| `text(text, at, *, font=None, size=11, fill=..., stroke=None, line_height=None)` | 最初のベースラインが`at`から始まる行 |
| `textbox(rect, text, *, font=None, size=11, align="left", line_height=None, ...)` | `rect`の中で折り返したテキスト。`TextFit`を返します |
| `image(rect, source, *, keep_aspect=True, rotate=0)` | JPEGまたはPNGのパス、エンコード済みのbytes、`Pixmap`のいずれか |
| `page(rect, source, *, keep_aspect=True)` | 開いている任意の文書（この文書も含む）のページ |
| `draw(item)` | [`page.content()`](#content)が返した`Path`項目 |

パスは`Color`の塗りと線を取ります。線は、幅、線端、角の形、マイター限界、破線を持つ`Stroke`か、1ポイントの線を表す`Color`です。どちらも指定しないパスは1ポイントの黒い線で描かれます。色の`alpha`は、その塗りや線を半透明にします。`PathBuilder`は`move_to`、`line_to`、`curve_to`、`quad_to`、`close`で形状を組み立て、`rect`、`ellipse`、`circle`、`polygon`、`sector`、`zigzag`、`wave`で図形をまるごと追加します。どのメソッドもビルダー自身を返すので、呼び出しを連鎖できます。

テキストは[`Font`](#font)で描きます。OpenTypeフォントはHarfRustで字形処理し、何回の呼び出しで使ってもキャンバスごとに1回だけサブセットとして埋め込みます。`font`を指定しない場合、西欧のテキストはHelveticaで、それ以外のテキストはその文字体系に対応するインストール済みの[フォントのextra](getting-started.md#installation)で描きます。テキストは塗り、線、またはその両方で描けます。`textbox`はUnicodeが改行を許す位置で行を折り返します。テキストが収まらないときは何も描かず、`TextFit.spare_height`は不足分を表す負の値になるので、`fit.fits`は偽になります。

次の4つのコンテキストマネージャは、その中で描いたものすべてに適用され、入れ子にできます。

| スコープ | 効果 |
|---|---|
| `clip(path, *, fill_rule=...)` | パスの内側だけに描きます |
| `transform(matrix)` | 描画を変換します。入れ子の変換は内側から先に適用されます |
| `opacity(alpha)` | 描画を1つのグループとして合成し、それを不透明度`alpha`で描きます |
| `blend(mode)` | `BlendMode`を使って、描画をその下にあるものとブレンドします |

`page.draw(underlay=True)`は、ページの既存の内容の下に書き込みます。1つのキャンバスで何度も描いた画像やページ、または文書が以前に配置した画像やページは、1回だけ格納されます。ページは`canvas.page()`を呼び出した時点の状態で描かれます。`draw(item)`は項目のクリップ、グループの不透明度、ブレンドモードを再現しますが、ソフトマスクは再現しません。テキストラン、画像、パターンのペイントはまだ描けません。1つのキャンバスが保持できるパスの点は1,048,576個まで、スコープの入れ子は16段までです。それを超えると`LimitError`を送出し、そのコードは`path_point_count`または`canvas_scope_depth`です。

## 新しい文書 { #create }

`pylopdf.create()`は、krillaが全体を書き出す文書を作り始めます。`builder.page(size)`は新しいページの[キャンバス](#canvas)を返し、そのページは`with`ブロックが終わったときに文書に加わります。`builder.to_bytes()`はファイルを返し、`builder.finish()`はそれを`Document`として開きます。

```python
from pathlib import Path

import pylopdf
from pylopdf import Color, TagRole

font = pylopdf.Font(fontfile="NotoSans-Regular.ttf")
builder = pylopdf.create(standard="pdf/ua-1", language="en", title="Annual report")
with builder.page((595.28, 841.89)) as canvas:
    canvas.bookmark("Summary")
    with canvas.tag(TagRole.HEADING_1, title="Summary"):
        canvas.text("Summary", (56, 80), font=font, size=20)
    with canvas.tag(TagRole.PARAGRAPH):
        canvas.textbox((56, 100, 539, 300), body, font=font, size=11)
    with canvas.tag(TagRole.FIGURE, alt="Revenue by quarter"):
        canvas.image((56, 320, 300, 480), "chart.png")
    canvas.line((56, 800), (539, 800), stroke=Color(0.6, 0.6, 0.6))
Path("report.pdf").write_bytes(builder.to_bytes())
```

| `create()`の引数 | 意味 |
|---|---|
| `standard` | `"pdf/a-1a"`から`"pdf/a-4f"`まで、`"pdf/ua-1"`、またはそれらの複数（`("pdf/a-2a", "pdf/ua-1")`など） |
| `tagged` | `tag`スコープから構造ツリーを作ります。PDF/UAとPDF/Aの`a`レベルでは自動的に有効になります |
| `language`, `title`, `author`, `subject`, `keywords` | 文書のBCP 47の言語とメタデータ |
| `created` | 作成日時。既定は現在時刻で、固定の値を指定すると出力を再現できます |

`standard`を指定した文書は書き出すときに検証されます。違反があるとファイルを書き出さずに`PdfError`を送出し、そのエラーにはPDF/A-1での透明効果や、PDF/UAでのタイトルやしおりの欠落など、krillaが見つけたすべての問題が並びます。意味のある代替テキストのように判断を要する要件は、引き続き利用者自身が満たす必要があります。

新しい文書のキャンバスは、ページのキャンバスと同じものを描けますが、次の点が異なります。

- すべてのフォントを埋め込むので、テキストにはOpenTypeの[`Font`](#font)が必要です。`font`を指定しないテキストはその文字体系に対応するインストール済みのフォントのextraで描きます。西欧のテキストは、krillaがStandard 14フォントを描けるようになるまで`font`が必要です。
- `canvas.page()`はまだほかのページを配置できません。完成した文書のページに描いてください。
- `tag(role, *, alt=None, lang=None, title=None, actual_text=None)`は、ブロックの中で描いたものを構造要素にします。役割は`TagRole`のメンバーで、`PARAGRAPH`、`HEADING_1`から`HEADING_6`、`LIST`と`LIST_ITEM`、`TABLE`、`TABLE_ROW`、`TABLE_DATA`、そして`alt`テキストを伴う`FIGURE`などがあります。スコープは入れ子にでき、スコープの中のテキストは1行ごとにそのスパンになります。どのスコープにも含まれない内容と、`ARTIFACT`スコープの中の内容はアーティファクトになります。
- `bookmark(title, *, level=1, at=(0, 0))`は、ページ上の`at`を表示するしおりの項目を追加します。レベルは一度に1つずつしか深くできません。

ページを追加したあとでも、`finish()`と`to_bytes()`をもう一度呼び出せます。`finish()`が返す`Document`を保存するとlopdfで書き直されるので、krillaが検証したとおりのファイルには`to_bytes()`を使ってください。1つの文書が持てるのはページ65,536枚としおり65,536項目まで、1つのタグのテキストや1つのしおりのタイトルは64 KiBまでです（`tag_text_size`）。

## 表 { #tables }

`page.tables()`は、結合セルも含め、罫線で描かれた表を検出します。`strategy="text"`を指定すると、行と列が明確に揃っている罫線のない表も検出します。揃った段組みの文章が表に見えることがあるため、既定にはしていません。`clip=`は、矩形の中に完全に収まる表だけを残します。

結果は`Table`レコードのリストです。`Table`は`bbox`、`rows`（行ごとのセルのテキスト）、`cells`（各セルの`Rect`を同じ形で）、`diagnostics`、`markdown()`を持ちます。結合セルはそのテキストと矩形を左上のスロットに持ち、それが覆うスロットは`None`です。`diagnostics.confidence`は候補を順位付けするための0–1のスコアで、確率ではありません。その根拠はほかの診断項目で確認できます。

```python
for table in page.tables():
    header, *body = table.rows
    print(table.bbox, header, len(body))
```

## テキストとフォント { #fonts }

### テキストを書く { #writing-text }

```python
page.insert_text(point, text, *, fontsize=11, lineheight=None, font=None,
                 fontname="helv", fontfile=None, fontbuffer=None, fontindex=0,
                 font_language=None, color=(0, 0, 0), overlay=True)
page.insert_textbox(rect, text, *, align=pylopdf.TEXT_ALIGN_LEFT,
                    expandtabs=1, ...)   # フォントの指定は同じ
```

フォントは次のいずれかの引数で選びます。

- 何も指定しない、または`fontname`: テキストに必要ならインストール済みextraのフォント、そうでなければStandard 14フォント（既定は`"helv"`、ほかに`"Times-Bold"`など）。Standard 14フォントは埋め込まれず、西欧（WinAnsi）の文字しか扱えません。
- `fontfile`または`fontbuffer`: OpenTypeフォント。使ったグリフだけを埋め込みます。
- `font`: 読み込み済みの[`Font`](#font)。

1つのフォントがテキストのすべての文字を含んでいる必要があります。pylopdfは文字ごとにフォントを切り替えません。かな、ハングル、注音符号、アラビア文字、ヘブライ文字、デーヴァナーガリー、タイ文字を含むテキストは、対応するインストール済みextraを自動で選びます。漢字だけでは言語を判別できないため、既定では日本語フォントを使います。選ぶには`font_language="zh-CN"`、`"zh-TW"`、`"ko"`、`"ja"`を渡してください。

`insert_text`は文字列を`str.splitlines()`で行に分けるか、1項目を1行とするリストを受け取ります。行の間隔は`lineheight × fontsize`で、`lineheight`の既定値はpymupdfと同じくフォントのアセンダーとディセンダーから決まります。ページの下端より下に出る行は描かず、描いた行数を返します。

`insert_textbox`は、Unicodeが改行を許す位置（UAX #14）でテキストを折り返し、明示的な改行を保ち、タブを展開し、1行に収まらない長い単語は分割します。`align`には`TEXT_ALIGN_LEFT`、`TEXT_ALIGN_CENTER`、`TEXT_ALIGN_RIGHT`、`TEXT_ALIGN_JUSTIFY`を指定します。戻り値は余った高さで、負の値はテキストが収まらなかったことを意味し、そのときは何も描きません。

右から左に書くテキストは正しく字形処理されます。PDFを読み戻すと、すべて右から左の行と、右から左のテキストの中にラテン文字の単語か数字を1つだけ含む行は論理順で、それ以外の方向が混在する行は見た目の順で取り出されます。複雑な文字体系のクラスタの一部、特に長いデーヴァナーガリーの並びは、元のテキストとわずかに異なって抽出されることがあります。

### Font { #font }

```python
pylopdf.Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None,
             is_bold=False, is_italic=False, is_serif=False, weight=None,
             fontindex=0, family=None, allow_regular_fallback=False)
```

| 例 | 読み込むもの |
|---|---|
| `Font("helv")`, `Font("Times-Bold")` | Standard 14フォント |
| `Font(fontfile="NotoSans-Regular.ttf")` | OpenTypeファイル |
| `Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)` | 可変フォントの1つの太さ |
| `Font(language="ja")`, `Font(family="Noto Sans JP")` | インストール済みextraのフォント。`is_serif=True`で明朝体（セリフ体）を選びます |
| `Font(language="he", is_bold=True)` | インストール済み可変フォントのBoldの太さ |

メソッドとプロパティ: `text_length(text, fontsize=11)`（カーニングを含む、描いたときの幅）、`ascender`、`descender`、`has_glyph(chr)`、`name`、`family`、`weight`、`is_bold`、`is_italic`、`is_serif`、`units_per_em`。

`weight`を指定せずに読み込んだ可変フォントは既定のインスタンスを使いますが、それがRegularの太さとは限りません。可変フォントのNoto Sans CJK JPの既定はThinです。Regularにするには`weight=400`を渡してください。描画メソッドの`fontfile`と`fontbuffer`引数も既定のインスタンスを使います。別の太さで描くには、`Font`を`font=`に渡してください。

アラビア語・ヘブライ語・ヒンディー語・タイ語のextraは可変フォントを含むので、軸上の任意の太さを使えます（100–900。Noto Naskh Arabicは400–700）。CJKのextraはスタイルごとにRegularのフォントを1つだけ含みます。そのため`Font(language="ja", is_bold=True)`は`PdfError`を送出します。代わりに太字のファイルを`fontfile=`で読み込むか、`allow_regular_fallback=True`を渡して警告付きでRegularのフォントを受け入れてください。pylopdfがグリフを変形して太字や斜体を真似ることはありません。

pylopdfはシステムのフォントディレクトリを探さず、グローバルなフォント登録の仕組みも持たないため、出力は渡したフォントだけで決まります。

### Shape { #shapes }

`page.new_shape()`は`Shape`を返します。複数のパスとテキストを1つの単位として描くための、pymupdfの仕組みです。

```python
regular = pylopdf.Font("helv")
bold = pylopdf.Font("hebo")
body = "Findings are listed below. " * 20

with page.new_shape() as shape:
    shape.draw_rect((50, 50, 545, 792))
    shape.finish(color=(0.72, 0.78, 0.82), fill=(0.95, 0.96, 0.97), width=0.5)
    shape.draw_circle((80, 140), 7)
    shape.finish(color=(1, 1, 1), fill=(0.83, 0.26, 0.05), width=1.2)
    shape.insert_text((56, 80), "Inspection report", font=bold, fontsize=18)
    shape.insert_textbox((56, 160, 540, 400), body, font=regular, fontsize=10.5)
```

- `draw_*`メソッドはパスの線分を追加し、終点を返します。前の線分の終点から始まる線分は、同じパスの続きになります。
- `finish(*, width=1, color=(0,), fill=None, lineCap=0, lineJoin=0, dashes=None, even_odd=False, closePath=True, fill_opacity=1, stroke_opacity=1)`は、前の`finish`以降に描いたものすべてにスタイルを付けます。色は0から1のグレー、RGB、CMYKのタプルです。
- `insert_text`と`insert_textbox`は`Page`のメソッドと同じように動きます。
- `commit()`でShapeを描きます。パスを順に描き、その上にすべてのテキストを描きます。`with`ブロックは例外なく終わったときに確定します。

各フォントは、何行で使っても確定1回につき1度だけ埋め込まれるので、ページのテキストを1つのShapeで描けばファイルを小さく保てます。pymupdfと異なり、pylopdfは描画を黙って捨てたり黒くしたりせず、`PdfError`を送出します。`commit()`は`finish()`でスタイルを付けていないパスを拒否し、`finish()`は何も描かないスタイルを拒否します。`morph=`と`oc=`にはまだ対応していません。

### フォーム { #forms }

`set_form_field`はテキスト、コンボボックス、リストボックス、チェックボックス、ラジオボタンのフィールドに記入し、それぞれの見た目を生成し直します。西欧（WinAnsi）のテキストはHelveticaで収まるように大きさを調整します。それ以外の文字には`fontfile`、`fontbuffer`、読み込み済みの`font`を渡すか、フォントのextraをインストールしてください。コーム（comb）フィールド、配置、最大文字数を守り、長すぎる値は例外を送出して文書を変更しません。リッチテキスト、プッシュボタンのアクション、署名には対応していません。署名については[エコシステム連携](ecosystem.md#signatures)を参照してください。

## 添付ファイル { #attachments }

`doc.attachments`は名前から`Attachment`オブジェクトへのマッピングです。

```python
doc.attachments.add("data.csv", csv_bytes, description="Source data")
for name in doc.attachments:
    item = doc.attachments[name]
    print(item.filename, item.size, item.description)
data = doc.attachments["data.csv"].read()
doc.attachments["data.csv"].write(new_bytes)
del doc.attachments["data.csv"]
```

`Attachment`には`name`、`creation_date`、`mod_date`もあります。存在しない名前は`KeyError`を送出します。

## 低レベルオブジェクト { #low-level-objects }

`doc.objects`はオブジェクト番号からPDFのオブジェクトへのマッピングで、`doc.trailer`、`doc.catalog`、`page.obj`は辞書のハンドルです。オブジェクトはPDF構文ではなくPythonの値として扱います。

```python
from pylopdf import Name

catalog = doc.catalog
catalog["PageLayout"] = Name("TwoColumnLeft")   # 名前。素のstrはテキスト文字列になる
font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (Hi) Tj ET")
page.obj["Resources"] = {"Font": {"F9": doc.objects[font]}}   # 参照として格納される
page.obj["Contents"] = doc.objects[content]
doc.objects[content].read()                     # デコード済みのストリームデータ
```

| PDFの値 | Pythonの値 |
|---|---|
| null、真偽値、整数、実数 | `None`, `bool`, `int`, `float` |
| 名前 | `Name`。先頭のスラッシュを除いた`str` |
| 文字列 | `String`。`.text`でデコードできる`bytes` |
| 配列、辞書 | `Array`（変更可能なシーケンス）、`Dictionary`（変更可能なマッピング） |
| ストリーム | `Stream`。`read(*, decode=True)`と`write(data, *, filter="FlateDecode")`を持つ辞書 |

変更はすぐに反映されます。存在しないオブジェクトへの参照は`None`として読めます。ハンドルを別のオブジェクトの中に格納すると、そのオブジェクトへの参照が格納されます。ハンドルは、オブジェクトが削除された後や、`save(renumber=True)`でオブジェクトの番号が付け直された後には`StaleObjectError`を送出します。pylopdfは、編集結果がPDFとして意味をなすかどうかを検査しません。

## 幾何型 { #geometry-types }

`Point`、`Matrix`、`Rect`、`IRect`、`Quad`は不変の名前付きタプルなので、インデックス参照、アンパック、比較はタプルと同じように使えます。演算子は新しい値を返します。`transform()`や`normalize()`のようなpymupdfのインプレースのメソッドはないので、代わりに結果を代入してください（`r = r * m`）。座標はすべて有限の数でなければなりません。

### PointとMatrix { #geometry }

`Point(x, y)`は位置です。`Matrix(a, b, c, d, e, f)`はアフィン変換で、`Matrix(sx, sy)`は拡大縮小、`Matrix(degrees)`は回転、`Matrix(sx, sy, 1)`はせん断です。

```python
from pylopdf import Matrix, Point

scale = Matrix(2, 3)
shift = Matrix(1, 0, 0, 1, 20, 30)
transform = scale * shift            # 先に拡大縮小、次に平行移動
position = Point(10, 20) * transform  # Point(x=40.0, y=90.0)
original = position / transform      # Point(x=10.0, y=20.0)
```

| 演算 | 結果 |
|---|---|
| `p + q`, `p - q`, `p * 2`, `-p` | ベクトルの演算 |
| `p * m` | 変換した点: `(a*x + c*y + e, b*x + d*y + f)` |
| `m1 * m2` | 先に`m1`、次に`m2`を適用 |
| `~m` | 逆行列（`m`が逆行列を持たなければゼロ行列） |
| `p / m`, `m1 / m2` | 逆行列を掛けます。逆行列がなければ`ZeroDivisionError`を送出します |
| `abs(p)`, `p.unit`, `p.distance_to(other, unit="px")` | 長さ、向き、距離（`px`、`in`、`cm`、`mm`） |
| `m.is_rectilinear` | 変換が辺を水平・垂直に保つかどうか |

y軸は下向きなので、`Matrix(90)`は`(1, 0)`を`(0, 1)`に移します。`IdentityMatrix`（`Identity`とも）は単位行列です。

### Rect、IRect、Quad { #rectangles }

`Rect(x0, y0, x1, y1)`はタプルや、`Rect(top_left, bottom_right)`のような角の点も受け取ります。`IRect`は同じ引数を取り、外側に向けて整数に丸めます。`Quad(ul, ur, ll, lr)`は回転した矩形のような任意の四角形で、`rect.quad`と`quad.rect`で相互に変換できます。

```python
from pylopdf import Matrix

hits = page.search("Total")
area = hits[0] | hits[-1]                    # 最初と最後のヒットを覆う
inside = [hit for hit in hits if hit in page.rect]
box = area * Matrix(1, 0, 0, 1, 0, 20)        # 20ポイント下へ移動
quad = area.morph(area.tl, Matrix(30))       # 角を中心に30度回転
```

| 演算 | 結果 |
|---|---|
| `r | p`, `r | other` | 点や矩形も覆う最小の矩形 |
| `r & other` | 重なる部分 |
| `r * m`, `r / m` | 変換した矩形の外接矩形 |
| `r + other`, `r * 2` | 座標の演算 |
| `p in r`, `other in r` | 包含判定 |
| `r.width`, `r.height`, `abs(r)`, `r.get_area(unit="px")` | サイズと面積 |
| `r.is_empty`, `r.is_valid`, `r.is_infinite`, `r.intersects(other)` | 判定 |
| `r.normalized()` | `x0 <= x1`かつ`y0 <= y1`にした矩形 |
| `r.round()`, `r.irect` | 覆う`IRect` |
| `r.torect(other)` | 矩形を別の矩形に写す行列 |
| `r.morph(point, m)`, `q.morph(point, m)` | 固定点を中心に変換した`Quad` |
| `q * m`, `p in q`, `q.is_rectangular`, `q.is_convex` | Quadの変換と判定 |

`EMPTY_RECT()`、`INFINITE_RECT()`とそれらの`IRECT`版・`QUAD`版、`planish_line(p1, p2)`はpymupdfと同じヘルパーです。pymupdfと異なり、矩形が四角形の内側にあるかの判定では4つの角をすべて検査します。

## Pixmap { #pixmap }

`Pixmap`はレンダリングしたページの画素を保持し、変更できません。

| メンバ | 用途 |
|---|---|
| `width`, `height`, `n`, `alpha`, `stride`, `colorspace` | サイズと配置。`n`はRGBなら3、RGBAなら4 |
| `samples` | 画素を`bytes`で |
| `memoryview(pix)`, `np.frombuffer(pix, dtype=np.uint8)` | コピーせずに画素を参照 |
| `tobytes()` | PNGのbytes |
| `save(filename)` | PNGファイルを書き出し |

## モジュールの関数と型 { #module-level }

| 名前 | 用途 |
|---|---|
| `open(source, /, *, password=None, limits=None)` | 文書を開きます。[Document](#document)を参照 |
| `probe(source, /, *, password=None, max_file_size=None)` | 文書を読み込まずに読み取った、`page_count`、`version`、`needs_password`、`is_repaired`、`metadata`（`title`、`author`、`subject`、`keywords`、`creator`、`producer`、`created`、`modified`のうち存在するもの）を持つ`Probe`レコード |
| `DocumentLimits`, `DocumentLimits.web()` | 信頼できない入力向けの資源の上限。[セキュリティ](security.md#untrusted-pdfs)を参照 |
| `OcrEngine` | 再利用できるOCRエンジン。[オフラインOCR](ocr.md)を参照 |
| `Canvas`, `PathBuilder`, `TextFit` | [キャンバス](#canvas)を参照 |
| `create(...)`, `Builder`, `Standard`, `TagRole` | [新しい文書](#create)を参照 |
| `Font`, `Shape` | [Font](#font)と[Shape](#shapes)を参照 |
| `Base14_fontnames`, `Base14_fontdict` | `fontname=`が受け付けるStandard 14フォントの名前 |
| `Name`, `String`, `Array`, `Dictionary`, `Stream`, `Objects` | [低レベルオブジェクト](#low-level-objects) |
| `Content`, `TextRun`, `Path`, `Image`, `Glyph`, `FontRef`, `Color`, `Stroke`, `GraphicsContext`ほかの内容の型 | [ページの内容](#content) |
| `Colorspace`, `csRGB`, `csGRAY`, `csCMYK` | pymupdfと同じデバイス色空間 |

定数はpymupdfと同じ名前で、列挙型にもまとめています。`Permissions`（`PDF_PERM_*`）、`LinkKind`（`LINK_*`）、`TextAlign`（`TEXT_ALIGN_*`）、`AnnotType`（`PDF_ANNOT_*`）、`AnnotFlags`（`PDF_ANNOT_IS_*`）です。`Access`のメンバは`USER`と`OWNER`で、`Encryption`は保存するファイルの暗号化方法を表します。ページの内容の列挙型は`BlendMode`、`FillRule`、`LineCap`、`LineJoin`、`TextRenderMode`、`ShadingType`、`SoftMaskType`、`ImageMask`です。

テキスト、表、OCR、ページの内容の結果はレコードで、`Word`、`Block`、`Line`、`Span`、`Char`、`Layout`、`Table`、`TableDiagnostics`、`OcrWord`、`Path`、`Image`などの変更できないデータクラス（frozen dataclass）です。APIが返すそのほかの辞書はただの`dict`で、型チェッカー向けに`LinkInfo`、`FormFieldInfo`、`DocumentMetadata`などの`TypedDict`クラスで記述しています。`OutlineEntry`は名前付きタプルです。

## 例外と警告 { #exceptions }

| 名前 | 送出される場面 |
|---|---|
| `PdfError` | pylopdfのエラーの基底クラス。`ValueError`のサブクラス |
| `MalformedPdfError` | 入力が読めるPDFではない（空の入力を含む） |
| `PasswordError` | パスワードがユーザーパスワードともオーナーパスワードとも一致しない |
| `EncryptedDocumentError` | 先に`unlock()`で文書を復号する必要がある |
| `LimitError` | [サイズや資源の上限](security.md#error-codes)に達した。どの上限かは`.code`でわかります |
| `DocumentClosedError` | 文書が閉じられている |
| `StalePageError` | ページの追加・削除・並べ替えの前に取得した`Page`を使った |
| `StaleObjectError` | 低レベルハンドルがもうオブジェクトを指していない |
| `OcrError` | OCRを実行できなかった |
| `PylopdfWarning` | 修復したファイルや読めなかったフォントなど、回復可能な問題 |

存在しないファイルは、Python組み込みの`FileNotFoundError`を送出します。
