---
title: pyropdf로 이전 안내
description: pylopdf는 아카이브되었습니다. Python에서 PDF 읽기, 렌더링, 편집, 서명은 후속 프로젝트인 pyropdf로 이전해 주세요.
hide:
  - navigation
---

<p class="living-eyebrow">Archived project · 최종 릴리스 0.16.1</p>

# pylopdf에서 pyropdf로 이전하기 { #migration-notice }

`pylopdf`는 아카이브되었으며 더 이상 새 기능, 버그 수정, 보안 업데이트가 제공되지 않습니다. 개발은 새로운 Rust 기반 위에 재구축된 후속 라이브러리 **[pyropdf](https://github.com/yhay81/pyropdf)**에서 이어집니다.

<div class="living-actions">
  <a class="living-button living-button--primary" href="https://pyropdf.haya.works/">pyropdf 문서&nbsp; →</a>
  <a class="living-button" href="https://github.com/yhay81/pyropdf">GitHub (yhay81/pyropdf)</a>
  <a class="living-button" href="https://pypi.org/project/pyropdf/">PyPI (pyropdf)</a>
</div>

<div class="living-install" aria-label="설치 명령">
  <span class="living-install__prompt" aria-hidden="true">$</span>
  <code tabindex="0">pip install pyropdf</code>
  <button
    class="living-install__copy"
    type="button"
    data-copy-command="pip install pyropdf"
    data-copied-label="복사됨"
    aria-label="설치 명령 복사"
  ><span data-copy-label>복사</span></button>
</div>

## pyropdf로 대체되는 이유 { #why }

`pylopdf`는 `lopdf`의 편집 그래프, 직렬화된 바이트를 파싱하는 `hayro`, `krilla`가 생성하는 PDF라는 세 가지 객체 모델을 따로 유지한 뒤 사후에 조율했습니다. 이 때문에 편집할 때마다 `hayro` 스냅샷을 버리고 문서 전체를 다시 직렬화해야 했으며, 트랜잭션은 전체 그래프를 복제했고, `lopdf`가 실수를 32비트 부동소수점으로 저장해 정밀도가 손실되었습니다.

`pyropdf`는 `pylopdf`의 철학과 Python API 설계를 유지하면서 Rust의 **단일 버전 관리 객체 저장소** 위에 새로 구축되었습니다([기반 설계 및 측정 결과](https://github.com/yhay81/pyropdf/blob/main/DESIGN.md)):

- **수정한 만큼만 드는 편집 비용.** 모든 엔진이 하나의 저장소에서 현재 버전을 읽으므로 편집 후 문서 전체를 다시 직렬화하지 않습니다.
- **버전 관리와 실행 취소.** 모든 편집은 불변 버전을 만들며, 실패한 트랜잭션은 문서를 복제하지 않고 원래 상태로 되돌아가고 `doc.history`로 실행 취소 및 다시 실행이 가능합니다.
- **무손실 저장과 변경 리포트.** 수정하지 않은 객체는 원본 바이트를 유지하고, `incremental=True` 증분 저장은 변경된 객체만 덧붙여 기존 전자서명을 유지하며, `doc.changes`가 저장 전에 깨질 수 있는 서명이나 PDF/A·PDF/UA 적합성 선언을 알려 줍니다.
- **확장된 PDF 기능.** 전자서명 및 검증, 콘텐츠 정화(sanitize), 색상 변환, 캔버스의 가변 글꼴 및 컬러 글꼴, Arlington PDF Model에서 생성된 타입 객체 모델을 지원합니다.

## 이전 방법 { #migrate }

패키지를 교체하고 `import` 문을 변경합니다:

```bash
pip uninstall pylopdf
pip install pyropdf
```

```python
import pyropdf

with pyropdf.open("input.pdf") as doc:
    page = doc.pages[0]
    text = page.text()
    hits = page.search("합계")
    page.pixmap(dpi=150).save("page.png")

    with doc.transaction("annotate"):
        page.annotations.add(pyropdf.Highlight(hits))
    doc.save("output.pdf", incremental=True)
```

- **`pylopdf` 0.16에서 이전:** `import pylopdf`를 `import pyropdf`로 바꾸면 대부분의 문서, 페이지, 추출, 렌더링, 캔버스, 주석, 폼, 목차, 첨부 파일, 레이어 호출이 그대로 동작합니다. 아래 차이점을 확인해 주세요.
- **`pylopdf` 0.15 이하 또는 `pymupdf`에서 이전:** 기계적인 코드 변환이 먼저 필요하다면 `pylopdf[migrate]==0.16.1`의 `python -m pylopdf.migrate src/`로 0.16 API 형태로 변환한 뒤 `import pylopdf`를 `import pyropdf`로 변경하세요.

## pylopdf 0.16과의 주요 차이점 { #differences }

| 항목 | `pylopdf` 0.16 | `pyropdf` |
|---|---|---|
| import 및 경고 클래스 | `import pylopdf`, `PylopdfWarning` | `import pyropdf`, `PyropdfWarning` |
| 선택 의존성(extras) | `ocr`, `jp`, `ko`, `zh-cn`, `zh-tw`, `ar`, `he`, `hi`, `th`, `migrate` | `ocr`, `jp`, `ko`, `zh-cn`, `zh-tw`(동일한 데이터 패키지 재사용). 그 외 문자 글꼴은 `pyropdf.Font.from_file(...)` 사용 |
| 페이지 박스 | `page.boxes.media`, `.crop`, `.bleed`, `.trim`, `.art`(PDF 사용자 공간) | `page.media_box`, `page.crop_box`, `page.bleed_box`, `page.trim_box`, `page.art_box`(표시 좌표계) |
| 새 문서 생성 | `pylopdf.Document()` 또는 `pylopdf.create()` | `doc = pyropdf.Document()`, `doc.pages.new(...)` 또는 `doc.pages.new_from_image(...)`, `page.draw()`로 일원화 |
| `doc.save()` 기본값 | 기본적으로 스트림 압축 및 미사용 객체 정리 수행(`compress=True, prune=True`) | 기본적으로 수정되지 않은 객체를 그대로 보존(`compress=False, prune=False`). 용량 최적화 시 `compress=True, prune=True`, 증분 저장 시 `incremental=True` 지정 |
| 신뢰할 수 없는 입력 | 중첩 검사 + `DocumentLimits.web()` | 업스트림 경계 생성 기능이 배포되기 전까지는 [상태 및 안전성 안내](https://pyropdf.haya.works/status/)와 [워커 프로세스 제한 가이드](https://pyropdf.haya.works/limits/#reading-a-pdf-in-a-worker)를 따르세요 |
| WebAssembly | Pyodide 0.28.3용 wheel 배포 | [pyropdf 로드맵](https://github.com/yhay81/pyropdf/issues/1)에서 추적 중. 기존 `pylopdf` 0.16 wheel은 PyPI와 GitHub Releases에서 계속 설치 가능 |

## 링크 { #links }

- **pyropdf 문서:** <https://pyropdf.haya.works/> ([`llms.txt`](https://pyropdf.haya.works/llms.txt))
- **pyropdf 저장소 및 이슈:** <https://github.com/yhay81/pyropdf>
- **pyropdf (PyPI):** <https://pypi.org/project/pyropdf/>
- **pyropdf 기반 설계 문서:** <https://github.com/yhay81/pyropdf/blob/main/DESIGN.md>
- **아카이브된 pylopdf 저장소 및 릴리스:** <https://github.com/yhay81/pylopdf> · [PyPI (`pylopdf`)](https://pypi.org/project/pylopdf/) · [변경 이력](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md)
