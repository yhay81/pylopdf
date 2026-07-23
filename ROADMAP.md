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
  20MB 超 wheel）は追随されにくい差別化軸。さらに 2026-06 の pymupdf-layout
  （pymupdf4llm の精度を裏で支えるレイアウト解析）は Polyform Noncommercial + 商用で、
  商用制限は AGPL より強まる方向 = MIT の商用優位は拡大している
- 同ポジションの Rust 製競合 pdf_oxide（2025-11 開始）は週次リリース・月 14.5 万 DL と
  実稼働だがレンダリング無し・ベンチは自己申告のまま第三者検証ゼロ（2026-07-23 確認）。
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
- アーキテクチャ原則は**単方向データフロー**: lopdf の Document が唯一の真実で、
  hayro はそのシリアライズ結果に対する純関数ビュー（描画・抽出。hayro_pdf キャッシュ +
  編集時 invalidate で同期）。hayro が見るのは lopdf 正規化済みバイト列なので、
  壊れた PDF の解釈差が編集と描画で割れず、描画結果 = 保存結果が常に成立する。
  エンジンを増やすときもこの形を崩さない（krilla も「バイト列を返す純関数 →
  lopdf へ移植」の一方向で入れる。エンジン同士が状態を持ち合う形は採らない）
- lopdf / hayro を使い切る: lopdf の保存時暗号化・SaveOptions・画像挿入/抽出・TOC・
  テキスト置換・インクリメンタル保存、hayro の Device（抽出エンジン化）・
  RenderSettings・warning_sink・hayro-write（ページ→XObject）
- lopdf に存在しない領域（AcroForm・注釈作成・添付・ページラベル）は
  生辞書操作で pylopdf 自身が実装し、付加価値にする
- 自前実装とエコシステム連携を使い分けてコア wheel を軽く保つ:
  組版・新規文書の PDF/A 出力 = typst（typst-py）、電子署名 = pyHanko、
  PDF/A 検証 = veraPDF。連携レシピは統合テストで保証する（v0.7.x 参照）
- krilla（hayro と同一作者の生成クレート、MIT/Apache-2.0）は
  「編集 = lopdf / レンダリング = hayro / 生成 = krilla」の 3 エンジン分担で**導入する**
  （2026-07-23 監査で決定）。確定事実: **krilla コアは hayro 非依存**
  （hayro-write を引くのは `pdf` feature のみ。ページ埋め込みは lopdf 移植で足りるため
  不要）→ hayro-syntax 単一バージョン制約は krilla 導入には効かない。導入形は
  `default-features = false, features = ["simple-text"]`（rustybuzz シェーピング内蔵。
  raster-images は自前実装があるため不要）。skrifa / flate2 / png は hayro 経由で
  ツリー内共有のためサイズ増は +0.5〜1MB 見込み（導入前に実測）。解放される機能の順:
  ① CJK 含む任意フォントの insert_text（サブセット埋め込み）② AcroForm 第 2 段階の
  外観ストリーム生成 ③ 新規文書 PDF/A 出力の内製化（typst 委譲の部分回収）
  ④ タグ付き PDF/UA（将来）

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
- 注意: hayro 0.8 で Device API の破壊的変更（DrawProps 化）が予定されており、
  追従が 1 回必要（詳細はウォッチリスト）

### v0.7.x — エコシステム連携の文書化（自前実装しない領域を「連携で解決済み」にする）

- [x] README（両言語）にエコシステム連携節: 組版・新規文書の PDF/A = typst-py、
      電子署名 = pyHanko、PDF/A 検証 = veraPDF 外部委譲の案内
- [x] 連携レシピの統合テスト（tests/test_interop.py。interop dependency-group で
      typst / pyHanko を入れ、`typst.compile → pylopdf.open(stream=)` と
      「pylopdf 出力 → pyHanko 増分署名で元バイトが無加工で保たれる」ことを検証。
      CI にも interop グループを追加済み）

### v0.8 — 描き込み（v0.8.0 として 2026-07-23 リリース済み）

