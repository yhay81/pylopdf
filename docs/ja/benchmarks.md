---
title: ベンチマーク
description: 抽出・結合・レンダリングの再現可能なベンチマーク。速い結果も遅い結果も同時に公開します。
---

# ベンチマーク

pylopdfは**速い結果も遅い結果も同時に公開**します。以下は、ひとつの環境と
コーパスで得たスナップショットです。普遍的な順位ではなく、自分のワークロードで
何を測るべきか判断する材料として使ってください。

!!! info "最新の実行"
    **2026-07-24 12:59 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.9.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    ウォームアップ1回 + 計測5回。表は中央値（ミリ秒）です。

## 概要 { #overview }

| ワークロード | 最新コーパスで分かったこと |
|---|---|
| 実世界PDF 7件を結合 | pylopdf **40.6 ms**、pymupdf 127.1 ms、pypdf 366.3 ms |
| 先頭ページを2倍で描画 | 7ファイルすべてでpylopdfが高速 |
| 12ページを2倍で描画 | `render_pages()`は400.8 ms（1 worker）から83.6 ms（8 workers）へ短縮し、**4.80倍高速化** |
| 全ページのテキスト抽出 | 4ファイルでpylopdf、3ファイルでpymupdfが高速 |
| 抽出精度の代理指標 | 読み順の流儀により類似度0.292〜1.000 |

## テキスト抽出 { #text-extraction }

全ページ、単位ms。小さいほど高速です。

| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **138.1** | 179.5 | 848.4 | 9850.9 |
| f1040.pdf | **16.8** | 33.4 | 176.3 | 572.9 |
| mhlw-doc.pdf | 18.4 | **11.3** | 109.3 | 195.3 |
| patent-us223898.pdf | 29.5 | **6.8** | 81.4 | 512.9 |
| pdf20-simple.pdf | **0.3** | 1.1 | 1.8 | 2.2 |
| usrguide.pdf | 144.9 | **50.7** | 665.2 | 1756.4 |
| wdl6812-manuscript.pdf | **0.3** | 0.7 | 1.4 | 2.2 |

## 抽出内容 { #extraction-content }

これは正解率ではなく代理指標です。空白を正規化してpymupdfと比較しています。
フォームやOCR層で類似度が低くても、文字数がほぼ同じなら読み順・空白の方針だけが
違う場合があります。

| ファイル | pylopdf文字数 | pymupdf文字数 | 類似度 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 結合 { #merge }

| タスク | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| コーパス7件をすべて結合 | **40.6** | 127.1 | 366.3 |

## レンダリング { #rendering }

先頭ページを2倍PNGへ描画。単位ms、小さいほど高速です。

| ファイル | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **38.2** | 86.2 |
| f1040.pdf | **53.1** | 94.7 |
| mhlw-doc.pdf | **35.7** | 70.1 |
| patent-us223898.pdf | **36.4** | 69.0 |
| pdf20-simple.pdf | **9.0** | 18.9 |
| usrguide.pdf | **31.8** | 56.6 |
| wdl6812-manuscript.pdf | **45.5** | 87.0 |

## 並列レンダリング { #parallel-rendering }

`usrguide.pdf`の先頭12ページを2倍PNGへ描画。単位ms、小さいほど高速です。
バッチは入力順を保持し、単一の不変ドキュメントスナップショットを使います。

| Workers | 時間 | 1 worker比 |
|---:|---:|---:|
| 1 | 400.8 | 1.00倍 |
| 2 | 200.5 | 2.00倍 |
| 4 | 118.5 | 3.38倍 |
| 8 | 83.6 | 4.80倍 |

実際の並列度は、指定worker数と推定512 MBの描画作業メモリの両方で制限されます。

## free-threadedでの抽出 { #free-threaded-extraction }

Windows 11のfree-threaded CPython 3.14.6で、独立した`bill-hr815.pdf` 2部の
全ページテキストを抽出しました。1回warmup後、先行モードを交互にした7組の実行の
中央値です。

| モード | Workers | 時間 | 高速化 |
|---|---:|---:|---:|
| 逐次 | 1 | 341.8 ms | 1.00倍 |
| 並行 | 2 | 195.9 ms | 1.74倍 |

すべての実行で2部の出力は完全に一致し、import後もGILが無効であることを
インタープリタから確認しています。

## 再現する { #reproduce }

コーパスは`tests/assets/real_world`にあり、出典とライセンスも同じ場所へ記録しています。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
# free-threaded CPython 3.14インタープリタで:
python3.14t bench/free_threaded.py
```

生成元レポートは
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)
へコミットされています。数値を引用するときは、環境とコーパスを併記してください。
