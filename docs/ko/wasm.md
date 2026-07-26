# WebAssembly 호환성

pylopdf는 Pyodide 0.28.3과 Cloudflare Python Workers가 사용하는 Python 3.13 ABI용
static PyEmscripten wheel을 빌드합니다. native wheel과 같은 Rust PDF engine을 포함하며
JavaScript PDF 구현이나 wasm-bindgen shim을 사용하지 않습니다.

!!! note "릴리스 상태"

    WebAssembly wheel은 pylopdf 0.11부터 배포되었습니다. v0.10.0은 native wheel만
    포함합니다.

## 검증한 환경

| Component | 고정 version 또는 계약 |
|---|---|
| Python runtime | CPython 3.13.2 |
| Pyodide | 0.28.3 |
| Emscripten toolchain | 4.0.9 |
| Wheel platform | PyEmscripten `2025_0`, `wasm32` |
| Build/smoke Node.js | 고정 Emscripten SDK의 20.18.0 |
| Cloudflare SDK | `workers-py` 1.15.0 |
| Cloudflare bundler | Wrangler 4.114.0 |
| Worker compatibility date | 2026-07-26 |

배포 artifact는 `cp310-abi3-pyemscripten_2025_0_wasm32`를 사용합니다. builder는 같은
binary를 runtime-native `pyodide_2025_0_wasm32` tag로 먼저 실행하고 PEP 783 배포용으로
결정적으로 retag합니다. PyEmscripten tag artifact만 PyPI, provenance attestation,
release SBOM에 포함됩니다.

| 환경 | 상태 | 세부 내용 |
|---|---|---|
| Cloudflare Python Workers | 지원, release gate 적용 | CI가 고정 SDK로 PEP 783 wheel을 해석하고 Wrangler bundle을 만든 뒤 local `workerd`를 시작하여 `/health`로 module-scope import를 검증합니다. tag 릴리스는 PyPI에서 같은 검증을 반복한 뒤 GitHub Release를 만듭니다. |
| Node.js의 Pyodide 0.28.3 | runtime 호환 gate 적용 | CI가 runtime-tagged local wheel을 정확히 고정한 runtime에 설치하고 전체 공유 호환성 suite를 실행합니다. |
| PyPI에서 browser로 직접 설치 | Pyodide 0.28.3에서는 미지원 | 이 버전의 `micropip`은 PyPI가 요구하는 PEP 783 `pyemscripten_*` tag보다 오래되었습니다. binary는 호환되지만 frontend 설치 경로는 호환되지 않습니다. |
| 다른 Pyodide / Python-Wasm 버전 | 미검증 | wheel tag나 지원 범위를 넓히기 전에 platform과 ABI를 검증해야 합니다. |

## Cloudflare Worker