- [x] `insert_image`（JPEG は SOF 解析でパススルー埋め込み・PNG は png crate で展開し
      透過をソフトマスク化。lopdf の embed_image feature（image crate）は使わず自前実装で
      wheel を軽く保った。既存コンテンツは再エンコードせず追記のみ + 一度だけの q/Q ラップ）
- [x] 透かし・スタンプ = `show_pdf_page`（lopdf ネイティブの ページ→Form XObject 変換。
      hayro-write は不要だった: merge と同じオブジェクト移植で Resources ごと取り込み、
      コンテンツはバイト列のまま Form に包む。元ページの回転・CropBox も表示どおり解決）
- [x] 日本語テキストの描き込み（透かし・ヘッダ/フッタの CJK）は **typst 連携で解決**:
      typst で 1 ページ組んで show_pdf_page で焼く（フォントは typst がサブセット埋め込み、
      pylopdf-fonts-cjk の Noto を font_paths で再利用）。統合テストで保証済み。
      krilla 導入は「連携なしで完結する insert_text の CJK 対応」が要るときの
      将来オプションへ格下げ（制約は旧記載どおり: hayro-syntax の単一バージョン解決が必須）
- [x] テキスト置換（lopdf replace_partial_text）の公開（Page.replace_text。
      単純エンコーディングのみ・CJK 非対応と明記）
- [x] ヘッダ / フッタ / ページ番号 / Bates 番号 = `Page.insert_text`（標準 14 フォント・
      WinAnsi の範囲。CJK 入力は typst レシピへ誘導するエラー。回転ページは表示空間の
      Tm で正立。ページ番号等はループで印字するレシピを README に記載）
- [x] 注釈の読み取り（annots）+ highlight / link 注釈の作成（search_for の結果を
      そのまま渡す「検索してマーカー」が完成）。ハイライトは外観ストリーム（AP /N、
      Multiply ブレンド）を必ず生成する — **hayro は AP 付き注釈を描画する**
      （render_annotations 既定 true、12.5.5 実装をソース確認）ため、pylopdf 自身の
      レンダリングで画素検証できる。AP の無い注釈は hayro が描画しない点に注意

### v0.9 — 文書の仕上げ（v0.9.0 として 2026-07-23 リリース済み）

- [x] AcroForm 読み取り → 記入の第 1 段階 = `get_form_fields / set_form_field`
      （継承 FT/Ff/V 解決・ドット連結の完全名・チェックボックスの bool → on 状態自動解決・
      /AS 同期・NeedAppearances 方式。外観ストリームの自前生成は第 2 段階として残す =
      pylopdf 自身のレンダリングには記入値が現れない）
- [x] 添付ファイル（EmbeddedFiles）= `embfile_add / names / get / del`（Kids 分割
      ツリーの再帰読み + 平坦書き戻し、/Names の他ツリーは保存。日本語ファイル名は
      UF へ。garbage/deflate/object_streams 保存でも生存することをテストで保証）
- [x] ページラベル = `get_page_labels / set_page_labels` + `Page.get_label`
      （番号ツリーの再帰読み + 平坦書き戻し、R/r/A/a/D の表示ラベル計算付き）
- [x] `to_markdown` 初版（Document / Page。サイズ最頻値 = 本文、大きいサイズを # 階層へ。
      CJK の行折り返しは空白なし連結・箇条書き正規化・OCR 層とも連動。
      未対応と明記: 太字/斜体（スパンにフォント名が無い → 抽出エンジンの拡張課題）、
      表、多段組の読み順、縦書き。実世界コーパス 6 本でスモーク確認済み）
- 見送り: インクリメンタル保存（2026-07-23 の OSS 分析で判断）。qpdf/pikepdf は増分更新の
  生成を持たない「正規化して書き直す」設計で成功しており、pypdf は 5.0（2024-09）での追加
  直後から不具合が続いた（pypdf#3118 等）= 実装が壊れやすい割に、生成の本命ユースケース
  「署名の維持」は pyHanko 連携（増分署名・バイト無加工保証済み）が既に担っている。
  需要が issue として実在したら再評価（ウォッチリスト参照）
