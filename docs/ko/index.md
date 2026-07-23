---
title: pylopdf
description: Python다운 API와 간결한 Rust 코어로 PDF를 편집하고 렌더링하며 추출합니다.
hide:
  - navigation
  - toc
---

<div class="living-home" id="__skip">
  <section class="living-hero" aria-labelledby="living-hero-title">
    <div>
      <p class="living-eyebrow">Python-native PDF toolkit</p>
      <h1 id="living-hero-title">PDF를, <span class="living-annotated">Python답게</span>. 핵심은Rust.</h1>
      <p class="living-lede">
        편집·렌더링·추출·마무리를 익숙한 API 하나로 처리합니다.
        무거운 런타임 의존성이 없습니다.
      </p>

      <div class="living-actions">
        <a class="living-button living-button--primary" href="getting-started/">시작하기&nbsp; →</a>
        <a class="living-button" href="api/">API 둘러보기</a>
      </div>

      <div class="living-install" aria-label="설치 명령">
        <span class="living-install__prompt" aria-hidden="true">$</span>
        <code tabindex="0">pip install pylopdf</code>
        <button
          class="living-install__copy"
          type="button"
          data-copy-command="pip install pylopdf"
          data-copied-label="복사됨"
          aria-label="설치 명령 복사"
        ><span data-copy-label>복사</span></button>
      </div>

      <ul class="living-proofs" aria-label="프로젝트 정보">
        <li>MIT 라이선스</li>
        <li>약 3.5 MB wheel</li>
        <li>런타임 의존성 없음</li>
        <li>Python 3.10–3.14</li>
      </ul>
    </div>

    <div
      class="living-stage"
      role="img"
      aria-label="검색 결과, 교정 표시, 렌더링 출력 미리보기가 있는 추상적인 PDF 페이지"
    >
      <span class="living-stage__coordinate">612 × 792 pt</span>
      <div class="living-page" aria-hidden="true">
        <div class="living-page__inner">
          <p class="living-page__kicker">Portable document</p>
          <h2>살아 있는 <span class="living-search-target">PDF</span>를,<br>Python으로.</h2>
          <div class="living-page__rule"></div>
          <div class="living-line"></div>
          <div class="living-line living-line--medium"></div>
          <div class="living-line living-line--cobalt"></div>
          <div class="living-line living-line--short"></div>
          <div class="living-code-sample">doc = pylopdf.open("input.pdf")<br>page = doc[0]<br>hits = page.search_for("합계")</div>
        </div>
        <span class="living-markup-circle"></span>
      </div>
      <span class="living-margin-note" aria-hidden="true">search_for()</span>
      <aside class="living-output" aria-hidden="true">
        <div class="living-output__head">
          <span>Output</span>
          <span>PNG · 144 dpi</span>
        </div>
        <div class="living-output__sheet">
          <span class="living-selection-tag">match 01</span>
        </div>
      </aside>
    </div>
  </section>

  <section class="living-capabilities" aria-label="핵심 기능">
    <article class="living-capability">
      <span class="living-capability__number">01 / EDIT</span>
      <h2>정밀하게 편집</h2>
      <p>병합, 선택, 주석 추가, 폼 입력을 수행하면서 중요한 PDF 구조를 보존합니다.</p>
      <a class="living-capability__link" href="getting-started/#editing" aria-label="PDF 편집 알아보기">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">02 / RENDER</span>
      <h2>충실하게 렌더링</h2>
      <p>간결한 네이티브 엔진으로 안정적인 PNG 또는 SVG 출력을 만듭니다.</p>
      <a class="living-capability__link" href="getting-started/#rendering" aria-label="PDF 렌더링 알아보기">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">03 / EXTRACT</span>
      <h2>의미를 추출</h2>
      <p>텍스트, 단어, 블록, 좌표, OCR 비가시 레이어를 하나의 API로 읽습니다.</p>
      <a class="living-capability__link" href="getting-started/#pages-text-search" aria-label="텍스트 추출 알아보기">↗</a>
    </article>
  </section>

  <section class="living-section" aria-labelledby="living-evidence-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Useful constraints, visible evidence</p>
      <h2 id="living-evidence-title">배포에는 작게, 실제 업무에는 충분하게.</h2>
      <p>
        편집에는 lopdf, 렌더링에는 Typst도 사용하는 순수 Rust 렌더러 hayro를 결합해
        의도적으로 간결한 Python 패키지로 제공합니다.
      </p>
    </div>
    <div class="living-evidence">
      <div class="living-evidence__item">
        <span class="living-evidence__value">약 3.5 MB</span>
        <span class="living-evidence__label">현재 wheel 크기. AWS Lambda처럼 용량 제약이 있는 환경에도 적합합니다.</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">0</span>
        <span class="living-evidence__label">런타임 의존성. wheel 하나만 설치하면 PDF 처리를 시작할 수 있습니다.</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">3.10–3.14</span>
        <span class="living-evidence__label">하나의 abi3 wheel 계열이 다섯 세대의 Python을 지원합니다.</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">MIT</span>
        <span class="living-evidence__label">오픈 소스와 상용 제품 모두에 적합한 허용적 라이선스입니다.</span>
      </div>
    </div>
  </section>

  <section class="living-section" aria-labelledby="living-scope-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Deliberate scope</p>
      <h2 id="living-scope-title">집중된 PDF 코어, Python 생태계와의 협력.</h2>
      <p>
        불리한 결과까지 포함한 재현 가능한 벤치마크를 공개하고, 인접 분야는 다시
        구현하지 않고 검증된 도구와 연동합니다.
      </p>
    </div>
    <div class="living-scope">
      <div class="living-scope__column">
        <h3>pylopdf가 담당하는 것</h3>
        <ul>
          <li>페이지 관리, 메타데이터, 목차, 암호화</li>
          <li>위치 정보가 있는 텍스트 추출, 검색, Markdown 변환</li>
          <li>PNG/SVG 렌더링, 이미지, 주석, OCR 텍스트 레이어</li>
          <li>AcroForm 입력, 첨부 파일, 페이지 레이블</li>
        </ul>
      </div>
      <div class="living-scope__column">
        <h3>연동으로 해결하는 것</h3>
        <ul>
          <li><a href="ecosystem/#typesetting">Typst</a>를 사용한 조판과 새 PDF/A 생성</li>
          <li><a href="ecosystem/#signatures">pyHanko</a>를 사용한 PAdES 서명</li>
          <li>veraPDF를 사용한 PDF/A 검증</li>
          <li>단어와 사각형을 반환하는 모든 OCR 엔진</li>
        </ul>
      </div>
    </div>
  </section>
</div>
