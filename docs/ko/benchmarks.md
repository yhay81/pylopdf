---
title: 벤치마크
description: 추출, 병합 및 렌더링에서의 pylopdf 성능을 재현 가능하게 측정하고 장단점을 함께 공개합니다.
---

# 벤치마크

pylopdf는 **빠른 결과와 느린 결과를 함께** 공개합니다. 이 측정은 특정 컴퓨터와
코퍼스의 스냅샷이며 보편적인 순위가 아닙니다. 여러분의 작업에서 무엇을 측정할지
판단하는 자료로 사용하세요.

!!! info "최신 실행"
    **2026-07-26 08:18 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.11.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    워밍업 1회와 측정 5회, 표는 중앙값(밀리초)을 표시합니다.

## 한눈에 보기 { #overview }

| 작업 | 최신 코퍼스의 결과 |
|---|---|
| 실제 PDF 10개 병합 | pylopdf **36.6 ms**, pymupdf 131.8 ms, pypdf 426.3 ms |
| 첫 페이지를 2×로 렌더링 | 코퍼스의 10개 파일 모두 pylopdf가 가장 빨랐음 |
| 12페이지를 2×로 렌더링 | `render_pages()`가 317.4 ms(1 worker)에서 81.5 ms(8 workers)로 줄어 **3.89배 가속** |
| 전체 텍스트 추출 | 4개는 pylopdf, 6개는 pymupdf가 가장 빨랐음 |
| 추출 충실도의 대용 지표 | 읽기 순서 규칙에 따라 유사도 0.121~1.000 |

## 텍스트 추출 { #text-extraction }

모든 페이지, 밀리초 단위이며 낮을수록 빠릅니다.

| 파일 | pylopdf | pymupdf | pypdf | pdfplumber |
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

## 추출 내용 { #extraction-content }

이 값은 정확도 점수가 아니라 대용 지표입니다. 공백을 정규화한 텍스트를 pymupdf와
비교합니다. 폼과 OCR 레이어에서 유사도가 낮더라도 문자 수가 일치한다면 읽기 순서나
공백 정책의 차이일 수 있습니다.

| 파일 | pylopdf 문자 수 | pymupdf 문자 수 | 유사도 |
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

## 병합 { #merge }

| 작업 | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| 코퍼스 파일 10개 모두 병합 | **36.6** | 131.8 | 426.3 |

## 렌더링 { #rendering }

첫 페이지를 2× PNG로 변환한 밀리초이며 낮을수록 빠릅니다.

| 파일 | pylopdf | pymupdf |
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

## 병렬 렌더링 { #parallel-rendering }

`usrguide.pdf`의 첫 12페이지를 2× PNG로 변환한 밀리초이며 낮을수록 빠릅니다.
묶음은 입력 순서를 유지하고 하나의 불변 문서 스냅샷을 사용합니다.

| Workers | 시간 | 1 worker 대비 |
|---:|---:|---:|
| 1 | 317.4 | 1.00배 |
| 2 | 179.6 | 1.77배 |
| 4 | 99.1 | 3.20배 |
| 8 | 81.5 | 3.89배 |

실제 동시성은 요청한 worker 수와 약 512 MB의 추정 실시간 렌더링 메모리로 제한됩니다.

## free-threaded 추출 { #free-threaded-extraction }

Windows 11의 free-threaded CPython 3.14.6에서 서로 독립된 `bill-hr815.pdf`
두 개의 전체 페이지 텍스트를 추출했습니다. 한 번의 warmup 후 먼저 실행할 모드를
번갈아 바꾼 일곱 쌍 실행의 중앙값입니다.

| 모드 | Workers | 시간 | 속도 향상 |
|---|---:|---:|---:|
| 순차 | 1 | 280.3 ms | 1.00배 |
| 병렬 | 2 | 160.8 ms | 1.74배 |

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
# free-threaded CPython 3.14 interpreter에서:
python3.14t bench/free_threaded.py
```

생성된 원본 보고서는
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)에
커밋됩니다. native/Pyodide resource-policy 기준값은
[`bench/results/limits-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/limits-latest.md)에
별도로 커밋됩니다. 두 번째 command는 제한된 open/extract와 제어된 거부를 측정합니다.
CI는 같은 case를Pyodide에서도 실행하고 Wasm linear memory 증가를 기록합니다.
이 시간과memory 값은 추세이며 native/Wasm 성능 비교 주장이 아닙니다. 수치를
인용할 때는 환경과 코퍼스도 함께 적어 주세요.
