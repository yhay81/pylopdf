# 実世界 PDF テストコーパス

実際のツールチェーンが生成した PDF に対する回帰テスト（tests/test_real_world.py）用のアセット。
lopdf / hayro の限界を早期発見することが目的。すべて再配布可能なライセンスの文書のみを同梱する。

取得日: 2026-07-22

| ファイル | 出典 | ライセンス | 検証している軸 |
|---|---|---|---|
| f1040.pdf | [irs.gov](https://www.irs.gov/pub/irs-pdf/f1040.pdf) | 米国政府著作物（パブリックドメイン） | PDF 1.7、AcroForm、タグ付き PDF、オブジェクトストリーム（Adobe Designer 生成） |
| pdf20-simple.pdf | [pdf-association/pdf20examples](https://github.com/pdf-association/pdf20examples)（"Simple PDF 2.0 file.pdf"） | CC-BY 4.0 | PDF 2.0 ヘッダ、非圧縮の最小構成、/Encoding 指定のない Type1 フォント |
| usrguide.pdf | [latex-project.org](https://www.latex-project.org/help/documentation/usrguide.pdf) | LPPL（自由に再配布可） | PDF 1.5、pdfTeX 生成、Type1 サブセットフォント、数式・合字 |
| bill-hr815.pdf | [govinfo.gov](https://www.govinfo.gov/content/pkg/BILLS-118hr815enr/pdf/BILLS-118hr815enr.pdf)（H.R.815, 118th Congress） | 米国政府著作物（パブリックドメイン） | PDF 1.5、GPO 組版、110 ページの中規模文書 |
| mhlw-doc.pdf | [mhlw.go.jp](https://www.mhlw.go.jp/content/11201250/001526113.pdf)（労働基準法における「労働者」に関する研究会 資料2-1） | [政府標準利用規約 2.0](https://www.digital.go.jp/resources/open_data/)（CC-BY 4.0 互換） | PDF 1.7、CJK 埋め込み CID フォント、縦横混在レイアウト |
| patent-us223898.pdf | [Google Patents](https://patents.google.com/patent/US223898A)（エジソンの電球特許、1880 年） | パブリックドメイン（米国特許） | PDF 1.3、スキャン画像（CCITTFaxDecode）、OCR テキストレイヤー（取得日: 2026-07-22） |
| wdl6812-manuscript.pdf | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Illuminated_Panel_and_Qur%27anic_Chapter_WDL6812.pdf)（World Digital Library の彩飾写本） | パブリックドメイン | PDF 1.4、カラースキャン（DCTDecode + JBIG2Decode）、テキストレイヤー無し（取得日: 2026-07-22） |

## 過去に記録していた既知の限界（解消済み）

- **pdf20-simple.pdf のテキスト抽出が空になる**（lopdf の content パーサが
  「`%` コメント行 + 直後のインデント行」で以降の全演算を落とす。
  [lopdf#535](https://github.com/J-F-Liu/lopdf/issues/535) として上流報告済み）
  → v0.7 で抽出を hayro エンジンへ置き換えて解消。test_pdf20_comment_streams_extract が
  回帰検知を担う。非埋め込み CJK（90ms-RKSJ-H）の抽出も同時に可能になった。

## カバー済みの軸

- 暗号化 PDF（tests/assets/encrypted/: RC4-40/128・AES-128・AES-256）
- 非埋め込み CJK フォント（tests/test_cjk.py の合成 PDF + pylopdf[cjk]）
- スキャン画像: CCITTFaxDecode + OCR レイヤー（patent-us223898.pdf）、
  DCTDecode + JBIG2Decode + テキストレイヤー無し（wdl6812-manuscript.pdf）

追加候補が出たら「再配布可能なライセンス・1MB 未満・既存にない軸」を基準に選ぶ。
