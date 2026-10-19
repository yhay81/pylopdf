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
password inputはUTF-8 127 byteが上限です。

| メンバ | 用途 |
|---|---|
| `doc[i]` / `load_page(pno)` / イテレーション | `Page` ビュー（負数可。構造変更後は取得し直す） |
| `page_count` / `len(doc)` | ページ数 |
| `limits` / `complexity` | 読み込み時の不変な資源ポリシー / streamを展開しない軽量な構造指標 |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | UTF-8 127 byte上限付きの暗号化状態と復号（pymupdf互換の意味論） |
| `is_repaired` | 読み込み時に破損したcross-reference dataを復旧したか（offsetの補正またはtableの再構築）。保存するとxref dataを正規化 |
| `metadata` / `set_metadata(dict)` | 標準Info 8項目（UTF-16BE対応）。aggregate text 1 MiB、書き込みは原子的 |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `get_text(pages=None)` | 全page（`None`）またはpage数と4,096の大きい方までの明示entryを1回のbatchで抽出するplain text。4,096 entryごとのchunkでinterpreter font cacheを共有 |
| `to_markdown(pages=None, table_strategy="lines", max_size=64 MiB)` | 上限付き線形entry builderによるpage単位2-pass Markdown変換。既定は全page、明示entryはpage数と4,096の大きい方まで、累積UTF-8出力上限付き（`None`で解除）、見出し・CJK・強調・list・column・縦書き順・table制御 |
| `render_page(..., max_size=64 MiB)` / `render_pages(..., workers=, max_size=512 MiB)` / `render_page_svg(..., max_size=64 MiB)` | 上限付きPNG、全pageまたはpage数と4,096の大きい方までの明示entryを累積encoded output上限付きで描画する順序保証並列PNG群、上限付きUTF-8 SVG（`None`で解除） |
| `compress_images(dpi=150, quality=75)` | 安全なDCT/Flate raster XObjectを配置DPIに応じて非可逆縮小・JPEG再圧縮し、型付きのbyte/count統計を返す |
| `set_fallback_font(font, kind=, index=, font_language="ja", max_font_size=64 MiB)` | Adobe Japan1／Korea1／GB1／CNS1を区別する非埋め込みCJKの上限付き代替font。信頼できるfont inputは`None`で解除 |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | page操作。select/deleteのentryはpage数と4,096の大きい方まで、`insert_pdf`は取り込み元の全範囲を受け付け、navigationのcleanupとlink取り込みはTOC・named destinationの上限を共有 |
| `get_toc()` / `set_toc(toc)` | 大規模manualに対応するcycle対応・上限付きのしおり（1始まり。65,536 entry/node、131,072 edge、深さ64、text 16 MiB。named destinationは`get_links()`と同じ） |
| `get_page_labels()` / `set_page_labels(labels)` | ページラベル。固定上限は4,096 entry/node、深さ32、label text 1 MiB |
| `get_form_fields()` / `set_form_field(name, value, font=, fontfile=, fontbuffer=, fontindex=, font_language=, max_font_size=64 MiB)` | field tree・caller名／値1 MiB・button state・font inputに上限を持つ、ネイティブ外観付きAcroFormの一覧と記入。`font`には読み込み済みの`Font`を渡せる |
| `embfile_add(..., max_size=64 MiB) / embfile_names / embfile_get(name, max_size=64 MiB) / embfile_del` | 入力と展開出力に対称な既定上限を持ち、caller text 1 MiB、追加metadata・inline FileSpec clone形状も制限する添付ファイル操作。`max_size=None`で明示的に解除 |
| `get_pdfa_claim(max_size=1 MiB)` | 上限付きXMP PDF/A宣言読み取り。`max_size=None`で明示的に上限解除。検証ではない |
| `save(...)` / `tobytes(..., max_size=512 MiB)` | 同一directoryへの完全なstream書き込み後の原子的file置換／上限付きPDF byte列。`garbage=` `deflate=` `object_streams=`、127 byte上限付き`user_pw=`／`owner_pw=`。`max_size=None`で解除 |
| `close()` | with 文でも |

`compress_images()`は全ページを解釈して、各間接raster objectが最も大きく配置される
寸法を求めてから、lopdfのcloneへ原子的に編集します。`dpi=None`では縮小せず、
quality再圧縮だけを行います。対象はmaskや独自decode arrayを持たない、直接かつ
単一filterの8-bit DeviceGray/DeviceRGB DCT/Flate streamに限定します。DCTの
decode parameterは対象外で、Flateはpredictorなし、または辞書と整合するPNG
predictorに対応します。解釈された非対応の間接画像と結果が小さくならないencodingは
skipし、inline画像は集計対象外です。1回の呼び出しで解釈する間接raster配置が
65,536件を超える場合は拒否します。同じ設定の再実行は冪等です。

