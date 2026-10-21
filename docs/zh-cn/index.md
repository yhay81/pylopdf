---
title: pylopdf
description: 用Python编辑、渲染、提取和创建PDF：沿用pymupdf风格的API，以Rust为核心，采用MIT许可。
hide:
  - navigation
  - toc
---

<div class="living-home" id="__skip">
  <section class="living-hero" aria-labelledby="living-hero-title">
    <div>
      <p class="living-eyebrow">Python原生PDF工具包</p>
      <h1 id="living-hero-title">用<span class="living-annotated">Python的方式</span>处理PDF，核心由Rust驱动。</h1>
      <p class="living-lede">
        用一套沿用pymupdf的API编辑、渲染、提取和创建PDF，采用MIT许可，无需安装其他依赖。
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
        <li>7.8–9.1 MB wheel</li>
        <li>无必需依赖</li>
        <li>Python 3.11–3.15</li>
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
        <span class="living-margin-note" aria-hidden="true">search_for()</span>
      </div>
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
      <p>合并、重排和旋转页面，绘制文本与图像，添加批注，填写表单。</p>
      <a class="living-capability__link" href="getting-started/#editing" aria-label="了解PDF编辑">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">02 / RENDER</span>
      <h2>忠实渲染</h2>
      <p>将页面转换为PNG或SVG，可逐页处理，也可多页并行。</p>
      <a class="living-capability__link" href="getting-started/#rendering" aria-label="了解PDF渲染">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">03 / EXTRACT</span>
      <h2>提取信息</h2>
      <p>从电子文档和扫描页面中读取带位置的文本，并进行搜索、表格识别和Markdown转换。</p>
      <a class="living-capability__link" href="getting-started/#pages-text-search" aria-label="了解文本提取">↗</a>
    </article>
  </section>

  <section class="living-section" aria-labelledby="living-evidence-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Python能运行的地方都能运行</p>
      <h2 id="living-evidence-title">足够轻，便于部署；足够完整，胜任真实工作流。</h2>
      <p>
        pylopdf使用lopdf进行编辑，使用hayro进行渲染与提取，并通过krilla和HarfRust生成文本与表单外观。
      </p>
    </div>
    <div class="living-evidence">
      <a class="living-evidence__item" href="wasm/">
        <span class="living-evidence__value">Wasm</span>
        <span class="living-evidence__label">同一套API可在Pyodide与Cloudflare Python Workers中运行。</span>
      </a>
      <a class="living-evidence__item" href="concurrency/">
        <span class="living-evidence__value">3.14t</span>
        <span class="living-evidence__label">面向自由线程CPython的wheel，可在无GIL的情况下并行提取互相独立的文档。</span>
      </a>
      <a class="living-evidence__item" href="ocr/">
        <span class="living-evidence__value">OCR</span>
        <span class="living-evidence__label">可选的离线OCR在原生核心中运行，无需云服务或系统程序。</span>
      </a>
      <a class="living-evidence__item" href="security/">
        <span class="living-evidence__value">Limits</span>
        <span class="living-evidence__label">DocumentLimits可为不受信任的输入限制页数、对象数与解压大小。</span>
      </a>
    </div>
  </section>

  <section class="living-section" aria-labelledby="living-scope-title">
    <div class="living-section__header">
      <p class="living-eyebrow">明确的边界</p>
      <h2 id="living-scope-title">专注的PDF核心，与Python生态系统协同工作。</h2>
      <p>
        pylopdf专注于处理PDF，排版、签名和验证则交给成熟的工具。
      </p>
    </div>
    <div class="living-scope">
      <div class="living-scope__column">
        <h3>pylopdf负责</h3>
        <ul>
          <li>页面管理、元数据、书签与加密</li>
          <li>带位置的文本提取、搜索、表格与Markdown</li>
          <li>PNG与SVG渲染、图像、绘图、批注与OCR</li>
          <li>表单填写、附件与页码标签</li>
        </ul>
      </div>
      <div class="living-scope__column">
        <h3>通过生态协作</h3>
        <ul>
          <li>使用<a href="ecosystem/#typesetting">Typst</a>排版并生成新的PDF/A文档</li>
          <li>使用<a href="ecosystem/#signatures">pyHanko</a>添加PAdES签名</li>
          <li>使用<a href="ecosystem/#new-pdfa">veraPDF</a>验证PDF/A</li>
          <li>任何能够返回单词与矩形的<a href="ecosystem/#ocr">OCR引擎</a></li>
        </ul>
      </div>
    </div>
  </section>
</div>
