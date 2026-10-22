---
title: ベンチマーク
description: 抽出・結合・ラスタライズ・PNGエンコードの再現可能なベンチマーク。速い結果も遅い結果も同時に公開します。
---

# ベンチマーク

このベンチマークでは、14件の実際のPDFでpylopdfをpymupdf、pypdf、pdfplumberと比較し、pylopdfが速いケースだけでなく遅いケースも示します。1台のマシンと1つのコーパスでの結果なので、順位としてではなく、自分の文書で何を測るべきかの目安として扱ってください。

!!! info "最新の実行"
    **2026-10-17 17:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro（arm64）<br>
    thin LTOのrelease profileでビルドしたpylopdf 0.14.0 · pymupdf 1.28.0 · pypdf 6.15.0 · pdfplumber 0.11.10<br>ウォームアップ1回 + 計測7回。表は中央値（ミリ秒）です。

## 概要 { #overview }

| ワークロード | 最新コーパスで分かったこと |
|---|---|
| 実世界PDF 14件を結合 | pylopdf **15.4 ms**、pymupdf 111.2 ms、pypdf 141.5 ms |
| 先頭ページを2倍PNGへ（一連の処理） | 14ファイルすべてでpylopdfが高速。合計は**212.9 ms**対573.9 ms |
| ラスタライズのみ | 14ファイル中、coldでは9件、warmでは12件でpymupdfが高速（warmの1件は同値）。warmの合計はpylopdf 145.9 ms、pymupdf **78.8 ms** |
| PNGエンコード | pylopdfは約9倍高速（**44.4 ms**対397.4 ms）ですが、出力は40%大きくなります（4,645 KiB対**3,318 KiB**） |
| 12ページを2倍で描画 | `doc.render()`は1 workerの163.7 msから8 workersの34.6 msへ短縮し、**4.74倍高速化**。pymupdfの逐次処理は425.3 ms |
| 全ページのテキスト抽出 | 14ファイル中12件でpylopdf、残る2件ではpymupdfとpypdfが1件ずつ高速 |
| 抽出精度の代理指標 | 両ライブラリがテキストを抽出したファイルでは類似度0.443〜1.000。Type 3のfixtureからpylopdfはテキストを抽出しません |
| LinuxとWindowsのCI runner | pylopdfの抽出のリードは小さく、MuPDFはcoldのラスタライズでも高速。[他のプラットフォーム](#other-platforms)を参照 |

## テキスト抽出 { #text-extraction }

全ページ、単位ms。小さいほど高速です。

| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **71.3** | 298.5 | 407.8 | 4295.0 |
| bunka-kokugo-series-019-p4.pdf | **0.17** | 0.25 | 0.30 | 0.45 |
| f1040.pdf | **8.9** | 28.2 | 68.6 | 201.8 |
| mhlw-doc.pdf | **8.0** | 9.1 | 34.0 | 70.1 |
| nics-background-checks-2015-11.pdf | **4.8** | 7.5 | 51.4 | 119.7 |
| patent-us223898.pdf | 16.9 | **11.3** | 39.6 | 165.3 |
| pdf20-simple.pdf | **0.17** | 0.62 | 0.54 | 0.80 |
| pdfium-jpx-lzw.pdf | **0.14** | 0.26 | 0.16 | 0.31 |
| pdfium-links-highlights-annots.pdf | 0.33 | 0.69 | **0.32** | 1.4 |
| pdfium-smask-blend.pdf | **0.07** | 0.27 | 0.18 | 0.51 |
| pdfium-type3.pdf | **0.08** | 0.29 | 0.29 | 0.48 |
| senate-expenditures.pdf | **2.8** | 6.6 | 49.9 | 110.3 |
| usrguide.pdf | **27.4** | 64.5 | 279.1 | 799.3 |
| wdl6812-manuscript.pdf | **0.15** | 0.58 | 0.53 | 0.93 |
| **合計** | **141.2** | 428.6 | 932.6 | 5766.4 |

## 抽出内容 { #extraction-content }

これは正解率ではなく代理指標です。空白を正規化してpymupdfと比較しています。フォームや回転した表で類似度が低くても、文字数がほぼ同じなら読み順・空白の方針だけが違う場合があります。文字数0の行は画像のみのPDFです。ただし`pdfium-type3.pdf`は例外で、hayroがUnicode対応を報告しないglyphをpylopdfがスキップしています。

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
| コーパス14件をすべて結合 | **15.4** | 111.2 | 141.5 |

## レンダリング { #rendering }

先頭ページを2倍で描画。単位ms、小さいほど高速です。どちらも計測したリリースの既定の設定を使い、pylopdf 0.14.0は透明背景のstraight-alpha RGBA8、pymupdfは白背景のRGBを生成しました。現在のpylopdfは、pymupdfと同じく白背景のRGBが既定です。

### 一連の処理 { #rendering-end-to-end }

各実行で新しくドキュメントを開き、ページをラスタライズしてPNGへエンコードします。

| ファイル | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **19.4** | 57.0 |
| bunka-kokugo-series-019-p4.pdf | **19.6** | 82.9 |
| f1040.pdf | **25.7** | 65.3 |
| mhlw-doc.pdf | **18.4** | 48.5 |
| nics-background-checks-2015-11.pdf | **28.8** | 50.6 |
| patent-us223898.pdf | **15.4** | 50.4 |
| pdf20-simple.pdf | **3.3** | 13.4 |
| pdfium-jpx-lzw.pdf | **10.0** | 37.3 |
| pdfium-links-highlights-annots.pdf | **6.0** | 26.1 |
| pdfium-smask-blend.pdf | **0.77** | 3.2 |
| pdfium-type3.pdf | **0.55** | 1.7 |
| senate-expenditures.pdf | **28.9** | 38.3 |
| usrguide.pdf | **15.9** | 37.0 |
| wdl6812-manuscript.pdf | **20.1** | 62.3 |
| **合計** | **212.9** | 573.9 |

この差の大部分はラスタライズではなくPNGエンコードによるものです。次の2つの表で同じ処理を分けて示します。

### ラスタライズ { #rasterization }

coldは繰り返しごとに新しくドキュメントを開き、warmは開いたままのドキュメントで同じページを再びラスタライズします。MuPDFのプロセス全体のglyph cacheのようにドキュメントより長く残るcacheは、coldでも有効です。MuPDFは開いているドキュメントの読み込み済みフォントと描画済みglyphも保持するため、テキストの多いページで特に差が出ます。pylopdfはドキュメントを開いたスレッドで解析済みのフォントとglyph outlineを再利用しますが、glyphのラスタライズと画像のデコードは毎回やり直すため、warmでの短縮幅はMuPDFよりはるかに小さくなります。

| ファイル | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 12.9 | **8.3** | 11.5 | **4.0** |
| bunka-kokugo-series-019-p4.pdf | **15.2** | 49.6 | **14.4** | **14.4** |
| f1040.pdf | **19.3** | 26.1 | 15.8 | **15.6** |
| mhlw-doc.pdf | 14.0 | **12.5** | 9.8 | **3.3** |
| nics-background-checks-2015-11.pdf | 22.9 | **7.3** | 21.5 | **4.8** |
| patent-us223898.pdf | **11.3** | 21.9 | 11.0 | **10.9** |
| pdf20-simple.pdf | 2.1 | **0.71** | 2.0 | **0.26** |
| pdfium-jpx-lzw.pdf | **8.5** | 14.2 | **8.1** | 9.5 |
| pdfium-links-highlights-annots.pdf | 4.3 | **2.0** | 3.9 | **1.2** |
| pdfium-smask-blend.pdf | 0.60 | **0.57** | 0.47 | **0.36** |
| pdfium-type3.pdf | 0.42 | **0.25** | 0.26 | **0.03** |
| senate-expenditures.pdf | 24.4 | **5.0** | 23.1 | **3.3** |
| usrguide.pdf | 12.5 | **7.4** | 8.0 | **0.67** |
| wdl6812-manuscript.pdf | **16.4** | 21.1 | 16.2 | **10.7** |
| **合計** | **164.7** | 177.1 | 145.9 | **78.8** |

### PNGエンコード { #png-encoding }

どちらのエンコーダにも描画済みのラスタを渡します。pylopdfはRGBA8出力に高速なfdeflate圧縮を使い、ファイルサイズより速度を優先します。pymupdfはRGB出力にzlibの既定レベルを使います。エンコード時間よりファイルサイズが重要な場合は、pylopdfの出力を外部で再圧縮してください。

| ファイル | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.7** | 49.2 | 328.3 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **4.0** | 33.1 | 438.8 | **249.4** |
| f1040.pdf | **5.8** | 39.5 | 841.3 | **360.9** |
| mhlw-doc.pdf | **4.1** | 36.2 | 340.5 | **301.4** |
| nics-background-checks-2015-11.pdf | **5.8** | 43.0 | 737.1 | **343.0** |
| patent-us223898.pdf | **3.9** | 27.7 | 245.0 | **154.6** |
| pdf20-simple.pdf | **1.1** | 12.0 | 28.4 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.3** | 23.1 | 39.9 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.6** | 23.8 | 53.5 | **21.5** |
| pdfium-smask-blend.pdf | **0.12** | 2.6 | 4.2 | **2.6** |
| pdfium-type3.pdf | **0.10** | 1.4 | 3.5 | **1.1** |
| senate-expenditures.pdf | **4.0** | 33.1 | 332.8 | **253.8** |
| usrguide.pdf | **3.3** | 29.5 | 188.8 | **138.0** |
| wdl6812-manuscript.pdf | **3.6** | 43.4 | **1062.9** | 1191.3 |
| **合計** | **44.4** | 397.4 | 4645.1 | **3317.7** |

## 並列レンダリング { #parallel-rendering }

`usrguide.pdf`の先頭12ページを2倍PNGへ描画。単位ms、小さいほど高速です。バッチは入力順を保持し、単一の不変ドキュメントスナップショットを使います。

| Workers | 時間 | 1 worker比 |
|---:|---:|---:|
| 1 | 163.7 | 1.00倍 |
| 2 | 88.4 | 1.85倍 |
| 4 | 47.4 | 3.45倍 |
| 8 | 34.6 | 4.74倍 |

実際の並列度は、指定worker数と推定512 MBの描画作業メモリの両方で制限されます。参考として、pymupdfは同じ12ページを1つの新しいドキュメントで逐次描画・エンコードし、425.3 msかかりました。

## 他のプラットフォーム { #other-platforms }

上のスナップショットは1台のmacOS arm64マシンの結果です。2026-10-17に同じ`bench/run.py`を、thin LTOのabi3 wheelでGitHub-hosted release runnerごとに1回実行しました（Python 3.14.8、macOS x86_64のみ3.14.7。ウォームアップ1回 + 計測5回）。各セルはpylopdf / pymupdfのコーパス合計（ミリ秒）です。共有runnerはスナップショットよりノイズが大きいため、小さな差はノイズとして扱ってください。

| Runner | テキスト抽出 | 結合 | 一連の処理 | ラスタライズ cold | ラスタライズ warm | PNGエンコード |
|---|---:|---:|---:|---:|---:|---:|
| Linux x86_64, AMD EPYC 9V45, 4 vCPU | **119.7** / 146.8 | **14.3** / 73.8 | **234.8** / 459.9 | 198.1 / **167.1** | 182.9 / **72.9** | **33.4** / 284.7 |
| Linux aarch64, 4 vCPU | **186.0** / 270.3 | **27.5** / 111.7 | **372.1** / 720.7 | 289.0 / **226.1** | 264.2 / **101.3** | **81.4** / 505.7 |
| macOS arm64, Apple M1 (virtual), 3 vCPU | **171.3** / 601.1 | **22.4** / 179.7 | **290.4** / 772.1 | **220.2** / 251.7 | 204.3 / **110.3** | **57.9** / 539.1 |
| macOS x86_64, Intel Core i7-8700B, 4 vCPU | **254.7** / 1188.0 | **87.0** / 430.4 | **651.3** / 922.2 | 535.8 / **392.0** | 444.7 / **178.6** | **62.2** / 490.5 |
| Windows Server 2025 x86_64, Intel, 4 vCPU | **243.1** / 269.7 | **37.5** / 132.9 | **533.2** / 874.6 | 470.5 / **293.7** | 434.2 / **147.5** | **57.8** / 579.6 |

pymupdfのテキスト抽出は、2つのmacOS runnerではLinuxやWindowsより大幅に遅くなりました。そのためLinuxとWindowsでのpylopdfの抽出のリードは、macOSのスナップショットより小さくなります。MuPDFのラスタライズは、warmではすべてのrunnerで、coldでもApple M1以外のすべてのrunnerで高速でした。それでも一連の処理ではpylopdfが上回りました。pymupdfの合計の大半をPNGエンコードが占めるためです。

## フリースレッド版での抽出 { #free-threaded-extraction }

Windows 11のフリースレッド版CPython 3.14.6で、独立した`bill-hr815.pdf` 2部の全ページテキストを抽出しました。1回warmup後、先行モードを交互にした7組の実行の中央値です。

| モード | Workers | 時間 | 高速化 |
|---|---:|---:|---:|
| 逐次 | 1 | 400.8 ms | 1.00倍 |
| 並行 | 2 | 235.5 ms | 1.70倍 |

すべての実行で2部の出力は完全に一致し、import後もGILが無効であることをインタープリタから確認しています。

## 再現する { #reproduce }

コーパスは[`tests/assets/real_world`](https://github.com/yhay81/pylopdf/tree/main/tests/assets/real_world)にあり、各ファイルの出典とライセンスも記載しています。リポジトリをクローンした場所で次を実行します。

```bash
uv sync --all-extras --group bench
uv run python bench/run.py                 # bench/results/latest.md に書き出す
python3.14t bench/free_threaded.py         # フリースレッド版CPython 3.14が必要
```

最新のレポートは[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)と[`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md)としてコミットしています。数値を引用するときは、計測した環境とコーパスを併記してください。
