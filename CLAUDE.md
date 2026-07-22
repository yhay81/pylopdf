# CLAUDE.md

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
- メタデータ文字列は ASCII 以外を UTF-16BE（BOM 付き）でエンコードする
- wheel は abi3-py310 の単一ビルド（Python 3.10–3.14）。サイズを増やす依存追加は慎重に（現在約 3.5MB）

## 既知の罠

- lopdf の `time`/`jiff` feature は最新 time クレートとコンパイル非互換 → `chrono` に固定している（rust/Cargo.toml）
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

## ロードマップ（2026-07 時点）

1. CJK フォントの opt-in extra（`pylopdf[cjk]`）— 非埋め込み日本語 PDF のレンダリング対応
2. 暗号化 PDF の読み取り対応（lopdf の decrypt 機能の露出）
3. 実世界 PDF コーパスの拡充（スキャン画像 PDF、非埋め込み CJK、暗号化 PDF — 詳細は
   tests/assets/real_world/README.md の「将来追加したい軸」）

完了済み: GitHub Release ノート + README バッジ、実世界 PDF の回帰テストスイート（2026-07-22）
