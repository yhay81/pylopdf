---
title: 동시성과 free-threaded Python
description: 여러 스레드에서 pylopdf를 사용하는 방법, 페이지를 병렬로 렌더링하는 방법, free-threaded CPython 3.14t와 3.15t에서 실행하는 방법을 설명합니다.
---

# 동시성과 free-threaded Python

pylopdf는 열기, 저장, 렌더링, 추출, 병합, 압축 같은 무거운 작업 중에 GIL을 해제하므로, 일반 CPython에서도 스레드가 병렬로 실행될 수 있습니다. free-threaded CPython 3.14t와 3.15t에는 GIL을 비활성 상태로 유지하는 전용 wheel이 있습니다.

## 지원 범위 { #support-contract }

| 사용 방식 | 지원 여부 |
|---|---|
| 서로 다른 스레드가 서로 다른 `Document` 객체를 다룸 | 지원 |
| 여러 스레드가 같은 `Document`를 동시에 호출하거나 편집 | 미지원. 잠금을 사용하거나 스레드마다 파일을 따로 여세요 |
| 한 문서에서 `Document.render(workers=...)` | 지원. 한 문서를 병렬로 렌더링하는 공식 방법입니다 |
| 여러 스레드가 같은 `Pixmap`을 읽음 | 지원. `Pixmap`은 변경할 수 없습니다 |
| free-threaded CPython 3.14t와 3.15t | 지원. GIL을 비활성화한 상태로 테스트합니다 |

`Page`는 자신의 `Document`에 속하며 같은 규칙을 따릅니다. pylopdf가 한 문서에 대한 동시 호출을 거부할 수도 있지만, 이를 잠금 대신 믿어서는 안 됩니다.

## 여러 파일 처리하기 { #many-files }

각 작업자에게 자기 문서를 하나씩 줍니다.

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.markdown()


paths = ["a.pdf", "b.pdf", "c.pdf"]
with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

## 한 문서의 여러 페이지 렌더링하기 { #render-pages }

여러 스레드에서 한 문서를 호출하는 대신 `doc.render()`를 사용하세요.

```python
with pylopdf.open("report.pdf") as document:
    png_pages = document.render(dpi=144, workers=4)
```

요청한 페이지 순서대로 PNG bytes를 반환하며, 페이지가 크면 작업자 수를 줄여 추정 작업 메모리를 약 512 MB 이하로 유지합니다.

## 반복 렌더링 { #repeated-rendering }

문서를 연 스레드에서 같은 문서의 페이지를 반복해서 렌더링하면, 이전 호출에서 해석한 글꼴과 글리프 윤곽을 재사용합니다. 출력은 새로 연 문서에서 렌더링한 것과 같습니다. 이 캐시는 최대 64페이지를 다루며, 문서를 편집하거나 닫으면 해제됩니다.

## Pixmap과 NumPy { #pixmap-buffers }

`Pixmap`은 픽셀을 읽기 전용 버퍼로 노출하므로 NumPy가 복사 없이 사용할 수 있습니다.

```python
import numpy as np

pix = page.pixmap()
pixels = np.asarray(pix)  # 모양은 (높이, 너비, 채널 수)
```

반면 `bytes(pix)`는 독립된 사본을 반환하고, `Pixmap.from_array(pixels)`는 배열을 다시 pixmap으로 만듭니다.
