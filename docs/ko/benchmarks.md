---
title: 벤치마크
description: 추출, 병합, 래스터화, PNG 인코딩에서의 pylopdf 성능을 재현 가능하게 측정하고 빠른 결과와 느린 결과를 함께 공개합니다.
---

# 벤치마크

이 벤치마크는 실제 PDF 14개로 pylopdf를 pymupdf, pypdf, pdfplumber와 비교하며, pylopdf가 빠른 경우뿐 아니라 느린 경우도 보여 줍니다. 컴퓨터 한 대와 코퍼스 하나에서 얻은 결과이므로 순위가 아니라, 여러분의 문서로 무엇을 측정할지 판단하는 참고 자료로 활용하세요.

!!! info "최신 실행"
    **2026-10-17 17:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro(arm64)<br>
    thin-LTO 릴리스 프로필로 빌드한 pylopdf 0.14.0 · pymupdf 1.28.0 · pypdf 6.15.0 · pdfplumber 0.11.10<br>
    워밍업 1회와 측정 7회. 표는 중앙값(밀리초)을 보여 줍니다.

## 한눈에 보기 { #overview }

| 작업 | 최신 코퍼스의 결과 |
|---|---|
| 실제 PDF 14개 병합 | pylopdf **15.4 ms**, pymupdf 111.2 ms, pypdf 141.5 ms |
| 첫 페이지를 2× PNG로(전체 과정) | 14개 파일 모두 pylopdf가 앞섰으며 합계는 **212.9 ms** 대 573.9 ms |
| 래스터화만 | 14개 파일 중 cold에서는 9개, warm에서는 12개에서 pymupdf가 앞섰고(warm 1개는 동률) warm 합계는 pylopdf 145.9 ms, pymupdf **78.8 ms** |
| PNG 인코딩 | pylopdf가 약 9배 빨랐지만(**44.4 ms** 대 397.4 ms) 40% 더 많은 바이트를 기록(4,645 대 **3,318 KiB**) |
| 12페이지를 2×로 렌더링 | `render_pages()`가 작업자 1개의 163.7 ms에서 8개의 34.6 ms로 줄어 **4.74배 가속**. pymupdf는 순차 처리에 425.3 ms |
| 전체 텍스트 추출 | 14개 중 12개에서 pylopdf가 앞섰고, pymupdf와 pypdf가 각각 하나씩 앞섬 |
| 추출 충실도의 대용 지표 | 두 라이브러리가 모두 텍스트를 추출한 파일에서 유사도 0.443~1.000. Type 3 fixture에서는 pylopdf가 텍스트를 추출하지 않음 |
| Linux와 Windows CI 러너 | pylopdf의 추출 우위가 더 작았고, MuPDF가 cold 래스터화에서도 더 빨랐음. [다른 플랫폼](#other-platforms) 참고 |

## 텍스트 추출 { #text-extraction }

모든 페이지, 밀리초 단위이며 낮을수록 빠릅니다.

| 파일 | pylopdf | pymupdf | pypdf | pdfplumber |
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
| **합계** | **141.2** | 428.6 | 932.6 | 5766.4 |

## 추출 내용 { #extraction-content }

이 값은 정확도 점수가 아니라 대용 지표입니다. 공백을 정규화한 텍스트를 pymupdf와 비교합니다. 폼과 회전된 표에서 유사도가 낮더라도, 문자 수가 일치한다면 읽기 순서나 공백 처리 방식의 차이일 수 있습니다. 문자 수가 0인 행은 이미지만 있는 파일입니다. 단, `pdfium-type3.pdf`는 예외로, hayro가 Unicode 매핑을 보고하지 않아 pylopdf가 그 글리프를 건너뜁니다.

| 파일 | pylopdf 문자 수 | pymupdf 문자 수 | 유사도 |
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

## 병합 { #merge }

| 작업 | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| 코퍼스 파일 14개 전체 병합 | **15.4** | 111.2 | 141.5 |

## 렌더링 { #rendering }

첫 페이지를 2×로, 밀리초 단위이며 낮을수록 빠릅니다. 두 라이브러리 모두 측정한 릴리스의 기본값을 사용했습니다. pylopdf 0.14.0은 투명 배경의 straight-alpha RGBA8을, pymupdf는 흰 바탕의 RGB를 만듭니다. 현재 pylopdf는 pymupdf와 마찬가지로 기본값이 흰 바탕의 RGB입니다.

### 전체 과정 { #rendering-end-to-end }

각 실행은 문서를 새로 열고, 페이지를 래스터화하고, PNG로 인코딩합니다.

| 파일 | pylopdf | pymupdf |
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
| **합계** | **212.9** | 573.9 |

이 우위의 대부분은 래스터화가 아니라 PNG 인코딩에서 나옵니다. 다음 두 표는 같은 작업을 나누어 보여 줍니다.

### 래스터화 { #rasterization }

cold 실행은 반복할 때마다 문서를 새로 열고, warm 실행은 이미 열린 문서의 같은 페이지를 래스터화합니다. MuPDF의 프로세스 전역 글리프 캐시처럼 문서보다 오래 사는 캐시는 cold 실행에도 적용됩니다. MuPDF는 열린 문서에 대해 불러온 글꼴과 렌더링한 글리프도 유지하며, 이는 텍스트가 많은 페이지에서 특히 중요합니다. pylopdf는 문서를 연 스레드에서 해석한 글꼴과 글리프 윤곽을 재사용하지만, 여전히 모든 글리프를 래스터화하고 모든 이미지를 다시 디코딩하므로 warm 실행에서 얻는 이득이 MuPDF보다 훨씬 적습니다.

| 파일 | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
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
| **합계** | **164.7** | 177.1 | 145.9 | **78.8** |

### PNG 인코딩 { #png-encoding }

두 인코더 모두 이미 렌더링된 래스터를 받습니다. pylopdf는 RGBA8 출력에 빠른 fdeflate 압축을 사용해 파일 크기 대신 속도를 택하고, pymupdf는 RGB 출력에 zlib의 기본 수준을 사용합니다. 인코딩 시간보다 파일 크기가 중요하다면 pylopdf의 출력을 다시 압축하세요.

| 파일 | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
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
| **합계** | **44.4** | 397.4 | 4645.1 | **3317.7** |

## 병렬 렌더링 { #parallel-rendering }

`usrguide.pdf`의 처음 12페이지를 2× PNG로, 밀리초 단위이며 낮을수록 빠릅니다. 일괄 처리는 입력 순서를 유지하고 변경할 수 없는 문서 스냅샷 하나를 사용합니다.

| 작업자 | 시간 | 작업자 1개 대비 가속 |
|---:|---:|---:|
| 1 | 163.7 | 1.00× |
| 2 | 88.4 | 1.85× |
| 4 | 47.4 | 3.45× |
| 8 | 34.6 | 4.74× |

실제 동시성은 요청한 작업자 수와, 추정치 512 MB의 활성 렌더링 메모리 양쪽으로 제한됩니다. 참고로 pymupdf는 새로 연 문서 하나에서 같은 12페이지를 순차적으로 렌더링하고 인코딩하는 데 425.3 ms가 걸렸습니다.

## 다른 플랫폼 { #other-platforms }

위의 스냅샷은 macOS arm64 컴퓨터 한 대에서 얻은 것입니다. 2026-10-17에 같은 `bench/run.py`를 GitHub 호스팅 릴리스 러너마다 thin-LTO abi3 wheel로 한 번씩 실행했습니다(Python 3.14.8, macOS x86_64에서는 3.14.7, 워밍업 1회와 측정 5회). 각 셀은 pylopdf / pymupdf의 코퍼스 합계를 밀리초로 보여 줍니다. 공유 러너는 스냅샷보다 잡음이 크므로 작은 차이는 잡음으로 보세요.

| 러너 | 텍스트 추출 | 병합 | 전체 과정 | 래스터 cold | 래스터 warm | PNG 인코딩 |
|---|---:|---:|---:|---:|---:|---:|
| Linux x86_64, AMD EPYC 9V45, 4 vCPU | **119.7** / 146.8 | **14.3** / 73.8 | **234.8** / 459.9 | 198.1 / **167.1** | 182.9 / **72.9** | **33.4** / 284.7 |
| Linux aarch64, 4 vCPU | **186.0** / 270.3 | **27.5** / 111.7 | **372.1** / 720.7 | 289.0 / **226.1** | 264.2 / **101.3** | **81.4** / 505.7 |
| macOS arm64, Apple M1 (virtual), 3 vCPU | **171.3** / 601.1 | **22.4** / 179.7 | **290.4** / 772.1 | **220.2** / 251.7 | 204.3 / **110.3** | **57.9** / 539.1 |
| macOS x86_64, Intel Core i7-8700B, 4 vCPU | **254.7** / 1188.0 | **87.0** / 430.4 | **651.3** / 922.2 | 535.8 / **392.0** | 444.7 / **178.6** | **62.2** / 490.5 |
| Windows Server 2025 x86_64, Intel, 4 vCPU | **243.1** / 269.7 | **37.5** / 132.9 | **533.2** / 874.6 | 470.5 / **293.7** | 434.2 / **147.5** | **57.8** / 579.6 |

pymupdf의 텍스트 추출은 두 macOS 러너에서 Linux와 Windows보다 훨씬 느렸으므로, Linux와 Windows에서 pylopdf의 추출 우위는 macOS 스냅샷보다 작습니다. MuPDF는 warm에서는 모든 러너에서, cold에서는 Apple M1을 제외한 모든 러너에서 더 빠르게 래스터화했습니다. 그래도 pymupdf의 합계에서는 PNG 인코딩이 대부분을 차지하므로 전체 과정에서는 pylopdf가 앞섰습니다.

## free-threaded 추출 { #free-threaded-extraction }

`bill-hr815.pdf`의 독립된 사본 두 개에 대해, Windows 11용 free-threaded CPython 3.14.6에서 모든 페이지의 텍스트를 추출했습니다. 워밍업 1회와, 어느 모드를 먼저 실행할지 번갈아 바꾼 짝지은 실행 7회의 중앙값입니다.

| 모드 | 작업자 | 시간 | 가속 |
|---|---:|---:|---:|
| 순차 | 1 | 400.8 ms | 1.00× |
| 병렬 | 2 | 235.5 ms | 1.70× |

모든 실행에서 두 사본은 정확히 같은 출력을 냈고, 인터프리터는 import 후에도 GIL이 비활성 상태로 유지되었다고 보고했습니다.

## 재현하기 { #reproduce }

코퍼스는 각 파일의 출처, 라이선스와 함께 [`tests/assets/real_world`](https://github.com/yhay81/pylopdf/tree/main/tests/assets/real_world)에 있습니다. 저장소를 클론한 곳에서:

```bash
uv sync --all-extras --group bench
uv run python bench/run.py                 # bench/results/latest.md에 기록
python3.14t bench/free_threaded.py         # free-threaded CPython 3.14 필요
```

최신 보고서는 [`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)와 [`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md)로 커밋되어 있습니다. 수치를 인용할 때는 측정한 환경과 코퍼스를 함께 밝혀 주세요.
