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
- `cargo clippy --manifest-path rust/Cargo.toml --all-targets` / `cargo fmt --manifest-path rust/Cargo.toml`
- Rust の単体テストは書かない方針。挙動はすべて Python テスト（tests/）で検証する
- 実世界 PDF の回帰テストは tests/test_real_world.py。コーパスの出典・ライセンス・既知の限界は
  tests/assets/real_world/README.md に記録し、追加時も再配布可能なものだけを同梱する

## アーキテクチャと不変条件

- `_Document`（rust/src/document.rs）は型変換とエラー変換のみの薄い層。
  使い勝手（検証・0/1 始まり変換・close 管理）は Python 側 `Document`（src/pylopdf/__init__.py）が担う
- ページ番号は Python API が 0 始まり、Rust/lopdf 層が 1 始まり。変換は `_lopdf_page_number` に集約
- merge / select / extract_text は「継承属性（Resources, MediaBox, CropBox, Rotate）を
  ページ辞書へ焼き込む」パターンが前提（lopdf はページ属性の継承を解決しないため）
- レンダリングは save_bytes → hayro で再パースする方式。編集後の状態が常に反映される
- 暗号化 PDF: user password 空は lopdf がロード時に自動復号。それ以外は password 引数か
  authenticate()（内部はパスワード付き開き直し）。未復号のまま操作すると 0 ページに
  見えるため、_ensure_open が is_encrypted を検査して明確なエラーにする
- CJK fallback: hayro の font_resolver を差し替え（rust/src/document.rs の
  pick_cjk_fallback）。CIDSystemInfo か BaseFont 名で CJK 判定し、明朝系名は serif、
  それ以外は sans スロットを使う。フォント実体は fonts/pylopdf-fonts-cjk/
  （uv workspace メンバー、[cjk] extra、レンダリング時に自動検出）
- メタデータ文字列は ASCII 以外を UTF-16BE（BOM 付き）でエンコードする
- wheel は abi3-py310 の単一ビルド（Python 3.10–3.14）。サイズを増やす依存追加は慎重に（現在約 3.5MB）

## 既知の罠

- lopdf の `time` feature は 0.43.0 で入った `From<time::Time>` impl が最初から
  コンパイル不能（上流 #527 で修正済み・未リリース）→ `chrono` に固定している（rust/Cargo.toml）
- lopdf の content パーサは「コメント行 + 直後のインデント行」で以降の全演算を落とし
  テキスト抽出が空になる（レンダリングは正常。lopdf#535 として報告済み、
  tests/test_real_world.py の xfail で追跡）
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

## ロードマップ（2026-07 時点）

1. lopdf#535（コメント + インデント行で抽出が空になる）の修正リリースを待って
   xfail の解消を確認する
2. 検討中の候補: get_toc() の露出、README 比較表への暗号化/CJK 行の追加、
   CI への Python 3.10 ジョブ、pymupdf/pypdf との簡易ベンチマーク

完了済み（2026-07-22）: GitHub Release ノート + README バッジ、実世界 PDF の
回帰テストスイート（スキャン軸は CCITT+OCR / DCT+JBIG2+テキスト無しまでカバー）、
暗号化 PDF の読み取り対応、CJK フォント fallback（pylopdf[cjk]）、
v0.5.0 リリース（pylopdf 0.5.0 + pylopdf-fonts-cjk 0.1.0 を PyPI 公開、E2E 検証済み）、
lopdf#535 の upstream 報告
