---
title: コントリビュートとサポート
description: 質問やバグ報告の窓口、pylopdfへのコントリビュートの方法、ライセンスと謝辞。
---

# コントリビュートとサポート

## ヘルプとバグ報告 { #support }

- **質問、バグ、機能の要望:** [GitHub](https://github.com/yhay81/pylopdf/issues/new/choose)でIssueを作成してください。先に[既存のIssue](https://github.com/yhay81/pylopdf/issues)を検索してください。
- **セキュリティの脆弱性:** 非公開で報告してください。[セキュリティ](security.md#report-a-vulnerability)を参照してください。

役に立つバグ報告には、pylopdf、Python、OSのバージョン、問題を再現する短いスクリプト、期待した結果が含まれます。問題が特定のPDFに依存する場合は、小さなPDFか、それを作るスクリプトを添付してください。PDFには個人情報、署名、隠れたメタデータが含まれていることがよくあります。共有するのは再配布を許可されたファイルだけにし、機密の内容は取り除いてください。

## コントリビュートする { #contribute }

バグ修正、pylopdfがうまく扱えない実際のPDF、ドキュメントの改善、翻訳はどれも歓迎します。大きな機能の場合は、コードを書く前に設計について合意できるよう、まずIssueを作成してください。

ソースからビルドするには、Rustのツールチェーンと[uv](https://docs.astral.sh/uv/)が必要です。

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf
uv sync
uv run pytest
```

[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md)に開発ガイドの全体があります。実行すべきチェック、テスト用PDFの追加方法、プルリクエストのレビューの進め方を説明しています。参加するすべての人には[行動規範](https://github.com/yhay81/pylopdf/blob/main/CODE_OF_CONDUCT.md)に従うことを求めます。

### ドキュメントと翻訳 { #documentation }

このサイトは[`docs/`](https://github.com/yhay81/pylopdf/tree/main/docs)のMarkdownファイルから[Zensical](https://zensical.org/)でビルドしています。各ページには、GitHubでそのソースを開く編集ボタンがあります。変更をローカルでプレビューするには:

```bash
uv sync --group docs
uv run zensical serve -f mkdocs.yml
```

英語版が基準です。日本語、簡体字中国語、韓国語の翻訳は英語版と並行して保守しており、同じページ名と節のアンカーを使うので、リンクはどの言語でも機能します。

## 計画とリリース { #plans }

- [ロードマップ](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md): 何をどの順で予定しているか
- [変更履歴](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md): 各リリースで何が変わったか
- [リリース](https://github.com/yhay81/pylopdf/releases)と[PyPI](https://pypi.org/project/pylopdf/)
- [API安定性](stability.md): v1.0の前後で何が変わりうるか

## ライセンスと謝辞 { #license }

pylopdfは[MITライセンス](https://github.com/yhay81/pylopdf/blob/main/LICENSE)で公開しています。

PDFの読み込みと編集には[lopdf](https://github.com/J-F-Liu/lopdf)、レンダリングとテキスト抽出には[hayro](https://github.com/LaurenzV/hayro)、テキストの書き込みには[krilla](https://github.com/LaurenzV/krilla)と[HarfRust](https://github.com/harfbuzz/harfrust)、OCRには[RTen](https://github.com/robertknight/rten)を使っています。ISO 32000-2が定めるPDFのすべての機能を、プログラムから完全に制御できるようにすることを目指しています。[pymupdf](https://pymupdf.readthedocs.io/)がPDFファイルでできることは、その最低限として独自のAPIでカバーします。同梱しているサードパーティのコードのライセンスは[NOTICE.md](https://github.com/yhay81/pylopdf/blob/main/NOTICE.md)に一覧があります。オプションのフォントパッケージはSIL Open Font LicenseのNotoフォントを、OCRモデルパッケージはApache License 2.0のPP-OCRv6モデルを含みます。
