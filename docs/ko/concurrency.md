---
title: 동시성과 free-threaded Python
description: Document, Page, Pixmap, 병렬 렌더링 및 CPython 3.14t와 3.15t에서 지원하는 스레드 경계입니다.
---

# 동시성과 free-threaded Python

pylopdf는 GIL이 있는 CPython 3.11–3.15용 플랫폼별 `abi3` wheel과 free-threaded
CPython용 Python 버전별 wheel을 배포합니다. `cp314-cp314t`는 v0.10부터,
`cp315-cp315t`는 v0.15부터 제공합니다. free-threaded wheel을
import해도 GIL이 다시 활성화되지 않습니다.

## 지원 계약 { #support-contract }

| 사용 방식 | 지원 |
|---|---|
| GIL이 있는 CPython 3.11–3.15 | `abi3-py311` wheel로 지원 |
| free-threaded CPython 3.14t와 3.15t | `cp314-cp314t`와 `cp315-cp315t` wheel로 지원하며 GIL이 꺼진 상태에서 테스트 |
| 서로 다른 `Document` 객체의 동시 작업 | 지원. load, save, render, 추출, merge, 압축의 무거운 작업은 GIL을 해제 |
| 같은 `Document`의 동시 호출 또는 편집 | 지원하지 않음. lock으로 직렬화하거나 독립된 Document를 사용 |
| 한 Document의 `Document.render_pages(workers=...)` | 지원. 같은 Document 안에서 경계가 정해진 병렬 렌더링 방식 |
| `Pixmap` 동시 읽기 | 지원. `Pixmap`은 불변 |

`Page`는 부모 `Document`를 보는 view이므로 같은 Document 규칙을 따릅니다. 외부의
동시 접근은 PyO3의 런타임 borrow 검사에서 거부될 수 있지만, 이 검사를 동기화 수단으로
사용해서는 안 됩니다.

## 올바른 경계 선택 { #choose-the-right-boundary }

서로 독립된 파일은 worker마다 독립된 Document를 사용합니다.

```python
from concurrent.futures import ThreadPoolExecutor

import pylopdf


def extract(path: str) -> str:
    with pylopdf.open(path) as document:
        return document.to_markdown()


with ThreadPoolExecutor() as pool:
    results = list(pool.map(extract, paths))
```

한 Document의 여러 페이지를 처리할 때는 외부 스레드에서 같은 Document를 호출하지
말고 `render_pages()`를 사용합니다.

```python
png_pages = document.render_pages(scale=2, workers=4)
```

`render_pages()`는 하나의 불변 렌더러 snapshot을 사용하고 요청한 페이지 순서와 중복을
유지하며, 추정 동시 작업 메모리를 약 512 MB로 제한합니다.

## 반복 렌더링 { #repeated-rendering }

Document를 연 스레드에서 `render_page()`, `Page.get_pixmap()`, `Page.render()`,
직렬 `render_pages()`를 반복 호출하면 이전 호출에서 파싱한 font, 색 공간, glyph
윤곽선을 재사용하며, 출력은 새로 연 Document와 동일합니다. 각 스레드는 이러한
cache를 최대 하나 유지하고, 호출 사이에는 서로 다른 페이지 최대 64개 분량만
유지합니다. Document를 편집하거나 fallback font를 변경하거나 닫으면 cache가
무효화되며, 연 스레드에서 이 작업을 하거나 그 스레드에서 Document를 해제하면 즉시
해제됩니다. 다른 스레드에서 무효화된 cache는 해당 스레드가 다음에 렌더링하거나
종료할 때 해제됩니다. 다른 스레드의 호출과 병렬 `render_pages()` worker는 호출 단위
cache를 사용합니다.

## Pixmap 버퍼 { #pixmap-buffers }

모든 wheel은 불변 RGBA8 저장소를 읽기 전용, 1차원, zero-copy buffer로
노출합니다.

```python
view = memoryview(page.get_pixmap())
assert view.readonly and view.format == "B"
```

view가 Pixmap을 유지하므로 NumPy는 `np.frombuffer(pixmap, dtype=np.uint8)`로
픽셀을 복사 없이 읽을 수 있습니다. 독립된 `bytes` 복사본이 필요하면 계속
`pixmap.samples`를 사용할 수 있습니다. v0.15부터는 Python 3.11 이상이
필요하고 그 stable ABI에는 `Py_buffer`가 포함되므로 abi3 wheel도 같은 buffer를
노출합니다. v0.14.0 이하에서는 free-threaded wheel만 이 buffer를 노출했습니다.
