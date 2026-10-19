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

page count／lookup、complexity、render snapshot、TOC／link、annotation、
Form import、page構造編集を含むすべての公開page indexingは、1つの反復的な
page-tree walkerを使います。Page／Pages objectの再利用とcycle、32参照を超える
間接`/Kids` chain、256階層を超える内部tree、間接object数を超えるedgeを拒否します。
`max_pages`は上限を超えた最初のpageで停止します。pageポリシーなしのopenは互換性を
維持し、これらのpage-tree検査を最初のpage-indexing操作まで遅延します。
page count／lookupは1回のwalkで作ったpage indexを再利用するため、全pageへの
accessも線形です。回転、page box、描画、annotationの編集はpage treeを変えないため
indexを保持します。各編集は未使用のobject番号にだけobjectを追加し、walkが読んだ
entryを変えないことを確かめ、確かめられない場合はindexを破棄します。それ以外の編集は
indexを破棄して次のaccessで作り直します。失敗したwalkはcacheしないため、不正な
treeはaccessのたびに例外を送出します。

page batchは1回取得したpage数でentryを検証します。`pages=None`と`insert_pdf()`の
全範囲指定は、page数にかかわらずすべてのpageを対象にします。明示sequenceは同じpageを
繰り返せますが、page数と4,096の大きい方を超えた最初のentryでmaterializationを止め、
`page_entry_count`を返すため、反復しても文書全体を1回処理する以上のcostになりません。
text抽出とrenderingはcall単位のhayro cacheを4,096 entryごとに作り直し、`max_size`、
`max_text_size`などの出力予算はcall全体に適用されます。core APIを直接呼んでもentry
検査は繰り返され、core page importは変更前に重複した取り込み元pageを拒否します。

`LimitError`は`PdfError`のsubclassです。安定した`code`は`file_size`、
`page_count`、`object_count`、`object_depth`、`decompressed_size`、
`page_content_size`、`total_decompressed_size`、`text_size`、
`text_glyph_count`、`interpretation_size`、`embedded_file_size`、
`embedded_file_input_size`、`form_field_input_size`、`form_content_size`、
`annotation_input_size`、
`metadata_input_size`、`toc_input_size`、`toc_entry_count`、`toc_text_size`、
`named_destination_count`、`named_destination_text_size`、
`page_entry_count`、`page_label_input_size`、
`xmp_metadata_size`、`render_output_size`、
`markdown_output_size`、`svg_output_size`、`replacement_input_size`、
`replacement_output_size`、`pdf_output_size`、`image_input_size`、
`image_pixel_count`、`font_input_size`、`text_input_size`、`text_line_count`、
`path_point_count`、`search_input_size`、`search_hit_count`、`password_input_size`、`pixmap_output_size`、
`ocr_model_size`、`ocr_dictionary_entries`、`stream_filter_count`、
`decompression_unverifiable`、`recovery_scan_size`、`content_nesting_depth`、
`resource_nesting_depth`のいずれかです。同じ値を`error.args[0]`でも取得できます。
安全に上限計算できないfilter chainは、楽観的に展開せず拒否します。
pylopdfが所有する上限付きlopdf decode経路では、filter listをmaterializeする
前に16層を超える`/Filter` chainも拒否します。

`doc.complexity`はstreamを展開せずrendererも呼ばずに、ページ数、object数、
stream数、圧縮状態のstream byte数、直接objectの最大深度を返します。重い抽出へ
進む前のroutingに利用できます。構造・展開上限は開いたsourceを検査するため、
生成物が別のtrust boundaryを越えるときは同じポリシーで開き直してください。
直接深度検証とcomplexity検査の反復走査stackはfallibleに拡張されるため、
allocation refusalは部分的なfactsではなく`PdfError`になります。

`max_interpretation_size`は、hayroが保持済みinputを最初に読むときと、編集や復号の後、
またはtext markup appearanceの合成などrendering用copyの準備のためにpylopdfが現在状態を
serializeするときに適用されます。上限付き
writerは境界を越えるwriteを拒否し、不完全なrenderer／extractor cacheを登録しません。
互換性のため既定値は`None`で、`DocumentLimits.web()`は64 MiBです。

