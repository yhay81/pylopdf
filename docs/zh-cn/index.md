---
title: pylopdf
description: 以符合Python使用习惯的API和紧凑的Rust核心编辑、渲染与提取PDF。
hide:
  - navigation
  - toc
---

<div class="living-home" id="__skip">
  <section class="living-hero" aria-labelledby="living-hero-title">
    <div>
      <p class="living-eyebrow">Python-native PDF toolkit</p>
      <h1 id="living-hero-title">用<span class="living-annotated">Python的方式</span>处理PDF，核心由Rust驱动。</h1>
      <p class="living-lede">
        用一套熟悉的API完成PDF编辑、渲染、提取与最终处理，无需沉重的运行时依赖。
      </p>

      <div class="living-actions">
        <a class="living-button living-button--primary" href="getting-started/">快速开始&nbsp; →</a>
        <a class="living-button" href="api/">浏览API</a>
      </div>

      <div class="living-install" aria-label="安装命令">
        <span class="living-install__prompt" aria-hidden="true">$</span>
        <code tabindex="0">pip install pylopdf</code>
        <button
          class="living-install__copy"
          type="button"
          data-copy-command="pip install pylopdf"
          data-copied-label="已复制"
          aria-label="复制安装命令"
        ><span data-copy-label>复制</span></button>
      </div>

      <ul class="living-proofs" aria-label="项目信息">
        <li>MIT许可</li>
        <li>约3.5 MB wheel</li>
        <li>零运行时依赖</li>
        <li>Python 3.10–3.14</li>
      </ul>
    </div>

    <div
      class="living-stage"
      role="img"
      aria-label="带有搜索结果、校对标记和渲染输出预览的抽象PDF页面"
    >
      <span class="living-stage__coordinate">612 × 792 pt</span>
      <div class="living-page" aria-hidden="true">
        <div class="living-page__inner">
          <p class="living-page__kicker">Portable document</p>
          <h2>一份鲜活的<span class="living-search-target">PDF</span>，<br>交给Python。</h2>
          <div class="living-page__rule"></div>
          <div class="living-line"></div>
          <div class="living-line living-line--medium"></div>
          <div class="living-line living-line--cobalt"></div>
          <div class="living-line living-line--short"></div>
          <div class="living-code-sample">doc = pylopdf.open("input.pdf")<br>page = doc[0]<br>hits = page.search_for("合计")</div>
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

  <section class="living-capabilities" aria-label="核心功能">
    <article class="living-capability">
      <span class="living-capability__number">01 / EDIT</span>
      <h2>精准编辑</h2>
      <p>合并、选页、添加批注、填写表单，并保留关键的PDF结构。</p>
      <a class="living-capability__link" href="getting-started/#editing" aria-label="了解PDF编辑">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">02 / RENDER</span>
      <h2>忠实渲染</h2>
      <p>通过紧凑的原生引擎，将页面稳定输出为PNG或SVG。</p>
      <a class="living-capability__link" href="getting-started/#rendering" aria-label="了解PDF渲染">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">03 / EXTRACT</span>
      <h2>提取信息</h2>
      <p>通过统一API读取文本、单词、区块、坐标与OCR不可见文本层。</p>
      <a class="living-capability__link" href="getting-started/#pages-text-search" aria-label="了解文本提取">↗</a>
    </article>
  </section>

  <section class="living-section" aria-labelledby="living-evidence-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Useful constraints, visible evidence</p>
      <h2 id="living-evidence-title">足够轻，便于部署；足够完整，胜任真实工作流。</h2>
      <p>
        pylopdf使用lopdf进行编辑，使用Typst采用的纯Rust渲染器hayro进行渲染，
        并将它们组合成一个刻意保持紧凑的Python包。
      </p>
    </div>
    <div class="living-evidence">
      <div class="living-evidence__item">
        <span class="living-evidence__value">约3.5 MB</span>
        <span class="living-evidence__label">当前wheel大小，适合AWS Lambda等容量受限的环境。</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">0</span>
        <span class="living-evidence__label">运行时依赖。安装一个wheel即可开始处理PDF。</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">3.10–3.14</span>
        <span class="living-evidence__label">单一abi3 wheel系列覆盖五代Python。</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">MIT</span>
        <span class="living-evidence__label">宽松许可，适合开源与商业产品。</span>
      </div>
    </div>
  </section>

  <section class="living-section" aria-labelledby="living-scope-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Deliberate scope</p>
      <h2 id="living-scope-title">专注的PDF核心，与Python生态系统协同工作。</h2>
      <p>
        项目公开可复现的性能基准，包括不占优势的结果；相邻领域则采用成熟工具，
        而不是重复实现。
      </p>
    </div>
    <div class="living-scope">
      <div class="living-scope__column">
        <h3>pylopdf负责</h3>
        <ul>
          <li>页面管理、元数据、书签与加密</li>
          <li>带坐标的文本提取、搜索与Markdown转换</li>
          <li>PNG/SVG渲染、图像、批注与OCR文本层</li>
          <li>AcroForm填写、附件与页码标签</li>
        </ul>
      </div>
      <div class="living-scope__column">
        <h3>通过生态协作</h3>
        <ul>
          <li>使用<a href="ecosystem/#typesetting">Typst</a>排版并生成新的PDF/A</li>
          <li>使用<a href="ecosystem/#signatures">pyHanko</a>添加PAdES签名</li>
          <li>使用veraPDF验证PDF/A</li>
          <li>连接任何能够返回单词与矩形的OCR引擎</li>
        </ul>
      </div>
    </div>
  </section>
</div>
