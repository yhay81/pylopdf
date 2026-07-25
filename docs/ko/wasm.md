# WebAssembly 호환성

pylopdf는 Pyodide 0.28.3과 Cloudflare Python Workers가 사용하는 Python 3.13
ABI용 정적 PyEmscripten wheel을 빌드합니다. native wheel과 동일한 Rust PDF
engine을 포함하며 JavaScript PDF 구현이나 wasm-bindgen shim에 의존하지 않습니다.

!!! note "릴리스 상태"

    WebAssembly build는 `main`에 구현되어 있으며 v0.10.0 이후 패키지
    릴리스부터 배포됩니다. v0.10.0 자체에는 native wheel만 포함됩니다.

## 배포 상태

| 환경 | 상태 | 설명 |
|---|---|---|
| Cloudflare Python Workers | 릴리스 gate 적용 | CI가 `workers-py` 1.15.0으로 PEP 783 wheel을 해석하고 Wrangler 4.114.0으로 bundle을 dry-run합니다. tag 릴리스는 PyPI에서 다시 받은 뒤 같은 검사를 통과해야 GitHub Release를 만듭니다. |
| Node.js의 Pyodide 0.28.3 | 호환성 gate 적용 | CI가 정확히 고정한 runtime에 로컬 build의 legacy-tag wheel을 설치하고 전체 공유 호환성 suite를 실행합니다. |
| PyPI에서 browser로 직접 설치 | 현재 지원하지 않음 | Pyodide 0.28.3의 `micropip`은 PyPI가 요구하는 PEP 783 `pyemscripten_*` tag보다 오래되었습니다. binary는 호환되지만 안정적인 공개 설치 경로에는 더 새로운 frontend tooling이 필요합니다. |
| 다른 Pyodide / Python-Wasm 버전 | 미검증 | wheel tag나 지원 범위를 넓히기 전에 platform과 ABI를 검증해야 합니다. |

배포 artifact는 `cp310-abi3-pyemscripten_2025_0_wasm32`를 사용합니다. builder는
동일한 binary를 runtime 고유 `pyodide_2025_0_wasm32` tag로 실행한 뒤 PEP 783
tag로 결정적으로 변경합니다. PyEmscripten tag artifact만 PyPI, provenance
attestation, release SBOM에 포함됩니다.

## 검증된 API

native/Wasm 공유 suite는 현재 다음을 검증합니다.

- host filesystem 없는 `bytes` 입력, page count, PDF 2.0, AES-256 암호화 입력
- plain text, words, dict, 검색, 문서 Markdown, embedded Japanese text,
  세로쓰기 CJK 추론, 다단 순서, image-only page, 회전 page
- bordered table, 보수적인 borderless table, Markdown 통합, vector drawing 추출
- 빈 문서, Standard 14와 subset embedded OpenType text, textbox, render,
  `Pixmap`, serialization, 가상 filesystem save, merge, 재정렬, 복제, select
- `PdfError`, 안정적인 리소스 code를 가진`LimitError`, `PasswordError`, `EncryptedDocumentError`,
  `DocumentClosedError`, `StalePageError`와 잘못된 입력 이후 runtime 재사용
- `render_pages(workers=4)` 입력 순서와 `workers=1`의 byte 동일성

fixture에는 PDF 2.0, embedded CJK 일본 정부 문서, IRS Form 1040, 90도 회전한
미국 상원 표, image-only 일본어 scan, 합성 세로쓰기 문서가 포함됩니다.
commit된 PDF는 모두 1 MB 미만이며 재배포 가능한 license를 corpus README에 기록합니다.

같은 suite를 native wheel과 Pyodide에서 각각 실행하고 논리 결과의 완전한 일치를
요구합니다. 주요 문자열과 구조를 명시적으로 확인하면서 전체 추출 text 및 Markdown
hash도 비교합니다.

## runtime 차이와 제한

- 이 Emscripten build에는 rayon worker pool이 없습니다.
  `render_pages(workers=...)`는 일반 인자를 받지만 직렬 실행합니다.
  native build는 제한된 rayon 병렬 처리를 유지합니다.
- path는 browser나 Worker host가 아닌 runtime 가상 filesystem을 가리킵니다.
  application 경계에서는 `Document(stream=data)`와 `tobytes()`를 권장합니다.
- render 제한은 동일합니다. `clip=`은 반환 pixel을 줄이지만 hayro는 내부에서
  전체 page를 rasterize합니다.
- native OCR와 별도 배포 OCR model package는 현재 WebAssembly 호환 계약에 포함되지 않습니다.
- 외부 CJK fallback font 자동 발견은 아직 보장하지 않습니다. embedded CJK는 검증했고
  application이 font bytes를 명시적으로 전달할 수 있습니다.
- 현재 gate는 Cloudflare bundle 생성을 검증하며 인증된 production live deploy는 아닙니다.

같은 matrix가 native와Wasm에서`DocumentLimits`, `doc.complexity`, Web 예산 내의
대표 vector/scan, file/page/text 거부 code를 검증합니다. 정기 native Atheris
fuzzing은 더 큰 생성 hostile corpus도 추가합니다. native Python에서 통과했다고
더 큰 memory budget을 가정하지 말고 두 runtime 모두 명시적 policy를 사용하세요.