`max_text_size`を設定すると、plain-text抽出は組立後の正確なUTF-8 sizeを事前検査し、
1回の抽出callをglyph payload budgetの2倍に制限します。空でないglyphはpayloadを
最低1 byte持ち、推定gapと改行の合計はglyph数を超えません。この出力上限は内部の
4,096 entry chunk単位ではなくcall全体に適用され、明示sequenceはpage数と4,096の
大きい方までのentryに制限されるため、同じpageの反復でもポリシーを回避できません。
拒否codeは`text_size`のままで、互換性のため既定値は`None`です。

`max_text_glyphs`はline組立前に保持するrecord数を制限するため、構造化textが
materializeできるblock、line、span、wordの数も制限します。同じpageのtext解釈と
table解釈は1回分の累積admissionを共有し、拒否されたpageはbudgetを消費しません。
互換性のため既定値は`None`で、`DocumentLimits.web()`は65,536です。

寛容な読み込みはlopdfのcross-reference復旧を使います。`startxref`、`/Prev`、
`/XRefStm`のoffsetが64 byte以内でずれている場合は、近くの`xref` keywordへ移動
します。通常の解決にそれでも失敗すると、lopdfは行頭のobject headerと、`/Root`が
存在する最新のclassic `trailer`からtableを再構築します。後のrevisionのobjectが
優先されますが、破損範囲のobjectは失われることがあります。classic trailerのない
破損したxref streamファイルは拒否されます。いずれの復旧でも`PylopdfWarning`が
発生し、`doc.is_repaired`（metadata probeでは`repaired`）が`True`になります。
描画と抽出は保存時に書き出す復旧後のobject graphを使い、保存するとxref dataを
正規化します。復旧した入力を受け付けられない呼び出し側は、`is_repaired`の文書を
拒否できます。19 byteのxref entryや、曖昧さのないobject境界で補正したstream
`/Length`など、宣言された構造を保つ寛容な処理は報告しません。復旧した文書にも
`DocumentLimits`はそのまま適用されます。

lopdfの再構築は、後ろに`endstream`のない`stream` keywordごとに入力の残りを
再走査します。pylopdfは寛容な読み込みとmetadata probeの前にその作業量を線形時間で
見積もり、入力1 byteあたり64 byteに64 MiBを加えた量を超える入力を
`recovery_scan_size`で拒否します。これにより、敵対的な入力で復旧が二乗時間に
なることを防ぎます。実際の破損ファイルでは、その位置にある途切れたstreamは
多くても1つです。

hayro 0.7はcontent stream内のarrayとdictionaryを深さ無制限の再帰で読んでいたため、
数十万個の`[`を開くstreamが1つあるだけで、例外にならずstackを溢れさせてPython
プロセスを終了させていました。hayro 0.8は64階層で読むのをやめ、それより深いoperandを
黙って省きます。pylopdfは、text抽出、layout、table、検索、drawing、image取得、
rendering（PNG、pixmap、SVG）、Markdown変換、image圧縮がpageを解釈する前に、その
pageから到達できるcontent stream（`/Contents`、resourceにあるForm XObject、
annotationとwidgetのappearance（check boxとradio buttonはすべての状態。hayroが
描くのは`/AS`が選ぶ1つです）、soft-mask group、tiling pattern、Type 3 glyph
procedure）をデコードします。arrayまたはdictionaryが64階層を超えて
入れ子になる場合、stream objectが埋め込まれている場合、検査できないinline image
境界がある場合は、そのpageを`content_nesting_depth`で拒否します。この上限は固定で、
`DocumentLimits`の有無にかかわらず適用され、ほかのpageは引き続き利用できます。
各pageはdocument snapshotごとに1回だけ検査され、編集すると結果は破棄されます。
検査はcontentがどの名前を使うかを調べないため、operatorが描画しないresource
dictionaryのentryも検査対象です。

