---
title: セキュリティ
description: サポート対象のバージョン、脆弱性の非公開報告、信頼できないPDFをpylopdfで安全に処理する方法。
---

# セキュリティ

## サポート対象のバージョン { #supported-versions }

セキュリティ修正の対象は、PyPIで公開されている最新リリースだけです。

## 脆弱性を報告する { #report-a-vulnerability }

[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)から非公開で報告してください。公開のIssueは作成しないでください。最初の応答は1週間以内を目安にしています。

## 信頼できないPDFを扱う { #untrusted-pdfs }

pylopdfはRustで書かれ、必須のPython依存もありませんが、入手元のわからないPDFは過剰なメモリや時間を使うように作られていることがあります。ユーザーのアップロードなど、自分で管理していないファイルを処理するときは、資源ポリシーを指定して開いてください。

```python
import pylopdf

try:
    with pylopdf.open("upload.pdf", limits=pylopdf.DocumentLimits.web()) as doc:
        if doc.complexity["page_count"] > 50:
            reject_upload("too many pages")
        preview = doc.pages[0].get_pixmap(dpi=144)
except pylopdf.LimitError as error:
    reject_upload(error.code)
```

!!! warning "実行時間を制限し、プロセスを隔離する"

    上限はメモリと出力サイズを制限しますが、時間のかかっている呼び出しを止めはしません。ワーカー、プロセス、コンテナの側でCPU時間または経過時間の期限を設け、可能であれば信頼できないファイルはサンドボックスやコンテナで処理してください。

`DocumentLimits.web()`は、メモリに制約のあるWebサーバーやキューワーカー向けの保守的な出発点です。次の上限を設定します。

| 資源 | 上限 |
|---|---:|
| ファイルサイズ | 10 MiB |
| ページ数 | 200 |
| オブジェクト数 | 100,000 |
| 配列と辞書の入れ子 | 64階層 |
| 展開後の個々のストリーム（展開後の画像を含む） | 64 MiB |
| 1ページのコンテンツストリーム | 10 MiB |
| 展開後のストリームの合計 | 128 MiB |
| 抽出するテキストの合計 | 1 MiB |
| 抽出する文字（グリフ）の合計 | 65,536 |
| レンダラーに渡すPDFデータ | 64 MiB |

上限を変えるには、独自のポリシーを作ります。引数はすべて省略でき、`None`は上限なしを意味します。

```python
limits = pylopdf.DocumentLimits(
    max_file_size=50 * 1024 * 1024,
    max_pages=1_000,
    max_decompressed_size=128 * 1024 * 1024,
)
```

| 引数 | 制限するもの |
|---|---|
| `max_file_size` | 入力ファイルのサイズ |
| `max_pages` | ページ数 |
| `max_objects` | オブジェクト数 |
| `max_object_depth` | 配列と辞書の入れ子 |
| `max_decompressed_size` | 展開後の個々のストリームのサイズ |
| `max_page_content_size` | 1ページの展開後のコンテンツのサイズ |
| `max_total_decompressed_size` | 展開後のストリームの合計サイズ |
| `max_text_size` | 抽出するテキストのUTF-8での合計サイズ |
| `max_text_glyphs` | 抽出する文字の合計数 |
| `max_interpretation_size` | レンダリングと抽出が読むPDFデータのサイズ |

`doc.complexity`は、何も展開せずに、ページ数・オブジェクト数・ストリーム数、エンコードされたストリームのサイズ、最も深い入れ子を報告します。重い処理の前に、文書の振り分けや拒否に使ってください。

`limits`を指定せずに開いた文書には、開くときの上限はありませんが、次に説明する呼び出しごとの上限は適用されます。

### 呼び出しごとの上限 { #default-limits }

大量のデータを読んだり生成したりするメソッドには、それぞれ上限があり、既定値はほとんどの用途で安全な値です。表のキーワードで引き上げるか、信頼できる入力では`None`を渡して外してください。

