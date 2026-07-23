# AGENTS.md

このファイルがエージェント向け開発コンテキストの正本
（CLAUDE.md は @import でここを参照するだけ。更新はこのファイルへ）。

pylopdf: Rust 製 PDF 編集・レンダリングの Python ライブラリ（PyPI 公開中）。
編集 = [lopdf](https://github.com/J-F-Liu/lopdf)、レンダリング = [hayro](https://github.com/LaurenzV/hayro)。
API は pymupdf 風。コンセプトと API 一覧は [README.ja.md](README.ja.md) を参照。

## 運用ルール

- main へ直コミットし、作業の区切りごとに即 push する（ブランチ運用はしない）
- コミットメッセージ・コード内コメント・docstring は日本語
- PDF 処理と無関係な実験ファイルはこのリポジトリに置かない

## 開発コマンド

- `uv sync` — ビルド + 依存インストール（Rust 変更も cache-keys で自動再ビルド）
- `uv run pytest` / `uv run ruff check .` / `uv run mypy src tests`
- `uv sync --group bench && uv run python bench/run.py` — 再現可能ベンチマーク
  （結果は bench/results/latest.md。勝ち負け両方掲載の方針）
- `cargo clippy --manifest-path rust/Cargo.toml --all-targets` / `cargo fmt --manifest-path rust/Cargo.toml`
- Rust の単体テストは書かない方針。挙動はすべて Python テスト（tests/）で検証する
- 実世界 PDF の回帰テストは tests/test_real_world.py。コーパスの出典・ライセンス・既知の限界は
  tests/assets/real_world/README.md に記録し、追加時も再配布可能なものだけを同梱する

## アーキテクチャと不変条件

- `_Document`（rust/src/document.rs）は型変換とエラー変換のみの薄い層。
  使い勝手（検証・0/1 始まり変換・close 管理）は Python 側 `Document`（src/pylopdf/__init__.py）が担う
- ページ番号は Python API が 0 始まり、Rust/lopdf 層が 1 始まり。変換は `_lopdf_page_number` に集約
- merge / select は「継承属性（Resources, MediaBox, CropBox, Rotate）を
  ページ辞書へ焼き込む」パターンが前提（lopdf はページ属性の継承を解決しないため）
- テキスト抽出は hayro Device 実装（rust/src/extract.rs）。グリフの Unicode + 位置を
  収集し、行（LINE_TOLERANCE）→ 語（WORD_GAP）→ ブロック（BLOCK_GAP）へ組み立てる。
  get_text("words"/"blocks"/"dict") と search_for も同じグリフ収集の上に載る。
  CJK 代替フォント設定は抽出にも反映され、不可視テキスト（OCR レイヤー）も対象。
  注意: hayro のグリフ空間は 1000 upem 正規化のため、フォントサイズは変換係数 × 1000。
  bbox の縦方向はベースライン ± サイズ比の近似。抽出座標はレンダリングと同じ表示空間
  （Context にレンダラと同じ initial_transform(true) を渡す = ページ回転・CropBox
  オフセット解決済み）。縦書きの読み順は未対応
- レンダリングは save_bytes → hayro 再パースの結果を `_Document.hayro_pdf` にキャッシュし、
  編集メソッドが `invalidate_hayro_pdf` で破棄する。「編集後の状態が常に反映される」が
  不変条件（編集系メソッドを足すときは必ず invalidate を呼ぶこと）
- 重い処理（load / save / render / 抽出 / merge / 圧縮）は `Python::detach` で GIL を解放している
- `Page` は Document への軽量ビュー + 世代番号（`_generation`）。ページ構造を変える
  Python メソッドを足したら必ず `_bump_generation()` を呼ぶ（忘れると古い Page が
  黙って別のページを指す）。構造変更後の古い Page は StalePageError
- 例外は Rust 定義の `PdfError`（ValueError 互換の基底）/ `PasswordError` と、
  Python 定義の `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError`。
  新しいエラーは PdfError 系に載せる（素の ValueError を増やさない）
- 暗号化保存（save の user_pw/owner_pw）は clone に対して encrypt するため、
  メモリ上のドキュメントは常に平文。鍵は Python 側 os.urandom(32) で生成する
- TOC（get_toc/set_toc）のページ番号だけは pymupdf 互換の 1 始まり（他 API は 0 始まり）
- 暗号化 PDF: user password 空は lopdf がロード時に自動復号。それ以外は password 引数か
  authenticate()（内部はパスワード付き開き直し）。未復号のまま操作すると 0 ページに
  見えるため、_ensure_open が is_encrypted を検査して明確なエラーにする
- CJK fallback: hayro の font_resolver を差し替え（rust/src/document.rs の
  pick_cjk_fallback）。CIDSystemInfo か BaseFont 名で CJK 判定し、明朝系名は serif、
  それ以外は sans スロットを使う。フォント実体は fonts/pylopdf-fonts-cjk/
  （uv workspace メンバー、[cjk] extra、レンダリング時に自動検出）
- 描き込み（rust/src/draw.rs）: 既存コンテンツは再エンコードせず /Contents への
  ストリーム追記のみ（既存列は一度だけ q/Q で挟む）。座標は表示空間（左上原点・
  回転考慮）で受けて cm/Tm に変換する。注釈は AP /N を必ず生成する
  （hayro は AP の無い注釈を描画しないため。render_annotations は既定 true）
- メタデータ文字列は ASCII 以外を UTF-16BE（BOM 付き）でエンコードする
- wheel は abi3-py310 の単一ビルド（Python 3.10–3.14）。サイズを増やす依存追加は慎重に（現在約 3.5MB）
- hayro の警告は interpreter_settings の sink が pending_warnings に集め、
  Python 側 _emit_warnings が PylopdfWarning として発行する（操作ごとにドレイン）
- buffer protocol は abi3-py310 では使えない（Py_buffer の安定 ABI 入りは 3.11 から）。
  Pixmap.samples は 1 コピーの bytes

## 既知の罠

- lopdf の `time` feature は 0.43.0 で入った `From<time::Time>` impl が最初から
  コンパイル不能（上流 #527 で修正済み・未リリース）→ `chrono` に固定している（rust/Cargo.toml）
- lopdf の content パーサは「コメント行 + 直後のインデント行」で以降の全演算を落とす
  （lopdf#535 として報告済み）。pylopdf は v0.7 で抽出を hayro エンジン
  （rust/src/extract.rs）へ置き換えたため影響を受けない
- classifier の実在チェックは pre-commit の validate-pyproject（trove-classifiers 付き）が担う
  （v0.4.0 は無効 classifier `Topic :: Text Processing :: Markup :: PDF` で PyPI に拒否された実績）
  ※ validate-pyproject-schema-store は UnboundLocalError を起こすため入れない
- バージョンは 3 箇所を手動同期: pyproject.toml / rust/Cargo.toml / src/pylopdf/`__init__.py`
- リリース CI の macOS x86_64 は arm64 ランナーでクロスビルドする（Intel ランナーはキュー待ちが長い）

## リリース手順

1. バージョン 3 箇所を上げ、CHANGELOG.md に追記してコミット & push
2. `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`
3. GitHub Actions（release.yml）が 5 プラットフォームの wheel + sdist をビルドし、
   PyPI Trusted Publishing で自動公開する（PyPI 側設定は登録済み）

フォント wheel（pylopdf-fonts-cjk）は別リリース: fonts/pylopdf-fonts-cjk/pyproject.toml の
バージョンを上げて `fonts-vX.Y.Z` タグを push すると release-fonts.yml が公開する。
※初回は PyPI 側で pylopdf-fonts-cjk の Trusted Publisher 登録が必要
（workflow: release-fonts.yml / environment: pypi）。本体の [cjk] extra が参照するため、
本体リリースより先にフォント wheel を公開すること

## ロードマップ

中期計画の正本は [ROADMAP.md](ROADMAP.md)（2026-07-22 の市場・upstream 調査と
2026-07-23 のスコープ外領域深掘り再調査に基づく。戦略・リリース計画 v0.6〜v1.0・
エコシステム連携・ウォッチリスト・スコープ外の宣言を含む）。

- 現在のフェーズ: v0.9.0 リリース済み（2026-07-23、PyPI 公開・E2E 検証済み。
  OCR 不可視層・to_markdown・AcroForm 記入・添付・ページラベル・PDF/A 宣言読み取り。
  インクリメンタル保存は OSS 分析で見送り → ウォッチリスト）。
  次: v1.0「信頼の宣言」準備（SECURITY / 監査 CI・公開ベンチマーク・
  ドキュメントサイト・cp314t wheel）と v0.10 候補 [ocr] の精度実測
- lopdf#535（コメント + インデント行で抽出が空になる）は v0.7 の hayro エンジン
  置き換えで pylopdf 側は解消済み（上流の修正 PR 自作は並走候補として残る）
- 完了済みの履歴は CHANGELOG.md を参照
