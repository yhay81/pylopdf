---
title: セキュリティ
description: サポート対象、脆弱性の非公開報告、信頼できないPDFをpylopdfで扱うときの指針。
---

# セキュリティ

セキュリティ修正の対象は、PyPIで公開されている最新リリースだけです。

## 脆弱性を報告する { #report-a-vulnerability }

[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)
から非公開で報告してください。公開Issueは作成しないでください。最初の応答は
1週間以内を目安にしています。

## 信頼できないPDFを扱う { #untrusted-pdfs }

pylopdfはRust製で必須Python依存もありませんが、悪意あるPDF入力の解析には
本質的なリスクがあります。

!!! warning "資源ポリシーを一式指定する"
    `pylopdf.open()`へ`limits=pylopdf.DocumentLimits.web()`を渡してください。
    メモリに制約のあるWeb workerやqueue workerでユーザー入力を扱うための、
    保守的な初期値です。

```python
import pylopdf

try:
    with pylopdf.open(
        "upload.pdf",
        limits=pylopdf.DocumentLimits.web(),
    ) as doc:
        facts = doc.complexity
        preview = doc[0].get_pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

Webプロファイルは現在、次の上限を独立に適用します。

| 資源 | 上限 |
|---|---:|
| 入力ファイル | 10 MiB |
| ページ数 | 200 |
| 間接オブジェクト数 | 100,000 |
| 画像のRGBA見積りを含む個々の展開ストリーム | 64 MiB |
| 個々のページ内容ストリーム | 10 MiB |
| ストリームの累積展開／見積りbyte | 128 MiB |
| 直接array／dictionaryの入れ子 | 64 |
| 解釈済みページ全体のUTF-8 glyph payload | 1 MiB |
| rendering／extractionへ渡す完全なPDF snapshot | 64 MiB |
| 解釈済みページ全体の位置付きglyph record | 65,536 |

ワークロードに合わせる場合は`DocumentLimits(...)`を直接作成します。`None`以外は
正の整数でなければなりません。従来の`max_decompressed_size=`はストリーム単位の
短縮指定として残りますが、`limits=`とは同時指定できません。

`LimitError`は`PdfError`のsubclassです。安定した`code`は`file_size`、
`page_count`、`object_count`、`object_depth`、`decompressed_size`、
`page_content_size`、`total_decompressed_size`、`text_size`、
`text_glyph_count`、`interpretation_size`、`embedded_file_size`、`xmp_metadata_size`、`render_output_size`、
`markdown_output_size`、`svg_output_size`、`replacement_input_size`、
`replacement_output_size`、`pdf_output_size`、`image_input_size`、
`image_pixel_count`、`font_input_size`、`text_input_size`、
`search_input_size`、`search_hit_count`、`password_input_size`、`pixmap_output_size`、
`ocr_model_size`、`ocr_dictionary_entries`、`decompression_unverifiable`の
いずれかです。同じ値を`error.args[0]`でも取得できます。
安全に上限計算できないfilter chainは、楽観的に展開せず拒否します。

`doc.complexity`はstreamを展開せずrendererも呼ばずに、ページ数、object数、
stream数、圧縮状態のstream byte数、直接objectの最大深度を返します。重い抽出へ
進む前のroutingに利用できます。構造・展開上限は開いたsourceを検査するため、
生成物が別のtrust boundaryを越えるときは同じポリシーで開き直してください。

`max_interpretation_size`は、hayroが保持済みinputを最初に読むときと、編集、復号、
AcroForm state選択後の現在状態をpylopdfがserializeするときに適用されます。上限付き
writerは境界を越えるwriteを拒否し、不完全なrenderer／extractor cacheを登録しません。
互換性のため既定値は`None`で、`DocumentLimits.web()`は64 MiBです。

`max_text_glyphs`はline組立前に保持するrecord数を制限するため、構造化textが
materializeできるblock、line、span、wordの数も制限します。同じpageのtext解釈と
table解釈は1回分の累積admissionを共有し、拒否されたpageはbudgetを消費しません。
互換性のため既定値は`None`で、`DocumentLimits.web()`は65,536です。

寛容な読み込みが修復するのは1つの限定ケースだけです。同じ最終revisionに完全な
classic xref tableがあり、元の上限で全体parseが成功した場合に限り、誤った最終
`startxref`を置き換えます。object headerの走査、xref streamの修復、以前のrevision
への巻き戻しは行いません。修復時は`PylopdfWarning`が発生し、
`doc.is_repaired`（metadata probeでは`repaired`）が`True`になります。保存すると
xref dataを正規化します。

- レンダリングは1ページ64メガピクセルまでです。
- `Document.render_page()`、`Page.render()`、`Pixmap.tobytes()`のencoded
  PNG出力上限は既定64 MiBです。writerがPython `bytes`を返す前に境界を越える
  writeを拒否し、`max_size=None`で明示解除できます。renderingは
  `render_output_size`、Pixmap直接encodeは`pixmap_output_size`を返します。
- `Document.tobytes()`は通常・object/xref stream・暗号化出力すべてに512 MiBの
  既定serialization上限を適用します。Rust writerがPython `bytes`変換前に境界を
  越えるwriteを拒否し、`max_size=None`で明示的に解除できます。`save()`はtargetと
  同じdirectoryに安全に作成したsiblingへstreamし、完全なwrite後だけ要求pathを
  原子的に置換するため、serialization/置換失敗時も既存fileを保持します。この
  in-memory上限の対象外です。`garbage`、`deflate`、`object_streams`などのsave
  optionは、その後I/Oが失敗しても文書化済みのmutation semanticsを維持します。
- `Pixmap.save()`はtarget directory内に予測不能かつ排他的に作成したsiblingへ
  PNG encodeを直接streamし、完全なwriteが成功した後だけ要求pathを原子的に
  置換します。完成PNGをメモリにもう1つ保持しません。置換失敗時は既存outputを
  保持し、一時fileを削除します。
- `Page.insert_image()`はencoded JPEG/PNG inputを既定64 MiB、decoded PNG inputを
  既定64,000,000画素に制限します。filenameはGILを解放したRust境界で上限付きで
  読み、PNG dimensionはdecoded storage確保前に検査します。信頼できるworkloadは
  `max_size=None`／`max_pixels=None`で明示解除できます。
- `insert_text`、`insert_textbox`、`set_form_field`、`set_fallback_font`の
  明示／自動OpenType inputは既定64 MiBです。bufferはPyO3 copy前に拒否し、
  filenameはGILを解放した上限付きRust pathで読みます。信頼できるworkloadは
  `max_font_size=None`で明示解除できます。
- `insert_text()`と`insert_textbox()`の生成text inputは既定でUTF-8 1 MiBです。
  PythonはPyO3 copy前に検査し、Rust境界も再検査します。textboxのtab展開量は
  展開済みstringを確保する前に計算します。信頼できるinputは
  `max_text_size=None`で明示解除でき、拒否codeは`text_input_size`です。
- `search_for()`の検索語はUTF-8 4,096 byte、返却geometryは既定4,096件が上限です。
  PythonはPyO3 copy前に検索語を拒否し、Rust境界も両方の上限を再検査します。
  信頼できる結果集合は`max_hits=None`で明示解除でき、拒否codeは
  `search_input_size`／`search_hit_count`です。partial listは返しません。
- open、authenticate、高速metadata probe、AES-256出力のpasswordはUTF-8 127 byteで
  PyO3 copy／password KDF前に拒否します。Rust直呼びも再検査し、codeは
  `password_input_size`です。保存拒否はdocument mutation／output作成より前です。
- `render_page_svg()`と`Page.render_svg()`のUTF-8出力上限は既定64 MiBで、
  PyO3がPython stringを作る前に超過を拒否します。`max_size=None`で明示的に
  解除できます。hayro-svg 0.7が完成した`String`だけを返すため、pylopdfが
  制限を適用する前の内部Rust string 1つはこの境界の対象外です。
- 描画挿入はcache無効化・入力decode・dependent object作成の前に、page
  `/Contents`のraw arrayと参照chainを検査します。raw arrayは4,096 entry、
  chainは深さ32、最終arrayは一度だけ追加する`q`/`Q` isolation pair込みで
  4,096 stream参照が上限です。失敗時はdocumentを変更しません。
- `Page.replace_text()`はsearch・replacement・fallbackの合計を4,096 UTF-8
  byteに制限し、展開page content、font encoding data、置換増幅、最終streamの
  既定上限を64 MiBにします。page専用streamをcommit前に準備するため、複製pageの
  共有contentを変更せず、no-match/errorではdocumentとcacheを保持します。
  信頼できる入力では`max_size=None`で明示的に解除できます。
- `delete_pages()`・`select()`・`insert_pdf()`はPythonとRustの双方で1 call
  4,096 page entryが上限です。iterableは4,097 item目でgraph変更前に停止します。
  空deleteはcache・generation・既存`Page` viewを保持します。
- `Page.get_images()`は1ページで4,096配置、累積64,000,000 source画素、返却payload
  64 MiBを超える部分結果を拒否します。Flate-wrapped JPEG passthroughも残りbyte
  上限までしか展開しません。
- `Document.embfile_add()`はPyO3 copy前に64 MiB超の入力を拒否し、
  `embfile_get()`は各filter層の展開結果に同じ既定上限を適用します。既知の大容量
  添付では`max_size=`を増やせます。`max_size=None`は無制限の入力または
  materializationを明示的に許可します。添付名treeも4,096 entry/node、深さ32、
  encoded/decoded name合計1 MiBを超えると拒否します。追加時のkey/filename/
  description入力は合計1 MiBまでです。編集時はinline FileSpecのclone前に、直接
  object 4,096個、深さ32、直接string/name/stream data 1 MiBの上限とCatalogの
  書込先を検証し、rollbackのために文書全体をcloneしません。
- `Document.get_pdfa_claim()`は各filter層のXMP展開結果を既定で1 MiBに制限します。
  既知の大容量packetでは`max_size=`を増やせます。`max_size=None`は無制限の
  materializationを明示的に許可します。
- `Page.insert_ocr_text_layer()`は4,096個を超える非空wordまたはUTF-8 text合計
  1 MiBを超える時点でiterableのmaterializationを停止します。core直接呼出しも
  同じ上限を適用し、65,535種類目のCID割当前に停止し、入力由来bufferをPDF変異前に
  準備します。
- ページラベル番号treeは4,096 entry/node、深さ32、encoded/decoded
  style・prefix text合計1 MiBを超える部分結果を拒否します。参照cycleは一度だけ
  訪問し、書き込みにも同じentry/text上限を適用します。
- AcroForm field treeは4,096 entry/node、8,192 edge、深さ64、encoded/
  decoded/returned name・value 1 MiB、choice value 4,096 itemを超える部分結果を
  拒否します。参照cycleは一度だけ訪問し、継承値は返却leafごとに課金します。
  fillにも同じtree上限と入力値1 MiB上限を原子的に適用します。
- AcroForm button fieldは4,096 widget、8,192 normal appearance state entry、
  4,096 unique returned state name、encoded/returned state-name text 1 MiBを
  超えると拒否します。fillは不足する`Off`/on state keyを変更前に課金します。
- 注釈・link読み取りは4,096 `/Annots` entryまたは1 call当たりaggregate
  encoded/returned metadata text 1 MiBを超える部分結果を拒否します。追加は同じ
  page件数、生成subtypeとContents/URI入力の合計1 MiB、highlight 4,096矩形を
  dependent object作成とcache無効化の前に検査します。
- named destination lookupは参照cycleを一度だけ訪問し、4,096 entry/node、
  8,192 edge、深さ32、key byte 1 MiBを超えるtreeを、通常の未解決として黙って
  扱わず拒否します。`Page.get_links()`はnamed linkごとにtreeを再走査せず、
  1 callにつき1つのborrowed indexを構築します。
- TOC読み取りは反復outline walkで参照cycleを一度だけ訪問し、GILを解放します。
  4,096 node/entry、8,192 edge、深さ64、destination間接参照32段、source/returned
  text 1 MiBを超える部分結果を拒否します。書き込みもentry・深さ・title textを
  変更前に検査します。
- `Document.metadata`は標準Info 8項目だけをdecodeし、aggregate source/returned
  text 1 MiBを超えると拒否します。custom entryはPython出力にmaterializeしません。
  `peek_metadata(max_file_size=)`はpathまたはbyte inputをparse前に拒否でき、
  returned standard textも制限します。入力の既定値は無制限です。書き込みは
  source/encoded text 1 MiBを変更前に検査して原子的に適用します。
- 埋め込みJavaScriptは設計上非対応で、実行されません。
- `render_pages()`は最大4,096 page entryで、累積encoded PNG上限は既定512 MiB
  です。並列結果は1つのatomic budgetを共有し、失敗時に部分listを返しません。
  `max_size=None`で明示的に解除できます。worker admissionはlive raster/
  conversion bufferを別に制御するため、application側で無制限の並列呼び出しを
  重ねないでください。
- `Document.to_markdown()`は最大4,096 page entryで、累積UTF-8出力上限は既定
  64 MiBです。heading size集計passとrender passの双方で、同時に保持する
  interpreted layout・table・wordは1 page分だけです。page出力を組み立てる前に
  各tableへ累積残予算を渡します。`Table.to_markdown()`も既定で同じ上限を持ち、
  merged-cell展開を含むescape後の正確なUTF-8 sizeを事前検査します。見出し、
  paragraph、list、tableはentryを保持する時点で課金され、page全体はsize確定後に
  線形結合します。上限超過時は部分stringを返しません。`max_size=None`で明示的に
  解除できます。
- CPU deadlineはWorker、process、container側で設定してください。資源上限は
  文書化したallocationと出力量を抑えますが、実行中のparserやinterpreterを
  wall-clock時間で中断する機能ではありません。
- 可能なら、信頼できないファイルの一括処理はsandboxやcontainer内で行ってください。
  nativeとPyodideのCIは同じhostile-input回帰契約を共有し、定期Atheris fuzzingは
  壊れたxref、cycle、深いobject、broken stream、圧縮bombをseedにします。

## 依存関係の監査 { #dependency-auditing }

CIはpushごとに`cargo audit`を実行し、Rust依存ツリーをRustSecの脆弱性データベースと
照合します。

リポジトリ上の正本は
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)です。