- [x] OCR 結果の不可視テキスト層書き込み = `Page.insert_ocr_text_layer`（ocrmypdf 方式:
      非埋め込み CID フォント + Identity-H + ToUnicode + Tr 3。日本語含め fallback
      フォント非依存で抽出・検索でき、サイズ増ほぼゼロ。get_text("words") 形式を
      そのまま受ける。v0.10 [ocr] の土台）
- [x] XMP の PDF/A 宣言の読み取り = `Document.get_pdfa_claim`（(part, conformance)。
      typst の krilla 検証付き出力から (2, "B") を読めることを連携テストで保証。
      検証ではないことを docstring で明示）

### v0.10 候補 — `pylopdf[ocr]`（日本語 OCR、公開判断は精度実測後）

「pip だけで完結・共有ライブラリゼロ・寛容ライセンスの日本語 OCR」は Python エコシステムの
空白（pymupdf は Tesseract 外部インストール必須、pponnxcr は AGPL、rapidocr は
onnxruntime の C++ ランタイム依存）。CJK の堀と一致するため段階導入を検討する:

- ランタイム = rten（純 Rust ONNX、MIT/Apache-2.0）を静的リンク。本体 wheel +1.5〜2.5MB 見込み
- モデル = PP-OCRv5_mobile（det 4.6MB + rec 15.8MB、Apache-2.0。標準認識モデルが
  日本語込みのため日本語専用モデル不要）
- 配布 = `pylopdf-ocr-models` 別 wheel（fonts-cjk と同じパターン。モデル世代を本体と
  独立に更新できる）
- [x] 前提条件 1: 日本語精度の実測（2026-07-23 スパイク完了 → **go**）。
      PP-OCRv5 mobile（ch モデルが日中英を単体カバー、日本語専用 rec は v5 に存在しない）
      を 300dpi で実測: 厳密 CER 4.0% / NFKC 正規化後 1.3%（合成 5 種 + mhlw 実文書、
      GT 計 2,428 字）。漢字・かな・数字はほぼ完璧で、残存誤りは全半角折り畳みと記号
      （丸数字・〒・※）。v4 日本語専用モデルより実質精度で優り、server 版との差も 0.5pt
- 前提条件 2（残り）: rten が PP-OCRv5 mobile の ONNX を実行できるかの実行スパイク
- 設計注意（スパイクからの引き継ぎ）: OCR 入力は白背景指定必須（render_page 既定は透明）・
  既定 300dpi（200dpi は 9pt 以下の行を det が取りこぼす）・パイプラインに内部縮小を
  入れない・配布は det+rec+cls+辞書 ≈ 22MB を別 wheel で
- 参考実装 ocrs-cjk（MIT/Apache）は依存にせずコード参考に留める

### v1.0 — 信頼の宣言

- API 凍結・semver 宣言・deprecation ポリシー
- [x] ドキュメントサイト（EN / JA）+ pymupdf 移行ガイド（2026-07-24 に Zensical
      0.0.51 + 独自 Living Document テーマへ刷新。https://yhay81.github.io/pylopdf/ 。
      言語別の厳格ビルド、検索・ダークモード・同一ページ言語切替・llms.txt・OG カードに
      対応。docs.yml が main push で自動デプロイ、CI は Rust ビルド無しの
      --only-group docs）
- [x] 公開ベンチマーク基盤（bench/run.py。同一コーパス・同一タスク・中央値、
      勝ち負け両方掲載 + pymupdf との抽出類似度を正確さの代理指標に。初回実測 2026-07-23:
      抽出 7 本中 4 本で pymupdf より速い・merge 4.1 倍速・2x レンダリングは 7 本すべてで
      pylopdf が高速）。
      py-pdf/benchmarks への掲載申請は別途
- cp314t（free-threaded）wheel（abi3 は free-threaded に入らないため別ビルド。
  pymupdf はスレッド非対応明言のため追随困難 = 差別化）。このレーンでは
  **buffer protocol も有効化**する（abi3-py310 制約が消えるため。Pixmap の
  ゼロコピー numpy 連携が解禁され「並列 + ゼロコピー」の二本看板になる）
