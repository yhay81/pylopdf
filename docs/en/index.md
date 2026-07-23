---
title: pylopdf
description: Edit, render and extract PDFs with Python ergonomics and a compact Rust core.
hide:
  - navigation
  - toc
---

<div class="living-home" id="__skip">
  <section class="living-hero" aria-labelledby="living-hero-title">
    <div>
      <p class="living-eyebrow">Python-native PDF toolkit</p>
      <h1 id="living-hero-title">PDFs, with <span class="living-annotated">Python ergonomics</span> and a Rust core.</h1>
      <p class="living-lede">
        Edit, render, extract and finish PDFs through one familiar API—without
        heavyweight runtime dependencies.
      </p>

      <div class="living-actions">
        <a class="living-button living-button--primary" href="getting-started/">Get started&nbsp; →</a>
        <a class="living-button" href="api/">Explore the API</a>
      </div>

      <div class="living-install" aria-label="Installation command">
        <span class="living-install__prompt" aria-hidden="true">$</span>
        <code tabindex="0">pip install pylopdf</code>
        <button
          class="living-install__copy"
          type="button"
          data-copy-command="pip install pylopdf"
          data-copied-label="Copied"
          aria-label="Copy installation command"
        ><span data-copy-label>Copy</span></button>
      </div>

      <ul class="living-proofs" aria-label="Project facts">
        <li>MIT licensed</li>
        <li>~3.5 MB wheels</li>
        <li>Zero runtime dependencies</li>
        <li>Python 3.10–3.14</li>
      </ul>
    </div>

    <div
      class="living-stage"
      role="img"
      aria-label="An abstract PDF page with a search result, editorial proof marks and a rendered output preview"
    >
      <span class="living-stage__coordinate">612 × 792 pt</span>
      <div class="living-page" aria-hidden="true">
        <div class="living-page__inner">
          <p class="living-page__kicker">Portable document</p>
          <h2>A living <span class="living-search-target">PDF</span>,<br>ready for Python.</h2>
          <div class="living-page__rule"></div>
          <div class="living-line"></div>
          <div class="living-line living-line--medium"></div>
          <div class="living-line living-line--cobalt"></div>
          <div class="living-line living-line--short"></div>
          <div class="living-code-sample">doc = pylopdf.open("input.pdf")<br>page = doc[0]<br>hits = page.search_for("total")</div>
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

  <section class="living-capabilities" aria-label="Core capabilities">
    <article class="living-capability">
      <span class="living-capability__number">01 / EDIT</span>
      <h2>Edit precisely</h2>
      <p>Merge, select, annotate, fill forms and preserve the structure that matters.</p>
      <a class="living-capability__link" href="getting-started/#editing" aria-label="Explore PDF editing">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">02 / RENDER</span>
      <h2>Render faithfully</h2>
      <p>Turn pages into dependable PNG or SVG output with a compact native engine.</p>
      <a class="living-capability__link" href="getting-started/#rendering" aria-label="Explore PDF rendering">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">03 / EXTRACT</span>
      <h2>Extract meaning</h2>
      <p>Read text, words, blocks, geometry and invisible OCR layers through one API.</p>
      <a class="living-capability__link" href="getting-started/#pages-text-search" aria-label="Explore text extraction">↗</a>
    </article>
  </section>

  <section class="living-section" aria-labelledby="living-evidence-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Useful constraints, visible evidence</p>
      <h2 id="living-evidence-title">Small enough for deployment. Complete enough for real workflows.</h2>
      <p>
        pylopdf combines lopdf for editing with hayro—the pure-Rust renderer used
        by Typst—for an intentionally compact Python package.
      </p>
    </div>
    <div class="living-evidence">
      <div class="living-evidence__item">
        <span class="living-evidence__value">~3.5 MB</span>
        <span class="living-evidence__label">Current wheel size, suited to size-constrained environments such as AWS Lambda.</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">0</span>
        <span class="living-evidence__label">Runtime dependencies. Install one wheel and start processing PDFs.</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">3.10–3.14</span>
        <span class="living-evidence__label">A single abi3 wheel line covers five Python generations.</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">MIT</span>
        <span class="living-evidence__label">A permissive license for open-source and commercial products.</span>
      </div>
    </div>
  </section>

  <section class="living-section" aria-labelledby="living-scope-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Deliberate scope</p>
      <h2 id="living-scope-title">A focused PDF core that works with the Python ecosystem.</h2>
      <p>
        The project publishes reproducible benchmarks—including losses—and uses
        established tools instead of rebuilding adjacent domains.
      </p>
    </div>
    <div class="living-scope">
      <div class="living-scope__column">
        <h3>Inside pylopdf</h3>
        <ul>
          <li>Page management, metadata, outlines and encryption</li>
          <li>Positioned text extraction, search and Markdown conversion</li>
          <li>PNG/SVG rendering, images, annotations and OCR text layers</li>
          <li>AcroForm filling, attachments and page labels</li>
        </ul>
      </div>
      <div class="living-scope__column">
        <h3>Connected by design</h3>
        <ul>
          <li>Typesetting and new PDF/A documents with <a href="ecosystem/#typesetting">Typst</a></li>
          <li>PAdES signatures with <a href="ecosystem/#signatures">pyHanko</a></li>
          <li>PDF/A validation with veraPDF</li>
          <li>Any OCR engine that returns words and rectangles</li>
        </ul>
      </div>
    </div>
  </section>
</div>
