"""テスト用の最小 PDF を組み立てるヘルパーとフィクスチャ。"""

from __future__ import annotations

import pytest


def build_pdf(page_texts: list[str]) -> bytes:
    """1 テキスト = 1 ページの最小 PDF を組み立てる。

    MediaBox / Resources はあえて親の Pages 側に置き、
    ページ属性の継承が絡む実在レイアウトを再現する。
    """
    n = len(page_texts)
    objects: dict[int, str] = {}
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {n} /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> >>"
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


@pytest.fixture
def one_page_pdf() -> bytes:
    return build_pdf(["Hello PDF"])


@pytest.fixture
def three_page_pdf() -> bytes:
    return build_pdf(["Page one", "Page two", "Page three"])