| メソッド | 既定の上限 | キーワード |
|---|---|---|
| `Document.to_bytes()` | 出力512 MiB | `max_size` |
| `Document.render_pages()` | PNG出力の合計512 MiB | `max_size` |
| `to_markdown()`, `Table.to_markdown()` | 出力64 MiB | `max_size` |
| `Pixmap.tobytes()`, `Page.get_svg_image()` | 出力64 MiB | `max_size` |
| `Page.search_for()` | 結果4,096件 | `max_hits` |
| `Page.content()` | グリフ、パスとクリップのセグメント、項目で合わせて262,144 | `max_elements` |
| `Page.insert_text()`, `insert_textbox()` | テキスト1 MiBと4,096行 | `max_text_size` |
| 描画、フォーム、`Font`へのフォント入力 | フォント1つあたり64 MiB | `max_font_size` |
| `Page.insert_image()` | 入力64 MiBと6,400万画素 | `max_size`, `max_pixels` |
| `Page.replace_text()` | ページのコンテンツ64 MiB | `max_size` |
| 添付ファイル: `add()`, `read()`, `write()` | ファイル1つあたり64 MiB | `max_size` |
| `Stream.read()` | 64 MiB | `max_size` |
| `Document.get_pdfa_claim()`, `get_xml_metadata()`, `set_xml_metadata()` | XMPメタデータ1 MiB | `max_size` |
| `OcrEngine()` | モデルファイル64 MiB | `max_model_size` |

いくつかの上限は固定です。レンダリングは1ページあたり6,400万画素までです。パスワードはPDF 2.0の上限であるUTF-8で127バイトまでです。`doc.pages.select()`、`insert()`、`extend()`、`get_text()`、`to_markdown()`、`render_pages()`に渡すページのリストは、ページ数と4,096のうち大きい方の項目数まで持てます。しおり、ページラベル、フォームフィールド、注釈、添付ファイルの一覧には、実際の大きな文書に合わせた余裕のある固定の上限があります。

拒否された呼び出しは文書を変更する前に例外を送出し、部分的な結果を返すことはありません。

### エラーコード { #error-codes }

`LimitError`は`PdfError`のサブクラスです。`code`属性（`error.args[0]`と同じ）は到達した上限を示し、リリースをまたいで変わらないので、プログラムの分岐に使えます。メッセージは人が読むためのもので、変わることがあります。

