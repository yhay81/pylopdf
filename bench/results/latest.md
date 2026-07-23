# pylopdf ベンチマーク結果

- 実行日時: 2026-07-23 03:47 UTC
- 環境: Windows-11-10.0.26200-SP0 / Python 3.14.6 / CPU AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD
- バージョン: pylopdf 0.9.0, pymupdf 1.28.0, pypdf 6.14.2, pdfplumber 0.11.10
- 反復: 各タスク ウォームアップ 1 回 + 5 回の中央値（ミリ秒。小さいほど速い）
- コーパス: tests/assets/real_world（出典・ライセンスは同ディレクトリの README）
- 再現方法: `uv sync --all-extras --group bench && uv run python bench/run.py`

## テキスト抽出（全ページ、ms）

| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---|---|---|---|
| bill-hr815.pdf | 123.6 | 161.8 | 651.7 | 9132.5 |
| f1040.pdf | 17.0 | 33.3 | 180.5 | 588.7 |
| mhlw-doc.pdf | 12.8 | 11.3 | 84.2 | 180.7 |
| patent-us223898.pdf | 25.0 | 7.1 | 84.5 | 413.8 |
| pdf20-simple.pdf | 0.3 | 0.8 | 1.2 | 2.1 |
| usrguide.pdf | 111.0 | 45.2 | 655.2 | 1745.2 |
| wdl6812-manuscript.pdf | 0.4 | 0.8 | 1.6 | 2.3 |

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
| merge x7 | 31.8 | 131.8 | 358.7 |

## レンダリング（1 ページ目 → 2x PNG、ms）

| ファイル | pylopdf | pymupdf |
|---|---|---|
| bill-hr815.pdf | 103.4 | 85.9 |
| f1040.pdf | 120.3 | 96.6 |
| mhlw-doc.pdf | 106.2 | 74.1 |
| patent-us223898.pdf | 63.1 | 66.2 |
| pdf20-simple.pdf | 17.1 | 19.7 |
| usrguide.pdf | 70.3 | 56.0 |
| wdl6812-manuscript.pdf | 240.6 | 91.3 |

速い・遅いの両方をそのまま掲載する方針です。数値は環境依存のため、
引用時は必ず上記の環境情報とセットで扱ってください。
