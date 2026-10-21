---
title: 기여 및 지원
description: 질문과 버그 보고 방법, pylopdf에 기여하는 방법, 라이선스와 감사의 글을 안내합니다.
---

# 기여 및 지원

## 도움 받기와 버그 보고 { #support }

- **질문, 버그, 기능 요청:** [GitHub](https://github.com/yhay81/pylopdf/issues/new/choose)에서 이슈를 여세요. 먼저 [기존 이슈](https://github.com/yhay81/pylopdf/issues)를 검색해 주세요.
- **보안 취약점:** 비공개로 보고하세요. [보안](security.md#report-a-vulnerability)을 참고하세요.

좋은 버그 보고에는 pylopdf, Python, 운영 체제의 버전, 문제를 재현하는 짧은 스크립트, 기대한 동작이 포함됩니다. 문제가 특정 PDF에 따라 나타난다면 작은 PDF나 그 PDF를 만드는 스크립트를 첨부하세요. PDF에는 개인 정보, 서명, 숨겨진 메타데이터가 들어 있는 경우가 많습니다. 재배포가 허용된 파일만 기밀 내용을 제거한 뒤 공유하세요.

## 기여하기 { #contribute }

버그 수정, pylopdf가 제대로 처리하지 못하는 실제 PDF, 문서 개선, 번역을 모두 환영합니다. 큰 기능이라면 코드를 작성하기 전에 설계에 합의할 수 있도록 먼저 이슈를 여세요.

소스에서 빌드하려면 Rust 툴체인과 [uv](https://docs.astral.sh/uv/)가 필요합니다.

```bash
git clone https://github.com/yhay81/pylopdf.git
cd pylopdf
uv sync
uv run pytest
```

[CONTRIBUTING.md](https://github.com/yhay81/pylopdf/blob/main/CONTRIBUTING.md)에 실행할 검사, 테스트 PDF 추가 방법, 풀 리퀘스트 검토 방식을 담은 전체 개발 가이드가 있습니다. 참여하는 모든 사람은 [행동 강령](https://github.com/yhay81/pylopdf/blob/main/CODE_OF_CONDUCT.md)을 따라야 합니다.

### 문서와 번역 { #documentation }

이 사이트는 [`docs/`](https://github.com/yhay81/pylopdf/tree/main/docs)의 Markdown 파일로 [Zensical](https://zensical.org/)을 사용해 빌드합니다. 모든 페이지에는 GitHub에서 그 소스를 여는 편집 버튼이 있습니다. 변경 내용을 로컬에서 미리 보려면:

```bash
uv sync --group docs
uv run zensical serve -f mkdocs.yml
```

영어판이 기준입니다. 일본어, 중국어 간체, 한국어 번역은 영어판과 함께 관리되며 같은 페이지 이름과 섹션 앵커를 사용하므로, 링크가 모든 언어에서 동작합니다.

## 계획과 릴리스 { #plans }

- [로드맵](https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md): 무엇을 어떤 순서로 계획하고 있는지
- [변경 이력](https://github.com/yhay81/pylopdf/blob/main/CHANGELOG.md): 각 릴리스에서 무엇이 바뀌었는지
- [릴리스](https://github.com/yhay81/pylopdf/releases)와 [PyPI](https://pypi.org/project/pylopdf/)
- [API 안정성](stability.md): v1.0 전후로 무엇이 바뀔 수 있는지

## 라이선스와 감사의 글 { #license }

pylopdf는 [MIT 라이선스](https://github.com/yhay81/pylopdf/blob/main/LICENSE)로 배포됩니다.

PDF 읽기와 편집에는 [lopdf](https://github.com/J-F-Liu/lopdf), 렌더링과 텍스트 추출에는 [hayro](https://github.com/LaurenzV/hayro), 텍스트 쓰기에는 [krilla](https://github.com/LaurenzV/krilla)와 [HarfRust](https://github.com/harfbuzz/harfrust), OCR에는 [RTen](https://github.com/robertknight/rten)을 기반으로 합니다. API는 [pymupdf](https://pymupdf.readthedocs.io/)를 따릅니다. [NOTICE.md](https://github.com/yhay81/pylopdf/blob/main/NOTICE.md)에 포함된 모든 서드파티 코드의 라이선스가 나열되어 있습니다. 선택형 글꼴 패키지에는 SIL Open Font License의 Noto 글꼴이, OCR 모델 패키지에는 Apache License 2.0의 PP-OCRv6 모델이 들어 있습니다.
