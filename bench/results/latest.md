# pylopdf ベンチマーク結果

- 実行日時: 2026-07-23 04:47 UTC
- 環境: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- バージョン: pylopdf 0.9.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- 反復: 各タスク ウォームアップ 1 回 + 5 回の中央値（ミリ秒。小さいほど速い）
- コーパス: tests/assets/real_world（出典・ライセンスは同ディレクトリの README）
- 再現方法: `uv sync --all-extras --group bench && uv run python bench/run.py`

## テキスト抽出（全ページ、ms）

| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 131.6 | 150.7 | 631.4 | 8652.7 |
| f1040.pdf | 16.0 | 32.9 | 155.6 | 506.2 |
| mhlw-doc.pdf | 11.8 | 10.3 | 84.2 | 175.7 |
| patent-us223898.pdf | 26.3 | 6.0 | 83.4 | 390.2 |
| pdf20-simple.pdf | 0.3 | 0.8 | 1.2 | 1.9 |
| usrguide.pdf | 108.2 | 42.7 | 579.3 | 1673.5 |
| wdl6812-manuscript.pdf | 0.4 | 1.0 | 1.4 | 2.6 |

## 抽出内容の突き合わせ（正確さの代理指標）

| ファイル | pylopdf 文字数 | pymupdf 文字数 | 類似度（空白正規化後） |
|---|---|---|---|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10158 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

類似度の読み方: 1.0 に近いほど pymupdf と同じテキストが取れている。
低い行はフォーム（f1040）やスキャン + OCR 層（patent）で、文字数がほぼ同数の
まま読み順・空白の流儀が違うことを示す（どちらが正とは言えない）。
0 文字の行は画像のみでテキスト層が無い PDF（0 はどちらも正しい）。

## 結合（コーパス全ファイルを 1 文書へ、ms）

| タスク | pylopdf | pymupdf | pypdf |
|---|---|---|---|
| merge x7 | 30.1 | 122.2 | 325.3 |

## レンダリング（1 ページ目 → 2x PNG、ms）

| ファイル | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 40.8 | 84.0 |
| f1040.pdf | 49.9 | 92.1 |
| mhlw-doc.pdf | 33.8 | 68.7 |
| patent-us223898.pdf | 34.7 | 64.1 |
| pdf20-simple.pdf | 9.0 | 18.9 |
| usrguide.pdf | 30.7 | 54.6 |
| wdl6812-manuscript.pdf | 43.4 | 83.8 |

速い・遅いの両方をそのまま掲載する方針です。数値は環境依存のため、
引用時は必ず上記の環境情報とセットで扱ってください。
