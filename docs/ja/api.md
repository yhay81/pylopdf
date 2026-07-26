---
title: API一覧
description: pylopdfのDocument、Page、Pixmap、Rect、権限、警告、例外を俯瞰するAPIマップ。
---

# API 一覧

詳細な docstring はパッケージ内にあります（`help(pylopdf.Document)`）。この
ページは地図です。ページ番号は `get_toc` / `set_toc`（pymupdf 互換の 1 始まり）を
除きすべて 0 始まり。座標はすべて左上原点の表示空間です。
[API安定性ポリシー](stability.md)で公開範囲と非推奨化の手順を定義しています。

## Document { #document }

`pylopdf.Document(filename=None, stream=None, password=None, max_decompressed_size=None, *, limits=None)` —
`pylopdf.open()` は別名コンストラクタ。with 文に対応。

| メンバ | 用途 |
|---|---|
| `doc[i]` / `load_page(pno)` / イテレーション | `Page` ビュー（負数可。構造変更後は取得し直す） |
| `page_count` / `len(doc)` | ページ数 |
| `limits` / `complexity` | 読み込み時の不変な資源ポリシー / streamを展開しない軽量な構造指標 |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | 暗号化状態と復号（pymupdf 互換の意味論） |
| `is_repaired` | 最終classic `startxref`の誤りを読み込み時に修復したか。保存するとxref dataを正規化 |
| `metadata` / `set_metadata(dict)` | Info 辞書（UTF-16BE 対応） |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None, table_strategy="lines")` | Markdown変換（見出し・CJK連結・強調・リスト・複数カラム・保守的な縦書き順。既定で罫線表、`"text"`で罫線なし表を追加、`None`で表変換を無効化） |
| `render_page(...)` / `render_pages(..., workers=)` / `render_page_svg(...)` | PNG、順序保証の並列 PNG 群、SVG |
| `compress_images(dpi=150, quality=75)` | 安全なDCT/Flate raster XObjectを配置DPIに応じて非可逆縮小・JPEG再圧縮し、型付きのbyte/count統計を返す |
| `set_fallback_font(font, kind=, index=)` | 非埋め込み CJK の代替フォント |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | ページ操作 |
| `get_toc()` / `set_toc(toc)` | しおり（1 始まり） |
| `get_page_labels()` / `set_page_labels(labels)` | ページラベル |
| `get_form_fields()` / `set_form_field(name, value, fontfile=, fontbuffer=, fontindex=)` | ネイティブ外観付き AcroForm の一覧と記入 |
| `embfile_add / embfile_names / embfile_get / embfile_del` | 添付ファイル |
| `get_pdfa_claim()` | XMP の PDF/A 宣言（読み取りであって検証ではない） |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | with 文でも |

`compress_images()`は全ページを解釈して、各間接raster objectが最も大きく配置される
寸法を求めてから、lopdfのcloneへ原子的に編集します。`dpi=None`では縮小せず、
quality再圧縮だけを行います。対象はmaskや独自decode arrayを持たない、直接かつ
単一filterの8-bit DeviceGray/DeviceRGB DCT/Flate streamに限定します。DCTの
decode parameterは対象外で、Flateはpredictorなし、または辞書と整合するPNG
predictorに対応します。解釈された非対応の間接画像と結果が小さくならないencodingは
skipし、inline画像は集計対象外です。同じ設定の再実行は冪等です。

## Page { #page }

| メンバ | 用途 |
|---|---|
| `number` / `parent` / `get_label()` | 素性と表示ラベル |
| `get_text(option)` / `search_for(needle)` | 抽出と検索（大文字小文字を区別しない） |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | 編集せずローカルPP-OCRv6で位置付き単語を認識。`rotation`は入力を時計回りに補正し、`clip`は表示座標 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 向きを保持した不可視の検索可能層を挿入。選択領域の既存テキストは既定でスキップ |
| `find_tables(strategy="lines", clip=None)` | 完全なベクタ罫線、保守的に補完した疎な罫線、結合セル。`"text"`で罫線なし検出、`clip`で表示座標の領域を指定 |
| `to_markdown(table_strategy="lines")` | ドキュメントと同じ表制御を持つ 1 ページ分の Markdown |
| `get_images()` | 描画された画像（`bbox` 付き。JPEG パススルー / PNG） |
| `get_drawings()` | ページで解釈されたベクターの fill/stroke パス。表示座標の line/cubic 形状と正規化された描画属性 |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(...)` / `render_svg()` | レンダリング。`clip` は表示座標 |
| `rotation` / `set_rotation(deg)` | 表示回転 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | ページボックス |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=)` | JPEG/PNG、または描画済みRGBA `Pixmap`の挿入。`rotate`は90度単位の時計回り回転 |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | PDFページをベクタのまま重ねる。`src`は同じ文書でもよい |
| `insert_text(point, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, overlay=)` | 標準14または字形処理済みsubsetを印字。`pylopdf[cjk]`導入時は日本語・漢字にJP fontを自動選択 |
| `insert_textbox(rect, text, fontsize=, fontname=, fontfile=, fontbuffer=, fontindex=, color=, align=, expandtabs=, lineheight=, overlay=)` | Core 14、明示OpenType、または自動選択JP fontの実幅でUAX #14折り返し。残り高さを返し、収まらなければ描画しない |
| `insert_ocr_text_layer(words, rotation=)` | 向きを保持した不可視OCRテキスト層（searchable PDF化） |
| `replace_text(search, replacement, default_char=)` | 単純エンコーディングのテキスト置換 |
| `annots()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 注釈 |

