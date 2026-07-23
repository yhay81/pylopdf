---
title: pylopdf
description: Pythonらしい操作感とコンパクトなRustコアで、PDFを編集・描画・抽出。
hide:
  - navigation
  - toc
---

<div class="living-home" id="__skip">
  <section class="living-hero" aria-labelledby="living-hero-title">
    <div>
      <p class="living-eyebrow">Python-native PDF toolkit</p>
      <h1 id="living-hero-title">PDFを、<span class="living-annotated">Pythonらしく</span>。中核はRust。</h1>
      <p class="living-lede">
        編集・描画・抽出・仕上げを、ひとつの馴染みやすいAPIで。
        重い実行時依存はありません。
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
        <li>約3.5 MBのwheel</li>
        <li>実行時依存ゼロ</li>
        <li>Python 3.10–3.14</li>
      </ul>
    </div>

    <div
      class="living-stage"
      role="img"
      aria-label="検索結果・校正記号・レンダリング結果が重なった抽象的なPDFページ"
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
          <div class="living-code-sample">doc = pylopdf.open("input.pdf")<br>page = doc[0]<br>hits = page.search_for("合計")</div>
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

  <section class="living-capabilities" aria-label="主要機能">
    <article class="living-capability">
      <span class="living-capability__number">01 / EDIT</span>
      <h2>正確に編集</h2>
      <p>結合・並べ替え・注釈・フォーム記入。必要なPDF構造を保って操作します。</p>
      <a class="living-capability__link" href="getting-started/#editing" aria-label="PDF編集を見る">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">02 / RENDER</span>
      <h2>忠実に描画</h2>
      <p>コンパクトなネイティブエンジンから、安定したPNG・SVGを生成します。</p>
      <a class="living-capability__link" href="getting-started/#rendering" aria-label="PDFレンダリングを見る">↗</a>
    </article>
    <article class="living-capability">
      <span class="living-capability__number">03 / EXTRACT</span>
      <h2>意味を抽出</h2>
      <p>テキスト・単語・ブロック・座標・OCR不可視層を、ひとつのAPIから扱います。</p>
      <a class="living-capability__link" href="getting-started/#pages-text-search" aria-label="テキスト抽出を見る">↗</a>
    </article>
  </section>

  <section class="living-section" aria-labelledby="living-evidence-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Useful constraints, visible evidence</p>
      <h2 id="living-evidence-title">配布には小さく、実務には十分に。</h2>
      <p>
        編集はlopdf、描画はTypstにも採用された純Rustレンダラhayro。
        意図的にコンパクトなPythonパッケージへまとめています。
      </p>
    </div>
    <div class="living-evidence">
      <div class="living-evidence__item">
        <span class="living-evidence__value">約3.5 MB</span>
        <span class="living-evidence__label">現在のwheelサイズ。AWS Lambdaなど容量制約のある環境にも適します。</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">0</span>
        <span class="living-evidence__label">実行時依存。ひとつのwheelを入れればPDF処理を始められます。</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">3.10–3.14</span>
        <span class="living-evidence__label">ひとつのabi3 wheel系列で5世代のPythonをカバーします。</span>
      </div>
      <div class="living-evidence__item">
        <span class="living-evidence__value">MIT</span>
        <span class="living-evidence__label">OSSにも商用製品にも組み込みやすい、パーミッシブなライセンスです。</span>
      </div>
    </div>
  </section>

  <section class="living-section" aria-labelledby="living-scope-title">
    <div class="living-section__header">
      <p class="living-eyebrow">Deliberate scope</p>
      <h2 id="living-scope-title">軽量コアに集中し、Pythonエコシステムとつながる。</h2>
      <p>
        遅い結果も隠さない再現可能ベンチマークを公開し、隣接領域は実績あるツールを
        再実装せず連携します。
      </p>
    </div>
    <div class="living-scope">
      <div class="living-scope__column">
        <h3>pylopdfの担当</h3>
        <ul>
          <li>ページ操作・メタデータ・しおり・暗号化</li>
          <li>位置付きテキスト抽出・検索・Markdown変換</li>
          <li>PNG/SVG描画・画像・注釈・OCR不可視層</li>
          <li>AcroForm記入・添付ファイル・ページラベル</li>
        </ul>
      </div>
      <div class="living-scope__column">
        <h3>連携して解決</h3>
        <ul>
          <li>組版と新規PDF/Aは<a href="ecosystem/#typesetting">Typst</a></li>
          <li>PAdES署名は<a href="ecosystem/#signatures">pyHanko</a></li>
          <li>PDF/A検証はveraPDF</li>
          <li>単語と矩形を返す任意のOCRエンジン</li>
        </ul>
      </div>
    </div>
  </section>
</div>
