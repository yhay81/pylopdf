"""to_markdown（Document / Page）のテスト。"""

from __future__ import annotations

import pylopdf


def _layout_pdf(pages: list[list[tuple]]) -> bytes:
    """(サイズ, ベースライン y[PDF 座標], テキスト[, フォント]) の列からページを組み立てる。

    フォントは F1 = Helvetica（既定）、F2 = Helvetica-Bold、F3 = Helvetica-Oblique。
    """
    n = len(pages)
    kids = " ".join(f"{10 + 2 * i} 0 R" for i in range(n))
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{kids}] /Count {n} /MediaBox [0 0 612 792]"
            " /Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> >>"
        ),
        3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >>",
    }
    for i, items in enumerate(pages):
        ops = ""
        for item in items:
            size, y, text = item[0], item[1], item[2]
            font = item[3] if len(item) > 3 else "F1"
            escaped = text.replace(chr(92), chr(92) * 2).replace("(", chr(92) + "(").replace(")", chr(92) + ")")
            ops += f"BT /{font} {size} Tf 72 {y} Td ({escaped}) Tj ET\n"
        objects[10 + 2 * i] = f"<< /Type /Page /Parent 2 0 R /Contents {11 + 2 * i} 0 R >>"
        objects[11 + 2 * i] = f"<< /Length {len(ops)} >>\nstream\n{ops}endstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in range(1, size):
        if num in offsets:
            out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
        else:
            out += b"0000000000 65535 f \n"
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return bytes(out)


def test_heading_detected_by_size() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [
                    (24, 720, "Big Title"),
                    (12, 660, "Body line one"),
                    (12, 646, "body line two"),
                ]
            ]
        )
    )
    md = doc.to_markdown()
    assert md.startswith("# Big Title")
    # 本文 2 行は 1 段落に空白連結される
    assert "Body line one body line two" in md


def test_two_heading_levels() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [
                    (28, 720, "Title"),
                    (18, 660, "Section"),
                    (12, 600, "Body text here"),
                    (12, 586, "and more body"),
                ]
            ]
        )
    )
    md = doc.to_markdown()
    assert "# Title" in md
    assert "## Section" in md
    assert "### " not in md


def test_uniform_size_has_no_headings() -> None:
    doc = pylopdf.open(stream=_layout_pdf([[(12, 720, "Only body"), (12, 706, "same size")]]))
    assert "#" not in doc.to_markdown()


def test_bullets_and_numbers_normalize() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [
                    (12, 720, "Intro paragraph"),
                    (12, 680, "- first item"),
                    (12, 666, "- second item"),
                    (12, 626, "1) numbered"),
                ]
            ]
        )
    )
    md = doc.to_markdown()
    assert "- first item\n- second item" in md  # 連続項目は 1 つのリスト
    assert "1. numbered" in md


def test_dict_spans_have_font_and_flags_keys() -> None:
    # 太字・斜体の実検出は埋め込みフォントが対象（tests/test_interop.py の typst テスト）。
    # 標準 14（Type1 代替）は hayro がメタデータを公開しないため flags 0 / font 空が現状仕様
    doc = pylopdf.open(stream=_layout_pdf([[(12, 720, "Standard font words", "F2")]]))
    span = doc.get_page_text(0, "dict")["blocks"][0]["lines"][0]["spans"][0]
    assert span["flags"] == 0
    assert span["font"] == ""


def test_cjk_lines_join_without_space() -> None:
    # CJK フォントのフィクスチャを作らず、OCR 層で日本語の 2 行を用意する
    doc = pylopdf.Document()
    doc.new_page(width=300, height=200)
    page = doc[0]
    page.insert_ocr_text_layer(
        [
            (50, 50, 200, 64, "日本語の折り返しは"),
            (50, 66, 200, 80, "空白なしで繋がる"),
        ]
    )
    md = doc.to_markdown()
    assert "日本語の折り返しは空白なしで繋がる" in md


def test_latin_lines_join_with_space() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=300, height=200)
    doc[0].insert_ocr_text_layer(
        [
            (50, 50, 200, 64, "Latin lines"),
            (50, 66, 200, 80, "join spaced"),
        ]
    )
    assert "Latin lines join spaced" in doc.to_markdown()


def test_page_to_markdown_and_page_selection() -> None:
    doc = pylopdf.open(
        stream=_layout_pdf(
            [
                [(12, 720, "First page")],
                [(12, 720, "Second page")],
            ]
        )
    )
    assert doc[1].to_markdown() == "Second page"
    assert doc.to_markdown(pages=[1, 0]) == "Second page\n\nFirst page"
    full = doc.to_markdown()
    assert "First page" in full
    assert "Second page" in full


def test_empty_document() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    assert doc.to_markdown() == ""