`get_drawings()`は`type="f"` / `"s"` / `"fs"`、自己完結したline/cubicの
`items`、`rect`、RGB/opacity、fill rule、width、cap、join、dashesを持つ
`DrawingInfo`辞書を返します。pattern paintでは形状を保持し、色とopacityは`None`です。
clip path、clip適用後の可視判定、group/soft-mask構造、optional-content layer名、
text、image、annotationは返しませんが、optional-contentの表示状態は適用されます。
8,192 pathsまたは131,072 commandsを超える結果は切り詰めず拒否します。

埋め込みフォントを使う`insert_text`では、すべての字形を含む単一フォントが必要です。
sourceを省略し`pylopdf[cjk]`が入っていれば、日本語・漢字にはJP subsetのNoto Sans、
Times系`fontname`にはNoto Serifを自動選択します。これはrun全体で1 fontを選ぶ動作で、
glyphごとのfallbackではありません。Hangul、中国語地域に合う字形、他script、別書体は
OpenType fontを明示します。各行の字形処理は行いますが、双方向paragraph layoutと
折り返しは行いません。RTLの字形処理結果は正しく描画されますが、現時点の抽出順は
論理順ではなく視覚順です。

`insert_textbox`はリッチテキストエンジンではなく、明示改行、tab展開、CJKのUnicode
改行位置、長すぎる単語のgrapheme単位の緊急折り返しを扱います。整列には
`TEXT_ALIGN_LEFT`、`TEXT_ALIGN_CENTER`、`TEXT_ALIGN_RIGHT`、
`TEXT_ALIGN_JUSTIFY`を使います。戻り値が負なら高さ不足で、ページ内容やフォント
resourceは追加されません。

`set_form_field`はテキスト、コンボ／リスト選択、チェックボックス、ラジオボタンの
外観を生成します。WinAnsiはHelveticaで自動縮小され、UnicodeはOpenTypeの
`fontfile`または`fontbuffer`を指定するとサブセット埋め込みされます。
`pylopdf[cjk]`の導入時は、WinAnsi外の値にJP subsetのsans fontを試します。Hangulや
中国語地域に合う字形には対応fontを明示します。既存の
空でないボタン外観は保持し、不足する状態だけをベクタで生成します。他のWinAnsi
フィールドに不足する外観も同時に補完し、記入可能な全widgetが自己完結したときだけ
`NeedAppearances`を解除します。combテキスト欄は継承された`MaxLen`と整列を尊重し、
Unicode graphemeを各位置の中央に配置して、長すぎる値を文書無変更で拒否します。
リッチテキスト、pushbutton action、署名は生成しません。

`Table.confidence`は0–1の決定的な順位付けheuristicで、校正された確率ではありません。
`Table.diagnostics`は`TableDiagnostics` tupleです。罫線なし表ではem正規化したalignment
誤差、最小gutter、行間ばらつきを保持します。完全なベクタグリッドは1.0、
疎な罫線を補完したhybrid gridは0.95で、いずれもこれらtext専用metricは`None`です。
`TableFinder.strategy`と`TableFinder.clip`には
使用した設定が残ります。

## モジュールレベル { #module-level }

| 名前 | 用途 |
|---|---|
| `peek_metadata(path_or_stream, password=)` | 高速メタデータ読み取り。`repaired`で限定的なclassic `startxref`修復を確認 |
| `Permissions` | 暗号化の許可フラグ（IntFlag） |
| `Rect` | 矩形の NamedTuple（`width` / `height` 付き） |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` の TypedDict 階層 |
| `ImageInfo` / `ImageCompressionResult` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` / `DrawingInfo` | page・document操作・form・vector drawing の辞書形式結果を表す TypedDict |
| `DrawingItem` | line/cubic 描画コマンドを表す型 alias |
| `PageLabelInfo` / `PageLabelSpec` | 正規化済みページラベル出力／setter入力の契約 |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata出力／部分更新／高速probeの契約 |
| `DocumentLimits` / `DocumentComplexity` | 信頼できない入力の不変な上限／軽量な構造TypedDict |
| `OcrEngine` / `OcrWord` | 再利用可能な純Rust PP-OCRエンジン／位置付き結果の契約 |
| `OcrRotation` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtimeでimportできるOCR回転・tuple・literal型alias |
| `TableFinder` / `Table` / `TableDiagnostics` | 所有権を持つ表の座標、セル文字列（結合継続位置は`None`）、strategy、confidence根拠 |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 例外階層。上限違反は安定した`.code`を公開（ValueError互換の基底） |
| `Pixmap` | 不変のRGBA8画素: `samples` / `width` / `height` / `stride` / `n` / `tobytes()` / PNG専用`save(path)`。cp314tではread-only・zero-copyの`memoryview()`にも対応 |
| `PylopdfWarning` | 復旧可能な解釈警告（xref修復・フォント未解決・画像デコード失敗） |

`TypedDict`は静的型付けだけに作用し、値は従来どおり通常のpymupdf形式の辞書です。
`LinkInfo`では`kind`と`from`が必須で、遷移先ごとのキーは任意です。
`PageLabelSpec`では`startpage`が必須で、`style`、`prefix`、`firstpagenum`の
runtime既定値は変わりません。
