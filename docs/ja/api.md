---
title: API一覧
description: pylopdfのDocument、Page、Pixmap、Rect、権限、警告、例外を俯瞰するAPIマップ。
---

# API 一覧

詳細な docstring はパッケージ内にあります（`help(pylopdf.Document)`）。この
ページは地図です。ページ番号は `get_toc` / `set_toc`（pymupdf 互換の 1 始まり）を
除きすべて 0 始まり。座標はすべて左上原点の表示空間です。

## Document { #document }

`pylopdf.Document(filename=None, stream=None, password=None, max_decompressed_size=None)` —
`pylopdf.open()` は別名コンストラクタ。with 文に対応。

| メンバ | 用途 |
|---|---|
| `doc[i]` / `load_page(pno)` / イテレーション | `Page` ビュー（負数可。構造変更後は取得し直す） |
| `page_count` / `len(doc)` | ページ数 |
| `needs_pass` / `is_encrypted` / `authenticate(pw)` | 暗号化状態と復号（pymupdf 互換の意味論） |
| `metadata` / `set_metadata(dict)` | Info 辞書（UTF-16BE 対応） |
| `get_page_text(pno, option)` | `"text"` / `"words"` / `"blocks"` / `"dict"` |
| `to_markdown(pages=None)` | Markdown 変換（見出し・CJK 連結・強調・リスト） |
| `render_page(pno, scale=, dpi=, background=)` / `render_page_svg(pno)` | PNG bytes / SVG 文字列 |
| `set_fallback_font(font, kind=, index=)` | 非埋め込み CJK の代替フォント |
| `select` / `delete_page(s)` / `insert_pdf` / `new_page` / `copy_page` | ページ操作 |
| `get_toc()` / `set_toc(toc)` | しおり（1 始まり） |
| `get_page_labels()` / `set_page_labels(labels)` | ページラベル |
| `get_form_fields()` / `set_form_field(name, value)` | AcroForm の一覧と記入（NeedAppearances） |
| `embfile_add / embfile_names / embfile_get / embfile_del` | 添付ファイル |
| `get_pdfa_claim()` | XMP の PDF/A 宣言（読み取りであって検証ではない） |
| `save(...)` / `tobytes(...)` | `garbage=` `deflate=` `object_streams=` `user_pw=` `owner_pw=` `permissions=` |
| `close()` | with 文でも |

## Page { #page }

| メンバ | 用途 |
|---|---|
| `number` / `parent` / `get_label()` | 素性と表示ラベル |
| `get_text(option)` / `search_for(needle)` | 抽出と検索（大文字小文字を区別しない） |
| `find_tables()` | 完全な罫線グリッド（`Table.extract()` / `to_markdown()`） |
| `to_markdown()` | 1 ページ分の Markdown |
| `get_images()` | 描画された画像（`bbox` 付き。JPEG パススルー / PNG） |
| `get_pixmap(scale=, dpi=, background=)` / `render(...)` / `render_svg()` | レンダリング |
| `rotation` / `set_rotation(deg)` | 表示回転 |
| `mediabox` / `cropbox` / `rect` / `set_mediabox` / `set_cropbox` | ページボックス |
| `insert_image(rect, filename= / stream=, keep_proportion=, overlay=)` | JPEG/PNG の描き込み |
| `show_pdf_page(rect, src, pno=, keep_proportion=, overlay=)` | 別 PDF ページをベクタのまま重ねる |
| `insert_text(point, text, fontsize=, fontname=, color=)` | 標準 14 フォント印字（WinAnsi） |
| `insert_ocr_text_layer(words)` | 不可視 OCR テキスト層（searchable PDF 化） |
| `replace_text(search, replacement, default_char=)` | 単純エンコーディングのテキスト置換 |
| `annots()` / `add_highlight_annot(...)` / `add_link_annot(rect, uri)` | 注釈 |

## モジュールレベル { #module-level }

| 名前 | 用途 |
|---|---|
| `peek_metadata(path_or_stream, password=)` | 全体パース無しの高速メタデータ読み取り |
| `Permissions` | 暗号化の許可フラグ（IntFlag） |
| `Rect` | 矩形の NamedTuple（`width` / `height` 付き） |
| `TableFinder` / `Table` | 所有権を持つ罫線表の座標とセル文字列 |
| `PdfError` / `PasswordError` / `DocumentClosedError` / `EncryptedDocumentError` / `StalePageError` | 例外階層（ValueError 互換の基底） |
| `Pixmap` | RGBA8 画素: `samples` / `width` / `height` / `stride` / `n` / `tobytes()` |
| `PylopdfWarning` | インタープリタ警告（フォント未解決・画像デコード失敗） |
