---
title: WebAssembly
description: Cloudflare Python Workers와 Pyodide에서 pylopdf를 실행합니다. 테스트한 버전과 네이티브 Python과 다른 기능을 정리합니다.
---

# WebAssembly

pylopdf는 Cloudflare Python Workers와 Pyodide에서 동작하는 WebAssembly wheel을 배포합니다. 네이티브 wheel과 같은 Rust 코드를 담고 있어 API와 결과가 같으며, JavaScript PDF 라이브러리를 사용하지 않습니다.

## 지원 환경 { #supported-environments }

| 환경 | 상태 |
|---|---|
| Cloudflare Python Workers | 지원. 모든 릴리스에서 배포된 wheel로 Worker를 빌드하고 시작해 테스트합니다 |
| Pyodide 0.28.3 | wheel의 태그를 바꾸면 지원(아래 참고). 전체 호환성 테스트 스위트를 그 안에서 실행합니다 |
| Pyodide 0.28.3의 `micropip`으로 PyPI에서 설치 | 미지원. 해당 `micropip`은 wheel의 `pyemscripten` 플랫폼 태그를 인식하지 못합니다 |
| 그 밖의 Pyodide 또는 WebAssembly Python 버전 | 테스트하지 않음 |

wheel은 Python 3.13과 `pyemscripten_2025_0_wasm32` 플랫폼을 대상으로 하며, 이는 Pyodide 0.28.3과 Cloudflare의 `workers-py` 1.15.0이 사용하는 런타임입니다. 브라우저나 Worker 안에서 소스 빌드로 대체하는 경로는 없습니다.

## Cloudflare Worker { #cloudflare-worker }

저장소에는 PDF를 받아 페이지 수와 첫 페이지의 텍스트를 반환하는 [테스트된 예제 Worker](https://github.com/yhay81/pylopdf/tree/main/examples/cloudflare-worker)가 있습니다.

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf/examples/cloudflare-worker
uv sync
uv run pywrangler dev
```

다른 터미널에서:

```bash
curl http://localhost:8787/health

curl --request POST \
  --header "content-type: application/pdf" \
  --data-binary @document.pdf \
  http://localhost:8787
```

`uv run pywrangler deploy`로 배포합니다. pylopdf를 포함한 Worker는 Workers Free 플랜의 압축 크기 제한인 3 MB보다 크므로 Workers Paid 플랜이 필요합니다.

Worker의 128 MiB 메모리 예산은 Python, JavaScript, 요청 자체가 함께 쓰기 때문에, 예제는 입력을 4 MiB로 제한하고 `DocumentLimits.web()`보다 엄격한 [`DocumentLimits`](security.md#untrusted-pdfs)를 설정합니다. pylopdf는 요청 본문 전체를 읽으므로, 직접 작성한 코드에 메모리가 더 필요하다면 제한을 더 낮추세요.

## Pyodide { #pyodide }

Pyodide 0.28.3의 `micropip`은 배포된 wheel의 `pyemscripten` 태그를 받아들이지 않지만, 안에 든 바이너리는 Pyodide 0.28.3에서 동작합니다. CI는 같은 바이너리에 `pyodide_2025_0_wasm32` 태그를 붙여 전체 테스트를 실행합니다. wheel을 내려받은 뒤 `wheel` 도구로 태그를 바꾸세요. 이 도구는 wheel 안의 메타데이터도 함께 고칩니다(파일 이름만 바꾸는 것으로는 부족합니다).

```bash
pip download pylopdf --no-deps --only-binary=:all: \
  --platform pyemscripten_2025_0_wasm32 --python-version 3.13 -d wheels
pip install wheel
wheel tags --platform-tag pyodide_2025_0_wasm32 --remove wheels/pylopdf-*.whl
```

그런 다음 태그를 바꾼 wheel을 Pyodide가 접근할 수 있는 URL에서 제공하고 `micropip`으로 설치합니다.

```javascript
const pyodide = await loadPyodide();
await pyodide.loadPackage("micropip");
await pyodide.runPythonAsync(`
import micropip
await micropip.install(
    "https://example.com/pylopdf-0.15.0-cp311-abi3-pyodide_2025_0_wasm32.whl"
)
`);
```

`tools/build_pyodide.sh`로 소스에서 빌드한 wheel([CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md#pyodide-0283-build) 참고)도 같은 `pyemscripten` 태그를 가지므로 같은 단계가 필요합니다.

파일 경로는 Pyodide의 가상 파일 시스템을 가리키므로, 브라우저의 데이터는 bytes로 전달합니다.

```python
import pylopdf

with pylopdf.open(pdf_bytes, limits=pylopdf.DocumentLimits.web()) as doc:
    text = doc.pages[0].get_text() if doc.pages else ""
    output = doc.tobytes()
```

WebAssembly 런타임인지 확인하려면 `sys.platform == "emscripten"`을 검사하세요.

## 네이티브 Python과의 차이 { #differences }

추출, 렌더링, 편집, 텍스트 생성, 폼, 암호화를 포함한 나머지 기능은 모두 네이티브 Python과 같이 동작합니다.

| 기능 | WebAssembly에서 |
|---|---|
| `render_pages(workers=...)` | 받아들이지만 페이지를 하나씩 차례로 렌더링 |
| 내장 OCR(`OcrEngine`, `get_text_ocr`, `apply_ocr`) | 사용 불가. `OcrError`가 발생합니다. 다른 곳에서 OCR을 실행하고 그 결과를 `Page.insert_ocr_text_layer()`로 추가하세요 |
| 글꼴 extra(`pylopdf[jp]` 등) | 테스트하지 않음. 글꼴 데이터를 `fontbuffer=`나 `set_fallback_font()`로 직접 전달하세요 |

큰 페이지를 높은 해상도로 렌더링하면 PDF 파일 자체가 작더라도 이미지 전체가 메모리에 필요하며, 리소스 제한은 오래 실행되는 호출을 멈추지 않습니다. 네이티브 Python과 마찬가지로 호스트에서 시간 제한을 강제하세요.

## 크기와 테스트 { #size-and-testing }

각 릴리스는 같은 호환성 스위트를 네이티브와 Pyodide 안에서 실행하고 결과가 동일한지 확인합니다. 이 스위트는 bytes에서 열기, 암호화된 파일, 텍스트, 단어, 검색, Markdown, 일본어 세로쓰기, 다단 페이지, 표, 벡터 그림, 렌더링, 표준 글꼴과 포함 글꼴로 생성한 텍스트, 편집, 저장, 오류 처리를 다룹니다. 크기, 시작 시간, 메모리 사용량은 CI에서 측정해 [`bench/results/wasm-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)에 공개합니다.

새 Pyodide, Emscripten, `workers-py`, Wrangler 버전은 전체 스위트를 통과한 뒤에만 지원합니다.
