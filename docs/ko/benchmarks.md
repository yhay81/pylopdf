---
title: 벤치마크
description: 추출, 병합, 래스터화 및 PNG 인코딩에서의 pylopdf 성능을 재현 가능하게 측정하고 장단점을 함께 공개합니다.
---

# 벤치마크

pylopdf는 **빠른 결과와 느린 결과를 함께** 공개합니다. 이 측정은 특정 컴퓨터와
코퍼스의 스냅샷이며 보편적인 순위가 아닙니다. 여러분의 작업에서 무엇을 측정할지
판단하는 자료로 사용하세요.

!!! info "최신 실행"
    **2026-10-17 17:29 UTC** · macOS 27.0.1 · Python 3.14.8 · Apple M2 Pro(arm64)<br>
    thin LTO release profile로 빌드한 pylopdf 0.14.0 · pymupdf 1.28.0 · pypdf 6.15.0 · pdfplumber 0.11.10<br>
    워밍업 1회와 측정 7회, 표는 중앙값(밀리초)을 표시합니다.

## 한눈에 보기 { #overview }

| 작업 | 최신 코퍼스의 결과 |
|---|---|
| 실제 PDF 14개 병합 | pylopdf **15.4 ms**, pymupdf 111.2 ms, pypdf 141.5 ms |
| 첫 페이지를 2× PNG로 처리(전체 과정) | 14개 파일 모두 pylopdf가 가장 빨랐으며 합계는 **212.9 ms** 대 573.9 ms |
| 래스터화만 | 14개 파일 중 cold에서는 9개, warm에서는 12개에서 pymupdf가 빨랐고(warm 1개는 동률) warm 합계는 145.9 ms 대 **78.8 ms** |
| PNG 인코딩 | pylopdf가 약 9배 빨랐지만(**44.4 ms** 대 397.4 ms) 출력은 40% 컸음(4,645 KiB 대 **3,318 KiB**) |
| 12페이지를 2×로 렌더링 | `render_pages()`가 1 worker의 163.7 ms에서 8 workers의 34.6 ms로 줄어 **4.74배 가속**. pymupdf의 순차 처리는 425.3 ms |
| 전체 텍스트 추출 | 14개 중 12개는 pylopdf, 나머지 2개는 pymupdf와 pypdf가 하나씩 가장 빨랐음 |
| 추출 충실도의 대용 지표 | 두 라이브러리 모두 텍스트를 추출한 파일에서 유사도 0.443~1.000. Type 3 fixture에서는 pylopdf가 텍스트를 추출하지 않음 |
| Linux와 Windows CI runner | pylopdf의 추출 우위가 더 작았고 MuPDF는 cold 래스터화에서도 빨랐음. [다른 플랫폼](#other-platforms) 참고 |

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

이 값은 정확도 점수가 아니라 대용 지표입니다. 공백을 정규화한 텍스트를 pymupdf와
비교합니다. 폼과 회전된 표에서 유사도가 낮더라도 문자 수가 일치한다면 읽기 순서나
공백 정책의 차이일 수 있습니다. 문자 수가 0인 행은 이미지 전용 PDF입니다. 단,
`pdfium-type3.pdf`는 예외이며 hayro가 Unicode 매핑을 보고하지 않는 glyph를 pylopdf가
건너뜁니다.

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
| 코퍼스 파일 14개 모두 병합 | **15.4** | 111.2 | 141.5 |

## 렌더링 { #rendering }

첫 페이지를 2×로 처리한 밀리초이며 낮을수록 빠릅니다. 두 라이브러리 모두 기본값을
사용합니다. pylopdf는 투명 배경의 straight-alpha RGBA8을, pymupdf는 흰 배경의 RGB를
생성합니다.

### 전체 과정 { #rendering-end-to-end }

매 실행마다 새 문서를 열고 페이지를 래스터화한 다음 PNG로 인코딩합니다.

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

이 우위의 대부분은 래스터화가 아니라 PNG 인코딩에서 나옵니다. 다음 두 표는 같은
작업을 나누어 보여 줍니다.

### 래스터화 { #rasterization }

cold 실행은 반복할 때마다 새 문서를 열고, warm 실행은 이미 열린 문서의 같은 페이지를
다시 래스터화합니다. MuPDF의 프로세스 전역 glyph cache처럼 문서보다 오래 남는
cache는 cold 실행에도 적용됩니다. MuPDF는 열린 문서의 로드된 글꼴과 렌더링된
glyph도 유지하므로 텍스트가 많은 페이지에서 차이가 가장 큽니다. pylopdf는 문서를 연
스레드에서 파싱한 글꼴과 glyph outline을 재사용하지만, glyph 래스터화와 이미지 디코딩은
매번 다시 하므로 warm에서 줄어드는 시간이 MuPDF보다 훨씬 작습니다.

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

두 인코더 모두 이미 렌더링된 래스터를 받습니다. pylopdf는 RGBA8 출력에 빠른 fdeflate
압축을 사용해 파일 크기보다 속도를 우선하고, pymupdf는 RGB 출력에 zlib의 기본 수준을
사용합니다. 인코딩 시간보다 파일 크기가 중요하다면 pylopdf 출력을 외부에서 다시
압축하세요.

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

`usrguide.pdf`의 첫 12페이지를 2× PNG로 변환한 밀리초이며 낮을수록 빠릅니다.
묶음은 입력 순서를 유지하고 하나의 불변 문서 스냅샷을 사용합니다.

| Workers | 시간 | 1 worker 대비 |
|---:|---:|---:|
| 1 | 163.7 | 1.00배 |
| 2 | 88.4 | 1.85배 |
| 4 | 47.4 | 3.45배 |
| 8 | 34.6 | 4.74배 |

실제 동시성은 요청한 worker 수와 약 512 MB의 추정 실시간 렌더링 메모리로 제한됩니다.
참고로 pymupdf는 같은 12페이지를 새 문서 하나에서 순차적으로 렌더링하고 인코딩하는 데
425.3 ms가 걸렸습니다.

## 다른 플랫폼 { #other-platforms }

위 스냅샷은 macOS arm64 컴퓨터 한 대의 결과입니다. 2026-10-17에 같은 `bench/run.py`를
thin LTO abi3 wheel로 GitHub-hosted release runner마다 한 번씩 실행했습니다(Python
3.14.8, macOS x86_64만 3.14.7, 워밍업 1회와 측정 5회). 각 칸은 pylopdf / pymupdf의
코퍼스 합계(밀리초)입니다. 공유 runner는 스냅샷보다 잡음이 크므로 작은 차이는 잡음으로
보세요.

| Runner | 텍스트 추출 | 병합 | 전체 과정 | 래스터화 cold | 래스터화 warm | PNG 인코딩 |
|---|---:|---:|---:|---:|---:|---:|
| Linux x86_64, AMD EPYC 9V45, 4 vCPU | **119.7** / 146.8 | **14.3** / 73.8 | **234.8** / 459.9 | 198.1 / **167.1** | 182.9 / **72.9** | **33.4** / 284.7 |
| Linux aarch64, 4 vCPU | **186.0** / 270.3 | **27.5** / 111.7 | **372.1** / 720.7 | 289.0 / **226.1** | 264.2 / **101.3** | **81.4** / 505.7 |
| macOS arm64, Apple M1 (virtual), 3 vCPU | **171.3** / 601.1 | **22.4** / 179.7 | **290.4** / 772.1 | **220.2** / 251.7 | 204.3 / **110.3** | **57.9** / 539.1 |
| macOS x86_64, Intel Core i7-8700B, 4 vCPU | **254.7** / 1188.0 | **87.0** / 430.4 | **651.3** / 922.2 | 535.8 / **392.0** | 444.7 / **178.6** | **62.2** / 490.5 |
| Windows Server 2025 x86_64, Intel, 4 vCPU | **243.1** / 269.7 | **37.5** / 132.9 | **533.2** / 874.6 | 470.5 / **293.7** | 434.2 / **147.5** | **57.8** / 579.6 |

pymupdf의 텍스트 추출은 두 macOS runner에서 Linux와 Windows보다 훨씬 느렸습니다. 따라서
Linux와 Windows에서 pylopdf의 추출 우위는 macOS 스냅샷보다 작습니다. MuPDF는 warm에서는
모든 runner에서, cold에서는 Apple M1을 제외한 모든 runner에서 더 빠르게 래스터화했습니다.
그래도 pymupdf 합계의 대부분을 PNG 인코딩이 차지하므로 전체 과정에서는 pylopdf가
앞섰습니다.

## free-threaded 추출 { #free-threaded-extraction }

Windows 11의 free-threaded CPython 3.14.6에서 서로 독립된 `bill-hr815.pdf`
두 개의 전체 페이지 텍스트를 추출했습니다. 한 번의 warmup 후 먼저 실행할 모드를
번갈아 바꾼 일곱 쌍 실행의 중앙값입니다.

| 모드 | Workers | 시간 | 속도 향상 |
|---|---:|---:|---:|
| 순차 | 1 | 400.8 ms | 1.00배 |
| 병렬 | 2 | 235.5 ms | 1.70배 |

모든 실행에서 두 문서의 출력이 정확히 일치했고, interpreter는 import 후에도
GIL이 비활성 상태임을 확인했습니다.

## 재현 방법 { #reproduce }

코퍼스는 `tests/assets/real_world`에 있으며, 출처와 라이선스도 같은 위치에
기록되어 있습니다.

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
uv run python tools/pyodide_compat.py --root . --benchmark-only \
  --benchmark-output .tmp/limits-benchmark.json
# Windows의 free-threaded CPython 3.14 interpreter에서:
py -3.14t bench/free_threaded.py
# POSIX에서:
python3.14t bench/free_threaded.py
```

생성된 원본 보고서는
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)에
커밋됩니다. 별도로 생성되는 free-threaded 보고서는
[`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md)에
커밋되며, `bench/run.py`를 다시 실행해도 CPython 3.14t 증거를 덮어쓰지 않습니다.
native/Pyodide resource-policy 기준값은
[`bench/results/limits-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/limits-latest.md)에
별도로 커밋됩니다. 두 번째 command는 제한된 open/extract와 제어된 거부를 측정합니다.
CI는 같은 case를Pyodide에서도 실행하고 Wasm linear memory 증가를 기록합니다.
이 시간과memory 값은 추세이며 native/Wasm 성능 비교 주장이 아닙니다. 수치를
인용할 때는 환경과 코퍼스도 함께 적어 주세요.