## Page { #page }

| メンバ | 用途 |
|---|---|
| `number` / `parent` / `get_label()` | 素性と表示ラベル |
| `get_text(option)` / `search_for(needle, max_hits=4096)` | 抽出と上限付き検索（大文字小文字を区別しない）。検索語はUTF-8 4,096 byte、信頼できる結果集合は`None`で解除 |
| `get_text_ocr(dpi=, engine=, tile_size=, overlap=, min_confidence=, rotation=, clip=)` | 編集せずローカルPP-OCRv6で位置付き単語を認識。`rotation`は入力を時計回りに補正し、`clip`は表示座標 |
| `apply_ocr(..., rotation=, clip=, skip_existing=True)` | 向きを保持した不可視の検索可能層を挿入。選択領域の既存テキストは既定でスキップ |
| `find_tables(strategy="lines", clip=None)` | 完全なベクタ罫線、保守的に補完した疎な罫線、結合セル。`"text"`で罫線なし検出、`clip`で表示座標の領域を指定 |
| `to_markdown(table_strategy="lines", max_size=64 MiB)` | documentと同じtable・UTF-8出力制御を持つ1 page Markdown |
| `get_images()` | 描画された画像（`bbox`付き、JPEG passthrough / PNG）。4,096配置、累積64,000,000画素、payload 64 MiBを超える部分結果は拒否 |
| `get_drawings()` | ページで解釈されたベクターの fill/stroke パス。表示座標の line/cubic 形状と正規化された描画属性。8,192パス、131,072コマンド、またはページ集約131,072 dash値を超える場合は部分結果を返さず拒否 |
| `get_pixmap(scale=, dpi=, background=, clip=)` / `render(max_size=64 MiB)` / `render_svg(max_size=64 MiB)` | 上限付きPNG / UTF-8 SVG rendering。`clip`は表示座標 |
| `rotation` / `set_rotation(deg)` | 表示回転 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | ページボックス |
| `insert_image(rect, filename= / stream= / pixmap=, rotate=, keep_proportion=, overlay=, max_size=64 MiB, max_pixels=64,000,000)` | 上限付きJPEG/PNG、または既に上限管理されたRGBA `Pixmap`を挿入。信頼できるencoded input／PNG画素は`None`で解除。`rotate`は90度単位の時計回り回転 |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | PDFページをベクタのまま重ねる。`src`は同じ文書でもよい |
| `insert_text(point, text, fontsize=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | UTF-8と4,096行を上限として標準14またはshape済みsubset textを印字。`font`には読み込み済みの`Font`を渡せる。導入済みscript／locale providerを自動選択し、曖昧な漢字は`font_language`で指定。信頼できる各inputは`None`で解除 |
| `insert_textbox(rect, text, fontsize=, font=, fontname=, fontfile=, fontbuffer=, fontindex=, font_language=, color=, align=, expandtabs=, lineheight=, overlay=, max_font_size=64 MiB, max_text_size=1 MiB)` | textとtab展開を事前検査し、Core 14、OpenType、読み込み済みの`Font`、または導入済みproviderの実幅でUAX #14折り返し。物理行・折り返し後layoutは4,096行が上限で、収まらなければ描画しない |
| `insert_ocr_text_layer(words, rotation=)` | 向きを保持した不可視OCRテキスト層。1 callあたり4,096語・UTF-8 text 1 MiBが上限 |
| `replace_text(search, replacement, default_char=, max_size=64 MiB)` | 入出力上限とcopy-on-writeを備えた原子的な単純エンコーディング置換 |
| `annots()` / `get_links()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 上限付き注釈・link読み取りと作成。named destinationは65,536 entry/node、131,072 edge、深さ32、key 16 MiBまでのname treeで解決し、treeは一度だけindex化して次の編集まで後続の読み取りで再利用。描画時は、有効な`QuadPoints`を持つ上限内のRGB Highlight、Underline、StrikeOut、Squigglyについて、元PDFを変更せず不足appearanceを保守的に補完 |

`get_drawings()`は`type="f"` / `"s"` / `"fs"`、自己完結したline/cubicの
`items`、`rect`、RGB/opacity、fill rule、width、cap、join、dashesを持つ
`DrawingInfo`辞書を返します。pattern paintでは形状を保持し、色とopacityは`None`です。
clip path、clip適用後の可視判定、group/soft-mask構造、optional-content layer名、
text、image、annotationは返しませんが、optional-contentの表示状態は適用されます。
8,192 pathsまたは131,072 commandsを超える結果は切り詰めず拒否します。

