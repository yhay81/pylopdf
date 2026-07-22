# pylopdf ロードマップ

2026-07-22 実施の市場・upstream 調査（lopdf 0.44 全 API / hayro 0.7 全クレート /
Python PDF エコシステム）と、2026-07-23 実施のスコープ外領域の深掘り再調査
（krilla / typst / 純 Rust OCR / 電子署名 / HTML→PDF。確定事実は文末の調査メモ）に基づく中期計画の正本。
日々の開発コンテキストは [AGENTS.md](AGENTS.md)、確定した変更履歴は [CHANGELOG.md](CHANGELOG.md) を参照。

## 戦略

**「permissive ライセンスで、レンダリング + 位置付きテキスト抽出 + 編集が 1 つで完結する、
検証可能に正確なライブラリ」** を目指す。

- 2026-07 時点、この 3 拍子が揃った permissive な成熟ライブラリは存在しない
  （pymupdf = AGPL、pypdfium2 = 編集が弱く bus factor 1 を公式宣言、
  pikepdf = 抽出/描画を明示的にスコープ外、pypdf = 抽出が遅く描画なし）
- pymupdf の構造的弱点（AGPL、スレッド非対応の公式明言、free-threaded wheel 無し、
  20MB 超 wheel）は追随されにくい差別化軸
- 同ポジションの Rust 製競合 pdf_oxide（2025-11 開始、自己申告ベンチ中心）に対しては
  「実世界コーパス + 再現可能な検証 + 上流貢献」の信頼で差別化する
- pdf_oxide とは別に **oxidize-pdf**（bzsanti/oxidizePdf, MIT, crates.io `oxidize-pdf`）が
  もう一つの直接競合。パース/生成/抽出/暗号化/分割結合回転を pure Rust で一体提供し
  「AI/RAG 向け構造認識チャンキング」を前面に押す。2026-07-22 時点で 91 リリース・
  直近も同月更新と開発速度が速く、要ウォッチ（pdf_oxide とは別作者・別リポジトリなので混同注意）
- 需要が最も大きいのは位置付きテキスト抽出とその先の Markdown 変換
  （RAG/LLM 用途。pymupdf4llm は月 2,400 万 DL、docling は月 2,000 万 DL）
- CJK（縦書き・CID フォント・日本語帳票）は既存の fallback 実装と
  コーパスを活かせる、グローバル競合が再現しにくい固有の堀

## 基本方針

- pymupdf「互換」ではなく「風」。ただし words タプル順・dict 構造・
  `search_for → list[Rect]` など移行コストを決めるデータ形状は pymupdf に合わせる
- lopdf / hayro を使い切る: lopdf の保存時暗号化・SaveOptions・画像挿入/抽出・TOC・
  テキスト置換・インクリメンタル保存、hayro の Device（抽出エンジン化）・
  RenderSettings・warning_sink・hayro-write（ページ→XObject）
- lopdf に存在しない領域（AcroForm・注釈作成・添付・ページラベル）は
  生辞書操作で pylopdf 自身が実装し、付加価値にする

## リリース計画

各リリースは 1 テーマ。順序は依存関係（Page オブジェクト → 抽出 → 描き込み）で決めている。

### 直近（0.5.x の基盤強化）

- [x] レンダリングキャッシュ（編集で無効化される hayro Pdf の保持。
      毎レンダリングの再シリアライズ + 再パースを解消）
- [x] 重い処理（load / save / render / 抽出 / merge）での GIL 解放
- [x] `render_page` の `dpi=` / `background=`
- [x] `save` / `tobytes` の `garbage=` / `deflate=` / `object_streams=`
      （lopdf SaveOptions。圧縮済みの bill-hr815.pdf でも実測 13% 削減）
- [x] リポジトリ public 化（2026-07-22。説明文とトピックも設定済み）
- [x] README 比較表への暗号化/CJK 行追加（2026-07-23）
- [ ] 発見可能性の続き: py-pdf/benchmarks への参加検討、Zenn/Qiita 等での発信

### v0.6 — ページ操作と保存の完成（v0.6.0 として 2026-07-23 リリース済み）

- [x] Page オブジェクト（`doc[i]` / 負数インデックス / イテレーション。世代管理で
      構造変更後の古い Page を StalePageError に）
- [x] ページ回転・MediaBox/CropBox の取得/設定（継承・間接参照解決付き）
- [x] `insert_pdf` の範囲指定（from_page / to_page / start_at、逆順可）、`new_page`、
      `copy_page`、select の重複指定によるページ複製
- [x] TOC 読み書き（`get_toc` / `set_toc`。ページ番号は pymupdf 互換の 1 始まり）
- [x] 保存時暗号化（AES-256 V5/R6 + Permissions。元ドキュメントは平文のまま）
- [x] 例外階層（PdfError 基底 = ValueError 互換、PasswordError / DocumentClosedError /
      EncryptedDocumentError / StalePageError）
- [x] `peek_metadata`（全体パース無しの高速メタデータ）、
      `max_decompressed_size`（解凍爆弾対策）の公開

### v0.7 — 位置付きテキスト抽出（v0.7.0 として 2026-07-23 リリース済み）

- [x] hayro-interpret の Device 実装による抽出エンジン（lopdf extract_text から置き換え。
      `get_text("text"/"words"/"blocks"/"dict")`、`search_for → list[Rect]`、
      不可視テキスト対応。lopdf#535 と非埋め込み CJK 抽出はこれで解消。
      ※MCID の保持は未実装（v0.9 の to_markdown で必要になったら対応）
