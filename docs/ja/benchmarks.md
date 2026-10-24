---
title: ベンチマーク
description: 抽出・結合・ラスタライズ・PNGエンコードの再現可能なベンチマーク。速い結果も遅い結果も同時に公開します。
---

# ベンチマーク

このベンチマークでは、14件の実際のPDFでpylopdfをpymupdf、pypdf、pdfplumberと比較し、pylopdfが速いケースだけでなく遅いケースも示します。1台のマシンと1つのコーパスでの結果なので、順位としてではなく、自分の文書で何を測るべきかの目安として扱ってください。

!!! info "最新の実行"
    **2026-10-24 00:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro（arm64）<br>
    Renderingマイルストーンのブランチ（hayro 0.8.0）からthin LTOのrelease profileでビルドしたpylopdf · pymupdf 2.0 (MuPDF 1.28.5) · pypdf 6.21.1 · pdfplumber 0.11.10<br>ウォームアップ1回 + 計測7回。表は中央値（ミリ秒）です。

## 概要 { #overview }

| ワークロード | 最新コーパスで分かったこと |
|---|---|
| 実世界PDF 14件を結合 | pylopdf **19.7 ms**、pymupdf 136.0 ms、pypdf 120.7 ms |
| 先頭ページを2倍PNGへ（一連の処理） | 14ファイルすべてでpylopdfが高速。合計は**143.5 ms**対514.5 ms |
| ラスタライズのみ | coldでは14ファイル中10件でpylopdfが高速（2件は同値）、warmでは5件。合計はcold **103.4** / 186.1 ms、warm 87.8 / **81.0** ms（pylopdf / pymupdf） |
| PNGエンコード | pylopdfは約8倍高速（**38.6 ms**対319.0 ms）ですが、出力は55%大きくなります（5,132 KiB対**3,318 KiB**） |
| 12ページを2倍で描画 | `doc.render()`は1 workerの109.4 msから8 workersの33.1 msへ短縮し、**3.30倍高速化**。pymupdfの逐次処理は332.7 ms |
| 全ページのテキスト抽出 | 14ファイルすべてでpylopdfが高速。合計は**117.1 ms**対229.7 ms |
| 抽出精度の代理指標 | 両ライブラリがテキストを抽出したファイルでは類似度0.443〜1.000。Type 3のfixtureからpylopdfはテキストを抽出しません |
| LinuxとWindowsのCI runner | pylopdf 0.14.0での計測。pylopdfの抽出のリードは小さく、MuPDFはcoldのラスタライズでも高速。[他のプラットフォーム](#other-platforms)を参照 |

## テキスト抽出 { #text-extraction }

全ページ、単位ms。小さいほど高速です。

| ファイル | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **59.4** | 127.5 | 376.9 | 4865.3 |
| bunka-kokugo-series-019-p4.pdf | **0.05** | 0.29 | 0.44 | 0.52 |
| f1040.pdf | **17.3** | 34.6 | 156.1 | 414.1 |
| mhlw-doc.pdf | **9.6** | 9.9 | 49.9 | 76.9 |
| nics-background-checks-2015-11.pdf | **3.4** | 5.6 | 56.8 | 131.1 |
| patent-us223898.pdf | **4.7** | 6.1 | 46.6 | 168.3 |
| pdf20-simple.pdf | **0.05** | 0.48 | 0.69 | 0.84 |
| pdfium-jpx-lzw.pdf | **0.02** | 0.28 | 0.25 | 0.34 |
| pdfium-links-highlights-annots.pdf | **0.12** | 0.74 | 0.45 | 1.4 |
| pdfium-smask-blend.pdf | **0.04** | 0.31 | 0.29 | 0.55 |
| pdfium-type3.pdf | **0.03** | 0.32 | 0.39 | 0.50 |
| senate-expenditures.pdf | **2.4** | 4.7 | 52.0 | 114.2 |
| usrguide.pdf | **19.7** | 38.1 | 312.9 | 821.2 |
| wdl6812-manuscript.pdf | **0.21** | 0.68 | 0.77 | 0.97 |
| **合計** | **117.1** | 229.7 | 1054.4 | 6596.2 |

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
| コーパス14件をすべて結合 | **19.7** | 136.0 | 120.7 |

## レンダリング { #rendering }

先頭ページを2倍で描画。単位ms、小さいほど高速です。どちらも既定の設定で、白背景の不透明なRGBを生成します。

### 一連の処理 { #rendering-end-to-end }

各実行で新しくドキュメントを開き、ページをラスタライズしてPNGへエンコードします。

| ファイル | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **13.2** | 45.4 |
| bunka-kokugo-series-019-p4.pdf | **15.8** | 78.5 |
| f1040.pdf | **18.5** | 62.1 |
| mhlw-doc.pdf | **13.3** | 41.9 |
| nics-background-checks-2015-11.pdf | **16.7** | 44.0 |
| patent-us223898.pdf | **10.4** | 43.6 |
| pdf20-simple.pdf | **1.6** | 9.2 |
| pdfium-jpx-lzw.pdf | **7.3** | 30.3 |
| pdfium-links-highlights-annots.pdf | **2.8** | 16.7 |
| pdfium-smask-blend.pdf | **0.34** | 2.6 |
| pdfium-type3.pdf | **0.24** | 1.2 |
| senate-expenditures.pdf | **15.5** | 32.9 |
| usrguide.pdf | **12.5** | 39.1 |
| wdl6812-manuscript.pdf | **15.3** | 66.8 |
| **合計** | **143.5** | 514.5 |

この差の大部分はラスタライズではなくPNGエンコードによるものです。次の2つの表で同じ処理を分けて示します。

### ラスタライズ { #rasterization }

coldは繰り返しごとに新しくドキュメントを開き、warmは開いたままのドキュメントで同じページを再びラスタライズします。MuPDFのプロセス全体のglyph cacheのようにドキュメントより長く残るcacheは、coldでも有効です。pylopdfはドキュメントを開いたスレッドでドキュメントごとに1つのhayroのrender cacheを保持し、フォント、glyph outline、画像の解析は1回で済みます。MuPDFは描画済みglyphのビットマップも保持するため、テキストの多いページではwarmでもMuPDFが高速です（`usrguide.pdf`で4.5 ms対0.74 ms）が、スキャンや画像の多いページではpylopdfが高速です。この差を埋めるglyph cacheは上流でスコープを相談中で（[LaurenzV/hayro#1613](https://github.com/LaurenzV/hayro/issues/1613)）、ページ全体を覆うCropBoxのクリップを省く[LaurenzV/hayro#1612](https://github.com/LaurenzV/hayro/pull/1612)はhayro単体でwarmの合計を9%短縮します。

| ファイル | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **7.8** | **7.8** | 6.4 | **3.2** |
| bunka-kokugo-series-019-p4.pdf | **12.3** | 51.7 | **12.0** | 15.0 |
| f1040.pdf | **13.1** | 28.3 | **8.8** | 16.8 |
| mhlw-doc.pdf | **9.7** | 12.1 | 5.4 | **3.0** |
| nics-background-checks-2015-11.pdf | 11.3 | **8.0** | 9.8 | **4.8** |
| patent-us223898.pdf | **7.0** | 22.2 | **7.1** | 11.7 |
| pdf20-simple.pdf | **0.65** | 0.82 | 0.60 | **0.28** |
| pdfium-jpx-lzw.pdf | **6.0** | 14.9 | **5.8** | 9.9 |
| pdfium-links-highlights-annots.pdf | **1.4** | **1.4** | 1.2 | **0.56** |
| pdfium-smask-blend.pdf | **0.23** | 0.56 | **0.19** | 0.32 |
| pdfium-type3.pdf | **0.14** | 0.30 | 0.10 | **0.04** |
| senate-expenditures.pdf | 12.1 | **5.5** | 11.3 | **3.6** |
| usrguide.pdf | **8.8** | 9.6 | 4.5 | **0.74** |
| wdl6812-manuscript.pdf | **12.9** | 23.0 | 14.6 | **11.0** |
| **合計** | **103.4** | 186.1 | 87.8 | **81.0** |

### PNGエンコード { #png-encoding }

どちらのエンコーダにも描画済みのラスタを渡します。pylopdfは高速なfdeflate圧縮を使い、ファイルサイズより速度を優先します。pymupdfはzlibの既定レベルを使います。エンコード時間よりファイルサイズが重要な場合は、pylopdfの出力を外部で再圧縮してください。

| ファイル | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **5.8** | 36.8 | 535.8 | **276.1** |
| bunka-kokugo-series-019-p4.pdf | **3.5** | 25.9 | 420.7 | **249.4** |
| f1040.pdf | **5.0** | 34.0 | 816.8 | **360.9** |
| mhlw-doc.pdf | **3.8** | 30.0 | 581.9 | **301.4** |
| nics-background-checks-2015-11.pdf | **5.0** | 35.0 | 689.9 | **343.0** |
| patent-us223898.pdf | **3.2** | 21.1 | 232.4 | **154.6** |
| pdf20-simple.pdf | **0.85** | 8.1 | 27.2 | **14.6** |
| pdfium-jpx-lzw.pdf | **1.1** | 15.3 | 31.8 | **9.4** |
| pdfium-links-highlights-annots.pdf | **1.2** | 15.0 | 54.9 | **21.5** |
| pdfium-smask-blend.pdf | **0.10** | 2.0 | 3.4 | **2.6** |
| pdfium-type3.pdf | **0.08** | 0.90 | 3.0 | **1.1** |
| senate-expenditures.pdf | **3.4** | 27.8 | 578.7 | **253.8** |
| usrguide.pdf | **2.8** | 22.6 | 253.3 | **138.0** |
| wdl6812-manuscript.pdf | **2.8** | 44.6 | **901.7** | 1191.3 |
| **合計** | **38.6** | 319.0 | 5131.6 | **3317.7** |

## 並列レンダリング { #parallel-rendering }

`usrguide.pdf`の先頭12ページを2倍PNGへ描画。単位ms、小さいほど高速です。バッチは入力順を保持し、単一の不変ドキュメントスナップショットを使います。

| Workers | 時間 | 1 worker比 |
|---:|---:|---:|
| 1 | 109.4 | 1.00x |
| 2 | 63.3 | 1.73x |
| 4 | 45.5 | 2.41x |
| 8 | 33.1 | 3.30x |

実際の並列度は、指定worker数と推定512 MBの描画作業メモリの両方で制限されます。参考として、pymupdfは同じ12ページを1つの新しいドキュメントで逐次描画・エンコードし、332.7 msかかりました。

## 他のプラットフォーム { #other-platforms }

これらはhayro 0.7上のpylopdf 0.14.0、render cacheを保持する前の計測です。上のmacOSのスナップショットのほうが新しい結果です。

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
