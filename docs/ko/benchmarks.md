---
title: 벤치마크
description: 추출, 병합, 래스터화, PNG 인코딩에서의 pylopdf 성능을 재현 가능하게 측정하고 빠른 결과와 느린 결과를 함께 공개합니다.
---

# 벤치마크

이 벤치마크는 실제 PDF 14개로 pylopdf를 pymupdf, pypdf, pdfplumber와 비교하며, pylopdf가 빠른 경우뿐 아니라 느린 경우도 보여 줍니다. 컴퓨터 한 대와 코퍼스 하나에서 얻은 결과이므로 순위가 아니라, 여러분의 문서로 무엇을 측정할지 판단하는 참고 자료로 활용하세요.

!!! info "최신 실행"
    **2026-10-24 00:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro(arm64)<br>
    Rendering 마일스톤 브랜치(hayro 0.8.0)에서 thin-LTO 릴리스 프로필로 빌드한 pylopdf · pymupdf 2.0 (MuPDF 1.28.5) · pypdf 6.21.1 · pdfplumber 0.11.10<br>
    워밍업 1회와 측정 7회. 표는 중앙값(밀리초)을 보여 줍니다.

## 한눈에 보기 { #overview }

| 작업 | 최신 코퍼스의 결과 |
|---|---|
| 실제 PDF 14개 병합 | pylopdf **19.7 ms**, pymupdf 136.0 ms, pypdf 120.7 ms |
| 첫 페이지를 2× PNG로(전체 과정) | 14개 파일 모두 pylopdf가 앞섰으며 합계는 **143.5 ms** 대 514.5 ms |
| 래스터화만 | cold에서는 14개 중 10개에서 pylopdf가 앞섰고(2개는 동률) warm에서는 5개. 합계는 cold **103.4** / 186.1 ms, warm 87.8 / **81.0** ms(pylopdf / pymupdf) |
| PNG 인코딩 | pylopdf가 약 8배 빨랐지만(**38.6 ms** 대 319.0 ms) 55% 더 많은 바이트를 기록(5,132 대 **3,318 KiB**) |
| 12페이지를 2×로 렌더링 | `doc.render()`가 작업자 1개의 109.4 ms에서 8개의 33.1 ms로 줄어 **3.30배 가속**. pymupdf는 순차 처리에 332.7 ms |
| 전체 텍스트 추출 | 14개 파일 모두 pylopdf가 앞섰으며 합계는 **117.1 ms** 대 229.7 ms |
| 추출 충실도의 대용 지표 | 두 라이브러리가 모두 텍스트를 추출한 파일에서 유사도 0.443~1.000. Type 3 fixture에서는 pylopdf가 텍스트를 추출하지 않음 |
| Linux와 Windows CI 러너 | pylopdf 0.14.0으로 측정. pylopdf의 추출 우위가 더 작았고, MuPDF가 cold 래스터화에서도 더 빨랐음. [다른 플랫폼](#other-platforms) 참고 |

## 텍스트 추출 { #text-extraction }

모든 페이지, 밀리초 단위이며 낮을수록 빠릅니다.

| 파일 | pylopdf | pymupdf | pypdf | pdfplumber |
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
| **합계** | **117.1** | 229.7 | 1054.4 | 6596.2 |

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
| 코퍼스 파일 14개 전체 병합 | **19.7** | 136.0 | 120.7 |

## 렌더링 { #rendering }

첫 페이지를 2×로, 밀리초 단위이며 낮을수록 빠릅니다. 두 라이브러리 모두 기본값인 흰 바탕의 불투명 RGB를 만듭니다.

### 전체 과정 { #rendering-end-to-end }

각 실행은 문서를 새로 열고, 페이지를 래스터화하고, PNG로 인코딩합니다.

| 파일 | pylopdf | pymupdf |
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
| **합계** | **143.5** | 514.5 |

이 우위의 대부분은 래스터화가 아니라 PNG 인코딩에서 나옵니다. 다음 두 표는 같은 작업을 나누어 보여 줍니다.

### 래스터화 { #rasterization }

cold 실행은 반복할 때마다 문서를 새로 열고, warm 실행은 이미 열린 문서의 같은 페이지를 래스터화합니다. MuPDF의 프로세스 전역 글리프 캐시처럼 문서보다 오래 사는 캐시는 cold 실행에도 적용됩니다. pylopdf는 문서를 연 스레드에서 문서마다 hayro 렌더 캐시 하나를 유지하므로 글꼴, 글리프 윤곽, 이미지를 한 번만 해석합니다. MuPDF는 렌더링한 글리프 비트맵도 유지하므로 텍스트가 많은 페이지에서는 warm에서도 MuPDF가 앞서고(`usrguide.pdf` 4.5 ms 대 0.74 ms), 스캔과 이미지가 많은 페이지에서는 pylopdf가 앞섭니다. 이 차이를 메울 글리프 캐시는 업스트림에서 범위를 논의 중이며([LaurenzV/hayro#1613](https://github.com/LaurenzV/hayro/issues/1613)), 페이지 전체를 덮는 CropBox 클립을 생략하는 [LaurenzV/hayro#1612](https://github.com/LaurenzV/hayro/pull/1612)는 hayro만으로 warm 합계를 9% 줄입니다.

| 파일 | pylopdf cold | pymupdf cold | pylopdf warm | pymupdf warm |
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
| **합계** | **103.4** | 186.1 | 87.8 | **81.0** |

### PNG 인코딩 { #png-encoding }

두 인코더 모두 이미 렌더링된 래스터를 받습니다. pylopdf는 빠른 fdeflate 압축을 사용해 파일 크기 대신 속도를 택하고, pymupdf는 zlib의 기본 수준을 사용합니다. 인코딩 시간보다 파일 크기가 중요하다면 pylopdf의 출력을 다시 압축하세요.

| 파일 | pylopdf ms | pymupdf ms | pylopdf KiB | pymupdf KiB |
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
| **합계** | **38.6** | 319.0 | 5131.6 | **3317.7** |

## 병렬 렌더링 { #parallel-rendering }

`usrguide.pdf`의 처음 12페이지를 2× PNG로, 밀리초 단위이며 낮을수록 빠릅니다. 일괄 처리는 입력 순서를 유지하고 변경할 수 없는 문서 스냅샷 하나를 사용합니다.

| 작업자 | 시간 | 작업자 1개 대비 가속 |
|---:|---:|---:|
| 1 | 109.4 | 1.00x |
| 2 | 63.3 | 1.73x |
| 4 | 45.5 | 2.41x |
| 8 | 33.1 | 3.30x |

실제 동시성은 요청한 작업자 수와, 추정치 512 MB의 활성 렌더링 메모리 양쪽으로 제한됩니다. 참고로 pymupdf는 새로 연 문서 하나에서 같은 12페이지를 순차적으로 렌더링하고 인코딩하는 데 332.7 ms가 걸렸습니다.

## 다른 플랫폼 { #other-platforms }

이 실행은 hayro 0.7 위의 pylopdf 0.14.0, 렌더 캐시를 유지하기 전의 측정입니다. 위의 macOS 스냅샷이 더 최신입니다.

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