- [x] 画像抽出（Page.get_images。DCT で終わるフィルタ列は JPEG パススルー）
- [x] hayro の warning_sink → Python warnings 連携（PylopdfWarning）
- [x] Pixmap オブジェクト（※buffer protocol は断念: Py_buffer が安定 ABI に入るのは
      Python 3.11 からで abi3-py310 と両立しない。samples は 1 コピー。
      abi3 下限引き上げ時か cp314t 別ビルド時に再検討）
- 注意: hayro 0.8 で Device API の破壊的変更（DrawProps 化）が予定されており、追従が 1 回必要

### v0.8 — 描き込み

- `insert_image`（lopdf embed_image feature。JPEG はパススルー埋め込み）
- 透かし・スタンプ（hayro-write の ページ→Form XObject 経路で「ベクタのまま」重ねる。
  pymupdf の show_pdf_page 相当）
- ヘッダ / フッタ / ページ番号 / Bates 番号の印字（標準 14 フォントのテキスト描画）
- テキスト置換（lopdf replace_partial_text）の公開
- 注釈の読み取り + highlight / link 注釈の作成
  （search_for と組み合わせて「検索してマーカー」を完成させる）

### v0.9 — 文書の仕上げ

- AcroForm 読み取り → 記入（NeedAppearances → 外観生成の段階導入。lopdf に無いため自前実装）
- 添付ファイル（EmbeddedFiles）、ページラベル
- `to_markdown` 初版（見出し推定・読み順。pymupdf4llm の代替）
- インクリメンタル保存（lopdf IncrementalDocument。0.44 から暗号化文書も対応）

### v1.0 — 信頼の宣言

- API 凍結・semver 宣言・deprecation ポリシー
- ドキュメントサイト（EN / JA）、pymupdf からの移行ガイド
- py-pdf/benchmarks 掲載と公開ベンチマーク（速度・正確さの第三者検証可能な形）
- cp314t（free-threaded）wheel（abi3 は free-threaded に入らないため別ビルド。
  pymupdf はスレッド非対応明言のため追随困難 = 差別化）
- SECURITY.md + cargo-audit / pip-audit の CI 組み込み

### 並走（リリースに紐づけない）

- コーパス拡充: 壊れた PDF（切断 xref など）、Type3 フォント、JPX、透明グループ、注釈/リンクもの
- 上流貢献: lopdf#535 の修正 PR 自作、hayro #452（公式テキスト抽出 Device）への貢献
- CI への Python 3.10 ジョブ追加（abi3 下限の検証）
- Pyodide / emscripten wheel の実験（pymupdf の wasm wheel は micropip 不可という弱点あり）
- テーブル抽出の研究（v1.0 後の主要テーマ候補）

## やらないこと（明示的スコープ外）

集中のために宣言する: pymupdf ドロップイン互換 / PDF/A 生成・検証 / 電子署名の付与 /
XFA・JavaScript フォーム / HTML→PDF（weasyprint の領域）/ 高レベル組版（reportlab の領域）/
OCR エンジン内蔵（ただし OCR 結果を不可視テキスト層として書き込むプリミティブは検討対象）。

## 調査メモ（2026-07-22 時点の確定事実）

- lopdf 0.44.0 が最新リリース。`time` feature は 0.44 でもコンパイル不能
  （上流 #527 の修正はマージ済み・未リリース）→ default-features 無効を維持
- lopdf の save_with_options（object streams）は PDF バージョン 1.5 への引き上げと
  xref stream への切り替えを自動で行う。ObjectStreamConfig の既定は 100 obj / 圧縮レベル 6
- hayro 0.7 系は Device トレイト + 公式抽出サンプルを同梱済み。全クレート MIT/Apache-2.0 デュアル。
  typst 0.14 が PDF 埋め込みの実体として採用
- 月間 DL（pypistats、2026-07-22）: pymupdf 1.06 億 / pypdf 1.16 億 / pdfplumber 5,400 万 /
  pypdfium2 6,800 万 / pikepdf 930 万 / pymupdf4llm 2,400 万 / docling 2,000 万
- AGPL 回避の実例: doctr#486（pymupdf 除去）、browser-use#2610（推移的依存でも問題化）、
  marker の pdftext 自作（「without the AGPL license」を明記）

## 調査メモ（2026-07-23: Rust 製 PDF crate エコシステム）

- **krilla**（LaurenzV/typst エコシステム、pdf-writer 上の高レベル生成 API）は hayro の
  「兄弟プロジェクト」。同作者ゆえ API 思想が近く、v0.8 以降で描き込み機能を強化する際の
  最有力参考実装候補
- 抽出専業の新興 Rust 実装が 2026 年に集中して登場している: **kreuzberg**（多言語バインディング付き
  汎用ドキュメント抽出、star 8.7k・活発）、**pdf-extract**（lopdf 依存、crates.io 総 DL 319 万と
  地味に定着済み）、unpdf、pdfsink-rs。v0.7 の抽出機能は空白市場ではなく既に競合が多い前提で臨む
- mupdf-rs（AGPL）・poppler-rs（GPL 系）はライセンス上 pylopdf が直接依存する対象にはならない
  （参考実装止まり）。pdfium-render（MIT、PDFium は BSD 系だがネイティブバイナリ依存）はレンダリングを
  既に hayro が担うため競合しない
- pdf-rs/pdf（低レベルパーサ、MIT）は書き込みが実験的で、lopdf（DL 1287 万・star 2.2k）に対し
  規模が小さく乗り換え動機は薄い
