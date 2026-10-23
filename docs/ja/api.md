---
title: APIリファレンス
description: pylopdfのDocument、Page、Canvas、Font、Pixmap、幾何型、低レベルオブジェクト、例外の一覧。
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
| `metadata` | 存在するInfo項目のライブなマッピング。キーは`title`・`author`・`subject`・`keywords`・`creator`・`producer`・`created`・`modified`・`trapped`で、日付はPDFの日付文字列です。キーへの代入や`update()`で書き込み、`del`で項目を削除し、マッピングを代入するとすべての項目を置き換え、`{}`の代入や`clear()`ですべて削除します |
| `version`, `encryption_method` | ファイルヘッダーのPDFバージョン（`"1.7"`など）と、開いたときの暗号化方式（`"Standard V5 R6 256-bit AES"`など）または`None` |
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
| `outline` | しおり（アウトライン）を`OutlineItem`ハンドルの木として。[しおりとページラベル](#outline)を参照 |
| `page_labels` | 前付けのローマ数字のようなページラベルの範囲を`PageLabelRange`レコードとして |

```python
merged = pylopdf.Document()
for path in ["a.pdf", "b.pdf"]:
    merged.pages.extend(pylopdf.open(path).pages)
del merged.pages[0]                         # 最初のページを削除
merged.pages.insert(0, merged.pages[-1])    # 最後のページを先頭にコピー
merged.pages.select(range(len(merged.pages) - 1, -1, -1))  # 順序を逆にする
```

`insert`と`extend`は`list.insert`と同じようにインデックスの位置を受け取るので、末尾を超えるインデックスを指定すると末尾に追加します。1回の呼び出しで渡すページはすべて1つの文書のもので、別の文書のページは1回の呼び出しにつき各ページ1度までです。別の文書のページは、リンク、注釈、フォームフィールドとともに取り込みます。すでに使われているフィールド名には、使われていない最小の`+N`の接尾辞を付けます。同じ文書のページのコピーは新しいページオブジェクトで、どちらかが編集されるまでは元のページとコンテンツストリームとリソースを共有します。

ページを追加・削除・並べ替えると、それ以前の`Page`ビューは古くなります。古いビューを使うと`StalePageError`を送出し、ページを変更しながら`doc.pages`をイテレーションすると`RuntimeError`を送出します。ページを削除・選択すると、削除したページだけを指していたリンク、フォームフィールド、タグ付きPDFの構造も削除します。削除したページへ移動するしおりの項目は、pymupdfと同じく行き先のない項目としてその場に残ります。ただし、そのページの内容のコピーをすべて消すわけではないので、安全な墨消しにはなりません。

### しおりとページラベル { #outline }

`doc.outline`は、しおり（アウトライン）を最上位の`OutlineItem`ハンドルの並びとして返します。各項目の`children`が木の残りを持ち、`doc.outline.walk()`は文書の順序ですべての項目をたどります。項目はアクセスのたびに文書を読み、プロパティに代入するとその場で文書を編集します。

| `OutlineItem`のメンバー | 用途 |
|---|---|
| `title` | ビューアーに表示するタイトル。代入できます |
| `destination` | 項目の行き先。下の表の行き先か`None`。代入できます |
| `page` | `Destination`の0始まりのページ。それ以外は`None` |
| `level`, `children` | 深さ（最上位の項目は1）と子の項目 |
| `color`, `bold`, `italic` | タイトルの表示。透明度のない`Color`か`None`と、2つのフラグ。代入できます |
| `is_open` | 文書を開いたときに子を表示するかどうか。子を持つ項目に代入できます |
| `xref`, `obj` | 項目のオブジェクト番号と、[低レベルオブジェクト](#low-level-objects)での辞書 |
| `to_entry()` | 子を除いた項目を`OutlineEntry`として返す |

`OutlineEntry(level, title, destination=None, *, color=None, bold=False, italic=False, is_open=False)`レコードのリストを代入するとしおり全体を置き換え、`del doc.outline`で削除します。レベルは1から始まり、次の項目へ進むごとに最大1ずつ増えます。項目の子は、その後に続く1つ深いレベルの項目です。`doc.outline.entries()`は現在のしおりを同じ形で返すので、リストとして編集して代入し直せます。

```python
from pylopdf import Destination, OutlineEntry

doc.outline = [
    OutlineEntry(1, "Chapter 1", Destination(0), bold=True, is_open=True),
    OutlineEntry(2, "Section 1.1", Destination(1, point=(72, 100))),
    OutlineEntry(1, "Project site", "https://example.com"),
]
for item in doc.outline.walk():
    if item.page == 1:
        item.title = item.title.upper()
```

| 行き先 | 移動先 |
|---|---|
| `Destination(page, point=None, zoom=None)` | この文書の0始まりのページ。`point`は表示座標での表示範囲の左上、`zoom`は倍率です。どちらもなければ、ビューアーはページをウィンドウに合わせます |
| `str` | URI |
| `RemoteDestination(file, page=0, name=None)` | 別のPDFファイルのページ、または名前付き行き先 |
| `Launch(file)` | ビューアーが関連付けたアプリケーションで開くファイル |
| `NamedAction(name)` | `"NextPage"`や`"FirstPage"`などのビューアーの操作 |

名前付き行き先は、それが指す`Destination`として読み取り、ページがもう存在しない行き先は`None`として読み取ります。読み書きは、65,536項目、64レベル、タイトルと行き先の文字列で16 MiBまでです（`LimitError`のコードは`toc_entry_count`、`toc_text_size`、`toc_input_size`）。

タイトルや行き先の編集では、変更後のしおり全体の文字列サイズを確認してから書き込みます。拒否された編集では、しおりは変わりません。リンク先のファイル名にはUnicodeを使えます。座標と拡大率は、PDFの有限な実数で表せる範囲に限られます。多数のタイトルや行き先を編集するときは、`entries()`のリストを変更して一度だけ代入すると、全体の文字列サイズの確認も一度で済みます。

`doc.page_labels`は`PageLabelRange(start, style=DECIMAL, prefix="", first=1)`レコードのリストです。各範囲は、0始まりの`start`から次の範囲の手前までのページに、`prefix`と、`first`から数えた番号を`PageLabelStyle`（`DECIMAL`、`UPPER_ROMAN`、`LOWER_ROMAN`、`UPPER_LETTERS`、`LOWER_LETTERS`、または接頭辞だけの`None`）の形式で付けます。リストを代入するとページ順に範囲を置き換えます。最初の範囲はページ0から始まる必要があり、`[]`の代入か`del doc.page_labels`で削除します。`page.label`は1ページのラベルで、文書にラベルがなければ`""`です。`doc.pages.labeled(label)`はラベルからページを探します。

```python
from pylopdf import PageLabelRange, PageLabelStyle

doc.page_labels = [PageLabelRange(0, PageLabelStyle.LOWER_ROMAN), PageLabelRange(4, prefix="A-")]
print([page.label for page in doc.pages])  # ['i', 'ii', 'iii', 'iv', 'A-1', 'A-2']
```

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
| `render(pages=None, *, dpi=None, matrix=None, alpha=False, background=None, annotations=True, colorspace="rgb", format="png", quality=95, workers=None, max_size=512 MiB)` | 全ページ、または指定したページのPNGかJPEGのbytesを順番どおりに。並列にレンダリングします |
| `images` | オブジェクト番号ごとの画像オブジェクト。[画像](#images)を参照 |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0, ...)` | 画像を縮小・再圧縮してファイルを小さくします |
| `fallback_fonts` | フォントが埋め込まれていない中国語・日本語・韓国語のテキストを表示するフォントを言語ごとに。CJKのextraを入れると自動で設定されます |

`rewrite_images`はpymupdfと同じキーワードに従います。描かれた位置での解像度が`dpi_threshold`を超える画像を`dpi_target`に再サンプリングし、`quality`（1–100）でJPEGに再エンコードします。対象はマスクのない単純な8ビットのグレーまたはRGB画像だけで、再エンコードしても小さくならない画像はそのまま残し、統計情報を返します。

`fallback_fonts`は、Adobe-Japan1、Korea1、GB1、CNS1の文字コレクションの言語である`"ja"`、`"ko"`、`"zh-CN"`、`"zh-TW"`を、OpenTypeの`Font`を持つ`FallbackFont(sans=None, serif=None)`レコードに対応付けます。明朝体、宋体、明體、バタン体のように名前がセリフ体を示す埋め込まれていないフォントは`serif`で、それ以外は`sans`で描画し、どちらかが`None`ならもう一方が両方を受け持ちます。編集するまでは、インストールされているフォントのextra（`pylopdf[jp]`、`[ko]`、`[zh-cn]`、`[zh-tw]`）を列挙します。1つの言語を代入・削除するとその言語だけが変わり、マッピングを代入するとすべての言語を置き換え、`del doc.fallback_fonts`でインストールされたextraに戻ります。フォントの面はコピーせずに共有するので、1つの`Font`を多くの文書で使えます。

```python
from pylopdf import FallbackFont, Font

doc.fallback_fonts["ja"] = FallbackFont(Font(fontfile="NotoSansJP-Regular.otf"), Font(fontfile="NotoSerifJP-Regular.otf"))
doc.fallback_fonts = {}           # no fallback: such text is not drawn
del doc.fallback_fonts            # back to the installed font extras
```

### フォーム・添付ファイル・その他のデータ { #document-data }

| メンバ | 用途 |
|---|---|
| `form` | フォームフィールドを、完全名から`Field`ハンドルへのマッピングとして返します。[フォーム](#forms)を参照 |
| `attachments` | 埋め込みファイルのマッピング。[添付ファイル](#attachments)を参照 |
| `pdfa_claim` | ファイルが宣言しているPDF/Aのパートと適合レベルを表す`PdfaClaim(part=2, conformance="B")`レコード、または`None`。宣言を読むだけで、検証はしません |
| `xmp` | XMPメタデータのパケットをテキストで、または`None`。文字列を代入すると書き込み、`del doc.xmp`で削除します。読み書きとも1 MiBまでで、それより大きいパケットは`doc.catalog["Metadata"].read(max_size=None)`で読めます。`metadata`はこれを変更しません |
| `language` | 文書の言語を表す`zh-TW`や`sr-Latn-RS`のような完全なBCP 47タグ、または`None`。タグを代入すると書き込み、`del doc.language`で削除します。形式の正しくないタグは例外になります |
| `page_mode`, `page_layout` | ビューアーが文書をどう開くかを表す`PageMode`・`PageLayout`のメンバー（`PageMode.USE_OUTLINES`、`PageLayout.TWO_COLUMN_LEFT`など）。未設定のときは`USE_NONE`と`SINGLE_PAGE`。メンバーかその正確な名前を代入します |
| `mark_info` | タグ付きPDFのフラグを表す`MarkInfo(marked=, user_properties=, suspects=)`レコード、またはない場合は`None`。代入すると3つのフラグをすべて書き込み、`del`で削除します |
| `objects`, `trailer`, `catalog` | PDFオブジェクトへの直接アクセス。[低レベルオブジェクト](#low-level-objects)を参照 |

`language`は完全なタグを保持します。pymupdfの`language`はタグを3文字までしか保持しないため、`zh-TW`と`zh-HK`はどちらも`zh-Hant`として読まれます。XMPを書き込むと、保存時にストリームを圧縮してもパケットは圧縮されないので、ファイルの中からXMPを探すツールでも見つけられます。

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
| `label` | `"iv"`のようなページラベル。[しおりとページラベル](#outline)を参照 |

`Word`は`text`と`bbox`に加えて、ページのレイアウト内での位置を示す`block`、`line`、`index`を持ちます。それぞれ、属するブロックのインデックス、そのブロック内での行のインデックス、その行内での語のインデックスです。

読み順は行と段組みに従い、右から左に書くテキストや、日本語・中国語の縦書きにも対応します。ルビ、割注、向きが混在するテキストは解釈しません。

### レンダリング { #page-rendering }

| メンバ | 用途 |
|---|---|
| `pixmap(*, dpi=None, matrix=None, clip=None, alpha=False, background=None, annotations=True, colorspace="rgb")` | [`Pixmap`](#pixmap)へレンダリング |
| `svg(*, annotations=True, max_size=64 MiB)` | テキストをパスで描いたSVG文字列へレンダリング |
| `resources` | ページのリソースが届くもの。`resources.images`が画像オブジェクトを並べます。[画像](#images)を参照 |

ピクセルマップは既定で72 dpi、白地のRGBで、`alpha=True`でRGBAです。`dpi`と`matrix`はどちらか一方を渡します。行列は`Matrix(2, 2)`のような拡大縮小だけのものでなければなりません。`clip`はページの一部だけをレンダリングし、`annotations=False`は注釈とフォームのウィジェットを描かず、`colorspace="gray"`はグレーの画素でレンダリングします。

### ページの幾何情報 { #page-geometry }

| メンバ | 用途 |
|---|---|
| `index`, `document` | 文書内でのページのインデックスと、その文書 |
| `rect` | 回転を適用した、ページの見える領域 |
| `rotation` | 0、90、180、270のいずれか。90の倍数を代入するとページを時計回りに回転します |
| `boxes` | MediaBox、CropBox、BleedBox、TrimBox、ArtBoxをPDFのユーザー空間の`Rect`として。それぞれ代入できます |
| `user_to_display` | PDFのユーザー空間を表示座標に写す`Matrix` |
| `xref`, `obj` | ページオブジェクトの番号と[低レベルハンドル](#low-level-objects) |

ページボックスは表示座標を定めるものなので、PDFのユーザー空間（MediaBoxの下端から上向きのy）を使う唯一の場所です。`page.boxes`は保存されているとおりに角を並べ替えて読み取ります。`media`がなければ原点にあるA4、`crop`がなければMediaBox、`bleed`、`trim`、`art`がなければCropBoxになります。`media`に代入すると、pymupdfの`set_mediabox`と同じくほかのボックスを削除します。ほかのボックスへの代入には、MediaBoxの内側にある面積のある矩形が必要で、`del page.boxes.crop`（`bleed`、`trim`、`art`も同様）で既定値に戻ります。`page.user_to_display`はユーザー空間を表示座標に写し、`~page.user_to_display`はその逆を写します。

```python
page.boxes.crop = (36, 36, 576, 756)                # show a Letter page without its half-inch margin
trim_on_screen = page.boxes.trim * page.user_to_display
page.rotation = 90
```

### 内容の追加 { #page-content }

| メンバ | 用途 |
|---|---|
| `draw(*, underlay=False)` | テキスト、線や四角形などのパス、画像、任意の文書のページを順に描く[キャンバス](#canvas)。[テキストを書く](#writing-text)を参照 |
| `replace_text(search, replacement, *, default_char=None)` | 単純な（1バイトの）フォントで描かれたテキストを置換 |

同じ画像やページを再び描いても、ファイルには1回だけ格納されます。

### 注釈とリンク { #page-annotations }

| メンバ | 用途 |
|---|---|
| `annotations` | リンクとフォームウィジェット以外のページの注釈を、`Annotation`ハンドルとして |
| `links` | ページのリンクを、ページが並べる順の`Link`ハンドルとして |

`page.annotations`は、ページが並べる順の`Annotation`ハンドルのシーケンスです。ポップアップはそれを指す注釈に属し、一覧には含まれません。ハンドルはアクセスのたびに文書を読み、`AnnotationType`のメンバである`type`、表示座標の`rect`、テキストの`contents`、`author`、`subject`、`name`、`icon`、`intent`、保存されたPDFの日付文字列としての日付`created`と`modified`、`Color`レコードまたは`None`としての`color`と`interior_color`、`opacity`、`AnnotationFlags`の値である`flags`、それに`xref`、`obj`、`page`を持ちます。`page.annotations.add(Highlight(rects, *, color=Color(1, 1, 0), opacity=0.4, contents=None))`は検索結果など1つ以上の矩形や四辺形をハイライトして新しいハンドルを返し、`del page.annotations[i]`は注釈をそのポップアップとともに削除します。`AnnotationType`と`AnnotationFlags`のメンバはpymupdfの`PDF_ANNOT_*`と`PDF_ANNOT_IS_*`の定数と同じ値です。

```python
from pylopdf import AnnotationType, Color, Highlight

note = page.annotations.add(Highlight(page.search("total"), color=Color(1, 0.8, 0), contents="Check"))
for annotation in page.annotations:
    if annotation.type is AnnotationType.TEXT:
        print(annotation.author, annotation.contents)
```

注釈の読み取りは1ページあたり4,096個の注釈と1 MiBのテキストで止まり、ハイライトの`contents`は1 MiBまでです（`LimitError`のコード`annotation_input_size`）。

`page.links`は`Link`ハンドルのシーケンスです。`page.links.add(rect, target)`は表示座標の矩形にリンクを追加してそのハンドルを返し、`del page.links[i]`はリンクを削除します。ハンドルはアクセスのたびに文書を読み、`rect`や`target`への代入でリンクを編集します。リンク先は[しおり](#outline)と同じで、URI文字列、`Destination`、`RemoteDestination`、`Launch`、`NamedAction`のいずれかです。JavaScriptなどpylopdfが読まないアクションのリンクや、どのページも指さない行き先のリンクのリンク先は`None`です。`xref`と`obj`は注釈のオブジェクト番号と、[オブジェクトモデル](#low-level-objects)での辞書を返し、枠線、色、フラグはこの辞書にあります。

```python
from pylopdf import Destination

for rect in page.search("Chapter 2"):
    page.links.add(rect, Destination(5))
for link in page.links:
    if isinstance(link.target, str) and link.target.startswith("http:"):
        link.target = "https:" + link.target.removeprefix("http:")
```

リンクの読み取りは1ページあたり4,096個の注釈と1 MiBのリンク先テキストで止まり、書き込むリンク先テキストは1 MiBまでです（`LimitError`のコード`annotation_input_size`）。

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
| `image(rect, source, *, keep_aspect=True, rotate=0)` | JPEGまたはPNGのパス、エンコード済みのbytes、`Pixmap`のいずれか。`rotate`で時計回りに回します |
| `page(rect, source, *, keep_aspect=True)` | 開いている任意の文書（この文書も含む）のページをベクタのまま |
| `draw(item)` | [`page.content()`](#content)が返した`Path`項目 |

パスは`Color`の塗りと線を取ります。線は、幅、線端、角の形、マイター限界、破線を持つ`Stroke`か、1ポイントの線を表す`Color`です。どちらも指定しないパスは1ポイントの黒い線で描かれます。色の`alpha`は、その塗りや線を半透明にします。`PathBuilder`は`move_to`、`line_to`、`curve_to`、`quad_to`、`close`で形状を組み立て、`rect`、`ellipse`、`circle`、`polygon`、`sector`、`zigzag`、`wave`で図形をまるごと追加します。どのメソッドもビルダー自身を返すので、呼び出しを連鎖できます。

テキストは[`Font`](#font)で描きます。OpenTypeフォントはHarfRustで字形処理し、何回の呼び出しで使ってもキャンバスごとに1回だけサブセットとして埋め込みます。`font`を指定しない場合、西欧のテキストはHelveticaで、それ以外のテキストはその文字体系に対応するインストール済みの[フォントのextra](getting-started.md#installation)で描きます。テキストは塗り、線、またはその両方で描けます。`text`は各行を前の行から`line_height × size`下に置き、既定ではフォント自身の行の高さ（アセンダーからディセンダーを引いた値）を使います。`textbox`はUnicodeが改行を許す位置で行を折り返し、同じ間隔で行を置きます。最初のベースラインは`rect`の上端からアセンダー1つ分下です。テキストが収まらないときは何も描かず、`TextFit.spare_height`は不足分を表す負の値になるので、`fit.fits`は偽になります。[`Font.text_height`](#font)を使うと、textboxが必要とする高さを描く前に測れるので、テキストを囲む枠の大きさや改ページを決められます。

```python
font = pylopdf.Font.from_file("NotoSansJP-Bold.otf")
height = font.text_height(note, 10.5, width=300)
if y + height + 16 > page.rect.height - 40:
    page = doc.pages.new()
    y = 40
with page.draw() as canvas:
    canvas.rect((40, y, 356, y + height + 16), radius=4, stroke=navy)
    canvas.textbox((48, y + 8, 348, y + 8 + height), note, font=font, size=10.5)
```

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

font = pylopdf.Font.from_file("NotoSans-Regular.ttf")
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

テキストは[キャンバス](#canvas)で描きます。`canvas.text(text, at, ...)`は点を起点に行を書き、`canvas.textbox(rect, text, ...)`は矩形の中にテキストを折り返して書きます。

```python
from pylopdf import Color, Font

with page.draw() as canvas:
    canvas.text("CONFIDENTIAL", (40, 40), size=18, fill=Color(1, 0, 0))
    canvas.text("社外秘", (40, 70), size=18)                                    # pylopdf[jp]が必要
    canvas.text("简体中文", (40, 100), font=Font.installed(language="zh-CN"))  # pylopdf[zh-cn]が必要
```

フォントは`font=`で選びます。

- 何も指定しない: 西欧（WinAnsi）のテキストはHelvetica、それ以外はテキストの文字体系に対応するインストール済みextraのフォント。
- `Font.standard(name)`: `"Times-Bold"`などのStandard 14フォント。Standard 14フォントは埋め込まれず、西欧の文字しか扱えません。
- `Font.from_file(path)`または`Font.from_bytes(data)`: OpenTypeフォント。使ったグリフだけを埋め込みます。
- `Font.installed(language=...)`または`Font.installed(family=...)`: インストール済みextraのフォント。

1つのフォントがテキストのすべての文字を含んでいる必要があります。pylopdfは文字ごとにフォントを切り替えません。かな、ハングル、注音符号、アラビア文字、ヘブライ文字、デーヴァナーガリー、タイ文字を含むテキストは、対応するインストール済みextraを自動で選びます。漢字だけでは言語を判別できないため、既定では日本語フォントを使います。ほかの言語にするには`font=Font.installed(language="zh-CN")`（または`"zh-TW"`、`"ko"`）を渡してください。

右から左に書くテキストは正しく字形処理されます。PDFを読み戻すと、すべて右から左の行と、右から左のテキストの中にラテン文字の単語か数字を1つだけ含む行は論理順で、それ以外の方向が混在する行は見た目の順で取り出されます。複雑な文字体系のクラスタの一部、特に長いデーヴァナーガリーの並びは、元のテキストとわずかに異なって抽出されることがあります。

### Font { #font }

```python
pylopdf.Font.standard(name)
pylopdf.Font.from_file(path, /, *, index=0, weight=None, max_size=64 MiB)
pylopdf.Font.from_bytes(data, /, *, index=0, weight=None, max_size=64 MiB)
pylopdf.Font.installed(*, language=None, family=None, serif=False, weight=None, italic=False)
```

| 例 | 読み込むもの |
|---|---|
| `Font.standard("Helvetica")`, `Font.standard("Times-Bold")` | Standard 14フォント（PostScript名で指定） |
| `Font.from_file("NotoSansJP-Bold.otf")`, `Font.from_bytes(data)` | OpenTypeフォント（ファイルまたはそのバイト列から） |
| `Font.from_file("NotoSansCJKjp-VF.otf", weight=700)` | 可変フォントの1つの太さ |
| `Font.from_file("fonts.ttc", index=1)` | フォントコレクションの1つのフェイス |
| `Font.installed(language="ja")`, `Font.installed(family="Noto Sans JP")` | インストール済みextraのフォント。`serif=True`でその言語の明朝体（セリフ体）を選びます |
| `Font.installed(language="he", weight=700)` | インストール済み可変フォントのBoldの太さ |

フォントは、[キャンバス](#canvas)が描くとおりにテキストを測ります。`text_width(text, size)`は1行の幅で、カーニングと合字を含みます。`text_height(text, size, *, width, line_height=None)`は、テキストを幅`width`ポイントで折り返したときに`canvas.textbox`が必要とする高さです。この高さのtextboxにはテキストがちょうど収まります。プロパティは`name`、`family`、`weight`、`is_bold`、`is_italic`、`is_serif`、`units_per_em`、`ascender`、`descender`で、`has_glyph(char)`はフェイスがその文字のグリフを持つかどうかを返します。`typing.get_args(pylopdf.StandardFontName)`は`Font.standard()`が受け付ける14の名前を列挙します。Standard 14フォントは埋め込まれず、西欧（WinAnsi）の文字しか書けません。

`weight`を指定せずに読み込んだ可変フォントは既定のインスタンスを使いますが、それがRegularの太さとは限りません。可変フォントのNoto Sans CJK JPの既定はThinです。Regularにするには`weight=400`を渡してください。フォームに記入する書体（`doc.form.font`）も同じです。

アラビア語・ヘブライ語・ヒンディー語・タイ語のextraは可変フォントを含むので、軸上の任意の太さを使えます（100–900。Noto Naskh Arabicは400–700）。CJKのextraはスタイルごとにRegularのフォントを1つだけ含みます。そのため`Font.installed(language="ja", weight=700)`は`PdfError`を送出します。Noto Sans JP Boldのような太字のフェイスを`Font.from_file()`で読み込んでください。pylopdfがグリフを変形して太字や斜体を真似ることはありません。

pylopdfはシステムのフォントディレクトリを探さず、グローバルなフォント登録の仕組みも持たないため、出力は渡したフォントだけで決まります。

### フォーム { #forms }

`doc.form`は、`person.first`のような完全修飾のフィールド名から`Field`ハンドルへの、名前順のマッピングです。ハンドルはアクセスのたびに文書を読み、`name`、`type`（`FieldType.TEXT`、`CHECKBOX`、`RADIO`、`PUSHBUTTON`、`COMBOBOX`、`LISTBOX`、`SIGNATURE`などの`FieldType`メンバ）、`value`、`flags`（親フィールドから継承する`FieldFlags`）、`choices`（選択フィールドの`FieldChoice(value=, label=)`の選択肢）、それに`xref`と`obj`を持ちます。

```python
from pylopdf import Font

for name, field in doc.form.items():
    print(name, field.type, field.value)
doc.form["customer"].value = "Alice"   # 値とその見た目を書き込む
doc.form["agree"].value = True         # チェックボックスのオンの状態
doc.form.font = Font.from_file("NotoSansJP-Regular.otf")
doc.form["name"].value = "山田 太郎"
```

`value`に代入すると、テキスト、コンボボックス、リストボックス、チェックボックス、ラジオボタンのフィールドに記入し、それぞれの見た目を生成し直します。`True`と`False`はボタンのオンの状態と`"Off"`を選びます。西欧（WinAnsi）のテキストはHelveticaで収まるように大きさを調整し、それ以外の文字はインストールされたフォントのextraで書きます。`doc.form.font`にOpenTypeの`Font`を設定すると、以降の記入はすべてその書体で書きます。中国語のテキストなら`Font.installed(language="zh-CN")`です。この設定は`Document`オブジェクトに属し、保存されません。コーム（comb）フィールド、配置、最大文字数を守り、長すぎる値は例外を送出して文書を変更しません。リッチテキスト、プッシュボタンのアクション、署名には対応していません。署名については[エコシステム連携](ecosystem.md#signatures)を参照してください。

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

y軸は下向きなので、`Matrix(90)`は`(1, 0)`を`(0, 1)`に移します。`Matrix.IDENTITY`は単位行列で、`Matrix.planish(p1, p2)`は`p1`を原点に、`p2`をx軸の正の側に移す行列を返します。

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

`Rect.EMPTY`と`Rect.INFINITE`はMuPDFの空の矩形と無限の矩形で、`IRect`と`Quad`にも同じクラス属性があります。pymupdfと異なり、矩形が四角形の内側にあるかの判定では4つの角をすべて検査します。

## Pixmap { #pixmap }

`Pixmap`はレンダリングしたページや画像の画素を保持し、変更できません。

| メンバ | 用途 |
|---|---|
| `width`, `height`, `channels`, `alpha`, `stride`, `colorspace` | サイズと配置。`channels`はグレーなら1、アルファ付きグレーなら2、RGBなら3、アルファ付きRGBなら4。`colorspace`は`Colorspace.GRAY`か`Colorspace.RGB` |
| `numpy.asarray(pix)`, `memoryview(pix)` | コピーせずに画素を参照。形はグレーなら`(height, width)`、それ以外は`(height, width, channels)` |
| `bytes(pix)` | 画素のコピー |
| `Pixmap.from_array(array)` | 同じ形の`uint8`配列をコピーしたピクセルマップ |
| `to_bytes(format="png", *, quality=95, max_size=64 MiB)` | PNGかJPEGのbytes。JPEGにアルファはありません |
| `save(path, /, *, format=None, quality=95)` | PNGかJPEGを書き出し。`format`を省くと拡張子`.png`、`.jpg`、`.jpeg`で決まります |

画像処理はNumPy、Pillow、OpenCVに任せます。`PIL.Image.fromarray(numpy.asarray(pix))`でPillowの画像になります。

## モジュールの関数と型 { #module-level }

| 名前 | 用途 |
|---|---|
| `open(source, /, *, password=None, limits=None)` | 文書を開きます。[Document](#document)を参照 |
| `probe(source, /, *, password=None, max_file_size=None)` | 文書を読み込まずに読み取った、`page_count`、`version`、`needs_password`、`is_repaired`、`metadata`（`title`、`author`、`subject`、`keywords`、`creator`、`producer`、`created`、`modified`のうち存在するもの）を持つ`Probe`レコード |
| `DocumentLimits`, `DocumentLimits.web()` | 信頼できない入力向けの資源の上限。[セキュリティ](security.md#untrusted-pdfs)を参照 |
| `OcrEngine` | 再利用できるOCRエンジン。[オフラインOCR](ocr.md)を参照 |
| `Canvas`, `PathBuilder`, `TextFit` | [キャンバス](#canvas)を参照 |
| `create(...)`, `Builder`, `Standard`, `TagRole` | [新しい文書](#create)を参照 |
| `Font`, `StandardFontName` | [Font](#font)を参照 |
| `Name`, `String`, `Array`, `Dictionary`, `Stream`, `Objects` | [低レベルオブジェクト](#low-level-objects) |
| `Content`, `TextRun`, `Path`, `Image`, `Glyph`, `FontRef`, `Color`, `Stroke`, `GraphicsContext`ほかの内容の型 | [ページの内容](#content) |
| `Colorspace` | [`Pixmap`](#pixmap)の色空間。`GRAY`と`RGB` |
| `Outline`, `OutlineItem`, `OutlineEntry`, `Destination`, `RemoteDestination`, `Launch`, `NamedAction`, `PageLabelRange`, `PageLabelStyle` | [しおりとページラベル](#outline) |
| `Annotation`, `Annotations`, `AnnotationType`, `AnnotationFlags`, `Highlight`, `Link`, `Links` | [注釈とリンク](#page-annotations) |
| `PageBoxes` | [ページの幾何情報](#page-geometry) |
| `Form`, `Field`, `FieldType`, `FieldFlags`, `FieldChoice` | [フォーム](#forms) |
| `FallbackFont`, `FallbackFonts` | [レンダリングと画像](#document-rendering) |

コードとフラグは列挙型のメンバだけで、モジュールの定数はありません。pymupdfの`PDF_PERM_PRINT`は`Permissions.PRINT`、`PDF_ANNOT_TEXT`は`AnnotationType.TEXT`、`PDF_ANNOT_IS_HIDDEN`は`AnnotationFlags.HIDDEN`で、値は同じです。`Access`のメンバは`USER`と`OWNER`で、`Encryption`は保存するファイルの暗号化方法を表します。ページの内容の列挙型は`BlendMode`、`FillRule`、`LineCap`、`LineJoin`、`TextRenderMode`、`ShadingType`、`SoftMaskType`、`ImageMask`です。

テキスト、表、OCR、ページの内容の結果はレコードで、`Word`、`Block`、`Line`、`Span`、`Char`、`Layout`、`Table`、`TableDiagnostics`、`OcrWord`、`Path`、`Image`、`MarkInfo`、`PdfaClaim`、`OutlineEntry`、`PageLabelRange`、しおりの行き先などの変更できないデータクラス（frozen dataclass）です。APIが返すそのほかの辞書はただの`dict`で、型チェッカー向けに`DocumentComplexity`などの`TypedDict`クラスで記述しています。

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