- 実行時エラーメッセージの英語化（現状は日本語。EN/JA ドキュメントで世界配布する以上、
  例外メッセージは英語に揃える。**API 凍結前に実施**。コメント・docstring・コミット
  メッセージ日本語の方針は変えない。規模実測 2026-07-23: rust 51 箇所 +
  python 49 箇所 ≈ 100 箇所。メッセージを断言するテストの追随込みで 1 セッション仕事）
- [x] SECURITY.md（私的報告の導線 + 信頼できない PDF の扱い + max_decompressed_size 案内）と
      cargo-audit の CI 組み込み（2026-07-23。pip-audit は実行時依存ゼロのため対象が無く見送り）

### v1.x — パリティと性能の穴埋め（2026-07-23 の使い切り監査より）

lopdf / hayro / krilla の残在庫・インターフェース穴・性能余地の監査で確定した候補。
優先順は「即効小粒 → krilla スパイク → 上流サイクル → 穴埋め → cp314t」。

- [ ] zlib-rs（flate2 の backend feature 切替のみ。高圧縮系 3.3 倍の調査済み）
- [x] save の `compression_level` / `linearize` は 2026-07-23 のソース確認で**公開見送り**:
      linearize は lopdf 0.44 では writer が一切参照しない死にフラグ（定義のみ。
      is_linearized は既存線形化文書の検出用）。compression_level は object streams
      専用かつ 0/1-3/4-6/7+ の 4 段階バケットで、deflate=True の通常ストリームは
      Object::compress が Compression::best() 固定 = 半分しか効かない紛らわしい
      ノブになる。→ lopdf へ「compression_level を通常ストリーム圧縮へも一貫適用」の
      上流貢献をしてから公開する（下の上流貢献候補に追加）
- [ ] `Page.get_links`（リンク注釈 + /Dest・/A GoTo・named destination の解決。
      annots() はあるがリンク先ページ解決が無い = pymupdf 移行需要の高い穴）
- [x] krilla 導入スパイク（2026-07-23 完了 → **go**）: krilla 0.8.2 を
      `default-features = false, features = ["simple-text"]` で単離ビルドし問題なし。
      Noto Sans JP 4.5MB からサブセット埋め込みで **8KB** の日本語 1 ページ PDF を生成し、
      pylopdf 自身で「開く → get_text が原文完全一致（ToUnicode も正しい）→
      レンダリング正常」を確認。スパイク exe は 3.3MB だが skrifa などは hayro と
      共有のため pyd への実増はこれを大きく下回る見込み（統合時に実測）。
      次の段: `insert_text` の任意フォント対応 API 設計 →「krilla で 1 ページ生成 →
      lopdf へ Form XObject 移植」の単方向パターンで統合
- [ ] `get_pixmap(clip=)`（hayro RenderSettings は原点固定 viewport のみで任意 clip
      不可 → 上流へ RenderSettings の offset/transform 公開を提案するか、全面レンダ +
      切り出しの先行実装かを判断）
- [ ] 抽出結果の世代キャッシュ（search → annotate ループの再解釈をゼロに。
      hayro_pdf と同じ generation キーで 1 層。TextPage 風の明示オブジェクトも検討）
- [ ] `Document.render_pages(workers=)`（GIL 解放済み 1.93 倍/2T の薄い公式 API 化。
      cp314t で真価）
- Annot/Widget のオブジェクトモデル化は更新系 API が増えるまで dict/タプル維持
  （pymupdf の重厚な Annot は追わない）

### 並走（リリースに紐づけない）

- コーパス拡充: 壊れた PDF（切断 xref など）、Type3 フォント、JPX、透明グループ、注釈/リンクもの
- [x] 回転ページの抽出を表示空間へ正規化（2026-07-23。抽出 Context へレンダラと同じ
      `initial_transform(true)` を渡す方式で解消。読み順・検索・words・画像 bbox・OCR 層が
      回転ページでも表示座標になり、CropBox 原点が 0 でないページのオフセットも正しくなった）
