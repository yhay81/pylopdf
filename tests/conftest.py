"""テスト用の最小 PDF を組み立てるヘルパーとフィクスチャ。"""

from __future__ import annotations

import pytest


def build_raw_pdf(objects: dict[int, bytes | str], *, version: str = "1.7") -> bytes:
    """連続したオブジェクト辞書から xref table 形式の最小 PDF を組み立てる。"""
    expected = list(range(1, len(objects) + 1))
    if sorted(objects) != expected:
        msg = f"objects の番号は 1..{len(objects)} の連番で指定してください"
        raise ValueError(msg)
    out = bytearray(f"%PDF-{version}\n".encode())
    offsets: dict[int, int] = {}
    for number in expected:
        value = objects[number]
        body = value.encode("latin-1") if isinstance(value, str) else value
        offsets[number] = len(out)
        out.extend(f"{number} 0 obj\n".encode())
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for number in expected:
        out.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())
    return bytes(out)


def build_pdf(page_texts: list[str], page_size: tuple[int, int] = (612, 792)) -> bytes:
    """1 テキスト = 1 ページの最小 PDF を組み立てる。

    MediaBox / Resources はあえて親の Pages 側に置き、
    ページ属性の継承が絡む実在レイアウトを再現する。
    """
    n = len(page_texts)
    objects: dict[int, str] = {}
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    width, height = page_size
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {n} /MediaBox [0 0 {width} {height}] "
        "/Resources << /Font << /F1 3 0 R >> >> >>"
    )
    objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for i, text in enumerate(page_texts):
        stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET"
        objects[4 + 2 * i] = f"<< /Type /Page /Parent 2 0 R /Contents {5 + 2 * i} 0 R >>"
        objects[5 + 2 * i] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in sorted(objects):
        out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return bytes(out)


def build_nonembedded_cjk_pdf() -> bytes:
    """MS-Mincho を 90ms-RKSJ-H で参照するだけ（非埋め込み）の 1 ページ PDF を組み立てる。

    「こんにちは日本語」を Shift-JIS バイト列で描画する。CJK 代替フォントを
    設定しない限り、レンダリングしても文字は描画されない。
    """
    sjis = "こんにちは日本語".encode("cp932")
    text_octal = "".join(f"\\{b:03o}" for b in sjis)
    stream = f"BT /F1 24 Tf 72 720 Td ({text_octal}) Tj ET"
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> >>",
        3: "<< /Type /Font /Subtype /Type0 /BaseFont /MS-Mincho /Encoding /90ms-RKSJ-H /DescendantFonts [6 0 R] >>",
        4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
        5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        6: "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /MS-Mincho"
        " /CIDSystemInfo << /Registry (Adobe) /Ordering (Japan1) /Supplement 6 >>"
        " /FontDescriptor 7 0 R /DW 1000 >>",
        7: "<< /Type /FontDescriptor /FontName /MS-Mincho /Flags 6 /FontBBox [0 -137 1000 859]"
        " /ItalicAngle 0 /Ascent 859 /Descent -140 /CapHeight 769 /StemV 78 >>",
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in sorted(objects):
        out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return bytes(out)


def png_size(data: bytes) -> tuple[int, int]:
    """PNG の IHDR チャンクから (幅, 高さ) を読み取る。"""
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


@pytest.fixture
def one_page_pdf() -> bytes:
    return build_pdf(["Hello PDF"])


@pytest.fixture
def three_page_pdf() -> bytes:
    return build_pdf(["Page one", "Page two", "Page three"])
