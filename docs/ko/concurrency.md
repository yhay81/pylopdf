---
title: 동시성과 free-threaded Python
description: Document, Page, Pixmap, 병렬 렌더링 및 CPython 3.14t에서 지원하는 스레드 경계입니다.
---

# 동시성과 free-threaded Python

v0.10부터 pylopdf는 GIL이 있는 CPython 3.10–3.14용 플랫폼별 `abi3` wheel과
free-threaded CPython 3.14용 `cp314-cp314t` wheel을 모두 배포합니다. 후자를
import해도 GIL이 다시 활성화되지 않습니다.

## 지원 계약 { #support-contract }

| 사용 방식 | 지원 |
|---|---|
| GIL이 있는 CPython 3.10–3.14 | `abi3-py310` wheel로 지원 |
| free-threaded CPython 3.14t | `cp314-cp314t` wheel로 지원하며 GIL이 꺼진 상태에서 테스트 |
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

## Pixmap 버퍼 { #pixmap-buffers }

free-threaded wheel은 불변 RGBA8 저장소를 읽기 전용, 1차원, zero-copy buffer로
노출합니다.

```python
view = memoryview(page.get_pixmap())
assert view.readonly and view.format == "B"
```

`Py_buffer`는 Python 3.11에서 stable ABI에 포함되었으므로 `abi3-py310` wheel에서는
노출할 수 없습니다. 해당 wheel에서는 한 번 복사되는 `bytes` 값인
`pixmap.samples`를 사용하세요. 이 방식은 buffer 수명과 불변성 보장을 약화하지
않으면서 Python 3.10 호환성을 유지합니다.
