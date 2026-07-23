---
title: 보안
description: 지원 버전, 비공개 취약점 보고, 신뢰할 수 없는 PDF를 처리할 때의 지침입니다.
---

# 보안

PyPI의 최신 릴리스만 보안 수정 지원을 받습니다.

## 취약점 보고 { #report-a-vulnerability }

[GitHub Security Advisories](https://github.com/yhay81/pylopdf/security/advisories/new)를
통해 비공개로 보고하세요. 공개 Issue를 만들지 마세요. 첫 답변은 일주일 이내를 목표로 합니다.

## 신뢰할 수 없는 PDF 처리 { #untrusted-pdfs }

pylopdf는 Rust로 작성되었고 런타임 의존성이 없지만, 악의적인 PDF 입력을 파싱하는
작업에는 본질적인 위험이 있습니다.

!!! warning "압축 해제 예산을 명시하세요"
    `pylopdf.open()`에`max_decompressed_size=`를 전달하세요. 렌더러가 지연 압축 해제할
    페이지 콘텐츠를 포함해, Document를 반환하기 전에 읽을 수 있는 모든 스트림을 검사합니다.

```python
import pylopdf

with pylopdf.open("upload.pdf", max_decompressed_size=128 * 1024 * 1024) as doc:
    preview = doc[0].get_pixmap(dpi=144)
```

- 이미지 스트림은 디코딩된 RGBA 크기로 제한됩니다.
- 출력 상한을 안전하게 계산할 수 없는 필터 체인은 제한이 활성화된 동안 거부됩니다.
- 렌더링은 페이지당 6,400만 픽셀로 제한됩니다.
- 임베드된 JavaScript는 설계상 지원하지 않으며 실행하지 않습니다.
- 신뢰할 수 없는 파일을 일괄 처리할 때는 가능하면 sandbox나 container에서 실행하세요.

## 의존성 감사 { #dependency-auditing }

CI는 push할 때마다 RustSec 취약점 데이터베이스를 기준으로 Rust 의존성 트리에
`cargo audit`를 실행합니다.

저장소의 정책 원본은
[`SECURITY.md`](https://github.com/yhay81/pylopdf/blob/main/SECURITY.md)입니다.
