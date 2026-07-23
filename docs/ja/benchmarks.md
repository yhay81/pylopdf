---
title: ベンチマーク
description: 抽出・結合・レンダリングの再現可能なベンチマーク。速い結果も遅い結果も同時に公開します。
---

# ベンチマーク

pylopdfは**速い結果も遅い結果も同時に公開**します。以下は、ひとつの環境と
コーパスで得たスナップショットです。普遍的な順位ではなく、自分のワークロードで
何を測るべきか判断する材料として使ってください。

!!! info "最新の実行"
    **2026-07-23 04:47 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.9.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    ウォームアップ1回 + 計測5回。表は中央値（ミリ秒）です。

## 概要 { #overview }

| ワークロード | 最新コーパスで分かったこと |
|---|---|
| 実世界PDF 7件を結合 | pylopdf **30.1 ms**、pymupdf 122.2 ms、pypdf 325.3 ms |
| 先頭ページを2倍で描画 | 7ファイルすべてでpylopdfが高速 |
| 全ページのテキスト抽出 | 4ファイルでpylopdf、3ファイルでpymupdfが高速 |
| 抽出精度の代理指標 | 読み順の流儀により類似度0.292〜1.000 |

## テキスト抽出 { #text-extraction }

全ページ、単位ms。小さいほど高速です。

| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **131.6** | 150.7 | 631.4 | 8652.7 |
| f1040.pdf | **16.0** | 32.9 | 155.6 | 506.2 |
| mhlw-doc.pdf | 11.8 | **10.3** | 84.2 | 175.7 |
| patent-us223898.pdf | 26.3 | **6.0** | 83.4 | 390.2 |
| pdf20-simple.pdf | **0.3** | 0.8 | 1.2 | 1.9 |
| usrguide.pdf | 108.2 | **42.7** | 579.3 | 1673.5 |
| wdl6812-manuscript.pdf | **0.4** | 1.0 | 1.4 | 2.6 |

## 抽出内容 { #extraction-content }

これは正解率ではなく代理指標です。空白を正規化してpymupdfと比較しています。
フォームやOCR層で類似度が低くても、文字数がほぼ同じなら読み順・空白の方針だけが
違う場合があります。

| ファイル | pylopdf文字数 | pymupdf文字数 | 類似度 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10158 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 結合 { #merge }

| タスク | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| コーパス7件をすべて結合 | **30.1** | 122.2 | 325.3 |

## レンダリング { #rendering }

先頭ページを2倍PNGへ描画。単位ms、小さいほど高速です。

| ファイル | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **40.8** | 84.0 |
| f1040.pdf | **49.9** | 92.1 |
| mhlw-doc.pdf | **33.8** | 68.7 |
| patent-us223898.pdf | **34.7** | 64.1 |
| pdf20-simple.pdf | **9.0** | 18.9 |
| usrguide.pdf | **30.7** | 54.6 |
| wdl6812-manuscript.pdf | **43.4** | 83.8 |

## 再現する { #reproduce }

コーパスは`tests/assets/real_world`にあり、出典とライセンスも同じ場所へ記録しています。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
```

生成元レポートは
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)
へコミットされています。数値を引用するときは、環境とコーパスを併記してください。