repository에는
[검증된 extraction Worker](https://github.com/yhay81/pylopdf/tree/main/examples/cloudflare-worker)가
있습니다. 제한을 둔 PDF body를 받아 page 수와 첫 page text를 반환합니다.

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf/examples/cloudflare-worker
uv sync
uv run pywrangler dev
```

다른 terminal에서 PDF를 보냅니다.

```bash
curl http://localhost:8787/health

curl --request POST \
  --header "content-type: application/pdf" \
  --data-binary @document.pdf \
  http://localhost:8787
```

선택한 Cloudflare plan의 제한과 compatibility date를 검토한 다음
`uv run pywrangler deploy`를 사용하십시오. CI는 이 example 자체를 복사하고 공개
pylopdf requirement만 방금 빌드한 wheel로 교체한 뒤 `workers-py`로 해결하고
`wrangler deploy --dry-run`을 실행한 다음 local `workerd`를 시작하고 `/health`를
요청합니다. 따라서 module-scope `import pylopdf`는 시작 시 사용할 수 없는 entropy나
request 전용 runtime state에 의존하지 않고 완료되어야 합니다.

example은 입력을 4 MiB로 제한하고 structure와 압축 해제 data에
`DocumentLimits.web()`보다 엄격한 budget을 둡니다. pylopdf는 현재 path 또는 완전한
bytes를 받으므로 request body 전체를 buffer합니다. Cloudflare의 128 MiB isolate
budget에는 Python, JavaScript, WebAssembly linear memory, request buffer도 포함되므로
주변 코드가 더 많은 공간을 사용하면 file budget을 더 낮추십시오.

## Pyodide에서 직접 사용

Pyodide 0.28.3 개발 환경에서는 `tools/build_pyodide.sh`로 빌드하고 runtime에서 접근
가능한 URL의 runtime-tagged wheel을 설치합니다.

```javascript
const pyodide = await loadPyodide();
await pyodide.loadPackage("micropip");
await pyodide.runPythonAsync(`
import micropip
await micropip.install(
    "https://example.invalid/pylopdf-0.11.1-"
    "cp310-abi3-pyodide_2025_0_wasm32.whl"
)
`);
```

URL은 예시입니다. release artifact는 PyPI와 Cloudflare용 PEP 783
`pyemscripten_2025_0_wasm32` tag를 사용하며 Pyodide 0.28.3의 오래된 `micropip`은 이
공개 tag를 받아들이지 않습니다. 내부 `WHEEL` metadata를 바꾸지 않고 파일 이름만
변경하지 마십시오.

browser 또는 Worker 안에서 지원되는 sdist fallback은 없습니다. extension 빌드에는
고정 Rust, Emscripten, Pyodide cross environment, retag verifier가 필요합니다. 향후
ABI에 맞는 wheel이 없다면 runtime package installer가 sdist를 컴파일하게 하지 말고
해당 ABI를 미지원으로 처리하십시오.

application은 `sys.platform == "emscripten"`으로 runtime을 판별할 수 있습니다.
path는 browser file에 직접 연결되지 않고 virtual filesystem을 가리킵니다.

```python
import sys
import pylopdf

assert sys.platform == "emscripten"
with pylopdf.Document(stream=pdf_bytes, limits=pylopdf.DocumentLimits.web()) as doc:
    text = doc.get_page_text(0) if doc.page_count else ""
    output = doc.tobytes()
```

## 검증한 API

native/Wasm 공유 suite는 현재 다음을 검증합니다.

- host filesystem이 없는 `bytes` 입력, page 수, PDF 2.0, AES-256 암호화 입력
- plain text, word, dict, 검색, document Markdown, embedded Japanese, 추론한
  vertical CJK, 지속적인 multi-column 순서, image-only page, 회전 page
- bordered 및 보수적인 borderless table, Markdown 통합, vector drawing 추출
- 빈 document, Standard 14와 subset-embedded OpenType text, textbox layout,
  rendering, `Pixmap`, serialization, virtual-filesystem save, merge, reorder,
  duplicate, select
- `PdfError`, stable resource code가 있는 `LimitError`, `PasswordError`,
  `EncryptedDocumentError`, `DocumentClosedError`, `StalePageError`와 malformed
  input 이후 runtime 재사용
- `render_pages(workers=4)`의 입력 순서와 `workers=1`의 byte 일치

fixture에는 PDF 2.0, CJK가 embedded된 일본 정부 문서, IRS Form 1040, 회전한 미국 상원
표, image-only 일본어 scan, 생성한 세로쓰기 문서가 포함됩니다. 저장된 PDF는 모두
1 MiB 미만이며 재배포 가능한 license를 corpus README에 기록합니다.

같은 suite를 native wheel과 Pyodide에서 각각 실행하고 논리 결과의 완전한 일치를
요구합니다. 명시적인 structure와 기대 text뿐 아니라 전체 extraction 및 Markdown
hash도 검사합니다.

## 기능 및 의존성

Wasm wheel은 부분적으로 호환되는 여러 variant로 나누지 않고 하나의 artifact를
유지합니다.

| 기능 | Rust component | Wasm 상태 |
|---|---|---|
| PDF structure, 편집, 암호화 | lopdf | 포함 |
| text, table, vector path | hayro syntax/interpreter와 CMap | 포함 |
| PNG raster rendering | hayro, Vello, PNG encoding | 포함 |
| SVG rendering | hayro SVG backend | 포함 |
| 생성 text와 form appearance | krilla, HarfRust, read-fonts, UAX line breaking | 포함 |
| image와 JPEG 압축 | Flate, zune-jpeg, jpeg-encoder | 포함 |
| 같은 document의 병렬 rendering | rayon | native 전용, Wasm은 serial 실행 |
| PP-OCRv6 inference | RTen과 외부 model wheel | native 전용, Wasm binary에서 제외 |
| 자동 CJK fallback 탐색 | 외부 CJK font wheel과 host path | Wasm 호환 계약 외 |

결정적인 capability 확인을 위해 `OcrEngine()`은 존재하지만 Emscripten에서는
`OcrError`를 발생시키고 Wasm 외부에서 OCR한 뒤 `Page.insert_ocr_text_layer()`를
사용하도록 안내합니다. 사용하지 않는 RTen inference runtime을 제거해도 PDF extraction,
rendering, 생성 또는 외부 OCR text 삽입은 제거되지 않습니다.

## 측정한 deployment 범위

고정 CI artifact는 3.834 MiB wheel과 10.404 MiB 압축 해제 Wasm extension입니다.
검증한 Worker bundle은 압축 시 3.882 MiB, 압축 해제 시 10.844 MiB입니다. 따라서
Cloudflare Workers Free의 3 MB 압축 제한은 넘지만 paid plan의 10 MB 압축 제한과 공통
64 MB 비압축 제한에는 맞습니다. pylopdf는 paid-plan deployment 경로를 지원하며 기능을
줄인 별도 distribution은 배포하지 않습니다.

Node/Pyodide harness에서 Form 1040 첫 open/extraction은 116.267 ms, 5회 반복 median은
26.893 ms였습니다. Wasm linear memory는 설치 후 40.375 MiB, 전체 호환성 및 resource
suite 후 70.625 MiB에 도달했습니다. 이는 재현 가능한 CI trend이며 Cloudflare request
latency나 isolate resident-memory 측정은 아닙니다.
[전체 size 및 startup report](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)를
참조하십시오.

## Runtime 제한

- 이 Emscripten build에는 rayon worker pool이 없습니다.
  `render_pages(workers=...)`는 같은 인수를 받지만 serial로 실행합니다.
- `clip=`은 반환 pixel을 줄이지만 hayro는 내부에서 전체 page를 rasterize합니다.
- 현재 renderer는 전체 raster output을 buffer하므로 큰 page나 높은 DPI는 PDF file이
  작아도 memory를 지배할 수 있습니다.
- native OCR와 별도 OCR model package는 WebAssembly 호환 계약에 포함되지 않습니다.
- 외부 CJK fallback font 자동 탐색은 대상이 아닙니다. embedded CJK는 검증하며
  application이 font bytes를 명시적으로 공급할 수 있습니다.
- 현재 gate는 Cloudflare bundle 생성과 module-scope import를 포함한 local `workerd`
  시작을 증명합니다. 인증된 production deploy나 workload별 latency는 보장하지 않습니다.

같은 matrix가 native와 Wasm에서 `DocumentLimits`, `doc.complexity`, Web budget 내
vector/scan 입력, stable file/page/text rejection code를 검증합니다. 정기 native
Atheris fuzzing은 더 큰 생성 hostile corpus를 추가합니다. CPU deadline은 host의
책임입니다. native Python에서 통과했다는 이유로 Wasm에 더 큰 memory budget이 있다고
추정하지 말고 두 runtime 모두 명시적인 policy를 사용하십시오.

## 지원 및 release policy

Wasm wheel을 배포하는 각 pylopdf release는 다음을 모두 통과해야 합니다.

1. reproducible build와 wheel metadata/import verifier
2. native/Pyodide 공유 논리 호환 suite
3. untrusted input 거부 및 resource trend 검사
4. wheel, Wasm section, startup/workload, linear memory 측정
5. local wheel dependency resolution, Cloudflare Wrangler dry-run, local
   `workerd` 시작 및 module-scope-import health request
6. GitHub Release 확정 전 PyPI artifact에서 같은 resolution, bundle 및 runtime health gate

runtime 업데이트는 호환성을 가정하지 않고 새로운 검증 matrix로 다룹니다. 고정 version은
해당 pylopdf minor release에서 지원하며 새로운 Pyodide, PyEmscripten, Emscripten,
`workers-py`, Wrangler는 전체 gate를 통과한 후에만 지원 범위에 포함됩니다. 측정값과
regression은
[`bench/results/wasm-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/wasm-latest.md)에
함께 게시합니다.