hayroはcolour space、shading、functionも、それらが名前で参照するobjectをたどる深さ
無制限の再帰で構築します（hayro 0.7と0.8の両方）。IndexedまたはPattern colour space
はbase、SeparationまたはDeviceN colour spaceはalternate spaceとtint transform、
ICCBased colour spaceは解析できないprofileの`/Alternate`、stitching function
（type 3）は`/Functions`のすべてのentryから構築され、Type 4 functionは`{ }`
procedureを再帰でparseします。互いを参照する約2万個のindirect object、または30万
個の入れ子の波括弧で、stackが溢れてプロセスが終了していました。pylopdfはpageを解釈
する前に、そのpageのresource dictionaryの`/ColorSpace`と`/Shading`のentry、shading
pattern、image XObject、soft-mask group、graphics stateとsoft maskの転送関数
（`/TR`、`/TR2`）から到達できるものを測り、colour spaceとfunctionの連鎖が16階層を
超える場合、またはType 4 functionのprocedureが64階層を超えて入れ子になる場合は、
そのpageを`resource_nesting_depth`で拒否します。連鎖にはcolour spaceのarray、
function、profile streamが数えられ、hayro自身が循環を断つため自己参照や短い輪は
そのまま描画されますが、互いに到達し合うobjectが16個を超える循環は拒否され、共有された
sub-graphは1回だけ測られ、objectは参照する項目の数にかかわらず1回しか読まれません。これらの上限は固定で、`DocumentLimits`の有無にかかわらず
適用され、ほかのpageは引き続き利用できます。制限するのはstackの使用量であり時間では
ありません。各階層で同じfunctionを2回参照する、Separationを入れ子にするなど、上限
以内でも細工されたgraphではhayroが指数時間のCPUを要するため、ホスト側でCPU
deadlineを強制してください。

hayro 0.8は、それを保持するobjectを含めて64階層を超えて入れ子になったarrayや
dictionaryを読まず、そのobject全体をないものとして扱います。hayro 0.7はこうした
objectを再帰で読んでおり、lopdfは100階層まで読みます。page treeから継承する属性を
含めたpageのdictionaryがこれより深いpageは、操作が解釈するたびに`object_depth`で
拒否され、ほかのpageは引き続き利用できます。trailer、catalog、page treeのroot node
がこの深さで入れ子になっていても、文書の描画は妨げられません。renderingとextraction
は深すぎるentryを除いたcopyを読み、編集・保存する文書にはそのentryが残ります。それ
以外のobjectがこの深さで入れ子になっている場合は、hayroと同じくないものとして読ま
れます。`DocumentLimits.web()`は、このような文書をopen時に`object_depth`で拒否します。