埋め込みフォントを使う`insert_text`では、すべての字形を含む単一フォントが必要です。
`pylopdf[ar]`、`[he]`、`[hi]`、`[jp]`、`[ko]`、`[th]`、`[zh-cn]`、
`[zh-tw]`はNoto Sans／Serif生成fontを提供します。Kana、Hangul、Bopomofo、
Arabic、Hebrew、Devanagari、Thaiは導入済みproviderを自動選択します。漢字だけの
inputは互換性のためJP優先で、地域字形が重要なら`font_language="zh-CN"`、
`"zh-TW"`、`"ko"`、`"ja"`を指定します。これはrun全体で1 fontを選ぶ動作で、
glyphごとのfallbackではありません。各行の字形処理は行いますが、双方向paragraph
layoutと折り返しは行いません。RTLの字形処理結果は正しく描画されます。純RTL行と、
RTL textに両側を挟まれたLatinまたは数字tokenが1つだけある行は、論理Unicode順で
抽出されます。行端のtoken、複数token、一般的な混在方向paragraphはproducerの
視覚順を維持します。

複雑な字形処理では、論理textとglyphが1対1に対応しない箇所をPDFの
`/ActualText`で保持することがあります。生成PDFは外部viewer向けにこれを保持しますが、
現行pylopdf extractorはまだ`/ActualText`を読みません。そのため、特に長い
Devanagari runなど、並べ替えやcontext依存のclusterは再open後の抽出が近似になる
場合があります。

`insert_textbox`はリッチテキストエンジンではなく、明示改行、tab展開、CJKのUnicode
改行位置、長すぎる単語のgrapheme単位の緊急折り返しを扱います。整列には
`TEXT_ALIGN_LEFT`、`TEXT_ALIGN_CENTER`、`TEXT_ALIGN_RIGHT`、
`TEXT_ALIGN_JUSTIFY`を使います。戻り値が負なら高さ不足で、ページ内容やフォント
resourceは追加されません。

`Font`はOpenTypeの書体を一度だけ読み込み、`insert_text(font=)`・
`insert_textbox(font=)`・`set_form_field(font=)`で使います。計測は描画と同じshapingで行うので、
`font.text_length(text, fontsize)`はその行を描いたときの送り幅（カーニングと合字を含む）
と一致し、描く書体の幅で折り返し・揃え・頁割りを計算できます。`ascender`と
`descender`は`insert_textbox`が行を置くときのemに対する比率で、`has_glyph`は
文字マップを引きます。

`Font(fontfile=)`と`Font(fontbuffer=)`は渡した書体をそのまま読み込み、可変フォントは
既定のインスタンスで使います。既定のインスタンスがRegularとは限らず、可変フォントの
Noto Sans CJK JPはThinです。`weight=`を渡すと可変フォントの太さの軸から別のインスタンスを
選びます。`Font("NotoSansCJKjp-VF.otf", weight="bold")`はBoldのインスタンスの送り幅で測り、
`NotoSansCJKjp-Bold`という固有の名前で埋め込みます。`weight=650`も選べます。このため
こうした書体では、`weight="regular"`を明示した場合と省略した場合で結果が異なります。
軸の範囲外の太さは`ValueError`になり、静的な書体が持たない太さも同じです。
`insert_text`・`insert_textbox`・`set_form_field`に渡す`fontfile`と`fontbuffer`は、
従来どおり既定のインスタンスで描きます。選んだインスタンスで描くには、その`Font`を
`font=`に渡します。

`Font(language="ja")`は導入済みの書体extraの書体を選びます
（`serif=True`で明朝体）。extraが入れる書体はスタイルごとに1つだけなので、ほかの太さは
必要になったときに足します。`register_font(path)`は書体から書体名・太さ・斜体を読み、
以後`Font(family="Noto Sans JP", weight="bold")`でその書体を選べます。この書体は
`pylopdf[jp]`が入れる書体と同じファミリーなので、`Font(language="ja", weight="bold")`
でも選ばれます。可変フォントを登録すると軸上のすべての太さを選べるようになり、
求める太さで登録された書体があれば軸よりそちらを優先します。
一方`pylopdf[ar]`・`[he]`・`[hi]`・`[th]`は可変フォントを入れるため、
`Font(language="he", weight="bold")`は別のファイルなしに太さの軸からBoldのインスタンスを
選び、そのインスタンスの送り幅で測り、`NotoSansHebrew-Bold`という固有の名前で埋め込みます。
太さの範囲は100から900で、`pylopdf[ar]`のセリフ側の書体であるNoto Naskh Arabicは
400から700です。同じファミリーの書体が登録されていれば、軸よりそちらを優先します。
導入済みの可変フォントは、`insert_text`・`insert_textbox`・`set_form_field`が自動で
選ぶ場合も含めてRegularの太さで描きます。Noto Sans Hebrewの既定のインスタンスはThinだからです。
pylopdfはOSの書体フォルダを探しません。指定した書体がなければ
`ValueError`にし、輪郭線や重ね塗りで太さを真似ることはしません。
`allow_regular_fallback=True`を明示したときだけ、そのファミリーのRegularを使い
`PylopdfWarning`を出します。描画の呼び出しごとに、その呼び出しで使う字のサブセットを
埋め込む点は変わりません。