| コード | 発生する場面 |
|---|---|
| `file_size`, `page_count`, `object_count`, `object_depth` | 文書が`DocumentLimits`の構造上の上限を超えた |
| `decompressed_size`, `page_content_size`, `total_decompressed_size`, `decompression_unverifiable`, `stream_filter_count` | ストリームの展開後のサイズが許容を超えた、または出力の大きさを制限できないフィルター（16個を超える連鎖したフィルターを含む）を使っている |
| `text_size`, `text_glyph_count`, `interpretation_size` | 抽出やレンダリングがポリシーの許容を超えて読み込もうとした |
| `content_element_count` | ページが描くグリフ、セグメント、項目が`max_elements`より多い |
| `recovery_scan_size` | 壊れたファイルの修復に不当な量の作業が必要になる |
| `content_nesting_depth`, `resource_nesting_depth` | ページの入れ子が深すぎて、解釈するのが安全でない。[後述](#built-in-protections)を参照 |
| `pdf_output_size`, `render_output_size`, `markdown_output_size`, `svg_output_size`, `pixmap_output_size`, `replacement_output_size` | 出力が`max_size`を超える |
| `search_input_size`, `search_hit_count` | 検索テキストが4,096バイトを超える、または結果が`max_hits`より多い |
| `text_input_size`, `text_line_count`, `font_input_size`, `path_point_count` | 描画に渡したテキスト、フォント、Shapeのパスが大きすぎる |
| `canvas_scope_depth` | Canvasのスコープが16段を超えて入れ子になっている |
| `tag_text_size` | 新しい文書で、タグのテキストまたはしおりのタイトルが64 KiBを超えている |
| `image_input_size`, `image_pixel_count`, `image_inventory_count` | 画像が大きすぎる、またはページが4,096を超える画像を使っている |
| `embedded_file_size`, `embedded_file_input_size` | 添付ファイル、またはその名前や説明が大きすぎる |
| `stream_data_size`, `object_value_depth` | 低レベルのストリームが大きすぎて読めない、または書き込む値の入れ子が深すぎる |
| `password_input_size`, `metadata_input_size`, `annotation_input_size`, `form_field_input_size`, `toc_input_size`, `page_label_input_size`, `replacement_input_size` | メソッドに渡したテキストが大きすぎる |
| `page_entry_count` | ページのリストが許容より長い |
| `toc_entry_count`, `toc_text_size`, `named_destination_count`, `named_destination_text_size`, `structure_tree_count`, `form_content_size` | しおり、リンク先の表、タグ付きPDFの構造、取り込むページが大きすぎる |
| `xmp_metadata_size` | XMPメタデータが`max_size`より大きい |
| `xmp_metadata_input_size` | `set_xml_metadata()`に渡したXMPメタデータが`max_size`より大きい |
| `ocr_model_size`, `ocr_dictionary_entries` | OCRのモデルファイルが大きすぎる |

## 組み込みの保護 { #built-in-protections }

次の保護は、`DocumentLimits`の有無にかかわらずすべての文書に適用されます。

- **深い入れ子。** レンダリングエンジンのhayroは、入れ子の構造を再帰的に読みます。pylopdfはページをレンダリング・抽出する前に、そのコンテンツ、色空間、関数の入れ子の深さを検査し、スタックをあふれさせたり、黙って途中で打ち切られたりするページを拒否します（`content_nesting_depth`、`resource_nesting_depth`、`object_depth`）。文書のほかのページは引き続き使えます。
- **レンダラーのクラッシュ。** 不正なデータでレンダラーがパニックした場合、pylopdfは`except Exception`で捕まえられない例外ではなく`PdfError`を送出し、文書は引き続き使えます。
- **壊れたファイル。** pylopdfは可能であれば壊れた相互参照データを修復し、`PylopdfWarning`を出して`doc.is_repaired`を設定します。修復した入力を受け付けてはならないアプリケーションでは、そうした文書を拒否してください。開けない壊れたファイルもあり、その場合は`MalformedPdfError`を送出します。
- **安全な保存。** `save()`と`Pixmap.save()`は保存先の隣にある一時ファイルに書き込み、書き込みがすべて終わってから置き換えるので、失敗しても書きかけのファイルは残りません。Windowsのアクセス制御リストを含め、既存のファイルの権限は保持します。
- **スクリプトなし。** 埋め込まれたJavaScriptは決して実行しません。

## 推奨事項 { #recommendations }

- **時間制限はホスト側で強制してください。** 上限はメモリと出力サイズを抑えますが、一見正常な文書でもレンダリングに長い時間がかかることがあります。信頼できない処理は、ワーカー、プロセス、コンテナでCPU時間または実時間の期限を設けて実行してください。
- **バッチ処理を隔離してください。** 可能であれば、信頼できないファイルはサンドボックスやコンテナで処理してください。
- **墨消しをページ削除に頼らないでください。** ページを削除すると、そのリンク、フォームフィールド、構造の項目は削除されますが、内容のコピーが添付ファイルやメタデータなど、ファイルのほかの場所に残ることがあります。
- **並列レンダリングには`render_pages()`を使ってください。** それ自体がメモリ使用量を制限しているので、さらに上限のない並列処理で包まないでください。

pylopdfのCIは、ネイティブのPythonとWebAssemblyで同じ悪意ある入力のテストを実行し、定期的なファジングで不正な相互参照テーブル、参照の循環、深い入れ子のオブジェクト、壊れたストリーム、圧縮爆弾を与えています。

## 依存関係の監査 { #dependency-auditing }

CIはプッシュのたびに、Rustの依存関係とRustSecのアドバイザリデータベースに対して`cargo audit`を実行します。

このポリシーのリポジトリ版は[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)です。