hayro 0.8は、成分数の合わない値を返すSeparationやDeviceNのtint transform、Pattern
colour spaceのshadingなど、一部の不正な色データでpanicします
（[LaurenzV/hayro#1546](https://github.com/LaurenzV/hayro/issues/1546)）。hayro 0.7
は同じpageを描画できていました。hayroにpageを解釈させるすべての操作はこのpanicを捕捉
し、PyO3の`PanicException`（`BaseException`の派生で、`except Exception`をすり抜け
ます）の代わりに`PdfError`を送出します。renderer stateは編集時と同じく破棄されるため、
以降の呼び出しは最初からやり直され、文書は引き続き利用できます。panicのメッセージは
Rustが標準エラー出力に書き出します。

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
- `insert_text()`と`insert_textbox()`の生成text inputは既定でUTF-8 1 MiB、
  物理行・折り返し後layoutは4,096行が上限です。PythonはPyO3 copy前に物理行を
  検査し、Rust境界も再検査してmutation前に折り返し行を止めます。textboxのtab
  展開量は展開済みstringを確保する前に計算します。信頼できる挿入inputは
  `max_text_size=None`で明示解除でき、拒否codeは`text_input_size`または
  `text_line_count`です。AcroFormのtext／choice外観は固定4,096行上限を維持します。
  UAX #14／grapheme index、行collection、保持する行textはfallibleに拡張され、
  allocation refusalで部分layoutやdocument編集を公開しません。pylopdfが所有する
  正規化、grapheme参照、shaped line／glyph collection、WinAnsi出力、入力由来の
  text operator拡張もfallibleです。Standard 14の幅検査はencoded text copyを
  確保しません。allocation refusalは`PdfError`を返し、挿入は非mutationのまま、
  form fillは原子的にrollbackします。
- `Shape.insert_text()`と`Shape.insert_textbox()`は、呼び出しごとに同じtextと
  書体の上限を適用します。1つの図形が確定までに持てる経路の点は1,048,576個までで、
  点を足すときとRust境界の両方で検査し、拒否codeは`path_point_count`です。確定は
  描画の生成、取り込みの計画、頁の描画先の準備を頁を変える前に終えるので、拒否
  されたときdocumentは変わりません。
- `search_for()`の検索語はUTF-8 4,096 byte、返却geometryは既定4,096件が上限です。
  PythonはPyO3 copy前に検索語を拒否し、Rust境界も両方の上限を再検査します。
  信頼できる結果集合は`max_hits=None`で明示解除でき、拒否codeは
  `search_input_size`／`search_hit_count`です。partial listは返しません。
- open、authenticate、高速metadata probe、AES-256出力のpasswordはUTF-8 127 byteで
  PyO3 copy／password KDF前に拒否します。Rust直呼びも再検査し、codeは
  `password_input_size`です。保存拒否はdocument mutation／output作成より前です。
- `render_page_svg()`と`Page.render_svg()`のUTF-8出力上限は既定64 MiBで、
  PyO3がPython stringを作る前に超過を拒否します。`max_size=None`で明示的に
  解除できます。hayro-svg 0.8が完成した`String`だけを返すため、pylopdfが
  制限を適用する前の内部Rust string 1つはこの境界の対象外です。
- 描画挿入はcache無効化・入力decode・dependent object作成の前に、page
  `/Contents`のraw arrayと参照chainを検査します。raw arrayは4,096 entry、
  chainは深さ32、最終arrayは一度だけ追加する`q`/`Q` isolation pair込みで
  4,096 stream参照が上限です。失敗時はdocumentを変更しません。
- `Page.replace_text()`はsearch・replacement・fallbackの合計を4,096 UTF-8
  byteに制限し、展開page content、font encoding data、置換増幅、最終streamの
  既定上限を64 MiBにします。page専用streamをcommit前に準備するため、複製pageの
  共有contentを変更せず、no-match/errorではdocumentとcacheを保持します。
  caller textはPyO3 copy前に完全なencoded copyを作らず逐次計数します。
  信頼できる入力では`max_size=None`で明示的に解除できます。
- `delete_pages()`と`select()`はPythonとRustの双方で、1 callあたりpage数と
  4,096の大きい方までのentryを受け付けます。そのため同じpageを繰り返しても選択結果は
  文書より大きくなりません。iterableは上限を超えた最初のentryでgraph変更前に停止し、
  `page_entry_count`を返します。`insert_pdf()`は取り込み元の全範囲を受け付けます。
  空deleteはcache・generation・既存`Page` viewを保持します。
- `Page.get_images()`は1ページで4,096配置、累積64,000,000 source画素、返却payload
  64 MiBを超える部分結果を拒否します。Flate-wrapped JPEG passthroughも残りbyte
  上限までしか展開しません。
- `Document.embfile_add()`はPyO3 copy前に64 MiB超の入力を拒否し、
  `embfile_get()`は各filter層の展開結果に同じ既定上限を適用します。既知の大容量
  添付では`max_size=`を増やせます。`max_size=None`は無制限の入力または
  materializationを明示的に許可します。添付名treeも4,096 entry/node、深さ32、
  encoded/decoded name合計1 MiBを超えると拒否します。callerのlookup／削除名と
  追加時のkey/filename/description入力は、tree走査やdata copy前に合計1 MiBで
  停止し、`embedded_file_input_size`を使います。編集時はinline FileSpecのclone前に、直接
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
  訪問し、書き込みはPyO3 copy前に`page_label_input_size`で同じentry/text上限を
  適用します。
- AcroForm field treeは4,096 entry/node、8,192 edge、深さ64、encoded/
  decoded/returned name・value 1 MiB、choice value 4,096 itemを超える部分結果を
  拒否します。参照cycleは一度だけ訪問し、継承値は返却leafごとに課金します。
  fillにも同じtree上限とcaller名／値1 MiB上限を原子的に適用し、font探索・button
  lookup・file読込前の拒否には`form_field_input_size`を使います。
- `insert_pdf()`は取り込み先を変更する前に、最上位のfield名を1 MiBのname予算内で
  比較し、ページ範囲の指定時は同じnode・edge・widget・深さの上限で取り込み元の
  field treeを走査します。全ページの取り込みはfield treeを走査せずにコピーします。
  共有のための既定リソース2件の比較は4,096 object・1 MiBまでで、それを超える組は
  改名します。
- AcroForm button fieldは4,096 widget、8,192 normal appearance state entry、
  4,096 unique returned state name、encoded/returned state-name text 1 MiBを
  超えると拒否します。fillは不足する`Off`/on state keyを変更前に課金します。
- 注釈・link読み取りは4,096 `/Annots` entryまたは1 call当たりaggregate
  encoded/returned metadata text 1 MiBを超える部分結果を拒否します。追加は同じ
  page件数、生成subtypeとContents/URI入力の合計1 MiB、highlight 4,096矩形を
  dependent object作成とcache無効化の前に検査します。caller textはPyO3 copyや
  rectangle iteration前に`annotation_input_size`で拒否し、highlight iterationは
  4,097 item目で停止します。
- named destination lookupは参照cycleを一度だけ訪問し、65,536 entry/nodeまたは
  131,072 edgeを超えるtreeを`named_destination_count`、key byte 16 MiBを超える
  treeを`named_destination_text_size`で拒否し、深さ32も超えさせません。途中で
  打ち切ったtreeを通常の未解決として黙って扱うことはありません。`get_toc()`、
  `Page.get_links()`、`insert_pdf()`のlink取り込み、page削除・選択時のname tree
  と旧形式`/Dests`の書き換えはこの上限を共有するため、解決できる正しい形式の
  treeはcleanupと取り込みもできます。書き換えでは、保持するdestination値を
  direct object 1,048,576個（entryあたり16個）とtext 16 MiBの範囲でcopyします。
  参照したdestination名は走査中の構造の予算に計上され、outlineはTOC text、
  name treeはkey text、linkはpageごとの1 MiB link metadataを使います。
  `Page.get_links()`は最初のnamed lookupでtreeを一度だけindex化し、次の編集
  まではそのindexとpage indexを再利用するため、全pageのlinkを読んでも処理時間
  は線形に収まります。保持するindexは同じentry・key text上限の範囲で重複しない
  keyを一度ずつ持ち、拒否したtreeはcacheしません。
- TOC読み取りは反復outline walkで参照cycleを一度だけ訪問し、GILを解放します。
  65,536 node/entryまたは131,072 edgeを超える部分結果は`toc_entry_count`、
  source/returned text 16 MiBを超えるものは`toc_text_size`で拒否し、深さ64と
  destination間接参照32段も超えさせません。書き込みも同じentry・深さ・title text
  上限をPyO3 copy・変更前に`toc_entry_count`と`toc_input_size`で検査します。page
  削除・選択はoutline上限を共有するため、書き込んだoutlineは編集可能なままです。
- `Document.metadata`は標準Info 8項目だけをdecodeし、aggregate source/returned
  text 1 MiBを超えると拒否します。custom entryはPython出力にmaterializeしません。
  `peek_metadata(max_file_size=)`はpathまたはbyte inputをparse前に拒否でき、
  returned standard textも制限します。入力の既定値は無制限です。書き込みは
  source/encoded text 1 MiBをPyO3 copy前に`metadata_input_size`で検査して
  原子的に適用します。
- 埋め込みJavaScriptは設計上非対応で、実行されません。
- `render_pages()`は既定で全pageを描画し、明示entryはpage数と4,096の大きい方
  までです。累積encoded PNG上限は既定512 MiBです。並列結果は1つのatomic budgetを
  共有し、失敗時に部分listを返しません。`max_size=None`で明示的に解除できます。
  worker admissionはlive raster/conversion bufferを別に制御し、各workerは4,096
  pageごとにhayro cacheを作り直すため、application側で無制限の並列呼び出しを
  重ねないでください。
- `Document.to_markdown()`は既定で全pageを変換し、明示entryはpage数と4,096の
  大きい方までです。累積UTF-8出力上限は既定64 MiBです。heading size集計passとrender passの双方で、同時に保持する
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
