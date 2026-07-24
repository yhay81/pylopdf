---
title: 벤치마크
description: 추출, 병합 및 렌더링에서의 pylopdf 성능을 재현 가능하게 측정하고 장단점을 함께 공개합니다.
---

# 벤치마크

pylopdf는 **빠른 결과와 느린 결과를 함께** 공개합니다. 이 측정은 특정 컴퓨터와
코퍼스의 스냅샷이며 보편적인 순위가 아닙니다. 여러분의 작업에서 무엇을 측정할지
판단하는 자료로 사용하세요.

!!! info "최신 실행"
    **2026-07-24 12:59 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.9.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    워밍업 1회와 측정 5회, 표는 중앙값(밀리초)을 표시합니다.

## 한눈에 보기 { #overview }

| 작업 | 최신 코퍼스의 결과 |
|---|---|
| 실제 PDF 7개 병합 | pylopdf **40.6 ms**, pymupdf 127.1 ms, pypdf 366.3 ms |
| 첫 페이지를 2×로 렌더링 | 코퍼스의 7개 파일 모두 pylopdf가 가장 빨랐음 |
| 12페이지를 2×로 렌더링 | `render_pages()`가 400.8 ms(1 worker)에서 83.6 ms(8 workers)로 줄어 **4.80배 가속** |
| 전체 텍스트 추출 | 4개는 pylopdf, 3개는 pymupdf가 가장 빨랐음 |
| 추출 충실도의 대용 지표 | 읽기 순서 규칙에 따라 유사도 0.292~1.000 |

## 텍스트 추출 { #text-extraction }

모든 페이지, 밀리초 단위이며 낮을수록 빠릅니다.

| 파일 | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **138.1** | 179.5 | 848.4 | 9850.9 |
| f1040.pdf | **16.8** | 33.4 | 176.3 | 572.9 |
| mhlw-doc.pdf | 18.4 | **11.3** | 109.3 | 195.3 |
| patent-us223898.pdf | 29.5 | **6.8** | 81.4 | 512.9 |
| pdf20-simple.pdf | **0.3** | 1.1 | 1.8 | 2.2 |
| usrguide.pdf | 144.9 | **50.7** | 665.2 | 1756.4 |
| wdl6812-manuscript.pdf | **0.3** | 0.7 | 1.4 | 2.2 |

## 추출 내용 { #extraction-content }

이 값은 정확도 점수가 아니라 대용 지표입니다. 공백을 정규화한 텍스트를 pymupdf와
비교합니다. 폼과 OCR 레이어에서 유사도가 낮더라도 문자 수가 일치한다면 읽기 순서나
공백 정책의 차이일 수 있습니다.

| 파일 | pylopdf 문자 수 | pymupdf 문자 수 | 유사도 |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## 병합 { #merge }

| 작업 | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| 코퍼스 파일 7개 모두 병합 | **40.6** | 127.1 | 366.3 |

## 렌더링 { #rendering }

첫 페이지를 2× PNG로 변환한 밀리초이며 낮을수록 빠릅니다.

| 파일 | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **38.2** | 86.2 |
| f1040.pdf | **53.1** | 94.7 |
| mhlw-doc.pdf | **35.7** | 70.1 |
| patent-us223898.pdf | **36.4** | 69.0 |
| pdf20-simple.pdf | **9.0** | 18.9 |
| usrguide.pdf | **31.8** | 56.6 |
| wdl6812-manuscript.pdf | **45.5** | 87.0 |

## 병렬 렌더링 { #parallel-rendering }

`usrguide.pdf`의 첫 12페이지를 2× PNG로 변환한 밀리초이며 낮을수록 빠릅니다.
묶음은 입력 순서를 유지하고 하나의 불변 문서 스냅샷을 사용합니다.

| Workers | 시간 | 1 worker 대비 |
|---:|---:|---:|
| 1 | 400.8 | 1.00배 |
| 2 | 200.5 | 2.00배 |
| 4 | 118.5 | 3.38배 |
| 8 | 83.6 | 4.80배 |

실제 동시성은 요청한 worker 수와 약 512 MB의 추정 실시간 렌더링 메모리로 제한됩니다.

## free-threaded 추출 { #free-threaded-extraction }

Windows 11의 free-threaded CPython 3.14.6에서 서로 독립된 `bill-hr815.pdf`
두 개의 전체 페이지 텍스트를 추출했습니다. 한 번의 warmup 후 먼저 실행할 모드를
번갈아 바꾼 일곱 쌍 실행의 중앙값입니다.

| 모드 | Workers | 시간 | 속도 향상 |
|---|---:|---:|---:|
| 순차 | 1 | 341.8 ms | 1.00배 |
| 병렬 | 2 | 195.9 ms | 1.74배 |

모든 실행에서 두 문서의 출력이 정확히 일치했고, interpreter는 import 후에도
GIL이 비활성 상태임을 확인했습니다.

## 재현 방법 { #reproduce }

코퍼스는 `tests/assets/real_world`에 있으며, 출처와 라이선스도 같은 위치에
기록되어 있습니다.

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
# free-threaded CPython 3.14 interpreter에서:
python3.14t bench/free_threaded.py
```

생성된 원본 보고서는
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md)에
커밋됩니다. 수치를 인용할 때는 환경과 코퍼스도 함께 적어 주세요.
