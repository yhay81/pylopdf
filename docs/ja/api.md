---
title: API一覧
description: pylopdfのDocument、Page、Pixmap、Rect、権限、警告、例外を俯瞰するAPIマップ。
---

# API一覧

詳細なdocstringはパッケージ内にあります（`help(pylopdf.Document)`）。このページはその地図です。ページ番号は、アウトラインのページも含めてすべて0始まりです。座標はすべて左上原点の表示空間です。[API安定性ポリシー](stability.md)で公開範囲と非推奨化の手順を定義しています。以下のバイト数、件数、深さの上限はすべて、悪意のある入力からプロセスを守るためのものです。ポリシーの説明は[信頼できないPDFを扱う](security.md#untrusted-pdfs)にあり、上限に達すると安定した`.code`を持つ`LimitError`が送出されます。

## Document { #document }

`pylopdf.Document(filename=None, stream=None, filetype=None, *, password=None, limits=None, max_decompressed_size=None)` — pymupdfと同じく、`pylopdf.open`は同一の呼び出し可能オブジェクトです。コンテキストマネージャーに対応しています。`stream`はbytes、`bytearray`、`memoryview`、`io.BytesIO`を受け付け、`filetype`は`"pdf"`を取ります。pymupdfの`open("pdf", data)`と同じく、`stream`と並べて渡した文字列の`filename`はファイル形式を表します。

| メンバー | 用途 |
|---|---|
| `doc[i]`、`load_page(pno)`、イテレーション | `Page`ビュー。負のインデックスも使えます。構造を変更した後はページを取得し直してください。 |
| `page_count`、`len(doc)` | ページ数。 |
| `limits`、`complexity` | 開いたときの不変な資源ポリシーと、ストリームを展開せずに集めた軽量な構造の指標。 |
| `needs_pass`、`is_encrypted`、`authenticate(pw)` | 暗号化の状態と、pymupdfと同じ戻り値0/1/2/4/6による解除。空のユーザーパスワードで開いた文書でも戻り値を検査します。 |
| `is_repaired` | 開くときに破損した相互参照データを復旧したかどうか（オフセットの補正または表の再構築）。保存すると相互参照データを正規化します。 |
| `objects`、`trailer`、`catalog`、`page_xref(pno)` | オブジェクトグラフへの型付きアクセス。pymupdfの`xref_*`メソッドを置き換えます。[低水準オブジェクト](#low-level-objects)を参照してください。 |
| `metadata`、`set_metadata(m)` | pymupdfのメタデータのキー（`format`、`trapped`を含むInfo辞書の項目、`encryption`）の読み書き。 |
| `get_page_text(pno, option)` | 1ページのテキスト。`option`は`"text"`、`"words"`、`"blocks"`、`"dict"`、`"rawdict"`のいずれかです。 |
| `get_text(pages=None)` | すべてのページ、または指定したページのリストのプレーンテキストを一括で抽出します。 |
| `to_markdown(pages=None, table_strategy="lines")` | 見出し、CJK、強調、リスト、段組み、縦書きの順序、表を扱うMarkdown変換。 |
| `extract_image(xref)`、`get_page_images(pno, *, full=False)` | pymupdfと同じキーで返す1つの画像のデータと、ページの画像一覧。 |
| `get_page_pixmap(pno, *, matrix=, dpi=, clip=, alpha=False)` | ページの`Pixmap`。pymupdfの`doc[pno].get_pixmap()`の簡便形です。 |
| `render_pages(pages=None, *, dpi=, alpha=False, workers=)` | すべてのページまたは指定したページのリストを、順序を保って並列にPNGへレンダリングします。 |
| `rewrite_images(*, dpi_threshold=None, dpi_target=0, quality=0)` | 安全なラスター画像を配置に応じて縮小し、JPEGで再圧縮します。型付きのバイト数と件数の統計を返します。 |
| `set_fallback_font(font, kind=, index=, font_language="ja")` | 埋め込まれていないAdobe Japan1、Korea1、GB1、CNS1のフォントに対する、地域を区別したCJKの代替フォント。 |
| `select`、`delete_page(s)`、`insert_pdf(docsrc, *, from_page=-1, to_page=-1, start_at=-1)`、`new_page`、`fullcopy_page` | ページの管理。 |
| `get_outline()`、`set_outline(entries)` | 0始まりのページを持つ`OutlineEntry(level, title, page)`の項目で表すアウトライン。 |
| `get_page_labels()`、`set_page_labels(labels)` | ページラベルの範囲。 |
| `get_form_fields()`、`set_form_field(name, value, font=, fontfile=)` | AcroFormのフィールドの一覧と、ネイティブのウィジェット外観を付けた入力。[フォームの入力](#form-filling)を参照してください。 |
| `attachments` | `Attachment`ハンドルの`Attachments`マッピング。 |
| `get_pdfa_claim()` | XMPのPDF/A宣言を読みます。検証ではありません。 |
| `save(...)`、`tobytes(...)` | PDFをファイルに書き出すか、bytesとして返します。 |
| `close()` | 文書を閉じます。`with`でも閉じられます。 |

**詳細**

- `set_metadata`はUTF-16BEに対応し、`format`と`encryption`を無視し、偽の値を持つ項目を削除し、`{}`ではInfo辞書を削除し、アトミックに書き込みます。
- `get_text`は明示した項目を4,096項目ずつの区切りで処理し、各区切りで1つのインタープリターのフォントキャッシュを共有します。`to_markdown`は上限付きの線形な項目の組み立てで、ページ単位に2回の処理を行います。
- `extract_image`はDCTをJPEGのまま渡し、ほかのフィルターはPNGに復号し、ソフトマスクは含めません。
- `get_page_pixmap`と`render_pages`はどちらも`matrix=`、`dpi=`、`alpha=`、`background=`を取り、`get_page_pixmap`は`clip=`も取ります。
- `rewrite_images`はpymupdfのキーワード`dpi_threshold=None`、`dpi_target=0`、`quality=0`、`lossy=True`、`lossless=True`、`color=True`、`gray=True`を取ります。`dpi_threshold`を超える軸は`dpi_target`へ再標本化され、`quality`の0は再圧縮なしを意味するため、再標本化には`quality`が必要です。`set_to_gray`と`options`は例外を送出します。上限の後の段落を参照してください。
- `insert_pdf`は取り込み元の任意の全範囲を受け付け、取り込んだページのフォームフィールドを登録します。`select`と`delete_page(s)`は、除外したページのウィジェット、フィールド、タグ付きの構造の内容を削除します。
- アウトラインの名前付きリンク先は、`get_links()`と同じように解決されます。
- `set_form_field`は`fontbuffer=`、`fontindex=`、`font_language=`も取ります。`font`は読み込み済みのOpenTypeの`Font`で描きます。
- `attachments`は`add(name, data, /, *, filename, description)`と`del`に対応します。各`Attachment`は`filename`、`description`、`size`、`creation_date`、`mod_date`、`read()`、`write(data, /)`を持ちます。
- `save`は同じディレクトリへのストリーム書き込みが完了した後に、ファイルをアトミックに置き換えます。`save`と`tobytes`はどちらも、`garbage=0`–`4`（pymupdfと同じ段階。3と4は同一のオブジェクトとストリームを統合）、`deflate=`、`use_objstms=`、`encryption=`（既定は`PDF_ENCRYPT_NONE`、または`PDF_ENCRYPT_AES_256`。`PDF_ENCRYPT_AES_256`なしでパスワードを渡すと例外）、`user_pw=`、`owner_pw=`を取ります。

**上限**

- パスワード（`password=`、`authenticate`、`user_pw=`、`owner_pw=`）はUTF-8で127バイトまでです。
- `set_metadata`: テキストの合計1 MiB。
- `get_text`、`to_markdown`、`render_pages`、`select`、`delete_page(s)`に明示的に渡す`pages`の項目: ページ数と4,096の大きい方まで。`pages=None`はすべてのページを対象にします。
- `to_markdown(max_size=64 MiB)`: UTF-8出力の累積量。
- `render_pages(max_size=512 MiB)`: エンコード済みPNG出力の累積量。
- `tobytes(max_size=512 MiB)`: シリアライズしたPDFのバイト数。
- `set_fallback_font(max_font_size=64 MiB)`と`set_form_field(max_font_size=64 MiB)`: フォントの入力。`set_form_field`は呼び出し側の名前と値も1 MiBで止め、ウィジェットの外観にも上限を設けます。
- `get_outline` / `set_outline`: 項目・ノード65,536個、辺131,072本、64階層、テキスト16 MiB。大規模なマニュアルに合わせた上限で、循環にも対応します。`select`と`delete_page(s)`でのナビゲーションの整理と、`insert_pdf`でのリンクの取り込みは、目次と名前付きリンク先の上限を共有します。
- ページラベル: 項目・ノード4,096個、32階層、ラベルのテキスト1 MiBの固定の上限。
- 添付ファイル: `add(..., max_size=64 MiB)`、`read(*, max_size=64 MiB)`、`write(data, /, *, max_size=64 MiB)`は、入力と展開後の出力に対称な既定の上限を適用します。呼び出し側のテキストは1 MiBまでで、メタデータとインラインのFileSpecを複製する形状にも上限があります。
- `get_pdfa_claim(max_size=1 MiB)`: 展開後のXMP。
- `rewrite_images`: 1回の呼び出しで、解釈した間接ラスター画像の配置が65,536個を超えると拒否します。
- `max_size=None`と`max_font_size=None`は、信頼できる入力や出力について、対応するバイト数の上限を解除します。

`rewrite_images()`はすべてのページを解釈して、各間接ラスターオブジェクトが最も大きく配置される寸法を求めてから、lopdfの複製をアトミックに編集します。`dpi=None`は縮小を無効にしますが、品質による再圧縮は行います。対象は、マスクや独自のデコード配列を持たない、直接で単一フィルターの8ビットのDeviceGray・DeviceRGBのDCTまたはFlateのストリームに保守的に限定します。DCTのデコードパラメーターを持つものは対象外で、Flateは予測子なし、または辞書と整合するPNGの予測子を使えます。解釈した対応外の間接画像と、小さくならないエンコードはスキップし、インライン画像は対象にしません。同じ設定で繰り返しても結果は変わりません。

## Page { #page }

| メンバー | 用途 |
|---|---|
| `number`、`parent`、`get_label()` | 識別情報と表示ラベル。 |
| `xref`、`obj` | ページ辞書のオブジェクト番号と、低水準のライブなハンドル。継承される属性は祖先に残ります。 |
| `get_text(option)` | 文字単位の`"rawdict"`を含む、プレーンまたは位置付きの抽出。[位置付きテキスト](#positioned-text)を参照してください。 |
| `search_for(text, max_hits=4096)` | 大文字小文字を区別しない検索で、ヒットした矩形を返します。 |
| `get_text_ocr(dpi=, engine=, rotation=, clip=)` | ページを編集せずに、ローカルのPP-OCRv6で位置付きの単語を認識します。 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 文字を認識し、向きを考慮した不可視の検索可能な層を挿入します。選択した領域に既存のテキストがあるページは、既定でスキップします。 |
| `insert_ocr_text_layer(words, rotation=)` | 渡された単語から、向きを考慮した不可視のOCR層を挿入します。 |
| `find_tables(clip=None, strategy=None)` | 完全な、または保守的に補完したベクターの罫線のグリッドと結合セル。`"text"`で罫線のない表の検出を有効にします。[表](#tables)を参照してください。 |
| `to_markdown(table_strategy="lines")` | 同じ表と出力の制御を持つ、1ページのMarkdown変換。 |
| `get_images(*, full=False)` | pymupdfと同じ画像XObjectの一覧を`ImageItem`のNamedTupleで返します。`ImageItemFull`は`referencer`を追加します。 |
| `get_image_info(*, hashes=False, xrefs=False)` | `bbox`、`transform`、サイズ、色空間、`xrefs=True`のときは`xref`を持つ配置の辞書。 |
| `get_drawings()` | 解釈したベクターの塗りと線のパス。[ベクター図形](#vector-drawings)を参照してください。 |
| `get_pixmap(*, matrix=None, dpi=None, clip=None, alpha=False)` | pymupdfと同じ`Pixmap`へのレンダリング。既定は白地の不透明なRGBで、`alpha=True`でストレートアルファのRGBAになります。 |
| `get_svg_image(*, matrix=None, text_as_path=True)` | テキストをパスとして描くSVGレンダリング。 |
| `rotation`、`set_rotation(deg)` | 表示の回転。 |
| `mediabox`、`cropbox`、`rect`、`set_mediabox`、`set_cropbox` | pymupdfの規約に従うページボックス。 |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=)` → xref | JPEGまたはPNGを描くか、上限を管理済みのRGBAの`Pixmap`を再利用します。同一の入力を再び配置すると、1つの画像XObjectを共有します。 |
| `show_pdf_page(rect, docsrc, pno=)` → Form xref | PDFのページをベクターのまま重ねます。`docsrc`は同じ文書でもかまいません。 |
| `insert_text(point, text, fontsize=, font=)` | Standard 14のWinAnsiまたは整形済みのサブセットのテキストを描き、描いた行数を返します。[テキストの挿入](#text-insertion)を参照してください。 |
| `insert_textbox(rect, text, fontsize=, font=, align=)` | UAX #14の改行規則でテキストを矩形内に折り返します。収まらない場合は何も描きません。 |
| `new_shape()` | パスとテキストを1回で描くpymupdfの`Shape`。[図形](#shapes)を参照してください。 |
| `draw_*`メソッド | 仕上げた図形を1つ描き、pymupdfと同じ終点の`Point`を返します。[描画メソッド](#drawing-methods)を参照してください。 |
| `replace_text(search, replacement, default_char=)` | 単純エンコーディングのフォントでの、書き込み時コピーによるアトミックな置換。 |
| `annots(*, types=None)` | 読み取り専用の`Annot`ハンドル（`type`、`rect`、`info`、`colors`、`flags`、`xref`）。種類は`PDF_ANNOT_*`、フラグは`PDF_ANNOT_IS_*`です。 |
| `get_links()` | pymupdfの`xref`と`id`（`/NM`）を持つリンクの辞書。 |
| `add_highlight_annot(quads, ...)` | ハイライトを追加し、その`Annot`を返します。 |
| `insert_link(link, /)` | リンクの辞書からURIリンクを作成します。 |

**詳細**

- `get_text_ocr`は`tile_size=`、`overlap=`、`min_confidence=`も取ります。`apply_ocr`と同じく、`rotation`は入力を時計回りに補正し、`clip`は表示座標を使います。[OCR](ocr.md)を参照してください。
- `find_tables(clip=)`は表示座標の領域です。
- `get_pixmap`: `dpi`は拡大縮小の`matrix`より優先され、`clip`は表示座標で、`background=`は不透明な背景を設定します。
- `mediabox`はPDF座標で、`cropbox`はMediaBoxの上端を基準にy軸を反転します。`set_cropbox`はMediaBox内の矩形が必要で、`set_mediabox`はCropBox、BleedBox、TrimBox、ArtBoxを削除します。
- `insert_image`と`show_pdf_page`は`keep_proportion=`と`overlay=`も取ります。`rotate`は90度単位で時計回りに回転します。編集していない`docsrc`のページを再び配置すると、そのForm XObjectを共有します。
- `insert_text`と`insert_textbox`は`lineheight=`、`fontname=`、`fontfile=`、`fontbuffer=`、`fontindex=`、`font_language=`、`color=`、`overlay=`も取り、`insert_textbox`は`expandtabs=`も取ります。`font`は読み込み済みの`Font`で描き、導入済みの文字体系・言語のフォント提供元は自動で選ばれ、`font_language`は曖昧な漢字の地域を指定します。
- `get_links()`の名前付きリンク先は1度だけインデックス化され、次の編集までは以降の読み取りで再利用されます。
- レンダリングでは、有効な`QuadPoints`を持つ上限内のRGBのHighlight、Underline、StrikeOut、Squigglyの辞書について、元のPDFを変更せずに、不足している外観を保守的に補います。

**上限**

- `search_for`: 検索語はUTF-8で4,096バイトまで、結果は`max_hits=4096`までです。`None`で信頼できる結果集合の上限を解除します。
- `get_drawings`: パス8,192個、コマンド131,072個、または破線の値の合計131,072個を超える出力は、部分的に返さずに拒否します。
- `get_svg_image(max_size=64 MiB)`: UTF-8出力。
- `insert_image(max_size=64 MiB, max_pixels=64,000,000)`: エンコード済みの入力と、展開後のPNGの画素数。
- `insert_text`と`insert_textbox`: `max_font_size=64 MiB`、UTF-8で`max_text_size=1 MiB`、4,096行。`insert_textbox`では物理行と折り返し後のレイアウトがどちらも4,096行で止まり、タブ展開は事前に検査されます。
- `insert_ocr_text_layer`: 1回の呼び出しにつき単語4,096個とUTF-8テキスト1 MiBの固定の上限。
- `replace_text(max_size=64 MiB)`: 入力と出力の上限。
- `get_links()`の名前付きリンク先: 項目・ノード65,536個、辺131,072本、32階層、キー16 MiBまでの名前ツリー。
- これらの`max_*`引数のいずれかに`None`を渡すと、信頼できる入力について対応する上限を解除します。

### 位置付きテキストと文字 { #positioned-text }

`Page.get_text("rawdict")`と`Document.get_page_text(pno, "rawdict")`は、文字単位のテキストを返します。結果は`"dict"`の階層を保ちます。ページの`width`/`height`、テキストブロック（`number`、`type=0`、`bbox`、`lines`）、行（`bbox`、`wmode`、`dir`、`spans`）、スパンのメタデータ（`bbox`、`origin`、`size`、`font`、`flags`）を持ちます。各スパンは`text`の代わりに`chars`のリストを持ちます。

| `TextChar`のキー | 意味 |
|---|---|
| `c` | 解釈した1つのPDFの文字の、完全なUnicodeの対応。複数のUnicode文字を含むことがあります |
| `bbox` | 既存の文字の形状から求めた、軸に沿ったおおよその範囲`(x0, y0, x1, y1)` |
| `origin` | 文字のベースラインの始点`(x, y)` |

```python
import pylopdf

with pylopdf.open("report.pdf") as doc:
    layout = doc[0].get_text("rawdict")
    for block in layout["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    print(char["c"], char["bbox"], char["origin"])
```

位置はレンダリングと同じ表示座標のポイントで表します。原点は左上で、xは右向き、yは下向きで、ページの回転とCropBoxのオフセットが適用されます。既定の`/UserUnit=1`では1ポイントは1/72インチです。現在のエンジンは、既定以外の`/UserUnit`の値による物理的な拡大縮小を適用しません。範囲はベースラインとフォントサイズから求めた近似で、正確なインクの輪郭や四辺形ではありません。フォント名とスタイルのフラグは、既存の`"dict"`と同じ解釈です。

1つの項目は対応付けられた1つの文字を表し、1つのUnicodeのコードポイントや書記素とは限りません。合字などの対応は`c`の中でそのまま保たれ、形状から推定した空白や改行のために余分な項目を作ることはありません。対応付けられた文字に明示的に含まれる空白は残ります。そのため、`c`の値をつなげても`get_text("text")`の空白の入り方は再現されないことがあります。対応のない文字は、ほかの抽出モードと同じく重複を除いた`PylopdfWarning`を出してスキップします。

読み順は、既存の行、段組み、保守的なRTL・CJKの判定に従います。CJKの縦書きは文字の配置から推定します。ルビ、割注、一般的な方向の混在した段落、向きの混在した組版は近似のままです。任意の角度のベースラインでは、読み順が元の順序や意味上の順序と異なることがあります。アプリケーションが独自の順序を必要とする場合は、文字の`origin`と行の`dir`を使ってください。`rawdict`は既存の不可視のテキスト層を含め、抽出したPDFのテキストを返します。OCRは実行しません。

階層は[PyMuPDFのrawdict](https://pymupdf.readthedocs.io/en/latest/textpage.html#structure-of-dictionary-outputs)に似ていますが、完全な互換性を約束するものではありません。pylopdfはテキストのブロックだけを返し、複数文字の対応を1つの項目に保ち、PyMuPDFの画像・ベクター、色、不透明度、フォントの寸法、合成文字のフィールドではなく、既存のスパンのメタデータを公開します。画像やベクターの抽出には、`get_image_info()`と`extract_image()`、または`get_drawings()`を使ってください。[重ね表示の例](https://github.com/yhay81/pylopdf/blob/main/examples/rawdict-overlay.py)は、文字の範囲とベースラインの始点を示す単体のHTMLプレビューを作ります。

### ベクター図形 { #vector-drawings }

`get_drawings()`は`DrawingInfo`の辞書を返します。辞書は`type="f"`、`"s"`、`"fs"`、自己完結した直線・3次曲線の`items`、`rect`、RGBと不透明度、塗りの規則、線幅、線端、線の接合、破線、そして`seqno`を持ちます。`seqno`は、ページのパス、テキストの連なり、画像の中での描画操作の位置です。形状は表示座標で、塗りと線の属性は正規化されます。パターンによる塗りは形状を保ち、色と不透明度は`None`になります。クリッピングパス、クリップを適用した可視性、グループとソフトマスクの構造、オプショナルコンテンツのレイヤー名、テキスト、画像、注釈は返しませんが、オプショナルコンテンツの表示状態は適用されます。パス8,192個またはコマンド131,072個を超える結果は、切り詰めずに拒否します。

### テキストの挿入 { #text-insertion }

埋め込みフォントを使う`insert_text`では、すべての文字を含む1つのフォントが必要です。`pylopdf[ar]`、`[he]`、`[hi]`、`[jp]`、`[ko]`、`[th]`、`[zh-cn]`、`[zh-tw]`は、生成用のNoto Sans・Serifフォントを提供します。かな、ハングル、注音符号、アラビア文字、ヘブライ文字、デーヴァナーガリー、タイ文字は、導入済みの提供元を自動で選びます。漢字だけのテキストは日本語を優先する既定を維持します。地域が重要な場合は`font_language="zh-CN"`、`"zh-TW"`、`"ko"`、`"ja"`を渡してください。これは連なり全体で1つのフォントを選ぶ動作で、文字ごとの代替ではありません。各行は字形処理されますが、双方向の段落レイアウトと折り返しはこの基本機能の範囲外です。RTLの字形処理は正しく描画されます。RTLだけの行と、RTLのテキストに両側を挟まれたラテン文字または数字の語が1つだけある行は、論理的なUnicodeの順序で抽出されます。行端の語、複数の語、一般的な方向の混在した段落は、作成元の視覚的な順序のままです。

複雑な字形処理では、文字と1対1に対応しない論理的な文字列を保つために、PDFの`/ActualText`が必要になることがあります。生成したPDFは外部のビューアー向けにそのデータを含みますが、pylopdfの現在の抽出処理は`/ActualText`を読みません。そのため、特に長いデーヴァナーガリーの連なりなど、並べ替えや文脈に依存する文字のまとまりは、開き直した後の抽出が近似になることがあります。

`insert_text`は文字列を`str.splitlines()`で行に分け、リストは1項目を1行とします。ページの下にはみ出す行は描かず、描いた行数を返します。

`insert_textbox`は、リッチテキストのエンジンにはならずに折り返しを加えます。明示的な改行を保ち、タブを展開し、CJKをUnicodeの改行可能位置で区切り、長すぎる単語には書記素を壊さない緊急の折り返しを使います。整列の定数は`TEXT_ALIGN_LEFT`、`TEXT_ALIGN_CENTER`、`TEXT_ALIGN_RIGHT`、`TEXT_ALIGN_JUSTIFY`です。戻り値が負の場合は高さの不足分で、その場合はページの内容もフォントのリソースも追加されません。

どちらのメソッドもpymupdfと同じ位置に行を置きます。既定では行の間隔は`fontsize`の`lineheight`倍で、`lineheight`の既定値はフォントの`ascender`から`descender`を引いた値です。

??? note "行の配置の詳細"

    `insert_text`は各行を前の行から`fontsize`の`lineheight`倍だけ下に置きます。`lineheight`を省くと、倍率はフォントの`ascender`から`descender`を引いた値で、その差が1以下なら1.2です。そのため、Helveticaの10ポイントでは行の間隔は13.74ポイントになります。pymupdfと同じく、この差はMuPDFが返す単精度の寸法で比べます。その精度では、ascenderが0.8、descenderが-0.2のフォントはわずかに1 emを超え、行の間隔は1 emのままです。

    `insert_textbox`も同じ既定値を使い、最初のベースラインを`rect`の上端からascender 1つ分下に置き、以降の行をascenderの`lineheight`倍ずつ下に置きます。テキストが収まるかどうかの判定と、返す余りの高さには、1行あたり`fontsize`の`lineheight`倍とdescender 1つ分を数えます。

    `insert_text`は、どの行を描くかもpymupdfと同じく決めます。文字列は`str.splitlines()`で分けるため、末尾の改行は行を増やさず、`\v`、`\f`、`\x1c`から`\x1e`、`\x85`、`\u2028`、`\u2029`も`\n`、`\r`、`\r\n`と同じく行を区切ります。リストやタプルは1項目を1行として、そのまま使います。最初の行は必ず描き、以降の行はベースラインが`page.rect`の下端より下に出ない間だけ描きます。戻り値は描いた行数で、`""`や`"\n"`のように描くもののないテキストは何も挿入せずに0を返します。pymupdfは回転前の座標でユーザー空間のy = 0まで余白を測り、pylopdfは表示座標で`page.rect`の下端まで測ります。両者は、回転しておらず見える範囲がy = 0で終わるページで一致します。ページの下にはみ出す行も含め、フォントはすべての行を検査します。

### フォント { #fonts }

#### フォントを読み込む

`Font`はpymupdfの`Font(fontname=None, fontfile=None, fontbuffer=None)`に従い、`insert_text(font=)`と`insert_textbox(font=)`に渡します。OpenTypeのフォントは`set_form_field(font=)`にも渡せます。

```python
helvetica = pylopdf.Font("helv")
noto = pylopdf.Font(fontfile="NotoSansJP-Regular.otf")
page.insert_text((72, 72), "Hello", font=noto, fontsize=12)
```

`Font("helv")`や`Font("Times-Bold")`はStandard 14のフォントを指し、埋め込まずに描き、AdobeのAFMの幅で測ります。pymupdfのほかの内蔵フォントは使えません。`Font(fontfile=)`と`Font(fontbuffer=)`はOpenTypeのフォントをそのまま読み込みます。`Font(language="ja")`や`Font(family="Noto Sans JP")`は導入済みのフォントextraのフォントを選び、`is_serif=True`で明朝体を選びます。

`font.text_length(text, fontsize)`は、カーニングや合字を含め、描画と同じ字形処理で測ります。そのため、レイアウトのコードは描くフォントのままで折り返し、揃え、ページ割りを計算できます。`has_glyph(chr)`はコードポイントの文字番号を返し、なければ0を返します。

pylopdfはフォントの登録表を持たず、システムのフォントフォルダーも探しません。描画の呼び出しはそれぞれ、自身が使う文字のサブセットを埋め込みます。[図形](#shapes)で描けば、各フォントは確定1回につき1度だけ埋め込まれます。

#### 太さと可変フォント

`Font(fontfile=)`と`Font(fontbuffer=)`は可変フォントを既定のインスタンスで読み込みます。既定のインスタンスがRegularとは限らず、可変フォントのNoto Sans CJK JPの既定はThinです。`weight=`を渡すと、可変フォントの太さの軸から別のインスタンスを選びます。

```python
bold = pylopdf.Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)
page.insert_text((72, 100), "Summary", font=bold, fontsize=18)
```

`Font(fontfile="NotoSansCJKjp-VF.otf", weight=700)`はBoldのインスタンスの送り幅で測り、`NotoSansCJKjp-Bold`として埋め込みます。`weight=650`も使えるため、こうしたフォントでは`weight=400`と太さを省略した場合とで結果が異なります。軸の範囲外の太さは`PdfError`を送出し、静的なフォントが宣言していない太さも同じです。`insert_text`、`insert_textbox`、`set_form_field`の`fontfile`と`fontbuffer`の引数は既定のインスタンスで描きます。`Font`が選んだインスタンスで描くには、その`Font`を`font=`に渡してください。

CJKのextraはスタイルごとに1つの静的なフォントを導入するため、フォントと一致しない`is_bold`、`is_italic`、OS/2の正確な`weight`の指定は`PdfError`を送出します。Noto Sans JP Boldのようなほかの太さは、そのファイルから読み込んでください。一方、`pylopdf[ar]`、`[he]`、`[hi]`、`[th]`のextraは可変フォントを導入します。`Font(language="he", is_bold=True)`は別のファイルなしにフォントの太さの軸からBoldのインスタンスを選び、そのインスタンスの送り幅で測り、独自のPostScript名`NotoSansHebrew-Bold`で埋め込みます。太さの範囲は100から900で、`pylopdf[ar]`のセリフ体であるNoto Naskh Arabicは400から700です。軸の範囲外の太さや斜体の指定は`PdfError`を送出します。

導入済みの可変フォントは、`insert_text`、`insert_textbox`、`set_form_field`が自動で選ぶ場合も含めて、Regularの太さで描きます。Noto Sans Hebrewの既定のインスタンスはThinだからです。pylopdfは文字の輪郭線や重ね塗りで太さを真似ることはしません。`allow_regular_fallback=True`を指定すると、`PylopdfWarning`を出したうえでextraのフォントを明示的に受け入れます。pymupdfなら無視するところでも、`fontname`やフォントファイルと一緒に`is_bold`、`is_italic`、`is_serif`を渡すと例外を送出します。

#### 行の寸法

`ascender`と`descender`はpymupdfが返すemに対する比率で、`insert_text`と`insert_textbox`はこれを使って行を置きます。OpenTypeのフォントでは、フォントが`USE_TYPO_METRICS`を設定していればOS/2のタイポグラフィックの値、そうでなければ`hhea`の値で、FreeTypeがMuPDFのために読む値と同じです。Standard 14のフォントでは、MuPDFがそのフォントを描くのに使うURWフォントの外接矩形の値で、Helveticaなら1.075と-0.299です。

### 図形 { #shapes }

`page.new_shape()`はpymupdfの`Shape`を返します。`draw_line`、`draw_polyline`、`draw_rect`（`radius=`で角を丸める）、`draw_quad`、`draw_circle`、`draw_oval`、`draw_sector`、`draw_curve`、`draw_bezier`、`draw_squiggle`、`draw_zigzag`は表示座標でパスの線分を追加し、pymupdfと同じ終点を返します。前の線分の終点から始まる線分は、同じ部分パスを続けます。`finish(*, width=1, color=(0,), fill=None, lineCap=0, lineJoin=0, dashes=None, even_odd=False, closePath=True, fill_opacity=1, stroke_opacity=1)`は、前の`finish`以降に描いたものすべてを線と塗りで仕上げます。色は0から1のグレー、RGB、CMYKで、破線は`"[3 4] 0"`のようなPDFの破線パターンで指定します。`insert_text`と`insert_textbox`は、`font=`と`lineheight=`を含め`Page.insert_text`と`Page.insert_textbox`のフォントの指定を受け取り、それらのメソッドと同じ位置に行を置きます。`insert_text`は`Page.insert_text`と同じくテキストを行に分けてページの下にはみ出す行を除き、追加した行数を返します。`insert_textbox`は使わなかった高さを返し、テキストが収まらない場合は負の値を返して何も追加しません。`commit(overlay=True)`で図形を描きます。pymupdfと同じく、仕上げたパスを順に描き、その上にすべてのテキストを描きます。

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

1回の確定で1つのForm XObjectを置きます。パスと埋め込みフォントのテキストはまとめて生成されるため、何行で使っても各フォントは確定1回につき1度だけ埋め込まれます。ページのテキストを1つの図形で描けば、各フォントはページごとに1度だけ埋め込まれます。`with`のブロックは正常に終わると確定し、例外が起きた後は何も描きません。確定していない描画を持ったまま破棄された図形は`ResourceWarning`を出します。pymupdfが黙って描画を捨てたり黒で描いたりするところでは、pylopdfは`PdfError`を送出します。`commit()`は`finish()`で仕上げていない描画を受け付けず、`finish()`は何も描かない指定（塗りがなく、`color=None`または`width=0`）を受け付けません。`morph=`と`oc=`は使えません。図形が確定までに保持できるパスの点は1,048,576個までで、それを超えるとコード`path_point_count`の`LimitError`を送出します。

#### 描画メソッド { #drawing-methods }

`Page.draw_*`メソッドは同じ指定をキーワード引数で受け取り、それぞれ仕上げた図形を1つ描いて、pymupdfと同じ終点の`Point`を返します。

- メソッド: `draw_line`、`draw_polyline`、`draw_rect`、`draw_quad`、`draw_circle`、`draw_oval`、`draw_sector`、`draw_curve`、`draw_bezier`、`draw_squiggle`、`draw_zigzag`。いずれも最初に形状を取ります。
- 共通のキーワード: `color=(0,)`、`fill=None`、`width=1`、`dashes=None`、`lineCap=0`、`lineJoin=0`、`closePath=`、`overlay=True`、`stroke_opacity=1`、`fill_opacity=1`。`fill`と`closePath`は、pymupdfが提供するメソッドにだけあります。
- メソッド固有のキーワード: `draw_rect`の`radius=`、`draw_sector`の`fullSector=`、波線（`draw_squiggle`、`draw_zigzag`）の`breadth`。`breadth`はpymupdfと同じく、点の後に位置引数でも渡せます。

### フォームの入力 { #form-filling }

`set_form_field`は、テキスト、コンボボックス・リストボックスの選択、チェックボックス、ラジオボタンのウィジェットの外観を生成します。WinAnsiのテキストはHelveticaで自動的に収まるように縮小されます。Unicodeをサブセット埋め込みするにはOpenTypeの`fontfile`または`fontbuffer`を渡し、可変フォントのBoldのインスタンスのように、読み込み済みのOpenTypeの`Font`を`font`として渡すこともできます。`Font`は`text_length`が測る送り幅で縮小、整列、コームの各位置の中央への配置を行い、ほかのフォントの引数とは併用できません。Standard 14の`Font`は`PdfError`を送出します。導入済みの言語・文字体系のフォント提供元は`insert_text`と同じ規則で選ばれ、`font_language`は曖昧な漢字の地域を指定します。

既存の空でないチェックボックス・ラジオボタンの外観は保持し、不足している状態にはベクターの印を生成します。ほかのWinAnsiのフィールドで不足している外観も同時に補い、入力可能なすべてのウィジェットが自己完結した場合にだけ`NeedAppearances`を解除します。コームのテキストフィールドは継承された`MaxLen`と整列に従い、Unicodeの書記素を各位置の中央に配置し、長すぎる値は文書を変更せずに拒否します。リッチテキスト、プッシュボタンのアクション、署名は生成しません。

### 表 { #tables }

`Table.confidence`は0–1の決定的な順位付けの経験則で、較正された確率ではありません。`Table.diagnostics`は`TableDiagnostics`のタプルで、検出方式と、罫線のないテキストの表ではemで正規化した整列の誤差、最小の列間の余白、行間のばらつきを持ちます。完全なベクターのグリッドは1.0、疎な罫線を補完したグリッドは0.95で、どちらもこれらのテキスト専用の指標は`None`です。`TableFinder.strategy`と`TableFinder.clip`には使った設定が残ります。

## 低水準オブジェクト { #low-level-objects }

`doc.objects`はオブジェクト番号から文書の間接オブジェクトへの対応で、`doc.trailer`、`doc.catalog`、`page.obj`は辞書のハンドルです。この型付きのモデルはpymupdfの文字列ベースの`xref_*`メソッドを置き換えます。オブジェクトはPDFの構文ではなく、Pythonの値として扱います。

```python
from pylopdf import Name

catalog = doc.catalog
catalog["PageLayout"] = Name("TwoColumnLeft")   # 名前。素のstrはテキスト文字列になる
font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (Hi) Tj ET")
page.obj["Resources"] = {"Font": {"F9": doc.objects[font]}}   # 間接ハンドルは参照として格納される
page.obj["Contents"] = doc.objects[content]
doc.objects[content].read()                    # 復号済み。既定で64 MiBまで
```

| 値 | Python |
|---|---|
| null、真偽値、整数、実数 | `None`、`bool`、`int`、`float` |
| 名前 | `Name`（スラッシュを含まない`str`のサブクラス） |
| 文字列 | `String`（`bytes`のサブクラス。`.text`でPDFのテキスト文字列を復号） |
| 配列 / 辞書 / ストリーム | ライブな`Array`（変更可能なシーケンス） / `Dictionary`（変更可能なマッピング） / `Stream`（`read(*, decode=True, max_size=64 MiB)`と`write(data, *, filter="FlateDecode")`を持つ辞書） |

参照は読み取り時に解決され、存在しないオブジェクトへの参照は`None`として読みます。書き込みはすぐに反映され、レンダリングと抽出のキャッシュを破棄します。ページの順序を変える書き込みは、既存の`Page`ビューも無効にします。リストとマッピングは直接オブジェクトとして書き込まれ、コンテナに格納した間接ハンドルは参照になり、`doc.objects[xref]`への代入や`add()`に渡したハンドルはコピーされます。オブジェクトが削除されるか、`save(garbage=2)`以上でオブジェクト番号が振り直されると、ハンドルは`StaleObjectError`を送出します。生の編集を意味的に正しく保つのは、呼び出し側の責任です。

## モジュールレベルの名前 { #module-level }

| 名前 | 用途 |
|---|---|
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | メタデータとページ数の高速な読み取り。`repaired`は相互参照の復旧を報告します。 |
| `Permissions` | 暗号化の許可フラグ（IntFlag）。メンバーはpymupdfの`PDF_PERM_*`定数です。 |
| `EncryptionMethod` | `save(encryption=)`の方式（IntEnum）。メンバーはpymupdfの`PDF_ENCRYPT_*`定数です。 |
| `Base14_fontnames`、`Base14_fontdict` | pymupdfのStandard 14のPostScript名と、`fontname=`が使う読み取り専用の対応表。略称と正式名を大文字小文字を問わず受け付けます。 |
| `LinkKind`、`TextAlign` | メンバーがpymupdfの`LINK_*`と`TEXT_ALIGN_*`定数であるIntEnum。 |
| `Rect`、`IRect` | 包含、和、積、変換、丸めを備えた不変の`(x0, y0, x1, y1)`のNamedTuple。 |
| `Quad` | 変換、包含、形状の判定を備えた、4つの`Point`による不変の`(ul, ur, ll, lr)`のNamedTuple。 |
| `Point` | ベクトル演算、アフィン変換、距離、方向を備えた不変の`(x, y)`のNamedTuple。 |
| `Matrix`、`IdentityMatrix`、`Identity` | 合成と逆変換を備えた不変のアフィン変換`(a, b, c, d, e, f)`のNamedTupleと、恒等変換。 |
| `EMPTY_RECT()`、`INFINITE_RECT()`とその`IRECT` / `QUAD`形、`FZ_MIN_INF_RECT`、`FZ_MAX_INF_RECT`、`planish_line(p1, p2)` | pymupdfの幾何定数と、線分を水平にする行列。 |
| `TextPageDict`、`TextBlock`、`TextLine`、`TextSpan` | `get_text("dict")`のTypedDictの階層。 |
| `RawTextPageDict`、`RawTextBlock`、`RawTextLine`、`RawTextSpan`、`TextChar` | 文字の対応、範囲、ベースラインの始点を持つ`get_text("rawdict")`のTypedDictの階層。 |
| `ImageInfo`、`ImageCompressionResult`、`DrawingInfo`、`AnnotationInfo`、`LinkInfo`、`FormFieldInfo` | ページ、文書の操作、フォームの辞書形式の結果を表すTypedDictの契約。 |
| `PageLabelInfo`、`PageLabelSpec` | 正規化したページラベルの出力と、設定時の入力の契約。 |
| `DocumentMetadata`、`MetadataUpdate`、`MetadataProbe` | メタデータの出力、部分的な更新、高速な読み取りの契約。 |
| `DocumentLimits`、`DocumentComplexity` | 信頼できない入力に対する不変の上限と、軽量な構造のTypedDict。 |
| `OcrEngine`、`OcrWord` | 再利用できる純Rust製のPP-OCRエンジンと、その位置付きの結果の契約。 |
| `Font(fontname=None, fontfile=None, fontbuffer=None, *, language=None, weight=None)` | 再利用できるStandard 14またはOpenTypeのフォント。[フォント](#fonts)を参照してください。 |
| `Shape(page)` | パスとテキストを1回で描くpymupdfの組み立て用クラス。`page.new_shape()`で作成します。 |
| `Name`、`String`、`Array`、`Dictionary`、`Stream`、`Objects` | 低水準のPDFの値とライブなオブジェクトのハンドル（[低水準オブジェクト](#low-level-objects)）。 |
| `Word`、`Block` | pymupdfのタプルの順序による`get_text("words")`と`get_text("blocks")`のNamedTuple。 |
| `OcrRotation`、`DrawingItem`、`FormFieldType` | 実行時にimportできる、OCRの回転、ベクターのコマンド、リテラルの型エイリアス。 |
| `TableFinder`、`Table`、`TableDiagnostics` | 所有権を持つ表の形状、セルのテキスト（結合セルの続きは`None`）、検出方式と信頼度の根拠。[表](#tables)を参照してください。 |
| `PdfError`、`LimitError`、`PasswordError`、`FileDataError`、`EmptyFileError`、`OcrError`、`DocumentClosedError`、`EncryptedDocumentError`、`StalePageError`、`StaleObjectError` | ValueError互換の基底を持つ例外の階層。 |
| `Pixmap` | 不変のRGB（`n == 3`）またはストレートアルファのRGBA（`n == 4`）の画素。 |
| `Colorspace`、`csRGB`、`csGRAY`、`csCMYK`、`CS_RGB`、`CS_GRAY`、`CS_CMYK` | pymupdfのデバイス色空間とその番号。 |
| `PylopdfWarning` | 復旧可能な解釈の警告（相互参照の修復、フォントの解決、画像の復号）。 |

**詳細**

- `peek_metadata`は、`max_file_size`を設定した場合にそれを超える入力を拒否し、パスワードをUTF-8で127バイトまでに制限します。
- `DocumentLimits`には、PDFスナップショット用の`max_interpretation_size`と、位置付きレイアウト用の`max_text_glyphs`も含まれます。
- `OcrEngine.recognize(pixmap, *, tile_size=, overlap=, min_confidence=, rotation=)`は、任意の`Pixmap`を画素座標で読みます。
- `Font`は`is_bold=False`、`is_italic=False`、`is_serif=False`、`fontindex=0`、`family=None`、`allow_regular_fallback=False`、`max_font_size=64 MiB`も取ります。`text_length`、`ascender` / `descender`、`has_glyph`、`name`、`family`、`weight`、`is_bold`、`is_italic`、`is_serif`、`units_per_em`を持ちます。`weight=`は可変フォントのファイルのインスタンスも選びます。
- `Table.to_markdown(max_size=64 MiB)`は、エスケープ後のUTF-8出力を事前に検査します。
- 上限に達したときの例外は、安定した`.code`を公開します。読めない入力は`FileDataError`（`RuntimeError`でもある）、空の入力は`EmptyFileError`、存在しないファイルは組み込みの`FileNotFoundError`を送出します。
- `Pixmap`は`samples`、`width`、`height`、`stride`、`n`、`alpha`、`colorspace`、`tobytes(max_size=64 MiB)`と、ストリーム出力し失敗時もアトミックなPNG専用の`save(filename)`を持ちます。どのwheelでも、読み取り専用でゼロコピーの`memoryview()`に対応します。

`TypedDict`の契約は静的な型付けにだけ作用し、値は通常のpymupdf形式の辞書のままです。`LinkInfo`は`kind`と`from`を必須とし、リンク先の種類ごとのキーは任意です。`PageLabelSpec`は`startpage`を必須とし、`style`、`prefix`、`firstpagenum`は実行時の既定値を保ちます。

## 点と行列の幾何 { #geometry }

`Point()`は原点を作り、`Point(x, y)`と`Point((x, y))`は位置を作ります。`Matrix()`はゼロ行列を作り、`Matrix(sx, sy)`は拡大縮小、`Matrix(degrees)`は回転、`Matrix(sx, sy, 1)`はせん断です。明示的な変換には、6つの座標か、6項目のシーケンスを1つ渡します。座標のキーワード引数は、すべての座標を指定する場合に使えます。どちらのクラスも不変のNamedTupleで、インデックス、アンパック、タプルとしての等価性、ハッシュ、pickleはタプルと同じく動作し、演算は幾何の値を返します。

```python
from pylopdf import Matrix, Point

scale = Matrix(2, 3)
shift = Matrix(1, 0, 0, 1, 20, 30)
transform = scale * shift
position = Point(10, 20) * transform  # Point(x=40.0, y=90.0)
original = position / transform      # Point(x=10.0, y=20.0)
page.insert_text(position, "Transformed position")
```

乗算は変換を左から右へ合成します。点`(x, y)`に`(a, b, c, d, e, f)`を掛けると`(a*x + c*y + e, b*x + d*y + f)`になります。表示座標ではy軸が下向きなので、`Matrix(90)`は`(1, 0)`を`(0, 1)`へ移します。`~matrix`は逆行列を返し、特異な場合はゼロ行列を返します。`point / matrix`と`matrix / other`は、割る側が特異な場合に`ZeroDivisionError`を送出します。有限の変換でも、結果を表現できない場合は`PdfError`を送出します。

| 演算 | 結果 |
|---|---|
| `point + other`、`point - other` | 点、2項目のシーケンス、またはスカラーとの成分ごとの演算 |
| `matrix + other`、`matrix - other` | 行列、6項目のシーケンス、またはスカラーとの成分ごとの演算 |
| `value * scalar`、`value / scalar`、`-value`、`+value` | 新しい値。単項の`+`では同じ不変の値 |
| `abs(value)`、`value.norm()` | 座標のユークリッド長 |
| `bool(value)` | すべての座標が0の場合だけ偽 |
| `point.unit`、`point.abs_unit` | 単位方向。後者は成分を非負にします。原点は0のまま |
| `point.distance_to(other, unit="px")` | 点、または正規化した矩形内の最も近い位置までの距離。単位は`px`（PDFのポイント）、`in`、`cm`、`mm` |
| `matrix.is_rectilinear` | 軸が揃ったままかどうか。0の判定には`1e-5`の許容誤差を使います |

座標は有限の数でなければなりません。座標のシーケンスの長さが誤っている場合や有限でない値は`PdfError`を、無効な引数の型は`TypeError`を送出します。入力は長さに上限のあるシーケンスで、任意のイテラブルではありません。`transform`、`invert`、`pretranslate`のような値を変更するメソッドはありません。代わりに演算子の結果を代入してください。加算とスカラー倍では、幾何の値を左側に置きます。

これらは値の演算なので、変換した点を描画に渡すと位置が変わりますが、テキストの向きや大きさは変わりません。`get_pixmap(matrix=)`は拡大縮小の行列だけを受け付け、回転とせん断には対応しません。

## 矩形と四辺形 { #rectangles }

`Rect()`はゼロの矩形です。`Rect(x0, y0, x1, y1)`、`Rect((x0, y0, x1, y1))`、`Rect(top_left, bottom_right)`、`Rect(top_left, x1, y1)`、`Rect(x0, y0, bottom_right)`は、座標または角の点を取ります。`IRect`は同じ引数を受け付け、端数のある辺を外側へ丸めます。`x0`と`y0`は切り下げ、`x1`と`y1`は切り上げで、演算の後も同じです。`Quad(ul, ur, ll, lr)`は4つの点を取り、`rect.quad`は矩形を四辺形に変えます。矩形を返すページのAPIはすべて`Rect`を返し、矩形を取るAPIはすべて4項目の通常のシーケンスも受け付けます。

```python
from pylopdf import Matrix, Point, Rect

hits = page.search_for("Total")
area = hits[0] | hits[-1]                    # 最初と最後のヒットの和
inside = [hit for hit in hits if hit in page.rect]
box = area * Matrix(1, 0, 0, 1, 0, 20)        # 20ポイント下へ移動
quad = area.morph(area.tl, Matrix(30))       # 角を中心に30度回転
```

| 演算 | 結果 |
|---|---|
| `rect + other`、`rect - other` | 矩形、4項目のシーケンス、またはスカラーとの成分ごとの演算 |
| `rect * scalar`、`rect / scalar` | すべての座標を拡大縮小 |
| `rect * matrix`、`rect / matrix` | 変換した角の外接矩形。無限の矩形は無限のまま |
| `rect \| point`、`rect \| other` | 点または矩形も含む最小の矩形 |
| `rect & other` | 共通部分。重ならない矩形では無効な空の結果 |
| `point in rect` | `x0 <= x < x1`かつ`y0 <= y < y1` |
| `other in rect`、`quad in rect` | 有効な矩形、または四辺形の外接矩形が辺の内側にあること。辺上も内側とみなします |
| `rect.width`、`rect.height` | 負になりません |
| `rect.is_empty`、`rect.is_valid`、`rect.is_infinite` | `x0 >= x1`または`y0 >= y1`、`x0 <= x1`かつ`y0 <= y1`、`INFINITE_RECT()`と等しいこと |
| `rect.intersects(other)` | 有限で空でない2つの矩形が面積を共有するかどうか |
| `rect.round()`、`rect.irect` | 覆う`IRect`。各辺で0.001までの誤差を許容します |
| `rect.torect(other)` | 一方の矩形をもう一方へ写す行列 |
| `rect.morph(point, matrix)`、`quad.morph(point, matrix)` | 固定点を中心に変換した`Quad` |
| `rect.normalized()` | 辺の順序を整えた矩形。pymupdfのその場で変更する`normalize()`の代わり |
| `abs(rect)`、`rect.get_area(unit="px")` | 面積。空または無限の矩形では`abs()`は0 |
| `quad * matrix`、`quad / matrix`、`quad + other` | すべての角を変換または移動 |
| `point in quad`、`rect in quad`、`other_quad in quad` | 辺上を含む包含。矩形や四辺形では、すべての角が内側にある必要があります |
| `quad.rect`、`quad.width`、`quad.height` | 外接矩形と、向かい合う辺の組それぞれの長い方 |
| `quad.is_rectangular`、`quad.is_convex`、`quad.is_empty` | `rect.quad`の回転方向で1e-5以内の直角であること、凸の輪郭であること、1e-5より短い辺の組があること |

pymupdfのその場で変更する`include_point`、`include_rect`、`intersect`、`normalize`、`transform`はありません。代わりに`r | p`、`r & other`、`r.normalized()`、`r * m`を代入してください。pymupdfと同じく、`IRect`は`abs()`を持たず常に真で、`Rect`は4つの座標がすべて0の場合だけ偽、`Quad`は空の場合に偽です。pymupdfは四辺形内の矩形を2つの角だけで判定しますが、pylopdfは4つの角すべてを検査します。