- [x] レンダリング速度の改善（2026-07-23 プロファイル: 主因はラスタライズではなく
      PNG エンコード（最悪 85%。png crate 既定の Balanced+Adaptive が写真系で ~11MB/s）。
      Fast(fdeflate) + GIL 解放へ変更し、コーパス全 7 本でレンダリングも pymupdf 超え
      （wdl6812 278→43ms）。残る候補: RenderCache の hayro_pdf 同寿命再利用（連続レンダで
      -27〜35%、自己参照設計が必要）/ flate2 の zlib-rs feature（高圧縮系 3.3 倍速）/
      hayro 上流: ステンシルマスク画像経路の最適化と num_threads 公開（issue ドラフト準備済み））
- [x] 抽出スパンへのフォント名 + pymupdf 互換 flags の追加（2026-07-23。埋め込みフォントの
      weight / italic メタデータ由来。to_markdown が本文の太字・斜体を強調マーカー化）。
      残: 標準 14（Type1）は hayro が font_data を返さないため flags 0 —
      Type1 のメタデータ公開は hayro への上流貢献候補
- 上流貢献（2026-07-23 に着手）: lopdf#535 の修正 PR を提出済み
  （[lopdf#537](https://github.com/J-F-Liu/lopdf/pull/537)。コメント直後の空白で
  content パーサが以降を全部落とすバグの 1 行修正 + 回帰テスト。レビュー待ち）。
  hayro へは性能 issue [#1315](https://github.com/LaurenzV/hayro/issues/1315)
  （ステンシルマスク画像が約 5 倍遅）と機能提案
  [#1316](https://github.com/LaurenzV/hayro/issues/1316)（RenderSettings への
  num_threads 公開）を提出。num_threads は修正 PR
  [#1317](https://github.com/LaurenzV/hayro/pull/1317) を提出済み
  （cargo hack --each-feature 全 8 通過・clippy 1.92/fmt クリーン・6 PDF・147 ページ
  ピクセルビット一致、レビュー待ち）。ステンシルマスクも修正 PR
  [#1318](https://github.com/LaurenzV/hayro/pull/1318) を提出済み
  （寸法不一致マスクをネスト描画でなく共通グリッドへ合成する方式。wdl6812 の
  マスク画像描画 11.4→4.2ms・ページ全体 ~30→~21ms。マスク合成順序が変わるため
  上流テスト 26 件に低振幅の描画差分あり＝目視確認済み・PR 本文に開示。
  レビュー待ち）。
  packed 1-bit マスク展開の LUT 化も issue
  [#1319](https://github.com/LaurenzV/hayro/issues/1319) + 修正 PR
  [#1320](https://github.com/LaurenzV/hayro/pull/1320) を提出済み（2026-07-23 夕。
  当初 JBIG2 起因と誤帰属 → 深掘りで JBIG2 フィルタは 8-bit 展開済みと判明し
  issue を公開訂正。実際の対象は Flate 等の packed 1-bit ImageMask で、合成
  2400x3150 マスクの decode_mask_data 48–60ms → 1.5–1.6ms（約 33 倍）・出力は
  スイート全体でピクセル同一。wdl6812 の残り ~4.4ms は hayro-jbig2 算術復号
  そのもの = 別テーマとして残る）。
  ほか候補: hayro #452（公式テキスト抽出 Device）、Type1 フォントメタデータの公開、
  RenderSettings への clip/offset 公開、
  RenderCache の 'static 化（同寿命再利用 -27〜35% を全 embedder に解放）。
  lopdf 側の候補: SaveOptions.compression_level の通常ストリームへの一貫適用
  （Object::compress の Compression::best() 固定解消）、死にフラグ linearize の
  実装または削除（2026-07-23 ソース確認）
- [x] CI への Python 3.10 ジョブ追加（abi3 下限の検証。2026-07-23）
- Pyodide / emscripten wheel の実験（pymupdf の wasm wheel は micropip 不可という弱点あり）
- テーブル抽出の研究（v1.0 後の主要テーマ候補）

## ウォッチリスト（再評価トリガー付き）

- **hayro 0.8**: Device API の DrawProps 化（#1245）は main マージ済み・未リリース。
  リリースされたら extract.rs の 2 impl を追従（paint をほぼ無視しているため機械的な
  書き換えで済む見込み）。krilla 導入とは別コミットに分ける
- **fulgur**（Blitz + krilla の HTML→PDF、MIT/Apache-2.0）: @page・改ページ・running
  headers/footers・タグ付き PDF/UA-1 まで実装済みだが、生後 4 か月・単独作者・
  css-page WPT 24.1%・API 激動中。2027-01 頃に生存・API 安定・Blitz 0.3 正式版を確認して
  統合を判断（pyfulgur が非 abi3・cp39–cp312 止まりなのは pylopdf 側の隙）
- **underskrift**（lopdf ベースの PAdES 署名、BSD-2-Clause、kushaldas 作）: 2026-03 出現、
  B-B〜LTA を謳い活発。成熟と lopdf 版数整合を確認できたら署名の optional バックエンド候補
- **PP-OCRv6**（2026-06 リリース）: ONNX 変換エコシステムの追随を待って [ocr] の
  モデル世代を判断
- **parley**（linebender のテキストレイアウト。krilla の dev-deps でも使用実績）:
  insert_textbox（矩形流し込み = 自前組版の宣言済み上限）を作る段になったら
  行分割エンジンとして評価
- **zune-jpeg**: 「PDF 軽量化」（compress_images(dpi=, quality=) — 日本のメール添付
  文化で実需の割に pypdf/pikepdf に無い）を作る場合の JPEG 再圧縮部品候補
- **PP-DocLayout**（Apache-2.0）: pymupdf-layout（Polyform Noncommercial）対抗の
  [layout] extra 候補。rten ランタイムを [ocr] と共有できる。[ocr] 成立後に評価
- **インクリメンタル保存**: 需要の実在（issue）か pypdf 実装の安定化を確認したら再評価。
  実装経路は「ロード時の元バイトを保持 + 保存時に元を再パースして差分オブジェクトだけを
  lopdf IncrementalDocument で追記」の diff 方式（暗号化文書は対象外から始める）

## やらないこと（明示的スコープ外）

集中のために宣言する（2026-07-23 の深掘り調査で根拠を更新。OCR 内蔵は条件付きで
v0.10 候補へ昇格したためこの一覧から外した）:

- **pymupdf ドロップイン互換**: 「風」に留める（基本方針参照）
- **任意 PDF の PDF/A 変換・検証**: krilla の validated export は新規生成専用で、
  既存 PDF ページの埋め込みは `ValidationError::EmbeddedPDF` として明示的に禁止 =
  「lopdf 編集済み PDF の PDF/A 化」は現行エコシステムでは組めない。検証は
  veraPDF（Java・数百ルール）の再発明になる。新規文書の PDF/A は typst 連携で解決し、
  XMP 宣言の読み取りだけを v0.9 で提供する
- **電子署名の自前実装**: lopdf IncrementalDocument で技術的には組める（元バイトの
  無加工保存を writer.rs で確認済み）が、pyHanko（MIT、PAdES B-LTA + 検証まで、現役）
  連携で解決する。国内需要は認定タイムスタンプ・LTV まで要求する SaaS 開発層に偏り、
  B-B 止まりの中途参入が最悪手。underskrift の成熟はウォッチリストで追う
- **XFA / JavaScript フォーム**: XFA は PDF 2.0 で deprecated・Rust 実装ゼロ・
  主要ビューア非対応。PDF 内 JS の実需はフォーム計算程度で、JS エンジン同梱は
  軽量方針にもセキュリティ面積にも反する
- **HTML→PDF の自前実装**: ページ分割レイヤーの自作は fulgur が 2,800 コミットを
  費やしている作業の再発明。fulgur をウォッチリストで追う
- **typst / 組版エンジンの組み込み**: typst は wheel +25〜33MB（typst-py 実測）で
  軽量の軸を破壊するため typst-py 連携で解決。自前の組版は「矩形へのテキスト流し込み」
  （pymupdf insert_htmlbox 相当の最小 API）を将来検討の上限とする

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

## 調査メモ（2026-07-23: スコープ外領域の深掘り再調査）

krilla / typst / 純 Rust OCR / 電子署名 / HTML→PDF の 5 領域を並列調査した確定事実:

- **krilla 0.8.2**（MIT OR Apache-2.0、hayro と同一作者）: PDF/A-1〜4 の全 conformance
  （a/b/u、A-4/F/E）+ PDF/UA-1 の validated export。CI で veraPDF + Arlington 検証、
  typst 0.14 から PDF バックエンド採用。`pdf` feature（hayro-write 0.7.0 経由）で既存 PDF
  ページを XObject / ページとして取り込めるが、validated export 中は EmbeddedPDF エラーで
  禁止。NOTICE.md に resvg（MPL）由来コードの開示あり（同梱時は wheel のライセンス表記へ追加）
- **hayro-write 0.7.0**: docs に「internal crate, not meant for external use」と明記。
  直接依存するより krilla の `pdf` feature をラッパーとして使う方が安定面
- **typst-py**: wheel 実測 25.7〜36.5MB、月 43.7 万 DL、上流リリース翌日追従、
  cp38-abi3 + cp314t + emscripten wheel あり。PDF standard 指定で PDF/A-1b〜4・UA-1 出力可。
  typst 本体は縦書き（#5908）・ルビ（#1489）とも未対応で「日本語組版」は名乗れない
- **純 Rust OCR**: ocrs 本家は Latin 専用 + モデルが CC-BY-SA-4.0 で不適合。
  PP-OCRv5_mobile（Apache-2.0、det 4.6MB + rec 15.8MB）は標準認識モデルが日本語込み。
  ランタイムは rten（純 Rust ONNX、累計 97 万 DL、活発）が本命、tract-onnx が次点。
  Apache-2.0 モデルの wheel 再配布は rapidocr が長年実践（LICENSE/NOTICE 同梱で可）
- **電子署名**: RustCrypto cms 0.2.3 世代（der 0.7 / x509-cert 0.2）で PAdES B-B は構築
  可能だが、ESS signing-certificate-v2 型が cms に無く自前 DER 定義が要る。lopdf
  IncrementalDocument は元バイト列を無加工で書き出す（writer.rs で確認）。Python 側は
  pyHanko 0.35.2（MIT）が現役の決定版
- **PDF/A 検証**: OSS は veraPDF（Java、GPLv3+/MPLv2+ デュアル）が事実上唯一。
  Rust の pdf-compliance は商用（本番有償）。Python ネイティブ実装は存在しない
- **HTML→PDF**: Blitz は pre-alpha でページ分割（fragmentation）は 1.0 バックログ、
  hyper-render は活動 2 日で死亡。fulgur（Blitz + krilla、MIT/Apache、2026-03 開始・
  55 リリース・2,814 commits）が paged media 一式を実装済み。pyfulgur 0.37.0 が
  PyPI 公開済（cp39–cp312、非 abi3）。weasyprint は月 3,313 万 DL で性能不満が
  公式文書に明記 = Rust 製代替の需要は実在
- **XFA / JS**: XFA は PDF 2.0 で deprecated・Rust 実装ゼロ。pdf.js は QuickJS
  サンドボックスでフォーム計算のみ既定有効 = 抽出・編集ライブラリに JS 実行は不要
- **pymupdf 1.28**: AGPL 維持。pymupdf-layout（GNN レイアウト解析、pymupdf4llm を裏で
  強化）を Polyform Noncommercial + 商用で投入
- **pdf_oxide**: 週次リリース・月 14.5 万 DL・899 stars と実稼働。ただしレンダリング無し、
  ベンチは自己申告のまま第三者検証なし
