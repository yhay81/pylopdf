---
title: ベンチマーク
description: 抽出・結合・レンダリングの再現可能なベンチマーク。速い結果も遅い結果も同時に公開します。
---

# ベンチマーク

pylopdfは**速い結果も遅い結果も同時に公開**します。以下は、ひとつの環境と
コーパスで得たスナップショットです。普遍的な順位ではなく、自分のワークロードで
何を測るべきか判断する材料として使ってください。

!!! info "最新の実行"
    **2026-07-26 08:18 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.11.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    ウォームアップ1回 + 計測5回。表は中央値（ミリ秒）です。

## 概要 { #overview }

| ワークロード | 最新コーパスで分かったこと |
|---|---|
| 実世界PDF 10件を結合 | pylopdf **36.6 ms**、pymupdf 131.8 ms、pypdf 426.3 ms |
| 先頭ページを2倍で描画 | 10ファイルすべてでpylopdfが高速 |
| 12ページを2倍で描画 | `render_pages()`は317.4 ms（1 worker）から81.5 ms（8 workers）へ短縮し、**3.89倍高速化** |
| 全ページのテキスト抽出 | 4ファイルでpylopdf、6ファイルでpymupdfが高速 |
| 抽出精度の代理指標 | 読み順の流儀により類似度0.121〜1.000 |

## テキスト抽出 { #text-extraction }

全ページ、単位ms。小さいほど高速です。

| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 191.6 | **183.2** | 689.7 | 9997.1 |
| bunka-kokugo-series-019-p4.pdf | 1.9 | **0.5** | 1.0 | 1.8 |
| f1040.pdf | **26.7** | 66.9 | 230.3 | 704.7 |
| mhlw-doc.pdf | 18.4 | **11.5** | 114.3 | 263.3 |
| nics-background-checks-2015-11.pdf | 16.1 | **10.8** | 177.5 | 524.7 |
| patent-us223898.pdf | 33.3 | **6.3** | 79.2 | 493.7 |
| pdf20-simple.pdf | **0.3** | 1.2 | 1.7 | 2.4 |
| senate-expenditures.pdf | **6.6** | 7.2 | 132.2 | 374.1 |
| usrguide.pdf | 163.0 | **54.5** | 673.6 | 2050.7 |
| wdl6812-manuscript.pdf | **0.3** | 0.8 | 1.3 | 2.3 |

## 抽出内容 { #extraction-content }

これは正解率ではなく代理指標です。空白を正規化してpymupdfと比較しています。
フォームやOCR層で類似度が低くても、文字数がほぼ同じなら読み順・空白の方針だけが
違う場合があります。

| ファイル | pylopdf文字数 | pymupdf文字数 | 類似度 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.121 |
| patent-us223898.pdf | 11218 | 11218 | 0.320 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| senate-expenditures.pdf | 4516 | 4516 | 0.443 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 結合 { #merge }

| タスク | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| コーパス10件をすべて結合 | **36.6** | 131.8 | 426.3 |

## レンダリング { #rendering }

先頭ページを2倍PNGへ描画。単位ms、小さいほど高速です。

| ファイル | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **41.1** | 88.9 |
| bunka-kokugo-series-019-p4.pdf | **48.2** | 110.5 |
| f1040.pdf | **49.5** | 97.1 |
| mhlw-doc.pdf | **35.5** | 71.2 |
| nics-background-checks-2015-11.pdf | **54.2** | 72.6 |
| patent-us223898.pdf | **32.3** | 68.8 |
| pdf20-simple.pdf | **8.0** | 19.9 |
| senate-expenditures.pdf | **55.2** | 56.8 |
| usrguide.pdf | **28.3** | 54.9 |
| wdl6812-manuscript.pdf | **42.9** | 83.3 |

## 並列レンダリング { #parallel-rendering }

`usrguide.pdf`の先頭12ページを2倍PNGへ描画。単位ms、小さいほど高速です。
バッチは入力順を保持し、単一の不変ドキュメントスナップショットを使います。

| Workers | 時間 | 1 worker比 |
|---:|---:|---:|
| 1 | 317.4 | 1.00倍 |
| 2 | 179.6 | 1.77倍 |
| 4 | 99.1 | 3.20倍 |
| 8 | 81.5 | 3.89倍 |

実際の並列度は、指定worker数と推定512 MBの描画作業メモリの両方で制限されます。

## free-threadedでの抽出 { #free-threaded-extraction }

Windows 11のfree-threaded CPython 3.14.6で、独立した`bill-hr815.pdf` 2部の
全ページテキストを抽出しました。1回warmup後、先行モードを交互にした7組の実行の
中央値です。

| モード | Workers | 時間 | 高速化 |
|---|---:|---:|---:|
| 逐次 | 1 | 280.3 ms | 1.00倍 |
| 並行 | 2 | 160.8 ms | 1.74倍 |

すべての実行で2部の出力は完全に一致し、import後もGILが無効であることを
インタープリタから確認しています。

## 再現する { #reproduce }

コーパスは`tests/assets/real_world`にあり、出典とライセンスも同じ場所へ記録しています。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
uv run python tools/pyodide_compat.py --root . --benchmark-only \
  --benchmark-output .tmp/limits-benchmark.json
# free-threaded CPython 3.14インタープリタで:
python3.14t bench/free_threaded.py
```

生成元レポートは
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)
へコミットされています。native／Pyodideのresource-policy基準値は
[`bench/results/limits-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/limits-latest.md)
へ分けてコミットされています。2番目のcommandは上限付きopen／extractと制御された拒否を
測定します。CIは同じcaseをPyodide内でも実行し、Wasm linear memoryの増加を
記録します。この時間とmemory値は傾向であり、native／Wasmの性能比較ではありません。
数値を引用するときは、環境とコーパスを併記してください。