`set_form_field`はテキスト、コンボ／リスト選択、チェックボックス、ラジオボタンの
外観を生成します。WinAnsiはHelveticaで自動縮小され、UnicodeはOpenTypeの
`fontfile`または`fontbuffer`を指定するとサブセット埋め込みされます。可変フォントの
Boldのインスタンスなど、読み込み済みの`Font`を`font`に渡すこともできます。`Font`では
`text_length`が測る送り幅で縮小・整列し、comb欄の各位置の中央に置きます。`font`は
ほかのフォント指定と併用できません。導入済みscript／locale providerは
`insert_text`と同じ規則で選ばれ、曖昧な漢字は`font_language`で指定します。既存の
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
| `peek_metadata(filename=None, stream=None, password=None, *, max_file_size=None)` | 入力サイズを任意に制限しpasswordを127 byteに制限する高速メタデータ読み取り。`repaired`でcross-reference復旧を確認 |
| `Permissions` | 暗号化の許可フラグ（IntFlag） |
| `Rect` | 矩形の NamedTuple（`width` / `height` 付き） |
| `TextPage` / `TextBlock` / `TextLine` / `TextSpan` | `get_text("dict")` の TypedDict 階層 |
| `ImageInfo` / `ImageCompressionResult` / `AnnotationInfo` / `LinkInfo` / `FormFieldInfo` / `DrawingInfo` | page・document操作・form・vector drawing の辞書形式結果を表す TypedDict |
| `DrawingItem` | line/cubic 描画コマンドを表す型 alias |
| `PageLabelInfo` / `PageLabelSpec` | 正規化済みページラベル出力／setter入力の契約 |
| `DocumentMetadata` / `MetadataUpdate` / `MetadataProbe` | metadata出力／部分更新／高速probeの契約 |
| `DocumentLimits` / `DocumentComplexity` | PDF snapshot用`max_interpretation_size`と位置付きlayout用`max_text_glyphs`を含む、信頼できない入力の不変な上限／軽量な構造TypedDict |
| `OcrEngine` / `OcrWord` | 再利用可能な純Rust PP-OCRエンジン／位置付き結果の契約 |
| `Font` / `register_font(fontfile=None, *, fontbuffer=None, fontindex=0, max_font_size=64 MiB)` | `text_length`、`ascender`／`descender`、`units_per_em`、`name`、`family`、`weight`、`is_italic`、`has_glyph`を持つ再利用可能なOpenType書体（可変フォントは`weight=`で選んだインスタンス）／書体名と太さで引けるようにする登録（可変フォントは軸上のすべての太さ） |
| `OcrRotation` / `WordEntry` / `BlockEntry` / `FormFieldType` | runtimeでimportできるOCR回転・tuple・literal型alias |
| `TableFinder` / `Table` / `TableDiagnostics` | 所有権を持つ表の座標、セル文字列（結合継続位置は`None`）、strategy、confidence根拠。`Table.to_markdown(max_size=64 MiB)`はescape後のUTF-8出力を事前検査 |
| `PdfError` / `LimitError` / `PasswordError` / `OcrError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 例外階層。上限違反は安定した`.code`を公開（ValueError互換の基底） |
| `Pixmap` | 不変のRGBA8画素: `samples` / `width` / `height` / `stride` / `n` / `tobytes(max_size=64 MiB)` / streamingし、失敗時も既存fileを保持するPNG専用`save(path)`。cp314tではread-only・zero-copyの`memoryview()`にも対応 |
| `PylopdfWarning` | 復旧可能な解釈警告（xref修復・フォント未解決・画像デコード失敗） |

`TypedDict`は静的型付けだけに作用し、値は従来どおり通常のpymupdf形式の辞書です。
`LinkInfo`では`kind`と`from`が必須で、遷移先ごとのキーは任意です。
`PageLabelSpec`では`startpage`が必須で、`style`、`prefix`、`firstpagenum`の
runtime既定値は変わりません。
