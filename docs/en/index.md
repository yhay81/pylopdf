---
title: pylopdf
description: Edit, render, extract, and create PDFs in Python on the Rust PDF engines lopdf, hayro, and krilla, under the MIT license.
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
        Edit, render, extract, and create PDFs on the Rust engines lopdf, hayro, and krilla, under the MIT license and with nothing else to install.
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
        <li>7.8–9.1 MB wheels</li>
        <li>No required dependencies</li>
        <li>Python 3.11–3.15</li>
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
          <div class="living-code-sample">doc = pylopdf.open("input.pdf")<br>page = doc.pages[0]<br>hits = page.search_for("total")</div>
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

  <section class="living-capabilities" aria-label="Core capabilities">
    <article class="living-capability">
      <span class="living-capability__number">01 / EDIT</span>
      <h2>Edit precisely</h2>
      <p>Merge, reorder, and rotate pages, draw text and images, annotate, and fill forms.</p>
      <a class="living-capability__link" href="getting-started/#editing" aria-label="Explore PDF editing">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">02 / RENDER</span>
      <h2>Render faithfully</h2>
      <p>Turn pages into PNG or SVG, one at a time or many in parallel.</p>
      <a class="living-capability__link" href="getting-started/#rendering" aria-label="Explore PDF rendering">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">03 / EXTRACT</span>
      <h2>Extract meaning</h2>
      <p>Read text with positions, search, tables, and Markdown, from digital and scanned pages.</p>
      <a class="living-capability__link" href="getting-started/#pages-text-search" aria-label="Explore text extraction">↗</a>
    </article>
  </section>

  <section class="living-section" aria-labelledby="living-evidence-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Runs where Python runs</p>
      <h2 id="living-evidence-title">Small enough for deployment. Complete enough for real workflows.</h2>
      <p>
        pylopdf combines lopdf for editing, hayro for rendering and extraction,
        and krilla with HarfRust for generated text and form appearances.
      </p>
    </div>
    <div class="living-evidence">
      <a class="living-evidence__item" href="wasm/">
        <span class="living-evidence__value">Wasm</span>
        <span class="living-evidence__label">The same API runs in Pyodide and Cloudflare Python Workers.</span>
      </a>
      <a class="living-evidence__item" href="concurrency/">
        <span class="living-evidence__value">3.14t</span>
        <span class="living-evidence__label">Free-threaded CPython wheels extract independent documents in parallel without the GIL.</span>
      </a>
      <a class="living-evidence__item" href="ocr/">
        <span class="living-evidence__value">OCR</span>
        <span class="living-evidence__label">Optional offline OCR runs in the native core, with no cloud service or system binary.</span>
      </a>
      <a class="living-evidence__item" href="security/">
        <span class="living-evidence__value">Limits</span>
        <span class="living-evidence__label">DocumentLimits caps pages, objects and decompression for untrusted input.</span>
      </a>
    </div>
  </section>

  <section class="living-section" aria-labelledby="living-scope-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Deliberate scope</p>
      <h2 id="living-scope-title">A focused PDF core that works with the Python ecosystem.</h2>
      <p>
        pylopdf concentrates on working with PDFs and pairs with established
        tools for typesetting, signatures, and validation.
      </p>
    </div>
    <div class="living-scope">
      <div class="living-scope__column">
        <h3>Inside pylopdf</h3>
        <ul>
          <li>Page management, metadata, outlines, and encryption</li>
          <li>Text extraction with positions, search, tables, and Markdown</li>
          <li>PNG and SVG rendering, images, drawing, annotations, and OCR</li>
          <li>Form filling, attachments, and page labels</li>
        </ul>
      </div>
      <div class="living-scope__column">
        <h3>Connected by design</h3>
        <ul>
          <li>Typesetting and new PDF/A documents with <a href="ecosystem/#typesetting">Typst</a></li>
          <li>PAdES signatures with <a href="ecosystem/#signatures">pyHanko</a></li>
          <li>PDF/A validation with <a href="ecosystem/#new-pdfa">veraPDF</a></li>
          <li>Any <a href="ecosystem/#ocr">OCR engine</a> that returns words and rectangles</li>
        </ul>
      </div>
    </div>
  </section>
</div>
