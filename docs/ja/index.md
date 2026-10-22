---
title: pylopdf
description: RustのPDFエンジンlopdf・hayro・krillaの上で、PythonからPDFを編集・描画・抽出・生成。MITライセンス。
hide:
  - navigation
  - toc
---

<div class="living-home" id="__skip">
  <section class="living-hero" aria-labelledby="living-hero-title">
    <div>
      <p class="living-eyebrow">PythonネイティブのPDFツールキット</p>
      <h1 id="living-hero-title">PDFを、<span class="living-annotated">Pythonらしく</span>。中核はRust。</h1>
      <p class="living-lede">
        PDFの編集・描画・抽出・生成を、Rustのエンジンlopdf・hayro・krillaの上で。MITライセンスで、ほかに何もインストールする必要はありません。
      </p>

      <div class="living-actions">
        <a class="living-button living-button--primary" href="getting-started/">はじめる&nbsp; →</a>
        <a class="living-button" href="api/">APIを見る</a>
      </div>

      <div class="living-install" aria-label="インストールコマンド">
        <span class="living-install__prompt" aria-hidden="true">$</span>
        <code tabindex="0">pip install pylopdf</code>
        <button
          class="living-install__copy"
          type="button"
          data-copy-command="pip install pylopdf"
          data-copied-label="コピー済み"
          aria-label="インストールコマンドをコピー"
        ><span data-copy-label>コピー</span></button>
      </div>

      <ul class="living-proofs" aria-label="プロジェクト情報">
        <li>MITライセンス</li>
        <li>7.8–9.1 MBのwheel</li>
        <li>必須依存なし</li>
        <li>Python 3.11–3.15</li>
      </ul>
    </div>

    <div
      class="living-stage"
      role="img"
      aria-label="検索結果・校正記号・レンダリング結果のプレビューが重なった抽象的なPDFページ"
    >
      <span class="living-stage__coordinate">612 × 792 pt</span>
      <div class="living-page" aria-hidden="true">
        <div class="living-page__inner">
          <p class="living-page__kicker">Portable document</p>
          <h2>生きた<span class="living-search-target">PDF</span>を、<br>Pythonから。</h2>
          <div class="living-page__rule"></div>
          <div class="living-line"></div>
          <div class="living-line living-line--medium"></div>
          <div class="living-line living-line--cobalt"></div>
          <div class="living-line living-line--short"></div>
          <div class="living-code-sample">doc = pylopdf.open("input.pdf")<br>page = doc.pages[0]<br>hits = page.search("合計")</div>
        </div>
        <span class="living-markup-circle"></span>
        <span class="living-margin-note" aria-hidden="true">search()</span>
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

  <section class="living-capabilities" aria-label="主要機能">
    <article class="living-capability">
      <span class="living-capability__number">01 / EDIT</span>
      <h2>正確に編集</h2>
      <p>ページの結合・並べ替え・回転、テキストや画像の描画、注釈、フォーム記入。</p>
      <a class="living-capability__link" href="getting-started/#editing" aria-label="PDF編集を見る">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">02 / RENDER</span>
      <h2>忠実に描画</h2>
      <p>ページをPNGやSVGに。1ページずつでも、複数ページを並列でも。</p>
      <a class="living-capability__link" href="getting-started/#rendering" aria-label="PDFレンダリングを見る">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">03 / EXTRACT</span>
      <h2>意味を抽出</h2>
      <p>位置付きテキスト、検索、表、Markdownを、デジタルのページからもスキャンしたページからも。</p>
      <a class="living-capability__link" href="getting-started/#pages-text-search" aria-label="テキスト抽出を見る">↗</a>
    </article>
  </section>

  <section class="living-section" aria-labelledby="living-evidence-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Pythonが動く場所で動く</p>
      <h2 id="living-evidence-title">配布には小さく、実務には十分に。</h2>
      <p>
        編集はlopdf、描画と抽出はhayro、テキストやフォーム外観の生成はkrillaとHarfRustが担います。
      </p>
    </div>
    <div class="living-evidence">
      <a class="living-evidence__item" href="wasm/">
        <span class="living-evidence__value">Wasm</span>
        <span class="living-evidence__label">同じAPIがPyodideとCloudflare Python Workersで動きます。</span>
      </a>
      <a class="living-evidence__item" href="concurrency/">
        <span class="living-evidence__value">3.14t</span>
        <span class="living-evidence__label">フリースレッド版CPython向けwheelで、独立した文書をGILなしで並列に抽出できます。</span>
      </a>
      <a class="living-evidence__item" href="ocr/">
        <span class="living-evidence__value">OCR</span>
        <span class="living-evidence__label">オプションのオフラインOCRはネイティブコア内で動作し、クラウドサービスや外部バイナリは不要です。</span>
      </a>
      <a class="living-evidence__item" href="security/">
        <span class="living-evidence__value">Limits</span>
        <span class="living-evidence__label">DocumentLimitsで、信頼できない入力のページ数・オブジェクト数・展開サイズを制限できます。</span>
      </a>
    </div>
  </section>

  <section class="living-section" aria-labelledby="living-scope-title">
    <div class="living-section__header">
      <p class="living-eyebrow">意図的な守備範囲</p>
      <h2 id="living-scope-title">PDFに集中したコアを、Pythonエコシステムとつなぐ。</h2>
      <p>
        pylopdfはPDFを扱うことに集中し、組版・署名・検証は実績あるツールと組み合わせて使います。
      </p>
    </div>
    <div class="living-scope">
      <div class="living-scope__column">
        <h3>pylopdfの担当</h3>
        <ul>
          <li>ページ操作・メタデータ・しおり・暗号化</li>
          <li>位置付きテキスト抽出・検索・表・Markdown変換</li>
          <li>PNG/SVG描画・画像・図形描画・注釈・OCR</li>
          <li>フォーム記入・添付ファイル・ページラベル</li>
        </ul>
      </div>
      <div class="living-scope__column">
        <h3>連携して解決</h3>
        <ul>
          <li>組版と新規PDF/Aは<a href="ecosystem/#typesetting">Typst</a></li>
          <li>PAdES署名は<a href="ecosystem/#signatures">pyHanko</a></li>
          <li>PDF/A検証は<a href="ecosystem/#new-pdfa">veraPDF</a></li>
          <li>単語と矩形を返す任意の<a href="ecosystem/#ocr">OCRエンジン</a></li>
        </ul>
      </div>
    </div>
  </section>
</div>
