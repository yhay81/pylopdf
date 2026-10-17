---
title: ベンチマーク
description: 抽出・結合・ラスタライズ・PNGエンコードの再現可能なベンチマーク。速い結果も遅い結果も同時に公開します。
---

# ベンチマーク

pylopdfは**速い結果も遅い結果も同時に公開**します。以下は、ひとつの環境と
コーパスで得たスナップショットです。普遍的な順位ではなく、自分のワークロードで
何を測るべきか判断する材料として使ってください。

!!! info "最新の実行"
    **2026-10-17 15:14 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro（arm64）<br>
    thin LTOのrelease profileでビルドしたpylopdf 0.14.0 · pymupdf 1.28.0 · pypdf 6.15.0 · pdfplumber 0.11.10<br>
    ウォームアップ1回 + 計測7回。表は中央値（ミリ秒）です。

## 概要 { #overview }

| ワークロード | 最新コーパスで分かったこと |
|---|---|
| 実世界PDF 14件を結合 | pylopdf **32.8 ms**、pymupdf 116.2 ms、pypdf 145.4 ms |
| 先頭ページを2倍PNGへ（一連の処理） | 14ファイルすべてでpylopdfが高速。合計は**216.1 ms**対591.3 ms |
| ラスタライズのみ | 14ファイル中、coldでは9件、warmでは13件でpymupdfが高速。warmの合計は160.8 ms対**81.2 ms** |
| PNGエンコード | pylopdfは約9倍高速（**45.4 ms**対407.4 ms）ですが、出力は40%大きくなります（4,645 KiB対**3,318 KiB**） |
| 12ページを2倍で描画 | `render_pages()`は1 workerの165.3 msから8 workersの34.9 msへ短縮し、**4.74倍高速化**。pymupdfの逐次処理は423.1 ms |
| 全ページのテキスト抽出 | 14ファイル中13件でpylopdf、残る1件でpymupdfが高速 |
| 抽出精度の代理指標 | 両ライブラリがテキストを抽出したファイルでは類似度0.443〜1.000。Type 3のfixtureからpylopdfはテキストを抽出しません |
| LinuxとWindowsのCI runner | pylopdfの抽出のリードは小さく、MuPDFはcoldのラスタライズでも高速。[他のプラットフォーム](#other-platforms)を参照 |

## テキスト抽出 { #text-extraction }

全ページ、単位ms。小さいほど高速です。

| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **70.6** | 291.7 | 404.8 | 4531.3 |
| bunka-kokugo-series-019-p4.pdf | **0.19** | 0.25 | 0.30 | 0.47 |
| f1040.pdf | **8.8** | 30.0 | 69.4 | 223.6 |
| mhlw-doc.pdf | **7.9** | 9.2 | 36.0 | 71.2 |
| nics-background-checks-2015-11.pdf | **4.9** | 7.5 | 52.0 | 119.5 |
| patent-us223898.pdf | 16.8 | **11.3** | 40.3 | 164.8 |
| pdf20-simple.pdf | **0.21** | 0.59 | 0.56 | 0.82 |
| pdfium-jpx-lzw.pdf | **0.11** | 0.26 | 0.16 | 0.31 |
| pdfium-links-highlights-annots.pdf | **0.29** | 0.75 | 0.32 | 1.3 |
| pdfium-smask-blend.pdf | **0.10** | 0.26 | 0.19 | 0.55 |
| pdfium-type3.pdf | **0.12** | 0.27 | 0.29 | 0.50 |
| senate-expenditures.pdf | **2.6** | 6.8 | 49.4 | 110.0 |
| usrguide.pdf | **27.6** | 63.1 | 282.3 | 785.5 |
| wdl6812-manuscript.pdf | **0.21** | 0.63 | 0.57 | 0.95 |
| **合計** | **140.4** | 422.5 | 936.5 | 6010.8 |

## 抽出内容 { #extraction-content }

これは正解率ではなく代理指標です。空白を正規化してpymupdfと比較しています。
フォームや回転した表で類似度が低くても、文字数がほぼ同じなら読み順・空白の方針だけが
違う場合があります。文字数0の行は画像のみのPDFです。ただし`pdfium-type3.pdf`は例外で、
hayroがUnicode対応を報告しないglyphをpylopdfがスキップしています。

| ファイル | pylopdf文字数 | pymupdf文字数 | 類似度 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.855 |
| patent-us223898.pdf | 11219 | 11218 | 0.581 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| pdfium-jpx-lzw.pdf | 0 | 0 | 1.000 |
| pdfium-links-highlights-annots.pdf | 92 | 92 | 1.000 |
| pdfium-smask-blend.pdf | 0 | 0 | 1.000 |
| pdfium-type3.pdf | 0 | 5 | 0.000 |
| senate-expenditures.pdf | 4516 | 4516 | 0.443 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 結合 { #merge }

| タスク | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| コーパス14件をすべて結合 | **32.8** | 116.2 | 145.4 |

## レンダリング { #rendering }

先頭ページを2倍で描画。単位ms、小さいほど高速です。どちらも既定の設定を使い、
pylopdfは透明背景のstraight-alpha RGBA8、pymupdfは白背景のRGBを生成します。

### 一連の処理 { #rendering-end-to-end }

各実行で新しくドキュメントを開き、ページをラスタライズしてPNGへエンコードします。

| ファイル | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **19.5** | 58.0 |
| bunka-kokugo-series-019-p4.pdf | **21.0** | 84.9 |
| f1040.pdf | **25.2** | 67.5 |
| mhlw-doc.pdf | **18.8** | 50.4 |
| nics-background-checks-2015-11.pdf | **29.1** | 53.3 |
| patent-us223898.pdf | **15.7** | 52.2 |
| pdf20-simple.pdf | **3.4** | 13.1 |
| pdfium-jpx-lzw.pdf | **10.4** | 38.5 |
| pdfium-links-highlights-annots.pdf | **6.1** | 25.7 |
| pdfium-smask-blend.pdf | **0.92** | 3.5 |
| pdfium-type3.pdf | **0.66** | 1.9 |
| senate-expenditures.pdf | **29.6** | 39.7 |
| usrguide.pdf | **15.7** | 38.5 |
| wdl6812-manuscript.pdf | **20.2** | 64.1 |
| **合計** | **216.1** | 591.3 |

この差の大部分はラスタライズではなくPNGエンコードによるものです。次の2つの表で
同じ処理を分けて示します。

### ラスタライズ { #rasterization }

coldは繰り返しごとに新しくドキュメントを開き、warmは開いたままのドキュメントで
同じページを再びラスタライズします。MuPDFのプロセス全体のglyph cacheのように
ドキュメントより長く残るcacheは、coldでも有効です。MuPDFは開いているドキュメントの
読み込み済みフォントと描画済みglyphも保持するため、テキストの多いページで特に
差が出ます。pylopdfは現在、描画の呼び出しごとにフォントとglyph outlineを含む
hayroのrender cacheを新しく作るため、warmとcoldの時間が近くなります。

| ファイル | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 13.5 | **8.5** | 12.5 | **4.0** |
| bunka-kokugo-series-019-p4.pdf | **16.7** | 49.8 | 16.5 | **14.6** |
| f1040.pdf | **19.7** | 26.4 | 16.3 | **15.6** |
| mhlw-doc.pdf | 14.5 | **13.1** | 13.8 | **3.5** |
| nics-background-checks-2015-11.pdf | 23.1 | **7.4** | 22.6 | **5.6** |
| patent-us223898.pdf | **11.6** | 22.0 | 11.5 | **11.4** |
| pdf20-simple.pdf | 2.1 | **0.78** | 1.9 | **0.32** |
| pdfium-jpx-lzw.pdf | **8.8** | 14.9 | **8.6** | 9.7 |
| pdfium-links-highlights-annots.pdf | 4.4 | **2.1** | 4.0 | **1.2** |
| pdfium-smask-blend.pdf | 0.79 | **0.62** | 0.48 | **0.39** |
| pdfium-type3.pdf | 0.59 | **0.27** | 0.29 | **0.04** |
| senate-expenditures.pdf | 24.8 | **5.4** | 24.4 | **3.6** |
| usrguide.pdf | 12.6 | **8.1** | 11.4 | **0.71** |
| wdl6812-manuscript.pdf | **16.8** | 21.8 | 16.5 | **10.6** |
| **合計** | **169.9** | 181.1 | 160.8 | **81.2** |

### PNGエンコード { #png-encoding }

どちらのエンコーダにも描画済みのラスタを渡します。pylopdfはRGBA8出力に高速な
fdeflate圧縮を使い、ファイルサイズより速度を優先します。pymupdfはRGB出力に
zlibの既定レベルを使います。エンコード時間よりファイルサイズが重要な場合は、
pylopdfの出力を外部で再圧縮してください。

| ファイル | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.8** | 51.1 | 328.3 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **4.2** | 33.7 | 438.8 | **249.4** |
| f1040.pdf | **5.8** | 39.9 | 841.3 | **360.9** |
| mhlw-doc.pdf | **4.2** | 37.3 | 340.5 | **301.4** |
| nics-background-checks-2015-11.pdf | **6.0** | 46.1 | 737.1 | **343.0** |
| patent-us223898.pdf | **3.9** | 28.6 | 245.0 | **154.6** |
| pdf20-simple.pdf | **1.1** | 12.5 | 28.4 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.4** | 23.7 | 39.9 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.7** | 23.9 | 53.5 | **21.5** |
| pdfium-smask-blend.pdf | **0.12** | 2.8 | 4.2 | **2.6** |
| pdfium-type3.pdf | **0.11** | 1.5 | 3.5 | **1.1** |
| senate-expenditures.pdf | **4.1** | 33.7 | 332.8 | **253.8** |
| usrguide.pdf | **3.4** | 30.5 | 188.8 | **138.0** |
| wdl6812-manuscript.pdf | **3.5** | 42.0 | **1062.9** | 1191.3 |
| **合計** | **45.4** | 407.4 | 4645.1 | **3317.7** |

## 並列レンダリング { #parallel-rendering }

`usrguide.pdf`の先頭12ページを2倍PNGへ描画。単位ms、小さいほど高速です。
バッチは入力順を保持し、単一の不変ドキュメントスナップショットを使います。

| Workers | 時間 | 1 worker比 |
|---:|---:|---:|
| 1 | 165.3 | 1.00倍 |
| 2 | 89.8 | 1.84倍 |
| 4 | 48.6 | 3.40倍 |
| 8 | 34.9 | 4.74倍 |

実際の並列度は、指定worker数と推定512 MBの描画作業メモリの両方で制限されます。
参考として、pymupdfは同じ12ページを1つの新しいドキュメントで逐次描画・エンコードし、
423.1 msかかりました。

## 他のプラットフォーム { #other-platforms }

上のスナップショットは1台のmacOS arm64マシンの結果です。2026-10-17に同じ
`bench/run.py`を、thin LTOのabi3 wheelでGitHub-hosted release runnerごとに1回実行しました
（Python 3.14.8、macOS x86_64のみ3.14.7。ウォームアップ1回 + 計測5回）。各セルは
pylopdf / pymupdfのコーパス合計（ミリ秒）です。共有runnerはスナップショットよりノイズが
大きいため、小さな差はノイズとして扱ってください。

| Runner | テキスト抽出 | 結合 | 一連の処理 | ラスタライズ cold | ラスタライズ warm | PNGエンコード |
|---|---:|---:|---:|---:|---:|---:|
| Linux x86_64, AMD EPYC 9V74, 4 vCPU | **166.4** / 208.2 | **29.7** / 97.7 | **322.1** / 628.2 | 280.8 / **221.0** | 266.3 / **102.2** | **39.0** / 401.7 |
| Linux aarch64, 4 vCPU | **189.0** / 267.8 | **37.8** / 111.3 | **359.3** / 744.4 | 278.2 / **220.9** | 265.1 / **99.8** | **78.9** / 526.5 |
| macOS arm64, Apple M1 (virtual), 3 vCPU | **140.8** / 509.8 | **32.2** / 131.1 | **225.1** / 611.0 | **175.8** / 187.3 | 164.3 / **83.3** | **46.6** / 424.3 |
| macOS x86_64, Intel Core i7-8700B, 4 vCPU | **282.1** / 1307.9 | **67.8** / 398.6 | **553.4** / 823.1 | 461.9 / **351.6** | 440.1 / **156.3** | **60.8** / 473.1 |
| Windows Server 2025 x86_64, Intel, 4 vCPU | **245.0** / 277.8 | **53.8** / 130.9 | **593.0** / 909.8 | 521.5 / **307.0** | 505.3 / **160.1** | **61.8** / 599.2 |

pymupdfのテキスト抽出は、2つのmacOS runnerではLinuxやWindowsより大幅に遅くなりました。
そのためLinuxとWindowsでのpylopdfの抽出のリードは、macOSのスナップショットより小さく
なります。MuPDFのラスタライズは、warmではすべてのrunnerで、coldでもApple M1以外の
すべてのrunnerで高速でした。それでも一連の処理ではpylopdfが上回りました。pymupdfの合計の
大半をPNGエンコードが占めるためです。

## free-threadedでの抽出 { #free-threaded-extraction }

Windows 11のfree-threaded CPython 3.14.6で、独立した`bill-hr815.pdf` 2部の
全ページテキストを抽出しました。1回warmup後、先行モードを交互にした7組の実行の
中央値です。

| モード | Workers | 時間 | 高速化 |
|---|---:|---:|---:|
| 逐次 | 1 | 400.8 ms | 1.00倍 |
| 並行 | 2 | 235.5 ms | 1.70倍 |

すべての実行で2部の出力は完全に一致し、import後もGILが無効であることを
インタープリタから確認しています。

## 再現する { #reproduce }

コーパスは`tests/assets/real_world`にあり、出典とライセンスも同じ場所へ記録しています。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
uv run python tools/pyodide_compat.py --root . --benchmark-only \
  --benchmark-output .tmp/limits-benchmark.json
# Windowsのfree-threaded CPython 3.14インタープリタで:
py -3.14t bench/free_threaded.py
# POSIXで:
python3.14t bench/free_threaded.py
```

生成元レポートは
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)
へコミットされています。free-threaded専用の生成レポートは
[`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md)
へ分離され、`bench/run.py`を再実行してもCPython 3.14tの証跡を上書きしません。
native／Pyodideのresource-policy基準値は
[`bench/results/limits-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/limits-latest.md)
へ分けてコミットされています。2番目のcommandは上限付きopen／extractと制御された拒否を
測定します。CIは同じcaseをPyodide内でも実行し、Wasm linear memoryの増加を
記録します。この時間とmemory値は傾向であり、native／Wasmの性能比較ではありません。
数値を引用するときは、環境とコーパスを併記してください。
