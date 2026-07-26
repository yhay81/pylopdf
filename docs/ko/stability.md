---
title: API 안정성
description: pylopdf의 공개 API 경계, 시맨틱 버전 보장, 사용 중단 수명 주기와 호환성 검토 절차.
---

# API 안정성

pylopdf는 [Semantic Versioning 2.0.0](https://semver.org/)을 따릅니다. 이 페이지는
결과가 PDF 생성기, font, renderer, 지원 runtime에도 의존하는 Python library에서 이
정책이 무엇을 의미하는지 정의합니다.

## 현재 상태 { #current-status }

0.12 API는 **후보 baseline**이며 v1.0 호환성 약속 자체는 아닙니다. 실제 사용으로
검증하는 동안 추가형 개선과 검토된 수정을 계속할 수 있습니다. 다만 지금부터 모든 공개
surface 변경을 감지하고 검토하여 최종 v1.0 경계를 우연이 아닌 명시적 결정으로 만듭니다.

[v1.0 이후](#after-v1)의 보장은 v1.0부터 시작합니다. 그전에도 호환되지 않는 변경과
이전 경로를 release note에 명시하며, 1.0 미만이라는 이유로 조용한 파괴를 허용하지
않습니다.

## 공개 API 경계 { #public-api-boundary }

지원하는 공개 API는 다음과 같습니다.

- `pylopdf.__all__`이 export하는 이름과 `pylopdf.__version__`
- 해당 class에서 문서화한 public member
- callable parameter의 이름, 종류, default와 문서화한 return contract
- 문서화한 상수와 enum 값
- TypedDict의 필수/선택 key, NamedTuple field, public type alias
- 문서화한 예외 계층과 `LimitError.code` 같은 기계 판독 속성

`_`로 시작하는 이름, `pylopdf.pylopdf_core`, Rust 구현 세부 사항, object `repr`,
정확한 예외 message, warning 순서와 문서화하지 않은 속성은 private입니다. 구현
module에서 public object를 import해도 그 module path가 공개 API가 되지는 않습니다.
`pylopdf`에서 import하십시오.

`save()`가 생성한 PDF byte와 정확한 PNG/SVG serialization은 byte 단위 안정 format이
아닙니다. 문서화한 시각적, 구조적, 추출 의미가 contract입니다.

## v1.0 이후 { #after-v1 }

stable release에는 다음 규칙을 적용합니다.

- **major** release는 public API를 제거하거나 호환되지 않게 바꿀 수 있습니다.
- **minor** release는 이전 버전과 호환되는 API와 동작을 추가합니다.
- **patch** release는 public API를 의도적으로 바꾸지 않고 결함을 수정합니다.

public symbol/member 제거 또는 이름 변경, parameter 필수화, positional/keyword 허용
방식의 비호환 변경, mapping key 제거, 입력 범위 축소, 문서화한 상수 변경, public 예외
상속 파괴에는 major release가 필요합니다.

선택 keyword parameter, 새 symbol/method, 입력으로 허용하는 새 enum 또는 Literal
선택지, 선택 결과 key 추가는 일반적으로 추가형 변경입니다. 타입 contract도 runtime
object와 같은 호환성 검토를 받습니다. TypedDict key를 필수와 선택 사이에서 옮기거나
value type을 호환되지 않게 바꾸는 일을 단순한 typing 변경으로 보지 않습니다.

## 사용 중단 수명 주기 { #deprecation-lifecycle }

v1.0 이후 제거할 public API는 일반적으로 다음 절차를 따릅니다.

1. 대체 방법과 가장 이른 제거 release를 함께 문서에 사용 중단으로 표시합니다.
2. 가능하면 `DeprecationWarning`을 발생시킵니다.
3. 최소 두 번의 minor release와 6개월 동안 유지합니다.
4. major release에서만 제거합니다.

`DeprecationWarning`은 개발자 이전용입니다. `PylopdfWarning`은 PDF 해석 과정의 운영
warning에 계속 사용하며 사용 중단 channel로 쓰지 않습니다.

security, legal 또는 upstream runtime 긴급 상황에서는 기간을 줄일 수 있습니다. 이러한
예외는 영향이 가장 적은 이전 방법과 함께 changelog와 release note에 명확히 알립니다.

## 동작과 data 호환성 { #behavior-and-data }

이전 동작이 잘못된 경우 bug fix가 출력을 바꿀 수 있습니다. 손상 PDF 복구, 읽기 순서,
glyph geometry, table 해석, 색 변환과 renderer 차이가 예입니다. 문서화한 contract에
가까워지는 수정에는 major release가 필요하지 않지만, 중요한 영향은 보고합니다.

resource limit은 이전 release가 처리를 시도했던 공격적이거나 예상보다 비싼 입력을
거부할 수 있습니다. caller가 기계적으로 판단해야 하는 곳에는 안정적인 예외 type과
error code를 사용하며, 사람이 읽는 message 본문은 API가 아닙니다.

지원 범위는 각 release에 문서화한 Python version, platform, ABI와 WebAssembly runtime
matrix로 정의합니다. upstream EOL 이후이거나 지원 유지가 security/correctness 수정을
막는 경우 minor release에서 runtime을 제외할 수 있으며, 이유와 이전 기간을 공개합니다.
Pyodide 호환성은 runtime 업그레이드 전반에 걸쳐 가정하지 않고 pylopdf minor line별로
고정해 검증합니다.

## 호환성 검토 { #compatibility-review }

[`api/public-api.json`](https://github.com/yhay81/pylopdf/blob/main/api/public-api.json)
은 검토한 0.11 후보 surface를 기록합니다. 모든 native Python lane에서 export, signature,
mapping key, type alias, enum/상수 값, public member와 예외 상속의 변화를 검사합니다.

```console
uv run python tools/check_api_surface.py
```

의도한 변경은 runtime, typing, documentation과 SemVer 영향을 검토한 뒤 snapshot을
갱신합니다.

```console
uv run python tools/check_api_surface.py --update
```

snapshot은 review gate이며 자동 호환성 판정이 아닙니다. 모든 의도한 변경에는 여전히
test, 지원하는 네 언어의 documentation과 changelog entry가 필요합니다.
